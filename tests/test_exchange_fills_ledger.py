from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app import database
from app.exchange_fills_ledger import (
    build_exchange_fill_accounting_report,
    build_exchange_fill_records,
    build_position_valuation_summary,
    compute_realized_pnl_from_ledger,
    is_real_exchange_order_uuid,
    persist_exchange_fill_records,
    real_duplicate_exchange_uuid_groups,
    summarize_ledger_rows,
)


def exchange_order(**overrides: object) -> dict:
    payload = {
        "uuid": "C0504000000407836246",
        "client_order_id": "client-1",
        "market": "KRW-XLM",
        "side": "bid",
        "price": "281",
        "executed_volume": "10",
        "executed_funds": "2810",
        "paid_fee": "1",
        "created_at": "2026-06-25T13:02:36+09:00",
    }
    payload.update(overrides)
    return payload


def db_order(**overrides: object) -> dict:
    payload = {
        "id": 7,
        "request_id": "req-1",
        "client_order_id": "client-1",
        "order_uuid": "C0504000000407836246",
        "market": "KRW-XLM",
        "side": "BUY",
        "status": "FILLED",
        "price": 281,
        "executed_volume": 10,
        "filled_amount_krw": 2810,
        "paid_fee": 1,
        "session_id": 3,
        "strategy_name": "smart_autonomous",
        "created_at": "2026-06-25T04:02:36Z",
        "updated_at": "2026-06-25T04:02:36Z",
    }
    payload.update(overrides)
    return payload


class ExchangeFillsLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        database.init_db()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_exchange_fills_ledger_dedupes_same_fill(self) -> None:
        records = build_exchange_fill_records(exchange_name="bithumb", exchange_orders=[exchange_order()], db_orders=[db_order()])

        first = persist_exchange_fill_records(records)
        second = persist_exchange_fill_records(records)

        self.assertEqual(first["row_count"], 1)
        self.assertEqual(second["row_count"], 1)
        with database.get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) AS count FROM exchange_fills_ledger").fetchone()["count"]
        self.assertEqual(count, 1)

    def test_multi_fill_same_exchange_order_uuid_is_not_duplicate_fill(self) -> None:
        order = exchange_order(
            executed_volume="10",
            executed_funds="2810",
            paid_fee="1.405",
            trades=[
                {"uuid": "trade-1", "price": "281", "volume": "4", "funds": "1124", "created_at": "2026-06-25T13:02:36+09:00"},
                {"uuid": "trade-2", "price": "281", "volume": "6", "funds": "1686", "created_at": "2026-06-25T13:02:37+09:00"},
            ],
        )

        records = build_exchange_fill_records(exchange_name="bithumb", exchange_orders=[order], db_orders=[db_order(executed_volume=10, filled_amount_krw=2810, paid_fee=1.405)])
        summary = summarize_ledger_rows(records)

        self.assertEqual(len(records), 2)
        self.assertEqual(summary["duplicate_fill_key_count"], 0)
        self.assertEqual(summary["duplicate_fill_count"], 0)
        self.assertEqual(summary["multi_fill_order_uuid_count"], 2)
        self.assertEqual({row["match_status"] for row in records}, {"MATCHED_DB_ORDER"})

    def test_duplicate_fill_count_uses_canonical_fill_key(self) -> None:
        fill = {
            "fill_key": "same-fill-key",
            "exchange_order_uuid": "C0504000000407836246",
            "market": "KRW-XLM",
            "side": "BUY",
            "quantity": 5,
            "executed_value": 1405,
            "fee": 0.5,
            "executed_at_utc": "2026-06-25T04:02:36Z",
        }

        summary = summarize_ledger_rows([fill, dict(fill)])

        self.assertEqual(summary["duplicate_fill_key_count"], 1)
        self.assertEqual(summary["duplicate_fill_count"], 1)

    def test_order_summary_fee_is_allocated_once_across_trade_rows(self) -> None:
        order = exchange_order(
            executed_volume="10",
            executed_funds="2810",
            paid_fee="1.405",
            trades=[
                {"uuid": "trade-1", "price": "281", "volume": "4", "funds": "1124", "created_at": "2026-06-25T13:02:36+09:00"},
                {"uuid": "trade-2", "price": "281", "volume": "6", "funds": "1686", "created_at": "2026-06-25T13:02:37+09:00"},
            ],
        )

        records = build_exchange_fill_records(exchange_name="bithumb", exchange_orders=[order], db_orders=[db_order(executed_volume=10, filled_amount_krw=2810, paid_fee=1.405)])

        self.assertAlmostEqual(sum(row["fee"] for row in records), 1.405)
        self.assertEqual({row["fee_source"] for row in records}, {"ORDER_SUMMARY_FEE"})

    def test_fill_row_fee_is_source_of_truth_when_present(self) -> None:
        order = exchange_order(
            paid_fee="999",
            trades=[
                {"uuid": "trade-1", "price": "281", "volume": "4", "funds": "1124", "fee": "0.4", "created_at": "2026-06-25T13:02:36+09:00"},
                {"uuid": "trade-2", "price": "281", "volume": "6", "funds": "1686", "fee": "0.6", "created_at": "2026-06-25T13:02:37+09:00"},
            ],
        )

        records = build_exchange_fill_records(exchange_name="bithumb", exchange_orders=[order], db_orders=[db_order(executed_volume=10, filled_amount_krw=2810, paid_fee=1)])

        self.assertAlmostEqual(sum(row["fee"] for row in records), 1.0)
        self.assertEqual({row["fee_source"] for row in records}, {"FILL_ROW_FEE"})

    def test_smart_order_uuid_is_not_real_exchange_uuid_or_duplicate(self) -> None:
        self.assertFalse(is_real_exchange_order_uuid("smart-order-uuid"))
        report = real_duplicate_exchange_uuid_groups(
            [
                {"id": 1, "request_id": "r1", "order_uuid": "smart-order-uuid"},
                {"id": 2, "request_id": "r2", "order_uuid": "smart-order-uuid"},
            ]
        )

        self.assertEqual(report["count"], 0)
        self.assertEqual(report["synthetic_uuid_count"], 2)

    def test_exchange_fill_without_canonical_log_is_missing_canonical_log(self) -> None:
        records = build_exchange_fill_records(exchange_name="bithumb", exchange_orders=[exchange_order()], db_orders=[])

        self.assertEqual(records[0]["match_status"], "MISSING_CANONICAL_LOG")
        self.assertEqual(records[0]["exchange_order_uuid"], "C0504000000407836246")

    def test_exchange_ledger_realized_pnl_tracks_fee_once(self) -> None:
        rows = [
            {
                "market": "KRW-XLM",
                "side": "BUY",
                "quantity": 10,
                "executed_value": 1000,
                "fee": 2,
                "fee_currency": "KRW",
                "executed_at_utc": "2026-06-25T00:00:00Z",
            },
            {
                "market": "KRW-XLM",
                "side": "SELL",
                "quantity": 10,
                "executed_value": 1200,
                "fee": 3,
                "fee_currency": "KRW",
                "executed_at_utc": "2026-06-25T00:10:00Z",
            },
        ]

        pnl = compute_realized_pnl_from_ledger(rows)

        self.assertEqual(pnl["exchange_gross_realized_pnl_before_fee"], 200)
        self.assertEqual(pnl["exchange_realized_fee"], 5)
        self.assertEqual(pnl["exchange_net_realized_pnl_after_fee"], 195)

    def test_snapshot_valuation_replaces_stale_db_price(self) -> None:
        summary = build_position_valuation_summary(
            positions=[
                {
                    "id": 12,
                    "market": "KRW-XLM",
                    "status": "OPEN",
                    "entry_volume": 10,
                    "entry_amount_krw": 1000,
                    "current_price": 120,
                }
            ],
            balances={"by_currency": {"XLM": {"balance": 10, "locked": 0}}},
            valuation_prices={"KRW-XLM": 90},
            balance_snapshot_at_utc="2026-06-25T00:00:00Z",
            valuation_price_snapshot_at_utc="2026-06-25T00:00:01Z",
        )

        self.assertEqual(summary["snapshot_unrealized_pnl"], -100)
        self.assertEqual(summary["stale_valuation_effect"], -300)

    def test_trading_reconciliation_requires_auth_and_sanitizes_response(self) -> None:
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "ADMIN_PASSWORD_HASH": "sha256$not-used",
                "SESSION_SECRET": "test-secret",
            },
            clear=False,
        ):
            from app.main import app

            client = TestClient(app)
            response = client.get("/api/trading-reconciliation")
            self.assertEqual(response.status_code, 401)

        with patch.dict(os.environ, {"APP_ENV": "development"}, clear=False):
            from app import main

            with patch.object(main, "_asset_reconciliation_from_exchange", new=AsyncMock(return_value={
                "initial_equity": 300000,
                "current_equity_from_exchange": 300000,
                "current_cash_krw": 300000,
                "current_coin_market_value": 0,
                "deposits": 0,
                "withdrawals": 0,
                "exchange_fills_ledger_summary": {},
                "exchange_realized_pnl": {},
                "position_valuation_summary": {},
                "secret_key": "SHOULD_NOT_LEAK",
                "raw_auth_header": "SHOULD_NOT_LEAK",
            })):
                response = TestClient(main.app).get("/api/trading-reconciliation")
                payload = response.json()

        rendered = str(payload)
        self.assertNotIn("SHOULD_NOT_LEAK", rendered)
        self.assertNotIn("secret_key", rendered)
        self.assertNotIn("raw_auth_header", rendered)

    def test_exchange_fill_ownership_and_scope_are_classified(self) -> None:
        report = build_exchange_fill_accounting_report(
            ledger_rows=[
                {
                    "exchange_order_uuid": "C0504000000407836246",
                    "client_order_id": "client-1",
                    "market": "KRW-XLM",
                    "symbol": "XLM",
                    "side": "BUY",
                    "quantity": 10,
                    "executed_value": 1000,
                    "fee": 0,
                    "fee_currency": "KRW",
                    "executed_at_utc": "2026-06-25T00:00:00Z",
                },
                {
                    "exchange_order_uuid": "C0101000003115587466",
                    "client_order_id": "",
                    "market": "KRW-BTC",
                    "symbol": "BTC",
                    "side": "BUY",
                    "quantity": 1,
                    "executed_value": 2000,
                    "fee": 0,
                    "fee_currency": "KRW",
                    "executed_at_utc": "2026-06-24T00:00:00Z",
                },
                {
                    "exchange_order_uuid": "C0786000000800146701",
                    "client_order_id": "",
                    "market": "KRW-WLD",
                    "symbol": "WLD",
                    "side": "BUY",
                    "quantity": 1,
                    "executed_value": 3000,
                    "fee": 0,
                    "fee_currency": "KRW",
                    "executed_at_utc": "2026-06-25T00:00:00Z",
                },
            ],
            canonical_db_orders=[db_order()],
            all_db_orders=[db_order()],
            sessions=[],
            position_fill_events=[{"id": 1, "order_uuid": "C0504000000407836246"}],
            trade_outcome_logs=[{"id": 2, "order_uuid": "C0504000000407836246"}],
            period_start_utc="2026-06-25T00:00:00Z",
            period_end_utc="2026-06-26T00:00:00Z",
        )

        self.assertEqual(report["ownership_summary"]["BOT_LIVE_CONFIRMED"]["count"], 1)
        self.assertEqual(report["ownership_summary"]["OUT_OF_RECONCILIATION_SCOPE"]["count"], 1)
        self.assertEqual(report["ownership_summary"]["MANUAL_OR_EXTERNAL"]["count"], 1)

    def test_bot_owned_pnl_is_separate_from_all_fill_pnl(self) -> None:
        report = build_exchange_fill_accounting_report(
            ledger_rows=[
                {
                    "exchange_order_uuid": "C0504000000407836246",
                    "client_order_id": "client-1",
                    "market": "KRW-XLM",
                    "symbol": "XLM",
                    "side": "BUY",
                    "quantity": 10,
                    "executed_value": 1000,
                    "fee": 0,
                    "fee_currency": "KRW",
                    "executed_at_utc": "2026-06-25T00:00:00Z",
                },
                {
                    "exchange_order_uuid": "C0504000000407836247",
                    "client_order_id": "client-1",
                    "market": "KRW-XLM",
                    "symbol": "XLM",
                    "side": "SELL",
                    "quantity": 10,
                    "executed_value": 900,
                    "fee": 0,
                    "fee_currency": "KRW",
                    "executed_at_utc": "2026-06-25T01:00:00Z",
                },
                {
                    "exchange_order_uuid": "C0101000003115587466",
                    "client_order_id": "",
                    "market": "KRW-BTC",
                    "symbol": "BTC",
                    "side": "SELL",
                    "quantity": 1,
                    "executed_value": 5000,
                    "fee": 0,
                    "fee_currency": "KRW",
                    "executed_at_utc": "2026-06-25T02:00:00Z",
                },
            ],
            canonical_db_orders=[
                db_order(order_uuid="C0504000000407836246", side="BUY", executed_volume=10, filled_amount_krw=1000),
                db_order(id=8, order_uuid="C0504000000407836247", side="SELL", executed_volume=10, filled_amount_krw=900),
            ],
            all_db_orders=[
                db_order(order_uuid="C0504000000407836246", side="BUY", executed_volume=10, filled_amount_krw=1000),
                db_order(id=8, order_uuid="C0504000000407836247", side="SELL", executed_volume=10, filled_amount_krw=900),
            ],
            sessions=[],
            position_fill_events=[],
            trade_outcome_logs=[],
            period_start_utc="2026-06-25T00:00:00Z",
            period_end_utc="2026-06-26T00:00:00Z",
        )

        pnl = report["pnl_by_ownership"]
        self.assertEqual(pnl["exchange_net_realized_pnl_after_fee_bot_owned"], -100)
        self.assertNotEqual(pnl["exchange_net_realized_pnl_after_fee_all_fills"], pnl["exchange_net_realized_pnl_after_fee_bot_owned"])
        self.assertEqual(report["missing_fill_breakdown"]["missing_live_position_accounting_fill_count"], 2)

    def test_db_order_match_without_canonical_log_is_split_from_canonical_missing(self) -> None:
        report = build_exchange_fill_accounting_report(
            ledger_rows=[
                {
                    "exchange_order_uuid": "C0504000000407836246",
                    "client_order_id": "client-1",
                    "market": "KRW-XLM",
                    "symbol": "XLM",
                    "side": "BUY",
                    "quantity": 10,
                    "executed_value": 1000,
                    "fee": 0,
                    "fee_currency": "KRW",
                    "executed_at_utc": "2026-06-25T00:00:00Z",
                }
            ],
            canonical_db_orders=[],
            all_db_orders=[db_order()],
            sessions=[],
            position_fill_events=[],
            trade_outcome_logs=[],
            period_start_utc="2026-06-25T00:00:00Z",
            period_end_utc="2026-06-26T00:00:00Z",
        )

        missing = report["missing_fill_breakdown"]
        self.assertEqual(missing["db_order_matched_fill_count"], 1)
        self.assertEqual(missing["missing_db_order_fill_count"], 0)
        self.assertEqual(missing["canonical_live_log_matched_fill_count"], 0)
        self.assertEqual(missing["missing_canonical_live_log_fill_count"], 1)
        self.assertEqual(report["accounting_legacy_missing_canonical_log_count"], 1)
        self.assertEqual(report["accounting_status_summary"]["ACCOUNTING_LEGACY_MISSING_CANONICAL_LOG"]["count"], 1)
        trace = report["missing_fill_trace"][0]
        self.assertEqual(trace["exchange_order_uuid"], "C0504000000407836246")
        self.assertFalse(trace["canonical_filled_log_exists"])
        self.assertIn("MISSING_FILLED_EVENT_ROW", trace["missing_reasons"])
        self.assertIn("LEGACY_SCHEMA_NO_FILL_ROW", trace["missing_reasons"])

    def test_ledger_strategy_and_symbol_pnl_are_source_of_truth(self) -> None:
        buy = {
            "exchange_order_uuid": "C0504000000407836246",
            "client_order_id": "client-1",
            "market": "KRW-XLM",
            "symbol": "XLM",
            "side": "BUY",
            "quantity": 10,
            "executed_value": 1000,
            "fee": 2,
            "fee_currency": "KRW",
            "executed_at_utc": "2026-06-25T00:00:00Z",
        }
        sell = {
            "exchange_order_uuid": "C0504000000407836247",
            "client_order_id": "client-2",
            "market": "KRW-XLM",
            "symbol": "XLM",
            "side": "SELL",
            "quantity": 5,
            "executed_value": 600,
            "fee": 1,
            "fee_currency": "KRW",
            "executed_at_utc": "2026-06-25T01:00:00Z",
        }
        report = build_exchange_fill_accounting_report(
            ledger_rows=[buy, sell],
            canonical_db_orders=[
                db_order(order_uuid="C0504000000407836246", side="BUY", executed_volume=10, filled_amount_krw=1000),
                db_order(id=8, client_order_id="client-2", order_uuid="C0504000000407836247", side="SELL", executed_volume=5, filled_amount_krw=600),
            ],
            all_db_orders=[
                db_order(order_uuid="C0504000000407836246", side="BUY", executed_volume=10, filled_amount_krw=1000),
                db_order(id=8, client_order_id="client-2", order_uuid="C0504000000407836247", side="SELL", executed_volume=5, filled_amount_krw=600),
            ],
            sessions=[],
            position_fill_events=[{"id": 1, "order_uuid": "C0504000000407836246"}, {"id": 2, "order_uuid": "C0504000000407836247"}],
            trade_outcome_logs=[{"id": 3, "order_uuid": "C0504000000407836246"}, {"id": 4, "order_uuid": "C0504000000407836247"}],
            valuation_prices={"KRW-XLM": 130},
            period_start_utc="2026-06-25T00:00:00Z",
            period_end_utc="2026-06-26T00:00:00Z",
        )

        self.assertEqual(report["pnl_source_of_truth"]["pnl_source_of_truth"], "EXCHANGE_FILLS_LEDGER")
        self.assertEqual(report["ledger_pnl_detail"]["pnl_accounting_method"], "FIFO")
        self.assertEqual(report["ledger_pnl_detail"]["gross_realized_pnl_before_fee"], 100)
        self.assertEqual(report["ledger_pnl_detail"]["realized_fee_total"], 2)
        self.assertEqual(report["ledger_pnl_detail"]["net_realized_pnl_after_fee"], 98)
        self.assertEqual(report["ledger_pnl_detail"]["open_position_quantity"], 5)
        self.assertEqual(report["ledger_symbol_pnl"][0]["symbol"], "XLM")
        self.assertEqual(report["ledger_symbol_pnl"][0]["total_pnl"], 247.675)
        self.assertEqual(report["ledger_strategy_pnl"][0]["strategy_name"], "smart_autonomous")

    def test_fifo_trace_warns_when_sell_exceeds_open_quantity(self) -> None:
        pnl = compute_realized_pnl_from_ledger(
            [
                {
                    "exchange_order_uuid": "C0504000000407836247",
                    "market": "KRW-XLM",
                    "symbol": "XLM",
                    "side": "SELL",
                    "quantity": 5,
                    "executed_value": 600,
                    "fee": 1,
                    "fee_currency": "KRW",
                    "executed_at_utc": "2026-06-25T01:00:00Z",
                }
            ]
        )

        self.assertEqual(pnl["fifo_trace_summary"]["sell_exceeds_open_quantity_count"], 1)
        self.assertIn("SELL_EXCEEDS_OPEN_QUANTITY", pnl["fifo_trace"][0]["warnings"])
        self.assertEqual(pnl["unpaired_sell_value_krw"], 600)

    def test_duplicate_fill_key_is_flagged_in_fifo_trace(self) -> None:
        fill = {
            "fill_key": "same-fill",
            "exchange_order_uuid": "C0504000000407836246",
            "market": "KRW-XLM",
            "symbol": "XLM",
            "side": "BUY",
            "quantity": 5,
            "executed_value": 500,
            "fee": 1,
            "fee_currency": "KRW",
            "executed_at_utc": "2026-06-25T00:00:00Z",
        }

        pnl = compute_realized_pnl_from_ledger([fill, dict(fill)])

        self.assertEqual(pnl["fifo_trace_summary"]["duplicate_fill_key_count"], 1)
        self.assertIn("DUPLICATE_FILL_KEY_REAPPLIED", pnl["fifo_trace"][1]["warnings"])


if __name__ == "__main__":
    unittest.main()
