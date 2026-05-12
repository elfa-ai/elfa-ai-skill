import json
from unittest.mock import MagicMock, patch

import pytest

from elfa_grvt_bot.registry import Registry, Strategy


def _seed(db_path):
    r = Registry(db_path)
    r.insert_strategy(Strategy(
        query_id="q_abc", title="t", description=None,
        eql_json="{}", symbol="BTC_USDT_Perp", side="buy",
        amount=0.05, order_type="market", price=None, leverage=None,
        tp_pct=None, sl_pct=None,
        time_in_force=None, reduce_only=False, max_notional_usd=4000.0,
        env="prod", status="active", created_at=1, fired_at=None,
    ))
    r.insert_alert(severity="error", category="grvt_other",
                   message="boom", created_at=10)
    return r


def test_list_active(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "r.db")
    _seed(db)
    monkeypatch.setenv("REGISTRY_DB_PATH", db)
    import registry_cli
    rc = registry_cli.main(["list", "--status", "active"])
    out = capsys.readouterr().out
    assert "q_abc" in out
    assert "BTC_USDT_Perp" in out
    assert rc == 0


def test_alerts_pending(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "r.db")
    _seed(db)
    monkeypatch.setenv("REGISTRY_DB_PATH", db)
    import registry_cli
    rc = registry_cli.main(["alerts", "--pending"])
    out = capsys.readouterr().out
    assert "grvt_other" in out
    assert rc == 0


def test_ack_clears_alert(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "r.db")
    r = _seed(db)
    monkeypatch.setenv("REGISTRY_DB_PATH", db)
    pending_before = r.list_alerts(only_unacked=True)
    aid = pending_before[0]["id"]

    import registry_cli
    rc = registry_cli.main(["ack", str(aid)])
    assert rc == 0
    pending_after = r.list_alerts(only_unacked=True)
    assert pending_after == []


def test_add_with_tp_sl_pcts(tmp_path, monkeypatch):
    db = str(tmp_path / "r.db")
    Registry(db)  # initialize schema
    monkeypatch.setenv("REGISTRY_DB_PATH", db)
    import registry_cli
    rc = registry_cli.main([
        "add",
        "--query-id", "q_tpsl",
        "--title", "short with tpsl",
        "--eql-json", "{}",
        "--symbol", "SOL_USDT_Perp",
        "--side", "sell",
        "--amount", "0.5",
        "--order-type", "market",
        "--max-notional-usd", "200",
        "--tp-pct", "1.5",
        "--sl-pct", "1.0",
    ])
    assert rc == 0
    s = Registry(db).get_strategy("q_tpsl")
    assert s is not None
    assert s.tp_pct == 1.5
    assert s.sl_pct == 1.0


def test_add_without_tp_sl_pcts_defaults_to_none(tmp_path, monkeypatch):
    db = str(tmp_path / "r.db")
    Registry(db)
    monkeypatch.setenv("REGISTRY_DB_PATH", db)
    import registry_cli
    rc = registry_cli.main([
        "add",
        "--query-id", "q_plain",
        "--title", "plain",
        "--eql-json", "{}",
        "--symbol", "BTC_USDT_Perp",
        "--side", "buy",
        "--amount", "0.05",
        "--order-type", "market",
        "--max-notional-usd", "4000",
    ])
    assert rc == 0
    s = Registry(db).get_strategy("q_plain")
    assert s.tp_pct is None
    assert s.sl_pct is None


def test_cancel_calls_elfa_and_updates_registry(tmp_path, monkeypatch):
    db = str(tmp_path / "r.db")
    _seed(db)
    monkeypatch.setenv("REGISTRY_DB_PATH", db)
    monkeypatch.setenv("ELFA_API_KEY", "ek")

    import registry_cli
    fake_client = MagicMock()
    fake_client.cancel_query.return_value = {"cancelled": True}
    with patch.object(registry_cli, "_make_elfa_client", return_value=fake_client):
        rc = registry_cli.main(["cancel", "q_abc"])
        assert rc == 0
        fake_client.cancel_query.assert_called_once_with("q_abc")

    r = Registry(db)
    s = r.get_strategy("q_abc")
    assert s.status == "cancelled"
