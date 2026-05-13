import responses

from elfa_grvt_bot.telegram_sender import TelegramSender


def test_send_success():
    sender = TelegramSender(bot_token="bot_test", chat_id="12345")
    with responses.RequestsMock() as rm:
        rm.post(
            "https://api.telegram.org/botbot_test/sendMessage",
            json={"ok": True},
            status=200,
        )
        ok = sender.send("hello")
        assert ok is True
        assert len(rm.calls) == 1
        # request body sent the chat_id and text
        import urllib.parse as up
        sent = up.parse_qs(rm.calls[0].request.body)
        assert sent["chat_id"] == ["12345"]
        assert sent["text"] == ["hello"]


def test_send_returns_false_on_http_error():
    sender = TelegramSender(bot_token="bot_test", chat_id="12345")
    with responses.RequestsMock() as rm:
        rm.post(
            "https://api.telegram.org/botbot_test/sendMessage",
            json={"ok": False},
            status=400,
        )
        ok = sender.send("hello")
        assert ok is False


def test_send_returns_false_on_network_error():
    sender = TelegramSender(bot_token="bot_test", chat_id="12345")
    with responses.RequestsMock() as rm:
        # no response registered -> ConnectionError
        ok = sender.send("hello")
        assert ok is False


def test_send_noops_when_token_missing():
    """Both creds blank in .env => sender is silently disabled. No HTTP call
    should happen; AlertWriter still records the alert to the registry."""
    sender = TelegramSender(bot_token="", chat_id="")
    assert sender.enabled is False
    with responses.RequestsMock():
        ok = sender.send("anything")
    assert ok is False


def test_send_noops_when_chat_id_missing():
    """Half-configured Telegram (token only, no chat id) is also a no-op.
    Avoids posting to a half-built URL and the resulting 4xx noise."""
    sender = TelegramSender(bot_token="bot_test", chat_id="")
    assert sender.enabled is False
    with responses.RequestsMock():
        ok = sender.send("anything")
    assert ok is False
