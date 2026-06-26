from __future__ import annotations

import unittest

from app.trading_reconciliation import build_equity_reconciliation
from app.trading_diagnostics import _restart_gate


def db_order(**overrides: object) -> dict:
    payload = {
        "id": 1,
        "request_id": "req-1",
        "client_order_id": "client-1",
        "order_uuid": "uuid-1",
        "exchange": "bithumb",
        "market": "KRW-XLM",
        "side": "BUY",
        "status": "FILLED",
        "price": 100,
        "volume": 10,
        "executed_volume": 10,
        "filled_amount_krw": 1000,
        "amount_krw": 1000,
        "paid_fee": 0.5,
        "created_at": "2026-06-25T00:00:00Z",
        "updated_at": "2026-06-25T00:00:05Z",
        "order_executed_at_utc": "2026-06-25T00:00:05Z",
    }
    payload.update(overrides)
    return payload


def exchange_order(**overrides: object) -> dict:
    payload = {
        "uuid": "uuid-1",
        "client_order_id": "client-1",
        "market": "KRW-XLM",
        "side": "bid",
        "price": "100",
        "executed_volume": "10",
        "executed_funds": "1000",
        "paid_fee": "0.5",
        "created_at": "2026-06-25T00:00:05Z",
    }
    payload.update(overrides)
    return payload


