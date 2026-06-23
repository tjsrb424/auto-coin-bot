from __future__ import annotations

import unittest

from app.profit_engine import ProfitEngineConfig, evaluate_profit_entry_gate


def config(enabled: bool = True) -> ProfitEngineConfig:
    return ProfitEngineConfig(
        enabled=enabled,
        mode="aggressive",
        order_sizing_mode="available_balance_cap",
        require_auto_exit=True,
        block_entry_when_exit_disabled=True,
        allow_balance_cap=True,
        disable_percent_sizing=True,
        extra_fee_buffer_rate=0.0002,
    )


class ProfitEngineGateTests(unittest.TestCase):
    def test_blocked_market_regimes_block_buy_entries(self) -> None:
        for regime in ["PANIC", "TREND_DOWN", "OVERHEATED", "UNKNOWN"]:
            with self.subTest(regime=regime):
                result = evaluate_profit_entry_gate(
                    market_regime=regime,
                    strategy_name="trend_pullback",
                    side="BUY",
                    auto_exit_enabled=True,
                    config=config(),
                )
                self.assertFalse(result["entry_allowed"])
                self.assertEqual(result["block_code"], f"PROFIT_ENGINE_BLOCKED_{regime}")

    def test_allowed_regime_strategy_pairs_pass(self) -> None:
        pairs = [
            ("RANGE", "range_reversion"),
            ("TREND_UP", "trend_pullback"),
            ("BREAKOUT", "volume_breakout"),
        ]

        for regime, strategy in pairs:
            with self.subTest(regime=regime, strategy=strategy):
                result = evaluate_profit_entry_gate(
                    market_regime=regime,
                    strategy_name=strategy,
                    side="BUY",
                    auto_exit_enabled=True,
                    config=config(),
                )
                self.assertTrue(result["entry_allowed"])

    def test_auto_exit_disabled_blocks_profit_engine_entries(self) -> None:
        result = evaluate_profit_entry_gate(
            market_regime="TREND_UP",
            strategy_name="trend_pullback",
            side="BUY",
            auto_exit_enabled=False,
            config=config(),
        )

        self.assertFalse(result["entry_allowed"])
        self.assertEqual(result["block_code"], "BLOCKED_AUTO_EXIT_DISABLED")


if __name__ == "__main__":
    unittest.main()
