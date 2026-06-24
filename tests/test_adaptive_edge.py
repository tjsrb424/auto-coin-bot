from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database
from app.adaptive_edge import attach_adaptive_edge_preview, build_adaptive_edge_preview, refresh_adaptive_edge_stats


def outcome_payload(
    order_uuid: str,
    *,
    candidate_strategy_id: int,
    post_5m: float,
    post_15m: float,
    realized_return: float | None = None,
    adverse: float = 0.1,
    slippage: float = 0.05,
    status: str = "REALIZED",
) -> dict:
    return {
        "order_uuid": order_uuid,
        "request_id": order_uuid,
        "live_order_log_id": 1,
        "session_id": 1,
        "position_id": 1,
        "exchange": "bithumb",
        "market": "KRW-BTC",
        "side": "BUY",
        "order_purpose": "ENTRY",
        "strategy_name": "rsi",
        "candidate_strategy_id": candidate_strategy_id,
        "market_regime": "TREND_UP",
        "action_hint": "BUY_MORE",
        "legacy_signal": "BUY",
        "attack_mode": "BALANCED",
        "target_source": "ADAPTIVE",
        "entry_or_exit_price": 100.0,
        "filled_price": 100.0,
        "filled_volume": 10.0,
        "filled_amount_krw": 1000.0,
        "fee_krw": 0.5,
        "slippage_pct": slippage,
        "spread_pct": 0.1,
        "fill_time_seconds": 5,
        "filled_at": "2026-06-24T00:00:00Z",
        "post_fill_return_1m": post_5m / 2,
        "post_fill_return_5m": post_5m,
        "post_fill_return_15m": post_15m,
        "max_favorable_excursion_pct": max(post_5m, post_15m, 0.0),
        "max_adverse_excursion_pct": adverse,
        "adverse_selection_pct": adverse,
        "realized_pnl_krw": realized_return * 10 if realized_return is not None else None,
        "realized_return_pct": realized_return,
        "holding_minutes": 10,
        "outcome_status": status,
    }


class AdaptiveEdgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        database.init_db()
        self.candidate_id = database.save_candidate_strategy(
            {
                "strategy": "rsi",
                "parameters": {"rsi_period": 14},
                "unit": 5,
                "market": "KRW-BTC",
                "backtest_period": "30d",
                "score": 70,
                "status": "LIVE_ELIGIBLE",
            }
        )

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_low_sample_count_has_low_confidence(self) -> None:
        for index in range(4):
            database.upsert_trade_outcome_log(outcome_payload(f"low-{index}", candidate_strategy_id=self.candidate_id, post_5m=1.0, post_15m=1.2, realized_return=0.8))

        refresh_adaptive_edge_stats(exchange="bithumb", market="KRW-BTC")
        stats = database.load_adaptive_edge_stats(exchange="bithumb", market="KRW-BTC", candidate_strategy_id=self.candidate_id)

        self.assertEqual(stats[0]["sample_count"], 4)
        self.assertEqual(stats[0]["unit"], 5)
        self.assertLess(stats[0]["confidence_score"], 30)
        self.assertGreater(stats[0]["edge_score"], 0)

    def test_profitable_group_scores_positive_and_losing_group_scores_negative(self) -> None:
        for index in range(12):
            database.upsert_trade_outcome_log(outcome_payload(f"win-{index}", candidate_strategy_id=self.candidate_id, post_5m=1.0, post_15m=1.5, realized_return=1.2))
        for index in range(12):
            payload = outcome_payload(f"loss-{index}", candidate_strategy_id=self.candidate_id, post_5m=-1.1, post_15m=-1.6, realized_return=-1.3, adverse=1.5, slippage=0.4)
            payload["market_regime"] = "CHOPPY"
            payload["action_hint"] = "BUY_MORE"
            database.upsert_trade_outcome_log(payload)

        refresh_adaptive_edge_stats(exchange="bithumb", market="KRW-BTC")
        positive = build_adaptive_edge_preview(
            {
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "strategy_name": "rsi",
                "candidate_strategy_id": self.candidate_id,
                "unit": 5,
                "market_regime": "TREND_UP",
                "action_hint": "BUY_MORE",
                "legacy_signal": "BUY",
                "attack_mode": "BALANCED",
                "target_source": "ADAPTIVE",
                "order_purpose": "ENTRY",
            }
        )
        negative = build_adaptive_edge_preview(
            {
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "strategy_name": "rsi",
                "candidate_strategy_id": self.candidate_id,
                "unit": 5,
                "market_regime": "CHOPPY",
                "action_hint": "BUY_MORE",
                "legacy_signal": "BUY",
                "attack_mode": "BALANCED",
                "target_source": "ADAPTIVE",
                "order_purpose": "ENTRY",
            }
        )

        self.assertEqual(positive["edge_status"], "POSITIVE_EDGE")
        self.assertGreater(positive["adaptive_edge_score"], 0)
        self.assertEqual(negative["edge_status"], "NEGATIVE_EDGE")
        self.assertLess(negative["adaptive_edge_score"], 0)

    def test_pending_market_data_outcomes_are_excluded_from_aggregation(self) -> None:
        database.upsert_trade_outcome_log(outcome_payload("done-1", candidate_strategy_id=self.candidate_id, post_5m=1.0, post_15m=1.0, realized_return=1.0))
        database.upsert_trade_outcome_log(
            outcome_payload("pending-1", candidate_strategy_id=self.candidate_id, post_5m=-9.0, post_15m=-9.0, realized_return=-9.0, status="PENDING_MARKET_DATA")
        )

        refresh_adaptive_edge_stats(exchange="bithumb", market="KRW-BTC")
        stats = database.load_adaptive_edge_stats(exchange="bithumb", market="KRW-BTC", candidate_strategy_id=self.candidate_id)

        self.assertEqual(stats[0]["sample_count"], 1)
        self.assertGreater(stats[0]["edge_score"], 0)

    def test_attach_preview_records_shadow_adaptive_edge_on_intent(self) -> None:
        for index in range(8):
            database.upsert_trade_outcome_log(outcome_payload(f"preview-{index}", candidate_strategy_id=self.candidate_id, post_5m=0.8, post_15m=1.0, realized_return=0.6))
        refresh_adaptive_edge_stats(exchange="bithumb", market="KRW-BTC")
        intent = {"action_hint": "BUY_MORE", "attack_mode": "BALANCED", "target_source": "ADAPTIVE", "policy_preview": {}}
        snapshot = {
            "exchange": "bithumb",
            "market": "KRW-BTC",
            "selected_strategy_id": self.candidate_id,
            "selected_strategy_type": "rsi",
            "market_regime": "TREND_UP",
            "action_hint": "BUY_MORE",
            "legacy_signal": "BUY",
            "attack_mode": "BALANCED",
            "final_target_exposure_source": "ADAPTIVE",
            "timeframe": "5m",
        }

        attach_adaptive_edge_preview(intent=intent, snapshot=snapshot, candidate={"id": self.candidate_id, "strategy": "rsi", "unit": 5})

        self.assertIn("adaptive_edge", intent["policy_preview"])
        self.assertTrue(intent["policy_preview"]["adaptive_edge"]["shadow_only"])
        self.assertGreater(intent["policy_preview"]["adaptive_edge_score"], 0)
        self.assertGreater(intent["policy_preview"]["edge_confidence"], 0)


if __name__ == "__main__":
    unittest.main()
