from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import database
from app.live_smoke_test import CONFIRMATION_PHRASE, run_one_shot_live_smoke_test


class LiveSmokeTestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        database.init_db()
        database.update_bot_operation_policy(
            "KRW-BTC",
            {"auto_trading_enabled": False, "max_total_exposure_krw": 300_000, "daily_loss_limit_pct": 3},
        )
        with database.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO runtime_locks (
                    lock_id, instance_id, hostname, app_env, runtime_owner,
                    status, acquired_at, expires_at, updated_at
                ) VALUES ('auto-trading', 'test', 'test-host', 'test', 'test', 'STOPPED',
                    '2026-06-26T00:00:00Z', '2026-06-26T01:00:00Z', '2026-06-26T00:00:00Z')
                """
            )
        database.create_accounting_epoch(
            {
                "exchange_name": "bithumb",
                "epoch_id": "epoch-test",
                "epoch_started_at_utc": "2026-06-26T00:00:00Z",
                "starting_exchange_equity": 263_000,
                "starting_cash_krw": 263_000,
                "starting_positions": [],
                "starting_position_count": 0,
                "cost_basis_policy": "MARK_TO_MARKET",
                "epoch_trust_level": "MEDIUM",
                "legacy_history_isolated": True,
            }
        )

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.tmp.cleanup()

    async def test_confirmation_required_before_any_order(self) -> None:
        broker = AsyncMock()
        with patch("app.live_smoke_test.get_live_broker", return_value=broker):
            result = await run_one_shot_live_smoke_test(confirmation="NOPE")

        self.assertEqual(result["smoke_test_status"], "ABORTED")
        broker.place_order.assert_not_awaited()
        with database.get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) AS count FROM live_order_logs").fetchone()["count"]
        self.assertEqual(count, 0)

    async def test_disabled_smoke_test_does_not_place_order(self) -> None:
        broker = AsyncMock()
        with patch("app.live_smoke_test.get_live_broker", return_value=broker), patch("app.live_smoke_test._current_equity", return_value=263_000):
            result = await run_one_shot_live_smoke_test(confirmation=CONFIRMATION_PHRASE)

        self.assertEqual(result["smoke_test_status"], "ABORTED")
        self.assertIn("LIVE_SMOKE_TEST_DISABLED", result["pass_fail_reasons"])
        broker.place_order.assert_not_awaited()
        with database.get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) AS count FROM live_order_logs").fetchone()["count"]
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
