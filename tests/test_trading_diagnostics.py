from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database
from app.trading_diagnostics import build_trading_diagnostics_report, restart_block_reason


class TradingDiagnosticsTests(unittest.TestCase):
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
        self.session_id = database.create_live_strategy_session(
            {
                "exchange": "bithumb",
                "market": "KRW-XLM",
                "candidate_strategy_id": 12,
                "strategy_name": "smart_autonomous",
                "strategy_parameters": {},
                "status": "STOPPED",
                "auto_enabled": False,
                "initial_balance_krw": 0,
                "max_order_krw": 30_000,
                "max_orders_per_day": 10,
            }
        )

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.tmp.cleanup()

    def insert_order(self, request_id: str, **overrides: object) -> int:
        payload = {
            "request_id": request_id,
            "session_id": self.session_id,
            "candidate_strategy_id": 12,
            "exchange": "bithumb",
            "market": "KRW-XLM",
            "side": "BUY",
            "order_type": "LIMIT",
            "price": 100,
            "volume": 10,
            "amount_krw": 1000,
            "fee_estimate": 0.5,
            "risk_result": "ALLOWED",
            "order_preview_payload": {},
            "exchange_request_payload_masked": {},
            "exchange_response_payload": {},
            "status": "FILLED",
            "order_uuid": "uuid-1",
            "executed_volume": 10,
            "filled_amount_krw": 1000,
            "paid_fee": 0.5,
            "order_purpose": "ENTRY",
            "strategy_name": "smart_autonomous",
            "candle_time_utc": "2026-06-25T00:00:00Z",
        }
        payload.update(overrides)
        return database.insert_live_order_log(payload)

    def test_report_summarizes_recent_trades_and_blocks_restart_on_duplicate_uuid(self) -> None:
        self.insert_order("req-entry")
        self.insert_order("req-scale-filled-event", order_purpose="SCALE_IN")
        database.create_live_position(
            {
                "session_id": self.session_id,
                "exchange": "bithumb",
                "market": "KRW-XLM",
                "candidate_strategy_id": 12,
                "strategy_name": "smart_autonomous",
                "status": "CLOSED",
                "entry_order_uuid": "uuid-1",
                "entry_price": 100,
                "entry_volume": 10,
                "entry_amount_krw": 1000,
                "current_price": 90,
                "unrealized_pnl": 0,
                "realized_pnl": -100,
                "stop_loss_price": 0,
                "take_profit_price": 0,
            }
        )

        report = build_trading_diagnostics_report(exchange="bithumb", days=7, starting_asset_krw=300_000)

        self.assertEqual(report["summary"]["trade_count"], 1)
        self.assertEqual(report["summary"]["total_fee_krw"], 0.5)
        self.assertEqual(report["summary"]["total_pnl_krw"], -100)
        self.assertEqual(report["risk_diagnostics"]["duplicate_order_uuid"]["count"], 1)
        self.assertFalse(report["restart_gate"]["allowed"])
        self.assertIn(
            "DUPLICATE_ORDER_UUID",
            {reason["code"] for reason in report["restart_gate"]["reasons"]},
        )

    def test_restart_block_reason_is_idempotent_report_wrapper(self) -> None:
        self.insert_order("req-1")
        self.insert_order("req-2-filled-event")

        gate = restart_block_reason("bithumb")

        self.assertFalse(gate["allowed"])
        self.assertEqual(gate["block_code"], "LIVE_RESTART_BLOCKED_BY_DIAGNOSTICS")
        self.assertTrue(gate["report"]["risk_diagnostics"]["duplicate_order_uuid"]["count"] >= 1)

    def test_duplicate_exchange_order_uuid_is_blocked_by_service_insert(self) -> None:
        self.insert_order("req-1", order_uuid="unique-uuid")

        with self.assertRaisesRegex(ValueError, "DUPLICATE_EXCHANGE_ORDER_UUID"):
            self.insert_order("req-2", order_uuid="unique-uuid")

    def test_timestamp_format_mismatch_detects_non_utc_offsets(self) -> None:
        self.insert_order("req-kst", order_uuid="kst-uuid", candle_time_utc="2026-06-25T09:00:00+09:00")
        with database.get_connection() as conn:
            conn.execute(
                """
                UPDATE live_order_logs
                SET candle_time_utc = ?, candle_close_at_utc = ?
                WHERE request_id = ?
                """,
                ("2026-06-25T09:00:00+09:00", "2026-06-25T09:00:00+09:00", "req-kst"),
            )

        report = build_trading_diagnostics_report(exchange="bithumb", days=7, starting_asset_krw=300_000)

        self.assertEqual(report["risk_diagnostics"]["timestamp_mismatches"]["count"], 1)
        self.assertIn("TIMESTAMP_FORMAT_MISMATCH", {reason["code"] for reason in report["restart_gate"]["reasons"]})

    def test_equity_reconciliation_diff_blocks_restart(self) -> None:
        report = build_trading_diagnostics_report(
            exchange="bithumb",
            days=7,
            starting_asset_krw=300_000,
            asset_reconciliation={
                "initial_equity": 300_000,
                "current_equity_from_exchange": 299_000,
                "current_cash_krw": 299_000,
                "current_coin_market_value": 0,
            },
        )

        self.assertTrue(report["asset_reconciliation"]["gate_failed"])
        self.assertIn("EQUITY_RECONCILIATION_DIFF", {reason["code"] for reason in report["restart_gate"]["reasons"]})


if __name__ == "__main__":
    unittest.main()
