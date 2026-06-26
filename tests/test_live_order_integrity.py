from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database
from app.live_strategy_pilot import _entry_order_execution_preflight


class LiveOrderIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.env_patch = patch.dict("os.environ", {"DATABASE_URL": ""}, clear=False)
        self.env_patch.start()
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        database.init_db()
        database.update_bot_operation_policy(
            "KRW-BTC",
            {"auto_trading_enabled": True, "max_total_exposure_krw": 300_000, "daily_loss_limit_pct": 3},
        )
        database.acquire_runtime_lock(
            lock_id="auto-trading",
            instance_id="test-instance",
            hostname="test-host",
            app_env="test",
            runtime_owner="test",
            ttl_seconds=300,
        )
        self.session_id = database.create_live_strategy_session(
            {
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 7,
                "strategy_name": "volatility_breakout",
                "strategy_parameters": {},
                "status": "RUNNING",
                "auto_enabled": True,
                "initial_balance_krw": 0,
                "max_order_krw": 30_000,
                "max_orders_per_day": 10,
            }
        )

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.env_patch.stop()
        self.tmp.cleanup()

    def test_expired_reservation_is_blocked_before_order_execution(self) -> None:
        database.create_order_reservation(
            {
                "request_id": "reservation-expired",
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 7,
                "amount_krw": 30_000,
                "status": "PENDING",
                "expires_at_utc": "2000-01-01T00:00:00Z",
            }
        )
        session = database.load_latest_live_strategy_session()
        assert session is not None

        allowed, reason, reservation = _entry_order_execution_preflight(session, "KRW-BTC")

        self.assertFalse(allowed)
        self.assertEqual(reason, "BLOCKED_EXPIRED_RESERVATION")
        self.assertIsNotNone(reservation)
        with database.get_connection() as conn:
            row = conn.execute("SELECT status FROM order_reservations WHERE request_id = ?", ("reservation-expired",)).fetchone()
        self.assertEqual(row["status"], "EXPIRED")

    def test_diagnostic_gate_failure_blocks_preflight(self) -> None:
        database.insert_live_order_log(
            {
                "request_id": "dup-1",
                "session_id": self.session_id,
                "candidate_strategy_id": 7,
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "side": "BUY",
                "order_type": "LIMIT",
                "price": 100,
                "volume": 1,
                "amount_krw": 100,
                "fee_estimate": 0,
                "risk_result": "ALLOWED",
                "status": "FILLED",
                "order_uuid": "dup-order",
                "strategy_name": "volatility_breakout",
                "candle_time_utc": "2026-06-25T00:00:00Z",
            }
        )
        database.insert_live_order_log(
            {
                "request_id": "dup-1-filled-event",
                "session_id": self.session_id,
                "candidate_strategy_id": 7,
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "side": "BUY",
                "order_type": "LIMIT",
                "price": 100,
                "volume": 1,
                "amount_krw": 100,
                "fee_estimate": 0,
                "risk_result": "ALLOWED",
                "status": "FILLED",
                "order_uuid": "dup-order",
                "strategy_name": "volatility_breakout",
                "candle_time_utc": "2026-06-25T00:00:00Z",
            }
        )
        database.create_order_reservation(
            {
                "request_id": "reservation-active",
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 7,
                "amount_krw": 30_000,
                "status": "PENDING",
                "expires_at_utc": "2099-01-01T00:00:00Z",
            }
        )
        session = database.load_latest_live_strategy_session()
        assert session is not None

        allowed, reason, _reservation = _entry_order_execution_preflight(session, "KRW-BTC")

        self.assertFalse(allowed)
        self.assertEqual(reason, "BLOCKED_DIAGNOSTIC_GATE_FAILED")


if __name__ == "__main__":
    unittest.main()
