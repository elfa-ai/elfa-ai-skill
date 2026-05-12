"""Receiver: pulls trigger events from Elfa Auto via per-query SSE streams.

Single asyncio supervisor maintains one SSE consumer per active strategy in
the local registry. On stream close or disconnect, it backfills via REST
(`GET /v2/auto/queries/:id`) to recover any fire that landed in the gap.
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
    async def stream_query(self, query_id: str): ...  # AsyncIterator[dict]


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
    (anything but `active`), backfill any executions we missed and exit.
    """
    loop = asyncio.get_running_loop()
    backoff = backoff_initial
    while True:
        # 1. REST status check / backfill before opening SSE. Cheap and
        #    handles the case where the strategy already triggered while
        #    the supervisor was offline.
        try:
            query_state = await loop.run_in_executor(None, elfa.get_query, query_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("backfill GET failed for %s: %r", query_id, e)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, backoff_max)
            continue

        if query_state.get("status") != "active":
            await _replay_missed_executions(
                query_id, query_state,
                config=config, registry=registry,
                executor=executor, alerts=alerts,
            )
            return  # done with this strategy

        # 2. Open SSE. The iterator naturally exits when the stream closes
        #    (after a fire, or on connection error). On exit we loop back to
        #    step 1 which either backfills (terminal) or reconnects (active).
        try:
            async for ev in elfa.stream_query(query_id):
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


async def _replay_missed_executions(
    query_id: str,
    query_state: dict,
    *,
    config: Config,
    registry: Registry,
    executor: _Executor,
    alerts: AlertWriter,
) -> None:
    """For each execution Elfa reports, replay it through `_process_fire` if
    our local registry hasn't already recorded that fire. Dedupe is by
    execution id (same identifier the SSE notification carries).
    """
    loop = asyncio.get_running_loop()
    for ex in query_state.get("executions") or []:
        ex_id = ex.get("id")
        if not ex_id:
            continue
        existing = await loop.run_in_executor(None, registry.get_fire, ex_id)
        if existing is not None:
            continue
        synth = {
            "status": query_state.get("status"),
            "queryId": query_id,
            "executionId": ex_id,
            "triggerTime": ex.get("createdAt"),
            "source": "rest_backfill",
        }
        logger.info("backfilling missed execution %s for query %s", ex_id, query_id)
        await loop.run_in_executor(
            None,
            _process_fire,
            ex_id, query_id, json.dumps(synth),
            registry, executor, alerts, config,
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
