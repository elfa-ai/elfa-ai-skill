from __future__ import annotations

import asyncio
import logging
import signal
import sys

from pysdk.grvt_ccxt import GrvtCcxt
from pysdk.grvt_ccxt_env import GrvtEnv

from .alerts import AlertWriter
from .config import Config
from .elfa_client import ElfaClient
from .grvt_executor import GrvtExecutor
from .grvt_trigger_client import GrvtTriggerClient
from .receiver import supervisor
from .registry import Registry
from .telegram_sender import TelegramSender

logger = logging.getLogger(__name__)


def _load_or_exit() -> Config:
    try:
        return Config.load()
    except (RuntimeError, ValueError) as exc:
        logger.error("config load failed: %s", exc)
        logger.error(
            "set required env vars (see .env.example) and restart. "
            "Tip: `set -a && source .env && set +a`"
        )
        sys.exit(2)


def _build_components(config: Config):
    registry = Registry(config.registry_db_path)
    elfa = ElfaClient(api_key=config.elfa_api_key)

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
        logger.error("GrvtCcxt init failed (env=%s): %s", config.grvt_env, exc)
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
        logger.error("GrvtTriggerClient init failed (env=%s): %s", config.grvt_env, exc)
        sys.exit(4)

    executor = GrvtExecutor(client=grvt_client, trigger_client=trigger_client)
    telegram = TelegramSender(
        bot_token=config.telegram_bot_token, chat_id=config.telegram_chat_id
    )
    alerts = AlertWriter(registry=registry, telegram=telegram)
    return registry, elfa, executor, alerts


async def _run() -> None:
    config = _load_or_exit()
    registry, elfa, executor, alerts = _build_components(config)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass  # Windows

    logger.info("starting SSE supervisor (GRVT env=%s)", config.grvt_env)
    await supervisor(
        config=config, registry=registry, elfa=elfa,
        executor=executor, alerts=alerts, stop=stop,
    )
    logger.info("supervisor exited cleanly")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(_run())


if __name__ == "__main__":
    main()
