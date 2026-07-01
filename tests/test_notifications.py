from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import database
from app.discord_notifier import build_discord_embed
from app.main import app
from app.notifications import notification_config_status, send_notification


class NotificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        database.init_db()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_webhook_missing_records_skipped_without_url(self) -> None:
        with patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": "", "PROTECTED_AUTO_DISCORD_WEBHOOK_URL": ""}, clear=False):
            result = send_notification("PROTECTED_AUTO_STARTED", {"protected_session_id": "session-1"})

        self.assertEqual(result["status"], "SKIPPED")
        self.assertEqual(result["error_message"], "DISCORD_WEBHOOK_URL_NOT_CONFIGURED")
        self.assertNotIn("discord.com/api/webhooks", str(result))

    def test_webhook_failure_records_failed_log(self) -> None:
        with (
            patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/token"}, clear=False),
            patch("app.notifications.send_discord_embed", return_value={"ok": False, "status": "FAILED", "error_message": "HTTPError:403"}),
        ):
            result = send_notification("ACCOUNTING_ERROR", {"protected_session_id": "session-1"})

        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["error_message"], "HTTPError:403")
        self.assertEqual(database.load_notification_logs()[0]["event_type"], "ACCOUNTING_ERROR")

    def test_success_records_sent_log(self) -> None:
        with (
            patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/token"}, clear=False),
            patch("app.notifications.send_discord_embed", return_value={"ok": True, "status": "SENT", "error_message": ""}),
        ):
            result = send_notification("DAILY_SUMMARY", {"protected_session_id": "session-1"})

        self.assertEqual(result["status"], "SENT")
        self.assertTrue(result["sent_at_utc"])

    def test_embed_builders_for_core_events(self) -> None:
        for event_type in ("PROTECTED_AUTO_STARTED", "TRADE_OPENED", "TRADE_CLOSED", "PROTECTED_AUTO_STOPPED", "DAILY_SUMMARY"):
            embed = build_discord_embed(
                event_type,
                {
                    "protected_session_id": "session-1",
                    "symbol": "BTC",
                    "protected_strategy_pnl": 1234,
                    "session_loss_remaining": 1000,
                    "status": "RUNNING",
                },
            )
            self.assertTrue(embed["title"])
            self.assertLessEqual(len(embed["fields"]), 10)
            self.assertEqual(embed["footer"]["text"], "auto-coin-bot · PROTECTED_FULL_AUTO_LIVE_V1")

    def test_config_masks_webhook_url(self) -> None:
        url = "https://discord.com/api/webhooks/123/token"
        with patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": url}, clear=False):
            config = notification_config_status()

        self.assertEqual(config["webhook_url"], "설정됨")
        self.assertNotIn(url, str(config))

    def test_admin_test_discord_endpoint(self) -> None:
        with (
            patch.dict("os.environ", {"APP_ENV": "development", "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/token"}, clear=False),
            patch("app.notifications.send_discord_embed", return_value={"ok": True, "status": "SENT", "error_message": ""}),
        ):
            response = TestClient(app).post("/api/notifications/test-discord", json={})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["notification"]["status"], "SENT")


if __name__ == "__main__":
    unittest.main()
