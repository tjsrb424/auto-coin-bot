from __future__ import annotations

from datetime import datetime, timezone
from unittest import TestCase

from app.small_position_resolver import (
    DUST_HOLD,
    DUST_POSITION,
    FULL_EXIT_CANDIDATE,
    HOLD,
    SMALL_POSITION,
    classify_position_size,
    evaluate_small_position_resolution,
)


class SmallPositionResolverTests(TestCase):
    def position(self, *, entry_price: float = 110_000_000, entry_volume: float = 0.0001) -> dict:
        return {
            "entry_price": entry_price,
            "entry_volume": entry_volume,
            "entry_amount_krw": entry_price * entry_volume,
            "opened_at": "2026-06-19T01:00:00Z",
        }

    def test_classifies_dust_small_and_normal_positions(self) -> None:
        self.assertEqual(classify_position_size(4_999, min_order_krw=5_000), DUST_POSITION)
        self.assertEqual(classify_position_size(5_000, min_order_krw=5_000), SMALL_POSITION)
        self.assertEqual(classify_position_size(14_999, min_order_krw=5_000), SMALL_POSITION)
        self.assertEqual(classify_position_size(15_000, min_order_krw=5_000), "NORMAL_POSITION")

    def test_small_losing_position_with_weak_edge_becomes_full_exit_candidate(self) -> None:
        preview = evaluate_small_position_resolution(
            position=self.position(),
            current_price=100_000_000,
            min_order_krw=5_000,
            smart_snapshot={"market_regime": "RANGE"},
            intent={
                "action_hint": "HOLD_POSITION",
                "policy_preview": {"adaptive_edge": {"adaptive_edge_score": -0.3, "edge_confidence": 40}},
            },
            sellable_value_krw=10_000,
            now_utc=datetime(2026, 6, 19, 2, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(preview["classification"], SMALL_POSITION)
        self.assertEqual(preview["recommended_action"], FULL_EXIT_CANDIDATE)
        self.assertTrue(preview["full_exit_allowed"])
        self.assertEqual(preview["blockers"], [])

    def test_small_position_with_trend_up_and_positive_edge_holds(self) -> None:
        preview = evaluate_small_position_resolution(
            position=self.position(entry_price=100_000_000),
            current_price=100_000_000,
            min_order_krw=5_000,
            smart_snapshot={"market_regime": "TREND_UP"},
            intent={
                "action_hint": "HOLD_POSITION",
                "policy_preview": {"adaptive_edge": {"adaptive_edge_score": 0.4, "edge_confidence": 35}},
            },
            sellable_value_krw=10_000,
            now_utc=datetime(2026, 6, 19, 2, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(preview["recommended_action"], HOLD)
        self.assertIn("MARKET_REGIME_TREND_UP", preview["blockers"])
        self.assertIn("ADAPTIVE_EDGE_POSITIVE", preview["blockers"])

    def test_dust_below_min_order_is_not_sell_candidate(self) -> None:
        preview = evaluate_small_position_resolution(
            position=self.position(entry_price=100_000_000, entry_volume=0.00004),
            current_price=100_000_000,
            min_order_krw=5_000,
            smart_snapshot={"market_regime": "RANGE"},
            intent={"action_hint": "EXIT", "policy_preview": {"adaptive_edge": {"adaptive_edge_score": -0.5}}},
            sellable_value_krw=4_000,
            now_utc=datetime(2026, 6, 19, 2, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(preview["classification"], DUST_POSITION)
        self.assertEqual(preview["recommended_action"], DUST_HOLD)
        self.assertFalse(preview["full_exit_allowed"])
        self.assertIn("SELLABLE_VALUE_BELOW_MIN_ORDER", preview["blockers"])

    def test_open_order_or_balance_mismatch_blocks_small_position_exit(self) -> None:
        preview = evaluate_small_position_resolution(
            position=self.position(),
            current_price=100_000_000,
            min_order_krw=5_000,
            smart_snapshot={"market_regime": "RANGE", "balance_mismatch_detected": True, "open_order_mismatch_detected": True},
            intent={"action_hint": "EXIT", "policy_preview": {"adaptive_edge": {"adaptive_edge_score": -0.5}}},
            sellable_value_krw=10_000,
            now_utc=datetime(2026, 6, 19, 2, 0, tzinfo=timezone.utc),
        )

        self.assertFalse(preview["full_exit_allowed"])
        self.assertIn("BALANCE_MISMATCH", preview["blockers"])
        self.assertIn("OPEN_ORDER_MISMATCH", preview["blockers"])
