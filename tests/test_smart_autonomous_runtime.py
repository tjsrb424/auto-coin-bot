from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import database
from app.live_strategy_pilot import _process_session, start_live_strategy_pilot


def candle() -> dict:
    return {
        "market": "KRW-BTC",
        "unit": 5,
        "candle_time_utc": "2026-06-19T03:00:00Z",
        "trade_price": 100_000_000,
        "opening_price": 100_000_000,
        "high_price": 100_000_000,
        "low_price": 100_000_000,
        "candle_acc_trade_volume": 1.0,
        "candle_acc_trade_price": 100_000_000,
    }


class SmartAutonomousRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        database.init_db()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_start_runtime_without_candidate_uses_smart_autonomous_session(self) -> None:
        database.update_bot_operation_policy("KRW-BTC", {"auto_trading_enabled": True})
        with (
            patch.dict(
                "os.environ",
                {
                    "APP_ENV": "production",
                    "LIVE_AUTO_TRADING_ENABLED": "true",
                    "SMART_AUTONOMOUS_TRADING_ENABLED": "true",
                    "SMART_ENGINE_LIVE_MODE": "live",
                },
                clear=False,
            ),
            patch("app.live_strategy_pilot.run_live_strategy_tick"),
        ):
            result = start_live_strategy_pilot(
                confirmation="AUTO STRATEGY ENABLE",
                order_confirmation="PLACE AUTO LIVE ORDER",
            )

        self.assertTrue(result["ok"])
        session = database.load_latest_live_strategy_session()
        assert session is not None
        self.assertEqual(session["candidate_strategy_id"], 0)
        self.assertEqual(session["strategy_name"], "smart_autonomous")

    async def test_smart_live_mode_does_not_route_legacy_buy_to_entry_submit(self) -> None:
        candidate_id = database.save_candidate_strategy(
            {
                "strategy": "rsi",
                "parameters": {"period": 14, "oversold": 30, "overbought": 70},
                "unit": 5,
                "market": "KRW-BTC",
                "backtest_period": "30d",
                "score": 1,
            }
        )
        session_id = database.create_live_strategy_session(
            {
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": candidate_id,
                "strategy_name": "rsi",
                "strategy_parameters": {},
                "status": "READY",
                "auto_enabled": True,
                "initial_balance_krw": 0.0,
                "max_order_krw": 30_000,
                "max_orders_per_day": 0,
            }
        )
        session = database.load_latest_live_strategy_session()
        assert session is not None
        latest = candle()
        submit_smart = AsyncMock(return_value=True)
        submit_entry = AsyncMock()

        with (
            patch.dict(
                "os.environ",
                {
                    "APP_ENV": "production",
                    "LIVE_AUTO_TRADING_ENABLED": "true",
                    "AUTO_STRATEGY_PILOT_ENABLED": "true",
                    "SMART_ENGINE_LIVE_MODE": "live",
                },
                clear=False,
            ),
            patch("app.live_strategy_pilot._precheck_block_reason", new=AsyncMock(return_value=None)),
            patch("app.live_strategy_pilot.fetch_minute_candles", new=AsyncMock(return_value=[latest])),
            patch("app.live_strategy_pilot.insert_candles"),
            patch("app.live_strategy_pilot.load_candles", return_value=[latest]),
            patch("app.live_strategy_pilot._latest_signal", return_value={"signal": "BUY", "reason": "legacy buy"}),
            patch("app.live_strategy_pilot._record_smart_decision", new=AsyncMock(return_value={"order_intents": [{"side": "BID", "delta_value_krw": 20_000}]})),
            patch("app.live_strategy_pilot._submit_smart_intent_order", new=submit_smart),
            patch("app.live_strategy_pilot._submit_entry_order", new=submit_entry),
        ):
            await _process_session({**session, "id": session_id})

        submit_smart.assert_awaited_once()
        submit_entry.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
