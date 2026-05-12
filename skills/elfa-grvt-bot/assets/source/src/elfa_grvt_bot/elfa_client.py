from __future__ import annotations

import json
import time
from typing import Callable, Optional

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

    def _delete(self, full_path: str, *, op: str) -> dict:
        url = f"{self.base_url}{full_path}"
        resp = requests.delete(
            url,
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
