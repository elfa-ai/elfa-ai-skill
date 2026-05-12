from __future__ import annotations

import json
import logging
import threading
import time
from typing import Optional, Protocol

from fastapi import BackgroundTasks, FastAPI, Header, Request, Response

from .alerts import AlertWriter
from .config import Config
from .guardrails import Allow, Reject, check_guardrails
from .grvt_executor import GrvtError
from .registry import Registry, Strategy

logger = logging.getLogger(__name__)


class _Executor(Protocol):
    def fetch_mid_price(self, symbol: str) -> float: ...
    def set_leverage(self, *, symbol: str, leverage: int) -> None: ...
    def place_entry_with_tpsl(
        self, *, symbol: str, entry_side: str, amount: float,
        order_type: str, limit_price: Optional[float],
        reference_price: float,
        tp_pct: Optional[float], sl_pct: Optional[float],
    ) -> dict: ...


def create_app(
    *,
    config: Config,
    registry: Registry,
    executor: _Executor,
    alerts: AlertWriter,
) -> FastAPI:
    app = FastAPI(title="elfa-grvt-bot receiver")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.post("/auto/events")
    async def auto_events(
        request: Request,
        background_tasks: BackgroundTasks,
        # Elfa Auto webhook delivery is unsigned (no X-Auto-Signature header).
        # Only X-Auto-Event-Id is required, used as a dedupe key. SECURITY
        # NOTE: anyone who guesses the public webhook URL could fire trades;
        # mitigations are private tunnel URLs and the per-strategy notional
        # cap enforced in guardrails.
        x_auto_event_id: Optional[str] = Header(default=None, alias="X-Auto-Event-Id"),
    ) -> Response:
        if not x_auto_event_id:
            logger.warning("missing X-Auto-Event-Id header")
            return Response(status_code=400, content="missing X-Auto-Event-Id")
        raw = await request.body()
        background_tasks.add_task(
            _process_event,
            event_id=x_auto_event_id,
            raw=raw,
            registry=registry,
            executor=executor,
            alerts=alerts,
            config=config,
        )
        # Pass background_tasks explicitly so they run regardless of FastAPI's
        # implicit response-attachment behavior across versions.
        return Response(status_code=200, content="ok", background=background_tasks)

    return app


def _process_event(
    *,
    event_id: str,
    raw: bytes,
    registry: Registry,
    executor: _Executor,
    alerts: AlertWriter,
    config: Config,
) -> None:
    """Top-level safety net.

    Any uncaught exception inside ``_process_event_inner`` (malformed JSON,
    sqlite errors, …) would otherwise vanish into the background task with the
    200 already returned to Auto. Catch everything and emit a high-severity
    alert so the operator hears about it on Telegram.
    """
    try:
        _process_event_inner(
            event_id=event_id,
            raw=raw,
            registry=registry,
            executor=executor,
            alerts=alerts,
            config=config,
        )
    except Exception as exc:  # noqa: BLE001 , top-level safety net
        logger.exception("unhandled error processing event %s", event_id)
        try:
            alerts.emit(
                severity="error",
                category="receiver_internal_error",
                message=(
                    f"unhandled exception processing event_id={event_id!r}: "
                    f"{type(exc).__name__}: {exc}"
                ),
                fire_event_id=event_id,
                details={
                    "exception_type": type(exc).__name__,
                    "raw_payload": raw.decode(errors="replace")[:1000],
                },
            )
        except Exception:  # noqa: BLE001 , alerting itself failed
            logger.exception("alert emission failed for event %s", event_id)


