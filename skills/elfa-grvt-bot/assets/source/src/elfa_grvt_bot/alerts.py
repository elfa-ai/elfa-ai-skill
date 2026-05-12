from __future__ import annotations

import json
import logging
import time
from typing import Callable, Optional, Protocol

from .registry import Registry

logger = logging.getLogger(__name__)


class _Sender(Protocol):
    def send(self, text: str) -> bool: ...


_INFO_PREFIXES = {
    "trigger_received": "TRIGGER",
    "order_placed": "PLACED",
    "tpsl_armed": "TP/SL ARMED",
}


def _format_message(
    *, severity: str, category: str, message: str, query_id: Optional[str]
) -> str:
    """
    Compose the body of a Telegram alert.

    Routine info events (trigger_received / order_placed / tpsl_armed) get a
    compact prefix and skip the strategy UUID and ack-instruction trailer
    since they don't need acknowledgment. Errors and warnings keep the full
    triage format with category, strategy id, and the ack hint.
    """
    if severity == "info":
        prefix = _INFO_PREFIXES.get(category, category.upper())
        return f"{prefix}\n{message}"

    sev_prefix = {"error": "ERROR", "warning": "WARN"}.get(
        severity, severity.upper()
    )
    parts = [f"{sev_prefix} [{category}]"]
    if query_id:
        parts.append(f"strategy={query_id}")
    parts.append(message)
    return "\n".join(parts)


class AlertWriter:
    def __init__(
        self,
        *,
        registry: Registry,
        telegram: _Sender,
        clock: Callable[[], int] = lambda: int(time.time()),
    ) -> None:
        self.registry = registry
        self.telegram = telegram
        self.clock = clock

    def emit(
        self,
        *,
        severity: str,
        category: str,
        message: str,
        query_id: Optional[str] = None,
        fire_event_id: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> int:
        details_json = json.dumps(details) if details is not None else None
        alert_id = self.registry.insert_alert(
            severity=severity,
            category=category,
            message=message,
            query_id=query_id,
            fire_event_id=fire_event_id,
            details_json=details_json,
            created_at=self.clock(),
        )
        text = _format_message(
            severity=severity, category=category, message=message, query_id=query_id
        )
        try:
            self.telegram.send(text)
        except Exception as exc:  # never let telegram bubble up
            logger.warning("telegram send raised unexpectedly: %s", exc)
        return alert_id
