"""Receiver: pulls trigger events from Elfa Auto via per-query SSE streams.

Single asyncio supervisor maintains one SSE consumer per active strategy in
the local registry. On stream close or disconnect, it polls
`GET /v2/auto/queries/:id` for status reconciliation only - executions on
the poll-query response use a different identifier namespace
(`executions[i].id` = `exec_xxx`) than SSE events (`eventId` = `evt_xxx`),
so we cannot dedupe missed fires across the two channels. Live SSE is the
sole order-placement path; if the receiver was offline when a trigger
fired, the strategy is reported as remotely-terminated via an in-chat
alert so the user can review on the exchange manually.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Optional, Protocol

import httpx

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


class _ElfaClient(Protocol):
    def get_query(self, query_id: str) -> dict: ...
    async def stream_notifications(self, query_id: str): ...  # AsyncIterator[dict]


# ---------------------------------------------------------------------------
# Supervisor: one SSE consumer per active strategy
# ---------------------------------------------------------------------------


async def supervisor(
    *,
    config: Config,
    registry: Registry,
    elfa: _ElfaClient,
    executor: _Executor,
    alerts: AlertWriter,
    poll_interval: float = 5.0,
    stop: Optional[asyncio.Event] = None,
) -> None:
    """Long-running supervisor. Spawns a per-strategy SSE task for each
    `active` row in the local registry. Reconciles every `poll_interval`
    seconds so newly-added strategies get picked up without restart.
    Exits cleanly when `stop` is set.
    """
    tasks: dict[str, asyncio.Task] = {}
    stop = stop or asyncio.Event()
    logger.info("supervisor started (poll_interval=%.1fs)", poll_interval)
    try:
        while not stop.is_set():
            try:
                active = registry.list_strategies(status="active")
            except Exception:  # noqa: BLE001 - registry read should never crash supervisor
                logger.exception("registry list failed; will retry")
                await _wait_or_stop(stop, poll_interval)
                continue

            active_qids = {s.query_id for s in active}

            for qid in active_qids - set(tasks):
                logger.info("spawning SSE task for %s", qid)
                tasks[qid] = asyncio.create_task(
                    _strategy_loop(
                        qid,
                        config=config, registry=registry,
                        elfa=elfa, executor=executor, alerts=alerts,
                    ),
                    name=f"sse-{qid[:8]}",
                )

            for qid in list(tasks):
                if tasks[qid].done():
                    exc = tasks[qid].exception()
                    if exc is not None:
                        logger.error("strategy loop %s exited with %r", qid, exc)
                    else:
                        logger.info("strategy loop %s finished", qid)
                    del tasks[qid]

            await _wait_or_stop(stop, poll_interval)
    finally:
        logger.info("supervisor shutting down; cancelling %d task(s)", len(tasks))
        for t in tasks.values():
            t.cancel()
        for t in tasks.values():
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


async def _wait_or_stop(stop: asyncio.Event, secs: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=secs)
    except asyncio.TimeoutError:
        pass


_LIVE_STATUSES = {"active", "recurring"}
_TERMINAL_STATUSES = {"triggered", "expired", "cancelled", "failed"}


async def _strategy_loop(
    query_id: str,
    *,
    config: Config,
    registry: Registry,
    elfa: _ElfaClient,
    executor: _Executor,
    alerts: AlertWriter,
    backoff_initial: float = 2.0,
    backoff_max: float = 60.0,
) -> None:
    """One iteration per (re)connect attempt. On terminal Elfa-side status
    (per the documented set: `triggered` / `expired` / `cancelled` /
    `failed`), reconcile the local registry status and exit. `recurring`
    is treated as live (re-opens SSE the same as `active`).
    """
    loop = asyncio.get_running_loop()
    backoff = backoff_initial
    while True:
        # 1. Poll query for status reconciliation. We do NOT use the
        #    `executions` array for fire dedupe: executions[i].id is in a
        #    different identifier namespace (`exec_xxx`) than SSE events
        #    (`eventId` = `evt_xxx`), per the documented schemas. Cross-
        #    channel dedupe would silently double-fire or miss entirely.
        try:
            query_state = await loop.run_in_executor(None, elfa.get_query, query_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("poll-query failed for %s: %r", query_id, e)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, backoff_max)
            continue

        remote_status = query_state.get("status") or "unknown"
        if remote_status not in _LIVE_STATUSES:
            # Terminal (or unknown) status. Sync to local registry and
            # surface a visible alert if executions occurred while the
            # receiver was offline. Order placement is owned by the live
            # SSE path only; if a trigger fired while we were down, the
            # user is told to review manually rather than us guessing.
            had_executions = bool(query_state.get("executions"))
            await _sync_terminal_status_locally(
                query_id, remote_status,
                had_executions=had_executions,
                registry=registry, alerts=alerts,
            )
            return  # done with this strategy

        # 2. Open SSE. The iterator naturally exits when the stream closes
        #    (after a fire, or on connection error). On exit we loop back to
        #    step 1 which either backfills (terminal) or reconnects (active).
        try:
            async for ev in elfa.stream_notifications(query_id):
                event_id = ev.get("event_id") or "unknown"
                payload = ev.get("data") or {}
                raw_payload = json.dumps(payload)
                await loop.run_in_executor(
                    None,
                    _process_fire,
                    event_id, query_id, raw_payload,
                    registry, executor, alerts, config,
                )
                backoff = backoff_initial
        except (httpx.HTTPError, ConnectionError) as e:
            logger.warning("SSE transport error for %s: %r; backoff %.1fs",
                           query_id, e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, backoff_max)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("unexpected error in SSE iteration for %s", query_id)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, backoff_max)


async def _sync_terminal_status_locally(
    query_id: str,
    remote_status: str,
    *,
    had_executions: bool,
    registry: Registry,
    alerts: AlertWriter,
) -> None:
    """Reconcile a remote-side terminal status against the local registry.

    The supervisor polls `list_strategies(status='active')` every ~5s, so a
    strategy whose Elfa-side status flipped to a terminal value outside our
    flow (e.g. user clicked Cancel in the Elfa UI, or Auto's expiresIn
    elapsed) would otherwise keep getting an SSE task re-spawned that
    exits immediately. Sync the status locally and surface an alert.

    When `had_executions` is True, the strategy fired at least once while
    we were not connected via SSE. Because executions[i].id and SSE
    eventId are different identifier spaces we cannot dedupe across
    channels (see module docstring), so we emit a manual-review alert
    rather than re-firing the order placement path.
    """
    loop = asyncio.get_running_loop()
    local = await loop.run_in_executor(None, registry.get_strategy, query_id)
    if local is None:
        return  # not registered locally, nothing to sync
    if local.status != "active":
        # Already in a terminal state locally. Live SSE may have already
        # transitioned active -> fired via `_process_fire`; don't overwrite.
        return

    # Map Elfa's terminal vocabulary to our registry vocabulary. The
    # documented Auto status set is {active, recurring, triggered, expired,
    # cancelled, failed}. We only reach here for the non-live members.
    local_status = {
        "triggered": "fired",
        "expired": "expired",
        "cancelled": "cancelled",
        "failed": "failed",
    }.get(remote_status, "cancelled")

    try:
        await loop.run_in_executor(
            None,
            lambda: registry.set_strategy_status(query_id, local_status),
        )
    except Exception:  # noqa: BLE001
        logger.exception("failed to sync terminal status for %s", query_id)
        return

    if had_executions and remote_status == "triggered":
        # The strategy triggered at least once while the receiver wasn't
        # connected via SSE. We can't safely replay it as a fire because
        # the execution id namespace doesn't align with our SSE-keyed
        # `fires` table, and we'd risk double-placing on GRVT. Tell the
        # user to reconcile manually.
        alerts.emit(
            severity="error",
            category="manual_intervention_required",
            message=(
                f"strategy triggered on Elfa while receiver was disconnected. "
                "Order was NOT placed by the bot. Review the position on "
                f"GRVT and decide whether to enter manually. Remote status: "
                f"{remote_status!r}, local status now: {local_status!r}."
            ),
            query_id=query_id,
        )
        return

    # No fires to surface. Severity tracks how surprising the terminal
    # state is: expired = expected lifecycle, cancelled/failed = worth
    # surfacing but not order-placement-critical.
    severity = "info" if remote_status == "expired" else "warning"
    alerts.emit(
        severity=severity,
        category="strategy_terminated_remotely",
        message=(
            f"strategy ended with remote status {remote_status!r} "
            f"(no fires recorded). local status set to {local_status!r}."
        ),
        query_id=query_id,
    )


# ---------------------------------------------------------------------------
# Fire handler
# ---------------------------------------------------------------------------


def _process_fire(
    event_id: str,
    query_id: str,
    raw_payload: str,
    registry: Registry,
    executor: _Executor,
    alerts: AlertWriter,
    config: Config,
) -> None:
    """Top-level safety net: any uncaught exception inside `_process_fire_inner`
    must emit a high-severity alert rather than vanishing into asyncio.
    """
    try:
        _process_fire_inner(
            event_id=event_id, query_id=query_id, raw_payload=raw_payload,
            registry=registry, executor=executor, alerts=alerts, config=config,
        )
    except Exception as exc:  # noqa: BLE001
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
                    "raw_payload": raw_payload[:1000],
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("alert emission failed for event %s", event_id)


def _process_fire_inner(
    *,
    event_id: str,
    query_id: str,
    raw_payload: str,
    registry: Registry,
    executor: _Executor,
    alerts: AlertWriter,
    config: Config,
) -> None:
    received_at = int(time.time())

    inserted = registry.insert_fire_if_new(
        event_id=event_id,
        query_id=query_id,
        received_at=received_at,
        outcome="pending",
        raw_payload=raw_payload,
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

    if strategy.status != "active":
        reason = f"strategy status is {strategy.status!r}, only 'active' fires"
        registry.update_fire_outcome(
            event_id, outcome="rejected_guardrail", error=reason
        )
        logger.info("rejecting fire for non-active strategy: %s", reason)
        return

    # IMMEDIATE Telegram ping: dispatched in a daemon thread so it runs in
    # parallel with set_leverage + place_entry_with_tpsl. Failures inside the
    # thread are swallowed (already logged by AlertWriter); they must never
    # affect order placement.
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
        strategy=strategy, current_mid=current_mid, receiver_env=config.grvt_env,
    )
    if isinstance(guard, Reject):
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

    if parent_id is None:
        joined = "; ".join(errors) or "unknown bulk_orders failure"
        registry.update_fire_outcome(event_id, outcome="grvt_error", error=joined)
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

    try:
        registry.set_strategy_status(query_id, "fired", fired_at=received_at)
        registry.update_fire_outcome(
            event_id, outcome="placed", grvt_order_id=parent_id
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("registry write failed AFTER successful entry placement")
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

    alerts.emit(
        severity="info",
        category="order_placed",
        message=(
            f"{strategy.side.upper()} {strategy.amount} {strategy.symbol} "
            f"({strategy.order_type})"
        ),
        query_id=query_id, fire_event_id=event_id,
    )

    has_tpsl = strategy.tp_pct is not None or strategy.sl_pct is not None
    if not has_tpsl:
        return

    if errors:
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
