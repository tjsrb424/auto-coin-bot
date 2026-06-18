from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app import database
from app.live_recovery import (
    apply_reconciled_order_status,
    ensure_filled_entry_order_positions,
    is_timeout_exception,
    normalize_exchange_order,
    reconcile_balances,
    run_startup_live_recovery_async,
)


def order_log(request_id: str = "strategy-test") -> dict:
    return {
        "request_id": request_id,
        "session_id": 1,
        "candidate_strategy_id": 1,
        "exchange": "bithumb",
        "market": "KRW-BTC",
        "side": "BUY",
        "order_type": "LIMIT",
        "price": 100_000_000,
        "volume": 0.0001,
        "amount_krw": 10_000,
        "fee_estimate": 5,
        "risk_result": "ALLOWED",
        "order_preview_payload": {},
        "exchange_request_payload_masked": {},
        "exchange_response_payload": {},
        "status": "SUBMITTED",
        "order_uuid": "order-1",
        "strategy_name": "ma_cross",
        "candle_time_utc": "2026-06-16T00:00:00Z",
    }


def create_strategy_session() -> int:
    return database.create_live_strategy_session(
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
            "max_orders_per_day": 0,
        }
    )


class LiveRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        database.init_db()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_normalize_partial_fill_records_execution_amounts(self) -> None:
        status = normalize_exchange_order(
            {
                "uuid": "order-1",
                "state": "wait",
                "price": "100000000",
                "volume": "0.0002",
                "executed_volume": "0.0001",
                "remaining_volume": "0.0001",
                "paid_fee": "5",
            }
        )

        self.assertEqual(status.status, "PARTIALLY_FILLED")
        self.assertEqual(status.executed_volume, 0.0001)
        self.assertEqual(status.remaining_volume, 0.0001)
        self.assertEqual(status.filled_amount_krw, 10_000)
        self.assertEqual(status.paid_fee, 5)

    def test_apply_reconciled_status_updates_live_order_log(self) -> None:
        database.insert_live_order_log(order_log())
        current = database.get_live_order_log("strategy-test")
        assert current is not None

        apply_reconciled_order_status(
            current,
            normalize_exchange_order(
                {
                    "uuid": "order-1",
                    "state": "wait",
                    "price": "100000000",
                    "volume": "0.0002",
                    "executed_volume": "0.0001",
                    "remaining_volume": "0.0001",
                }
            ),
            "TEST_RECONCILE",
        )

        updated = database.get_live_order_log("strategy-test")
        assert updated is not None
        self.assertEqual(updated["status"], "PARTIALLY_FILLED")
        self.assertEqual(updated["risk_result"], "PARTIAL_FILL_REQUIRES_RECOVERY")
        self.assertEqual(updated["executed_volume"], 0.0001)
        self.assertEqual(updated["remaining_volume"], 0.0001)
        self.assertEqual(updated["filled_amount_krw"], 10_000)
        self.assertEqual(database.load_live_recovery_events(1)[0]["event_type"], "TEST_RECONCILE")

    def test_filled_entry_reconciliation_creates_and_links_position(self) -> None:
        session_id = create_strategy_session()
        database.insert_live_order_log({**order_log(), "session_id": session_id})
        current = database.get_live_order_log("strategy-test")
        assert current is not None

        apply_reconciled_order_status(
            current,
            normalize_exchange_order(
                {
                    "uuid": "order-1",
                    "state": "done",
                    "price": "100000000",
                    "volume": "0.0001",
                    "executed_volume": "0.0001",
                    "remaining_volume": "0",
                }
            ),
            "TEST_RECONCILE",
        )

        updated = database.get_live_order_log("strategy-test")
        assert updated is not None
        self.assertEqual(updated["status"], "FILLED")
        self.assertIsNotNone(updated["position_id"])
        positions = database.load_open_live_positions("bithumb", "KRW-BTC")
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["entry_order_uuid"], "order-1")
        self.assertEqual(database.load_latest_live_strategy_session()["current_position_id"], updated["position_id"])

        self.assertEqual(ensure_filled_entry_order_positions("bithumb", "KRW-BTC"), {"created": 0, "attached": 0, "skipped": 0})
        self.assertEqual(len(database.load_open_live_positions("bithumb", "KRW-BTC")), 1)

    async def test_startup_recovery_pauses_running_sessions(self) -> None:
        database.create_auto_live_pilot_session(
            {
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "strategy_name": "ma_cross",
                "status": "RUNNING",
                "auto_enabled": True,
                "order_amount_krw": 10_000,
                "max_orders_per_day": 1,
            }
        )
        database.create_live_strategy_session(
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

        with patch("app.live_recovery.sync_open_orders", new=AsyncMock(return_value={"status": "SUCCESS"})):
            result = await run_startup_live_recovery_async()

        self.assertEqual(result["paused_auto_sessions"], 1)
        self.assertEqual(result["paused_strategy_sessions"], 1)
        self.assertEqual(database.load_latest_auto_live_pilot_session()["status"], "LIVE_PAUSED")
        self.assertEqual(database.load_latest_live_strategy_session()["status"], "LIVE_PAUSED")

    async def test_balance_mismatch_blocks_auto_orders(self) -> None:
        broker = AsyncMock()
        broker.get_balances.return_value = {
            "by_currency": {"BTC": {"balance": 0.01, "locked": 0.0}},
            "btc": {"balance": 0.01, "locked": 0.0},
            "krw": {"balance": 0.0, "locked": 0.0},
        }

        with patch("app.live_recovery.get_live_broker", return_value=broker):
            result = await reconcile_balances("bithumb", "KRW-BTC")

        self.assertEqual(result["status"], "BALANCE_MISMATCH")
        self.assertTrue(result["blocking"])
        self.assertEqual(database.load_live_recovery_events(1)[0]["event_type"], "BALANCE_MISMATCH")

    async def test_balance_reconciliation_recovers_filled_entry_before_mismatch_check(self) -> None:
        session_id = create_strategy_session()
        database.insert_live_order_log(
            {
                **order_log("filled-entry"),
                "session_id": session_id,
                "status": "FILLED",
                "executed_volume": 0.0001,
                "remaining_volume": 0.0,
                "filled_amount_krw": 10_000,
            }
        )
        broker = AsyncMock()
        broker.get_balances.return_value = {
            "by_currency": {"BTC": {"balance": 0.0001, "locked": 0.0}},
            "btc": {"balance": 0.0001, "locked": 0.0},
            "krw": {"balance": 0.0, "locked": 0.0},
        }

        with patch("app.live_recovery.get_live_broker", return_value=broker):
            result = await reconcile_balances("bithumb", "KRW-BTC")

        self.assertEqual(result["status"], "OK")
        self.assertFalse(result["blocking"])
        self.assertEqual(result["position_sync"]["created"], 1)
        updated = database.get_live_order_log("filled-entry")
        assert updated is not None
        self.assertIsNotNone(updated["position_id"])

    def test_timeout_exception_detection_blocks_retry_path(self) -> None:
        self.assertTrue(is_timeout_exception(httpx.ReadTimeout("timed out")))
        self.assertTrue(is_timeout_exception(RuntimeError("request timeout")))
        self.assertFalse(is_timeout_exception(RuntimeError("permission denied")))


if __name__ == "__main__":
    unittest.main()
