from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import database
from app.discord_notifier import build_discord_embed
from app.main import app
from app.notifications import drain_notification_queue_for_tests, notification_config_status, send_notification


class NotificationTests(unittest.TestCase):
    def setUp(self) -> None:
        drain_notification_queue_for_tests(timeout_seconds=1.0)
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
        self.assertEqual(result["dedupe_status"], "FAILED")
        self.assertEqual(result["error_message"], "HTTPError:403")
        self.assertEqual(database.load_notification_logs()[0]["event_type"], "ACCOUNTING_ERROR")

    def test_success_records_sent_log(self) -> None:
        with (
            patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/token"}, clear=False),
            patch("app.notifications.send_discord_embed", return_value={"ok": True, "status": "SENT", "error_message": ""}),
        ):
            result = send_notification("DAILY_SUMMARY", {"date_kst": "2026-07-01"})

        self.assertEqual(result["status"], "SENT")
        self.assertEqual(result["dedupe_status"], "SENT")
        self.assertTrue(result["sent_at_utc"])

    def test_started_is_sent_once_per_session(self) -> None:
        with (
            patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/token"}, clear=False),
            patch("app.notifications.send_discord_embed", return_value={"ok": True, "status": "SENT", "error_message": ""}) as send_mock,
        ):
            first = send_notification("PROTECTED_AUTO_STARTED", {"protected_session_id": "session-1"})
            second = send_notification("PROTECTED_AUTO_STARTED", {"protected_session_id": "session-1"})

        self.assertEqual(first["status"], "SENT")
        self.assertEqual(second["status"], "SKIPPED_DUPLICATE")
        self.assertEqual(send_mock.call_count, 1)
        logs = database.load_notification_logs()
        self.assertEqual(logs[0]["dedupe_status"], "SKIPPED_DUPLICATE")
        self.assertEqual(logs[1]["event_dedupe_key"], "PROTECTED_AUTO_STARTED:session-1")

    def test_stopped_is_sent_once_per_stop_reason(self) -> None:
        with (
            patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/token"}, clear=False),
            patch("app.notifications.send_discord_embed", return_value={"ok": True, "status": "SENT", "error_message": ""}) as send_mock,
        ):
            send_notification("PROTECTED_AUTO_STOPPED", {"protected_session_id": "session-1", "reason": "A"})
            duplicate = send_notification("PROTECTED_AUTO_STOPPED", {"protected_session_id": "session-1", "reason": "A"})
            distinct = send_notification("PROTECTED_AUTO_STOPPED", {"protected_session_id": "session-1", "reason": "B"})

        self.assertEqual(duplicate["status"], "SKIPPED_DUPLICATE")
        self.assertEqual(distinct["status"], "SENT")
        self.assertEqual(send_mock.call_count, 2)

    def test_trade_opened_and_closed_are_sent_once_per_order(self) -> None:
        with (
            patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/token"}, clear=False),
            patch("app.notifications.send_discord_embed", return_value={"ok": True, "status": "SENT", "error_message": ""}) as send_mock,
        ):
            send_notification("TRADE_OPENED", {"exchange_order_uuid": "order-1"})
            duplicate_open = send_notification("TRADE_OPENED", {"exchange_order_uuid": "order-1"})
            send_notification("TRADE_CLOSED", {"exit_order_uuid": "exit-1"})
            duplicate_close = send_notification("TRADE_CLOSED", {"exit_order_uuid": "exit-1"})

        self.assertEqual(duplicate_open["status"], "SKIPPED_DUPLICATE")
        self.assertEqual(duplicate_close["status"], "SKIPPED_DUPLICATE")
        self.assertEqual(send_mock.call_count, 2)

    def test_stale_and_accounting_errors_are_rate_limited(self) -> None:
        with (
            patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/token"}, clear=False),
            patch("app.notifications.send_discord_embed", return_value={"ok": True, "status": "SENT", "error_message": ""}) as send_mock,
        ):
            first_stale = send_notification("PROTECTED_AUTO_STALE", {"protected_session_id": "session-1"})
            second_stale = send_notification("PROTECTED_AUTO_STALE", {"protected_session_id": "session-1"})
            first_error = send_notification("ACCOUNTING_ERROR", {"protected_session_id": "session-1", "error_type": "MISSING"})
            second_error = send_notification("ACCOUNTING_ERROR", {"protected_session_id": "session-1", "error_type": "MISSING"})

        self.assertEqual(first_stale["status"], "SENT")
        self.assertEqual(second_stale["status"], "RATE_LIMITED")
        self.assertEqual(first_error["status"], "SENT")
        self.assertEqual(second_error["status"], "RATE_LIMITED")
        self.assertEqual(send_mock.call_count, 2)

    def test_daily_summary_is_sent_once_per_kst_date(self) -> None:
        with (
            patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/token"}, clear=False),
            patch("app.notifications.send_discord_embed", return_value={"ok": True, "status": "SENT", "error_message": ""}) as send_mock,
        ):
            first = send_notification("DAILY_SUMMARY", {"date_kst": "2026-07-01"})
            second = send_notification("DAILY_SUMMARY", {"date_kst": "2026-07-01"})

        self.assertEqual(first["status"], "SENT")
        self.assertEqual(second["status"], "SKIPPED_DUPLICATE")
        self.assertEqual(send_mock.call_count, 1)

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
            self.assertEqual(embed["footer"]["text"], "auto-coin-bot - PROTECTED_FULL_AUTO_LIVE_V1")

    def test_config_masks_webhook_url(self) -> None:
        url = "https://discord.com/api/webhooks/123/token"
        with patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": url}, clear=False):
            config = notification_config_status()

        self.assertEqual(config["webhook_url"], "configured")
        self.assertNotIn(url, str(config))
        self.assertIn("stats", config)

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
