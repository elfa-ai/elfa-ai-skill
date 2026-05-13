"""Tests for receiver.py.

Two layers:
  * `_process_fire` business logic - synchronous tests that drive the fire
    handler directly.
  * `_strategy_loop` SSE integration - asyncio tests with a fake ElfaClient
    that yields fire events and reports query state.
"""

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from elfa_grvt_bot.alerts import AlertWriter
from elfa_grvt_bot.config import Config
from elfa_grvt_bot.grvt_executor import GrvtError, ErrorClass
from elfa_grvt_bot.receiver import _process_fire, _strategy_loop
from elfa_grvt_bot.registry import Registry, Strategy


def _config(tmp_path) -> Config:
    return Config(
        elfa_api_key="ek",
        grvt_api_key="g",
        grvt_private_key="0xg",
        grvt_trading_account_id="ta",
        grvt_env="prod",
        telegram_bot_token="bt",
        telegram_chat_id="123",
        registry_db_path=str(tmp_path / "r.db"),
    )


def _strategy(query_id: str = "q_abc", **overrides) -> Strategy:
    base = dict(
        query_id=query_id, title="t", description=None,
        eql_json="{}", symbol="BTC_USDT_Perp", side="buy",
        amount=0.05, order_type="market", price=None, leverage=None,
        tp_pct=None, sl_pct=None,
        time_in_force=None, reduce_only=False, max_notional_usd=4000.0,
        env="prod", status="active", created_at=1, fired_at=None,
    )
    base.update(overrides)
    return Strategy(**base)


def _ok_pair(parent="ord_xyz", *, tp_id=None, sl_id=None, tp_price=None, sl_price=None) -> dict:
    return {
        "parent_order_id": parent,
        "tp_order_id": tp_id, "sl_order_id": sl_id,
        "tp_price": tp_price, "sl_price": sl_price,
        "errors": [],
    }


def _fail_pair(error: str) -> dict:
    return {
        "parent_order_id": None,
        "tp_order_id": None, "sl_order_id": None,
        "tp_price": None, "sl_price": None,
        "errors": [error],
    }


def _fire(*, event_id, query_id, registry, executor, alerts, config,
          raw=None):
    """Convenience: call _process_fire with a synthesized SSE payload."""
    raw = raw or json.dumps({"queryId": query_id, "eventId": event_id})
    _process_fire(event_id, query_id, raw, registry, executor, alerts, config)


def _setup(tmp_path, *, strategy=None, mid=60000.0):
    cfg = _config(tmp_path)
    registry = Registry(cfg.registry_db_path)
    if strategy is not None:
        registry.insert_strategy(strategy)
    executor = MagicMock()
    executor.fetch_mid_price.return_value = mid
    executor.place_entry_with_tpsl.return_value = _ok_pair("ord_xyz")
    sender = MagicMock(send=MagicMock(return_value=True))
    alerts = AlertWriter(registry=registry, telegram=sender, clock=lambda: 1)
    return cfg, registry, executor, sender, alerts


# ---------------------------------------------------------------------------
# Fire handler business logic
# ---------------------------------------------------------------------------


def test_happy_path_places_order(tmp_path):
    cfg, registry, executor, sender, alerts = _setup(tmp_path, strategy=_strategy())
    _fire(event_id="evt_1", query_id="q_abc",
          registry=registry, executor=executor, alerts=alerts, config=cfg)

    fire = registry.get_fire("evt_1")
    assert fire["outcome"] == "placed"
    assert fire["grvt_order_id"] == "ord_xyz"
    assert registry.get_strategy("q_abc").status == "fired"

    executor.place_entry_with_tpsl.assert_called_once()
    kw = executor.place_entry_with_tpsl.call_args.kwargs
    assert kw["symbol"] == "BTC_USDT_Perp"
    assert kw["entry_side"] == "buy"
    assert kw["amount"] == 0.05
    assert kw["order_type"] == "market"
    assert kw["limit_price"] is None
    assert kw["reference_price"] == 60000.0
    sender.send.assert_called()


