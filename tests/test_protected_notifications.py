from __future__ import annotations

import unittest

from app.protected_notifications import _channel_for_url, _webhook_payload


class ProtectedNotificationTests(unittest.TestCase):
    def test_discord_webhook_uses_discord_content_payload(self) -> None:
        channel = _channel_for_url("https://discord.com/api/webhooks/123/token")
        payload = _webhook_payload(channel, "protected alert")

        self.assertEqual(channel, "DISCORD")
        self.assertEqual(payload, {"content": "protected alert"})

    def test_discord_payload_is_limited_to_discord_content_max(self) -> None:
        payload = _webhook_payload("DISCORD", "x" * 2500)

        self.assertEqual(len(payload["content"]), 2000)


if __name__ == "__main__":
    unittest.main()
