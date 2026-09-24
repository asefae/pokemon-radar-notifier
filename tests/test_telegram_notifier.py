import unittest
from unittest import mock

from telegram_notifier import TelegramNotifier


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


class TelegramNotifierTests(unittest.TestCase):
    @mock.patch("telegram_notifier.requests.post")
    def test_url_payload_and_success(self, post):
        post.return_value = FakeResponse(200, {"ok": True, "description": "OK"})
        result = TelegramNotifier(" 123:abc ", " -1001 ").send_message("hello")
        self.assertTrue(result.success)
        post.assert_called_once_with(
            "https://api.telegram.org/bot123:abc/sendMessage",
            json={"chat_id": "-1001", "text": "hello"},
            timeout=10.0,
        )

    def test_empty_token_and_chat_id(self):
        self.assertIn("Token", TelegramNotifier("", "1").send_message("x").description)
        self.assertIn("Chat ID", TelegramNotifier("token", "").send_message("x").description)

    @mock.patch("telegram_notifier.requests.post")
    def test_http_401(self, post):
        post.return_value = FakeResponse(401, {"ok": False, "description": "Unauthorized"})
        result = TelegramNotifier("token", "1").send_message("x")
        self.assertFalse(result.success)
        self.assertEqual(result.status_code, 401)
        self.assertFalse(result.ok)
        self.assertEqual(result.description, "Unauthorized")

    @mock.patch("telegram_notifier.requests.post")
    def test_invalid_chat_id(self, post):
        post.return_value = FakeResponse(400, {"ok": False, "description": "Bad Request: chat not found"})
        result = TelegramNotifier("token", "bad").send_message("x")
        self.assertFalse(result.success)
        self.assertIn("chat not found", result.description)

    @mock.patch("telegram_notifier.requests.post")
    def test_timeout(self, post):
        import requests
        post.side_effect = requests.Timeout("timed out")
        result = TelegramNotifier("token", "1").send_message("x")
        self.assertFalse(result.success)
        self.assertIn("Сетевая ошибка", result.description)

    @mock.patch("telegram_notifier.requests.post")
    def test_get_me_and_send_message(self, post):
        post.side_effect = [
            FakeResponse(200, {"ok": True, "description": "OK"}),
            FakeResponse(200, {"ok": True, "description": "OK"}),
        ]
        auth, message = TelegramNotifier("token", "1").diagnose()
        self.assertTrue(auth.success)
        self.assertIsNotNone(message)
        self.assertTrue(message.success)
        self.assertIn("/getMe", post.call_args_list[0].args[0])


if __name__ == "__main__":
    unittest.main()