def test_duplicate_event_id_does_not_place_again(tmp_path):
    cfg, registry, executor, _, alerts = _setup(tmp_path, strategy=_strategy())
    _fire(event_id="evt_1", query_id="q_abc",
          registry=registry, executor=executor, alerts=alerts, config=cfg)
    _fire(event_id="evt_1", query_id="q_abc",
          registry=registry, executor=executor, alerts=alerts, config=cfg)
    assert executor.place_entry_with_tpsl.call_count == 1


def test_unknown_strategy_alerts_no_order(tmp_path):
    cfg, registry, executor, _, alerts = _setup(tmp_path)
    _fire(event_id="evt_1", query_id="q_unknown",
          registry=registry, executor=executor, alerts=alerts, config=cfg)
    fire = registry.get_fire("evt_1")
    assert fire["outcome"] == "unknown_strategy"
    pending = registry.list_alerts(only_unacked=True)
    assert any(a["category"] == "unknown_strategy" for a in pending)
    executor.place_entry_with_tpsl.assert_not_called()


def test_guardrail_rejection_alerts_no_order(tmp_path):
    # max_notional_usd=4000, amount=0.05, mid=100_000 -> notional=5000 > 4000.
    cfg, registry, executor, _, alerts = _setup(
        tmp_path, strategy=_strategy(), mid=100_000.0,
    )
    _fire(event_id="evt_1", query_id="q_abc",
          registry=registry, executor=executor, alerts=alerts, config=cfg)
    fire = registry.get_fire("evt_1")
    assert fire["outcome"] == "rejected_guardrail"
    pending = registry.list_alerts(only_unacked=True)
    assert any(a["category"] == "guardrail_rejected" for a in pending)
    executor.place_entry_with_tpsl.assert_not_called()


def test_terminal_bulk_error_marks_fired_and_alerts(tmp_path):
    cfg, registry, executor, _, alerts = _setup(tmp_path, strategy=_strategy())
    executor.place_entry_with_tpsl.return_value = _fail_pair(
        "parent: code=2010: insufficient margin"
    )
    _fire(event_id="evt_1", query_id="q_abc",
          registry=registry, executor=executor, alerts=alerts, config=cfg)
    fire = registry.get_fire("evt_1")
    assert fire["outcome"] == "grvt_error"
    assert registry.get_strategy("q_abc").status == "fired"
    pending = registry.list_alerts(only_unacked=True)
    assert any(a["category"] == "insufficient_margin" for a in pending)


def test_transient_bulk_error_keeps_strategy_active(tmp_path):
    cfg, registry, executor, _, alerts = _setup(tmp_path, strategy=_strategy())
    executor.place_entry_with_tpsl.return_value = _fail_pair(
        "bulk_orders submission failed: connection reset"
    )
    _fire(event_id="evt_1", query_id="q_abc",
          registry=registry, executor=executor, alerts=alerts, config=cfg)
    fire = registry.get_fire("evt_1")
    assert fire["outcome"] == "grvt_error"
    assert registry.get_strategy("q_abc").status == "active"


def test_set_leverage_failure_skips_order(tmp_path):
    cfg, registry, executor, _, alerts = _setup(
        tmp_path, strategy=_strategy(leverage=5),
    )
    executor.set_leverage.side_effect = GrvtError(
        "invalid leverage", error_class=ErrorClass.LEVERAGE
    )
    _fire(event_id="evt_1", query_id="q_abc",
          registry=registry, executor=executor, alerts=alerts, config=cfg)
    fire = registry.get_fire("evt_1")
    assert fire["outcome"] == "grvt_error"
    assert registry.get_strategy("q_abc").status == "fired"
    executor.place_entry_with_tpsl.assert_not_called()


