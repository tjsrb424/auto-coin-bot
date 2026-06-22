import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from app import database
from app.capital_snapshot import build_capital_snapshot_async, sellable_volume_for_position


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
            }
        ]
    )


def balances(krw: float = 100_000, eth: float = 0.0, btc: float = 0.0) -> dict:
    return {
        "by_currency": {
            "KRW": {"balance": krw, "locked": 0.0},
            "ETH": {"balance": eth, "locked": 0.0},
            "BTC": {"balance": btc, "locked": 0.0},
        },
        "krw": {"balance": krw, "locked": 0.0},
    }


def candidate_payload(market: str = "KRW-ETH") -> dict:
    return {
        "name": f"{market} snapshot test",
        "description": "",
        "strategy": "ma_cross",
        "parameters": {"short_window": 5, "long_window": 20},
        "unit": 5,
        "market": market,
        "backtest_period": "30d",
        "score": 95,
        "backtest_total_return": 0.04,
        "backtest_mdd": 0.04,
        "backtest_win_rate": 0.55,
        "backtest_profit_factor": 1.4,
        "backtest_trade_count": 12,
        "backtest_average_trade_pnl": 0.002,
        "warning": "",
        "status": "LIVE_ACTIVE",
    }


class CapitalSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        self.env_patch = patch.dict(
            os.environ,
            {
                "DATABASE_URL": "",
                "AUTO_MAX_OPEN_POSITION_COUNT": "5",
                "AUTO_CASH_RESERVE_PCT": "5",
            },
            clear=False,
        )
        self.env_patch.start()
        database.init_db()
        allow_market()
        database.update_bot_operation_policy(
            "KRW-BTC",
            {"auto_trading_enabled": True, "max_total_exposure_krw": 500_000, "daily_loss_limit_pct": 5},
        )

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.db_patch.stop()
        self.tmp.cleanup()

    def broker(self, account: dict, open_orders: list[dict] | None = None):
        broker = Mock()
        broker.get_balances = AsyncMock(return_value=account)
        broker.list_open_orders = AsyncMock(return_value={"orders": open_orders or []})
        return broker

    def run_snapshot(self, account: dict, open_orders: list[dict] | None = None) -> dict:
        with patch("app.capital_snapshot.get_live_broker", return_value=self.broker(account, open_orders)):
            return asyncio.run(build_capital_snapshot_async("bithumb"))

    def test_available_budget_is_limited_by_krw_balance(self) -> None:
        snapshot = self.run_snapshot(balances(krw=100_000))

        self.assertEqual(snapshot["available_krw_balance"], 100_000)
        self.assertEqual(snapshot["cash_reserve_krw"], 25_000)
        self.assertEqual(snapshot["available_budget_krw"], 75_000)

    def test_remaining_exposure_zero_blocks_even_with_krw(self) -> None:
        candidate_id = database.save_candidate_strategy(candidate_payload())
        session_id = database.create_live_strategy_session(
            {
                "exchange": "bithumb",
                "market": "KRW-ETH",
                "candidate_strategy_id": candidate_id,
                "strategy_name": "ma_cross",
                "strategy_parameters": {},
                "status": "RUNNING",
                "auto_enabled": True,
                "initial_balance_krw": 0,
                "max_order_krw": 20_000,
                "max_orders_per_day": 3,
            }
        )
        database.create_live_position(
            {
                "session_id": session_id,
                "exchange": "bithumb",
                "market": "KRW-ETH",
                "candidate_strategy_id": candidate_id,
                "strategy_name": "ma_cross",
                "status": "OPEN",
                "entry_price": 1000,
                "entry_volume": 500,
                "entry_amount_krw": 500_000,
                "current_price": 1000,
                "stop_loss_price": 900,
                "take_profit_price": 1100,
            }
        )

        snapshot = self.run_snapshot(balances(krw=1_000_000, eth=500))

        self.assertEqual(snapshot["remaining_exposure_krw"], 0)
        self.assertEqual(snapshot["available_budget_krw"], 0)
        self.assertIn("BLOCKED_REMAINING_EXPOSURE_TOO_SMALL", snapshot["blockers"])

    def test_reservation_and_exchange_buy_order_reduce_remaining_exposure(self) -> None:
        candidate_id = database.save_candidate_strategy(candidate_payload())
        database.create_order_reservation(
            {
                "request_id": "reservation-test",
                "exchange": "bithumb",
                "market": "KRW-ETH",
                "candidate_strategy_id": candidate_id,
                "slot_id": None,
                "amount_krw": 100_000,
                "status": "RESERVED",
                "expires_at": "2099-01-01T00:00:00Z",
            }
        )

        snapshot = self.run_snapshot(
            balances(krw=500_000),
            [{"uuid": "open-buy", "side": "bid", "price": "1000", "remaining_volume": "50"}],
        )

        self.assertEqual(snapshot["pending_buy_reserved_krw"], 100_000)
        self.assertEqual(snapshot["pending_exchange_buy_order_krw"], 50_000)
        self.assertEqual(snapshot["remaining_exposure_krw"], 350_000)

    def test_balance_fetch_failure_blocks_new_buy(self) -> None:
        broker = Mock()
        broker.get_balances = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("app.capital_snapshot.get_live_broker", return_value=broker):
            snapshot = asyncio.run(build_capital_snapshot_async("bithumb"))

        self.assertEqual(snapshot["available_budget_krw"], 0)
        self.assertEqual(snapshot["snapshot_error"], "BALANCE_FETCH_FAILED")
        self.assertIn("BLOCKED_EXCHANGE_BALANCE_UNAVAILABLE", snapshot["blockers"])

    def test_open_position_without_exchange_balance_detects_mismatch(self) -> None:
        candidate_id = database.save_candidate_strategy(candidate_payload())
        session_id = database.create_live_strategy_session(
            {
                "exchange": "bithumb",
                "market": "KRW-ETH",
                "candidate_strategy_id": candidate_id,
                "strategy_name": "ma_cross",
                "strategy_parameters": {},
                "status": "RUNNING",
                "auto_enabled": True,
                "initial_balance_krw": 0,
                "max_order_krw": 20_000,
                "max_orders_per_day": 3,
            }
        )
        position_id = database.create_live_position(
            {
                "session_id": session_id,
                "exchange": "bithumb",
                "market": "KRW-ETH",
                "candidate_strategy_id": candidate_id,
                "strategy_name": "ma_cross",
                "status": "OPEN",
                "entry_price": 1000,
                "entry_volume": 10,
                "entry_amount_krw": 10_000,
                "current_price": 1000,
                "stop_loss_price": 900,
                "take_profit_price": 1100,
            }
        )

        snapshot = self.run_snapshot(balances(krw=100_000, eth=0))

        self.assertTrue(snapshot["balance_mismatch_detected"])
        self.assertIn("BLOCKED_BALANCE_MISMATCH", snapshot["blockers"])
        self.assertEqual(sellable_volume_for_position(snapshot, database.load_live_position(position_id)), 0)

    def test_ignored_point_balance_and_grouped_positions_do_not_block(self) -> None:
        candidate_id = database.save_candidate_strategy(candidate_payload("KRW-BTC"))
        session_id = database.create_live_strategy_session(
            {
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": candidate_id,
                "strategy_name": "ma_cross",
                "strategy_parameters": {},
                "status": "RUNNING",
                "auto_enabled": True,
                "initial_balance_krw": 0,
                "max_order_krw": 20_000,
                "max_orders_per_day": 3,
            }
        )
        for volume in (0.00002410, 0.00010005):
            database.create_live_position(
                {
                    "session_id": session_id,
                    "exchange": "bithumb",
                    "market": "KRW-BTC",
                    "candidate_strategy_id": candidate_id,
                    "strategy_name": "ma_cross",
                    "status": "OPEN",
                    "entry_price": 96_722_000,
                    "entry_volume": volume,
                    "entry_amount_krw": 96_722_000 * volume,
                    "current_price": 96_722_000,
                    "stop_loss_price": 96_000_000,
                    "take_profit_price": 98_000_000,
                }
            )
        account = {
            "by_currency": {
                "KRW": {"balance": 100_000, "locked": 0.0},
                "BTC": {"balance": 0.00012416, "locked": 0.0},
                "P": {"balance": 6, "locked": 0.0},
            },
            "krw": {"balance": 100_000, "locked": 0.0},
        }

        snapshot = self.run_snapshot(account)

        self.assertFalse(snapshot["balance_mismatch_detected"])
        self.assertNotIn("BLOCKED_BALANCE_MISMATCH", snapshot["blockers"])
        self.assertNotIn("EXCHANGE_BALANCE_WITHOUT_DB_POSITION:P", snapshot["warnings"])
        self.assertAlmostEqual(snapshot["exchange_position_value_krw"], 96_722_000 * 0.00012416)

    def test_sellable_volume_uses_smaller_of_db_and_exchange_balance(self) -> None:
        candidate_id = database.save_candidate_strategy(candidate_payload())
        session_id = database.create_live_strategy_session(
            {
                "exchange": "bithumb",
                "market": "KRW-ETH",
                "candidate_strategy_id": candidate_id,
                "strategy_name": "ma_cross",
                "strategy_parameters": {},
                "status": "RUNNING",
                "auto_enabled": True,
                "initial_balance_krw": 0,
                "max_order_krw": 20_000,
                "max_orders_per_day": 3,
            }
        )
        position_id = database.create_live_position(
            {
                "session_id": session_id,
                "exchange": "bithumb",
                "market": "KRW-ETH",
                "candidate_strategy_id": candidate_id,
                "strategy_name": "ma_cross",
                "status": "OPEN",
                "entry_price": 1000,
                "entry_volume": 10,
                "entry_amount_krw": 10_000,
                "current_price": 1000,
                "stop_loss_price": 900,
                "take_profit_price": 1100,
            }
        )

        snapshot = self.run_snapshot(balances(krw=100_000, eth=4))

        self.assertEqual(sellable_volume_for_position(snapshot, database.load_live_position(position_id)), 4)


if __name__ == "__main__":
    unittest.main()
