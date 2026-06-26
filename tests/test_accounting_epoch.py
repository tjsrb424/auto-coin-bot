from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database
from app.accounting_epoch import (
    build_open_order_audit,
    build_current_epoch_diagnostics,
    build_smoke_test_preflight,
    legacy_history_quarantine,
    limited_auto_live_gate,
)
from app.trading_diagnostics import build_trading_diagnostics_report


class AccountingEpochTests(unittest.TestCase):
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
        with database.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO runtime_locks (
                    lock_id, instance_id, hostname, app_env, runtime_owner,
                    status, acquired_at, expires_at, updated_at
                ) VALUES ('auto-trading', 'test', 'test-host', 'test', 'test', 'STOPPED',
                    '2026-06-26T00:00:00Z', '2026-06-26T01:00:00Z', '2026-06-26T00:00:00Z')
                """
            )

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.tmp.cleanup()

    def create_epoch(self) -> dict:
        return database.create_accounting_epoch(
            {
                "exchange_name": "bithumb",
                "epoch_id": "epoch-test",
                "epoch_started_at_utc": "2026-06-26T00:00:00Z",
                "starting_exchange_equity": 263_000,
                "starting_cash_krw": 10_000,
                "starting_positions": [
                    {
                        "symbol": "BTC",
                        "market": "KRW-BTC",
                        "opening_quantity": 0.001,
                        "opening_cost_basis": 100_000,
                        "opening_avg_entry_price": 100_000_000,
                    }
                ],
                "starting_position_count": 1,
                "starting_valuation_source": "bithumb_ticker",
                "starting_valuation_snapshot_at_utc": "2026-06-26T00:00:00Z",
                "cost_basis_policy": "MARK_TO_MARKET",
                "epoch_trust_level": "MEDIUM",
                "legacy_history_isolated": True,
            }
        )

    def insert_open_order(
        self,
        *,
        request_id: str,
        order_uuid: str | None,
        market: str = "KRW-BTC",
        created_at: str = "2026-06-25T23:00:00Z",
        status: str = "WAITING",
    ) -> None:
        with database.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO live_order_logs (
                    request_id, exchange, market, side, order_type, price, volume, amount_krw,
                    fee_estimate, risk_result, order_preview_payload, exchange_request_payload_masked,
                    exchange_response_payload, status, order_uuid, order_purpose, created_at, updated_at
                ) VALUES (?, 'bithumb', ?, 'BUY', 'limit', 100, 1, 100,
                    0, 'TEST', '{}', '{}', '{}', ?, ?, 'ENTRY', ?, ?)
                """,
                (request_id, market, status, order_uuid, created_at, created_at),
            )

    def test_legacy_history_is_low_trust_and_excluded_from_live_decisions(self) -> None:
        legacy = legacy_history_quarantine(
            {
                "ledger_pnl_detail": {
                    "fifo_trace_summary": {"warning_counts": {"SELL_EXCEEDS_OPEN_QUANTITY": 1}}
                }
            }
        )

        self.assertEqual(legacy["history_trust_level"], "LOW")
        self.assertTrue(legacy["legacy_contaminated"])
        self.assertFalse(legacy["use_for_live_risk"])
        self.assertFalse(legacy["use_for_strategy_score"])
        self.assertFalse(legacy["use_for_dashboard_main_pnl"])

    def test_accounting_epoch_creation_stores_mark_to_market_snapshot(self) -> None:
        epoch = self.create_epoch()

        self.assertEqual(epoch["cost_basis_policy"], "MARK_TO_MARKET")
        self.assertTrue(epoch["legacy_history_isolated"])
        self.assertEqual(epoch["starting_position_count"], 1)
        self.assertEqual(epoch["starting_positions"][0]["opening_cost_basis"], 100_000)

    def test_current_epoch_is_clean_immediately_after_creation(self) -> None:
        self.create_epoch()

        report = build_current_epoch_diagnostics(exchange="bithumb", current_equity=263_000)

        self.assertEqual(report["current_epoch_fill_count"], 0)
        self.assertEqual(report["current_epoch_order_count"], 0)
        self.assertEqual(report["current_epoch_total_pnl"], 0)
        self.assertEqual(report["current_epoch_accounting_pending_count"], 0)
        self.assertTrue(report["current_epoch_sanity_passed"])
        self.assertTrue(report["current_epoch_restart_allowed"])

    def test_smoke_preflight_failure_does_not_create_order(self) -> None:
        self.create_epoch()

        result = build_smoke_test_preflight(exchange="bithumb", symbol="WLD", strategy_name="rsi")

        self.assertFalse(result["smoke_test_allowed"])
        codes = {item["code"] for item in result["smoke_test_blockers"]}
        self.assertIn("LIVE_SMOKE_TEST_DISABLED", codes)
        self.assertIn("SMOKE_TEST_BLOCKED_SYMBOL", codes)
        self.assertIn("SMOKE_TEST_BLOCKED_STRATEGY", codes)
        with database.get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) AS count FROM live_order_logs").fetchone()["count"]
        self.assertEqual(count, 0)

    def test_exchange_open_order_is_hard_smoke_blocker(self) -> None:
        self.create_epoch()
        current_epoch = build_current_epoch_diagnostics(exchange="bithumb", current_equity=263_000)
        audit = build_open_order_audit(
            exchange="bithumb",
            current_epoch=current_epoch,
            exchange_open_orders=[
                {"uuid": "exchange-open", "market": "KRW-BTC", "side": "bid", "state": "wait", "price": "100", "volume": "1"}
            ],
            exchange_open_order_status="SUCCESS",
        )

        preflight = build_smoke_test_preflight(exchange="bithumb", current_epoch=current_epoch, open_order_audit=audit)

        self.assertEqual(audit["open_order_audit_summary"]["exchange_open_order_count"], 1)
        self.assertIn("EXCHANGE_OPEN_ORDER_EXISTS", {item["code"] for item in preflight["smoke_test_blockers"]})
        self.assertEqual(audit["open_orders"][0]["recommended_action"], "USER_CONFIRM_CANCEL_REQUIRED")

    def test_current_epoch_open_order_is_hard_smoke_blocker(self) -> None:
        self.create_epoch()
        self.insert_open_order(request_id="epoch-open", order_uuid="epoch-open", created_at="2026-06-26T00:01:00Z")
        current_epoch = build_current_epoch_diagnostics(exchange="bithumb", current_equity=263_000)
        audit = build_open_order_audit(
            exchange="bithumb",
            current_epoch=current_epoch,
            exchange_open_orders=[],
            exchange_open_order_status="SUCCESS",
        )
        preflight = build_smoke_test_preflight(exchange="bithumb", current_epoch=current_epoch, open_order_audit=audit)

        self.assertEqual(audit["open_order_audit_summary"]["current_epoch_open_order_count"], 1)
        self.assertIn("CURRENT_EPOCH_OPEN_ORDER_EXISTS", {item["code"] for item in preflight["smoke_test_blockers"]})

    def test_legacy_db_stale_open_order_is_not_hard_smoke_blocker(self) -> None:
        self.create_epoch()
        self.insert_open_order(request_id="legacy-open", order_uuid="legacy-open", created_at="2026-06-25T23:59:00Z")
        current_epoch = build_current_epoch_diagnostics(exchange="bithumb", current_equity=263_000)
        audit = build_open_order_audit(
            exchange="bithumb",
            current_epoch=current_epoch,
            exchange_open_orders=[],
            exchange_open_order_status="SUCCESS",
        )
        with patch.dict(
            "os.environ",
            {
                "APP_ENV": "production",
                "LIVE_TRADING_ENABLED": "true",
                "BITHUMB_ACCESS_KEY": "access",
                "BITHUMB_SECRET_KEY": "secret",
                "LIVE_SMOKE_TEST_ENABLED": "true",
            },
            clear=False,
        ):
            preflight = build_smoke_test_preflight(exchange="bithumb", current_epoch=current_epoch, open_order_audit=audit)

        codes = {item["code"] for item in preflight["smoke_test_blockers"]}
        self.assertEqual(audit["open_order_audit_summary"]["db_stale_open_order_count"], 1)
        self.assertNotIn("CURRENT_EPOCH_OPEN_ORDER_EXISTS", codes)
        self.assertNotIn("EXCHANGE_OPEN_ORDER_EXISTS", codes)
        self.assertEqual(audit["open_order_audit_summary"]["smoke_test_blocking_open_order_count"], 0)

    def test_unavailable_exchange_audit_keeps_db_order_as_unknown_blocker(self) -> None:
        self.create_epoch()
        self.insert_open_order(request_id="legacy-open-unverified", order_uuid="legacy-unverified", created_at="2026-06-25T23:59:00Z")
        current_epoch = build_current_epoch_diagnostics(exchange="bithumb", current_equity=263_000)
        audit = build_open_order_audit(
            exchange="bithumb",
            current_epoch=current_epoch,
            exchange_open_orders=[],
            exchange_open_order_status="UNAVAILABLE",
        )
        preflight = build_smoke_test_preflight(exchange="bithumb", current_epoch=current_epoch, open_order_audit=audit)

        self.assertEqual(audit["open_order_audit_summary"]["unknown_open_order_count"], 1)
        self.assertIn("UNKNOWN_OPEN_ORDER_EXISTS", {item["code"] for item in preflight["smoke_test_blockers"]})

    def test_unknown_db_open_order_keeps_hard_smoke_blocker(self) -> None:
        self.create_epoch()
        self.insert_open_order(request_id="unknown-open", order_uuid=None, created_at="2026-06-26T00:01:00Z")
        current_epoch = build_current_epoch_diagnostics(exchange="bithumb", current_equity=263_000)
        audit = build_open_order_audit(
            exchange="bithumb",
            current_epoch=current_epoch,
            exchange_open_orders=[],
            exchange_open_order_status="SUCCESS",
        )
        preflight = build_smoke_test_preflight(exchange="bithumb", current_epoch=current_epoch, open_order_audit=audit)

        self.assertEqual(audit["open_order_audit_summary"]["unknown_open_order_count"], 1)
        self.assertIn("UNKNOWN_OPEN_ORDER_EXISTS", {item["code"] for item in preflight["smoke_test_blockers"]})

    def test_limited_auto_requires_passed_recent_smoke_and_full_auto_stays_false(self) -> None:
        self.create_epoch()
        current_epoch = build_current_epoch_diagnostics(exchange="bithumb", current_equity=263_000)
        with patch.dict(
            "os.environ",
            {
                "APP_ENV": "production",
                "LIVE_TRADING_ENABLED": "true",
                "BITHUMB_ACCESS_KEY": "access",
                "BITHUMB_SECRET_KEY": "secret",
                "LIVE_SMOKE_TEST_ENABLED": "true",
            },
            clear=False,
        ):
            preflight = build_smoke_test_preflight(exchange="bithumb", symbol="BTC", strategy_name="smoke_test", current_epoch=current_epoch)
            before = limited_auto_live_gate(current_epoch, preflight, exchange="bithumb")
            self.assertFalse(before["limited_auto_live_allowed"])
            self.assertFalse(before["full_auto_live_allowed"])

            self.insert_smoke_run(status="PASSED_AFTER_RECALC")
            after = limited_auto_live_gate(current_epoch, preflight, exchange="bithumb")

        self.assertTrue(after["limited_auto_live_allowed"])
        self.assertEqual(after["last_smoke_test_status"], "PASSED_AFTER_RECALC")
        self.assertFalse(after["full_auto_live_allowed"])
        self.assertEqual(after["limited_auto_constraints"]["allowed_symbols"], ["BTC", "ETH"])
        self.assertIn("rsi", after["limited_auto_constraints"]["blocked_strategies"])
        self.assertIn("WLD", after["limited_auto_constraints"]["blocked_symbols"])
        self.assertEqual(after["limited_auto_constraints"]["max_notional_krw"], 6000)
        self.assertEqual(after["limited_auto_constraints"]["max_orders"], 3)

    def test_limited_auto_recalculates_partial_smoke_to_passed_after_recalc(self) -> None:
        self.create_epoch()
        current_epoch = build_current_epoch_diagnostics(exchange="bithumb", current_equity=263_000)
        self.insert_smoke_order(
            request_id="smoke-buy",
            client_order_id="smoke-buy-client",
            order_uuid="C0101000003129835396",
            side="BUY",
            paid_fee=14.99,
            trades=[
                {"uuid": "buy-fill-1", "price": "599600000", "volume": "0.000025", "funds": "14990", "created_at": "2026-06-26T16:54:35+09:00"},
                {"uuid": "buy-fill-2", "price": "599600000", "volume": "0.000025", "funds": "14990", "created_at": "2026-06-26T16:54:36+09:00"},
            ],
        )
        self.insert_smoke_order(
            request_id="smoke-sell",
            client_order_id="smoke-sell-client",
            order_uuid="C0101000003129835406",
            side="SELL",
            paid_fee=14.99,
            trades=[
                {"uuid": "sell-fill-1", "price": "599600000", "volume": "0.00005", "funds": "29980", "created_at": "2026-06-26T16:54:39+09:00"},
            ],
        )
        self.insert_smoke_run(
            status="PARTIAL",
            report={
                "exchange_order_uuid_list": ["C0101000003129835396", "C0101000003129835406"],
                "exchange_fill_count": 3,
                "ledger_fill_count": 3,
                "missing_ledger_fill_count": 0,
                "duplicate_fill_count": 2,
                "fee_diff": -14.99,
                "equity_diff_after": 0,
                "current_epoch_accounting_pending_count": 0,
                "current_epoch_accounting_failed_count": 0,
                "final_runtime_status": "STOPPED",
            },
        )
        with patch.dict(
            "os.environ",
            {
                "APP_ENV": "production",
                "LIVE_TRADING_ENABLED": "true",
                "BITHUMB_ACCESS_KEY": "access",
                "BITHUMB_SECRET_KEY": "secret",
                "LIVE_SMOKE_TEST_ENABLED": "true",
            },
            clear=False,
        ):
            preflight = build_smoke_test_preflight(exchange="bithumb", symbol="BTC", strategy_name="smoke_test", current_epoch=current_epoch)
            gate = limited_auto_live_gate(current_epoch, preflight, exchange="bithumb")

        self.assertTrue(gate["limited_auto_live_allowed"])
        self.assertEqual(gate["last_smoke_test_status"], "PASSED_AFTER_RECALC")
        self.assertEqual((gate["last_smoke_test"]["report"])["duplicate_fill_count"], 0)
        self.assertEqual((gate["last_smoke_test"]["report"])["fee_diff"], 0)

    def test_limited_auto_blocks_on_accounting_pending_fee_or_equity_diff(self) -> None:
        self.create_epoch()
        current_epoch = build_current_epoch_diagnostics(exchange="bithumb", current_equity=263_000)
        clean_preflight = {"smoke_test_blockers": [], "open_order_audit_summary": {"exchange_open_order_count": 0, "current_epoch_open_order_count": 0, "unknown_open_order_count": 0}}

        self.insert_smoke_run(status="PASSED_AFTER_RECALC")
        pending_epoch = {**current_epoch, "current_epoch_accounting_pending_count": 1, "current_epoch_sanity_passed": False, "current_epoch_restart_allowed": False}
        pending_gate = limited_auto_live_gate(pending_epoch, clean_preflight, exchange="bithumb")
        self.assertFalse(pending_gate["limited_auto_live_allowed"])
        self.assertIn("CURRENT_EPOCH_ACCOUNTING_PENDING", {item["code"] for item in pending_gate["limited_auto_live_blockers"]})

        self.insert_smoke_run(smoke_test_id="smoke-fee-diff", status="PASSED_AFTER_RECALC", report={"fee_diff": 10})
        fee_gate = limited_auto_live_gate(current_epoch, clean_preflight, exchange="bithumb")
        self.assertFalse(fee_gate["limited_auto_live_allowed"])
        self.assertIn("SMOKE_FEE_DIFF", {item["code"] for item in fee_gate["limited_auto_live_blockers"]})

        self.insert_smoke_run(smoke_test_id="smoke-equity-diff", status="PASSED_AFTER_RECALC", report={"equity_diff_after": 101})
        equity_gate = limited_auto_live_gate(current_epoch, clean_preflight, exchange="bithumb")
        self.assertFalse(equity_gate["limited_auto_live_allowed"])
        self.assertIn("SMOKE_EQUITY_DIFF", {item["code"] for item in equity_gate["limited_auto_live_blockers"]})

    def test_diagnostics_separates_legacy_current_epoch_and_smoke_blockers(self) -> None:
        report = build_trading_diagnostics_report(
            exchange="bithumb",
            days=7,
            starting_asset_krw=300_000,
            asset_reconciliation={
                "initial_equity": 300_000,
                "current_equity_from_exchange": 263_000,
                "current_cash_krw": 263_000,
                "current_coin_market_value": 0,
                "deposits": 0,
                "withdrawals": 0,
                "deposit_withdrawal_status": "UNAVAILABLE",
                "opening_inventory_report": {
                    "opening_snapshot_available": False,
                    "opening_snapshot_trust_level": "LOW",
                },
                "exchange_fill_accounting": {
                    "ledger_pnl_detail": {
                        "net_realized_pnl_after_fee": -66_000,
                        "unrealized_pnl_after_estimated_exit_fee": -121_000,
                        "total_pnl_after_estimated_exit_fee": -187_000,
                    },
                    "ledger_strategy_pnl": [{"strategy_name": "rsi", "total_pnl": -187_000, "unrealized_pnl": -121_000}],
                    "ledger_symbol_pnl": [{"symbol": "XLM", "total_pnl": -187_000, "unrealized_pnl": -121_000}],
                    "ledger_session_pnl": [{"session_id": "1", "total_pnl": -187_000, "unrealized_pnl": -121_000}],
                },
            },
        )

        self.assertEqual(report["legacy_history"]["history_trust_level"], "LOW")
        self.assertIn("TOTAL_PNL_SANITY_FAILED", {item["code"] for item in report["legacy_blockers"]})
        self.assertIn("CURRENT_EPOCH_MISSING", {item["code"] for item in report["current_epoch_blockers"]})
        self.assertIn("LIVE_SMOKE_TEST_DISABLED", {item["code"] for item in report["smoke_test_blockers"]})
        self.assertFalse(report["full_auto_live_allowed"])

    def insert_smoke_run(self, *, smoke_test_id: str = "smoke-pass", status: str = "PASSED", report: dict | None = None) -> None:
        base_report = {
            "smoke_test_status": status,
            "exchange_fill_count": 3,
            "ledger_fill_count": 3,
            "missing_ledger_fill_count": 0,
            "duplicate_fill_count": 0,
            "fee_diff": 0,
            "equity_diff_after": 0,
            "current_epoch_accounting_pending_count": 0,
            "current_epoch_accounting_failed_count": 0,
            "final_runtime_status": "STOPPED",
        }
        base_report.update(report or {})
        database.insert_smoke_test_run(
            {
                "smoke_test_id": smoke_test_id,
                "exchange_name": "bithumb",
                "symbol": "BTC",
                "market": "KRW-BTC",
                "status": status,
                "completed_at_utc": "2026-06-26T00:10:00Z",
                "max_notional_krw": 6000,
                "report": base_report,
            }
        )

    def insert_smoke_order(
        self,
        *,
        request_id: str,
        client_order_id: str,
        order_uuid: str,
        side: str,
        paid_fee: float,
        trades: list[dict],
    ) -> None:
        executed_volume = 0.00005
        filled_amount_krw = 29980
        database.insert_live_order_log(
            {
                "request_id": request_id,
                "client_order_id": client_order_id,
                "idempotency_key": f"test:{request_id}",
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "side": side,
                "order_type": "LIMIT",
                "price": filled_amount_krw / executed_volume,
                "volume": executed_volume,
                "amount_krw": filled_amount_krw,
                "fee_estimate": paid_fee,
                "risk_result": "SMOKE_TEST_FILLED",
                "order_preview_payload": {},
                "exchange_request_payload_masked": {},
                "exchange_response_payload": {
                    "uuid": order_uuid,
                    "client_order_id": client_order_id,
                    "market": "KRW-BTC",
                    "side": "bid" if side == "BUY" else "ask",
                    "price": str(filled_amount_krw / executed_volume),
                    "executed_volume": str(executed_volume),
                    "executed_funds": str(filled_amount_krw),
                    "paid_fee": str(paid_fee),
                    "created_at": "2026-06-26T16:54:34+09:00",
                    "trades": trades,
                },
                "status": "FILLED",
                "order_uuid": order_uuid,
                "executed_volume": executed_volume,
                "remaining_volume": 0,
                "filled_amount_krw": filled_amount_krw,
                "paid_fee": paid_fee,
                "order_purpose": "SMOKE_TEST",
                "strategy_name": "smoke_test",
                "signal_type": side,
                "manual_confirmed": True,
            }
        )


if __name__ == "__main__":
    unittest.main()
