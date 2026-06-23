from __future__ import annotations

import unittest

from app.order_sizing import calculate_available_balance_capped_order


class OrderSizingTests(unittest.TestCase):
    def test_requested_amount_within_available_balance(self) -> None:
        result = calculate_available_balance_capped_order(
            requested_order_krw=100_000,
            available_krw=300_000,
            min_order_krw=5_000,
            fee_rate=0.0005,
            extra_fee_buffer_rate=0.0002,
        )

        self.assertTrue(result["allowed"])
        self.assertEqual(result["actual_order_krw"], 100_000)
        self.assertEqual(result["sizing_reason"], "REQUEST_WITHIN_AVAILABLE_BALANCE")

    def test_requested_amount_is_capped_by_available_balance_and_fee_buffer(self) -> None:
        result = calculate_available_balance_capped_order(
            requested_order_krw=500_000,
            available_krw=300_000,
            min_order_krw=5_000,
            fee_rate=0.0005,
            extra_fee_buffer_rate=0.0002,
        )

        self.assertTrue(result["allowed"])
        self.assertAlmostEqual(result["actual_order_krw"], 300_000 / 1.0007, places=6)
        self.assertTrue(result["cap_applied"])

    def test_available_balance_below_minimum_blocks(self) -> None:
        result = calculate_available_balance_capped_order(
            requested_order_krw=100_000,
            available_krw=3_000,
            min_order_krw=5_000,
            fee_rate=0.0005,
            extra_fee_buffer_rate=0.0002,
        )

        self.assertFalse(result["allowed"])
        self.assertEqual(result["block_code"], "ORDER_BELOW_MINIMUM")

    def test_zero_available_balance_blocks(self) -> None:
        result = calculate_available_balance_capped_order(
            requested_order_krw=100_000,
            available_krw=0,
            min_order_krw=5_000,
            fee_rate=0.0005,
            extra_fee_buffer_rate=0.0002,
        )

        self.assertFalse(result["allowed"])
        self.assertEqual(result["block_code"], "INSUFFICIENT_BALANCE")


if __name__ == "__main__":
    unittest.main()
