import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database
from app.capital_allocator import run_capital_allocator_once


def candidate_payload(market: str = "KRW-ETH", status: str = "LIVE_ELIGIBLE", score: float = 95.0) -> dict:
    return {
        "name": f"{market} allocator test",
        "description": "",
        "strategy": "ma_cross",
        "parameters": {"short_window": 5, "long_window": 20},
        "unit": 5,
        "market": market,
        "backtest_period": "30d",
        "score": score,
        "backtest_total_return": 0.04,
        "backtest_mdd": 0.04,
        "backtest_win_rate": 0.55,
        "backtest_profit_factor": 1.4,
        "backtest_trade_count": 12,
        "backtest_average_trade_pnl": 0.002,
        "warning": "",
        "status": status,
    }


def allow_market(market: str = "KRW-ETH") -> None:
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


class CapitalAllocatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        self.env_patch = patch.dict(
            os.environ,
            {
                "DATABASE_URL": "",
                "AUTO_CAPITAL_ALLOCATOR_ENABLED": "true",
                "AUTO_ALLOWED_EXCHANGE": "bithumb",
                "AUTO_MAX_OPEN_POSITION_COUNT": "5",
                "AUTO_MAX_NEW_ENTRIES_PER_TICK": "2",
                "AUTO_MAX_ORDER_KRW": "30000",
                "AUTO_MIN_ORDER_KRW": "5000",
                "AUTO_SELECTOR_APPLY_BEST_ENABLED": "true",
            },
            clear=False,
        )
        self.env_patch.start()
        database.init_db()
        allow_market()

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_auto_trading_off_queues_candidate_without_live_active_or_policy_mutation(self) -> None:
        database.save_candidate_strategy(candidate_payload())
        database.update_bot_operation_policy(
            "KRW-BTC",
            {"auto_trading_enabled": False, "max_total_exposure_krw": 500_000, "daily_loss_limit_pct": 5},
        )
        before = database.load_bot_operation_policy("KRW-BTC")

        with patch("app.capital_allocator.is_emergency_stopped", return_value=False):
            result = run_capital_allocator_once("TEST", exchange="bithumb")

        after = database.load_bot_operation_policy("KRW-BTC")
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["accepted"]), 0)
        self.assertEqual(result["blocked"][0]["blocked_reason"], "POLICY_AUTO_TRADING_DISABLED")
        self.assertIsNone(database.load_active_strategy_selection())
        self.assertEqual(before["auto_trading_enabled"], after["auto_trading_enabled"])
        self.assertEqual(before["max_total_exposure_krw"], after["max_total_exposure_krw"])
        self.assertEqual(before["daily_loss_limit_pct"], after["daily_loss_limit_pct"])

    def test_auto_trading_on_assigns_live_eligible_candidate_to_slot_and_session(self) -> None:
        candidate_id = database.save_candidate_strategy(candidate_payload())
        database.update_bot_operation_policy(
            "KRW-BTC",
            {"auto_trading_enabled": True, "max_total_exposure_krw": 500_000, "daily_loss_limit_pct": 5},
        )

        with patch("app.capital_allocator.is_emergency_stopped", return_value=False):
            result = run_capital_allocator_once("TEST", exchange="bithumb")

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["accepted"]), 1)
        self.assertEqual(database.load_candidate_strategy(candidate_id)["status"], "LIVE_ACTIVE")
        self.assertEqual(database.load_active_strategy_selection()["candidate_strategy_id"], candidate_id)
        sessions = database.load_running_live_strategy_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["candidate_strategy_id"], candidate_id)
        self.assertLessEqual(float(sessions[0]["max_order_krw"]), 30_000)
        slots = database.load_position_slots(5, "bithumb")
        self.assertEqual(slots[0]["status"], "RESERVED")
        self.assertEqual(slots[0]["candidate_strategy_id"], candidate_id)

    def test_expired_order_reservation_releases_reserved_slot(self) -> None:
        candidate_id = database.save_candidate_strategy(candidate_payload())
        slots = database.ensure_position_slots(5, "bithumb")
        slot_id = int(slots[0]["id"])
        database.create_order_reservation(
            {
                "request_id": "expired-test",
                "exchange": "bithumb",
                "market": "KRW-ETH",
                "candidate_strategy_id": candidate_id,
                "slot_id": slot_id,
                "amount_krw": 10_000,
                "status": "RESERVED",
                "expires_at": "2000-01-01T00:00:00Z",
            }
        )
        database.reserve_position_slot(
            slot_id=slot_id,
            exchange="bithumb",
            market="KRW-ETH",
            candidate_strategy_id=candidate_id,
            live_strategy_session_id=None,
            amount_krw=10_000,
            reason="test",
        )

        slots = database.load_position_slots(5, "bithumb")

        self.assertEqual(slots[0]["status"], "EMPTY")


if __name__ == "__main__":
    unittest.main()
