from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database
from app.aggression_presets import (
    apply_aggression_preset,
    build_aggression_preset_preview,
    list_aggression_presets,
    load_active_aggression_preset,
)
from app.dynamic_sizing import build_dynamic_sizing_preview
from app.live_broker import LiveTradingConfig
from app.live_exit import LiveExitConfig
from app.live_strategy_pilot import LiveStrategyConfig
from app.risk_manager import RiskConfig


class AggressionPresetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.env_patch = patch.dict("os.environ", {"DATABASE_URL": ""}, clear=False)
        self.env_patch.start()
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        database.init_db()
        database.update_bot_operation_policy(
            "KRW-BTC",
            {
                "auto_trading_enabled": True,
                "max_total_exposure_krw": 500_000,
                "daily_loss_limit_pct": 3,
            },
        )

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.env_patch.stop()
        self.tmp.cleanup()

    def test_preview_shows_before_after_limits_without_changing_policy(self) -> None:
        preview = build_aggression_preset_preview("aggressive", market="KRW-BTC")

        self.assertEqual(preview["preset"], "aggressive")
        self.assertFalse(preview["application"]["requires_env_edit"])
        self.assertFalse(preview["application"]["runtime_restart_required"])
        self.assertEqual(preview["before"]["policy"]["max_total_exposure_krw"], 500_000)
        self.assertEqual(preview["after"]["policy"]["max_total_exposure_krw"], 500_000)
        self.assertEqual(preview["after"]["policy"]["daily_loss_limit_pct"], 3)
        self.assertGreater(preview["after"]["limits"]["auto_max_order_krw"], preview["before"]["limits"]["auto_max_order_krw"])
        self.assertGreaterEqual(
            preview["after"]["expected_max_exposure"]["expected_max_exposure_krw"],
            preview["before"]["expected_max_exposure"]["expected_max_exposure_krw"],
        )

    def test_apply_aggressive_preset_records_log_and_preserves_safety_guards(self) -> None:
        result = apply_aggression_preset("aggressive", market="KRW-BTC", requested_by="tester", reason="test")
        active = load_active_aggression_preset()
        logs = database.load_aggression_preset_logs()
        policy = database.load_bot_operation_policy("KRW-BTC")

        self.assertEqual(active["name"], "aggressive")
        self.assertEqual(result["change_log"]["preset_name"], "aggressive")
        self.assertEqual(logs[0]["requested_by"], "tester")
        self.assertEqual(policy["max_total_exposure_krw"], 500_000)
        self.assertEqual(policy["daily_loss_limit_pct"], 3)
        self.assertTrue(active["settings"]["AUTO_SCALE_IN_NO_AVERAGING_DOWN"])
        self.assertTrue(active["settings"]["RISK_BLOCK_ON_BALANCE_MISMATCH"])
        self.assertTrue(result["safety_guards"]["emergency_stop_preserved"])

    def test_applied_preset_feeds_runtime_configs(self) -> None:
        apply_aggression_preset("conservative", market="KRW-BTC")

        strategy_config = LiveStrategyConfig.from_env()
        exit_config = LiveExitConfig.from_env()
        risk_config = RiskConfig.from_env()
        live_config = LiveTradingConfig.for_exchange("bithumb")

        self.assertEqual(strategy_config.max_order_krw, 20_000)
        self.assertEqual(strategy_config.max_orders_per_day, 2)
        self.assertEqual(strategy_config.cooldown_seconds, 2_700)
        self.assertEqual(exit_config.max_hold_minutes, 45)
        self.assertEqual(risk_config.max_entry_orders_per_day, 1)
        self.assertEqual(risk_config.max_order_krw, 20_000)
        self.assertTrue(risk_config.block_on_balance_mismatch)
        self.assertTrue(risk_config.block_on_open_order)
        self.assertEqual(live_config.max_live_order_krw, 20_000)

    def test_aggressive_dynamic_sizing_stays_below_two_times_multiplier(self) -> None:
        apply_aggression_preset("aggressive", market="KRW-BTC")

        preview = build_dynamic_sizing_preview(
            original_amount_krw=10_000,
            adaptive_edge={"adaptive_edge_score": 5, "edge_confidence": 90, "avg_post_fill_return_5m": 2.0},
            fee_pct=0.05,
            min_order_krw=5_000,
            max_allowed_amount_krw=100_000,
        )

        self.assertTrue(preview["enabled"])
        self.assertEqual(preview["mode"], "apply")
        self.assertLess(preview["max_multiplier"], 2.0)
        self.assertLessEqual(preview["sizing_multiplier"], 1.8)

    def test_list_presets_includes_active_and_logs_are_empty_before_apply(self) -> None:
        payload = list_aggression_presets(market="KRW-BTC")

        self.assertIsNone(payload["active_preset"]["name"])
        self.assertEqual([item["name"] for item in payload["presets"]], ["conservative", "balanced", "aggressive"])
        self.assertEqual(database.load_aggression_preset_logs(), [])


if __name__ == "__main__":
    unittest.main()
