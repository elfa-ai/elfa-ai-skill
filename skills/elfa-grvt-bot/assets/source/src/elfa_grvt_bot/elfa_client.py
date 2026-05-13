from __future__ import annotations

import json
import logging
from typing import AsyncIterator, Optional

import httpx
import requests

logger = logging.getLogger(__name__)


class ElfaStreamError(RuntimeError):
    """Raised when the SSE stream returns an unexpected HTTP status. Carries
    `status_code` so callers (the strategy loop) can branch on 404 (query
    no longer exists remotely) vs 401 (auth) vs 5xx (transient)."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"elfa stream_notifications failed: {status_code} {body}")
        self.status_code = status_code
        self.body = body


_TRIGGER_EVENT_TYPES = ("query.triggered", "notification")
_REQUIRED_EVENT_FIELDS = ("queryId", "executionId", "triggerTime", "status")
_TRIGGER_STATUS = "triggered"
_NOTIFY_ACTION_TYPES = {"notify", "telegram_bot", "webhook"}


async def _parse_sse_frames(
    lines: AsyncIterator[str], expected_query_id: str
) -> AsyncIterator[dict]:
    """Generator over SSE lines that yields well-formed trigger events only.

    Drops (with a warning log) frames that:
 - have the wrong event type
 - have unparsable JSON in `data:`
 - have a payload that isn't a dict
 - are missing canonical top-level fields
 - belong to a different queryId than the stream URL

    Concatenates multiple `data:` lines with `\\n` per SSE spec.
    """
    current_event: Optional[str] = None
    current_id: Optional[str] = None
    data_lines: list[str] = []
    in_frame = False
    async for line in lines:
        if line.startswith(":"):
            continue  # keep-alive comment
        if line == "":
            if in_frame:
                event = _build_event(
                    event_type=current_event,
                    sse_id=current_id,
                    data="\n".join(data_lines) if data_lines else None,
                    expected_query_id=expected_query_id,
                )
                if event is not None:
                    yield event
            current_event, current_id, data_lines, in_frame = None, None, [], False
            continue
        in_frame = True
        if ":" not in line:
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        field = field.strip()
        if field == "event":
            current_event = value.strip()
        elif field == "id":
            current_id = value.strip()
        elif field == "data":
            data_lines.append(value)


def _build_event(
    *,
    event_type: Optional[str],
    sse_id: Optional[str],
    data: Optional[str],
    expected_query_id: str,
) -> Optional[dict]:
    # Production schema (captured 2026-05-13 against api.elfa.ai):
    #   event: notification
    #   id: <sse-level uuid>
    #   data: {"status":"triggered","queryId":"<uuid>","executionId":"<uuid>",
    #          "triggerTime":"2026-...Z","timestamp":<epoch_ms>,
    #          "title":..,"body":..,"message":..,"conditionsMet":<int>, ...}
    # `executionId` is the dedupe key and matches poll-query's
    # `executions[i].id`, so it is safe to use as canonical idempotency
    # key across both channels.
    if event_type not in _TRIGGER_EVENT_TYPES:
        return None
    if not data:
        logger.warning("dropping SSE %r frame: missing data", event_type)
        return None
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        logger.warning("dropping SSE %r frame: data is not JSON: %.200s", event_type, data)
        return None
    if not isinstance(payload, dict):
        logger.warning("dropping SSE %r frame: data is not a JSON object", event_type)
        return None
    missing = [field for field in _REQUIRED_EVENT_FIELDS if field not in payload]
    if missing:
        logger.warning("dropping SSE %r frame: missing fields %s", event_type, missing)
        return None
    if payload.get("status") != _TRIGGER_STATUS:
        logger.warning(
            "dropping SSE %r frame: status %r != %r",
            event_type, payload.get("status"), _TRIGGER_STATUS,
        )
        return None
    execution_id = payload.get("executionId")
    if not isinstance(execution_id, str) or not execution_id:
        logger.warning("dropping SSE %r frame: invalid executionId", event_type)
        return None
    if not isinstance(payload.get("triggerTime"), str) or not payload.get("triggerTime"):
        logger.warning("dropping SSE %r frame: invalid triggerTime", event_type)
        return None
    payload_qid = payload.get("queryId")
    if payload_qid != expected_query_id:
        logger.warning(
            "dropping SSE frame: payload queryId %r != stream queryId %r",
            payload_qid, expected_query_id,
        )
        return None
    return {"event_id": execution_id, "data": payload}


def _is_notify_action(action: object) -> bool:
    if not isinstance(action, dict):
        return False
    action_type = action.get("type")
    if action_type in _NOTIFY_ACTION_TYPES:
        return True
    if action_type == "llm":
        params = action.get("params", {})
        if not isinstance(params, dict):
            return False
        callback = params.get("callback", {})
        if not isinstance(callback, dict):
            return False
        callback_action = callback.get("action", {})
        return (
            isinstance(callback_action, dict)
            and callback_action.get("type") in _NOTIFY_ACTION_TYPES
        )
    return False


class ElfaClient:
    """Thin client over /v2/auto/* endpoints for notify-only queries."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.elfa.ai",
        timeout: float = 10.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _post(self, full_path: str, body: Optional[dict], *, op: str) -> dict:
        url = f"{self.base_url}{full_path}"
        kwargs = {
            "headers": {
                "Content-Type": "application/json",
                "x-elfa-api-key": self.api_key,
            },
            "timeout": self.timeout,
        }
        if body is not None:
            kwargs["data"] = json.dumps(body, separators=(",", ":"), sort_keys=False)
        resp = requests.post(url, **kwargs)
        return self._handle(resp, op=op)

    def _get(self, full_path: str, *, op: str) -> dict:
        resp = requests.get(
            f"{self.base_url}{full_path}",
            headers={"x-elfa-api-key": self.api_key},
            timeout=self.timeout,
        )
        return self._handle(resp, op=op)

    @staticmethod
    def _handle(resp: requests.Response, *, op: str) -> dict:
        if not resp.ok:
            raise RuntimeError(
                f"elfa {op} failed: {resp.status_code} {resp.text[:300]}"
            )
        return resp.json()

    def builder_chat(self, *, prompt: str, session_id: Optional[str] = None) -> dict:
        body = {"message": prompt}
        if session_id:
            body["sessionId"] = session_id
        return self._post("/v2/auto/chat", body, op="builder_chat")

    def validate_query(self, query: dict) -> dict:
        return self._post(
            "/v2/auto/queries/validate", {"query": query}, op="validate_query"
        )

    def create_query(self, body: dict) -> dict:
        """body shape: { "title", "description", "query": {...} }."""
        query = body.get("query")
        if not isinstance(query, dict):
            raise ValueError("create_query requires a query object")
        actions = query.get("actions")
        if not isinstance(actions, list) or not actions:
            raise ValueError("create_query requires at least one notify-style action")
        if not all(_is_notify_action(action) for action in actions):
            raise ValueError("create_query supports notify-style actions only")
        return self._post("/v2/auto/queries", body, op="create_query")

    def cancel_query(self, query_id: str) -> dict:
        """Cancel an active query. Treat already-terminal (409) as success.

        Two-step lifecycle: cancel transitions status to 'cancelled' but
        leaves the row queryable. Hard-deletion (DELETE /v2/auto/queries/:id)
        is allowed only after cancel and is intentionally NOT done here so
        the strategy stays auditable.

        Elfa returns 409 if the query is already terminal
        (`triggered`/`expired`/`cancelled`/`failed`); we coerce that into
        a no-op success so callers don't have to special-case it.
        """
        url = f"{self.base_url}/v2/auto/queries/{query_id}/cancel"
        resp = requests.post(
            url,
            headers={
                "Content-Type": "application/json",
                "x-elfa-api-key": self.api_key,
            },
            timeout=self.timeout,
        )
        if resp.status_code == 409:
            try:
                body = resp.json()
            except ValueError:
                body = {}
            return {"id": query_id, "status": "already_terminal", "detail": body}
        return self._handle(resp, op="cancel_query")

    def get_query(self, query_id: str) -> dict:
        """Poll query state and execution rows.

        `executions[i].id` matches the SSE payload's `executionId` field
        (same UUID namespace, verified against production 2026-05-13).
        Safe to use as canonical idempotency key across both channels.
        """
        return self._get(f"/v2/auto/queries/{query_id}", op="get_query")

    def get_execution(self, execution_id: str) -> dict:
        return self._get(
            f"/v2/auto/executions/{execution_id}", op="get_execution"
        )

    async def stream_notifications(
        self, query_id: str, *, http_client: Optional[httpx.AsyncClient] = None
    ) -> AsyncIterator[dict]:
        """Yield well-formed `query.triggered` SSE events for one query.

        Fail-closed: any frame missing canonical top-level fields, with
        `queryId` not matching the requested query_id, a non-notify action,
        or unparsable JSON is logged and dropped. The caller never sees a
        malformed event and so cannot place a GRVT order on garbage.

        Production wire format (captured 2026-05-13 against api.elfa.ai):
            event: notification
            id: <sse-level uuid>
            data: {"status":"triggered","queryId":"<uuid>","executionId":"<uuid>","triggerTime":"2026-...Z","timestamp":<epoch_ms>,"title":..,"body":..,"message":..,"conditionsMet":<int>, ...}

        Yields {"event_id": "<executionId>", "data": <parsed payload>}.

        Status handling:
 - 200: parse the body as SSE
 - 204: no content (treat as empty stream)
 - 410: query already terminal (yield nothing, caller polls for status)
 - other: raise ElfaStreamError(status_code, body)
        """
        url = f"{self.base_url}/v2/auto/queries/{query_id}/stream"
        headers = {
            "x-elfa-api-key": self.api_key,
            "accept": "text/event-stream",
        }
        timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
        client = http_client or httpx.AsyncClient(timeout=timeout)
        owns_client = http_client is None
        try:
            async with client.stream("GET", url, headers=headers) as r:
                if r.status_code in (204, 410):
                    return
                if r.status_code != 200:
                    body = (await r.aread()).decode(errors="replace")[:300]
                    raise ElfaStreamError(r.status_code, body)
                async for ev in _parse_sse_frames(r.aiter_lines(), query_id):
                    yield ev
        finally:
            if owns_client:
                await client.aclose()
