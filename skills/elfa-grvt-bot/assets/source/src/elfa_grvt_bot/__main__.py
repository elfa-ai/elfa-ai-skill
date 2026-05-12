from __future__ import annotations

import logging
import sys

import uvicorn
from fastapi import FastAPI
from pysdk.grvt_ccxt import GrvtCcxt
from pysdk.grvt_ccxt_env import GrvtEnv

from .alerts import AlertWriter
from .config import Config
from .grvt_executor import GrvtExecutor
from .grvt_trigger_client import GrvtTriggerClient
from .receiver import create_app
from .registry import Registry
from .telegram_sender import TelegramSender

logger = logging.getLogger(__name__)


def build_app() -> FastAPI:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = Config.load()
    except (RuntimeError, ValueError) as exc:
        logger.error("config load failed: %s", exc)
        logger.error(
            "set required env vars (see .env.example) and restart. "
            "Tip: `set -a && source .env && set +a`"
        )
        sys.exit(2)

    registry = Registry(config.registry_db_path)

    try:
        grvt_client = GrvtCcxt(
            env=GrvtEnv(config.grvt_env),
            parameters={
                "api_key": config.grvt_api_key,
                "private_key": config.grvt_private_key,
                "trading_account_id": config.grvt_trading_account_id,
            },
        )
    except Exception as exc:
        logger.error(
            "GrvtCcxt init failed (env=%s): %s", config.grvt_env, exc
        )
        logger.error(
            "check GRVT_API_KEY / GRVT_PRIVATE_KEY / GRVT_TRADING_ACCOUNT_ID "
            "and that the GRVT %s endpoint is reachable", config.grvt_env
        )
        sys.exit(3)

    try:
        trigger_client = GrvtTriggerClient(
            env=config.grvt_env,
            trading_account_id=config.grvt_trading_account_id,
            private_key=config.grvt_private_key,
            api_key=config.grvt_api_key,
        )
    except Exception as exc:
        logger.error(
            "GrvtTriggerClient init failed (env=%s): %s", config.grvt_env, exc
        )
        logger.error(
            "trigger client is required for TP/SL strategies; check "
            "GRVT_API_KEY / GRVT_PRIVATE_KEY / GRVT_TRADING_ACCOUNT_ID"
        )
        sys.exit(4)

    executor = GrvtExecutor(client=grvt_client, trigger_client=trigger_client)
    telegram = TelegramSender(
        bot_token=config.telegram_bot_token, chat_id=config.telegram_chat_id
    )
    alerts = AlertWriter(registry=registry, telegram=telegram)
    return create_app(
        config=config, registry=registry, executor=executor, alerts=alerts
    )


def main() -> None:
    app = build_app()
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
