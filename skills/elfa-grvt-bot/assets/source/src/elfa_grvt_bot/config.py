from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    elfa_api_key: str
    grvt_api_key: str
    grvt_private_key: str
    grvt_trading_account_id: str
    grvt_env: str
    # Telegram is optional: empty strings disable the push channel; alerts
    # still land in the in-chat registry.
    telegram_bot_token: str
    telegram_chat_id: str
    registry_db_path: str

    @classmethod
    def load(cls) -> "Config":
        def required(name: str) -> str:
            v = os.environ.get(name)
            if not v:
                raise RuntimeError(f"missing required env var: {name}")
            return v

        grvt_env = os.environ.get("GRVT_ENV", "prod")
        if grvt_env != "prod":
            raise ValueError(
                f"GRVT_ENV must be 'prod' (this bot is prod-only by design; "
                f"see references/setup.md). got: {grvt_env!r}"
            )

        return cls(
            elfa_api_key=required("ELFA_API_KEY"),
            grvt_api_key=required("GRVT_API_KEY"),
            grvt_private_key=required("GRVT_PRIVATE_KEY"),
            grvt_trading_account_id=required("GRVT_TRADING_ACCOUNT_ID"),
            grvt_env=grvt_env,
            telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
            registry_db_path=required("REGISTRY_DB_PATH"),
        )
