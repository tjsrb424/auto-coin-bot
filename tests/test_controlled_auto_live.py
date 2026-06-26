from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import database
from app.controlled_auto_live import CONFIRMATION_PHRASE, _ma_cross_decision, run_controlled_auto_live


class ControlledAutoLiveTests(unittest.IsolatedAsyncioTestCase):
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
                "epoch_id": "epoch-controlled",
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
        with patch("app.controlled_auto_live.get_live_broker", return_value=broker):
            result = await run_controlled_auto_live(confirmation="NOPE")

        self.assertEqual(result["controlled_auto_live_status"], "ABORTED")
        broker.place_order.assert_not_awaited()
        with database.get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) AS count FROM live_order_logs").fetchone()["count"]
        self.assertEqual(count, 0)

    def test_ma_cross_buy_below_expected_edge_is_blocked(self) -> None:
        candles = []
        prices = [100.0] * 21
        prices[-2] = 100.0
        prices[-1] = 100.05
        for index, price in enumerate(prices):
            candles.append(
                {
                    "candle_time_utc": f"2026-06-26T00:{index:02d}:00Z",
                    "opening_price": price,
                    "high_price": price,
                    "low_price": price,
                    "trade_price": price,
                    "candle_acc_trade_volume": 1,
                }
            )

        decision = _ma_cross_decision(
            "BTC",
            "KRW-BTC",
            candles,
            {"best_bid": 100.0, "best_ask": 100.05},
            6000,
        )

        if decision["signal"] == "BUY":
            self.assertFalse(decision["edge_allowed"])
            self.assertEqual(decision["blocker"], "BLOCKED_EXPECTED_EDGE_BELOW_COST")
        self.assertGreaterEqual(decision["min_expected_edge_rate"], 0.006)


if __name__ == "__main__":
    unittest.main()