def test_malformed_payload_emits_internal_error_alert(tmp_path):
    """If the synthesized fire payload is junk, the safety net still records
    the fire row and emits an alert rather than crashing the supervisor."""
    cfg, registry, executor, _, alerts = _setup(tmp_path)
    # Crash inside the handler by giving the registry a None executor call.
    executor.fetch_mid_price.side_effect = RuntimeError("boom from mid")
    registry.insert_strategy(_strategy())
    _fire(event_id="evt_bad", query_id="q_abc",
          registry=registry, executor=executor, alerts=alerts, config=cfg)
    pending = registry.list_alerts(only_unacked=True)
    # fetch_mid_price RuntimeError is caught and surfaced as grvt_other, not
    # as receiver_internal_error.
    assert any(a["category"] == "grvt_other" for a in pending)
    executor.place_entry_with_tpsl.assert_not_called()


def test_post_order_registry_failure_triggers_manual_intervention_alert(tmp_path):
    cfg, registry, executor, _, alerts = _setup(tmp_path, strategy=_strategy())
    executor.place_entry_with_tpsl.return_value = _ok_pair("ord_real_money")

    def raising_set(*args, **kwargs):
        raise RuntimeError("simulated DB lock")
    registry.set_strategy_status = raising_set

    _fire(event_id="evt_1", query_id="q_abc",
          registry=registry, executor=executor, alerts=alerts, config=cfg)

    # Restore so we can read alerts.
    registry.__class__.set_strategy_status = Registry.set_strategy_status

    pending = registry.list_alerts(only_unacked=True)
    manual = [a for a in pending if a["category"] == "manual_intervention_required"]
    assert len(manual) == 1
    assert "ord_real_money" in manual[0]["message"]
    success = [a for a in pending if a["category"] == "order_placed"]
    assert len(success) == 0


def test_strategy_with_tpsl_arms_close_orders_atomically(tmp_path):
    cfg, registry, executor, _, alerts = _setup(
        tmp_path, strategy=_strategy(side="sell", tp_pct=1.5, sl_pct=1.0),
        mid=100.0,
    )
    executor.place_entry_with_tpsl.return_value = {
        "parent_order_id": "ord_entry",
        "tp_order_id": "ord_tp",
        "sl_order_id": "ord_sl",
        "tp_price": 98.5,
        "sl_price": 101.0,
        "errors": [],
    }
    _fire(event_id="evt_1", query_id="q_abc",
          registry=registry, executor=executor, alerts=alerts, config=cfg)

    kw = executor.place_entry_with_tpsl.call_args.kwargs
    assert kw["entry_side"] == "sell"
    assert kw["reference_price"] == 100.0
    assert kw["tp_pct"] == 1.5
    assert kw["sl_pct"] == 1.0

    pending = registry.list_alerts(only_unacked=True)
    armed = [a for a in pending if a["category"] == "tpsl_armed"]
    assert len(armed) == 1
    assert "98.5" in armed[0]["message"]
    assert "101.0" in armed[0]["message"]
    assert registry.get_strategy("q_abc").status == "fired"


def test_strategy_with_tpsl_partial_failure_emits_manual_intervention(tmp_path):
    cfg, registry, executor, _, alerts = _setup(
        tmp_path, strategy=_strategy(side="sell", tp_pct=1.5, sl_pct=1.0),
        mid=100.0,
    )
    executor.place_entry_with_tpsl.return_value = {
        "parent_order_id": "ord_entry",
        "tp_order_id": "ord_tp",
        "sl_order_id": None,
        "tp_price": 98.5,
        "sl_price": 101.0,
        "errors": ["sl: code=2020: trigger rejected"],
    }
    _fire(event_id="evt_1", query_id="q_abc",
          registry=registry, executor=executor, alerts=alerts, config=cfg)

    pending = registry.list_alerts(only_unacked=True)
    manual = [a for a in pending if a["category"] == "manual_intervention_required"]
    assert len(manual) == 1
    msg = manual[0]["message"]
    assert "ord_entry" in msg
    assert "trigger rejected" in msg
    assert registry.get_strategy("q_abc").status == "fired"


