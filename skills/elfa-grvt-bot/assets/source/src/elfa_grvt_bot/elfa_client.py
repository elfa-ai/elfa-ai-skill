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


async def _parse_sse_frames(
    lines: AsyncIterator[str], expected_query_id: str
) -> AsyncIterator[dict]:
    """Generator over SSE lines that yields well-formed trigger events only.

    Drops (with a warning log) frames that:
 - have the wrong event type
 - have unparsable JSON in `data:`
 - have a payload that isn't a dict
 - are missing `eventId`
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
    event_id = payload.get("eventId") or sse_id
    if not event_id:
        logger.warning("dropping SSE %r frame: no eventId or SSE id", event_type)
        return None
    payload_qid = payload.get("queryId")
    if payload_qid and payload_qid != expected_query_id:
        logger.warning(
            "dropping SSE frame: payload queryId %r != stream queryId %r",
            payload_qid, expected_query_id,
        )
        return None
    return {"event_id": event_id, "data": payload}


class ElfaClient:
    """Thin client over /v2/auto/* endpoints. All routes accept API-key auth."""

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
        return self._post("/v2/auto/queries", body, op="create_query")

    def cancel_query(self, query_id: str) -> dict:
        """Cancel an active query.

        Two-step lifecycle: cancel transitions status to 'cancelled' but
        leaves the row queryable. Hard-deletion (DELETE /v2/auto/queries/:id)
        is allowed only after cancel and is intentionally NOT done here so
        the strategy stays auditable.
        """
        return self._post(
            f"/v2/auto/queries/{query_id}/cancel", None, op="cancel_query"
        )

    def get_query(self, query_id: str) -> dict:
        """Poll query state. Status reconciliation only.

        `executions[i].id` is `exec_xxx`, a different identifier namespace
        from SSE `eventId` (`evt_xxx`) per docs.elfa.ai/auto/notifications,
        so this is NOT used for fire dedupe.
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

        Fail-closed: any frame missing the canonical fields (`eventId`,
        `eventType`, `queryId` matching the requested query_id) or with
        unparsable JSON is logged and dropped. The caller never sees a
        malformed event and so cannot place a GRVT order on garbage.

        Canonical wire format (docs.elfa.ai/auto/notifications):
            event: query.triggered
            id: evt_01J...
            data: {"version":"1.0","eventType":"query.triggered","eventId":"evt_01J...","queryId":"q_123","channel":"sse","trigger":{...},"evaluation":{...},"action":{...}}

        Yields {"event_id": "<eventId>", "data": <parsed payload>}.

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
