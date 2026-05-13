import pytest
from elfa_grvt_bot.config import Config


def _full_env(monkeypatch):
    env = {
        "ELFA_API_KEY": "ek_test",
        "GRVT_API_KEY": "grvt_test",
        "GRVT_PRIVATE_KEY": "0xprivkey",
        "GRVT_TRADING_ACCOUNT_ID": "ta_1",
        "TELEGRAM_BOT_TOKEN": "bot_test",
        "TELEGRAM_CHAT_ID": "12345",
        "REGISTRY_DB_PATH": "/tmp/registry-test.db",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("GRVT_ENV", raising=False)


def test_defaults_grvt_env_to_prod_when_unset(monkeypatch):
    _full_env(monkeypatch)
    cfg = Config.load()
    assert cfg.grvt_env == "prod"


def test_missing_required_var_raises(monkeypatch):
    _full_env(monkeypatch)
    monkeypatch.delenv("ELFA_API_KEY")
    with pytest.raises(RuntimeError, match="ELFA_API_KEY"):
        Config.load()


def test_grvt_env_non_prod_raises(monkeypatch):
    """Prod-only by design. Loading with anything else aborts boot."""
    _full_env(monkeypatch)
    for env in ("testnet", "staging", "dev", "mainnet"):
        monkeypatch.setenv("GRVT_ENV", env)
        with pytest.raises(ValueError, match="GRVT_ENV"):
            Config.load()


def test_telegram_vars_optional_when_unset(monkeypatch):
    _full_env(monkeypatch)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    cfg = Config.load()
    assert cfg.telegram_bot_token == ""
    assert cfg.telegram_chat_id == ""


def test_telegram_vars_partial_still_loads(monkeypatch):
    _full_env(monkeypatch)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    cfg = Config.load()
    assert cfg.telegram_bot_token == "bot_test"
    assert cfg.telegram_chat_id == ""