def test_non_active_strategy_rejection_does_not_telegram(tmp_path):
    cfg, registry, executor, sender, alerts = _setup(
        tmp_path, strategy=_strategy(status="fired"),
    )
    _fire(event_id="evt_replay", query_id="q_abc",
          registry=registry, executor=executor, alerts=alerts, config=cfg)
    fire = registry.get_fire("evt_replay")
    assert fire["outcome"] == "rejected_guardrail"
    pending = registry.list_alerts(only_unacked=True)
    assert pending == []
    sender.send.assert_not_called()
    executor.place_entry_with_tpsl.assert_not_called()


# ---------------------------------------------------------------------------
# SSE consumer / supervisor integration
# ---------------------------------------------------------------------------


class _FakeElfa:
    """Test double for ElfaClient with controllable SSE + REST behavior."""

    def __init__(self, *, query_states, sse_events_by_query):
        # query_states: dict query_id -> list[dict] (one per get_query call)
        self.query_states = {k: list(v) for k, v in query_states.items()}
        # sse_events_by_query: dict query_id -> list[event_dict]
        self.sse_events = sse_events_by_query
        self.get_query_calls = []

    def get_query(self, query_id):
        self.get_query_calls.append(query_id)
        states = self.query_states.get(query_id, [])
        if not states:
            return {"queryId": query_id, "status": "active", "executions": []}
        if len(states) == 1:
            return states[0]
        return states.pop(0)

    async def stream_notifications(self, query_id):
        for ev in self.sse_events.get(query_id, []):
            yield ev


async def test_strategy_loop_processes_canonical_sse_event(tmp_path):
    """End-to-end: poll-query reports active, SSE yields a canonical
    query.triggered event keyed on eventId, fire handler places the GRVT
    order, then poll-query reports terminal so the loop exits cleanly."""
    cfg, registry, executor, _, alerts = _setup(tmp_path, strategy=_strategy())
    fake = _FakeElfa(
        query_states={"q_abc": [
            {"queryId": "q_abc", "status": "active",
             "latestEvaluation": None, "executions": []},
            # After SSE closes, poll reports terminal -> loop exits.
            {"queryId": "q_abc", "status": "triggered",
             "latestEvaluation": None,
             "executions": [{"id": "exec_internal_1",
                             "queryId": "q_abc",
                             "type": "notify",
                             "status": "success",
                             "createdAt": "2026-05-12T10:41:03Z"}]},
        ]},
        sse_events_by_query={"q_abc": [
            {"event_id": "evt_01J_live",
             "data": {"version": "1.0",
                      "eventType": "query.triggered",
                      "eventId": "evt_01J_live",
                      "queryId": "q_abc",
                      "channel": "sse",
                      "trigger": {"symbol": "BTC"}}},
        ]},
    )
    await _strategy_loop(
        "q_abc", config=cfg, registry=registry, elfa=fake,
        executor=executor, alerts=alerts,
    )
    fire = registry.get_fire("evt_01J_live")
    assert fire is not None
    assert fire["outcome"] == "placed"
    executor.place_entry_with_tpsl.assert_called_once()


async def test_strategy_loop_recurring_remote_status_is_treated_as_terminal(tmp_path):
    """This bot is single-fire by design and does NOT support recurring
    queries. If poll-query reports `recurring`, treat as terminal-unknown:
    sync local status to `failed` and warn the user; do not try to keep
    a stream open across multiple fires."""
    cfg, registry, executor, _, alerts = _setup(tmp_path, strategy=_strategy())
    fake = _FakeElfa(
        query_states={"q_abc": [
            {"queryId": "q_abc", "status": "recurring",
             "latestEvaluation": None, "executions": []},
        ]},
        sse_events_by_query={},
    )
    await _strategy_loop(
        "q_abc", config=cfg, registry=registry, elfa=fake,
        executor=executor, alerts=alerts,
    )
    assert registry.get_strategy("q_abc").status == "failed"
    executor.place_entry_with_tpsl.assert_not_called()


