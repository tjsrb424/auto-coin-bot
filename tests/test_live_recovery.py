from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app import database
from app.live_recovery import (
    apply_reconciled_order_status,
    ensure_filled_entry_order_positions,
    ensure_filled_entry_order_position,
    is_timeout_exception,
    normalize_exchange_order,
    reconcile_balances,
    reconcile_order_log,
    run_startup_live_recovery_async,
    sync_open_orders,
)
from app.scale_in_repair import repair_scale_in_duplicate
from app.risk_manager import compute_risk_state


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


def live_position(session_id: int, **overrides: object) -> dict:
    position = {
        "session_id": session_id,
        "exchange": "bithumb",
        "market": "KRW-BTC",
        "candidate_strategy_id": 1,
        "strategy_name": "ma_cross",
        "status": "OPEN",
        "entry_order_uuid": "entry-1",
        "entry_price": 100_000_000,
        "entry_volume": 0.0001,
        "entry_amount_krw": 10_000,
        "current_price": 100_000_000,
        "unrealized_pnl": 0.0,
        "realized_pnl": 0.0,
        "stop_loss_price": 99_000_000,
        "take_profit_price": 101_000_000,
        "opened_at": "2026-06-16T00:00:00Z",
    }
    position.update(overrides)
    return position


class LiveRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.env_patch = patch.dict(os.environ, {"DATABASE_URL": ""}, clear=False)
        self.env_patch.start()
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        database.init_db()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.env_patch.stop()
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

    def test_normalize_waiting_order_does_not_treat_locked_funds_as_filled(self) -> None:
        status = normalize_exchange_order(
            {
                "uuid": "order-1",
                "state": "wait",
                "price": "323",
                "volume": "309.59752321",
                "executed_volume": "0",
                "remaining_volume": "309.59752321",
                "locked": "100250.99999683",
            }
        )

        self.assertEqual(status.status, "WAITING")
        self.assertEqual(status.executed_volume, 0.0)
        self.assertEqual(status.remaining_volume, 309.59752321)
        self.assertEqual(status.filled_amount_krw, 0.0)

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

    def test_filled_exit_reconciliation_closes_position(self) -> None:
        session_id = create_strategy_session()
        position_id = database.create_live_position(
            {
                "session_id": session_id,
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 1,
                "strategy_name": "ma_cross",
                "status": "CLOSING",
                "entry_order_uuid": "entry-1",
                "exit_order_uuid": "exit-1",
                "entry_price": 100_000_000,
                "entry_volume": 0.0001,
                "entry_amount_krw": 10_000,
                "current_price": 100_000_000,
            }
        )
        database.insert_live_order_log(
            {
                **order_log("exit-test"),
                "session_id": session_id,
                "side": "SELL",
                "status": "SUBMITTED",
                "order_uuid": "exit-1",
                "position_id": position_id,
                "order_purpose": "EXIT",
            }
        )
        current = database.get_live_order_log("exit-test")
        assert current is not None

        apply_reconciled_order_status(
            current,
            normalize_exchange_order(
                {
                    "uuid": "exit-1",
                    "state": "done",
                    "price": "101000000",
                    "volume": "0.0001",
                    "executed_volume": "0.0001",
                    "remaining_volume": "0",
                    "paid_fee": "4",
                }
            ),
            "TEST_EXIT_RECONCILE",
        )

        updated = database.get_live_order_log("exit-test")
        assert updated is not None
        self.assertEqual(updated["status"], "FILLED")
        self.assertEqual(updated["actual_pnl"], 96)
        position = database.load_live_position(position_id)
        assert position is not None
        self.assertEqual(position["status"], "CLOSED")
        self.assertEqual(position["realized_pnl"], 96)
        self.assertIsNotNone(position["closed_at"])
        self.assertEqual(database.load_open_live_positions("bithumb", "KRW-BTC"), [])

    def test_filled_partial_exit_reconciliation_reduces_position(self) -> None:
        session_id = create_strategy_session()
        position_id = database.create_live_position(
            {
                "session_id": session_id,
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 1,
                "strategy_name": "ma_cross",
                "status": "CLOSING",
                "entry_order_uuid": "entry-1",
                "exit_order_uuid": "exit-1",
                "entry_price": 100_000_000,
                "entry_volume": 0.0002,
                "entry_amount_krw": 20_000,
                "current_price": 100_000_000,
            }
        )
        database.insert_live_order_log(
            {
                **order_log("partial-exit-test"),
                "session_id": session_id,
                "side": "SELL",
                "status": "SUBMITTED",
                "order_uuid": "exit-1",
                "position_id": position_id,
                "order_purpose": "EXIT",
            }
        )
        current = database.get_live_order_log("partial-exit-test")
        assert current is not None

        apply_reconciled_order_status(
            current,
            normalize_exchange_order(
                {
                    "uuid": "exit-1",
                    "state": "done",
                    "price": "101000000",
                    "volume": "0.00015",
                    "executed_volume": "0.00015",
                    "remaining_volume": "0",
                    "paid_fee": "4",
                }
            ),
            "TEST_PARTIAL_EXIT_RECONCILE",
        )

        updated = database.get_live_order_log("partial-exit-test")
        assert updated is not None
        self.assertEqual(updated["actual_pnl"], 146)
        position = database.load_live_position(position_id)
        assert position is not None
        self.assertEqual(position["status"], "OPEN")
        self.assertAlmostEqual(position["entry_volume"], 0.00005)
        self.assertAlmostEqual(position["entry_amount_krw"], 5_000)
        self.assertEqual(position["realized_pnl"], 146)
        self.assertIsNone(position["exit_order_uuid"])
        self.assertEqual(len(database.load_open_live_positions("bithumb", "KRW-BTC")), 1)

    def test_filled_aggregate_exit_reconciliation_closes_multiple_positions(self) -> None:
        session_id = create_strategy_session()
        first_position_id = database.create_live_position(
            {
                "session_id": session_id,
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 1,
                "strategy_name": "ma_cross",
                "status": "CLOSING",
                "entry_order_uuid": "entry-1",
                "exit_order_uuid": "exit-aggregate",
                "entry_price": 100_000_000,
                "entry_volume": 0.0001,
                "entry_amount_krw": 10_000,
                "current_price": 100_000_000,
            }
        )
        second_position_id = database.create_live_position(
            {
                "session_id": session_id,
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 1,
                "strategy_name": "ma_cross",
                "status": "CLOSING",
                "entry_order_uuid": "entry-2",
                "exit_order_uuid": "exit-aggregate",
                "entry_price": 100_000_000,
                "entry_volume": 0.00002,
                "entry_amount_krw": 2_000,
                "current_price": 100_000_000,
            }
        )
        database.insert_live_order_log(
            {
                **order_log("aggregate-exit-test"),
                "session_id": session_id,
                "side": "SELL",
                "status": "SUBMITTED",
                "order_uuid": "exit-aggregate",
                "position_id": second_position_id,
                "order_purpose": "EXIT",
                "order_preview_payload": {
                    "aggregate_exit": True,
                    "aggregate_exit_position_ids": [first_position_id, second_position_id],
                },
            }
        )
        current = database.get_live_order_log("aggregate-exit-test")
        assert current is not None

        apply_reconciled_order_status(
            current,
            normalize_exchange_order(
                {
                    "uuid": "exit-aggregate",
                    "state": "done",
                    "price": "101000000",
                    "volume": "0.00012",
                    "executed_volume": "0.00012",
                    "remaining_volume": "0",
                    "paid_fee": "5",
                }
            ),
            "TEST_AGGREGATE_EXIT_RECONCILE",
        )

        first = database.load_live_position(first_position_id)
        second = database.load_live_position(second_position_id)
        assert first is not None
        assert second is not None
        self.assertEqual(first["status"], "CLOSED")
        self.assertEqual(second["status"], "CLOSED")
        self.assertEqual(database.load_open_live_positions("bithumb", "KRW-BTC"), [])

    def test_partially_filled_exit_reconciliation_reduces_position_and_keeps_closing(self) -> None:
        session_id = create_strategy_session()
        position_id = database.create_live_position(
            {
                "session_id": session_id,
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 1,
                "strategy_name": "ma_cross",
                "status": "CLOSING",
                "entry_order_uuid": "entry-1",
                "exit_order_uuid": "exit-1",
                "entry_price": 100_000_000,
                "entry_volume": 0.0002,
                "entry_amount_krw": 20_000,
                "current_price": 100_000_000,
            }
        )
        database.insert_live_order_log(
            {
                **order_log("partial-wait-exit-test"),
                "session_id": session_id,
                "side": "SELL",
                "status": "SUBMITTED",
                "order_uuid": "exit-1",
                "position_id": position_id,
                "order_purpose": "EXIT",
            }
        )
        current = database.get_live_order_log("partial-wait-exit-test")
        assert current is not None

        apply_reconciled_order_status(
            current,
            normalize_exchange_order(
                {
                    "uuid": "exit-1",
                    "state": "wait",
                    "price": "101000000",
                    "volume": "0.0001",
                    "executed_volume": "0.00005",
                    "remaining_volume": "0.00005",
                    "paid_fee": "2",
                }
            ),
            "TEST_PARTIAL_WAIT_EXIT_RECONCILE",
        )

        updated = database.get_live_order_log("partial-wait-exit-test")
        assert updated is not None
        self.assertEqual(updated["status"], "PARTIALLY_FILLED")
        position = database.load_live_position(position_id)
        assert position is not None
        self.assertEqual(position["status"], "CLOSING")
        self.assertAlmostEqual(position["entry_volume"], 0.00015)
        self.assertEqual(position["exit_order_uuid"], "exit-1")

    async def test_sync_open_orders_reconciles_submitted_exit_without_session_open_uuid(self) -> None:
        session_id = create_strategy_session()
        database.update_live_strategy_session(session_id, {"current_open_order_uuid": None})
        position_id = database.create_live_position(
            {
                "session_id": session_id,
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 1,
                "strategy_name": "ma_cross",
                "status": "CLOSING",
                "entry_order_uuid": "entry-1",
                "exit_order_uuid": "exit-1",
                "entry_price": 100_000_000,
                "entry_volume": 0.0002,
                "entry_amount_krw": 20_000,
                "current_price": 100_000_000,
            }
        )
        database.insert_live_order_log(
            {
                **order_log("exit-sync"),
                "session_id": session_id,
                "side": "SELL",
                "status": "SUBMITTED",
                "order_uuid": "exit-1",
                "position_id": position_id,
                "order_purpose": "EXIT",
            }
        )
        broker = AsyncMock()
        broker.list_open_orders.return_value = {"orders": []}
        broker.get_order.return_value = {
            "uuid": "exit-1",
            "state": "done",
            "price": "101000000",
            "volume": "0.00015",
            "executed_volume": "0.00015",
            "remaining_volume": "0",
            "paid_fee": "4",
        }

        with patch("app.live_recovery.get_live_broker", return_value=broker):
            result = await sync_open_orders("bithumb", "KRW-BTC")

        self.assertEqual(result["reconciled_count"], 1)
        broker.get_order.assert_awaited_once_with("exit-1")
        updated = database.get_live_order_log("exit-sync")
        assert updated is not None
        self.assertEqual(updated["status"], "FILLED")
        self.assertEqual(updated["risk_result"], "EXCHANGE_FILLED_SYNCED")
        position = database.load_live_position(position_id)
        assert position is not None
        self.assertEqual(position["status"], "OPEN")
        self.assertAlmostEqual(position["entry_volume"], 0.00005)
        self.assertIsNone(position["exit_order_uuid"])
        risk = compute_risk_state("bithumb", "KRW-BTC")
        self.assertEqual(risk["open_order_count"], 0)

    async def test_sync_open_orders_dedupes_unchanged_open_order_events(self) -> None:
        database.insert_live_order_log(order_log("open-sync-dedupe"))
        broker = AsyncMock()
        broker.list_open_orders.return_value = {
            "orders": [
                {
                    "uuid": "order-1",
                    "state": "wait",
                    "price": "100000000",
                    "volume": "0.0001",
                    "executed_volume": "0",
                    "remaining_volume": "0.0001",
                }
            ]
        }

        with patch("app.live_recovery.get_live_broker", return_value=broker):
            first = await sync_open_orders("bithumb", "KRW-BTC")
            second = await sync_open_orders("bithumb", "KRW-BTC")

        self.assertEqual(first["reconciled_count"], 1)
        self.assertEqual(second["reconciled_count"], 1)
        events = database.load_live_recovery_events(10)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "OPEN_ORDER_SYNC")

    async def test_sync_open_orders_uses_detail_reconciled_event_for_missing_open_order(self) -> None:
        database.insert_live_order_log(order_log("detail-reconcile"))
        broker = AsyncMock()
        broker.list_open_orders.return_value = {"orders": []}
        broker.get_order.return_value = {
            "uuid": "order-1",
            "state": "wait",
            "price": "100000000",
            "volume": "0.0001",
            "executed_volume": "0",
            "remaining_volume": "0.0001",
        }

        with patch("app.live_recovery.get_live_broker", return_value=broker):
            result = await sync_open_orders("bithumb", "KRW-BTC")

        self.assertEqual(result["reconciled_count"], 1)
        events = database.load_live_recovery_events(1)
        self.assertEqual(events[0]["event_type"], "OPEN_ORDER_DETAIL_RECONCILED")

    async def test_order_not_found_marks_pending_order_stale_without_deleting(self) -> None:
        database.insert_live_order_log(order_log("missing-order"))
        current = database.get_live_order_log("missing-order")
        assert current is not None
        broker = AsyncMock()
        broker.get_order.side_effect = RuntimeError("404 order_not_found")

        with patch("app.live_recovery.get_live_broker", return_value=broker):
            status = await reconcile_order_log(current, source="TEST_MISSING_ORDER")

        self.assertEqual(status.status, "STALE_CANCELED")
        updated = database.get_live_order_log("missing-order")
        assert updated is not None
        self.assertEqual(updated["status"], "STALE_CANCELED")
        self.assertEqual(updated["risk_result"], "STALE_CANCELED")
        self.assertEqual(updated["order_uuid"], "order-1")
        self.assertEqual(database.load_live_recovery_events(1)[0]["event_type"], "ORDER_NOT_FOUND_STALE_CANCELED")

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

    async def test_balance_reconciliation_uses_market_symbol(self) -> None:
        broker = AsyncMock()
        broker.get_balances.return_value = {
            "by_currency": {
                "BTC": {"balance": 0.01, "locked": 0.0},
                "STRAX": {"balance": 0.0, "locked": 0.0},
            },
            "btc": {"balance": 0.01, "locked": 0.0},
            "krw": {"balance": 0.0, "locked": 0.0},
        }

        with patch("app.live_recovery.get_live_broker", return_value=broker):
            result = await reconcile_balances("bithumb", "KRW-STRAX")

        self.assertEqual(result["status"], "OK")
        self.assertFalse(result["blocking"])
        self.assertEqual(result["symbol"], "STRAX")
        self.assertEqual(result["exchange_asset_total"], 0.0)
        self.assertEqual(database.load_live_recovery_events(1), [])

    async def test_balance_mismatch_event_is_deduped(self) -> None:
        broker = AsyncMock()
        broker.get_balances.return_value = {
            "by_currency": {"BTC": {"balance": 0.01, "locked": 0.0}},
            "btc": {"balance": 0.01, "locked": 0.0},
            "krw": {"balance": 0.0, "locked": 0.0},
        }

        with patch("app.live_recovery.get_live_broker", return_value=broker):
            first = await reconcile_balances("bithumb", "KRW-BTC")
            second = await reconcile_balances("bithumb", "KRW-BTC")

        self.assertEqual(first["status"], "BALANCE_MISMATCH")
        self.assertEqual(second["status"], "BALANCE_MISMATCH")
        events = database.load_live_recovery_events(10)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "BALANCE_MISMATCH")

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

    def test_scale_in_filled_entry_merges_existing_position(self) -> None:
        old_session_id = create_strategy_session()
        target_position_id = database.create_live_position(live_position(old_session_id))
        scale_session_id = create_strategy_session()
        preview = {
            "policy_preview": {
                "scale_in": {
                    "scale_in": True,
                    "allowed": True,
                    "position_id": target_position_id,
                    "blockers": [],
                }
            }
        }
        database.insert_live_order_log(
            {
                **order_log("scale-in-filled"),
                "session_id": scale_session_id,
                "status": "FILLED",
                "order_uuid": "scale-in-order",
                "price": 100_000_000,
                "volume": 0.00005,
                "executed_volume": 0.00005,
                "remaining_volume": 0.0,
                "filled_amount_krw": 5_000,
                "order_preview_payload": preview,
            }
        )
        log = database.get_live_order_log("scale-in-filled")
        assert log is not None

        result = ensure_filled_entry_order_position(log)

        self.assertEqual(result, "ATTACHED")
        positions = database.load_open_live_positions("bithumb", "KRW-BTC")
        self.assertEqual(len(positions), 1)
        updated = database.load_live_position(target_position_id)
        assert updated is not None
        self.assertAlmostEqual(updated["entry_volume"], 0.00015)
        self.assertAlmostEqual(updated["entry_amount_krw"], 15_000)
        self.assertEqual(updated["scale_in_count"], 1)
        self.assertIsNotNone(updated["last_scale_in_at"])
        order = database.get_live_order_log("scale-in-filled")
        assert order is not None
        self.assertEqual(order["position_id"], target_position_id)

    def test_scale_in_duplicate_uuid_is_idempotent(self) -> None:
        session_id = create_strategy_session()
        target_position_id = database.create_live_position(live_position(session_id))
        preview = {
            "policy_preview": {
                "scale_in": {
                    "scale_in": True,
                    "allowed": True,
                    "position_id": target_position_id,
                    "blockers": [],
                }
            }
        }
        base = {
            **order_log("scale-in-canonical"),
            "session_id": session_id,
            "status": "FILLED",
            "order_uuid": "scale-in-dup",
            "price": 100_000_000,
            "volume": 0.00005,
            "executed_volume": 0.00005,
            "remaining_volume": 0.0,
            "filled_amount_krw": 5_000,
            "order_preview_payload": preview,
        }
        database.insert_live_order_log(base)
        database.insert_live_order_log({**base, "request_id": "scale-in-canonical-filled-1", "position_id": None})
        database.insert_live_order_log({**base, "request_id": "scale-in-canonical-filled-2", "position_id": None})

        for request_id in ["scale-in-canonical", "scale-in-canonical-filled-1", "scale-in-canonical-filled-2"]:
            log = database.get_live_order_log(request_id)
            assert log is not None
            ensure_filled_entry_order_position(log)

        updated = database.load_live_position(target_position_id)
        assert updated is not None
        self.assertAlmostEqual(updated["entry_volume"], 0.00015)
        self.assertAlmostEqual(updated["entry_amount_krw"], 15_000)
        self.assertEqual(updated["scale_in_count"], 1)
        for request_id in ["scale-in-canonical", "scale-in-canonical-filled-1", "scale-in-canonical-filled-2"]:
            log = database.get_live_order_log(request_id)
            assert log is not None
            self.assertEqual(log["position_id"], target_position_id)

    async def test_duplicate_repair_preview_marks_inactive_ask_accumulator(self) -> None:
        session_id = create_strategy_session()
        target_position_id = database.create_live_position(live_position(session_id, entry_order_uuid="target-entry"))
        duplicate_uuid = "duplicate-entry"
        duplicate_a = database.create_live_position(live_position(session_id, entry_order_uuid=duplicate_uuid))
        database.create_live_position(live_position(session_id, entry_order_uuid=duplicate_uuid, status="CLOSED", closed_at="2026-06-16T01:00:00Z"))
        preview = {"policy_preview": {"scale_in": {"scale_in": True, "allowed": True, "position_id": target_position_id}}}
        database.insert_live_order_log(
            {
                **order_log("duplicate-entry-log"),
                "session_id": session_id,
                "status": "FILLED",
                "order_uuid": duplicate_uuid,
                "executed_volume": 0.0001,
                "filled_amount_krw": 10_000,
                "order_preview_payload": preview,
            }
        )
        database.update_live_strategy_session(session_id, {"status": "LIVE_PAUSED", "auto_enabled": False})
        database.upsert_rebalance_delta_accumulator(
            session_id=session_id,
            candidate_strategy_id=1,
            exchange="bithumb",
            market="KRW-BTC",
            side="ASK",
            delta_krw=20_000,
        )
        broker = AsyncMock()
        broker.get_balances.return_value = {"by_currency": {"BTC": {"balance": 0.0001, "locked": 0.0}}}

        with patch("app.scale_in_repair.get_live_broker", return_value=broker):
            result = await repair_scale_in_duplicate(exchange="bithumb", market="KRW-BTC", dry_run=True)

        self.assertTrue(result["dry_run"])
        self.assertEqual(result["duplicate_groups"][0]["duplicate_entry_order_uuid"], duplicate_uuid)
        self.assertEqual(result["duplicate_groups"][0]["target_scale_in_position_id"], target_position_id)
        self.assertEqual(result["duplicate_groups"][0]["duplicate_positions_to_close_or_reconcile"][0]["id"], duplicate_a)
        self.assertEqual(result["inactive_ask_accumulators_to_stale"][0]["side"], "ASK")

    def test_timeout_exception_detection_blocks_retry_path(self) -> None:
        self.assertTrue(is_timeout_exception(httpx.ReadTimeout("timed out")))
        self.assertTrue(is_timeout_exception(RuntimeError("request timeout")))
        self.assertFalse(is_timeout_exception(RuntimeError("permission denied")))


if __name__ == "__main__":
    unittest.main()
