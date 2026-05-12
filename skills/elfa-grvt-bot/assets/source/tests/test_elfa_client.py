import pytest
import responses

from elfa_grvt_bot.elfa_client import ElfaClient


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
