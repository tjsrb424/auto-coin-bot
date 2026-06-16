from __future__ import annotations

import tempfile
import unittest
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import database
from app.live_exit import (
    approve_exit_candidate,
    create_exit_candidate_for_position,
    create_exit_order_preview,
    evaluate_exit_order,
    maybe_create_price_exit_candidate,
)


class LiveExitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        database.init_db()
        self.session_id = database.create_live_strategy_session(
            {
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 1,
                "strategy_name": "ma_cross",
                "strategy_parameters": {},
                "status": "RUNNING",
                "auto_enabled": True,
                "initial_balance_krw": 0,
                "max_order_krw": 10_000,
                "max_orders_per_day": 1,
            }
        )

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.tmp.cleanup()

    def create_position(self, *, entry_price: float = 100_000_000, opened_minutes_ago: int = 10, volume: float = 0.0001) -> dict:
        opened_at = (datetime.now(timezone.utc) - timedelta(minutes=opened_minutes_ago)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        position_id = database.create_live_position(
            {
                "session_id": self.session_id,
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 1,
                "strategy_name": "ma_cross",
                "status": "OPEN",
                "entry_order_uuid": "entry-1",
                "entry_price": entry_price,
                "entry_volume": volume,
                "entry_amount_krw": entry_price * volume,
                "current_price": entry_price,
                "unrealized_pnl": 0,
                "realized_pnl": 0,
                "stop_loss_price": entry_price * 0.993,
                "take_profit_price": entry_price * 1.01,
                "opened_at": opened_at,
            }
        )
        position = database.load_live_position(position_id)
        assert position is not None
        return position

    def test_stop_loss_exit_candidate_created_without_order(self) -> None:
        position = self.create_position()
        candidate = maybe_create_price_exit_candidate(position, 99_300_000, "2026-06-16T00:00:00Z")

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate["reason"], "STOP_LOSS")
        self.assertEqual(candidate["status"], "PENDING")
        self.assertFalse(database.has_open_exit_order(position["id"]))

    def test_take_profit_exit_candidate_created_without_order(self) -> None:
        position = self.create_position()
        candidate = maybe_create_price_exit_candidate(position, 101_000_000, "2026-06-16T00:00:00Z")

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate["reason"], "TAKE_PROFIT")
        self.assertFalse(database.has_open_exit_order(position["id"]))

    def test_max_hold_time_exit_candidate_created(self) -> None:
        position = self.create_position(opened_minutes_ago=61)
        candidate = maybe_create_price_exit_candidate(position, 100_100_000, "2026-06-16T00:00:00Z")

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate["reason"], "MAX_HOLD_TIME")

    async def test_auto_exit_disabled_blocks_auto_order(self) -> None:
        position = self.create_position()
        candidate = create_exit_candidate_for_position(position, "STRATEGY_SELL", 100_000_000)
        assert candidate is not None

        with patch.dict(os.environ, {"AUTO_EXIT_ENABLED": "false"}, clear=False):
            risk = await evaluate_exit_order(candidate, position, manual_confirmed=False, is_auto_exit=True)

        self.assertFalse(risk["allowed"])
        self.assertEqual(risk["risk_result"], "BLOCKED_EXIT_DISABLED")

    async def test_manual_exit_preview_records_exit_order_log(self) -> None:
        position = self.create_position()
        candidate = create_exit_candidate_for_position(position, "STRATEGY_SELL", 100_000_000)
        assert candidate is not None
        approve_exit_candidate(int(candidate["id"]))
        broker = AsyncMock()
        broker.get_balances.return_value = {
            "by_currency": {"BTC": {"balance": 0.0001, "locked": 0.0}},
            "btc": {"balance": 0.0001, "locked": 0.0},
            "krw": {"balance": 0.0, "locked": 0.0},
        }
        broker.get_order_chance.return_value = {"market": "KRW-BTC"}

        with patch("app.live_recovery.get_live_broker", return_value=broker), patch("app.live_exit.get_live_broker", return_value=broker):
            result = await create_exit_order_preview(int(candidate["id"]), manual_confirmed=True)

        self.assertTrue(result["ok"])
        log = database.get_live_order_log(result["request_id"])
        assert log is not None
        self.assertEqual(log["order_purpose"], "EXIT")
        self.assertEqual(log["side"], "SELL")
        self.assertEqual(log["exit_reason"], "STRATEGY_SELL")
        self.assertTrue(log["manual_confirmed"])


if __name__ == "__main__":
    unittest.main()
