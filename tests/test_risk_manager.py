from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app import database
from app.risk_manager import check_order_risk, compute_risk_state, consecutive_loss_count, get_risk_dashboard


def order() -> dict:
    return {
        "request_id": "risk-test-request",
        "exchange": "bithumb",
        "market": "KRW-BTC",
        "side": "BUY",
        "order_type": "LIMIT",
        "price": 100_000_000,
        "volume": 0.0001,
        "amount_krw": 10_000,
    }


class RiskManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        database.init_db()
        database.update_bot_operation_policy(
            "KRW-BTC",
            {"auto_trading_enabled": True, "max_total_exposure_krw": 300_000, "daily_loss_limit_pct": 20},
        )

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.tmp.cleanup()

    def create_closed_position(self, realized_pnl: float, closed_at: datetime | None = None) -> int:
        session_id = database.create_live_strategy_session(
            {
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 1,
                "strategy_name": "ma_cross",
                "strategy_parameters": {},
                "status": "STOPPED",
                "auto_enabled": False,
                "initial_balance_krw": 0,
                "max_order_krw": 10_000,
                "max_orders_per_day": 1,
            }
        )
        closed = (closed_at or datetime.now(timezone.utc)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        return database.create_live_position(
            {
                "session_id": session_id,
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 1,
                "strategy_name": "ma_cross",
                "status": "CLOSED",
                "entry_price": 100_000_000,
                "entry_volume": 0.001,
                "entry_amount_krw": 100_000,
                "current_price": 99_000_000,
                "unrealized_pnl": 0,
                "realized_pnl": realized_pnl,
                "stop_loss_price": 0,
                "take_profit_price": 0,
                "opened_at": "2026-06-16T00:00:00Z",
                "closed_at": closed,
            }
        )

    def test_daily_loss_limit_blocks_entry(self) -> None:
        session_id = database.create_live_strategy_session(
            {
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 1,
                "strategy_name": "ma_cross",
                "strategy_parameters": {},
                "status": "STOPPED",
                "auto_enabled": False,
                "initial_balance_krw": 0,
                "max_order_krw": 10_000,
                "max_orders_per_day": 1,
            }
        )
        database.create_live_position(
            {
                "session_id": session_id,
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 1,
                "strategy_name": "ma_cross",
                "status": "CLOSED",
                "entry_price": 100_000_000,
                "entry_volume": 0.001,
                "entry_amount_krw": 100_000,
                "current_price": 99_000_000,
                "unrealized_pnl": 0,
                "realized_pnl": -20_000,
                "stop_loss_price": 0,
                "take_profit_price": 0,
                "opened_at": "2026-06-16T00:00:00Z",
                "closed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            }
        )
        with patch.dict(os.environ, {"RISK_MAX_DAILY_LOSS_KRW": "10000", "RISK_MIN_COOLDOWN_SECONDS": "0"}, clear=False):
            result = check_order_risk(order=order(), purpose="ENTRY", base_result={"allowed": True}, is_auto=True)

        self.assertFalse(result["allowed"])
        self.assertEqual(result["block_code"], "BLOCKED_DAILY_LOSS_LIMIT")
        self.assertEqual(compute_risk_state()["status"], "BLOCKED")

    def test_four_meaningful_consecutive_losses_block_entry(self) -> None:
        base = datetime.now(timezone.utc).replace(microsecond=0)
        for index in range(4):
            self.create_closed_position(-600, base + timedelta(minutes=index))

        env = {
            "RISK_MAX_DAILY_LOSS_KRW": "10000",
            "RISK_MAX_DAILY_LOSS_PERCENT": "99",
            "RISK_MAX_CONSECUTIVE_LOSSES": "4",
            "RISK_CONSECUTIVE_LOSS_MIN_KRW": "500",
            "RISK_MIN_COOLDOWN_SECONDS": "0",
        }
        with patch.dict(os.environ, env, clear=False):
            state = compute_risk_state()
            result = check_order_risk(order=order(), purpose="ENTRY", base_result={"allowed": True}, is_auto=True)

        self.assertEqual(state["consecutive_loss_count"], 4)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["block_code"], "BLOCKED_CONSECUTIVE_LOSS_LIMIT")

    def test_requested_max_open_positions_guard_blocks_entry(self) -> None:
        session_id = database.create_live_strategy_session(
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
        database.create_live_position(
            {
                "session_id": session_id,
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 1,
                "strategy_name": "ma_cross",
                "status": "OPEN",
                "entry_price": 100_000_000,
                "entry_volume": 0.001,
                "entry_amount_krw": 100_000,
                "current_price": 100_000_000,
                "unrealized_pnl": 0,
                "realized_pnl": 0,
                "stop_loss_price": 0,
                "take_profit_price": 0,
            }
        )
        env = {
            "MAX_OPEN_POSITIONS": "1",
            "RISK_BLOCK_ON_OPEN_POSITION": "false",
            "RISK_MIN_COOLDOWN_SECONDS": "0",
            "RISK_MAX_DAILY_LOSS_KRW": "999999",
            "RISK_MAX_DAILY_LOSS_PERCENT": "99",
        }
        with patch.dict(os.environ, env, clear=False):
            result = check_order_risk(order=order(), purpose="ENTRY", base_result={"allowed": True}, is_auto=True)

        self.assertFalse(result["allowed"])
        self.assertEqual(result["block_code"], "BLOCKED_MAX_OPEN_POSITIONS")

    def test_requested_symbol_allocation_guard_blocks_entry(self) -> None:
        session_id = database.create_live_strategy_session(
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
        database.create_live_position(
            {
                "session_id": session_id,
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 1,
                "strategy_name": "ma_cross",
                "status": "OPEN",
                "entry_price": 100_000_000,
                "entry_volume": 0.00025,
                "entry_amount_krw": 25_000,
                "current_price": 100_000_000,
                "unrealized_pnl": 0,
                "realized_pnl": 0,
                "stop_loss_price": 0,
                "take_profit_price": 0,
            }
        )
        env = {
            "MAX_SYMBOL_ALLOCATION_RATE": "0.1",
            "MAX_OPEN_POSITIONS": "5",
            "RISK_BLOCK_ON_OPEN_POSITION": "false",
            "RISK_MIN_COOLDOWN_SECONDS": "0",
            "RISK_MAX_DAILY_LOSS_KRW": "999999",
            "RISK_MAX_DAILY_LOSS_PERCENT": "99",
        }
        with patch.dict(os.environ, env, clear=False):
            result = check_order_risk(order=order(), purpose="ENTRY", base_result={"allowed": True}, is_auto=True)

        self.assertFalse(result["allowed"])
        self.assertEqual(result["block_code"], "BLOCKED_MAX_SYMBOL_ALLOCATION")

    def test_small_loss_resets_consecutive_loss_count(self) -> None:
        base = datetime.now(timezone.utc).replace(microsecond=0)
        for index in range(4):
            self.create_closed_position(-600, base + timedelta(minutes=index))
        self.create_closed_position(-100, base + timedelta(minutes=4))

        with patch.dict(os.environ, {"RISK_CONSECUTIVE_LOSS_MIN_KRW": "500"}, clear=False):
            count = consecutive_loss_count("bithumb", "KRW-BTC")

        self.assertEqual(count, 0)

    def test_daily_loss_percent_uses_account_equity_basis(self) -> None:
        session_id = database.create_live_strategy_session(
            {
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 1,
                "strategy_name": "ma_cross",
                "strategy_parameters": {},
                "status": "STOPPED",
                "auto_enabled": False,
                "initial_balance_krw": 0,
                "max_order_krw": 10_000,
                "max_orders_per_day": 1,
            }
        )
        database.create_live_position(
            {
                "session_id": session_id,
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 1,
                "strategy_name": "ma_cross",
                "status": "CLOSED",
                "entry_price": 100_000_000,
                "entry_volume": 0.001,
                "entry_amount_krw": 100_000,
                "current_price": 99_000_000,
                "unrealized_pnl": 0,
                "realized_pnl": -100,
                "stop_loss_price": 0,
                "take_profit_price": 0,
                "opened_at": "2026-06-16T00:00:00Z",
                "closed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            }
        )
        env = {
            "RISK_MAX_DAILY_LOSS_PERCENT": "1",
            "RISK_MAX_DAILY_LOSS_KRW": "10000",
            "RISK_ACCOUNT_EQUITY_KRW": "300000",
            "RISK_MIN_COOLDOWN_SECONDS": "0",
        }
        with patch.dict(os.environ, env, clear=False):
            state = compute_risk_state()
            result = check_order_risk(order=order(), purpose="ENTRY", base_result={"allowed": True}, is_auto=True)

        self.assertAlmostEqual(state["daily_loss_basis_krw"], 300_000)
        self.assertAlmostEqual(state["daily_loss_percent"], 100 / 300_000 * 100)
        self.assertTrue(result["allowed"])

    def test_daily_loss_percent_uses_live_balance_equity_when_available(self) -> None:
        session_id = database.create_live_strategy_session(
            {
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 1,
                "strategy_name": "ma_cross",
                "strategy_parameters": {},
                "status": "STOPPED",
                "auto_enabled": False,
                "initial_balance_krw": 0,
                "max_order_krw": 10_000,
                "max_orders_per_day": 1,
            }
        )
        database.create_live_position(
            {
                "session_id": session_id,
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 1,
                "strategy_name": "ma_cross",
                "status": "CLOSED",
                "entry_price": 100_000_000,
                "entry_volume": 0.001,
                "entry_amount_krw": 100_000,
                "current_price": 99_000_000,
                "unrealized_pnl": 0,
                "realized_pnl": -300,
                "stop_loss_price": 0,
                "take_profit_price": 0,
                "opened_at": "2026-06-16T00:00:00Z",
                "closed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            }
        )
        balances = {"balances": [{"currency": "KRW", "balance": "1000", "locked": "0"}]}
        env = {
            "RISK_MAX_DAILY_LOSS_PERCENT": "20",
            "RISK_MAX_DAILY_LOSS_KRW": "10000",
            "RISK_ACCOUNT_EQUITY_KRW": "300000",
            "RISK_MIN_COOLDOWN_SECONDS": "0",
        }
        with patch.dict(os.environ, env, clear=False):
            result = check_order_risk(
                order={**order(), "amount_krw": 500, "volume": 0.000005},
                purpose="ENTRY",
                base_result={"allowed": True},
                balances=balances,
                is_auto=True,
            )

        self.assertFalse(result["allowed"])
        self.assertEqual(result["block_code"], "BLOCKED_DAILY_LOSS_LIMIT")

    def test_daily_order_count_blocks(self) -> None:
        for index in range(3):
            payload = {
                **order(),
                "request_id": f"existing-{index}",
                "fee_estimate": 5,
                "risk_result": "ALLOWED",
                "order_preview_payload": {},
                "exchange_request_payload_masked": {},
                "exchange_response_payload": {},
                "status": "FILLED",
            }
            database.insert_live_order_log(payload)

        with patch.dict(os.environ, {"RISK_MAX_ORDERS_PER_DAY": "3", "RISK_MIN_COOLDOWN_SECONDS": "0"}, clear=False):
            result = check_order_risk(order=order(), purpose="ENTRY", base_result={"allowed": True}, is_auto=True)

        self.assertEqual(result["block_code"], "BLOCKED_MAX_ORDERS_PER_DAY")

    def test_daily_loss_limit_does_not_block_exit(self) -> None:
        session_id = database.create_live_strategy_session(
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
        position_id = database.create_live_position(
            {
                "session_id": session_id,
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 1,
                "strategy_name": "ma_cross",
                "status": "OPEN",
                "entry_price": 100_000_000,
                "entry_volume": 0.001,
                "entry_amount_krw": 100_000,
                "current_price": 99_000_000,
                "unrealized_pnl": -20_000,
                "realized_pnl": 0,
                "stop_loss_price": 0,
                "take_profit_price": 0,
                "opened_at": "2026-06-16T00:00:00Z",
            }
        )
        with patch.dict(os.environ, {"RISK_MAX_DAILY_LOSS_KRW": "10000", "RISK_MIN_COOLDOWN_SECONDS": "0"}, clear=False):
            result = check_order_risk(
                order={**order(), "request_id": "risk-test-exit", "side": "SELL"},
                purpose="EXIT",
                base_result={"allowed": True},
                position_id=position_id,
                is_auto=True,
            )

        self.assertTrue(result["allowed"])
        self.assertEqual(result["checks"]["daily_limit_check"]["allowed"], True)

    def test_cooldown_blocks(self) -> None:
        database.insert_live_order_log(
            {
                **order(),
                "request_id": "recent-order",
                "fee_estimate": 5,
                "risk_result": "ALLOWED",
                "order_preview_payload": {},
                "exchange_request_payload_masked": {},
                "exchange_response_payload": {},
                "status": "FILLED",
            }
        )

        with patch.dict(os.environ, {"RISK_MIN_COOLDOWN_SECONDS": "1800"}, clear=False):
            result = check_order_risk(order=order(), purpose="ENTRY", base_result={"allowed": True}, is_auto=True)

        self.assertEqual(result["block_code"], "BLOCKED_COOLDOWN")
        self.assertGreater(result["cooldown_remaining_seconds"], 0)

    def test_open_position_blocks_entry_but_not_exit(self) -> None:
        session_id = database.create_live_strategy_session(
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
        position_id = database.create_live_position(
            {
                "session_id": session_id,
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 1,
                "strategy_name": "ma_cross",
                "status": "OPEN",
                "entry_price": 100_000_000,
                "entry_volume": 0.001,
                "entry_amount_krw": 100_000,
                "current_price": 100_000_000,
                "unrealized_pnl": 0,
                "realized_pnl": 0,
                "stop_loss_price": 0,
                "take_profit_price": 0,
                "opened_at": "2026-06-16T00:00:00Z",
            }
        )

        with patch.dict(os.environ, {"RISK_MIN_COOLDOWN_SECONDS": "0"}, clear=False):
            entry = check_order_risk(order=order(), purpose="ENTRY", base_result={"allowed": True}, candidate_strategy_id=1, is_auto=True)
            exit_result = check_order_risk(order={**order(), "side": "SELL"}, purpose="EXIT", base_result={"allowed": True}, position_id=position_id, is_auto=False)

        self.assertEqual(entry["block_code"], "BLOCKED_OPEN_POSITION_EXISTS")
        self.assertNotEqual(exit_result.get("block_code"), "BLOCKED_OPEN_POSITION_EXISTS")

    def test_market_filters_block_entry(self) -> None:
        with patch.dict(os.environ, {"RISK_MIN_COOLDOWN_SECONDS": "0", "RISK_VOLATILITY_BLOCK_PERCENT": "2", "RISK_MIN_VOLUME_KRW": "100000000"}, clear=False):
            volatile = check_order_risk(
                order=order(),
                purpose="ENTRY",
                base_result={"allowed": True},
                is_auto=True,
                market_snapshot={"price": 100_000_000, "range_rate": 0.03, "volume": 10, "trade_price_volume": 1_000_000_000},
            )
            low_volume = check_order_risk(
                order={**order(), "request_id": "risk-test-low-volume"},
                purpose="ENTRY",
                base_result={"allowed": True},
                is_auto=True,
                market_snapshot={"price": 100_000_000, "range_rate": 0.001, "volume": 0.1, "trade_price_volume": 10_000_000},
            )

        self.assertEqual(volatile["block_code"], "BLOCKED_VOLATILITY_FILTER")
        self.assertEqual(low_volume["block_code"], "BLOCKED_LOW_VOLUME")

    def test_structured_liquidity_filters_block_entry(self) -> None:
        env = {
            "RISK_MIN_COOLDOWN_SECONDS": "0",
            "RISK_VOLATILITY_BLOCK_PERCENT": "2",
            "RISK_MIN_VOLUME_KRW": "0",
            "RISK_MIN_CURRENT_1M_VOLUME_KRW": "30000000",
            "RISK_MIN_AVG_5M_VOLUME_KRW": "50000000",
        }
        with patch.dict(os.environ, env, clear=False):
            low_current = check_order_risk(
                order=order(),
                purpose="ENTRY",
                base_result={"allowed": True},
                is_auto=True,
                market_snapshot={
                    "price": 100_000_000,
                    "range_rate": 0.001,
                    "liquidity_check_required": True,
                    "current_1m_trade_price_volume": 20_000_000,
                    "recent_5m_avg_trade_price_volume": 60_000_000,
                    "recent_5m_volume_count": 5,
                },
            )
            low_average = check_order_risk(
                order={**order(), "request_id": "risk-test-low-5m-volume"},
                purpose="ENTRY",
                base_result={"allowed": True},
                is_auto=True,
                market_snapshot={
                    "price": 100_000_000,
                    "range_rate": 0.001,
                    "liquidity_check_required": True,
                    "current_1m_trade_price_volume": 35_000_000,
                    "recent_5m_avg_trade_price_volume": 40_000_000,
                    "recent_5m_volume_count": 5,
                },
            )

        self.assertEqual(low_current["block_code"], "BLOCKED_LOW_1M_VOLUME")
        self.assertEqual(low_average["block_code"], "BLOCKED_LOW_5M_AVG_VOLUME")

    def test_structured_liquidity_filter_ignores_legacy_volume_when_present(self) -> None:
        env = {
            "RISK_MIN_COOLDOWN_SECONDS": "0",
            "RISK_VOLATILITY_BLOCK_PERCENT": "2",
            "RISK_MIN_VOLUME_KRW": "100000000",
            "RISK_MIN_CURRENT_1M_VOLUME_KRW": "30000000",
            "RISK_MIN_AVG_5M_VOLUME_KRW": "50000000",
        }
        with patch.dict(os.environ, env, clear=False):
            result = check_order_risk(
                order=order(),
                purpose="ENTRY",
                base_result={"allowed": True},
                is_auto=True,
                market_snapshot={
                    "price": 100_000_000,
                    "range_rate": 0.001,
                    "trade_price_volume": 35_000_000,
                    "liquidity_check_required": True,
                    "current_1m_trade_price_volume": 35_000_000,
                    "recent_5m_avg_trade_price_volume": 55_000_000,
                    "recent_5m_volume_count": 5,
                },
            )

        self.assertTrue(result["allowed"])
        self.assertEqual(result["checks"]["market_condition_check"]["allowed"], True)

    def test_policy_auto_trading_off_blocks_auto_entry_only(self) -> None:
        database.update_bot_operation_policy("KRW-BTC", {"auto_trading_enabled": False})
        with patch.dict(os.environ, {"RISK_MIN_COOLDOWN_SECONDS": "0"}, clear=False):
            entry = check_order_risk(order=order(), purpose="ENTRY", base_result={"allowed": True}, is_auto=True)
            manual = check_order_risk(order={**order(), "request_id": "manual-policy-preview"}, purpose="ENTRY", base_result={"allowed": True}, is_auto=False)
            exit_result = check_order_risk(order={**order(), "request_id": "exit-policy", "side": "SELL"}, purpose="EXIT", base_result={"allowed": True}, is_auto=True)

        self.assertFalse(entry["allowed"])
        self.assertEqual(entry["block_code"], "BLOCKED_POLICY_AUTO_TRADING_DISABLED")
        self.assertTrue(manual["checks"]["operation_policy_check"]["allowed"])
        self.assertTrue(exit_result["checks"]["operation_policy_check"]["allowed"])

    def test_policy_max_total_exposure_blocks_entry(self) -> None:
        session_id = database.create_live_strategy_session(
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
        database.create_live_position(
            {
                "session_id": session_id,
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 1,
                "strategy_name": "ma_cross",
                "status": "OPEN",
                "entry_price": 100_000_000,
                "entry_volume": 0.001,
                "entry_amount_krw": 100_000,
                "current_price": 100_000_000,
                "unrealized_pnl": 0,
                "realized_pnl": 0,
                "stop_loss_price": 0,
                "take_profit_price": 0,
                "opened_at": "2026-06-16T00:00:00Z",
            }
        )
        database.update_bot_operation_policy("KRW-BTC", {"max_total_exposure_krw": 100_000})
        with patch.dict(os.environ, {"RISK_MIN_COOLDOWN_SECONDS": "0", "RISK_BLOCK_ON_OPEN_POSITION": "false"}, clear=False):
            result = check_order_risk(order=order(), purpose="ENTRY", base_result={"allowed": True}, is_auto=True, market_snapshot={"price": 100_000_000})

        self.assertFalse(result["allowed"])
        self.assertEqual(result["block_code"], "BLOCKED_POLICY_MAX_TOTAL_EXPOSURE")
        self.assertEqual(result["operation_policy"]["current_bot_position_value_krw"], 100_000)
        self.assertEqual(result["checks"]["operation_policy_check"]["detail"]["max_total_exposure_krw"], 100_000)

        dashboard = get_risk_dashboard("bithumb", "KRW-BTC")
        latest = dashboard["latest_policy_block"]
        self.assertIsNotNone(latest)
        self.assertEqual(latest["policy_block_detail"]["code"], "BLOCKED_POLICY_MAX_TOTAL_EXPOSURE")
        self.assertEqual(latest["policy_block_detail"]["current_bot_position_value_krw"], 100_000)
        self.assertGreaterEqual(latest["policy_block_detail"]["exceeded_by_krw"], 10_000)

    def test_policy_daily_loss_blocks_entry_but_not_exit(self) -> None:
        session_id = database.create_live_strategy_session(
            {
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 1,
                "strategy_name": "ma_cross",
                "strategy_parameters": {},
                "status": "STOPPED",
                "auto_enabled": False,
                "initial_balance_krw": 0,
                "max_order_krw": 10_000,
                "max_orders_per_day": 1,
            }
        )
        database.create_live_position(
            {
                "session_id": session_id,
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 1,
                "strategy_name": "ma_cross",
                "status": "CLOSED",
                "entry_price": 100_000_000,
                "entry_volume": 0.001,
                "entry_amount_krw": 100_000,
                "current_price": 99_000_000,
                "unrealized_pnl": 0,
                "realized_pnl": -6_000,
                "stop_loss_price": 0,
                "take_profit_price": 0,
                "opened_at": "2026-06-16T00:00:00Z",
                "closed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            }
        )
        database.update_bot_operation_policy("KRW-BTC", {"max_total_exposure_krw": 100_000, "daily_loss_limit_pct": 5})
        with patch.dict(os.environ, {"RISK_MIN_COOLDOWN_SECONDS": "0", "RISK_MAX_DAILY_LOSS_KRW": "999999", "RISK_MAX_DAILY_LOSS_PERCENT": "99"}, clear=False):
            entry = check_order_risk(order=order(), purpose="ENTRY", base_result={"allowed": True}, is_auto=True)
            exit_result = check_order_risk(order={**order(), "request_id": "policy-loss-exit", "side": "SELL"}, purpose="EXIT", base_result={"allowed": True}, is_auto=True)

        self.assertFalse(entry["allowed"])
        self.assertEqual(entry["block_code"], "BLOCKED_POLICY_DAILY_LOSS_LIMIT")
        self.assertTrue(exit_result["checks"]["operation_policy_check"]["allowed"])

    def test_policy_krw_balance_blocks_entry(self) -> None:
        balances = {"by_currency": {"KRW": {"balance": 5_000, "locked": 0}}}
        with patch.dict(os.environ, {"RISK_MIN_COOLDOWN_SECONDS": "0"}, clear=False):
            result = check_order_risk(order=order(), purpose="ENTRY", base_result={"allowed": True}, balances=balances, is_auto=True)

        self.assertFalse(result["allowed"])
        self.assertEqual(result["block_code"], "BLOCKED_POLICY_KRW_BALANCE_INSUFFICIENT")

    def test_duplicate_candle_check_ignores_current_preview_request(self) -> None:
        session_id = database.create_live_strategy_session(
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
        preview_order = {**order(), "request_id": "exit-preview-test", "side": "SELL"}
        database.insert_live_order_log(
            {
                **preview_order,
                "session_id": session_id,
                "candidate_strategy_id": 1,
                "fee_estimate": 5,
                "risk_result": "ALLOWED",
                "status": "PREVIEWED",
                "error_message": None,
                "order_preview_payload": {},
                "exchange_request_payload_masked": {},
                "exchange_response_payload": {},
                "manual_confirmed": True,
                "is_auto": False,
                "is_auto_exit": False,
                "order_purpose": "EXIT",
                "candle_time_utc": "2026-06-16T00:00:00Z",
            }
        )

        with patch.dict(os.environ, {"RISK_MIN_COOLDOWN_SECONDS": "0", "RISK_BLOCK_ON_OPEN_POSITION": "false"}, clear=False):
            result = check_order_risk(
                order=preview_order,
                purpose="EXIT",
                base_result={"allowed": True},
                session_id=session_id,
                candidate_strategy_id=1,
                candle_time_utc="2026-06-16T00:00:00Z",
                is_auto=False,
            )

        self.assertTrue(result["checks"]["duplicate_check"]["allowed"])


if __name__ == "__main__":
    unittest.main()