async def test_strategy_loop_offline_trigger_emits_manual_intervention(tmp_path):
    """If the strategy fires on Elfa while the receiver was offline,
    poll-query returns executions[] but their ids are in a different
    namespace than SSE eventIds. We must NOT replay them through the
    order-placement path (would double-fire on GRVT or fire stale signals).
    Instead emit a manual_intervention_required alert so the user reviews
    the GRVT side themselves."""
    cfg, registry, executor, _, alerts = _setup(tmp_path, strategy=_strategy())
    fake = _FakeElfa(
        query_states={"q_abc": [
            {"queryId": "q_abc", "status": "triggered",
             "latestEvaluation": None,
             "executions": [{"id": "exec_xxx",
                             "queryId": "q_abc",
                             "type": "notify",
                             "status": "success",
                             "createdAt": "2026-05-12T10:00:00Z"}]},
        ]},
        sse_events_by_query={},
    )
    await _strategy_loop(
        "q_abc", config=cfg, registry=registry, elfa=fake,
        executor=executor, alerts=alerts,
    )
    # Local status synced.
    assert registry.get_strategy("q_abc").status == "fired"
    # NO order was placed.
    executor.place_entry_with_tpsl.assert_not_called()
    # Alert was emitted so user knows to reconcile.
    pending = registry.list_alerts(only_unacked=True)
    manual = [a for a in pending
              if a["category"] == "manual_intervention_required"]
    assert len(manual) == 1
    assert "receiver was disconnected" in manual[0]["message"].lower() \
        or "receiver was disconnected" in manual[0]["message"]


async def test_strategy_loop_syncs_remote_cancel_to_local_registry(tmp_path):
    """User cancels on Elfa UI directly. Poll detects `cancelled`, local
    registry is synced, warning alert is emitted, supervisor stops
    re-spawning the task."""
    cfg, registry, executor, sender, alerts = _setup(
        tmp_path, strategy=_strategy(),
    )
    fake = _FakeElfa(
        query_states={"q_abc": [
            {"queryId": "q_abc", "status": "cancelled",
             "latestEvaluation": None, "executions": []},
        ]},
        sse_events_by_query={},
    )
    await _strategy_loop(
        "q_abc", config=cfg, registry=registry, elfa=fake,
        executor=executor, alerts=alerts,
    )
    assert registry.get_strategy("q_abc").status == "cancelled"
    pending = registry.list_alerts(only_unacked=True)
    terminated = [a for a in pending
                  if a["category"] == "strategy_terminated_remotely"]
    assert len(terminated) == 1
    assert terminated[0]["severity"] == "warning"


async def test_strategy_loop_expiry_emits_info_not_warning(tmp_path):
    """expired = expected lifecycle (24h elapsed); info severity only so
    it doesn't Telegram-ping the user."""
    cfg, registry, executor, sender, alerts = _setup(
        tmp_path, strategy=_strategy(),
    )
    fake = _FakeElfa(
        query_states={"q_abc": [
            {"queryId": "q_abc", "status": "expired",
             "latestEvaluation": None, "executions": []},
        ]},
        sse_events_by_query={},
    )
    await _strategy_loop(
        "q_abc", config=cfg, registry=registry, elfa=fake,
        executor=executor, alerts=alerts,
    )
    assert registry.get_strategy("q_abc").status == "expired"
    pending = registry.list_alerts(only_unacked=True)
    terminated = [a for a in pending
                  if a["category"] == "strategy_terminated_remotely"]
    assert len(terminated) == 1
    assert terminated[0]["severity"] == "info"


