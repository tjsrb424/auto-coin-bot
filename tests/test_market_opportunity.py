from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database
from app.market_opportunity import build_market_opportunity_rankings, explain_candidate_blockers, rank_live_candidates


def candidate_payload(market: str, *, strategy: str = "rsi", score: float = 80.0, status: str = "LIVE_ELIGIBLE") -> dict:
    return {
        "name": f"{market} {strategy}",
        "description": "",
        "strategy": strategy,
        "parameters": {"period": 14},
        "unit": 5,
        "market": market,
        "backtest_period": "30d",
        "score": score,
        "backtest_total_return": 0.04,
        "backtest_mdd": 0.04,
        "backtest_win_rate": 0.55,
        "backtest_profit_factor": 1.4,
        "backtest_trade_count": 12,
        "backtest_average_trade_pnl": 0.002,
        "warning": "",
        "status": status,
    }


def allow_market(market: str, *, liquidity: float = 70.0, volatility: float = 45.0, risk: float = 10.0) -> None:
    database.upsert_market_universe(
        [
            {
                "exchange": "bithumb",
                "market": market,
                "symbol": market.split("-")[-1],
                "quote_currency": "KRW",
                "status": "DISCOVERED",
                "is_enabled": True,
                "is_live_allowed": True,
                "is_auto_selectable": True,
                "scan_rank": 1,
                "score": 90,
                "reason": "test",
                "min_24h_trade_price_krw": 0,
                "last_24h_trade_price_krw": 1_000_000_000,
                "last_price": 100_000,
                "last_change_rate": 0,
                "last_volatility_score": volatility,
                "last_liquidity_score": liquidity,
                "last_risk_score": risk,
                "last_scanned_at": "2026-06-24T00:00:00Z",
            }
        ]
    )


def insert_edge(candidate_id: int, market: str, *, edge_score: float, confidence: float = 85.0) -> None:
    database.upsert_adaptive_edge_stat(
        {
            "exchange": "bithumb",
            "market": market,
            "strategy_name": "rsi",
            "candidate_strategy_id": candidate_id,
            "unit": 5,
            "market_regime": "TREND_UP",
            "action_hint": "BUY_MORE",
            "legacy_signal": "BUY",
            "attack_mode": "BALANCED",
            "target_source": "ADAPTIVE",
            "order_purpose": "ENTRY",
            "sample_count": 30,
            "win_count": 21,
            "loss_count": 9,
            "win_rate": 0.7,
            "avg_post_fill_return_1m": edge_score / 2,
            "avg_post_fill_return_5m": edge_score,
            "avg_post_fill_return_15m": edge_score,
            "avg_realized_return_pct": edge_score,
            "avg_realized_pnl_krw": edge_score * 1000,
            "profit_factor": 1.8 if edge_score >= 0 else 0.6,
            "avg_adverse_selection_pct": 0.1 if edge_score >= 0 else 1.2,
            "avg_slippage_pct": 0.05,
            "avg_fill_time_seconds": 4,
            "max_drawdown_pct": min(edge_score, 0.0),
            "confidence_score": confidence,
            "edge_score": edge_score,
            "last_updated_at": "2026-06-24T00:00:00Z",
        }
    )


class MarketOpportunityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.env_patch = patch.dict(os.environ, {"DATABASE_URL": ""}, clear=False)
        self.env_patch.start()
        self.db_patch.start()
        database.init_db()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.env_patch.stop()
        self.tmp.cleanup()

    def test_high_adaptive_edge_candidate_moves_to_top(self) -> None:
        allow_market("KRW-AAA")
        allow_market("KRW-BBB")
        high_base = database.save_candidate_strategy(candidate_payload("KRW-AAA", score=95))
        high_edge = database.save_candidate_strategy(candidate_payload("KRW-BBB", score=82))
        candidates = [database.load_candidate_strategy(high_base), database.load_candidate_strategy(high_edge)]
        insert_edge(high_edge, "KRW-BBB", edge_score=2.0)

        ranked = rank_live_candidates(exchange="bithumb", candidates=[item for item in candidates if item], limit=2)

        self.assertEqual(ranked[0]["id"], high_edge)
        self.assertGreater(ranked[0]["market_opportunity_score"], ranked[1]["market_opportunity_score"])
        self.assertGreater(ranked[0]["opportunity_score_breakdown"]["adaptive_edge_component"], 0)

    def test_low_liquidity_candidate_is_explained_as_blocked(self) -> None:
        allow_market("KRW-LOW", liquidity=5)
        candidate_id = database.save_candidate_strategy(candidate_payload("KRW-LOW", score=95))
        candidate = database.load_candidate_strategy(candidate_id)

        blockers = explain_candidate_blockers(candidate or {}, exchange="bithumb")
        ranked = rank_live_candidates(exchange="bithumb", candidates=[candidate] if candidate else [], limit=1)

        self.assertIn("LOW_LIQUIDITY", blockers)
        self.assertFalse(ranked[0]["eligible_for_allocation"])
        self.assertEqual(ranked[0]["recommended_action"], "BLOCKED")

    def test_duplicate_market_position_and_open_order_mismatch_are_blockers(self) -> None:
        allow_market("KRW-DUP")
        candidate_id = database.save_candidate_strategy(candidate_payload("KRW-DUP"))
        session_id = database.create_live_strategy_session(
            {
                "exchange": "bithumb",
                "market": "KRW-DUP",
                "candidate_strategy_id": candidate_id,
                "strategy_name": "rsi",
                "strategy_parameters": {},
                "status": "RUNNING",
                "auto_enabled": True,
                "initial_balance_krw": 0,
                "max_order_krw": 10_000,
                "max_orders_per_day": 3,
            }
        )
        database.create_live_position(
            {
                "session_id": session_id,
                "exchange": "bithumb",
                "market": "KRW-DUP",
                "candidate_strategy_id": candidate_id,
                "strategy_name": "rsi",
                "status": "OPEN",
                "entry_order_uuid": "entry",
                "entry_price": 1000,
                "entry_volume": 10,
                "entry_amount_krw": 10_000,
                "current_price": 1000,
                "stop_loss_price": 900,
                "take_profit_price": 1100,
            }
        )
        candidate = database.load_candidate_strategy(candidate_id)

        rankings = build_market_opportunity_rankings(
            exchange="bithumb",
            candidates=[candidate] if candidate else [],
            snapshot={"positions": database.load_open_live_positions_for_exchange("bithumb"), "open_order_mismatch_detected": True},
            limit=1,
        )

        blockers = rankings["top_candidates"][0]["opportunity_blockers"]
        self.assertIn("BLOCKED_DUPLICATE_MARKET_POSITION", blockers)
        self.assertIn("BLOCKED_OPEN_ORDER_MISMATCH", blockers)

    def test_exit_open_order_only_blocks_same_market_but_entry_order_blocks_exchange(self) -> None:
        allow_market("KRW-AAA")
        allow_market("KRW-BBB")
        candidate_id = database.save_candidate_strategy(candidate_payload("KRW-AAA"))
        candidate = database.load_candidate_strategy(candidate_id)
        database.insert_live_order_log(
            {
                "request_id": "other-exit",
                "exchange": "bithumb",
                "market": "KRW-BBB",
                "side": "SELL",
                "order_type": "LIMIT",
                "price": 1000,
                "volume": 10,
                "amount_krw": 10_000,
                "fee_estimate": 5,
                "risk_result": "ALLOWED",
                "status": "WAITING",
                "order_uuid": "other-exit-uuid",
                "order_purpose": "EXIT",
            }
        )

        blockers = explain_candidate_blockers(candidate or {}, exchange="bithumb")
        self.assertNotIn("UNRESOLVED_OPEN_ORDER", blockers)

        database.insert_live_order_log(
            {
                "request_id": "entry-waiting",
                "exchange": "bithumb",
                "market": "KRW-BBB",
                "side": "BUY",
                "order_type": "LIMIT",
                "price": 1000,
                "volume": 10,
                "amount_krw": 10_000,
                "fee_estimate": 5,
                "risk_result": "ALLOWED",
                "status": "WAITING",
                "order_uuid": "entry-waiting-uuid",
                "order_purpose": "ENTRY",
            }
        )

        blockers = explain_candidate_blockers(candidate or {}, exchange="bithumb")
        self.assertIn("UNRESOLVED_OPEN_ORDER", blockers)


if __name__ == "__main__":
    unittest.main()
