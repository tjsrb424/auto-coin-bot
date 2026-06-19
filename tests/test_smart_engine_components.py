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
from app.smart_attack import apply_aggressive_target_layer, calculate_attack_score
from app.smart_decision import _order_intent


class SmartEngineComponentTests(unittest.TestCase):
    def build_readiness_with_empty_db(self, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            with patch.object(database, "DB_PATH", db_path):
                database.init_db()
                return build_limited_readiness(**kwargs)

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

    def test_promotion_live_mode_allows_ready_for_live_without_shadow_report(self) -> None:
        with patch.dict(os.environ, {"SMART_ENGINE_LIVE_MODE": "live", "RISK_MAX_ORDER_KRW": "300000"}, clear=False):
            result = evaluate_promotion(
                intent={"side": "BID", "delta_value_krw": 120_000},
                snapshot={"current_bot_position_value_krw": 0, "risk_score": 35},
                policy={"auto_trading_enabled": True, "max_total_exposure_krw": 500_000},
                risk_preview={"allowed": True},
                available_krw=500_000,
                now_utc=datetime(2026, 6, 18, 3, tzinfo=timezone.utc),
            )

        self.assertEqual(result["promotion_status"], "READY_FOR_LIVE")
        self.assertNotIn("SMART_SHADOW_REPORT_NOT_READY", result["promotion_blockers"])

    def test_promotion_records_smart_insufficient_krw_balance(self) -> None:
        with patch.dict(os.environ, {"SMART_ENGINE_LIVE_MODE": "live", "RISK_MAX_ORDER_KRW": "300000"}, clear=False):
            result = evaluate_promotion(
                intent={"side": "BID", "delta_value_krw": 120_000},
                snapshot={"current_bot_position_value_krw": 0, "risk_score": 35},
                policy={"auto_trading_enabled": True, "max_total_exposure_krw": 500_000},
                risk_preview={"allowed": True},
                available_krw=50_000,
                now_utc=datetime(2026, 6, 18, 3, tzinfo=timezone.utc),
            )

        self.assertEqual(result["promotion_status"], "BLOCKED")
        self.assertIn("SMART_INSUFFICIENT_KRW_BALANCE", result["promotion_blockers"])

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

    def test_aggressive_breakout_uses_higher_target(self) -> None:
        attack = calculate_attack_score(
            market_regime="BREAKOUT",
            internal_signals={},
            features={
                "ma_5": 110,
                "ma_20": 105,
                "ma_60": 100,
                "ma_20_slope": 0.2,
                "volume_ratio_20": 1.8,
                "recent_return_1h": 0.8,
                "recent_return_24h": 2.0,
            },
            external_factors={"providers": {"btc_usd_momentum": {"value": 2.0, "stale": False}, "fear_greed_score": {"value": 55, "stale": False}}},
            risk_score=35,
            current_position_pnl_pct=0.4,
            current_exposure_pct=10,
        )
        result = apply_aggressive_target_layer(
            market_regime="BREAKOUT",
            conservative_target_exposure_pct=40,
            attack_result=attack,
            current_exposure_pct=10,
            current_position_pnl_pct=0.0,
            current_price=100,
            highest_price_since_entry=None,
            risk_blockers=[],
        )
        self.assertGreaterEqual(attack["attack_score"], 80)
        self.assertGreater(result["target_exposure_pct"], 40)
        self.assertEqual(result["aggressive_target_exposure_pct"], 85)
        self.assertEqual(result["final_target_exposure_source"], "AGGRESSIVE")
        self.assertEqual(result["action_hint"], "BUY_MORE")

    def test_aggressive_trend_down_is_blocked(self) -> None:
        attack = calculate_attack_score(
            market_regime="TREND_DOWN",
            internal_signals={},
            features={"ma_5": 110, "ma_20": 105, "ma_60": 100, "ma_20_slope": 0.4, "volume_ratio_20": 2.0, "recent_return_1h": 1.2, "recent_return_24h": 4.0},
            external_factors={"providers": {"btc_usd_momentum": {"value": 3.0, "stale": False}, "fear_greed_score": {"value": 50, "stale": False}}},
            risk_score=35,
            current_position_pnl_pct=1.0,
            current_exposure_pct=30,
        )
        result = apply_aggressive_target_layer(
            market_regime="TREND_DOWN",
            conservative_target_exposure_pct=70,
            attack_result=attack,
            current_exposure_pct=30,
            current_position_pnl_pct=1.0,
            current_price=100,
            highest_price_since_entry=101,
            risk_blockers=[],
        )
        self.assertLessEqual(result["target_exposure_pct"], 10)
        self.assertIn("SMART_AGGRESSIVE_TREND_DOWN_BLOCKED", result["blockers"])

    def test_aggressive_blocks_averaging_down(self) -> None:
        attack = {"attack_score": 90, "attack_mode": "MAX_AGGRESSIVE", "positive_reasons": [], "negative_reasons": [], "blockers": [], "score_breakdown": {}}
        result = apply_aggressive_target_layer(
            market_regime="BREAKOUT",
            conservative_target_exposure_pct=40,
            attack_result=attack,
            current_exposure_pct=30,
            current_position_pnl_pct=-0.5,
            current_price=100,
            highest_price_since_entry=102,
            risk_blockers=[],
        )
        self.assertLessEqual(result["target_exposure_pct"], 30)
        self.assertNotEqual(result["action_hint"], "BUY_MORE")
        self.assertIn("SMART_AGGRESSIVE_NO_AVERAGING_DOWN", result["blockers"])

    def test_aggressive_allows_profitable_pyramiding(self) -> None:
        attack = {"attack_score": 78, "attack_mode": "AGGRESSIVE", "positive_reasons": [], "negative_reasons": [], "blockers": [], "score_breakdown": {}}
        result = apply_aggressive_target_layer(
            market_regime="BREAKOUT",
            conservative_target_exposure_pct=40,
            attack_result=attack,
            current_exposure_pct=20,
            current_position_pnl_pct=0.6,
            current_price=101,
            highest_price_since_entry=101,
            risk_blockers=[],
        )
        self.assertTrue(result["pyramiding_allowed"])
        self.assertEqual(result["action_hint"], "BUY_MORE")

    def test_aggressive_creates_partial_take_profit_candidate(self) -> None:
        attack = {"attack_score": 40, "attack_mode": "OFF", "positive_reasons": [], "negative_reasons": [], "blockers": ["SMART_AGGRESSIVE_OVERHEATED_BLOCKED"], "score_breakdown": {}}
        result = apply_aggressive_target_layer(
            market_regime="OVERHEATED",
            conservative_target_exposure_pct=70,
            attack_result=attack,
            current_exposure_pct=70,
            current_position_pnl_pct=1.6,
            current_price=102.8,
            highest_price_since_entry=103,
            risk_blockers=[],
        )
        self.assertEqual(result["action_hint"], "TAKE_PROFIT_PARTIAL")
        self.assertEqual(result["final_target_exposure_source"], "PARTIAL_TAKE_PROFIT")
        self.assertTrue(result["partial_take_profit_triggered"])

    def test_aggressive_trailing_stop_candidate(self) -> None:
        attack = {"attack_score": 72, "attack_mode": "AGGRESSIVE", "positive_reasons": [], "negative_reasons": [], "blockers": [], "score_breakdown": {}}
        result = apply_aggressive_target_layer(
            market_regime="TREND_UP",
            conservative_target_exposure_pct=60,
            attack_result=attack,
            current_exposure_pct=60,
            current_position_pnl_pct=2.0,
            current_price=102.2,
            highest_price_since_entry=103,
            risk_blockers=[],
        )
        self.assertIn(result["action_hint"], {"EXIT", "REDUCE"})
        self.assertEqual(result["final_target_exposure_source"], "TRAILING_EXIT")

    def test_core_exposure_applies_in_range(self) -> None:
        with patch.dict(os.environ, {"SMART_CORE_EXPOSURE_ENABLED": "true", "SMART_MIN_CORE_EXPOSURE_PCT": "30"}, clear=False):
            result = apply_aggressive_target_layer(
                market_regime="RANGE",
                conservative_target_exposure_pct=18,
                attack_result={"attack_score": 20, "attack_mode": "OFF", "positive_reasons": [], "negative_reasons": [], "blockers": [], "score_breakdown": {}},
                current_exposure_pct=0,
                current_position_pnl_pct=0,
                current_price=100,
                highest_price_since_entry=None,
                risk_blockers=[],
            )
        self.assertGreaterEqual(result["target_exposure_pct"], 30)
        self.assertEqual(result["final_target_exposure_source"], "CORE")
        self.assertTrue(result["core_exposure_applied"])

    def test_core_exposure_reduces_in_trend_down(self) -> None:
        with patch.dict(os.environ, {"SMART_CORE_EXPOSURE_ENABLED": "true", "SMART_MIN_CORE_EXPOSURE_PCT": "30", "SMART_TREND_DOWN_CORE_EXPOSURE_PCT": "15"}, clear=False):
            result = apply_aggressive_target_layer(
                market_regime="TREND_DOWN",
                conservative_target_exposure_pct=70,
                attack_result={"attack_score": 80, "attack_mode": "MAX_AGGRESSIVE", "positive_reasons": [], "negative_reasons": [], "blockers": ["SMART_AGGRESSIVE_TREND_DOWN_BLOCKED"], "score_breakdown": {}},
                current_exposure_pct=30,
                current_position_pnl_pct=0,
                current_price=101,
                highest_price_since_entry=101,
                risk_blockers=[],
            )
        self.assertLessEqual(result["target_exposure_pct"], 15)
        self.assertEqual(result["final_target_exposure_source"], "CORE_REDUCED")

    def test_panic_can_break_core_exposure(self) -> None:
        with patch.dict(os.environ, {"SMART_CORE_EXPOSURE_ENABLED": "true", "SMART_PANIC_CAN_BREAK_CORE": "true"}, clear=False):
            result = apply_aggressive_target_layer(
                market_regime="PANIC",
                conservative_target_exposure_pct=30,
                attack_result={"attack_score": 20, "attack_mode": "OFF", "positive_reasons": [], "negative_reasons": [], "blockers": ["SMART_AGGRESSIVE_PANIC_BLOCKED"], "score_breakdown": {}},
                current_exposure_pct=30,
                current_position_pnl_pct=-1,
                current_price=100,
                highest_price_since_entry=105,
                risk_blockers=[],
            )
        self.assertEqual(result["target_exposure_pct"], 0)
        self.assertTrue(result["core_exposure_broken_by_panic"])
        self.assertEqual(result["action_hint"], "EXIT")

    def test_trend_down_buy_blocker_does_not_block_reduce_intent(self) -> None:
        snapshot = {
            "exchange": "bithumb",
            "market": "KRW-BTC",
            "target_exposure_pct": 15,
            "current_exposure_pct": 30,
            "action_hint": "REDUCE",
            "attack_score": 80,
            "attack_mode": "MAX_AGGRESSIVE",
            "final_target_exposure_source": "CORE_REDUCED",
            "aggressive_buy_blockers": ["SMART_AGGRESSIVE_TREND_DOWN_BLOCKED"],
            "aggressive_warnings": ["SMART_AGGRESSIVE_TREND_DOWN_BLOCKED"],
        }
        intent = _order_intent(snapshot_id=1, snapshot=snapshot, max_total_exposure_krw=100_000, current_value=30_000, current_price=100, blockers=["SMART_AGGRESSIVE_TREND_DOWN_BLOCKED"])
        self.assertIsNotNone(intent)
        self.assertEqual(intent["side"], "ASK")
        self.assertEqual(intent["status"], "CREATED")
        self.assertNotIn("SMART_AGGRESSIVE_TREND_DOWN_BLOCKED", intent["blockers"])
        self.assertIn("SMART_AGGRESSIVE_TREND_DOWN_BLOCKED", intent["policy_preview"]["aggressive_warnings"])

    def test_overheated_buy_blocker_does_not_block_take_profit_intent(self) -> None:
        snapshot = {
            "exchange": "bithumb",
            "market": "KRW-BTC",
            "target_exposure_pct": 49,
            "current_exposure_pct": 70,
            "action_hint": "TAKE_PROFIT_PARTIAL",
            "attack_score": 40,
            "attack_mode": "OFF",
            "final_target_exposure_source": "PARTIAL_TAKE_PROFIT",
            "partial_take_profit_pct": 30,
            "aggressive_buy_blockers": ["SMART_AGGRESSIVE_OVERHEATED_BLOCKED"],
            "aggressive_warnings": ["SMART_AGGRESSIVE_OVERHEATED_BLOCKED"],
        }
        intent = _order_intent(snapshot_id=1, snapshot=snapshot, max_total_exposure_krw=100_000, current_value=70_000, current_price=100, blockers=["SMART_AGGRESSIVE_OVERHEATED_BLOCKED"])
        self.assertIsNotNone(intent)
        self.assertEqual(intent["side"], "ASK")
        self.assertEqual(intent["status"], "CREATED")
        self.assertNotIn("SMART_AGGRESSIVE_OVERHEATED_BLOCKED", intent["blockers"])

    def test_limited_readiness_summarizes_preflight_checks(self) -> None:
        readiness = self.build_readiness_with_empty_db(
            market="KRW-BTC",
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
        readiness = self.build_readiness_with_empty_db(
            market="KRW-BTC",
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

    def test_limited_readiness_accepts_active_weekly_market_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            with patch.object(database, "DB_PATH", db_path):
                database.init_db()
                database.insert_live_order_log({
                    "request_id": "smart-rehearsal-reviewed-before",
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
                    request_id="smart-rehearsal-reviewed-before",
                    exchange="bithumb",
                    market="KRW-BTC",
                    decision="APPROVED",
                    note="weekly approval",
                )
                database.insert_live_order_log({
                    "request_id": "smart-rehearsal-new-weekly-candidate",
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
        self.assertEqual(readiness["latest_rehearsal_order"]["request_id"], "smart-rehearsal-new-weekly-candidate")
        self.assertEqual(readiness["latest_rehearsal_order"]["review_status"], "APPROVED")
        self.assertNotIn("SMART_REHEARSAL_REVIEW_REQUIRED", readiness["rehearsal_blockers"])


if __name__ == "__main__":
    unittest.main()
