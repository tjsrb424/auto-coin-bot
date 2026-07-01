from __future__ import annotations

import unittest
from unittest.mock import patch

from app.protected_notifications import _channel_for_url, _post_webhook, _webhook_payload


class ProtectedNotificationTests(unittest.TestCase):
    def test_discord_webhook_uses_discord_content_payload(self) -> None:
        channel = _channel_for_url("https://discord.com/api/webhooks/123/token")
        payload = _webhook_payload(channel, "protected alert")

        self.assertEqual(channel, "DISCORD")
        self.assertEqual(payload, {"content": "protected alert"})

    def test_discord_payload_is_limited_to_discord_content_max(self) -> None:
        payload = _webhook_payload("DISCORD", "x" * 2500)

        self.assertEqual(len(payload["content"]), 2000)

    def test_webhook_post_sets_user_agent(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b""

        with patch("app.protected_notifications.urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
            _post_webhook("https://discord.com/api/webhooks/123/token", "DISCORD", "protected alert")

        request = urlopen.call_args.args[0]
        self.assertEqual(request.headers["User-agent"], "coin-bot-protected-auto/1.0")


if __name__ == "__main__":
    unittest.main()
