"""Receiver: pulls trigger events from Elfa Auto via per-query SSE streams.

Single-fire by design: each strategy authors a query, the live SSE
delivers one fire, the bot places the OTOCO order on GRVT, the local
strategy transitions `active -> fired`, and the SSE task exits. There is
no order-replay path: if the receiver was offline when Elfa fired, the
user gets a `manual_intervention_required` alert via the poll-query
status check on next start. Recurring queries are not supported by this
bot (the authoring flow forbids them).

Cross-channel dedupe key:
  SSE payload `executionId` matches poll-query `executions[i].id`
  (same UUID namespace, verified against api.elfa.ai 2026-05-13).
  The receiver uses `executionId` as the canonical idempotency key
  for both SSE delivery and poll-query reconciliation, so a fire
  delivered live via SSE and then re-observed via poll-query will
  not be processed twice.
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
from .elfa_client import ElfaStreamError
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


# Documented Auto status set (docs.elfa.ai/auto/agent-quickstart,
# v-2-auto.tag.mdx). `recurring` is documented as live but not supported
# by this single-fire bot; the authoring flow rejects it.
_LIVE_STATUSES = {"active"}
_TERMINAL_STATUSES = {"triggered", "expired", "cancelled", "failed"}


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
    """Reconcile local `active` strategies against running SSE tasks.

    On each poll: spawn tasks for newly-active strategies; cancel tasks
    whose strategy is no longer locally active (cancelled via CLI, or
    transitioned to `fired` by a live SSE event handled inside the task);
    reap finished tasks. Exits when `stop` is set.
    """
    tasks: dict[str, asyncio.Task] = {}
    stop = stop or asyncio.Event()
    logger.info("supervisor started (poll_interval=%.1fs)", poll_interval)
    try:
        while not stop.is_set():
            try:
                active = registry.list_strategies(status="active")
            except Exception:
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

            for qid in set(tasks) - active_qids:
                if not tasks[qid].done():
                    logger.info("cancelling SSE task for %s (no longer active)", qid)
                    tasks[qid].cancel()

            for qid in list(tasks):
                t = tasks[qid]
                if t.done():
                    exc = t.exception() if not t.cancelled() else None
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
    idle_close_backoff: float = 5.0,
) -> None:
    """Per-strategy loop. Poll-query for status, then open SSE.

    Exits when:
 - remote status is terminal (sync local + alert)
 - local strategy is no longer `active` (e.g. SSE fire transitioned
        to `fired`, or user cancelled via CLI)
 - poll-query returns 404 (query no longer exists remotely)
 - cancelled by the supervisor
    """
    loop = asyncio.get_running_loop()
    backoff = backoff_initial
    while True:
        local = await loop.run_in_executor(None, registry.get_strategy, query_id)
        if local is None or local.status != "active":
            return

        try:
            query_state = await loop.run_in_executor(None, elfa.get_query, query_id)
        except Exception as e:
            status_code = _status_code_of(e)
            if status_code == 404:
                await _sync_terminal_status_locally(
                    query_id, remote_status="cancelled",
                    executions=[], registry=registry, alerts=alerts,
                    reason="query no longer exists on Elfa (404)",
                )
                return
            if status_code in (401, 403):
                await _sync_terminal_status_locally(
                    query_id, remote_status="failed",
                    executions=[], registry=registry, alerts=alerts,
                    reason=f"Elfa auth failed during poll-query ({status_code})",
                )
                return
            logger.warning("poll-query failed for %s: %r", query_id, e)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, backoff_max)
            continue

        backoff = backoff_initial
        remote_status = query_state.get("status") or "unknown"
        executions = query_state.get("executions") or []

        if remote_status not in _LIVE_STATUSES:
            await _sync_terminal_status_locally(
                query_id, remote_status,
                executions=executions, registry=registry, alerts=alerts,
            )
            return

        yielded_event = False
        try:
            async for ev in elfa.stream_notifications(query_id):
                yielded_event = True
                event_id = ev.get("event_id")
                if not event_id:
                    logger.warning(
                        "skipping SSE event with no event_id for %s", query_id
                    )
                    continue
                payload = ev.get("data") or {}
                raw_payload = json.dumps(payload)
                await loop.run_in_executor(
                    None,
                    _process_fire,
                    event_id, query_id, raw_payload,
                    registry, executor, alerts, config,
                )
        except ElfaStreamError as e:
            if e.status_code == 404:
                await _sync_terminal_status_locally(
                    query_id, remote_status="cancelled",
                    executions=[], registry=registry, alerts=alerts,
                    reason="query no longer exists on Elfa (404)",
                )
                return
            if e.status_code in (401, 403):
                await _sync_terminal_status_locally(
                    query_id, remote_status="failed",
                    executions=[], registry=registry, alerts=alerts,
                    reason=f"Elfa auth failed during SSE stream ({e.status_code})",
                )
                return
            logger.warning("SSE stream error for %s: %r; backoff %.1fs",
                           query_id, e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, backoff_max)
            continue
        except (httpx.HTTPError, ConnectionError) as e:
            logger.warning("SSE transport error for %s: %r; backoff %.1fs",
                           query_id, e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, backoff_max)
            continue
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("unexpected error in SSE iteration for %s", query_id)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, backoff_max)
            continue

        if not yielded_event:
            await asyncio.sleep(idle_close_backoff)


def _status_code_of(exc: BaseException) -> Optional[int]:
    """Best-effort: extract an HTTP status code from an exception so the
    loop can branch on 404 (gone) vs 401 (auth) vs 5xx (transient). The
    ElfaClient REST helpers raise RuntimeError(\"... <code> ...\"), so we
    parse the prefix."""
    msg = str(exc)
    for code in (404, 410, 401, 403):
        if f" {code} " in msg or msg.endswith(f" {code}"):
            return code
    return None


async def _sync_terminal_status_locally(
    query_id: str,
    remote_status: str,
    *,
    executions: list,
    registry: Registry,
    alerts: AlertWriter,
    reason: Optional[str] = None,
) -> None:
    """Sync a terminal remote status to the local registry and emit one alert.

    Suppresses the alert if the local strategy already transitioned to a
    terminal state (which means a live SSE fire was handled by
    `_process_fire` and the alerts pipeline already surfaced it).
    """
    loop = asyncio.get_running_loop()
    local = await loop.run_in_executor(None, registry.get_strategy, query_id)
    if local is None or local.status != "active":
        return

    local_status_map = {
        "triggered": "fired",
        "expired": "expired",
        "cancelled": "cancelled",
        "failed": "failed",
    }
    if remote_status in local_status_map:
        local_status = local_status_map[remote_status]
    else:
        local_status = "failed"
        logger.warning(
            "unrecognized remote status %r for %s; treating as failed",
            remote_status, query_id,
        )

    try:
        await loop.run_in_executor(
            None,
            lambda: registry.set_strategy_status(query_id, local_status),
        )
    except Exception:
        logger.exception("failed to sync terminal status for %s", query_id)
        return

    had_executions = bool(executions)
    if had_executions and remote_status == "triggered":
        local_fires = await loop.run_in_executor(
            None, registry.count_fires_for_query, query_id
        )
        if local_fires == 0:
            alerts.emit(
                severity="error",
                category="manual_intervention_required",
                message=(
                    f"strategy triggered on Elfa while receiver was disconnected. "
                    "Order was NOT placed by the bot. Review the position on "
                    f"GRVT and decide whether to enter manually. "
                    f"Remote status: {remote_status!r}, local status now: {local_status!r}."
                ),
                query_id=query_id,
                details={"executions": executions[:10]},
            )
            return
        # Live SSE already delivered the fire; _process_fire produced the
        # canonical alerts (order_placed / guardrail_rejected / etc). Don't
        # double-alert.
        return

    if remote_status == "failed":
        alerts.emit(
            severity="error",
            category="strategy_terminated_remotely",
            message=(
                f"strategy failed on Elfa. local status set to {local_status!r}. "
                f"Reason: {reason or 'see executions'}."
            ),
            query_id=query_id,
            details={"executions": executions[:10]} if had_executions else None,
        )
        return

    severity = "info" if remote_status == "expired" else "warning"
    msg = (
        f"strategy ended with remote status {remote_status!r}. "
        f"local status set to {local_status!r}."
    )
    if reason:
        msg = f"{msg} {reason}"
    alerts.emit(
        severity=severity,
        category="strategy_terminated_remotely",
        message=msg,
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
        logger.info("duplicate event %s, skipped", event_id)
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

    # Telegram ping in a daemon thread so it runs in parallel with order
    # placement. Alert failures must never block trade execution.
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
