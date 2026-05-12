from __future__ import annotations

import os
from dataclasses import dataclass

VALID_GRVT_ENVS = {"testnet", "prod", "staging", "dev"}


@dataclass(frozen=True)
class Config:
    elfa_api_key: str
    grvt_api_key: str
    grvt_private_key: str
    grvt_trading_account_id: str
    grvt_env: str
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
        if grvt_env not in VALID_GRVT_ENVS:
            raise ValueError(
                f"GRVT_ENV must be one of {sorted(VALID_GRVT_ENVS)}, got: {grvt_env!r}"
            )

        return cls(
            elfa_api_key=required("ELFA_API_KEY"),
            grvt_api_key=required("GRVT_API_KEY"),
            grvt_private_key=required("GRVT_PRIVATE_KEY"),
            grvt_trading_account_id=required("GRVT_TRADING_ACCOUNT_ID"),
            grvt_env=grvt_env,
            telegram_bot_token=required("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=required("TELEGRAM_CHAT_ID"),
            registry_db_path=required("REGISTRY_DB_PATH"),
        )
