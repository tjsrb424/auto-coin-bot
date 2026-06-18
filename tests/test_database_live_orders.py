from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database


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
