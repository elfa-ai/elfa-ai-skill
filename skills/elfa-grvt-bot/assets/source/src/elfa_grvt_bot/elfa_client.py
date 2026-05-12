from __future__ import annotations

import json
import time
from typing import AsyncIterator, Callable, Optional

import httpx
import requests


class ElfaClient:
    """Thin client over /v2/auto/* endpoints. All routes accept API-key auth."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.elfa.ai",
        clock: Callable[[], int] = lambda: int(time.time()),
        timeout: float = 10.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.clock = clock
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
        """Fetch query state including the `executions` array.

        Used by the SSE consumer to backfill missed fires after a disconnect:
        if `status` is terminal (`triggered`, `expired`, `cancelled`), each
        entry in `executions` is a fire we may or may not have processed
        locally, keyed by `executions[i].id` (which matches the SSE
        `notification` event id).
        """
        return self._get(f"/v2/auto/queries/{query_id}", op="get_query")

    def get_execution(self, execution_id: str) -> dict:
        return self._get(
            f"/v2/auto/executions/{execution_id}", op="get_execution"
        )

    async def stream_query(
        self, query_id: str, *, http_client: Optional[httpx.AsyncClient] = None
    ) -> AsyncIterator[dict]:
        """Yield SSE `notification` events for one query until the stream closes.

        The stream emits at most one `notification` event (the trigger), then
        an `event: end` and the connection closes. A 410 response means the
        query was already in a terminal state on connect - yields nothing and
        returns.

        Yields dicts of shape:
            {"event_id": "<uuid>", "data": {<parsed fire payload>}}

        Caller is responsible for the lifecycle: on stream exit it should
        call `get_query()` to backfill in case the fire arrived during a
        reconnect gap.
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
                if r.status_code == 410:
                    return
                if r.status_code != 200:
                    body = (await r.aread()).decode(errors="replace")[:300]
                    raise RuntimeError(
                        f"elfa stream_query failed: {r.status_code} {body}"
                    )
                current: dict = {}
                async for line in r.aiter_lines():
                    if line.startswith(":"):
                        continue  # keep-alive comment
                    if line == "":
                        if current.get("event") == "notification" and "data" in current:
                            try:
                                payload = json.loads(current["data"])
                            except json.JSONDecodeError:
                                payload = {"raw": current["data"]}
                            yield {
                                "event_id": current.get("id"),
                                "data": payload,
                            }
                        # An `event: end` (or anything else) ends the stream
                        # naturally; the iterator exits when the server closes.
                        current = {}
                        continue
                    if ":" not in line:
                        continue
                    field, _, value = line.partition(":")
                    current[field.strip()] = value.lstrip()
        finally:
            if owns_client:
                await client.aclose()
