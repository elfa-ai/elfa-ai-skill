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
    return ElfaClient(api_key="ek_test", base_url="https://api.elfa.ai")


def _assert_api_key_only(req) -> None:
    assert req.headers["x-elfa-api-key"] == "ek_test"
    lower = {k.lower() for k in req.headers}
    assert "x-elfa-signature" not in lower
    assert "x-elfa-timestamp" not in lower


def test_builder_chat_sends_api_key_only():
    """Canonical response shape per docs.elfa.ai/api/rest/auto-chat-v-2:
    {sessionId, response (markdown), title, reasoning, planIds}."""
    canonical = {
        "sessionId": "ses_abc",
        "response": "I can help.\n\n```json\n{\"conditions\":{}}\n```\n",
        "title": "BTC RSI dip",
        "reasoning": None,
        "planIds": [],
    }
    with responses.RequestsMock() as rm:
        rm.post(
            "https://api.elfa.ai/v2/auto/chat", json=canonical, status=200,
        )
        out = _client().builder_chat(prompt="buy BTC when RSI < 30")
        assert out == canonical
        _assert_api_key_only(rm.calls[0].request)


def test_validate_query_sends_api_key_only():
    """Canonical validate response per docs.elfa.ai/api/rest/auto-validate-query-v-2:
    {valid, errors, warnings, estimatedCost, simulationLlmCallsEstimate}.
    No wouldTriggerNow on validate (that field is on poll-query's
    latestEvaluation)."""
    canonical = {
        "valid": True,
        "errors": [],
        "warnings": [],
        "estimatedCost": {"credits": 30, "price": "$0.270"},
    }
    with responses.RequestsMock() as rm:
        rm.post(
            "https://api.elfa.ai/v2/auto/queries/validate",
            json=canonical, status=200,
        )
        out = _client().validate_query({"conditions": {}})
        assert out["valid"] is True
        assert "wouldTriggerNow" not in out
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
    """Canonical create response: {queryId, status, cost} per
    docs.elfa.ai/api/rest/auto-create-query-v-2."""
    body = {
        "title": "BTC dip",
        "description": "buy BTC on RSI dip",
        "query": {"conditions": {}, "actions": [], "expiresIn": "24h"},
    }
    canonical = {
        "queryId": "q_abc",
        "status": "active",
        "cost": {"credits": 116, "price": "$1.045"},
    }
    with responses.RequestsMock() as rm:
        rm.post(
            "https://api.elfa.ai/v2/auto/queries", json=canonical, status=201,
        )
        out = _client().create_query(body)
        assert out["queryId"] == "q_abc"
        _assert_api_key_only(rm.calls[0].request)


def test_cancel_query_posts_to_cancel_subpath():
    with responses.RequestsMock() as rm:
        rm.post(
            "https://api.elfa.ai/v2/auto/queries/q_abc/cancel",
            json={"queryId": "q_abc", "status": "cancelled"},
            status=200,
        )
        out = _client().cancel_query("q_abc")
        assert out["status"] == "cancelled"
        _assert_api_key_only(rm.calls[0].request)


def test_get_query_returns_poll_shape():
    """Poll response per docs.elfa.ai/api/rest/auto-poll-query-v-2.
    `executions[i].id` is `exec_xxx` (different namespace from SSE eventId).
    """
    canonical = {
        "queryId": "q_abc",
        "status": "triggered",
        "latestEvaluation": {
            "evaluatedAt": "2026-04-01T12:00:00.000Z",
            "wouldTriggerNow": False,
        },
        "executions": [
            {
                "id": "exec_123", "queryId": "q_abc",
                "type": "notify", "status": "success",
                "createdAt": "2026-04-01T12:00:01.000Z",
            },
        ],
    }
    with responses.RequestsMock() as rm:
        rm.get(
            "https://api.elfa.ai/v2/auto/queries/q_abc",
            json=canonical, status=200,
        )
        out = _client().get_query("q_abc")
        assert out["queryId"] == "q_abc"
        assert out["status"] == "triggered"
        assert out["executions"][0]["id"].startswith("exec_")


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


