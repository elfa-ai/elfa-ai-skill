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
        # no response registered → ConnectionError
        ok = sender.send("hello")
        assert ok is False
