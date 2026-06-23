from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database
from app.strategy_promotion_pipeline import (
    apply_selector_if_allowed,
    enroll_backtest_passed_candidates,
    promote_shadow_candidates,
)


def candidate(status: str = "BACKTEST_PASSED", market: str = "KRW-ETH", score: float = 88.0) -> dict:
    return {
        "name": f"{market} test",
        "description": "",
        "strategy": "ma_cross",
        "parameters": {"short_window": 5, "long_window": 20},
        "unit": 5,
        "market": market,
        "backtest_period": "30d",
        "score": score,
        "backtest_total_return": 0.03,
        "backtest_mdd": 0.04,
        "backtest_win_rate": 0.55,
        "backtest_profit_factor": 1.4,
        "backtest_trade_count": 12,
        "backtest_average_trade_pnl": 0.002,
        "warning": "",
        "status": status,
    }


def allow_market(market: str = "KRW-ETH", *, live_allowed: bool = True) -> None:
    database.upsert_market_universe(
        [
            {
                "exchange": "bithumb",
                "market": market,
                "symbol": market.split("-")[-1],
                "quote_currency": "KRW",
                "status": "DISCOVERED",
                "is_enabled": True,
                "is_live_allowed": live_allowed,
                "is_auto_selectable": True,
                "scan_rank": 1,
                "score": 90,
                "reason": "test",
                "min_24h_trade_price_krw": 0,
                "last_24h_trade_price_krw": 1_000_000_000,
                "last_price": 100_000,
                "last_change_rate": 0,
                "last_volatility_score": 20,
                "last_liquidity_score": 60,
                "last_risk_score": 10,
                "last_scanned_at": "2026-06-22T00:00:00Z",
            }
        ]
    )


class StrategyPromotionPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        database.init_db()
        allow_market()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_backtest_passed_candidate_is_enrolled_into_forward_shadow(self) -> None:
        candidate_id = database.save_candidate_strategy(candidate("BACKTEST_PASSED"))

        async def fake_candles(*, market: str, unit: int, count: int) -> list[dict]:
            return [
                {"trade_price": 101_000, "candle_time_utc": "2026-06-22T00:05:00Z"},
                {"trade_price": 100_000, "candle_time_utc": "2026-06-22T00:00:00Z"},
            ]

        with patch("app.strategy_promotion_pipeline.fetch_minute_candles", fake_candles):
            result = asyncio.run(enroll_backtest_passed_candidates(limit=5))

        self.assertEqual(len(result["enrolled"]), 1)
        self.assertEqual(database.load_candidate_strategy(candidate_id)["status"], "SHADOW_RUNNING")
        session = database.load_latest_forward_session_for_candidate(candidate_id)
        self.assertIsNotNone(session)
        assert session is not None
        self.assertEqual(session["status"], "RUNNING")

    def test_forward_shadow_pass_promotes_to_live_eligible(self) -> None:
        candidate_id = database.save_candidate_strategy(candidate("SHADOW_RUNNING"))
        session_id = database.create_forward_session_from_candidate(
            database.load_candidate_strategy(candidate_id),
            initial_balance_krw=1_000_000,
            risk={},
            current_price=100_000,
            last_processed_candle_time_utc="2026-06-22T00:00:00Z",
        )
        with database.get_connection() as conn:
            conn.execute(
                """
                UPDATE paper_forward_sessions
                SET trade_count = 30,
                    win_count = 18,
                    loss_count = 12,
                    win_rate = 0.6667,
                    profit_factor = 2.0,
                    total_return_percent = 1.25,
                    max_drawdown = 0.04,
                    total_equity = 1012500,
                    realized_pnl = 30000,
                    started_at = '2026-06-01T00:00:00Z',
                    last_tick_time_utc = '2026-06-08T00:00:00Z',
                    updated_at = '2026-06-08T00:00:00Z'
                WHERE id = ?
                """,
                (session_id,),
            )
        for index in range(30):
            database.insert_forward_order(
                session_id,
                {
                    "candidate_strategy_id": candidate_id,
                    "market": "KRW-ETH",
                    "unit": 5,
                    "strategy": "ma_cross",
                    "side": "SELL",
                    "price": 100_000,
                    "volume": 0.01,
                    "amount_krw": 1_000,
                    "fee": 0,
                    "slippage": 0,
                    "realized_pnl": 1_000,
                    "reason": "TAKE_PROFIT",
                    "risk_result": "PASS",
                    "candle_time_utc": f"2026-06-02T00:{index:02d}:00Z",
                },
            )

        result = promote_shadow_candidates()

        self.assertEqual(database.load_candidate_strategy(candidate_id)["status"], "LIVE_ELIGIBLE")
        self.assertEqual([item["to_status"] for item in result["promoted"]], ["SHADOW_PASSED", "LIVE_ELIGIBLE"])

    def test_live_eligible_promotion_marks_market_live_allowed(self) -> None:
        allow_market("KRW-XRP", live_allowed=False)
        candidate_id = database.save_candidate_strategy(candidate("SHADOW_PASSED", market="KRW-XRP"))
        self.assertFalse(database.market_is_live_allowed("bithumb", "KRW-XRP"))

        promoted = database.promote_candidate_strategy(candidate_id, "LIVE_ELIGIBLE", reason="test")

        self.assertIsNotNone(promoted)
        self.assertTrue(database.market_is_live_allowed("bithumb", "KRW-XRP"))

    def test_selector_apply_requires_auto_trading_on_and_does_not_mutate_policy(self) -> None:
        candidate_id = database.save_candidate_strategy(candidate("LIVE_ELIGIBLE", score=95))
        database.update_bot_operation_policy(
            "KRW-BTC",
            {"auto_trading_enabled": False, "max_total_exposure_krw": 700_000, "daily_loss_limit_pct": 7},
        )
        before = database.load_bot_operation_policy("KRW-BTC")
        with patch.dict(os.environ, {"AUTO_SELECTOR_APPLY_BEST_ENABLED": "true"}, clear=False), \
            patch("app.strategy_promotion_pipeline.is_emergency_stopped", return_value=False), \
            patch("app.auto_strategy_selector.is_emergency_stopped", return_value=False):
            blocked = apply_selector_if_allowed(exchange="bithumb")
        after_blocked = database.load_bot_operation_policy("KRW-BTC")

        self.assertEqual(blocked["decision"], "BLOCKED")
        self.assertIn("POLICY_AUTO_TRADING_DISABLED", blocked["blockers"])
        self.assertIsNone(database.load_active_strategy_selection())
        self.assertEqual(before["auto_trading_enabled"], after_blocked["auto_trading_enabled"])
        self.assertEqual(before["max_total_exposure_krw"], after_blocked["max_total_exposure_krw"])
        self.assertEqual(before["daily_loss_limit_pct"], after_blocked["daily_loss_limit_pct"])

        database.update_bot_operation_policy("KRW-BTC", {"auto_trading_enabled": True})
        before_apply = database.load_bot_operation_policy("KRW-BTC")
        with patch.dict(os.environ, {"AUTO_SELECTOR_APPLY_BEST_ENABLED": "true"}, clear=False), \
            patch("app.strategy_promotion_pipeline.is_emergency_stopped", return_value=False), \
            patch("app.auto_strategy_selector.is_emergency_stopped", return_value=False):
            applied = apply_selector_if_allowed(exchange="bithumb")
        after_apply = database.load_bot_operation_policy("KRW-BTC")

        self.assertEqual(applied["decision"], "APPLY")
        self.assertEqual(database.load_active_strategy_selection()["candidate_strategy_id"], candidate_id)
        self.assertEqual(before_apply["auto_trading_enabled"], after_apply["auto_trading_enabled"])
        self.assertEqual(before_apply["max_total_exposure_krw"], after_apply["max_total_exposure_krw"])
        self.assertEqual(before_apply["daily_loss_limit_pct"], after_apply["daily_loss_limit_pct"])


if __name__ == "__main__":
    unittest.main()
