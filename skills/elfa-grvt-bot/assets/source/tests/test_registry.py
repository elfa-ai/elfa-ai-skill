import sqlite3
from pathlib import Path

import pytest

from elfa_grvt_bot.registry import Registry


@pytest.fixture
def db_path(tmp_path) -> Path:
    return tmp_path / "test.db"


def test_connect_creates_three_tables(db_path):
    Registry(str(db_path))  # __init__ runs migrations
    con = sqlite3.connect(db_path)
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = [r[0] for r in rows]
    assert "alerts" in names
    assert "fires" in names
    assert "strategies" in names


def test_connect_is_idempotent(db_path):
    Registry(str(db_path))
    Registry(str(db_path))  # no error on second open


import json
import time

from elfa_grvt_bot.registry import Strategy


def _sample_strategy(query_id: str = "q_abc") -> Strategy:
    return Strategy(
        query_id=query_id,
        title="BTC RSI dip",
        description="buy if RSI < 30",
        eql_json=json.dumps({"conditions": {}}),
        symbol="BTC_USDT_Perp",
        side="buy",
        amount=0.05,
        order_type="market",
        price=None,
        leverage=None,
        tp_pct=None,
        sl_pct=None,
        time_in_force=None,
        reduce_only=False,
        max_notional_usd=4000.0,
        env="prod",
        status="active",
        created_at=int(time.time()),
        fired_at=None,
    )


def test_insert_and_get_strategy(db_path):
    r = Registry(str(db_path))
    s = _sample_strategy()
    r.insert_strategy(s)
    got = r.get_strategy("q_abc")
    assert got is not None
    assert got.symbol == "BTC_USDT_Perp"
    assert got.amount == 0.05
    assert got.status == "active"


def test_get_strategy_missing_returns_none(db_path):
    r = Registry(str(db_path))
    assert r.get_strategy("q_missing") is None


def test_set_strategy_status(db_path):
    r = Registry(str(db_path))
    r.insert_strategy(_sample_strategy())
    r.set_strategy_status("q_abc", "fired", fired_at=12345)
    got = r.get_strategy("q_abc")
    assert got.status == "fired"
    assert got.fired_at == 12345


def test_strategy_with_tp_sl_pcts_round_trip(db_path):
    r = Registry(str(db_path))
    s = Strategy(
        query_id="q_tpsl",
        title="short with TP/SL",
        description=None,
        eql_json=json.dumps({"conditions": {}}),
        symbol="SOL_USDT_Perp",
        side="sell",
        amount=0.5,
        order_type="market",
        price=None,
        leverage=3,
        tp_pct=1.5,
        sl_pct=1.0,
        time_in_force=None,
        reduce_only=False,
        max_notional_usd=200.0,
        env="prod",
        status="active",
        created_at=int(time.time()),
        fired_at=None,
    )
    r.insert_strategy(s)
    got = r.get_strategy("q_tpsl")
    assert got is not None
    assert got.tp_pct == 1.5
    assert got.sl_pct == 1.0


def test_list_strategies_by_status(db_path):
    r = Registry(str(db_path))
    r.insert_strategy(_sample_strategy("q_active"))
    r.insert_strategy(_sample_strategy("q_done"))
    r.set_strategy_status("q_done", "fired", fired_at=12345)
    actives = r.list_strategies(status="active")
    assert [s.query_id for s in actives] == ["q_active"]


def test_insert_fire_returns_true_for_new_event(db_path):
    r = Registry(str(db_path))
    inserted = r.insert_fire_if_new(
        event_id="evt_1",
        query_id="q_abc",
        received_at=1000,
        outcome="pending",
        raw_payload="{}",
    )
    assert inserted is True


def test_insert_fire_returns_false_for_duplicate(db_path):
    r = Registry(str(db_path))
    r.insert_fire_if_new(
        event_id="evt_1", query_id="q_abc", received_at=1000,
        outcome="pending", raw_payload="{}",
    )
    second = r.insert_fire_if_new(
        event_id="evt_1", query_id="q_abc", received_at=1001,
        outcome="pending", raw_payload='{"new":"data"}',
    )
    assert second is False
    # IGNORE (not REPLACE): original row's received_at and raw_payload survive
    got = r.get_fire("evt_1")
    assert got["received_at"] == 1000
    assert got["raw_payload"] == "{}"


def test_update_fire_outcome(db_path):
    r = Registry(str(db_path))
    r.insert_fire_if_new(
        event_id="evt_1", query_id="q_abc", received_at=1000,
        outcome="pending", raw_payload="{}",
    )
    r.update_fire_outcome("evt_1", outcome="placed", grvt_order_id="ord_xyz")
    got = r.get_fire("evt_1")
    assert got["outcome"] == "placed"
    assert got["grvt_order_id"] == "ord_xyz"


def test_get_fire_missing_returns_none(db_path):
    r = Registry(str(db_path))
    assert r.get_fire("evt_missing") is None


def test_update_fire_outcome_raises_on_missing(db_path):
    r = Registry(str(db_path))
    with pytest.raises(KeyError, match="evt_missing"):
        r.update_fire_outcome("evt_missing", outcome="placed")


def test_insert_alert_returns_id(db_path):
    r = Registry(str(db_path))
    alert_id = r.insert_alert(
        severity="error",
        category="insufficient_margin",
        message="not enough margin",
        query_id="q_abc",
        fire_event_id="evt_1",
        details_json='{"foo":"bar"}',
        created_at=1000,
    )
    assert alert_id >= 1


def test_list_unacked_alerts(db_path):
    r = Registry(str(db_path))
    r.insert_alert(severity="error", category="grvt_other",
                   message="x", created_at=1000)
    a2 = r.insert_alert(severity="warning", category="guardrail_rejected",
                        message="y", created_at=1001)
    r.ack_alert(a2)
    pending = r.list_alerts(only_unacked=True)
    assert len(pending) == 1
    assert pending[0]["category"] == "grvt_other"


def test_ack_all(db_path):
    r = Registry(str(db_path))
    r.insert_alert(severity="error", category="x", message="m", created_at=1)
    r.insert_alert(severity="error", category="y", message="m", created_at=2)
    r.ack_all_alerts()
    pending = r.list_alerts(only_unacked=True)
    assert pending == []


def test_ack_alert_raises_on_missing(db_path):
    r = Registry(str(db_path))
    with pytest.raises(KeyError, match="999"):
        r.ack_alert(999)


def test_list_alerts_orders_newest_first(db_path):
    r = Registry(str(db_path))
    r.insert_alert(severity="info", category="x",
                   message="older", created_at=1000)
    r.insert_alert(severity="info", category="y",
                   message="newer", created_at=2000)
    rows = r.list_alerts()
    assert [a["message"] for a in rows] == ["newer", "older"]
