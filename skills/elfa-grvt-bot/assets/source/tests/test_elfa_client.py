import json

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
        "query": {
            "conditions": {},
            "actions": [{"stepId": "step_1", "type": "notify", "params": {}}],
            "expiresIn": "24h",
        },
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


def test_create_query_rejects_trade_action_before_http():
    action = {"stepId": "step_1", "type": "market_order"}
    body = {
        "title": "BTC trade",
        "description": "bad action",
        "query": {"conditions": {}, "actions": [action]},
    }
    with pytest.raises(ValueError, match="notify-style actions only"):
        _client().create_query(body)


@pytest.mark.parametrize(
    "action",
    [
        {"stepId": "step_1", "type": "telegram_bot", "params": {}},
        {"stepId": "step_1", "type": "webhook", "params": {}},
        {
            "stepId": "step_1",
            "type": "llm",
            "params": {"callback": {"action": {"type": "notify"}}},
        },
    ],
)
def test_create_query_allows_notify_style_actions(action):
    body = {
        "title": "BTC alert",
        "description": "notify action",
        "query": {
            "conditions": {},
            "actions": [action],
        },
    }
    with responses.RequestsMock() as rm:
        rm.post(
            "https://api.elfa.ai/v2/auto/queries",
            json={"queryId": "q_ok", "status": "active", "cost": {}},
            status=201,
        )
        out = _client().create_query(body)
        assert out["queryId"] == "q_ok"


def test_create_query_rejects_llm_trade_callback_before_http():
    body = {
        "title": "BTC trade",
        "description": "bad callback",
        "query": {
            "conditions": {},
            "actions": [{
                "stepId": "step_1",
                "type": "llm",
                "params": {"callback": {"action": {"type": "limit_order"}}},
            }],
        },
    }
    with pytest.raises(ValueError, match="notify-style actions only"):
        _client().create_query(body)


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


def _event_json(query_id="q_1", execution_id="exec_1", **overrides):
    """Production schema captured 2026-05-13 against api.elfa.ai."""
    payload = {
        "status": "triggered",
        "queryId": query_id,
        "executionId": execution_id,
        "triggerTime": "2026-05-13T06:53:25.405Z",
        "timestamp": 1778655205405,
        "title": "Auto Plan Alert",
        "body": "test body",
        "message": "test message",
        "conditionsMet": 1,
    }
    payload.update(overrides)
    return json.dumps(payload, separators=(",", ":"))


