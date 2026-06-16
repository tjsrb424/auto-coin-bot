from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app import database
from app.risk_manager import check_order_risk, compute_risk_state


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

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.tmp.cleanup()

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
            entry = check_order_risk(order=order(), purpose="ENTRY", base_result={"allowed": True}, is_auto=True)
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


if __name__ == "__main__":
    unittest.main()
