from __future__ import annotations

import unittest

from app.profit_engine import ProfitEngineConfig, evaluate_profit_entry_gate
from app.live_strategy_pilot import _profit_engine_strategy_name
from app.main import MultiMarketValidationRequest
from app.strategy_discovery_scheduler import discovery_scheduler_config


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

    def test_candidate_strategy_type_allows_profit_strategy_display_names(self) -> None:
        cases = [
            ("KRW-BTC volume_breakout 5m 82pt", "volume_breakout", "BREAKOUT"),
            ("KRW-BTC trend_pullback 5m 82pt", "trend_pullback", "TREND_UP"),
        ]

        for name, strategy_type, regime in cases:
            with self.subTest(name=name, strategy_type=strategy_type, regime=regime):
                strategy = _profit_engine_strategy_name(
                    {"strategy_name": "smart_autonomous"},
                    {"selected_strategy_name": name, "selected_strategy_type": strategy_type},
                    regime,
                )
                result = evaluate_profit_entry_gate(
                    market_regime=regime,
                    strategy_name=strategy,
                    side="BUY",
                    auto_exit_enabled=True,
                    config=config(),
                )

                self.assertEqual(strategy, strategy_type)
                self.assertTrue(result["entry_allowed"])

    def test_profit_strategy_type_can_be_extracted_from_display_name(self) -> None:
        cases = [
            ("KRW-BTC volume_breakout 5m 82pt", "BREAKOUT", "volume_breakout"),
            ("KRW-BTC trend_pullback 5m 82pt", "TREND_UP", "trend_pullback"),
            ("KRW-BTC range_reversion 5m 82pt", "RANGE", "range_reversion"),
        ]

        for name, regime, expected_strategy in cases:
            with self.subTest(name=name, regime=regime):
                result = evaluate_profit_entry_gate(
                    market_regime=regime,
                    strategy_name=name,
                    side="BUY",
                    auto_exit_enabled=True,
                    config=config(),
                )

                self.assertTrue(result["entry_allowed"])
                self.assertEqual(result["strategy_name"], expected_strategy)

    def test_smart_autonomous_falls_back_to_regime_default_strategy(self) -> None:
        strategy = _profit_engine_strategy_name(
            {"strategy_name": "smart_autonomous"},
            {"selected_strategy_name": "Smart Autonomous Engine", "selected_strategy_type": "smart_autonomous"},
            "TREND_UP",
        )
        result = evaluate_profit_entry_gate(
            market_regime="TREND_UP",
            strategy_name=strategy,
            side="BUY",
            auto_exit_enabled=True,
            config=config(),
        )

        self.assertEqual(strategy, "smart_autonomous")
        self.assertTrue(result["entry_allowed"])
        self.assertEqual(result["strategy_name"], "trend_pullback")

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


class ProfitEngineDiscoveryDefaultsTests(unittest.TestCase):
    def test_discovery_defaults_include_profit_buy_strategies(self) -> None:
        config_payload = discovery_scheduler_config()

        self.assertIn("trend_pullback", config_payload["fast_strategies"])
        self.assertIn("volume_breakout", config_payload["fast_strategies"])
        self.assertIn("range_reversion", config_payload["fast_strategies"])
        self.assertNotIn("panic_blocker", config_payload["fast_strategies"])
        self.assertEqual(config_payload["risk_off_only_strategies"], ["panic_blocker"])

    def test_multi_market_validation_defaults_include_profit_buy_strategies(self) -> None:
        payload = MultiMarketValidationRequest()

        self.assertIn("trend_pullback", payload.strategies)
        self.assertIn("volume_breakout", payload.strategies)
        self.assertIn("range_reversion", payload.strategies)
        self.assertNotIn("panic_blocker", payload.strategies)


if __name__ == "__main__":
    unittest.main()