async def test_strategy_loop_failed_status_emits_error_alert(tmp_path):
    """`failed` is terminal-with-error per the docs. Alert severity is
    `error` (not just `warning`) because the user typically needs to
    investigate why Auto rejected the query."""
    cfg, registry, executor, sender, alerts = _setup(
        tmp_path, strategy=_strategy(),
    )
    fake = _FakeElfa(
        query_states={"q_abc": [
            {"queryId": "q_abc", "status": "failed",
             "latestEvaluation": None, "executions": []},
        ]},
        sse_events_by_query={},
    )
    await _strategy_loop(
        "q_abc", config=cfg, registry=registry, elfa=fake,
        executor=executor, alerts=alerts,
    )
    assert registry.get_strategy("q_abc").status == "failed"
    pending = registry.list_alerts(only_unacked=True)
    terminated = [a for a in pending
                  if a["category"] == "strategy_terminated_remotely"]
    assert len(terminated) == 1
    assert terminated[0]["severity"] == "error"


async def test_strategy_loop_unknown_status_marks_failed(tmp_path):
    """Anything outside the documented status set is treated as failed
    (not silently mapped to cancelled). Surfaces a warning log + alert."""
    cfg, registry, executor, _, alerts = _setup(tmp_path, strategy=_strategy())
    fake = _FakeElfa(
        query_states={"q_abc": [
            {"queryId": "q_abc", "status": "this_status_does_not_exist",
             "latestEvaluation": None, "executions": []},
        ]},
        sse_events_by_query={},
    )
    await _strategy_loop(
        "q_abc", config=cfg, registry=registry, elfa=fake,
        executor=executor, alerts=alerts,
    )
    assert registry.get_strategy("q_abc").status == "failed"


async def test_strategy_loop_offline_trigger_alerts_only_if_no_local_fire(tmp_path):
    """If the bot already saw the SSE fire live (so `fires` has a row), the
    next poll sees remote=triggered + executions but must NOT emit a
    'receiver disconnected' alert. The fire was handled live; any
    follow-up alert was already emitted by _process_fire."""
    cfg, registry, executor, _, alerts = _setup(tmp_path, strategy=_strategy())
    # Pre-seed a live fire that we already processed.
    registry.insert_fire_if_new(
        event_id="evt_live", query_id="q_abc", received_at=1,
        outcome="placed", raw_payload="{}",
    )
    fake = _FakeElfa(
        query_states={"q_abc": [
            {"queryId": "q_abc", "status": "triggered",
             "latestEvaluation": None,
             "executions": [{"id": "exec_xxx", "queryId": "q_abc",
                             "type": "notify", "status": "success",
                             "createdAt": "2026-05-12T10:00:00Z"}]},
        ]},
        sse_events_by_query={},
    )
    await _strategy_loop(
        "q_abc", config=cfg, registry=registry, elfa=fake,
        executor=executor, alerts=alerts,
    )
    pending = registry.list_alerts(only_unacked=True)
    assert not any(
        a["category"] == "manual_intervention_required" for a in pending
    )


