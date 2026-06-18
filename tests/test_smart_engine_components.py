from __future__ import annotations

import os
import unittest
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch

from app import database
from app.smart_external_factors import load_external_factors
from app.smart_market_regime import classify_market_regime
from app.smart_promotion import evaluate_promotion, evaluate_rehearsal_preview
from app.smart_readiness import build_limited_readiness
from app.smart_signal_engine import aggregate_signal_score, evaluate_internal_signals
from app.smart_target_exposure import calculate_target_exposure


class SmartEngineComponentTests(unittest.TestCase):
    def test_internal_signals_include_direction_score_confidence_reason_and_raw_value(self) -> None:
        signals = evaluate_internal_signals(
            {"signal": "BUY", "reason": "legacy buy"},
            {
                "rsi_14": 31,
                "ma_5_20_gap_pct": 1.2,
                "ma_20_slope": 0.08,
                "volume_ratio_20": 1.6,
                "recent_return_1h": 0.8,
                "volatility_1h": 0.8,
            },
            "BREAKOUT",
        )
        self.assertIn("rsi", signals)
        self.assertEqual(signals["legacy_strategy"]["direction"], "BULLISH")
        self.assertGreater(aggregate_signal_score(signals), 0)
        for item in signals.values():
            self.assertIn("direction", item)
            self.assertIn("score", item)
            self.assertIn("confidence", item)
            self.assertIn("reason", item)
            self.assertIn("raw_value", item)

    def test_market_regime_classifies_breakout_and_unknown(self) -> None:
        regime, positives, negatives = classify_market_regime({
            "last_price": 105,
            "ma_20": 100,
            "ma_20_slope": 0.12,
            "volume_ratio_20": 1.7,
            "rsi_14": 58,
            "recent_return_1h": 0.8,
            "volatility_1h": 0.9,
        })
        self.assertEqual(regime, "BREAKOUT")
        self.assertTrue(positives)
        unknown, _, unknown_negatives = classify_market_regime({})
        self.assertEqual(unknown, "UNKNOWN")
        self.assertTrue(unknown_negatives)

    def test_target_exposure_blocks_buy_when_policy_off_or_risk_blocked(self) -> None:
        result = calculate_target_exposure(
            current_exposure_pct=30,
            risk_score=35,
            market_regime="BREAKOUT",
            internal_signals={"legacy": {"score": 80, "confidence": 80}},
            risk_state={"status": "OK", "daily_total_pnl": 0},
            policy={"auto_trading_enabled": False, "daily_loss_limit_pct": 3},
            max_total_exposure_krw=500_000,
            current_position_value_krw=150_000,
        )
        self.assertLessEqual(result["target_exposure_pct"], 30)
        self.assertIn("SMART_POLICY_AUTO_TRADING_DISABLED", result["blockers"])

    def test_promotion_defaults_to_shadow_and_limited_respects_twenty_percent_cap(self) -> None:
        intent = {"side": "BID", "delta_value_krw": 150_000}
        snapshot = {"current_bot_position_value_krw": 0}
        policy = {"auto_trading_enabled": True, "max_total_exposure_krw": 500_000}
        with patch.dict(os.environ, {"SMART_ENGINE_LIVE_MODE": "shadow", "RISK_MAX_ORDER_KRW": "300000"}, clear=False):
            shadow = evaluate_promotion(
                intent=intent,
                snapshot=snapshot,
                policy=policy,
                risk_preview={"allowed": True},
                shadow_recommendation="READY_FOR_LIMITED_PILOT_REVIEW",
                available_krw=500_000,
                now_utc=datetime(2026, 6, 18, 3, tzinfo=timezone.utc),
            )
        self.assertEqual(shadow["promotion_status"], "SHADOW_ONLY")
        with patch.dict(os.environ, {"SMART_ENGINE_LIVE_MODE": "limited", "RISK_MAX_ORDER_KRW": "300000"}, clear=False):
            limited = evaluate_promotion(
                intent=intent,
                snapshot=snapshot,
                policy=policy,
                risk_preview={"allowed": True},
                shadow_recommendation="READY_FOR_LIMITED_PILOT_REVIEW",
                available_krw=500_000,
                now_utc=datetime(2026, 6, 18, 3, tzinfo=timezone.utc),
            )
        self.assertEqual(limited["pilot_order_cap_krw"], 100_000)
        self.assertEqual(limited["promotion_status"], "BLOCKED")
        self.assertIn("SMART_PILOT_ORDER_CAP_EXCEEDED", limited["promotion_blockers"])

    def test_promotion_limited_mode_requires_rehearsal_rules(self) -> None:
        intent = {"side": "BID", "delta_value_krw": 20_000}
        snapshot = {"current_bot_position_value_krw": 0, "risk_score": 75}
        policy = {"auto_trading_enabled": True, "max_total_exposure_krw": 500_000}
        with patch.dict(os.environ, {"SMART_ENGINE_LIVE_MODE": "limited", "RISK_MAX_ORDER_KRW": "300000"}, clear=False):
            result = evaluate_promotion(
                intent=intent,
                snapshot=snapshot,
                policy=policy,
                risk_preview={"allowed": True},
                shadow_recommendation="READY_FOR_LIMITED_PILOT_REVIEW",
                available_krw=500_000,
                daily_smart_order_count=1,
                now_utc=datetime(2026, 6, 18, 3, tzinfo=timezone.utc),
            )
        self.assertEqual(result["promotion_status"], "BLOCKED")
        self.assertIn("SMART_REHEARSAL_DAILY_ORDER_LIMIT", result["promotion_blockers"])
        self.assertIn("SMART_REHEARSAL_RISK_SCORE_TOO_HIGH", result["promotion_blockers"])
        self.assertFalse(result["policy_preview"]["rehearsal"]["allowed"])

    def test_rehearsal_daily_limit_zero_means_unlimited(self) -> None:
        with patch.dict(os.environ, {"SMART_REHEARSAL_MAX_DAILY_ORDERS": "0"}, clear=False):
            rehearsal = evaluate_rehearsal_preview(
                requested_order_krw=20_000,
                risk_score=35,
                daily_smart_order_count=99,
                now_utc=datetime(2026, 6, 18, 3, tzinfo=timezone.utc),
            )
        self.assertTrue(rehearsal["allowed"])
        self.assertNotIn("SMART_REHEARSAL_DAILY_ORDER_LIMIT", rehearsal["blockers"])

    def test_promotion_supports_limited_sell_without_exceeding_position_qty(self) -> None:
        snapshot = {"current_bot_position_value_krw": 120_000, "current_bot_position_qty": 0.002, "risk_score": 35}
        policy = {"auto_trading_enabled": True, "max_total_exposure_krw": 500_000}
        with patch.dict(os.environ, {"SMART_ENGINE_LIVE_MODE": "limited", "RISK_MAX_ORDER_KRW": "300000"}, clear=False):
            allowed = evaluate_promotion(
                intent={"side": "ASK", "delta_value_krw": -50_000, "target_qty": 0.001},
                snapshot=snapshot,
                policy=policy,
                risk_preview={"allowed": True},
                shadow_recommendation="READY_FOR_LIMITED_PILOT_REVIEW",
                now_utc=datetime(2026, 6, 18, 3, tzinfo=timezone.utc),
            )
            blocked = evaluate_promotion(
                intent={"side": "ASK", "delta_value_krw": -200_000, "target_qty": 0.003},
                snapshot=snapshot,
                policy=policy,
                risk_preview={"allowed": True},
                shadow_recommendation="READY_FOR_LIMITED_PILOT_REVIEW",
                now_utc=datetime(2026, 6, 18, 3, tzinfo=timezone.utc),
            )
        self.assertEqual(allowed["promotion_status"], "READY_FOR_LIMITED")
        self.assertEqual(blocked["promotion_status"], "BLOCKED")
        self.assertIn("SMART_SELL_QTY_EXCEEDS_POSITION", blocked["promotion_blockers"])

    def test_external_factors_connects_btc_usd_provider_and_derives_kimchi_premium(self) -> None:
        result = load_external_factors(
            "KRW-BTC",
            local_price_krw=140_000_000,
            usd_krw_rate=1400,
            fetcher=lambda: {"bitcoin": {"usd": 100_000, "usd_24h_change": 2.5}},
            fear_greed_fetcher=lambda: {"data": [{"value": "82", "value_classification": "Extreme Greed", "timestamp": "1710000000"}]},
            notice_fetcher=lambda: "정상 공지",
            news_fetcher=lambda: {"score": -35, "headline_count": 3, "negative_count": 1, "source": "test"},
        )
        providers = result["providers"]
        self.assertFalse(result["stale"])
        self.assertFalse(providers["btc_usd_momentum"]["stale"])
        self.assertEqual(providers["btc_usd_momentum"]["value"], 2.5)
        self.assertFalse(providers["kimchi_premium"]["stale"])
        self.assertEqual(providers["kimchi_premium"]["value"], 0.0)
        self.assertFalse(providers["fear_greed_score"]["stale"])
        self.assertEqual(providers["fear_greed_score"]["value"], 82.0)
        self.assertFalse(providers["news_sentiment_score"]["stale"])
        self.assertEqual(providers["news_sentiment_score"]["value"], -35.0)

    def test_exchange_notice_hard_block_and_provider_stale_are_normalized(self) -> None:
        blocked = load_external_factors(
            "KRW-BTC",
            fetcher=lambda: {"bitcoin": {"usd": 100_000, "usd_24h_change": 0}},
            fear_greed_fetcher=lambda: {"data": [{"value": "50"}]},
            notice_fetcher=lambda: "KRW-BTC 긴급 점검 및 입출금 중단 안내",
            news_fetcher=lambda: {"score": 0},
        )
        notice = blocked["providers"]["exchange_notice_risk"]
        self.assertEqual(notice["severity"], "hard")
        self.assertIn("SMART_EXCHANGE_NOTICE_RISK_BLOCK", blocked["hard_blockers"])
        stale = load_external_factors("KRW-BTC", fetcher=lambda: {}, fear_greed_fetcher=lambda: {}, notice_fetcher=lambda: (_ for _ in ()).throw(RuntimeError("down")))
        self.assertTrue(stale["providers"]["exchange_notice_risk"]["stale"])

    def test_target_exposure_applies_external_factors_conservatively(self) -> None:
        without_external = calculate_target_exposure(
            current_exposure_pct=0,
            risk_score=35,
            market_regime="BREAKOUT",
            internal_signals={"legacy": {"score": 60, "confidence": 80}},
            risk_state={"status": "OK", "daily_total_pnl": 0},
            policy={"auto_trading_enabled": True, "daily_loss_limit_pct": 3},
            max_total_exposure_krw=500_000,
            current_position_value_krw=0,
        )
        with_external = calculate_target_exposure(
            current_exposure_pct=0,
            risk_score=35,
            market_regime="BREAKOUT",
            internal_signals={"legacy": {"score": 60, "confidence": 80}},
            risk_state={"status": "OK", "daily_total_pnl": 0},
            policy={"auto_trading_enabled": True, "daily_loss_limit_pct": 3},
            max_total_exposure_krw=500_000,
            current_position_value_krw=0,
            external_factors={
                "hard_blockers": [],
                "providers": {
                    "btc_usd_momentum": {"value": 4.0, "stale": False},
                    "kimchi_premium": {"value": 6.0, "stale": False},
                    "fear_greed_score": {"value": 85, "stale": False},
                }
            },
        )
        self.assertLess(with_external["target_exposure_pct"], without_external["target_exposure_pct"])
        self.assertEqual(with_external["external_factor_adjustment_pct"], -7.6)
        self.assertTrue(with_external["negative_reasons"])

    def test_target_exposure_hard_blocks_buy_when_exchange_notice_risk_detected(self) -> None:
        result = calculate_target_exposure(
            current_exposure_pct=20,
            risk_score=25,
            market_regime="BREAKOUT",
            internal_signals={"legacy": {"score": 100, "confidence": 90}},
            risk_state={"status": "OK", "daily_total_pnl": 0},
            policy={"auto_trading_enabled": True, "daily_loss_limit_pct": 3},
            max_total_exposure_krw=500_000,
            current_position_value_krw=100_000,
            external_factors={
                "hard_blockers": ["SMART_EXCHANGE_NOTICE_RISK_BLOCK"],
                "providers": {"exchange_notice_risk": {"stale": False, "severity": "hard", "value": 1}},
            },
        )
        self.assertLessEqual(result["target_exposure_pct"], 20)
        self.assertIn("SMART_EXCHANGE_NOTICE_RISK_BLOCK", result["blockers"])

    def test_limited_readiness_summarizes_preflight_checks(self) -> None:
        readiness = build_limited_readiness(
            "KRW-BTC",
            decision={
                "risk_score": 35,
                "order_intents": [{
                    "id": 7,
                    "side": "BID",
                    "status": "CREATED",
                    "delta_value_krw": 20_000,
                    "promotion_status": "READY_FOR_LIMITED",
                }],
            },
            report={"summary": {"recommendation": "READY_FOR_LIMITED_PILOT_REVIEW"}},
            policy={"auto_trading_enabled": True},
            risk_state={"status": "OK"},
            daily_smart_order_count=0,
            emergency_stopped=False,
            live_mode="shadow",
            now_utc=datetime(2026, 6, 18, 3, tzinfo=timezone.utc),
        )
        self.assertTrue(readiness["can_enable_limited"])
        self.assertEqual(readiness["status"], "READY_TO_ENABLE_LIMITED")
        self.assertEqual(readiness["latest_intent_summary"]["id"], 7)

    def test_limited_readiness_blocks_when_policy_or_shadow_report_not_ready(self) -> None:
        readiness = build_limited_readiness(
            "KRW-BTC",
            decision={"risk_score": 35, "order_intents": [{"side": "BID", "delta_value_krw": 20_000}]},
            report={"summary": {"recommendation": "MORE_SHADOW_DATA_REQUIRED"}},
            policy={"auto_trading_enabled": False},
            risk_state={"status": "OK"},
            daily_smart_order_count=0,
            emergency_stopped=False,
            live_mode="shadow",
            now_utc=datetime(2026, 6, 18, 3, tzinfo=timezone.utc),
        )
        blocked = {item["id"] for item in readiness["checks"] if item["status"] == "block"}
        self.assertFalse(readiness["can_enable_limited"])
        self.assertIn("policy_auto_trading", blocked)
        self.assertIn("shadow_report", blocked)

    def test_limited_readiness_does_not_require_review_for_minimum_order_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            with patch.object(database, "DB_PATH", db_path):
                database.init_db()
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
                readiness = build_limited_readiness(
                    "KRW-BTC",
                    decision={"risk_score": 35, "order_intents": [{"side": "BID", "delta_value_krw": 20_000}]},
                    report={"summary": {"recommendation": "READY_FOR_LIMITED_PILOT_REVIEW"}},
                    policy={"auto_trading_enabled": True},
                    risk_state={"status": "OK"},
                    daily_smart_order_count=0,
                    emergency_stopped=False,
                    live_mode="limited",
                    now_utc=datetime(2026, 6, 18, 3, tzinfo=timezone.utc),
                )
        self.assertTrue(readiness["latest_rehearsal_order"]["reviewable"] is False)
        self.assertNotIn("SMART_REHEARSAL_REVIEW_REQUIRED", readiness["rehearsal_blockers"])


if __name__ == "__main__":
    unittest.main()
