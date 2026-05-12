from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol

logger = logging.getLogger(__name__)

# Substrings that indicate the error is permanent for this fire and we should
# not retry. Anything else is treated as transient (network, 5xx, timeout).
_TERMINAL_PATTERNS = (
    "insufficient margin",
    "insufficient_margin",
    "invalid signature",
    "401",
    "403",
    "geo",
    "blocked",
    "invalid price",
    "tick size",
    "price out of range",
    "symbol not found",
)


class ErrorClass(str, enum.Enum):
    TRANSIENT = "transient"   # retryable; receiver should keep strategy active
    TERMINAL = "terminal"     # not retryable; receiver moves strategy to fired
    LEVERAGE = "leverage"     # set-leverage failed; do not place order


@dataclass(frozen=True)
class OrderResult:
    order_id: str
    raw: dict


class GrvtError(Exception):
    def __init__(self, message: str, error_class: ErrorClass) -> None:
        super().__init__(message)
        self.error_class = error_class


class _GrvtClient(Protocol):
    def fetch_mini_ticker(self, symbol: str) -> dict: ...
    def set_leverage(self, leverage: int, symbol: str) -> dict: ...
    def create_order(self, **kwargs: Any) -> dict: ...
    def fetch_order(self, id: str, **kwargs: Any) -> dict: ...


class _TriggerClient(Protocol):
    def place_trigger_close(
        self, *, symbol: str, side: str, amount: float,
        trigger_price: float, trigger_type: str,
    ) -> dict: ...
    def set_initial_leverage(self, *, symbol: str, leverage: int) -> dict: ...
    def place_entry_with_tpsl(
        self, *, symbol: str, entry_side: str, amount: float,
        reference_price: float,
        tp_pct: Optional[float] = None, sl_pct: Optional[float] = None,
        order_type: str = "market", limit_price: Optional[float] = None,
    ) -> dict: ...


def _classify(exc: Exception) -> ErrorClass:
    msg = str(exc).lower()
    for pat in _TERMINAL_PATTERNS:
        if pat in msg:
            return ErrorClass.TERMINAL
    return ErrorClass.TRANSIENT