async def test_strategy_loop_live_sse_fire_no_extra_terminated_alert(tmp_path):
    """When a fire arrives live via SSE, `_process_fire` produces
    order_placed / tpsl_armed alerts and transitions the local strategy
    `active -> fired`. The poll-query that follows should see status as
    terminal but find local status NOT active anymore, so it must NOT add
    a second `strategy_terminated_remotely` alert on top."""
    cfg, registry, executor, _, alerts = _setup(
        tmp_path, strategy=_strategy(),
    )
    fake = _FakeElfa(
        query_states={"q_abc": [
            {"queryId": "q_abc", "status": "active",
             "latestEvaluation": None, "executions": []},
            # After SSE close, poll sees terminal
            {"queryId": "q_abc", "status": "triggered",
             "latestEvaluation": None,
             "executions": [{"id": "exec_irrelevant",
                             "queryId": "q_abc",
                             "type": "notify", "status": "success",
                             "createdAt": "2026-05-12T10:00:00Z"}]},
        ]},
        sse_events_by_query={"q_abc": [
            {"event_id": "evt_live",
             "data": {"eventType": "query.triggered",
                      "eventId": "evt_live",
                      "queryId": "q_abc"}},
        ]},
    )
    await _strategy_loop(
        "q_abc", config=cfg, registry=registry, elfa=fake,
        executor=executor, alerts=alerts,
    )
    pending = registry.list_alerts(only_unacked=True)
    # No double-alert: the strategy_terminated_remotely is suppressed
    # because local status is already `fired` after the live SSE fire.
    assert not any(
        a["category"] == "strategy_terminated_remotely" for a in pending
    )
    assert any(a["category"] == "order_placed" for a in pending)
    # And we did NOT emit manual_intervention_required either, since the
    # fire was handled live.
    assert not any(
        a["category"] == "manual_intervention_required" for a in pending
    )


# ---------------------------------------------------------------------------
# Supervisor: task lifecycle (spawn / cancel-on-leave / reap)
# ---------------------------------------------------------------------------


from elfa_grvt_bot.receiver import supervisor


async def test_supervisor_cancels_task_when_strategy_leaves_active(tmp_path):
    """If a strategy transitions from active to a terminal local status
    (CLI cancel, or live SSE fire that marked it fired), the supervisor
    must cancel its SSE task on the next reconcile cycle. Without this
    the task lingers, holds the SSE connection, and may keep retrying
    a stream that no longer corresponds to anything we care about."""
    cfg, registry, executor, _, alerts = _setup(tmp_path, strategy=_strategy())

    stream_cancelled = asyncio.Event()

    class _LiveElfa:
        def get_query(self, query_id):
            return {"queryId": query_id, "status": "active", "executions": []}

        async def stream_notifications(self, query_id):
            try:
                # Stay open forever to model a live SSE connection.
                while True:
                    await asyncio.sleep(3600)
                    yield {}
            except asyncio.CancelledError:
                stream_cancelled.set()
                raise

    fake = _LiveElfa()
    stop = asyncio.Event()
    sup_task = asyncio.create_task(supervisor(
        config=cfg, registry=registry, elfa=fake,
        executor=executor, alerts=alerts,
        poll_interval=0.01, stop=stop,
    ))
    # Let the supervisor spawn the SSE task.
    await asyncio.sleep(0.05)
    # Now transition the strategy out of active. Supervisor should cancel.
    registry.set_strategy_status("q_abc", "cancelled")
    await asyncio.sleep(0.1)
    stop.set()
    await sup_task
    assert stream_cancelled.is_set()


async def test_supervisor_picks_up_newly_registered_strategy(tmp_path):
    """A strategy authored while the supervisor is running must get an
    SSE task spawned on the next poll cycle, not require a restart."""
    cfg, registry, executor, _, alerts = _setup(tmp_path)  # no strategy yet

    spawned_for: list[str] = []

    class _LiveElfa:
        def get_query(self, query_id):
            return {"queryId": query_id, "status": "active", "executions": []}

        async def stream_notifications(self, query_id):
            spawned_for.append(query_id)
            await asyncio.sleep(3600)
            if False:
                yield {}

    fake = _LiveElfa()
    stop = asyncio.Event()
    sup_task = asyncio.create_task(supervisor(
        config=cfg, registry=registry, elfa=fake,
        executor=executor, alerts=alerts,
        poll_interval=0.01, stop=stop,
    ))
    # Author a strategy mid-flight.
    await asyncio.sleep(0.03)
    registry.insert_strategy(_strategy(query_id="q_new"))
    await asyncio.sleep(0.05)
    stop.set()
    await sup_task
    assert "q_new" in spawned_for
