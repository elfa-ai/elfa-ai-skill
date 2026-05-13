from elfa_grvt_bot.guardrails import (
    check_guardrails, Allow, Reject,
)
from elfa_grvt_bot.registry import Strategy


def _strategy(**overrides) -> Strategy:
    base = dict(
        query_id="q_abc",
        title="t", description=None,
        eql_json="{}",
        symbol="BTC_USDT_Perp",
        side="buy", amount=0.05, order_type="market",
        price=None, leverage=None,
        tp_pct=None, sl_pct=None,
        time_in_force=None,
        reduce_only=False, max_notional_usd=4000.0, env="prod",
        status="active", created_at=1, fired_at=None,
    )
    base.update(overrides)
    return Strategy(**base)


def test_allow_when_within_caps():
    r = check_guardrails(
        strategy=_strategy(),
        current_mid=60000.0,  # notional = 60000 * 0.05 = 3000 <= 4000
        receiver_env="prod",
    )
    assert isinstance(r, Allow)


def test_reject_notional_above_cap():
    r = check_guardrails(
        strategy=_strategy(amount=0.5),  # 60000 * 0.5 = 30000 > 4000
        current_mid=60000.0,
        receiver_env="prod",
    )
    assert isinstance(r, Reject)
    assert r.category == "guardrail_rejected"
    assert "notional" in r.reason.lower()


def test_reject_env_mismatch():
    r = check_guardrails(
        strategy=_strategy(env="testnet"),
        current_mid=60000.0,
        receiver_env="prod",
    )
    assert isinstance(r, Reject)
    assert r.category == "guardrail_rejected"
    assert "env" in r.reason.lower()


def test_reject_inactive_strategy():
    r = check_guardrails(
        strategy=_strategy(status="fired"),
        current_mid=60000.0,
        receiver_env="prod",
    )
    assert isinstance(r, Reject)
    # guardrail_status (not guardrail_rejected) so the receiver can log-only
    # , see spec section 6: status mismatches are logs-only, no Telegram spam on retries
    assert r.category == "guardrail_status"
    assert "status" in r.reason.lower()
