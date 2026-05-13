import pytest
import responses

from elfa_grvt_bot.elfa_client import ElfaClient


class _FakeStreamResponse:
    """Minimal async-context-manager that mimics httpx.AsyncClient.stream() for
    the SSE parser. Yields the raw lines one by one through aiter_lines()."""

    def __init__(self, status_code: int, lines: list[str]):
        self.status_code = status_code
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self):
        return b""


class _FakeAsyncClient:
    """Single-shot httpx.AsyncClient stand-in used to drive the SSE parser
    deterministically without going over the network."""

    def __init__(self, response: _FakeStreamResponse):
        self._response = response
        self.closed = False

    def stream(self, method: str, url: str, headers=None):
        return self._response

    async def aclose(self):
        self.closed = True


def _client() -> ElfaClient:
    return ElfaClient(
        api_key="ek_test",
        base_url="https://api.elfa.ai",
        clock=lambda: 1700000000,
    )


def _assert_api_key_only(req) -> None:
    assert req.headers["x-elfa-api-key"] == "ek_test"
    lower = {k.lower() for k in req.headers}
    assert "x-elfa-signature" not in lower
    assert "x-elfa-timestamp" not in lower


def test_builder_chat_sends_api_key_only():
    with responses.RequestsMock() as rm:
        rm.post(
            "https://api.elfa.ai/v2/auto/chat",
            json={"draft": {"conditions": {}}},
            status=200,
        )
        out = _client().builder_chat(prompt="buy BTC when RSI < 30")
        assert out == {"draft": {"conditions": {}}}
        _assert_api_key_only(rm.calls[0].request)


def test_validate_query_sends_api_key_only():
    with responses.RequestsMock() as rm:
        rm.post(
            "https://api.elfa.ai/v2/auto/queries/validate",
            json={"valid": True, "wouldTriggerNow": False},
            status=200,
        )
        out = _client().validate_query({"conditions": {}})
        assert out["valid"] is True
        _assert_api_key_only(rm.calls[0].request)


def test_builder_chat_raises_on_4xx():
    with responses.RequestsMock() as rm:
        rm.post(
            "https://api.elfa.ai/v2/auto/chat",
            json={"error": "bad request"},
            status=400,
        )
        with pytest.raises(RuntimeError, match="elfa builder_chat failed: 400"):
            _client().builder_chat(prompt="x")


def test_create_query_sends_api_key_only():
    query = {
        "title": "BTC dip",
        "description": "buy BTC on RSI dip",
        "query": {"conditions": {}, "actions": [], "expiresIn": "24h"},
    }
    with responses.RequestsMock() as rm:
        rm.post(
            "https://api.elfa.ai/v2/auto/queries",
            json={"id": "q_abc", "status": "active"},
            status=201,
        )
        out = _client().create_query(query)
        assert out["id"] == "q_abc"
        _assert_api_key_only(rm.calls[0].request)


def test_cancel_query_posts_to_cancel_subpath():
    with responses.RequestsMock() as rm:
        rm.post(
            "https://api.elfa.ai/v2/auto/queries/q_abc/cancel",
            json={"id": "q_abc", "status": "cancelled"},
            status=200,
        )
        out = _client().cancel_query("q_abc")
        assert out["status"] == "cancelled"
        _assert_api_key_only(rm.calls[0].request)


# ---------------------------------------------------------------------------
# SSE notifications parser
# ---------------------------------------------------------------------------


async def _collect(aiter):
    out = []
    async for ev in aiter:
        out.append(ev)
    return out


async def test_stream_notifications_parses_canonical_query_triggered_frame():
    """Live SSE wire format per docs.elfa.ai auto/notifications:

        event: query.triggered
        id: evt_01J...
        data: {"version":"1.0","eventType":"query.triggered","eventId":"evt_01J...","timestamp":"...","queryId":"q_123","channel":"sse","trigger":{...},"evaluation":{...},"action":{...}}

    The parser must accept `event: query.triggered` and key the dedupe
    identifier on `data.eventId`.
    """
    lines = [
        "event: query.triggered",
        "id: evt_01J_demo",
        ('data: {"version":"1.0","eventType":"query.triggered",'
         '"eventId":"evt_01J_demo","timestamp":"2026-04-01T12:00:00.000Z",'
         '"queryId":"q_1","channel":"sse","trigger":{"symbol":"BTC"},'
         '"evaluation":{"triggered":true},"action":{"type":"notify"}}'),
        "",
        "event: end",
        "data: {}",
        "",
    ]
    fake = _FakeAsyncClient(_FakeStreamResponse(200, lines))
    events = await _collect(
        _client().stream_notifications("q_1", http_client=fake)
    )
    assert len(events) == 1
    assert events[0]["event_id"] == "evt_01J_demo"
    assert events[0]["data"]["eventId"] == "evt_01J_demo"
    assert events[0]["data"]["queryId"] == "q_1"


async def test_stream_notifications_falls_back_to_sse_id_line():
    """The published wire example has the SSE `id:` line and `data.eventId`
    set to the same value. We still consult both: if the JSON payload is
    malformed or missing eventId (defensive), the SSE protocol id is the
    fallback so we don't return event_id=None into the dedupe layer."""
    lines = [
        "event: query.triggered",
        "id: evt_fallback",
        'data: {"queryId":"q_2","channel":"sse"}',
        "",
    ]
    fake = _FakeAsyncClient(_FakeStreamResponse(200, lines))
    events = await _collect(
        _client().stream_notifications("q_2", http_client=fake)
    )
    assert len(events) == 1
    assert events[0]["event_id"] == "evt_fallback"


async def test_stream_notifications_accepts_legacy_notification_event_type():
    """Defensive backward-compat: if Elfa rolls back to `event: notification`,
    parse it the same way. Both event types are treated as triggers; the
    dedupe path is the same."""
    lines = [
        "event: notification",
        "id: evt_legacy",
        'data: {"eventId":"evt_legacy","queryId":"q_3"}',
        "",
    ]
    fake = _FakeAsyncClient(_FakeStreamResponse(200, lines))
    events = await _collect(
        _client().stream_notifications("q_3", http_client=fake)
    )
    assert len(events) == 1
    assert events[0]["event_id"] == "evt_legacy"


async def test_stream_notifications_410_yields_nothing():
    """Query was terminal on connect. Parser returns cleanly so the strategy
    loop hands off to poll-query for status reconciliation."""
    fake = _FakeAsyncClient(_FakeStreamResponse(410, []))
    events = await _collect(
        _client().stream_notifications("q_4", http_client=fake)
    )
    assert events == []


async def test_stream_notifications_non_trigger_events_are_skipped():
    """`event: end` and any unrecognized event types are no-ops; only
    trigger events (`query.triggered` or legacy `notification`) yield."""
    lines = [
        "event: end",
        "data: {}",
        "",
        "event: heartbeat",
        "data: {}",
        "",
    ]
    fake = _FakeAsyncClient(_FakeStreamResponse(200, lines))
    events = await _collect(
        _client().stream_notifications("q_5", http_client=fake)
    )
    assert events == []
