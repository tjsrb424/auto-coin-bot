from __future__ import annotations

import unittest
from unittest.mock import patch

from app.dynamic_sizing import build_dynamic_sizing_preview


POSITIVE_EDGE = {
    "adaptive_edge_score": 1.2,
    "edge_confidence": 80,
    "avg_post_fill_return_5m": 1.0,
    "avg_post_fill_return_15m": 1.5,
    "avg_realized_return_pct": 1.2,
    "avg_adverse_selection_pct": 0.1,
    "avg_slippage_pct": 0.05,
}

NEGATIVE_EDGE = {
    "adaptive_edge_score": -1.4,
    "edge_confidence": 85,
    "avg_post_fill_return_5m": -1.0,
    "avg_post_fill_return_15m": -1.5,
    "avg_realized_return_pct": -1.2,
    "avg_adverse_selection_pct": 0.8,
    "avg_slippage_pct": 0.2,
}


class DynamicSizingTests(unittest.TestCase):
    def test_disabled_mode_records_shadow_without_changing_amount(self) -> None:
        with patch.dict("os.environ", {"SMART_DYNAMIC_SIZING_ENABLED": "false"}, clear=False):
            preview = build_dynamic_sizing_preview(
                original_amount_krw=10_000,
                adaptive_edge=POSITIVE_EDGE,
                fee_pct=0.05,
                max_allowed_amount_krw=15_000,
                min_order_krw=5_000,
            )

        self.assertFalse(preview["enabled"])
        self.assertTrue(preview["shadow_only"])
        self.assertEqual(preview["applied_amount_krw"], 10_000)
        self.assertEqual(preview["adjusted_amount_krw"], 10_000)

    def test_apply_mode_increases_positive_edge_with_cap(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "SMART_DYNAMIC_SIZING_ENABLED": "true",
                "SMART_DYNAMIC_SIZING_MODE": "apply",
                "SMART_DYNAMIC_SIZING_MAX_MULTIPLIER": "1.5",
            },
            clear=False,
        ):
            preview = build_dynamic_sizing_preview(
                original_amount_krw=10_000,
                adaptive_edge=POSITIVE_EDGE,
                fee_pct=0.05,
                max_allowed_amount_krw=12_000,
                min_order_krw=5_000,
            )

        self.assertTrue(preview["enabled"])
        self.assertEqual(preview["mode"], "apply")
        self.assertGreater(preview["sizing_multiplier"], 1.0)
        self.assertEqual(preview["adjusted_amount_krw"], 12_000)
        self.assertEqual(preview["applied_amount_krw"], 12_000)

    def test_negative_edge_reduces_but_shadow_keeps_actual_amount(self) -> None:
        with patch.dict("os.environ", {"SMART_DYNAMIC_SIZING_ENABLED": "true", "SMART_DYNAMIC_SIZING_MODE": "shadow"}, clear=False):
            preview = build_dynamic_sizing_preview(
                original_amount_krw=10_000,
                adaptive_edge=NEGATIVE_EDGE,
                fee_pct=0.05,
                max_allowed_amount_krw=20_000,
                min_order_krw=5_000,
            )

        self.assertEqual(preview["mode"], "shadow")
        self.assertLess(preview["sizing_multiplier"], 1.0)
        self.assertLess(preview["adjusted_amount_krw"], 10_000)
        self.assertEqual(preview["applied_amount_krw"], 10_000)
        self.assertLess(preview["net_edge_pct"], 0)

    def test_low_confidence_uses_default_multiplier(self) -> None:
        edge = {**POSITIVE_EDGE, "edge_confidence": 10}
        with patch.dict("os.environ", {"SMART_DYNAMIC_SIZING_ENABLED": "true", "SMART_DYNAMIC_SIZING_MODE": "apply"}, clear=False):
            preview = build_dynamic_sizing_preview(
                original_amount_krw=10_000,
                adaptive_edge=edge,
                fee_pct=0.05,
                max_allowed_amount_krw=20_000,
                min_order_krw=5_000,
            )

        self.assertFalse(preview["confidence_ok"])
        self.assertEqual(preview["sizing_multiplier"], 1.0)
        self.assertEqual(preview["applied_amount_krw"], 10_000)

    def test_block_mode_blocks_non_positive_net_edge(self) -> None:
        with patch.dict("os.environ", {"SMART_DYNAMIC_SIZING_ENABLED": "true", "SMART_DYNAMIC_SIZING_MODE": "block"}, clear=False):
            preview = build_dynamic_sizing_preview(
                original_amount_krw=10_000,
                adaptive_edge=NEGATIVE_EDGE,
                fee_pct=0.05,
                max_allowed_amount_krw=20_000,
                min_order_krw=5_000,
            )

        self.assertFalse(preview["allowed"])
        self.assertEqual(preview["blocker"], "SMART_DYNAMIC_SIZING_NET_EDGE_NON_POSITIVE")


if __name__ == "__main__":
    unittest.main()
