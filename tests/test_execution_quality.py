from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database
from app.execution_quality import build_execution_quality_payload, summarize_execution_quality


class ExecutionQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        database.init_db()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_build_payload_records_sizing_slippage_and_spread(self) -> None:
        order_log = {
            "id": 1,
            "request_id": "quality-1",
            "created_at": "2026-06-23T00:00:00Z",
            "updated_at": "2026-06-23T00:01:00Z",
            "status": "FILLED",
            "exchange": "bithumb",
            "market": "KRW-BTC",
            "strategy_name": "trend_pullback",
            "price": 100_000_000,
            "filled_amount_krw": 100_500,
            "executed_volume": 0.001,
            "remaining_volume": 0,
            "amount_krw": 100_000,
        }

        payload = build_execution_quality_payload(
            order_log=order_log,
            market_regime="TREND_UP",
            sizing={"requested_order_krw": 120_000, "available_krw": 101_000, "actual_order_krw": 100_000},
            orderbook_top={"best_bid": 99_900_000, "best_ask": 100_100_000, "spread_pct": 0.2},
            current_price_at_signal=99_800_000,
        )

        self.assertEqual(payload["market_regime"], "TREND_UP")
        self.assertEqual(payload["requested_order_krw"], 120_000)
        self.assertEqual(payload["actual_order_krw"], 100_000)
        self.assertAlmostEqual(payload["filled_price"], 100_500_000)
        self.assertAlmostEqual(payload["estimated_slippage_pct"], 0.5)
        self.assertEqual(payload["fill_time_seconds"], 60)

    def test_upsert_and_summary(self) -> None:
        rows = [
            {
                "request_id": "quality-filled",
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "strategy_name": "trend_pullback",
                "filled_volume": 0.001,
                "estimated_slippage_pct": 0.2,
                "fill_time_seconds": 30,
            },
            {
                "request_id": "quality-canceled",
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "strategy_name": "trend_pullback",
                "cancel_reason": "AUTO_CANCEL_UNFILLED_TIMEOUT",
                "estimated_slippage_pct": 0.4,
            },
        ]
        for row in rows:
            database.upsert_execution_quality_log(row)

        loaded = database.load_execution_quality_logs(exchange="bithumb", market="KRW-BTC")
        summary = summarize_execution_quality(loaded)

        self.assertEqual(summary["order_count"], 2)
        self.assertEqual(summary["fill_rate"], 0.5)
        self.assertEqual(summary["cancel_rate"], 0.5)
        self.assertAlmostEqual(summary["average_slippage_pct"], 0.3)


if __name__ == "__main__":
    unittest.main()
