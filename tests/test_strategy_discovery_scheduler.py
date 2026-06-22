from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database
from app.strategy_discovery_scheduler import (
    discovery_scheduler_status,
    run_fast_validation_scheduler_once,
    run_promotion_selector_scheduler_once,
)


def seed_market(market: str = "KRW-ETH") -> None:
    database.upsert_market_universe(
        [
            {
                "exchange": "bithumb",
                "market": market,
                "symbol": market.split("-")[-1],
                "quote_currency": "KRW",
                "status": "DISCOVERED",
                "is_enabled": True,
                "is_live_allowed": True,
                "is_auto_selectable": True,
                "scan_rank": 1,
                "score": 90,
                "reason": "test",
                "last_24h_trade_price_krw": 1_000_000_000,
            }
        ]
    )


def validation_result(market: str = "KRW-ETH", score: float = 82.0) -> dict:
    return {
        "strategy": "ma_cross",
        "periods": [{"label": "7d"}],
        "parameter_count": 1,
        "rows": [
            {
                "market": market,
                "unit": 5,
                "strategy": "ma_cross",
                "parameters": {"short_window": 5, "long_window": 20},
                "period_label": "7d",
                "metrics": {
                    "total_return": 0.04,
                    "mdd": 0.05,
                    "win_rate": 0.6,
                    "trade_count": 8,
                    "profit_factor": 1.5,
                },
                "stability_score": score,
                "warnings": [],
            }
        ],
    }


class StrategyDiscoverySchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        self.env_patch = patch.dict(
            os.environ,
            {
                "DATABASE_URL": "",
                "AUTO_DISCOVERY_SCHEDULER_ENABLED": "true",
                "AUTO_DISCOVERY_EXCHANGE": "bithumb",
                "AUTO_FAST_VALIDATION_SCHEDULER_ENABLED": "true",
                "AUTO_FAST_VALIDATION_INTERVAL_MINUTES": "15",
                "AUTO_FAST_VALIDATION_MAX_MARKETS": "5",
                "AUTO_FAST_VALIDATION_TIMEFRAMES": "5",
                "AUTO_FAST_VALIDATION_PERIODS": "7d",
                "AUTO_VALIDATION_REQUEST_DELAY_SECONDS": "0",
                "AUTO_VALIDATION_MAX_SAVE_PER_RUN": "1",
                "AUTO_VALIDATION_MAX_SAVE_PER_DAY": "2",
                "AUTO_VALIDATION_MAX_BACKTEST_PASSED": "3",
                "AUTO_PROMOTION_INTERVAL_MINUTES": "15",
                "AUTO_SELECTOR_APPLY_BEST_ENABLED": "true",
            },
            clear=False,
        )
        self.env_patch.start()
        database.init_db()
        seed_market()

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_validation_scheduler_saves_candidate_and_does_not_apply_when_policy_off(self) -> None:
        database.update_bot_operation_policy(
            "KRW-ETH",
            {"auto_trading_enabled": False, "max_total_exposure_krw": 700_000, "daily_loss_limit_pct": 7},
        )
        before = database.load_bot_operation_policy("KRW-ETH")

        async def fake_validation(**kwargs):
            return validation_result(kwargs["market"])

        with patch("app.strategy_discovery_scheduler.run_strategy_validation", fake_validation), \
            patch("app.strategy_promotion_pipeline.fetch_minute_candles", return_value=[]), \
            patch("app.strategy_promotion_pipeline.is_emergency_stopped", return_value=False), \
            patch("app.auto_strategy_selector.is_emergency_stopped", return_value=False):
            state = asyncio.run(run_fast_validation_scheduler_once())
            promotion = asyncio.run(run_promotion_selector_scheduler_once())

        after = database.load_bot_operation_policy("KRW-ETH")
        candidates = database.load_candidate_strategies(10, statuses=["BACKTEST_PASSED"])

        self.assertEqual(state["status"], "COMPLETED")
        self.assertEqual(state["last_result"]["saved_candidate_count"], 1)
        self.assertEqual(promotion["last_result"]["selector_decision"], "BLOCKED")
        self.assertEqual(len(candidates), 1)
        self.assertIsNone(database.load_active_strategy_selection())
        self.assertEqual(before["auto_trading_enabled"], after["auto_trading_enabled"])
        self.assertEqual(before["max_total_exposure_krw"], after["max_total_exposure_krw"])
        self.assertEqual(before["daily_loss_limit_pct"], after["daily_loss_limit_pct"])

    def test_validation_scheduler_prevents_duplicate_candidate_creation(self) -> None:
        async def fake_validation(**kwargs):
            return validation_result(kwargs["market"])

        with patch("app.strategy_discovery_scheduler.run_strategy_validation", fake_validation):
            first = asyncio.run(run_fast_validation_scheduler_once())
            second = asyncio.run(run_fast_validation_scheduler_once())

        candidates = database.load_candidate_strategies(10, statuses=["BACKTEST_PASSED"])
        self.assertEqual(first["last_result"]["saved_candidate_count"], 1)
        self.assertEqual(second["last_result"]["saved_candidate_count"], 0)
        self.assertEqual(second["last_result"]["skipped_candidates"][0]["reason"], "DUPLICATE_CANDIDATE")
        self.assertEqual(len(candidates), 1)

    def test_validation_scheduler_limits_daily_auto_saved_candidates(self) -> None:
        seed_market("KRW-XRP")

        async def fake_validation(**kwargs):
            return validation_result(kwargs["market"])

        with patch.dict(os.environ, {"AUTO_VALIDATION_MAX_SAVE_PER_DAY": "1", "AUTO_VALIDATION_MAX_SAVE_PER_RUN": "5", "AUTO_FAST_VALIDATION_MAX_MARKETS": "5"}, clear=False), \
            patch("app.strategy_discovery_scheduler.run_strategy_validation", fake_validation):
            state = asyncio.run(run_fast_validation_scheduler_once())

        candidates = database.load_candidate_strategies(10, statuses=["BACKTEST_PASSED"])
        self.assertEqual(state["last_result"]["saved_candidate_count"], 1)
        self.assertEqual(state["last_result"]["skipped_candidates"][0]["reason"], "DAILY_CANDIDATE_SAVE_LIMIT")
        self.assertEqual(len(candidates), 1)

    def test_status_endpoint_shape_includes_scheduler_tasks(self) -> None:
        database.finish_scheduler_task("market_scan", status="COMPLETED", result={"accepted_count": 2}, next_run_at="2026-06-22T00:15:00Z")

        status = discovery_scheduler_status()

        self.assertTrue(status["enabled"])
        self.assertEqual(status["exchange"], "bithumb")
        self.assertEqual(status["scan"]["status"], "COMPLETED")
        self.assertEqual(status["scan"]["last_result"]["accepted_count"], 2)
        self.assertEqual(status["fast_validation"]["status"], "IDLE")
        self.assertEqual(status["deep_validation"]["status"], "IDLE")
        self.assertEqual(status["promotion_selector"]["status"], "IDLE")


if __name__ == "__main__":
    unittest.main()
