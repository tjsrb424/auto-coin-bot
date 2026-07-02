from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import database
from app.main import app
from app.protected_auto_worker import start_protected_auto_daemon, protected_auto_status
from app import protected_gate_snapshot as gate_snapshot


def current_epoch() -> dict:
    return {
        "current_epoch_exists": True,
        "current_epoch_id": "epoch-test",
        "current_epoch_started_at_utc": "2026-07-01T00:00:00Z",
        "current_epoch_current_equity": 300_000.0,
        "current_epoch_starting_equity": 300_000.0,
        "current_epoch_total_pnl": 0.0,
        "current_epoch_accounting_pending_count": 0,
        "current_epoch_accounting_failed_count": 0,
        "current_epoch_sanity_passed": True,
        "current_epoch_trust_level": "MEDIUM",
        "current_epoch_blockers": [],
    }


class ProtectedGateSnapshotTests(unittest.TestCase):
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

    def insert_snapshot(self, **overrides: object) -> dict:
        payload = {
            "snapshot_id": "snapshot-test",
            "exchange": "bithumb",
            "created_at_utc": gate_snapshot.utc_now(),
            "expires_at_utc": gate_snapshot._plus_seconds(60),
            "broker_status": "READY",
            "emergency_status": "OFF",
            "current_epoch_id": "epoch-test",
            "current_epoch_sanity_passed": True,
            "current_epoch_trust_level": "MEDIUM",
            "gate_allowed": True,
            "gate_blockers": [],
            "gate_warnings": [],
            "refresh_status": "SUCCESS",
            "current_epoch": current_epoch(),
            "controlled_gate": {
                "protected_full_auto_live_allowed": True,
                "protected_session_start_allowed": True,
                "protected_full_auto_live_blockers": [],
            },
            "smoke_preflight": {"open_order_audit_summary": {"exchange_open_order_count": 0}},
            "open_order_audit": {"open_order_audit_summary": {"exchange_open_order_count": 0}},
        }
        payload.update(overrides)
        return database.insert_protected_auto_safety_snapshot(payload)

    def create_epoch(self) -> dict:
        return database.create_accounting_epoch(
            {
                "exchange_name": "bithumb",
                "epoch_id": "epoch-test",
                "epoch_started_at_utc": "2026-07-01T00:00:00Z",
                "starting_exchange_equity": 300_000,
                "starting_cash_krw": 300_000,
                "starting_positions": [],
                "starting_position_count": 0,
                "starting_valuation_source": "bithumb_ticker",
                "starting_valuation_snapshot_at_utc": "2026-07-01T00:00:00Z",
                "cost_basis_policy": "MARK_TO_MARKET",
                "epoch_trust_level": "MEDIUM",
                "legacy_history_isolated": True,
            }
        )

    def test_start_gate_requires_refresh_when_snapshot_missing(self) -> None:
        with patch.dict("os.environ", {"APP_ENV": "development"}, clear=False):
            response = TestClient(app).post("/api/protected-full-auto-live/v1/start", json={"confirmation": ""})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["status"], "GATE_REFRESH_REQUIRED")
        self.assertEqual(body["required_action"], "POST /api/protected-full-auto-live/v1/gate/refresh-critical")

    def test_fresh_clean_snapshot_allows_cached_gate(self) -> None:
        self.insert_snapshot()

        status = gate_snapshot.load_cached_protected_gate_snapshot("bithumb")

        self.assertTrue(status["gate_allowed"])
        self.assertEqual(status["gate_status"], "GATE_ALLOWED")
        self.assertTrue(status["snapshot"]["is_fresh"])

    def test_exchange_open_order_timeout_records_blocker_and_notification_log(self) -> None:
        async def slow_open_orders(exchange: str) -> dict:
            await asyncio.sleep(0.1)
            return {"status": "SUCCESS", "orders": [], "errors": []}

        with (
            patch("app.protected_gate_snapshot._server_load_guard", return_value=("OK", [], {})),
            patch("app.protected_gate_snapshot._broker_status", return_value={"broker_status": "READY", "emergency_status": "OFF"}),
            patch("app.protected_gate_snapshot._current_epoch_snapshot", return_value=current_epoch()),
            patch("app.protected_gate_snapshot._exchange_open_orders", side_effect=slow_open_orders),
            patch("app.protected_gate_snapshot.EXCHANGE_OPEN_ORDER_TIMEOUT_SECONDS", 0.01),
        ):
            result = asyncio.run(gate_snapshot.refresh_protected_gate_safety_snapshot(exchange="bithumb"))

        self.assertFalse(result["gate_allowed"])
        self.assertEqual(result["status"], "TIMEOUT")
        blockers = [item["code"] for item in result["snapshot"]["gate_blockers"]]
        self.assertIn("SAFETY_SNAPSHOT_REFRESH_TIMEOUT", blockers)
        logs = database.load_notification_logs(event_type="SAFETY_SNAPSHOT_REFRESH_TIMEOUT")
        self.assertEqual(len(logs), 1)

    def test_gate_status_endpoint_uses_cached_snapshot_only(self) -> None:
        self.insert_snapshot()
        with (
            patch.dict("os.environ", {"APP_ENV": "development", "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/secret/token"}, clear=False),
            patch("app.protected_gate_snapshot._exchange_open_orders", side_effect=AssertionError("exchange should not be called")),
        ):
            response = TestClient(app).get("/api/protected-full-auto-live/v1/gate/status")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["source"], "CACHED_SAFETY_SNAPSHOT_ONLY")
        self.assertEqual(body["gate_status"], "GATE_ALLOWED")
        self.assertNotIn("secret/token", str(body))

    def test_critical_refresh_does_not_call_optional_diagnostics(self) -> None:
        self.create_epoch()
        self.insert_snapshot(snapshot_id="equity-ok")

        async def open_orders(exchange: str, markets: list[str], *, broker=None) -> dict:
            return {"status": "SUCCESS", "orders": [], "errors": [], "markets": markets}

        with (
            patch("app.protected_gate_snapshot._broker_status", return_value={"broker_status": "READY", "emergency_status": "OFF"}),
            patch("app.protected_gate_snapshot._exchange_open_orders_for_markets", side_effect=open_orders),
            patch("app.protected_gate_snapshot._current_epoch_snapshot", side_effect=AssertionError("optional equity diagnostics should not run")),
            patch("app.protected_gate_snapshot._refresh_impl", side_effect=AssertionError("full refresh should not run")),
        ):
            result = asyncio.run(gate_snapshot.refresh_protected_gate_critical_snapshot(exchange="bithumb", force=True))

        self.assertTrue(result["protected_start_allowed"])
        snapshot = result["snapshot"]
        self.assertTrue(snapshot["critical_gate_allowed"])
        self.assertTrue(snapshot["protected_start_allowed"])
        steps = [step["step_name"] for step in snapshot["refresh_step_timings"]]
        self.assertIn("broker_status_check", steps)
        self.assertIn("exchange_open_order_check", steps)
        self.assertIn("final_gate_decision", steps)

    def test_critical_open_order_check_targets_btc_eth_and_db_unresolved_only(self) -> None:
        self.create_epoch()
        self.insert_snapshot(snapshot_id="equity-ok")
        markets_seen: list[str] = []
        with database.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO live_positions (
                    session_id, exchange, market, candidate_strategy_id, strategy_name, status,
                    entry_price, entry_volume, entry_amount_krw, current_price, unrealized_pnl,
                    realized_pnl, stop_loss_price, take_profit_price, opened_at, created_at, updated_at
                ) VALUES (1, 'bithumb', 'KRW-RE', 1, 'legacy', 'OPEN',
                    1, 1, 1, 1, 0, 0, 0, 0, '2026-07-01T00:00:00Z',
                    '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z')
                """
            )

        async def open_orders(exchange: str, markets: list[str], *, broker=None) -> dict:
            markets_seen.extend(markets)
            return {"status": "SUCCESS", "orders": [], "errors": []}

        with (
            patch("app.protected_gate_snapshot._broker_status", return_value={"broker_status": "READY", "emergency_status": "OFF"}),
            patch("app.protected_gate_snapshot._exchange_open_orders_for_markets", side_effect=open_orders),
        ):
            result = asyncio.run(gate_snapshot.refresh_protected_gate_critical_snapshot(exchange="bithumb", force=True))

        self.assertTrue(result["protected_start_allowed"])
        self.assertEqual(sorted(markets_seen), ["KRW-BTC", "KRW-ETH"])
        self.assertEqual(result["snapshot"]["legacy_open_position_count"], 1)

    def test_critical_exchange_open_order_timeout_records_timeout_step(self) -> None:
        self.create_epoch()
        self.insert_snapshot(snapshot_id="equity-ok")

        async def slow_open_orders(exchange: str, markets: list[str], *, broker=None) -> dict:
            await asyncio.sleep(0.1)
            return {"status": "SUCCESS", "orders": [], "errors": []}

        with (
            patch("app.protected_gate_snapshot._broker_status", return_value={"broker_status": "READY", "emergency_status": "OFF"}),
            patch("app.protected_gate_snapshot._exchange_open_orders_for_markets", side_effect=slow_open_orders),
            patch("app.protected_gate_snapshot.EXCHANGE_OPEN_ORDER_TIMEOUT_SECONDS", 0.01),
        ):
            result = asyncio.run(gate_snapshot.refresh_protected_gate_critical_snapshot(exchange="bithumb", force=True))

        self.assertFalse(result["protected_start_allowed"])
        self.assertEqual(result["status"], "TIMEOUT")
        self.assertEqual(result["snapshot"]["timeout_step"]["step_name"], "exchange_open_order_check")
        blockers = [item["code"] for item in result["snapshot"]["protected_start_blockers"]]
        self.assertIn("EXCHANGE_OPEN_ORDER_CHECK_TIMEOUT", blockers)

    def test_optional_diagnostics_timeout_does_not_block_critical_start_gate(self) -> None:
        self.create_epoch()
        self.insert_snapshot(snapshot_id="equity-ok", created_at_utc="2026-07-01T00:00:00Z")
        self.insert_snapshot(
            snapshot_id="latest-timeout",
            refresh_status="TIMEOUT",
            gate_allowed=False,
            gate_blockers=[{"code": "SAFETY_SNAPSHOT_REFRESH_TIMEOUT"}],
            current_epoch_sanity_passed=False,
        )

        async def open_orders(exchange: str, markets: list[str], *, broker=None) -> dict:
            return {"status": "SUCCESS", "orders": [], "errors": []}

        with (
            patch("app.protected_gate_snapshot._broker_status", return_value={"broker_status": "READY", "emergency_status": "OFF"}),
            patch("app.protected_gate_snapshot._exchange_open_orders_for_markets", side_effect=open_orders),
        ):
            result = asyncio.run(gate_snapshot.refresh_protected_gate_critical_snapshot(exchange="bithumb", force=True))

        self.assertTrue(result["protected_start_allowed"])
        self.assertEqual(result["snapshot"]["optional_diagnostics_status"], "TIMEOUT")

    def test_stale_critical_snapshot_blocks_start(self) -> None:
        self.insert_snapshot(
            snapshot_id="stale-critical",
            expires_at_utc="2026-07-01T00:00:00Z",
            critical_gate_allowed=True,
            protected_start_allowed=True,
        )

        response = TestClient(app).post(
            "/api/protected-full-auto-live/v1/start",
            json={"confirmation": "START PROTECTED FULL AUTO LIVE V1"},
        )

        body = response.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["status"], "GATE_REFRESH_REQUIRED")
        self.assertIn(body["snapshot_gate_status"], {"CRITICAL_SNAPSHOT_STALE", "GATE_SNAPSHOT_STALE"})

    def test_health_does_not_call_protected_gate_refresh(self) -> None:
        with patch("app.protected_gate_snapshot._exchange_open_orders", side_effect=AssertionError("exchange should not be called")):
            response = TestClient(app).get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["health_detail"], "LIGHTWEIGHT")

    def test_duplicate_refresh_is_blocked_while_refresh_in_progress(self) -> None:
        gate_snapshot._REFRESH_LOCK.acquire()
        try:
            result = asyncio.run(gate_snapshot.refresh_protected_gate_safety_snapshot(exchange="bithumb"))
        finally:
            gate_snapshot._REFRESH_LOCK.release()

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "REFRESH_IN_PROGRESS")
        self.assertTrue(result["refresh_in_progress"])

    def test_failed_refresh_does_not_stop_protected_daemon(self) -> None:
        start_protected_auto_daemon(
            exchange="bithumb",
            symbols=["BTC", "ETH"],
            amount_krw=6000,
            scan_interval_seconds=60,
            max_holding_minutes=10,
            max_position_trades=1,
            current_epoch=current_epoch(),
            gate={"protected_full_auto_live_allowed": True},
        )

        with (
            patch("app.protected_gate_snapshot._server_load_guard", return_value=("OK", [], {})),
            patch("app.protected_gate_snapshot._refresh_impl", side_effect=RuntimeError("boom")),
        ):
            result = asyncio.run(gate_snapshot.refresh_protected_gate_safety_snapshot(exchange="bithumb"))

        self.assertFalse(result["gate_allowed"])
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(protected_auto_status()["protected_auto_runtime_status"], "RUNNING")


if __name__ == "__main__":
    unittest.main()
