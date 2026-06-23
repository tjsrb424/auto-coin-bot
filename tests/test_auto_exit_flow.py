from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app import database
from app.live_exit import LiveExitConfig, maybe_create_price_exit_candidate
from app.profit_engine import ProfitEngineConfig, evaluate_profit_entry_gate


class AutoExitFlowTests(unittest.TestCase):
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
                "strategy_name": "trend_pullback",
                "strategy_parameters": {},
                "status": "RUNNING",
                "auto_enabled": True,
                "initial_balance_krw": 0,
                "max_order_krw": 100_000,
                "max_orders_per_day": 0,
            }
        )

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.tmp.cleanup()

    def create_position(self, *, opened_minutes_ago: int = 10, highest: float | None = None) -> dict:
        entry_price = 100_000_000
        opened_at = (datetime.now(timezone.utc) - timedelta(minutes=opened_minutes_ago)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        position_id = database.create_live_position(
            {
                "session_id": self.session_id,
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 1,
                "strategy_name": "trend_pullback",
                "status": "OPEN",
                "entry_order_uuid": "entry-1",
                "entry_price": entry_price,
                "entry_volume": 0.001,
                "entry_amount_krw": 100_000,
                "current_price": entry_price,
                "unrealized_pnl": 0,
                "realized_pnl": 0,
                "stop_loss_price": 99_000_000,
                "take_profit_price": 110_000_000,
                "highest_price_since_entry": highest,
                "trailing_stop_pct": 1.0,
                "trailing_stop_price": (highest or entry_price) * 0.99,
                "opened_at": opened_at,
            }
        )
        position = database.load_live_position(position_id)
        assert position is not None
        return position

    def test_v1_exit_defaults_match_plan(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = LiveExitConfig.from_env()

        self.assertEqual(config.stop_loss_percent, 0.8)
        self.assertEqual(config.take_profit_percent, 1.2)
        self.assertEqual(config.max_hold_minutes, 90)
        self.assertEqual(config.cancel_exit_order_after_seconds, 45)
        self.assertEqual(config.max_exit_retry_count, 2)
        self.assertFalse(config.require_manual_confirm)

    def test_stop_loss_take_profit_max_hold_and_trailing_candidates(self) -> None:
        stop = maybe_create_price_exit_candidate(self.create_position(), 99_000_000, "2026-06-23T00:00:00Z")
        take = maybe_create_price_exit_candidate(self.create_position(), 110_100_000, "2026-06-23T00:01:00Z")
        hold = maybe_create_price_exit_candidate(self.create_position(opened_minutes_ago=91), 100_100_000, "2026-06-23T00:02:00Z")
        trailing = maybe_create_price_exit_candidate(self.create_position(highest=105_000_000), 103_900_000, "2026-06-23T00:03:00Z")

        self.assertEqual(stop["reason"], "STOP_LOSS")
        self.assertEqual(take["reason"], "TAKE_PROFIT")
        self.assertEqual(hold["reason"], "MAX_HOLD_TIME")
        self.assertEqual(trailing["reason"], "TRAILING_STOP")

    def test_profit_engine_blocks_new_entry_when_auto_exit_disabled(self) -> None:
        result = evaluate_profit_entry_gate(
            market_regime="TREND_UP",
            strategy_name="trend_pullback",
            side="BUY",
            auto_exit_enabled=False,
            config=ProfitEngineConfig(
                enabled=True,
                mode="aggressive",
                order_sizing_mode="available_balance_cap",
                require_auto_exit=True,
                block_entry_when_exit_disabled=True,
                allow_balance_cap=True,
                disable_percent_sizing=True,
                extra_fee_buffer_rate=0.0002,
            ),
        )

        self.assertFalse(result["entry_allowed"])
        self.assertEqual(result["block_code"], "BLOCKED_AUTO_EXIT_DISABLED")


if __name__ == "__main__":
    unittest.main()
