from __future__ import annotations

import unittest

from app.strategy_kill_switch import StrategyKillSwitchConfig, evaluate_strategy_kill_switch
from app.strategy_promotion_pipeline import _config, _session_passes


def passing_session() -> dict:
    orders = [{"side": "SELL", "realized_pnl": 1_000, "risk_result": "TAKE_PROFIT"} for _ in range(30)]
    return {
        "started_at": "2026-06-01T00:00:00Z",
        "last_tick_time_utc": "2026-06-08T00:00:00Z",
        "balance": {"total_return_percent": 1.2},
        "metrics": {"trade_count": 30, "mdd": 0.04, "win_rate": 0.5, "profit_factor": 1.4, "average_trade_pnl": 1_000},
        "orders": orders,
    }


class StrategyPromotionGateTests(unittest.TestCase):
    def test_single_positive_forward_trade_cannot_promote(self) -> None:
        session = {
            "started_at": "2026-06-01T00:00:00Z",
            "last_tick_time_utc": "2026-06-01T01:00:00Z",
            "balance": {"total_return_percent": 1.5},
            "metrics": {"trade_count": 1, "mdd": 0.01, "win_rate": 1.0, "profit_factor": 999.0, "average_trade_pnl": 2_000},
            "orders": [{"side": "SELL", "realized_pnl": 2_000}],
        }

        passed, blockers = _session_passes(session, _config())

        self.assertFalse(passed)
        self.assertIn("FORWARD_TRADE_COUNT_TOO_LOW", blockers)
        self.assertIn("FORWARD_RUNTIME_TOO_SHORT", blockers)

    def test_full_forward_quality_gate_can_promote(self) -> None:
        passed, blockers = _session_passes(passing_session(), _config())

        self.assertTrue(passed)
        self.assertEqual(blockers, [])

    def test_profit_factor_mdd_expectancy_and_single_trade_share_block(self) -> None:
        session = passing_session()
        session["metrics"] = {**session["metrics"], "mdd": 0.09, "profit_factor": 1.1, "average_trade_pnl": -10}
        session["orders"] = [{"side": "SELL", "realized_pnl": 20_000}, *[{"side": "SELL", "realized_pnl": -1_000} for _ in range(29)]]

        passed, blockers = _session_passes(session, _config())

        self.assertFalse(passed)
        self.assertIn("FORWARD_MDD_TOO_HIGH", blockers)
        self.assertIn("FORWARD_PROFIT_FACTOR_TOO_LOW", blockers)
        self.assertIn("FORWARD_EXPECTANCY_TOO_LOW", blockers)
        self.assertIn("FORWARD_SINGLE_TRADE_PROFIT_SHARE_TOO_HIGH", blockers)

    def test_kill_switch_pauses_on_repeated_losses(self) -> None:
        result = evaluate_strategy_kill_switch(
            orders=[
                {"side": "SELL", "realized_pnl": -1_000},
                {"side": "SELL", "realized_pnl": -1_000},
                {"side": "SELL", "realized_pnl": -1_000},
            ],
            config=StrategyKillSwitchConfig(
                enabled=True,
                expectancy_window=10,
                max_consecutive_losses=3,
                market_cooldown_hours=6,
                max_cancel_rate=0.6,
                max_average_slippage_pct=0.5,
            ),
        )

        self.assertEqual(result["action"], "PAUSE_STRATEGY")
        self.assertIn("KILL_CONSECUTIVE_LOSSES", result["blockers"])


if __name__ == "__main__":
    unittest.main()