async def test_stream_notifications_204_yields_nothing():
    """204 No Content is a documented response (auto-stream-query-v-2).
    Treat as empty stream, not as an error."""
    fake = _FakeAsyncClient(_FakeStreamResponse(204, []))
    events = await _collect(
        _client().stream_notifications("q_x", http_client=fake)
    )
    assert events == []


async def test_stream_notifications_500_raises_streamerror_with_status():
    """Non-200/204/410 must raise ElfaStreamError carrying the status code
    so the caller can branch on transient vs terminal."""
    from elfa_grvt_bot.elfa_client import ElfaStreamError
    fake = _FakeAsyncClient(_FakeStreamResponse(500, []))
    with pytest.raises(ElfaStreamError) as info:
        await _collect(
            _client().stream_notifications("q_y", http_client=fake)
        )
    assert info.value.status_code == 500


async def test_stream_notifications_drops_frame_with_unparsable_json():
    """Fail-closed: malformed JSON in `data:` must NOT yield an event with
    a half-built payload (otherwise the downstream order placement runs
    with garbage)."""
    lines = [
        "event: query.triggered",
        "id: evt_bad",
        "data: not_json_at_all",
        "",
    ]
    fake = _FakeAsyncClient(_FakeStreamResponse(200, lines))
    events = await _collect(
        _client().stream_notifications("q_bad", http_client=fake)
    )
    assert events == []


async def test_stream_notifications_drops_frame_without_eventId_or_sse_id():
    """Without either an eventId or an SSE `id:` line we have no dedupe
    key; the parser must drop the frame rather than yield event_id=None
    (which would collide on the fires PK)."""
    lines = [
        "event: query.triggered",
        'data: {"queryId":"q_noid","channel":"sse"}',
        "",
    ]
    fake = _FakeAsyncClient(_FakeStreamResponse(200, lines))
    events = await _collect(
        _client().stream_notifications("q_noid", http_client=fake)
    )
    assert events == []


async def test_stream_notifications_drops_frame_when_queryId_mismatches():
    """If a payload claims a queryId different from the one we're streaming
    for, drop it. Defends against any (hypothetical) cross-stream mixup."""
    lines = [
        "event: query.triggered",
        "id: evt_wrong_query",
        'data: {"eventId":"evt_wrong_query","queryId":"q_OTHER"}',
        "",
    ]
    fake = _FakeAsyncClient(_FakeStreamResponse(200, lines))
    events = await _collect(
        _client().stream_notifications("q_REQUESTED", http_client=fake)
    )
    assert events == []


async def test_stream_notifications_concatenates_multiline_data():
    """SSE spec: multiple `data:` lines in a frame join with `\\n`.
    Without accumulation, multi-line JSON payloads would be silently dropped."""
    lines = [
        "event: query.triggered",
        "id: evt_multi",
        'data: {"eventId":"evt_multi",',
        'data: "queryId":"q_ml",',
        'data: "channel":"sse"}',
        "",
    ]
    fake = _FakeAsyncClient(_FakeStreamResponse(200, lines))
    events = await _collect(
        _client().stream_notifications("q_ml", http_client=fake)
    )
    assert len(events) == 1
    assert events[0]["event_id"] == "evt_multi"
    assert events[0]["data"]["queryId"] == "q_ml"


async def test_stream_notifications_skips_keep_alive_comment_lines():
    """SSE servers send `:` keep-alive lines to hold the connection open.
    Parser must skip them, not interpret as field lines."""
    lines = [
        ": ping",
        ": another keep-alive",
        "event: query.triggered",
        "id: evt_ka",
        'data: {"eventId":"evt_ka","queryId":"q_ka"}',
        "",
        ": ping",
    ]
    fake = _FakeAsyncClient(_FakeStreamResponse(200, lines))
    events = await _collect(
        _client().stream_notifications("q_ka", http_client=fake)
    )
    assert len(events) == 1
    assert events[0]["event_id"] == "evt_ka"
