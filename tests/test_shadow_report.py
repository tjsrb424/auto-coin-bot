from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database
from app.shadow_report import build_shadow_report


class ShadowReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        database.init_db()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_shadow_report_scores_future_markout_for_actionable_intent(self) -> None:
        database.insert_candles([
            candle("2026-06-18T00:00:00", 100_000_000),
            candle("2026-06-18T00:15:00", 101_000_000),
            candle("2026-06-18T00:30:00", 102_000_000),
            candle("2026-06-18T00:45:00", 103_000_000),
        ])
        snapshot_id = database.insert_decision_snapshot({
            "decided_at": "2026-06-18T00:00:10Z",
            "exchange": "bithumb",
            "market": "KRW-BTC",
            "timeframe": "15m",
            "candle_time_utc": "2026-06-18T00:00:00",
            "candle_time_kst": "2026-06-18T09:00:00",
            "selected_strategy_id": 1,
            "selected_strategy_name": "shadow",
            "legacy_signal": "BUY",
            "market_regime": "TREND_UP",
            "current_bot_position_qty": 0,
            "current_bot_position_value_krw": 0,
            "current_exposure_pct": 0,
            "target_exposure_pct": 40,
            "action_hint": "BUY_MORE",
            "confidence_score": 72,
            "risk_score": 35,
            "one_line_summary": "Shadow buy candidate.",
            "positive_reasons": ["trend"],
            "negative_reasons": [],
            "blockers": ["SMART_SHADOW_MODE"],
            "raw_features": {"last_price": 100_000_000},
            "external_factors": {},
            "internal_signals": {},
            "max_total_exposure_krw": 500_000,
            "daily_loss_limit_pct": 3,
            "daily_loss_limit_krw": 15_000,
            "available_krw_balance": None,
            "exposure_limit_blocked": False,
        })
        database.insert_order_intent({
            "decision_snapshot_id": snapshot_id,
            "exchange": "bithumb",
            "market": "KRW-BTC",
            "side": "BID",
            "action_hint": "BUY_MORE",
            "current_value_krw": 0,
            "target_value_krw": 200_000,
            "delta_value_krw": 200_000,
            "target_qty": 0.002,
            "order_type": "LIMIT",
            "limit_price": 100_000_000,
            "urgency": "NORMAL",
            "status": "BLOCKED",
            "blockers": ["SMART_SHADOW_MODE"],
        })

        report = build_shadow_report("KRW-BTC", limit=10, horizon_candles=3)

        self.assertEqual(report["summary"]["decision_count"], 1)
        self.assertEqual(report["summary"]["intent_count"], 1)
        self.assertEqual(report["summary"]["favorable_count"], 1)
        self.assertEqual(report["recent_rows"][0]["outcome"], "FAVORABLE")
        self.assertAlmostEqual(report["recent_rows"][0]["markout_pct"], 3.0)

    def test_shadow_report_requires_review_after_blocked_rehearsal_order(self) -> None:
        database.insert_live_order_log({
            "request_id": "smart-rehearsal-test",
            "exchange": "bithumb",
            "market": "KRW-BTC",
            "side": "BUY",
            "order_type": "LIMIT",
            "price": 100_000_000,
            "volume": 0.0001,
            "amount_krw": 10_000,
            "risk_result": "SMART_PROMOTION_BLOCKED",
            "status": "BLOCKED",
            "order_preview_payload": {},
        })

        report = build_shadow_report("KRW-BTC", limit=10, horizon_candles=3)

        self.assertEqual(report["summary"]["recommendation"], "REHEARSAL_REVIEW_REQUIRED")
        self.assertEqual(report["summary"]["rehearsal"]["blocked_count"], 1)
        self.assertTrue(report["summary"]["rehearsal"]["requires_review"])

    def test_shadow_report_does_not_require_review_for_minimum_order_block(self) -> None:
        database.insert_live_order_log({
            "request_id": "smart-rehearsal-too-small",
            "exchange": "bithumb",
            "market": "KRW-BTC",
            "side": "BUY",
            "order_type": "LIMIT",
            "price": 100_000_000,
            "volume": 0.0,
            "amount_krw": 0.145,
            "risk_result": "BLOCKED_MIN_ORDER_AMOUNT",
            "status": "BLOCKED",
            "order_preview_payload": {},
        })

        report = build_shadow_report("KRW-BTC", limit=10, horizon_candles=3)

        self.assertFalse(report["summary"]["rehearsal"]["latest_order_reviewable"])
        self.assertEqual(report["summary"]["rehearsal"]["reviewable_blocked_count"], 0)
        self.assertFalse(report["summary"]["rehearsal"]["requires_review"])
        self.assertNotEqual(report["summary"]["recommendation"], "REHEARSAL_REVIEW_REQUIRED")

    def test_shadow_report_accepts_active_approved_rehearsal_review(self) -> None:
        database.insert_live_order_log({
            "request_id": "smart-rehearsal-approved",
            "exchange": "bithumb",
            "market": "KRW-BTC",
            "side": "BUY",
            "order_type": "LIMIT",
            "price": 100_000_000,
            "volume": 0.0001,
            "amount_krw": 10_000,
            "risk_result": "SMART_PROMOTION_BLOCKED",
            "status": "BLOCKED",
            "order_preview_payload": {},
        })
        review = database.insert_smart_rehearsal_review(
            request_id="smart-rehearsal-approved",
            exchange="bithumb",
            market="KRW-BTC",
            decision="APPROVED",
            note="reviewed",
        )

        report = build_shadow_report("KRW-BTC", limit=10, horizon_candles=3)

        self.assertTrue(review["is_active"])
        self.assertFalse(report["summary"]["rehearsal"]["requires_review"])
        self.assertNotEqual(report["summary"]["recommendation"], "REHEARSAL_REVIEW_REQUIRED")

    def test_shadow_report_ignores_expired_rehearsal_review(self) -> None:
        database.insert_live_order_log({
            "request_id": "smart-rehearsal-expired",
            "exchange": "bithumb",
            "market": "KRW-BTC",
            "side": "BUY",
            "order_type": "LIMIT",
            "price": 100_000_000,
            "volume": 0.0001,
            "amount_krw": 10_000,
            "risk_result": "SMART_PROMOTION_BLOCKED",
            "status": "BLOCKED",
            "order_preview_payload": {},
        })
        database.insert_smart_rehearsal_review(
            request_id="smart-rehearsal-expired",
            exchange="bithumb",
            market="KRW-BTC",
            decision="APPROVED",
            note="reviewed",
        )
        with database.get_connection() as conn:
            conn.execute(
                "UPDATE smart_rehearsal_reviews SET expires_at = ? WHERE request_id = ?",
                ("2020-01-01T00:00:00Z", "smart-rehearsal-expired"),
            )

        report = build_shadow_report("KRW-BTC", limit=10, horizon_candles=3)

        self.assertTrue(report["summary"]["rehearsal"]["requires_review"])
        self.assertEqual(report["summary"]["recommendation"], "REHEARSAL_REVIEW_REQUIRED")


def candle(candle_time_utc: str, price: float) -> dict:
    return {
        "market": "KRW-BTC",
        "unit": 15,
        "candle_date_time_utc": candle_time_utc,
        "candle_date_time_kst": candle_time_utc,
        "opening_price": price,
        "high_price": price,
        "low_price": price,
        "trade_price": price,
        "candle_acc_trade_price": 1_000_000,
        "candle_acc_trade_volume": 1,
        "timestamp": 0,
    }


if __name__ == "__main__":
    unittest.main()
