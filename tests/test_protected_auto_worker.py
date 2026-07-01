from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database
from app.protected_auto_worker import (
    PROTECTED_AUTO_RUNTIME_ID,
    PROTECTED_MAX_NOTIONAL_KRW,
    load_protected_auto_state,
    _current_epoch_with_exchange_equity,
    protected_auto_safe_stop,
    protected_auto_status,
    run_protected_auto_startup_recovery,
    start_protected_auto_daemon,
)


def current_epoch() -> dict:
    return {
        "current_epoch_exists": True,
        "current_epoch_id": 1,
        "current_epoch_started_at_utc": "2026-07-01T00:00:00Z",
        "current_epoch_current_equity": 300_000.0,
        "current_epoch_total_pnl": 0.0,
        "current_epoch_realized_pnl": 0.0,
        "current_epoch_unrealized_pnl": 0.0,
        "current_epoch_accounting_pending_count": 0,
        "current_epoch_accounting_failed_count": 0,
        "current_epoch_sanity_passed": True,
        "current_epoch_trust_level": "HIGH",
    }


class ProtectedAutoWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        database.init_db()
        database.update_bot_operation_policy(
            "KRW-BTC",
            {"auto_trading_enabled": False, "max_total_exposure_krw": 300_000, "daily_loss_limit_pct": 3},
        )

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_start_protected_daemon_keeps_general_auto_stopped(self) -> None:
        result = start_protected_auto_daemon(
            exchange="bithumb",
            symbols=["BTC", "ETH", "WLD"],
            amount_krw=10_000,
            scan_interval_seconds=60,
            max_holding_minutes=10,
            max_position_trades=3,
            current_epoch=current_epoch(),
            gate={"protected_full_auto_live_allowed": True},
        )

        self.assertTrue(result["ok"])
        state = load_protected_auto_state()
        self.assertEqual(state["worker_status"], "RUNNING")
        self.assertEqual(state["session_status"], "RUNNING")
        self.assertEqual(state["symbols"], ["BTC", "ETH"])
        self.assertEqual(state["amount_krw"], PROTECTED_MAX_NOTIONAL_KRW)
        self.assertEqual(state["max_position_trades"], 1)
        self.assertFalse(database.load_global_bot_operation_policy()["auto_trading_enabled"])
        self.assertEqual(database.load_runtime_lock("auto-trading"), None)
        self.assertEqual(database.load_runtime_lock("protected-full-auto-live-v1")["status"], "RUNNING")

    def test_status_marks_missing_heartbeat_as_stale(self) -> None:
        start_protected_auto_daemon(
            exchange="bithumb",
            symbols=["BTC"],
            amount_krw=6000,
            scan_interval_seconds=60,
            max_holding_minutes=10,
            max_position_trades=1,
            current_epoch=current_epoch(),
            gate={"protected_full_auto_live_allowed": True},
        )
        with database.get_connection() as conn:
            conn.execute(
                """
                UPDATE protected_auto_runtime
                SET last_heartbeat_at_utc = '2026-06-30T00:00:00Z'
                WHERE runtime_id = ?
                """,
                (PROTECTED_AUTO_RUNTIME_ID,),
            )

        status = protected_auto_status()

        self.assertTrue(status["stale"])
        self.assertEqual(status["protected_worker_status"], "STALE")

    def test_safe_stop_records_stop_reason_and_releases_lock(self) -> None:
        start_protected_auto_daemon(
            exchange="bithumb",
            symbols=["BTC"],
            amount_krw=6000,
            scan_interval_seconds=60,
            max_holding_minutes=10,
            max_position_trades=1,
            current_epoch=current_epoch(),
            gate={"protected_full_auto_live_allowed": True},
        )

        stopped = protected_auto_safe_stop("TEST_STOP")

        self.assertEqual(stopped["protected_auto_runtime_status"], "STOPPED")
        self.assertEqual(stopped["stop_reason"], "TEST_STOP")
        self.assertEqual(database.load_runtime_lock("protected-full-auto-live-v1")["status"], "STOPPED")

    def test_startup_recovery_clears_expired_lock_without_active_daemon(self) -> None:
        with database.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO runtime_locks (
                    lock_id, instance_id, hostname, app_env, runtime_owner,
                    status, acquired_at, expires_at, updated_at
                ) VALUES ('protected-full-auto-live-v1', 'old', 'old-host', 'production',
                    'protected-full-auto-live-v1', 'RUNNING',
                    '2026-06-30T00:00:00Z', '2026-06-30T01:00:00Z', '2026-06-30T00:00:00Z')
                """
            )

        recovery = run_protected_auto_startup_recovery()

        self.assertEqual(recovery["action"], "NO_ACTIVE_PROTECTED_DAEMON")
        self.assertEqual(database.load_runtime_lock("protected-full-auto-live-v1")["status"], "STOPPED")
        self.assertEqual(protected_auto_status()["protected_runtime_lock_status"], "STOPPED")

    def test_worker_epoch_diagnostics_use_exchange_equity_snapshot(self) -> None:
        database.create_accounting_epoch(
            {
                "exchange_name": "bithumb",
                "epoch_id": "epoch-worker",
                "epoch_started_at_utc": "2026-07-01T00:00:00Z",
                "starting_exchange_equity": 300_000,
                "starting_cash_krw": 300_000,
                "starting_positions": [],
                "cost_basis_policy": "MARK_TO_MARKET",
                "epoch_trust_level": "MEDIUM",
            }
        )

        with patch("app.protected_auto_worker._current_equity", return_value=300_000):
            report = __import__("asyncio").run(_current_epoch_with_exchange_equity("bithumb"))

        self.assertTrue(report["current_epoch_sanity_passed"])
        self.assertEqual(report["current_epoch_current_equity"], 300_000)