async def test_stream_notifications_parses_production_notification_frame():
    """Live SSE wire format (captured 2026-05-13 against api.elfa.ai):

        event: notification
        id: <sse-level uuid>
        data: {"status":"triggered","queryId":"<uuid>","executionId":"<uuid>","triggerTime":"...","timestamp":..., ...}

    The parser must accept `event: notification` and key dedupe on
    `data.executionId` (matches poll-query's executions[i].id).
    """
    lines = [
        "event: notification",
        "id: 1b52d892-ba80-42d8-b421-36586410f23b",
        f'data: {_event_json("q_1", "exec_demo")}',
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
    assert events[0]["event_id"] == "exec_demo"
    assert events[0]["data"]["executionId"] == "exec_demo"
    assert events[0]["data"]["queryId"] == "q_1"


async def test_stream_notifications_accepts_query_triggered_event_type():
    """Backward-compat: if Elfa emits the old `event: query.triggered`
    name, accept it the same way as `notification`."""
    lines = [
        "event: query.triggered",
        "id: sse_id_1",
        f'data: {_event_json("q_3", "exec_compat")}',
        "",
    ]
    fake = _FakeAsyncClient(_FakeStreamResponse(200, lines))
    events = await _collect(
        _client().stream_notifications("q_3", http_client=fake)
    )
    assert len(events) == 1
    assert events[0]["event_id"] == "exec_compat"


async def test_stream_notifications_drops_frame_without_status_triggered():
    """Only `status: triggered` frames are fires. Anything else (the
    only documented values are documented values for the action result,
    but we treat non-`triggered` as a no-op for safety)."""
    lines = [
        "event: notification",
        "id: sse_id",
        f'data: {_event_json("q_x", "exec_x", status="pending")}',
        "",
    ]
    fake = _FakeAsyncClient(_FakeStreamResponse(200, lines))
    events = await _collect(
        _client().stream_notifications("q_x", http_client=fake)
    )
    assert events == []


async def test_stream_notifications_drops_frame_without_execution_id():
    """executionId is the canonical dedupe key. Without it, downstream
    has nothing to key idempotency on, so drop rather than yield."""
    lines = [
        "event: notification",
        "id: sse_id",
        f'data: {_event_json("q_noid", "")}',
        "",
    ]
    fake = _FakeAsyncClient(_FakeStreamResponse(200, lines))
    events = await _collect(
        _client().stream_notifications("q_noid", http_client=fake)
    )
    assert events == []


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


async def test_stream_notifications_drops_frame_when_queryId_mismatches():
    """If a payload claims a queryId different from the one we're streaming
    for, drop it. Defends against any (hypothetical) cross-stream mixup."""
    lines = [
        "event: notification",
        "id: sse_id",
        f'data: {_event_json("q_OTHER", "exec_mismatch")}',
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
        "event: notification",
        "id: sse_id",
    ]
    lines.extend(f"data: {line}" for line in json.dumps(
        json.loads(_event_json("q_ml", "exec_multi")), indent=2,
    ).splitlines())
    lines.append("")
    fake = _FakeAsyncClient(_FakeStreamResponse(200, lines))
    events = await _collect(
        _client().stream_notifications("q_ml", http_client=fake)
    )
    assert len(events) == 1
    assert events[0]["event_id"] == "exec_multi"
    assert events[0]["data"]["queryId"] == "q_ml"


async def test_stream_notifications_skips_keep_alive_comment_lines():
    """SSE servers send `:` keep-alive lines to hold the connection open.
    Parser must skip them, not interpret as field lines."""
    lines = [
        ": ping",
        ": another keep-alive",
        "event: notification",
        "id: sse_id",
        f'data: {_event_json("q_ka", "exec_ka")}',
        "",
        ": ping",
    ]
    fake = _FakeAsyncClient(_FakeStreamResponse(200, lines))
    events = await _collect(
        _client().stream_notifications("q_ka", http_client=fake)
    )
    assert len(events) == 1
    assert events[0]["event_id"] == "exec_ka"


async def test_stream_notifications_drops_frame_missing_required_fields():
    lines = [
        "event: notification",
        "id: sse_id",
        'data: {"status":"triggered","queryId":"q_missing"}',
        "",
    ]
    fake = _FakeAsyncClient(_FakeStreamResponse(200, lines))
    events = await _collect(
        _client().stream_notifications("q_missing", http_client=fake)
    )
    assert events == []


async def test_stream_notifications_parses_captured_production_frame():
    """Replay of the exact bytes captured from api.elfa.ai 2026-05-13.
    Locks the parser to the real production payload, not a synthesized one."""
    captured_data = (
        '{"status":"triggered","title":"Auto Plan Alert",'
        '"body":"probe fire","message":"probe fire",'
        '"queryId":"25bd0932-be09-4e80-913f-efcfa1567d22",'
        '"queryTitle":"SSE capture probe v2",'
        '"autoDetails":"Debug capture of raw SSE frames; do not register locally.",'
        '"executionId":"94631fa0-05db-482a-9040-cfbaf13ece71",'
        '"triggerTime":"2026-05-13T06:53:25.405Z",'
        '"queryIdShort":"25bd0932","conditionsMet":1,'
        '"queryDisplayTitle":"SSE capture probe v2 [25bd0932]",'
        '"timestamp":1778655205405}'
    )
    lines = [
        "id: 1b52d892-ba80-42d8-b421-36586410f23b",
        "event: notification",
        f"data: {captured_data}",
        "",
    ]
    fake = _FakeAsyncClient(_FakeStreamResponse(200, lines))
    events = await _collect(
        _client().stream_notifications(
            "25bd0932-be09-4e80-913f-efcfa1567d22", http_client=fake,
        )
    )
    assert len(events) == 1
    assert events[0]["event_id"] == "94631fa0-05db-482a-9040-cfbaf13ece71"
    assert events[0]["data"]["queryId"] == "25bd0932-be09-4e80-913f-efcfa1567d22"
    assert events[0]["data"]["conditionsMet"] == 1