class GrvtExecutor:
    """
    Wraps a GRVT client. Inject the client (e.g., GrvtCcxt) at construction time
    so tests can use a fake.
    """

    def __init__(
        self,
        *,
        client: _GrvtClient,
        max_attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        trigger_client: Optional[_TriggerClient] = None,
    ) -> None:
        self.client = client
        self.max_attempts = max_attempts
        self.sleep = sleep
        self.clock = clock
        self._trigger_client = trigger_client

    def fetch_mid_price(self, symbol: str) -> float:
        ticker = self.client.fetch_mini_ticker(symbol)
        # GRVT mini-ticker fields (verified 2026-05-06 against prod):
        # mid_price, mark_price, index_price, last_price.
        # Prefer mid_price; fall back to last_price, then mark_price.
        for field in ("mid_price", "last_price", "mark_price"):
            v = ticker.get(field)
            if v is not None:
                return float(v)
        raise KeyError(
            f"no usable price field in mini_ticker for {symbol}: keys={list(ticker.keys())}"
        )

    def set_leverage(self, *, symbol: str, leverage: int) -> None:
        # GRVT deprecated set_initial_leverage_v1 (2026; error code 2106:
        # "This API has been deprecated and can no longer be used to set
        # leverage"). On modern GRVT cross-margin accounts, leverage is
        # determined at the account/sub-account level, not per-order.
        #
        # We KEEP the call best-effort because some accounts may still
        # support it, and a successful set_leverage is harmless. But if
        # GRVT returns the deprecated error, we log a warning and PROCEED
        # to order placement (the account's existing leverage will apply).
        # Other failures (auth, network) still raise, since they likely
        # indicate a bigger problem.
        try:
            if self._trigger_client is not None and hasattr(
                self._trigger_client, "set_initial_leverage"
            ):
                self._trigger_client.set_initial_leverage(
                    symbol=symbol, leverage=leverage
                )
            else:
                self.client.set_leverage(leverage, symbol)
        except Exception as exc:
            msg = str(exc).lower()
            if "deprecated" in msg or "code=2106" in msg or "(code=2106)" in msg:
                logger.warning(
                    "set_leverage skipped: GRVT API deprecated (account-level "
                    "leverage applies). symbol=%s requested=%dx err=%s",
                    symbol, leverage, exc,
                )
                return
            raise GrvtError(
                f"set_leverage failed for {symbol} @ {leverage}x: {exc}",
                error_class=ErrorClass.LEVERAGE,
            ) from exc

    def place_order(
        self,
        *,
        symbol: str,
        side: str,
        amount: float,
        order_type: str,
        price: Optional[float],
        time_in_force: Optional[str],
        reduce_only: bool,
    ) -> OrderResult:
        # DEPRECATED for the entry+TPSL flow. Use place_entry_with_tpsl
        # instead, which submits entry + TP + SL atomically via GRVT's
        # full/v2/bulk_orders endpoint. Kept here for any single-leg path
        # that doesn't need atomic TP/SL armament.
        params: dict = {}
        if time_in_force:
            params["time_in_force"] = time_in_force
        if reduce_only:
            params["reduce_only"] = True

        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                resp = self.client.create_order(
                    symbol=symbol,
                    order_type=order_type,
                    side=side,
                    amount=amount,
                    price=price,
                    params=params,
                )
                order_id = str(resp.get("id"))
                return OrderResult(order_id=order_id, raw=resp)
            except Exception as exc:
                last_exc = exc
                cls = _classify(exc)
                if cls is ErrorClass.TERMINAL:
                    raise GrvtError(str(exc), error_class=ErrorClass.TERMINAL) from exc
                # transient: retry with backoff unless it was the last attempt
                if attempt < self.max_attempts:
                    backoff = 0.5 * (2 ** (attempt - 1))
                    logger.warning(
                        "grvt transient error (attempt %d/%d), retrying in %.1fs: %s",
                        attempt, self.max_attempts, backoff, exc,
                    )
                    self.sleep(backoff)
                    continue
        # exhausted retries
        raise GrvtError(
            f"transient grvt error after {self.max_attempts} attempts: {last_exc}",
            error_class=ErrorClass.TRANSIENT,
        ) from last_exc

    def wait_for_fill(
        self,
        *,
        symbol: str,
        order_id: str,
        timeout_secs: float = 10.0,
        poll_interval: float = 0.5,
    ) -> Optional[float]:
        """
        DEPRECATED. The receiver no longer waits for entry fills before
        arming TP/SL: TP/SL are computed off the trigger-time mid and
        submitted in the same OTOCO bulk_orders request via
        place_entry_with_tpsl. Kept for any callers / tests that still
        pre-place entries and arm TP/SL afterwards.

        Poll fetch_order until the order reports FILLED, then return the
        avg_fill_price (first leg). Return None if it doesn't fill within
        ``timeout_secs`` or any polling call errors.

        ``symbol`` is currently unused by the GRVT fetch_order endpoint (the
        order_id is sufficient) but is accepted to keep the executor's surface
        stable if future GRVT API revisions require it.
        """
        deadline = self.clock() + timeout_secs
        while True:
            try:
                resp = self.client.fetch_order(id=order_id)
            except Exception as exc:  # noqa: BLE001 , best-effort polling
                logger.warning("fetch_order raised while polling for fill: %s", exc)
                resp = {}

            state = (resp.get("result") or {}).get("state") or {}
            status = state.get("status") or ""
            if status.upper() == "FILLED":
                fills = state.get("avg_fill_price") or []
                if fills:
                    try:
                        return float(fills[0])
                    except (TypeError, ValueError):
                        logger.warning("avg_fill_price not parseable: %r", fills)
                        return None
                return None

            if self.clock() >= deadline:
                return None
            self.sleep(poll_interval)

    def place_tpsl_pair(
        self,
        *,
        symbol: str,
        entry_side: str,
        amount: float,
        fill_price: float,
        tp_pct: Optional[float],
        sl_pct: Optional[float],
    ) -> dict:
        """
        DEPRECATED. Superseded by place_entry_with_tpsl, which submits the
        entry and TP/SL atomically. Kept as a fallback for tests and for any
        recovery path that needs to attach TP/SL to a pre-existing fill.

        Place TP and/or SL close orders following a successful entry.

        ``entry_side`` is the side that just filled ('buy' = long, 'sell' =
        short). The close side is the opposite.
        ``tp_pct`` and ``sl_pct`` are percentages (e.g. 1.5 = 1.5%). Either
        may be None to skip that leg.

        Never raises: returns a dict describing what was placed and what
        failed. Caller (receiver) emits alerts based on the contents.
        """
        result: dict = {
            "tp_order_id": None,
            "sl_order_id": None,
            "tp_price": None,
            "sl_price": None,
            "errors": [],
        }

        if entry_side == "sell":
            close_side = "buy"
            tp_price = fill_price * (1 - (tp_pct or 0) / 100) if tp_pct is not None else None
            sl_price = fill_price * (1 + (sl_pct or 0) / 100) if sl_pct is not None else None
        elif entry_side == "buy":
            close_side = "sell"
            tp_price = fill_price * (1 + (tp_pct or 0) / 100) if tp_pct is not None else None
            sl_price = fill_price * (1 - (sl_pct or 0) / 100) if sl_pct is not None else None
        else:
            result["errors"].append(f"invalid entry_side: {entry_side!r}")
            return result

        result["tp_price"] = tp_price
        result["sl_price"] = sl_price

        # ---------- TP via standard CCXT path ----------
        if tp_price is not None:
            try:
                resp = self.client.create_order(
                    symbol=symbol,
                    order_type="limit",
                    side=close_side,
                    amount=amount,
                    price=tp_price,
                    params={"reduce_only": True},
                )
                result["tp_order_id"] = str(resp.get("id"))
            except Exception as exc:  # noqa: BLE001 , never raise from TP/SL
                logger.exception("TP submission failed for %s @ %s", symbol, tp_price)
                result["errors"].append(f"TP submit failed: {exc}")

        # ---------- SL via raw trigger client ----------
        if sl_price is not None:
            if self._trigger_client is None:
                result["errors"].append(
                    "SL requested but no trigger_client configured on executor"
                )
            else:
                try:
                    resp = self._trigger_client.place_trigger_close(
                        symbol=symbol,
                        side=close_side,
                        amount=amount,
                        trigger_price=sl_price,
                        trigger_type="STOP_LOSS",
                    )
                    # Trigger client returns either the SDK dict response or
                    # an ApiCreateOrderResponse-like object , be lenient.
                    order_id = None
                    if isinstance(resp, dict):
                        inner = resp.get("result") or {}
                        if isinstance(inner, dict):
                            order_id = inner.get("order_id") or inner.get("id")
                    result["sl_order_id"] = str(order_id) if order_id else "submitted"
                except Exception as exc:  # noqa: BLE001 , never raise from TP/SL
                    logger.exception("SL submission failed for %s @ %s", symbol, sl_price)
                    result["errors"].append(f"SL submit failed: {exc}")

        return result

    def place_entry_with_tpsl(
        self,
        *,
        symbol: str,
        entry_side: str,
        amount: float,
        order_type: str,
        limit_price: Optional[float],
        reference_price: float,
        tp_pct: Optional[float],
        sl_pct: Optional[float],
    ) -> dict:
        """
        Atomic entry + optional TP/SL submission via the trigger client's
        bulk_orders v2 path.

        ``reference_price`` is the price used to compute TP/SL absolute
        prices from percentages. Receiver passes the current mid at trigger
        time so the TP/SL band tracks live market conditions even before
        the entry fills.

        Never raises: input-validation errors are caught and surfaced via
        ``errors`` so the receiver can decide between a normal info alert
        and a manual_intervention_required alert.

        Returns a dict shaped like:
            {
              "parent_order_id": <str | None>,
              "tp_order_id":     <str | None>,
              "sl_order_id":     <str | None>,
              "tp_price":        <float | None>,
              "sl_price":        <float | None>,
              "errors":          <list[str]>,
            }
        """
        if self._trigger_client is None:
            return {
                "parent_order_id": None,
                "tp_order_id": None, "sl_order_id": None,
                "tp_price": None, "sl_price": None,
                "errors": ["place_entry_with_tpsl requires a trigger_client on the executor"],
            }

        try:
            return self._trigger_client.place_entry_with_tpsl(
                symbol=symbol,
                entry_side=entry_side,
                amount=amount,
                reference_price=reference_price,
                tp_pct=tp_pct,
                sl_pct=sl_pct,
                order_type=order_type,
                limit_price=limit_price,
            )
        except Exception as exc:  # noqa: BLE001 , never raise; surface in errors
            logger.exception(
                "place_entry_with_tpsl failed before/at submission: %s", exc
            )
            return {
                "parent_order_id": None,
                "tp_order_id": None, "sl_order_id": None,
                "tp_price": None, "sl_price": None,
                "errors": [f"bulk_orders submission failed: {exc}"],
            }
