from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app import database
from app.exchange_fills_ledger import (
    build_exchange_fill_records,
    build_position_valuation_summary,
    compute_realized_pnl_from_ledger,
    is_real_exchange_order_uuid,
    persist_exchange_fill_records,
    real_duplicate_exchange_uuid_groups,
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


if __name__ == "__main__":
    unittest.main()
