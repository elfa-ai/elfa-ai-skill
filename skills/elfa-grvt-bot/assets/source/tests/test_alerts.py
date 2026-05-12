import pytest
from unittest.mock import MagicMock

from elfa_grvt_bot.alerts import AlertWriter
from elfa_grvt_bot.registry import Registry


@pytest.fixture
def registry(tmp_path) -> Registry:
    return Registry(str(tmp_path / "alerts.db"))


def test_emit_inserts_alert_and_sends_telegram(registry):
    sender = MagicMock()
    sender.send = MagicMock(return_value=True)
    aw = AlertWriter(registry=registry, telegram=sender,
                     clock=lambda: 1000)

    aid = aw.emit(
        severity="error",
        category="insufficient_margin",
        message="not enough margin",
        query_id="q_abc",
        fire_event_id="evt_1",
        details={"required": 100.0, "available": 50.0},
    )
    assert aid >= 1

    pending = registry.list_alerts(only_unacked=True)
    assert len(pending) == 1
    assert pending[0]["category"] == "insufficient_margin"

    sender.send.assert_called_once()
    sent_text = sender.send.call_args.args[0]
    assert "INSUFFICIENT MARGIN" in sent_text or "insufficient_margin" in sent_text.lower()
    assert "q_abc" in sent_text


def test_alert_persists_even_when_telegram_fails(registry):
    sender = MagicMock()
    sender.send = MagicMock(return_value=False)
    aw = AlertWriter(registry=registry, telegram=sender,
                     clock=lambda: 1000)

    aid = aw.emit(
        severity="error", category="grvt_other", message="boom",
    )
    assert aid >= 1
    assert len(registry.list_alerts(only_unacked=True)) == 1
