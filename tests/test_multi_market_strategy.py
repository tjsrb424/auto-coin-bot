import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app import database
from app.auto_strategy_selector import evaluate_auto_strategy_selector
from app.risk_manager import check_order_risk
from app.strategy_validation import run_strategy_validation


def candidate_payload(market: str, status: str = "BACKTEST_PASSED", score: float = 82.0) -> dict:
    return {
        "name": f"{market} test",
        "description": "",
        "strategy": "ma_cross",
        "parameters": {"short_window": 3, "long_window": 8},
        "unit": 5,
        "market": market,
        "backtest_period": "7d",
        "score": score,
        "backtest_total_return": 0.04,
        "backtest_mdd": 0.05,
        "backtest_win_rate": 0.55,
        "backtest_profit_factor": 1.6,
        "backtest_trade_count": 8,
        "backtest_average_trade_pnl": 0.005,
        "warning": "",
        "status": status,
    }


def candle_rows(market: str, count: int = 80) -> list[dict]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    price = 1000.0
    for index in range(count):
        price += 4 if index % 9 else -8
        rows.append(
            {
                "market": market,
                "unit": 5,
                "candle_time_utc": (start + timedelta(minutes=5 * index)).isoformat().replace("+00:00", "Z"),
                "candle_time_kst": "",
                "opening_price": price - 2,
                "high_price": price + 5,
                "low_price": price - 5,
                "trade_price": price,
                "candle_acc_trade_price": 10_000_000,
                "candle_acc_trade_volume": 1000,
                "timestamp": index,
            }
        )
    return rows


class MultiMarketStrategyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        self.env_patch = patch.dict(
            os.environ,
            {
                "DATABASE_URL": "",
                "RISK_MIN_VOLUME_KRW": "0",
            },
            clear=False,
        )
        self.env_patch.start()
        database.init_db()

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.db_patch.stop()
        self.tmp.cleanup()

    def seed_market(self, market: str = "KRW-ETH", *, live_allowed: bool = True) -> None:
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
                }
            ]
        )

    def test_market_universe_persists_flags(self) -> None:
        self.seed_market(live_allowed=False)
        row = database.load_market_universe_item("bithumb", "KRW-ETH")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertTrue(row["is_enabled"])
        self.assertFalse(row["is_live_allowed"])
        updated = database.update_market_universe_item(int(row["id"]), {"is_live_allowed": True})
        self.assertTrue(updated["is_live_allowed"])

    def test_non_btc_live_order_requires_live_candidate_and_live_allowed_market(self) -> None:
        self.seed_market(live_allowed=True)
        database.update_bot_operation_policy("KRW-BTC", {"auto_trading_enabled": True, "max_total_exposure_krw": 500_000, "daily_loss_limit_pct": 5})
        candidate_id = database.save_candidate_strategy(candidate_payload("KRW-ETH", "BACKTEST_PASSED"))
        order = {"exchange": "bithumb", "market": "KRW-ETH", "side": "BUY", "order_type": "LIMIT", "amount_krw": 20_000, "price": 1_000, "volume": 20}
        blocked = check_order_risk(
            order=order,
            purpose="ENTRY",
            mode="AUTO_STRATEGY_RUNNING",
            candidate_strategy_id=candidate_id,
            market_snapshot={"price": 1_000, "range_rate": 0.01, "volume": 1000, "trade_price_volume": 20_000_000, "complete": True},
            balances={"by_currency": {"KRW": {"balance": 100_000, "locked": 0}}},
            is_auto=True,
        )
        self.assertFalse(blocked["allowed"])
        self.assertEqual(blocked["risk_result"], "BLOCKED_MARKET_NOT_ALLOWED")

        database.update_candidate_strategy(candidate_id, {"status": "LIVE_ELIGIBLE"})
        allowed = check_order_risk(
            order={**order, "request_id": "allowed-eth"},
            purpose="ENTRY",
            mode="AUTO_STRATEGY_RUNNING",
            candidate_strategy_id=candidate_id,
            market_snapshot={"price": 1_000, "range_rate": 0.01, "volume": 1000, "trade_price_volume": 20_000_000, "complete": True},
            balances={"by_currency": {"KRW": {"balance": 100_000, "locked": 0}}},
            is_auto=True,
        )
        self.assertTrue(allowed["allowed"], allowed)

    def test_selector_does_not_enable_policy_and_can_apply_when_policy_is_on(self) -> None:
        self.seed_market(live_allowed=True)
        candidate_id = database.save_candidate_strategy(candidate_payload("KRW-ETH", "LIVE_ELIGIBLE", 95))
        blocked = evaluate_auto_strategy_selector(exchange="bithumb", apply=True)
        self.assertFalse(blocked["can_apply"])
        self.assertIn("POLICY_AUTO_TRADING_DISABLED", blocked["blockers"])
        self.assertIsNone(database.load_active_strategy_selection())

        database.update_bot_operation_policy("KRW-BTC", {"auto_trading_enabled": True, "max_total_exposure_krw": 500_000, "daily_loss_limit_pct": 5})
        applied = evaluate_auto_strategy_selector(exchange="bithumb", apply=True)
        self.assertTrue(applied["can_apply"], applied)
        self.assertEqual(database.load_active_strategy_selection()["candidate_strategy_id"], candidate_id)

    def test_strategy_validation_accepts_non_btc_market(self) -> None:
        async def loader(market: str, unit: int, start: str, end: str):
            return candle_rows(market)

        async def run():
            return await run_strategy_validation(
                market="KRW-ETH",
                strategy="ma_cross",
                timeframes=[5],
                periods=["7d"],
                custom_start_time_utc=None,
                custom_end_time_utc=None,
                base_settings={"short_window": 3, "long_window": 8},
                risk={"fee_rate": 0.0005, "slippage_rate": 0.0005},
                load_period_candles=loader,
            )

        import asyncio

        result = asyncio.run(run())
        self.assertEqual(result["rows"][0]["market"], "KRW-ETH")


if __name__ == "__main__":
    unittest.main()
