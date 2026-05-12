"""
Trigger-order submission via the raw GRVT SDK.

The CCXT wrapper (`pysdk.grvt_ccxt.GrvtCcxt.create_order`) does not expose
trigger fields , its OrderMetadata has no `trigger`. So for stop-loss / take-
profit orders we go through `pysdk.grvt_raw_sync.GrvtRawSync.create_order_v1`
directly and sign the payload ourselves with `sign_order`.

This module owns the raw client, the eth_account, and the cached instrument
dict. It exposes:

- `place_trigger_close`: legacy single-leg trigger close (kept for
  back-compat; new code should not call this).
- `place_entry_with_tpsl`: atomic OTOCO / OTO / single-entry submission via
  GRVT's `full/v2/bulk_orders` endpoint. The pysdk does not ship a v2 client,
  so we POST JSON directly using the underlying authenticated session
  (`GrvtRawSyncBase._post`), which handles cookie refresh transparently.
- `place_close_with_oco`: atomic TP+SL OCO submission for closing an
  already-open position.
"""

from __future__ import annotations

import logging
import random
import time
from decimal import Decimal
from typing import List, Optional

from eth_account import Account

from pysdk.grvt_raw_base import GrvtApiConfig, GrvtError as RawGrvtError
from pysdk.grvt_raw_env import GrvtEnv as RawGrvtEnv
from pysdk.grvt_raw_signing import sign_order
from pysdk.grvt_raw_sync import GrvtRawSync
from pysdk.grvt_raw_types import (
    ApiCreateOrderRequest,
    ApiGetAllInstrumentsRequest,
    ApiSetInitialLeverageRequest,
    Order,
    OrderLeg,
    OrderMetadata,
    Signature,
    TimeInForce,
    TPSLOrderMetadata,
    TriggerBy,
    TriggerOrderMetadata,
    TriggerType,
)

logger = logging.getLogger(__name__)

_VALID_SIDES = {"buy", "sell"}
_VALID_TRIGGER_TYPES = {"TAKE_PROFIT", "STOP_LOSS"}
_VALID_ORDER_TYPES = {"market", "limit"}

# Signatures must expire; pick a generous default well under the 30-day cap.
_SIG_EXPIRATION_SECONDS = 24 * 3600

# Per the SDK docstring, client-generated nonces should fall in [2^63, 2^64-1]
# so they don't collide with UI-generated ones.
# client_order_id is 64-bit per GRVT docs; client machines should pick from
# [2**63, 2**64 - 1] to avoid colliding with the GRVT UI's [0, 2**63 - 1]
# range.
_CLIENT_ORDER_ID_LO = 2**63
_CLIENT_ORDER_ID_HI = 2**64 - 1

# nonce is encoded in EIP-712 as uint32, so it must fit in 32 bits.
_NONCE_LO = 0
_NONCE_HI = 2**32 - 1


def _gen_nonce() -> int:
    return random.randint(_NONCE_LO, _NONCE_HI)


def _gen_client_order_id() -> str:
    return str(random.randint(_CLIENT_ORDER_ID_LO, _CLIENT_ORDER_ID_HI))