def _process_event_inner(
    *,
    event_id: str,
    raw: bytes,
    registry: Registry,
    executor: _Executor,
    alerts: AlertWriter,
    config: Config,
) -> None:
    payload = json.loads(raw.decode() or "{}")
    query_id = payload.get("queryId") or payload.get("query_id") or ""
    received_at = int(time.time())

    inserted = registry.insert_fire_if_new(
        event_id=event_id,
        query_id=query_id,
        received_at=received_at,
        outcome="pending",
        raw_payload=raw.decode(errors="replace"),
    )
    if not inserted:
        logger.info("duplicate event %s , skipped", event_id)
        return

    strategy: Optional[Strategy] = (
        registry.get_strategy(query_id) if query_id else None
    )
    if strategy is None:
        registry.update_fire_outcome(event_id, outcome="unknown_strategy")
        alerts.emit(
            severity="error",
            category="unknown_strategy",
            message=f"no strategy registered for queryId={query_id!r}",
            query_id=query_id or None,
            fire_event_id=event_id,
        )
        return

    # Inline silent status check before anything else. Auto retries on
    # already-fired strategies should NOT ping Telegram. This used to live
    # inside check_guardrails (category="guardrail_status"); we promote it
    # here so the trigger_received Telegram ping below can fire as early as
    # possible without spamming on retries.
    if strategy.status != "active":
        reason = f"strategy status is {strategy.status!r}, only 'active' fires"
        registry.update_fire_outcome(
            event_id, outcome="rejected_guardrail", error=reason
        )
        logger.info("rejecting fire for non-active strategy: %s", reason)
        return

    # IMMEDIATE Telegram ping: dispatched in a daemon thread so it runs in
    # parallel with set_leverage + place_entry_with_tpsl. The user explicitly wants the
    # notification to happen "immediately and simultaneously" with the order,
    # not after order placement returns. Failures inside the thread are
    # swallowed (already logged by AlertWriter); they must never affect order
    # placement.
    def _fire_trigger_alert() -> None:
        try:
            alerts.emit(
                severity="info",
                category="trigger_received",
                message=(
                    f"Elfa trigger fired: {strategy.title}\n"
                    f"Placing {strategy.side.upper()} {strategy.amount} "
                    f"{strategy.symbol} ({strategy.order_type}) on GRVT"
                ),
                query_id=query_id, fire_event_id=event_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception("trigger_received alert thread raised")

    threading.Thread(
        target=_fire_trigger_alert, daemon=True, name=f"alert-{event_id}"
    ).start()

    # ---- Order placement runs concurrently with the alert thread above ----

    try:
        current_mid = executor.fetch_mid_price(strategy.symbol)
    except Exception as exc:
        logger.exception("fetch_mid_price failed")
        registry.update_fire_outcome(
            event_id, outcome="grvt_error", error=str(exc)
        )
        alerts.emit(
            severity="error",
            category="grvt_other",
            message=f"could not fetch mid price for {strategy.symbol}: {exc}",
            query_id=query_id, fire_event_id=event_id,
        )
        return

    guard = check_guardrails(
        strategy=strategy,
        current_mid=current_mid,
        receiver_env=config.grvt_env,
    )
    if isinstance(guard, Reject):
        # Status check was already done above; remaining guardrails are
        # env / notional. Symbol existence is GRVT's call (fetch_mid_price
        # above already failed if the symbol is missing). All of these
        # want a Telegram alert.
        registry.update_fire_outcome(
            event_id, outcome="rejected_guardrail", error=guard.reason
        )
        alerts.emit(
            severity="warning",
            category=guard.category,
            message=guard.reason,
            query_id=query_id, fire_event_id=event_id,
        )
        return
    assert isinstance(guard, Allow)

    if strategy.leverage is not None:
        try:
            executor.set_leverage(symbol=strategy.symbol, leverage=strategy.leverage)
        except GrvtError as exc:
            registry.update_fire_outcome(
                event_id, outcome="grvt_error", error=str(exc)
            )
            registry.set_strategy_status(query_id, "fired", fired_at=received_at)
            alerts.emit(
                severity="error",
                category="grvt_set_leverage",
                message=str(exc),
                query_id=query_id, fire_event_id=event_id,
            )
            return

    # ---- Atomic entry + (optional) TP/SL via bulk_orders v2 ----
    # place_entry_with_tpsl never raises; it returns a dict with parent /
    # tp / sl ids and an `errors` list. We branch on whether the parent
    # leg landed:
    #   - parent_order_id set, no errors  -> happy path (info alerts)
    #   - parent_order_id set, has errors -> entry placed, TP/SL needs
    #     manual intervention
    #   - parent_order_id is None         -> entry failed; leave strategy
    #     active for Auto retry unless the error looks terminal
    pair = executor.place_entry_with_tpsl(
        symbol=strategy.symbol,
        entry_side=strategy.side,
        amount=strategy.amount,
        order_type=strategy.order_type,
        limit_price=strategy.price,
        reference_price=current_mid,
        tp_pct=strategy.tp_pct,
        sl_pct=strategy.sl_pct,
    )
    parent_id = pair.get("parent_order_id")
    errors = pair.get("errors") or []

    # ---- Entry failed entirely ----
    if parent_id is None:
        joined = "; ".join(errors) or "unknown bulk_orders failure"
        registry.update_fire_outcome(
            event_id, outcome="grvt_error", error=joined
        )
        # Best-effort terminal classification so a clearly broken strategy
        # (bad symbol, insufficient margin, malformed price) doesn't keep
        # firing on every Auto retry. Anything else stays active.
        joined_lower = joined.lower()
        terminal_markers = (
            "insufficient margin", "insufficient_margin",
            "invalid signature", "401", "403",
            "invalid price", "tick size", "price out of range",
            "symbol not found",
        )
        is_terminal = any(m in joined_lower for m in terminal_markers)
        if is_terminal:
            registry.set_strategy_status(query_id, "fired", fired_at=received_at)
            category = "insufficient_margin" if "margin" in joined_lower else "grvt_other"
        else:
            category = "grvt_transient"
        alerts.emit(
            severity="error",
            category=category,
            message=joined,
            query_id=query_id, fire_event_id=event_id,
        )
        return

    # ---- Entry placed: flip strategy to 'fired' before doing anything else.
    # If the registry write fails we still own the on-exchange position; emit
    # manual_intervention_required so the operator can reconcile before any
    # further Auto retry can place a duplicate.
    try:
        registry.set_strategy_status(query_id, "fired", fired_at=received_at)
        registry.update_fire_outcome(
            event_id, outcome="placed", grvt_order_id=parent_id
        )
    except Exception as exc:  # noqa: BLE001 , real-money safety net
        logger.exception(
            "registry write failed AFTER successful entry placement , manual reconciliation required"
        )
        alerts.emit(
            severity="error",
            category="manual_intervention_required",
            message=(
                f"Order PLACED on GRVT but registry update failed. "
                f"Manually mark strategy={query_id!r} as 'fired' and fire={event_id!r} "
                f"as 'placed' with grvt_order_id={parent_id!r}. "
                f"Underlying error: {type(exc).__name__}: {exc}"
            ),
            query_id=query_id, fire_event_id=event_id,
            details={
                "grvt_order_id": parent_id,
                "exception_type": type(exc).__name__,
            },
        )
        return

    # Order id from bulk_orders is the literal "0x00" placeholder until GRVT
    # settles on chain, which is noise. Drop it from the user-visible alert
    # and keep it only in the receiver's structured log line.
    alerts.emit(
        severity="info",
        category="order_placed",
        message=(
            f"{strategy.side.upper()} {strategy.amount} {strategy.symbol} "
            f"({strategy.order_type})"
        ),
        query_id=query_id, fire_event_id=event_id,
    )

    # ---- TP/SL outcome ----
    has_tpsl = strategy.tp_pct is not None or strategy.sl_pct is not None
    if not has_tpsl:
        return

    if errors:
        # Entry succeeded but at least one TP/SL leg failed. The position
        # is open without a complete protective bracket; flag it loudly.
        alerts.emit(
            severity="error",
            category="manual_intervention_required",
            message=(
                f"entry order_id={parent_id!r} placed but TP/SL setup "
                f"partially/fully failed. Intended TP={pair.get('tp_price')}, "
                f"SL={pair.get('sl_price')}. Failures: {'; '.join(errors)}. "
                f"Manually place any missing leg for {strategy.symbol}."
            ),
            query_id=query_id, fire_event_id=event_id,
            details={
                "grvt_order_id": parent_id,
                "tp_order_id": pair.get("tp_order_id"),
                "sl_order_id": pair.get("sl_order_id"),
                "tp_price": pair.get("tp_price"),
                "sl_price": pair.get("sl_price"),
                "errors": errors,
            },
        )
        return

    armed_parts = [f"{strategy.amount} {strategy.symbol}"]
    if pair.get("tp_price") is not None and strategy.tp_pct is not None:
        armed_parts.append(f"TP ${pair['tp_price']} (+{strategy.tp_pct}%)")
    if pair.get("sl_price") is not None and strategy.sl_pct is not None:
        armed_parts.append(f"SL ${pair['sl_price']} (-{strategy.sl_pct}%)")
    alerts.emit(
        severity="info",
        category="tpsl_armed",
        message="\n".join(armed_parts),
        query_id=query_id, fire_event_id=event_id,
    )
