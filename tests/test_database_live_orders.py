from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database
from app.live_strategy_pilot import _insert_blocked_log


def live_order_log(request_id: str, status: str, *, order_uuid: str = "order-uuid", session_id: int = 1) -> dict:
    return {
        "request_id": request_id,
        "session_id": session_id,
        "candidate_strategy_id": 3,
        "exchange": "bithumb",
        "market": "KRW-BTC",
        "side": "BUY",
        "order_type": "LIMIT",
        "price": 100_000_000,
        "volume": 0.0003,
        "amount_krw": 30_000,
        "fee_estimate": 15,
        "risk_result": "ALLOWED",
        "status": status,
        "order_uuid": order_uuid,
        "strategy_name": "volatility_breakout",
    }


class DatabaseLiveOrderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        database.init_db()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_canceled_strategy_order_events_do_not_count_as_open(self) -> None:
        database.insert_live_order_log(live_order_log("strategy-request", "SUBMITTED"))
        database.insert_live_order_log(live_order_log("strategy-request-submitted-event", "SUBMITTED"))
        database.insert_live_order_log(live_order_log("strategy-request-waiting-event", "WAITING"))
        database.update_live_order_log("strategy-request", {"status": "CANCELED"})

        self.assertFalse(database.has_open_live_strategy_order("bithumb", "KRW-BTC"))

    def test_canonical_strategy_order_still_counts_as_open(self) -> None:
        database.insert_live_order_log(live_order_log("strategy-request", "SUBMITTED"))

        self.assertTrue(database.has_open_live_strategy_order("bithumb", "KRW-BTC"))

    def test_default_candidate_strategies_are_seeded(self) -> None:
        changed = database.ensure_default_candidate_strategies()
        candidates = database.load_candidate_strategies()

        self.assertEqual(changed, 3)
        self.assertEqual([item["name"] for item in candidates[:3]], ["필승 v1 - 추세 돌파", "필승 v2 - 눌림 반등", "필승 v3 - 안정 추세"])
        self.assertTrue(all(item["status"] == "ACTIVE" for item in candidates[:3]))
        self.assertEqual(database.ensure_default_candidate_strategies(), 0)

    def test_delete_candidate_strategy_without_references(self) -> None:
        candidate_id = database.save_candidate_strategy(
            {
                "strategy": "rsi",
                "parameters": {"rsi_period": 14, "buy_threshold": 30, "sell_threshold": 70},
                "unit": 5,
                "market": "KRW-BTC",
                "backtest_period": "30d",
                "score": 1,
            }
        )

        self.assertTrue(database.delete_candidate_strategy(candidate_id))
        self.assertIsNone(database.load_candidate_strategy(candidate_id))

    def test_delete_candidate_strategy_with_order_reference_is_blocked(self) -> None:
        candidate_id = database.save_candidate_strategy(
            {
                "strategy": "rsi",
                "parameters": {"rsi_period": 14, "buy_threshold": 30, "sell_threshold": 70},
                "unit": 5,
                "market": "KRW-BTC",
                "backtest_period": "30d",
                "score": 1,
            }
        )
        payload = live_order_log("strategy-request", "SUBMITTED")
        payload["candidate_strategy_id"] = candidate_id
        database.insert_live_order_log(payload)

        self.assertFalse(database.delete_candidate_strategy(candidate_id))
        self.assertIsNotNone(database.load_candidate_strategy(candidate_id))

    def test_blocked_log_accepts_missing_order_payload(self) -> None:
        _insert_blocked_log(
            {
                "id": 21,
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 5,
                "strategy_name": "rsi",
                "max_order_krw": 30_000,
            },
            "SMART_SELL_POSITION_MISSING",
            "No open bot position.",
            "2026-06-18T10:00:00Z",
            {"signal": "SELL"},
        )

        logs = database.load_live_order_logs()

        self.assertEqual(logs[0]["status"], "BLOCKED")
        self.assertEqual(logs[0]["risk_result"], "SMART_SELL_POSITION_MISSING")
        self.assertEqual(logs[0]["side"], "BUY")
