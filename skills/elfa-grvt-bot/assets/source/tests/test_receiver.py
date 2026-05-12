import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from elfa_grvt_bot.alerts import AlertWriter
from elfa_grvt_bot.config import Config
from elfa_grvt_bot.grvt_executor import GrvtError, ErrorClass
from elfa_grvt_bot.receiver import create_app
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
        receiver_public_url="https://example.test",
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


def _post_event(client, *, event_id, query_id, body_dict):
    body = json.dumps(body_dict).encode()
    return client.post(
        "/auto/events",
        content=body,
        headers={
            "X-Auto-Event-Id": event_id,
            "Content-Type": "application/json",
        },
    )


def test_healthz(tmp_path):
    cfg = _config(tmp_path)
    registry = Registry(cfg.registry_db_path)
    executor = MagicMock()
    alerts = AlertWriter(registry=registry, telegram=MagicMock(send=MagicMock(return_value=True)),
                         clock=lambda: 1)
    app = create_app(config=cfg, registry=registry, executor=executor, alerts=alerts)
    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_missing_event_id_returns_400(tmp_path):
    cfg = _config(tmp_path)
    registry = Registry(cfg.registry_db_path)
    executor = MagicMock()
    alerts = AlertWriter(registry=registry, telegram=MagicMock(send=MagicMock(return_value=True)),
                         clock=lambda: 1)
    app = create_app(config=cfg, registry=registry, executor=executor, alerts=alerts)
    client = TestClient(app)

    r = client.post(
        "/auto/events",
        content=b'{"queryId":"q_abc"}',
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400
    executor.place_entry_with_tpsl.assert_not_called()


def test_happy_path_places_order(tmp_path):
    cfg = _config(tmp_path)
    registry = Registry(cfg.registry_db_path)
    registry.insert_strategy(_strategy())
    executor = MagicMock()
    executor.fetch_mid_price.return_value = 60000.0
    executor.place_entry_with_tpsl.return_value = _ok_pair("ord_xyz")
    sender = MagicMock(send=MagicMock(return_value=True))
    alerts = AlertWriter(registry=registry, telegram=sender, clock=lambda: 1)

    app = create_app(config=cfg, registry=registry, executor=executor, alerts=alerts)
    client = TestClient(app)
    r = _post_event(client, event_id="evt_1",
                    query_id="q_abc", body_dict={"queryId": "q_abc"})
    assert r.status_code == 200

    fire = registry.get_fire("evt_1")
    assert fire is not None
    assert fire["outcome"] == "placed"
    assert fire["grvt_order_id"] == "ord_xyz"

    strat = registry.get_strategy("q_abc")
    assert strat.status == "fired"

    # Single atomic call replaces the old place_order + wait + place_tpsl_pair.
    executor.place_entry_with_tpsl.assert_called_once()
    kw = executor.place_entry_with_tpsl.call_args.kwargs
    assert kw["symbol"] == "BTC_USDT_Perp"
    assert kw["entry_side"] == "buy"
    assert kw["amount"] == 0.05
    assert kw["order_type"] == "market"
    assert kw["limit_price"] is None
    assert kw["reference_price"] == 60000.0
    assert kw["tp_pct"] is None
    assert kw["sl_pct"] is None

    sender.send.assert_called()  # Telegram receipt fired


def test_duplicate_event_id_does_not_place_again(tmp_path):
    cfg = _config(tmp_path)
    registry = Registry(cfg.registry_db_path)
    registry.insert_strategy(_strategy())
    executor = MagicMock()
    executor.fetch_mid_price.return_value = 60000.0
    executor.place_entry_with_tpsl.return_value = _ok_pair("ord_xyz")
    sender = MagicMock(send=MagicMock(return_value=True))
    alerts = AlertWriter(registry=registry, telegram=sender, clock=lambda: 1)
    app = create_app(config=cfg, registry=registry, executor=executor, alerts=alerts)
    client = TestClient(app)

    _post_event(client, event_id="evt_1", query_id="q_abc",
                body_dict={"queryId": "q_abc"})
    _post_event(client, event_id="evt_1", query_id="q_abc",
                body_dict={"queryId": "q_abc"})

    assert executor.place_entry_with_tpsl.call_count == 1


def test_unknown_strategy_alerts_no_order(tmp_path):
    cfg = _config(tmp_path)
    registry = Registry(cfg.registry_db_path)
    executor = MagicMock()
    sender = MagicMock(send=MagicMock(return_value=True))
    alerts = AlertWriter(registry=registry, telegram=sender, clock=lambda: 1)
    app = create_app(config=cfg, registry=registry, executor=executor, alerts=alerts)
    client = TestClient(app)

    r = _post_event(client, event_id="evt_1", query_id="q_unknown",
                    body_dict={"queryId": "q_unknown"})
    assert r.status_code == 200

    fire = registry.get_fire("evt_1")
    assert fire["outcome"] == "unknown_strategy"
    pending = registry.list_alerts(only_unacked=True)
    assert any(a["category"] == "unknown_strategy" for a in pending)
    executor.place_entry_with_tpsl.assert_not_called()


def test_guardrail_rejection_alerts_no_order(tmp_path):
    # Trigger the notional-cap guardrail: strategy max_notional_usd=4000,
    # amount=0.05, mid=100_000 -> notional=5000 > 4000 -> reject.
    cfg = _config(tmp_path)
    registry = Registry(cfg.registry_db_path)
    registry.insert_strategy(_strategy())
    executor = MagicMock()
    executor.fetch_mid_price.return_value = 100_000.0
    sender = MagicMock(send=MagicMock(return_value=True))
    alerts = AlertWriter(registry=registry, telegram=sender, clock=lambda: 1)
    app = create_app(config=cfg, registry=registry, executor=executor, alerts=alerts)
    client = TestClient(app)

    _post_event(client, event_id="evt_1", query_id="q_abc",
                body_dict={"queryId": "q_abc"})
    fire = registry.get_fire("evt_1")
    assert fire["outcome"] == "rejected_guardrail"
    pending = registry.list_alerts(only_unacked=True)
    assert any(a["category"] == "guardrail_rejected" for a in pending)
    executor.place_entry_with_tpsl.assert_not_called()


def test_terminal_bulk_error_marks_fired_and_alerts(tmp_path):
    cfg = _config(tmp_path)
    registry = Registry(cfg.registry_db_path)
    registry.insert_strategy(_strategy())
    executor = MagicMock()
    executor.fetch_mid_price.return_value = 60000.0
    executor.place_entry_with_tpsl.return_value = _fail_pair(
        "parent: code=2010: insufficient margin"
    )
    sender = MagicMock(send=MagicMock(return_value=True))
    alerts = AlertWriter(registry=registry, telegram=sender, clock=lambda: 1)
    app = create_app(config=cfg, registry=registry, executor=executor, alerts=alerts)
    client = TestClient(app)

    _post_event(client, event_id="evt_1", query_id="q_abc",
                body_dict={"queryId": "q_abc"})

    fire = registry.get_fire("evt_1")
    assert fire["outcome"] == "grvt_error"
    strat = registry.get_strategy("q_abc")
    assert strat.status == "fired"
    pending = registry.list_alerts(only_unacked=True)
    assert any(a["category"] == "insufficient_margin" for a in pending)


def test_transient_bulk_error_keeps_strategy_active(tmp_path):
    cfg = _config(tmp_path)
    registry = Registry(cfg.registry_db_path)
    registry.insert_strategy(_strategy())
    executor = MagicMock()
    executor.fetch_mid_price.return_value = 60000.0
    executor.place_entry_with_tpsl.return_value = _fail_pair(
        "bulk_orders submission failed: connection reset"
    )
    sender = MagicMock(send=MagicMock(return_value=True))
    alerts = AlertWriter(registry=registry, telegram=sender, clock=lambda: 1)
    app = create_app(config=cfg, registry=registry, executor=executor, alerts=alerts)
    client = TestClient(app)

    _post_event(client, event_id="evt_1", query_id="q_abc",
                body_dict={"queryId": "q_abc"})

    fire = registry.get_fire("evt_1")
    assert fire["outcome"] == "grvt_error"
    strat = registry.get_strategy("q_abc")
    assert strat.status == "active"  # left active for Auto retry


def test_set_leverage_failure_skips_order(tmp_path):
    cfg = _config(tmp_path)
    registry = Registry(cfg.registry_db_path)
    registry.insert_strategy(_strategy(leverage=5))
    executor = MagicMock()
    executor.fetch_mid_price.return_value = 60000.0
    executor.set_leverage.side_effect = GrvtError(
        "invalid leverage", error_class=ErrorClass.LEVERAGE
    )
    sender = MagicMock(send=MagicMock(return_value=True))
    alerts = AlertWriter(registry=registry, telegram=sender, clock=lambda: 1)
    app = create_app(config=cfg, registry=registry, executor=executor, alerts=alerts)
    client = TestClient(app)

    _post_event(client, event_id="evt_1", query_id="q_abc",
                body_dict={"queryId": "q_abc"})
    fire = registry.get_fire("evt_1")
    assert fire["outcome"] == "grvt_error"
    strat = registry.get_strategy("q_abc")
    assert strat.status == "fired"
    executor.place_entry_with_tpsl.assert_not_called()


def test_malformed_json_emits_internal_error_alert(tmp_path):
    cfg = _config(tmp_path)
    registry = Registry(cfg.registry_db_path)
    executor = MagicMock()
    sender = MagicMock(send=MagicMock(return_value=True))
    alerts = AlertWriter(registry=registry, telegram=sender, clock=lambda: 1)
    app = create_app(config=cfg, registry=registry, executor=executor, alerts=alerts)
    client = TestClient(app)

    body = b"not json {{{"
    r = client.post(
        "/auto/events",
        content=body,
        headers={
            "X-Auto-Event-Id": "evt_bad",
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 200  # accepted; JSON parse fails inside background task

    pending = registry.list_alerts(only_unacked=True)
    assert any(a["category"] == "receiver_internal_error" for a in pending)
    executor.place_entry_with_tpsl.assert_not_called()


def test_post_order_registry_failure_triggers_manual_intervention_alert(tmp_path):
    cfg = _config(tmp_path)
    registry = Registry(cfg.registry_db_path)
    registry.insert_strategy(_strategy())
    executor = MagicMock()
    executor.fetch_mid_price.return_value = 60000.0
    executor.place_entry_with_tpsl.return_value = _ok_pair("ord_real_money")
    sender = MagicMock(send=MagicMock(return_value=True))
    alerts = AlertWriter(registry=registry, telegram=sender, clock=lambda: 1)

    # Patch set_strategy_status to raise AFTER the order is placed.
    original_set = registry.set_strategy_status

    def raising_set(*args, **kwargs):
        raise RuntimeError("simulated DB lock")

    registry.set_strategy_status = raising_set

    app = create_app(config=cfg, registry=registry, executor=executor, alerts=alerts)
    client = TestClient(app)
    _post_event(client, event_id="evt_1", query_id="q_abc",
                body_dict={"queryId": "q_abc"})

    # Restore so the test can read alerts.
    registry.set_strategy_status = original_set

    pending = registry.list_alerts(only_unacked=True)
    manual_alerts = [a for a in pending if a["category"] == "manual_intervention_required"]
    assert len(manual_alerts) == 1
    assert "ord_real_money" in manual_alerts[0]["message"]
    # The normal "order_placed" success alert should NOT have been emitted.
    success_alerts = [a for a in pending if a["category"] == "order_placed"]
    assert len(success_alerts) == 0


def test_strategy_with_tpsl_arms_close_orders_atomically(tmp_path):
    cfg = _config(tmp_path)
    registry = Registry(cfg.registry_db_path)
    registry.insert_strategy(_strategy(side="sell", tp_pct=1.5, sl_pct=1.0))
    executor = MagicMock()
    executor.fetch_mid_price.return_value = 100.0
    executor.place_entry_with_tpsl.return_value = {
        "parent_order_id": "ord_entry",
        "tp_order_id": "ord_tp",
        "sl_order_id": "ord_sl",
        "tp_price": 98.5,
        "sl_price": 101.0,
        "errors": [],
    }
    sender = MagicMock(send=MagicMock(return_value=True))
    alerts = AlertWriter(registry=registry, telegram=sender, clock=lambda: 1)
    app = create_app(config=cfg, registry=registry, executor=executor, alerts=alerts)
    client = TestClient(app)

    _post_event(client, event_id="evt_1", query_id="q_abc",
                body_dict={"queryId": "q_abc"})

    executor.place_entry_with_tpsl.assert_called_once()
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
    # The strategy should be marked fired.
    strat = registry.get_strategy("q_abc")
    assert strat.status == "fired"


def test_strategy_with_tpsl_partial_failure_emits_manual_intervention(tmp_path):
    cfg = _config(tmp_path)
    registry = Registry(cfg.registry_db_path)
    registry.insert_strategy(_strategy(side="sell", tp_pct=1.5, sl_pct=1.0))
    executor = MagicMock()
    executor.fetch_mid_price.return_value = 100.0
    executor.place_entry_with_tpsl.return_value = {
        "parent_order_id": "ord_entry",
        "tp_order_id": "ord_tp",
        "sl_order_id": None,
        "tp_price": 98.5,
        "sl_price": 101.0,
        "errors": ["sl: code=2020: trigger rejected"],
    }
    sender = MagicMock(send=MagicMock(return_value=True))
    alerts = AlertWriter(registry=registry, telegram=sender, clock=lambda: 1)
    app = create_app(config=cfg, registry=registry, executor=executor, alerts=alerts)
    client = TestClient(app)

    _post_event(client, event_id="evt_1", query_id="q_abc",
                body_dict={"queryId": "q_abc"})

    pending = registry.list_alerts(only_unacked=True)
    manual = [a for a in pending if a["category"] == "manual_intervention_required"]
    assert len(manual) == 1
    msg = manual[0]["message"]
    assert "ord_entry" in msg
    assert "trigger rejected" in msg
    # Entry placed -> strategy still goes fired even though TP/SL had partial failure.
    strat = registry.get_strategy("q_abc")
    assert strat.status == "fired"


def test_strategy_without_tpsl_uses_atomic_path_too(tmp_path):
    cfg = _config(tmp_path)
    registry = Registry(cfg.registry_db_path)
    registry.insert_strategy(_strategy())  # tp_pct=None, sl_pct=None
    executor = MagicMock()
    executor.fetch_mid_price.return_value = 60000.0
    executor.place_entry_with_tpsl.return_value = _ok_pair("ord_xyz")
    sender = MagicMock(send=MagicMock(return_value=True))
    alerts = AlertWriter(registry=registry, telegram=sender, clock=lambda: 1)
    app = create_app(config=cfg, registry=registry, executor=executor, alerts=alerts)
    client = TestClient(app)

    _post_event(client, event_id="evt_1", query_id="q_abc",
                body_dict={"queryId": "q_abc"})

    # Single-call atomic flow regardless of TP/SL presence.
    executor.place_entry_with_tpsl.assert_called_once()
    fire = registry.get_fire("evt_1")
    assert fire["outcome"] == "placed"
    pending = registry.list_alerts(only_unacked=True)
    # No tpsl_armed alert when neither pct is set.
    assert not any(a["category"] == "tpsl_armed" for a in pending)


def test_non_active_strategy_rejection_does_not_telegram(tmp_path):
    cfg = _config(tmp_path)
    registry = Registry(cfg.registry_db_path)
    registry.insert_strategy(_strategy(status="fired"))
    executor = MagicMock()
    executor.fetch_mid_price.return_value = 60000.0
    sender = MagicMock(send=MagicMock(return_value=True))
    alerts = AlertWriter(registry=registry, telegram=sender, clock=lambda: 1)
    app = create_app(config=cfg, registry=registry, executor=executor, alerts=alerts)
    client = TestClient(app)

    _post_event(client, event_id="evt_replay", query_id="q_abc",
                body_dict={"queryId": "q_abc"})

    fire = registry.get_fire("evt_replay")
    assert fire["outcome"] == "rejected_guardrail"
    pending = registry.list_alerts(only_unacked=True)
    assert pending == []
    sender.send.assert_not_called()
    executor.place_entry_with_tpsl.assert_not_called()
