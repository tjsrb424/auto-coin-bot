from __future__ import annotations

import unittest

from app.strategy_validation import build_periods


class StrategyValidationTests(unittest.TestCase):
    def test_build_periods_accepts_custom_day_count_label(self) -> None:
        periods = build_periods(["45d"], None, None)

        self.assertEqual(len(periods), 1)
        self.assertEqual(periods[0]["label"], "45d")
        self.assertEqual(periods[0]["days"], 45)


if __name__ == "__main__":
    unittest.main()