class GrvtTriggerClient:
    """
    Lazily-initialised trigger-order client.

    Constructor does NOT call any network APIs (that would couple receiver
    boot to GRVT availability). The first network-using call fetches and
    caches the instrument dict needed for signing.
    """

    def __init__(
        self,
        *,
        env: str,
        trading_account_id: str,
        private_key: str,
        api_key: str,
    ) -> None:
        self._raw_env = RawGrvtEnv(env)
        self._config = GrvtApiConfig(
            env=self._raw_env,
            trading_account_id=trading_account_id,
            private_key=private_key,
            api_key=api_key,
            logger=None,
        )
        self._client = GrvtRawSync(self._config)
        self._account = Account.from_key(private_key)
        self._trading_account_id = trading_account_id
        self._instruments: Optional[dict] = None

    def _get_instruments(self) -> dict:
        if self._instruments is None:
            resp = self._client.get_all_instruments_v1(
                ApiGetAllInstrumentsRequest(is_active=True)
            )
            if isinstance(resp, RawGrvtError):
                raise RuntimeError(
                    f"get_all_instruments_v1 failed: {resp.message} (code={resp.code})"
                )
            self._instruments = {inst.instrument: inst for inst in resp.result}
        return self._instruments

    def set_initial_leverage(self, *, symbol: str, leverage: int) -> dict:
        """
        Set initial leverage for the sub-account on a specific instrument.

        Routed through the raw API because pysdk.grvt_ccxt.GrvtCcxt does not
        expose this method (verified 2026-05-06 against pysdk).
        """
        req = ApiSetInitialLeverageRequest(
            sub_account_id=self._trading_account_id,
            instrument=symbol,
            leverage=str(leverage),
        )
        resp = self._client.set_initial_leverage_v1(req)
        if isinstance(resp, RawGrvtError):
            raise RuntimeError(
                f"set_initial_leverage failed for {symbol} @ {leverage}x: "
                f"{resp.message} (code={resp.code})"
            )
        return {"success": getattr(resp, "success", True)}

    def place_trigger_close(
        self,
        *,
        symbol: str,
        side: str,
        amount: float,
        trigger_price: float,
        trigger_type: str,
    ) -> dict:
        """
        Submit a reduce-only trigger order that closes the position when the
        mark price crosses ``trigger_price``.

        ``side`` is the close side: 'buy' to close a short, 'sell' to close a
        long. ``trigger_type`` is 'TAKE_PROFIT' or 'STOP_LOSS'.

        Returns the raw SDK response dict on success.
        Raises RuntimeError if the SDK surfaces a GrvtError, ValueError on
        bad input.
        """
        if side not in _VALID_SIDES:
            raise ValueError(f"side must be one of {_VALID_SIDES}, got {side!r}")
        if trigger_type not in _VALID_TRIGGER_TYPES:
            raise ValueError(
                f"trigger_type must be one of {_VALID_TRIGGER_TYPES}, "
                f"got {trigger_type!r}"
            )

        instruments = self._get_instruments()
        if symbol not in instruments:
            raise RuntimeError(f"unknown instrument: {symbol!r}")

        # Trigger orders submit a market order on activation (close_position
        # flag), so limit_price doesn't gate execution. We still pass a
        # nominal value (the trigger price) to satisfy the signing path,
        # which expects a numeric leg price.
        leg = OrderLeg(
            instrument=symbol,
            size=str(amount),
            is_buying_asset=(side == "buy"),
            limit_price=str(trigger_price),
        )

        now_ns = time.time_ns()
        signature = Signature(
            signer="",
            r="",
            s="",
            v=0,
            expiration=str(now_ns + _SIG_EXPIRATION_SECONDS * 1_000_000_000),
            nonce=_gen_nonce(),
        )

        metadata = OrderMetadata(
            client_order_id=_gen_client_order_id(),
            trigger=TriggerOrderMetadata(
                trigger_type=TriggerType(trigger_type),
                tpsl=TPSLOrderMetadata(
                    trigger_by=TriggerBy.MARK,
                    trigger_price=str(trigger_price),
                    close_position=True,
                ),
            ),
        )

        order = Order(
            sub_account_id=self._trading_account_id,
            time_in_force=TimeInForce.GOOD_TILL_TIME,
            legs=[leg],
            signature=signature,
            metadata=metadata,
            is_market=True,  # trigger fires a market close
            post_only=False,
            reduce_only=True,
        )

        signed = sign_order(order, self._config, self._account, instruments)
        resp = self._client.create_order_v1(ApiCreateOrderRequest(order=signed))
        if isinstance(resp, RawGrvtError):
            raise RuntimeError(
                f"create_order_v1 failed: {resp.message} (code={resp.code})"
            )
        # The SDK returns a dataclass on success but raw dict elsewhere; for
        # the executor's needs (order id discovery, error reporting) we hand
        # back whatever was returned.
        return resp  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Bulk orders v2: OTOCO / OCO / OTO / single
    # ------------------------------------------------------------------

    def _build_signed_order(
        self,
        *,
        symbol: str,
        side: str,
        amount: float,
        is_market: bool,
        limit_price: float,
        reduce_only: bool,
        time_in_force: TimeInForce,
        trigger_type: Optional[str],
        trigger_price: Optional[float],
        instruments: dict,
    ) -> Order:
        """
        Construct a fully-signed `Order` ready to be packed into a bulk
        request. Caller is responsible for passing already-fetched
        ``instruments`` so we sign once per batch instead of per leg.

        For market orders, `limit_price` on the leg MUST be "0" (verified
        empirically; GRVT error 2020 if not). For trigger orders that
        market-out on activation, the leg `limit_price` is also "0" because
        the leg itself is treated as a market order.
        """
        if side not in _VALID_SIDES:
            raise ValueError(f"side must be one of {_VALID_SIDES}, got {side!r}")

        leg = OrderLeg(
            instrument=symbol,
            size=str(amount),
            is_buying_asset=(side == "buy"),
            limit_price="0" if is_market else str(limit_price),
        )

        now_ns = time.time_ns()
        signature = Signature(
            signer="",
            r="",
            s="",
            v=0,
            expiration=str(now_ns + _SIG_EXPIRATION_SECONDS * 1_000_000_000),
            nonce=_gen_nonce(),
        )

        if trigger_type is not None:
            if trigger_type not in _VALID_TRIGGER_TYPES:
                raise ValueError(
                    f"trigger_type must be one of {_VALID_TRIGGER_TYPES}, "
                    f"got {trigger_type!r}"
                )
            if trigger_price is None:
                raise ValueError("trigger_price required when trigger_type set")
            metadata = OrderMetadata(
                client_order_id=_gen_client_order_id(),
                trigger=TriggerOrderMetadata(
                    trigger_type=TriggerType(trigger_type),
                    tpsl=TPSLOrderMetadata(
                        trigger_by=TriggerBy.MARK,
                        trigger_price=str(trigger_price),
                        # close_position=False on OTOCO/OCO legs: amount is
                        # explicit on the leg, and bulk_orders rejects mixed
                        # close_position semantics across the batch.
                        close_position=False,
                    ),
                ),
            )
        else:
            metadata = OrderMetadata(
                client_order_id=_gen_client_order_id(),
                trigger=None,
            )

        order = Order(
            sub_account_id=self._trading_account_id,
            time_in_force=time_in_force,
            legs=[leg],
            signature=signature,
            metadata=metadata,
            is_market=is_market,
            post_only=False,
            reduce_only=reduce_only,
        )
        return sign_order(order, self._config, self._account, instruments)

    def _post_bulk_orders(self, orders: List[Order]) -> dict:
        """
        POST a list of signed orders to ``full/v2/bulk_orders``.

        We bypass the SDK because pysdk only ships v1 endpoints. We reuse
        ``GrvtRawSyncBase._post`` directly: it (a) refreshes the auth cookie
        if needed, (b) JSON-encodes via DataclassJSONEncoder so dataclass
        Orders serialize correctly inside our plain-dict envelope, and
        (c) returns a parsed dict.
        """
        url = self._client.td_rpc + "/full/v2/bulk_orders"
        payload = {
            "sub_account_id": self._trading_account_id,
            "orders": orders,
        }
        # is_auth=True triggers cookie refresh before the request.
        return self._client._post(True, url, payload)

    @staticmethod
    def _extract_order_id(entry: object) -> Optional[str]:
        """
        Pull out a usable order id from one bulk_orders response entry.

        bulk_orders returns a list of objects mirroring the request order
        list. Each entry, on success, exposes an Order dataclass-shaped dict
        with `order_id` (and `metadata.client_order_id`). Errors come back
        as `{"code": ..., "message": ...}` shaped entries. We do NOT assume
        a single canonical shape; we probe defensively.
        """
        if entry is None:
            return None
        if isinstance(entry, dict):
            # Direct order_id at top level.
            oid = entry.get("order_id")
            if oid:
                return str(oid)
            # Some payloads wrap each result: {"result": {...order...}}.
            inner = entry.get("result")
            if isinstance(inner, dict):
                oid = inner.get("order_id")
                if oid:
                    return str(oid)
                meta = inner.get("metadata") or {}
                if isinstance(meta, dict) and meta.get("client_order_id"):
                    return str(meta["client_order_id"])
            meta = entry.get("metadata") or {}
            if isinstance(meta, dict) and meta.get("client_order_id"):
                return str(meta["client_order_id"])
        return None

    @staticmethod
    def _extract_error(entry: object) -> Optional[str]:
        """Return a non-empty error string if `entry` represents a failure."""
        if isinstance(entry, dict):
            code = entry.get("code")
            msg = entry.get("message")
            if code and msg:
                return f"code={code}: {msg}"
            if msg and not entry.get("order_id") and not entry.get("result"):
                return str(msg)
        return None

    def place_entry_with_tpsl(
        self,
        *,
        symbol: str,
        entry_side: str,
        amount: float,
        reference_price: float,
        tp_pct: Optional[float] = None,
        sl_pct: Optional[float] = None,
        order_type: str = "market",
        limit_price: Optional[float] = None,
    ) -> dict:
        """
        Submit an entry plus optional TP and SL atomically via
        ``full/v2/bulk_orders``.

        Shapes:
        - tp_pct AND sl_pct: OTOCO (parent + TP + SL), 3 orders.
        - tp_pct XOR sl_pct: OTO (parent + 1 trigger), 2 orders.
        - neither: single entry, 1 order.

        ``reference_price`` is the price used to compute TP/SL absolute
        prices from the percentages. Caller typically passes the current
        mid at trigger time so TP/SL track the live market rather than a
        stale fill expectation.

        Returns a dict:
            {
              "parent_order_id": <str | None>,
              "tp_order_id":     <str | None>,
              "sl_order_id":     <str | None>,
              "tp_price":        <float | None>,
              "sl_price":        <float | None>,
              "errors":          <list[str]>,
              "raw":             <full bulk response>,
            }

        The receiver inspects ``errors`` to decide between an info alert and
        a manual_intervention_required alert. This method never raises on
        partial-success: input-validation errors raise ValueError before the
        network call; any post-submit issue is surfaced via ``errors``.
        """
        if entry_side not in _VALID_SIDES:
            raise ValueError(
                f"entry_side must be one of {_VALID_SIDES}, got {entry_side!r}"
            )
        if order_type not in _VALID_ORDER_TYPES:
            raise ValueError(
                f"order_type must be one of {_VALID_ORDER_TYPES}, got {order_type!r}"
            )
        if order_type == "limit" and limit_price is None:
            raise ValueError("limit_price required when order_type='limit'")
        if reference_price <= 0:
            raise ValueError(f"reference_price must be > 0, got {reference_price}")

        # --- Compute TP/SL absolute prices ---------------------------------
        close_side = "sell" if entry_side == "buy" else "buy"
        if entry_side == "buy":
            tp_price = (
                reference_price * (1 + (tp_pct or 0) / 100)
                if tp_pct is not None else None
            )
            sl_price = (
                reference_price * (1 - (sl_pct or 0) / 100)
                if sl_pct is not None else None
            )
        else:  # entry_side == "sell"
            tp_price = (
                reference_price * (1 - (tp_pct or 0) / 100)
                if tp_pct is not None else None
            )
            sl_price = (
                reference_price * (1 + (sl_pct or 0) / 100)
                if sl_pct is not None else None
            )

        instruments = self._get_instruments()
        if symbol not in instruments:
            raise RuntimeError(f"unknown instrument: {symbol!r}")

        # Align TP/SL prices to the instrument's tick_size BEFORE signing.
        # If we sign a price like 88.21872499999999 but GRVT validates
        # against tick-aligned 88.22, the EIP-712 hash recomputed on the
        # server differs from ours and we get error 2002 (signature
        # mismatch). Round to the nearest tick (round-half-even via
        # Decimal.quantize) so our signed payload exactly matches what
        # the server rebuilds. Verified empirically against prod 2026-05-06.
        tick_size_str = getattr(instruments[symbol], "tick_size", None) or "0.01"
        tick = Decimal(str(tick_size_str))
        if tp_price is not None:
            tp_price = float(
                (Decimal(str(tp_price)) / tick).quantize(Decimal("1")) * tick
            )
        if sl_price is not None:
            sl_price = float(
                (Decimal(str(sl_price)) / tick).quantize(Decimal("1")) * tick
            )

        # --- Build orders --------------------------------------------------
        orders: List[Order] = []

        # Parent (entry).
        parent_is_market = order_type == "market"
        parent = self._build_signed_order(
            symbol=symbol,
            side=entry_side,
            amount=amount,
            is_market=parent_is_market,
            limit_price=limit_price if not parent_is_market else 0,
            reduce_only=False,
            # GTT works for both market and limit; matches the existing
            # trigger-close path. IOC could also work for market entries
            # but GTT is what the existing code uses, so we keep it.
            time_in_force=TimeInForce.GOOD_TILL_TIME,
            trigger_type=None,
            trigger_price=None,
            instruments=instruments,
        )
        orders.append(parent)

        # TP leg: limit reduce-only on the opposite side, with TAKE_PROFIT
        # trigger metadata.
        if tp_price is not None:
            tp = self._build_signed_order(
                symbol=symbol,
                side=close_side,
                amount=amount,
                is_market=False,
                limit_price=tp_price,
                reduce_only=True,
                time_in_force=TimeInForce.GOOD_TILL_TIME,
                trigger_type="TAKE_PROFIT",
                trigger_price=tp_price,
                instruments=instruments,
            )
            orders.append(tp)

        # SL leg: market reduce-only on the opposite side, with STOP_LOSS
        # trigger metadata. Market => leg limit_price="0".
        if sl_price is not None:
            sl = self._build_signed_order(
                symbol=symbol,
                side=close_side,
                amount=amount,
                is_market=True,
                limit_price=0,
                reduce_only=True,
                time_in_force=TimeInForce.GOOD_TILL_TIME,
                trigger_type="STOP_LOSS",
                trigger_price=sl_price,
                instruments=instruments,
            )
            orders.append(sl)

        # --- Submit --------------------------------------------------------
        raw = self._post_bulk_orders(orders)
        return self._parse_bulk_response(
            raw=raw,
            slot_names=self._slot_names_for_entry(
                tp_present=tp_price is not None,
                sl_present=sl_price is not None,
            ),
            tp_price=tp_price,
            sl_price=sl_price,
            client_order_ids=[o.metadata.client_order_id for o in orders],
        )

    def place_close_with_oco(
        self,
        *,
        symbol: str,
        close_side: str,
        amount: float,
        tp_price: float,
        sl_price: float,
    ) -> dict:
        """
        Submit a TP+SL OCO pair to close an already-open position
        atomically via ``full/v2/bulk_orders``.

        Both legs are reduce_only on the same ``close_side`` with the same
        size. The receiver path does not currently call this; it exists for
        retroactive TP/SL adds (e.g. operator tooling, recovery after a
        partial OTOCO failure).

        Returns the same dict shape as ``place_entry_with_tpsl`` but with
        ``parent_order_id=None``.
        """
        if close_side not in _VALID_SIDES:
            raise ValueError(
                f"close_side must be one of {_VALID_SIDES}, got {close_side!r}"
            )

        instruments = self._get_instruments()
        if symbol not in instruments:
            raise RuntimeError(f"unknown instrument: {symbol!r}")

        tp = self._build_signed_order(
            symbol=symbol,
            side=close_side,
            amount=amount,
            is_market=False,
            limit_price=tp_price,
            reduce_only=True,
            time_in_force=TimeInForce.GOOD_TILL_TIME,
            trigger_type="TAKE_PROFIT",
            trigger_price=tp_price,
            instruments=instruments,
        )
        sl = self._build_signed_order(
            symbol=symbol,
            side=close_side,
            amount=amount,
            is_market=True,
            limit_price=0,
            reduce_only=True,
            time_in_force=TimeInForce.GOOD_TILL_TIME,
            trigger_type="STOP_LOSS",
            trigger_price=sl_price,
            instruments=instruments,
        )
        orders = [tp, sl]
        raw = self._post_bulk_orders(orders)
        return self._parse_bulk_response(
            raw=raw,
            slot_names=["tp", "sl"],
            tp_price=tp_price,
            sl_price=sl_price,
            client_order_ids=[o.metadata.client_order_id for o in orders],
        )

    @staticmethod
    def _slot_names_for_entry(
        *, tp_present: bool, sl_present: bool
    ) -> List[str]:
        names = ["parent"]
        if tp_present:
            names.append("tp")
        if sl_present:
            names.append("sl")
        return names

    @classmethod
    def _parse_bulk_response(
        cls,
        *,
        raw: dict,
        slot_names: List[str],
        tp_price: Optional[float],
        sl_price: Optional[float],
        client_order_ids: List[str],
    ) -> dict:
        """
        Translate the raw bulk_orders response into the executor-facing
        dict. Best-effort across response shapes:
          - ``raw`` may itself be ``{"code": ..., "message": ...}`` for a
            top-level error (e.g. 401, malformed batch). We mark every leg
            failed in that case.
          - ``raw`` may be ``{"result": [<entry>, <entry>, ...]}`` or
            ``{"results": [...]}`` per docs sections; we accept either.
          - Each entry, on success, exposes ``order_id``; on failure, a
            ``code``/``message``.

        Falls back to client_order_id when the server didn't return a
        backend order_id (e.g. accepted-but-not-yet-assigned states).
        """
        out: dict = {
            "parent_order_id": None,
            "tp_order_id": None,
            "sl_order_id": None,
            "tp_price": tp_price,
            "sl_price": sl_price,
            "errors": [],
            "raw": raw,
        }

        # Top-level error: every slot fails the same way.
        if isinstance(raw, dict) and raw.get("code") and raw.get("message"):
            err = f"bulk_orders failed: code={raw.get('code')}: {raw.get('message')}"
            out["errors"].append(err)
            return out

        entries: list = []
        if isinstance(raw, dict):
            for key in ("result", "results", "orders"):
                v = raw.get(key)
                if isinstance(v, list):
                    entries = v
                    break
        elif isinstance(raw, list):
            entries = raw

        # If we got nothing parseable, surface the response as-is.
        if not entries:
            out["errors"].append(
                f"bulk_orders returned no parseable order entries; raw={raw!r}"
            )
            return out

        for slot_idx, slot in enumerate(slot_names):
            entry = entries[slot_idx] if slot_idx < len(entries) else None
            err = cls._extract_error(entry)
            if err:
                out["errors"].append(f"{slot}: {err}")
                continue
            oid = cls._extract_order_id(entry)
            if oid is None and slot_idx < len(client_order_ids):
                # Best-effort fallback so receivers always have an id.
                oid = client_order_ids[slot_idx]
            out[f"{slot}_order_id"] = oid

        return out