class TradingReconciliationTests(unittest.TestCase):
    def test_net_realized_pnl_does_not_subtract_total_fee_twice(self) -> None:
        report = build_equity_reconciliation(
            initial_equity=1000,
            current_equity_from_exchange=900,
            realized_pnl_from_db=-100,
            unrealized_pnl_from_positions=0,
            total_fee_from_db=10,
            db_orders=[],
            db_positions=[],
        )

        self.assertEqual(report["expected_equity"], 900)
        self.assertEqual(report["legacy_expected_equity_with_double_fee"], 890)
        self.assertEqual(report["gross_realized_pnl_before_fee"], -100)
        self.assertEqual(report["net_realized_pnl_after_fee"], -100)

    def test_realized_fee_separates_gross_and_net_pnl(self) -> None:
        report = build_equity_reconciliation(
            initial_equity=1000,
            current_equity_from_exchange=895,
            realized_pnl_from_db=-105,
            unrealized_pnl_from_positions=0,
            total_fee_from_db=5,
            db_orders=[db_order(side="SELL", order_purpose="EXIT", paid_fee=5)],
            db_positions=[],
        )

        self.assertEqual(report["realized_fee"], 5)
        self.assertEqual(report["gross_realized_pnl_before_fee"], -100)
        self.assertEqual(report["net_realized_pnl_after_fee"], -105)

    def test_locked_balances_are_reported_and_current_equity_uses_totals(self) -> None:
        balances = {
            "by_currency": {
                "KRW": {"balance": 1000, "locked": 50},
                "XLM": {"balance": 3, "locked": 2},
            }
        }
        report = build_equity_reconciliation(
            initial_equity=1000,
            current_equity_from_exchange=1550,
            realized_pnl_from_db=0,
            unrealized_pnl_from_positions=0,
            total_fee_from_db=0,
            db_orders=[],
            db_positions=[],
            exchange_balances=balances,
            valuation_prices={"KRW-XLM": 100},
        )

        self.assertTrue(report["current_equity_uses_locked_balances"])
        self.assertEqual(report["locked_krw_value"], 50)
        self.assertEqual(report["locked_coin_market_value"], 200)

    def test_exchange_fill_missing_in_db_is_classified(self) -> None:
        report = build_equity_reconciliation(
            initial_equity=1000,
            current_equity_from_exchange=1000,
            realized_pnl_from_db=0,
            unrealized_pnl_from_positions=0,
            total_fee_from_db=0,
            db_orders=[],
            db_positions=[],
            exchange_orders=[exchange_order(uuid="exchange-only", client_order_id="exchange-client")],
        )

        missing = report["exchange_fill_match"]["missing_exchange_fill_in_db"]
        self.assertEqual(missing["count"], 1)
        self.assertEqual(missing["amount_krw"], 1000)
        self.assertTrue(report["gate_failed"])

    def test_db_only_trade_is_classified(self) -> None:
        report = build_equity_reconciliation(
            initial_equity=1000,
            current_equity_from_exchange=1000,
            realized_pnl_from_db=0,
            unrealized_pnl_from_positions=0,
            total_fee_from_db=0.5,
            db_orders=[db_order()],
            db_positions=[],
            exchange_orders=[],
        )

        db_only = report["exchange_fill_match"]["db_only_trade"]
        self.assertEqual(db_only["count"], 1)
        self.assertEqual(db_only["amount_krw"], 1000)
        self.assertTrue(report["gate_failed"])

    def test_fee_mismatch_uses_indexed_exchange_fill_row(self) -> None:
        report = build_equity_reconciliation(
            initial_equity=1000,
            current_equity_from_exchange=1000,
            realized_pnl_from_db=0,
            unrealized_pnl_from_positions=0,
            total_fee_from_db=2,
            db_orders=[db_order(order_uuid="C0504000000407836246", paid_fee=2)],
            db_positions=[],
            exchange_orders=[exchange_order(uuid="C0504000000407836246", paid_fee="0.5")],
        )

        self.assertEqual(report["exchange_fill_match"]["fee_mismatches"]["count"], 1)
        self.assertEqual(report["exchange_fill_match"]["fee_mismatches"]["amount_krw"], 1.5)
        self.assertTrue(report["gate_failed"])

    def test_duplicate_exchange_uuid_and_client_id_are_classified(self) -> None:
        report = build_equity_reconciliation(
            initial_equity=1000,
            current_equity_from_exchange=1000,
            realized_pnl_from_db=0,
            unrealized_pnl_from_positions=0,
            total_fee_from_db=1,
            db_orders=[
                db_order(id=1, request_id="req-1", order_uuid="C0504000000407836246", client_order_id="dup-client"),
                db_order(id=2, request_id="req-2", order_uuid="C0504000000407836246", client_order_id="dup-client"),
            ],
            db_positions=[],
        )

        self.assertEqual(report["duplicate_exchange_uuid_in_db"]["count"], 1)
        self.assertEqual(report["duplicate_client_order_id_in_db"]["count"], 1)
        self.assertTrue(report["gate_failed"])

    def test_valuation_price_diff_is_classified(self) -> None:
        report = build_equity_reconciliation(
            initial_equity=1000,
            current_equity_from_exchange=900,
            realized_pnl_from_db=0,
            unrealized_pnl_from_positions=0,
            total_fee_from_db=0,
            db_orders=[],
            db_positions=[
                {
                    "id": 7,
                    "market": "KRW-XLM",
                    "status": "OPEN",
                    "current_price": 100,
                    "entry_volume": 10,
                }
            ],
            valuation_prices={"KRW-XLM": 90},
        )

        self.assertEqual(report["valuation_price_diff_detail"]["amount_krw"], -100)
        self.assertEqual(report["equity_diff_breakdown"]["valuation_price_diff"], -100)

    def test_unexplained_over_threshold_blocks_restart(self) -> None:
        report = build_equity_reconciliation(
            initial_equity=1000,
            current_equity_from_exchange=800,
            realized_pnl_from_db=0,
            unrealized_pnl_from_positions=0,
            total_fee_from_db=0,
            db_orders=[],
            db_positions=[],
        )

        self.assertEqual(report["equity_diff_breakdown"]["unexplained"], -200)
        self.assertTrue(report["gate_failed"])

    def test_deposit_withdrawal_unavailable_blocks_restart_gate(self) -> None:
        risk = {
            "duplicate_open_symbols": {"count": 0},
            "duplicate_session_orders": {"count": 0},
            "stopped_session_trades": {"count": 0},
            "expired_reservation_executions": {"count": 0},
            "duplicate_candle_executions": {"count": 0},
            "incomplete_candle_usage": {"count": 0},
            "timestamp_mismatches": {"count": 0},
            "duplicate_order_uuid": {"count": 0},
            "duplicate_fill_events": {"count": 0},
            "overtrade": {"breached": False, "trade_count": 0},
            "fee_pressure": {"warning": False},
        }
        gate = _restart_gate(
            risk,
            {"daily_loss_limit_reached": False},
            {"total_pnl_krw": 0},
            {
                "gate_failed": False,
                "asset_reconciliation_requested": True,
                "deposit_withdrawal_status": "UNAVAILABLE",
                "deposit_withdrawal_mismatch_is_verified": False,
                "exchange_fill_match": {},
                "exchange_fills_ledger_summary": {},
                "exchange_fill_missing_breakdown": {},
            },
        )

        codes = {reason["code"] for reason in gate["reasons"]}
        self.assertFalse(gate["allowed"])
        self.assertIn("DEPOSIT_WITHDRAWAL_LEDGER_UNAVAILABLE", codes)
        self.assertIn("DEPOSIT_WITHDRAWAL_MISMATCH_UNVERIFIED", codes)


if __name__ == "__main__":
    unittest.main()
