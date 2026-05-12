from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)


class TelegramSender:
    def __init__(self, bot_token: str, chat_id: str, timeout: float = 5.0) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout

    def send(self, text: str) -> bool:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            resp = requests.post(
                url,
                data={"chat_id": self.chat_id, "text": text},
                timeout=self.timeout,
            )
        except requests.exceptions.RequestException as exc:
            logger.warning("telegram send failed: %s", exc)
            return False
        if not resp.ok:
            logger.warning(
                "telegram send returned HTTP %s: %s", resp.status_code, resp.text[:200]
            )
            return False
        return True
