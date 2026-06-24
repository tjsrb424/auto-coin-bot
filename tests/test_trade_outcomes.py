from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database
from app.trade_outcomes import (
    record_filled_order_outcome,
    refresh_realized_outcomes_for_position,
    refresh_trade_outcome_post_fill_returns,
)


def candle(time_utc: str, price: float, market: str = "KRW-BTC") -> dict:
    return {
        "market": market,
        "unit": 1,
        "candle_date_time_utc": time_utc,
        "candle_date_time_kst": time_utc,
        "opening_price": price,
        "high_price": price,
        "low_price": price,
        "trade_price": price,
        "candle_acc_trade_price": 1_000_000,
        "candle_acc_trade_volume": 10,
        "timestamp": 1,
    }


def live_order(
    request_id: str,
    *,
    order_uuid: str,
    side: str = "BUY",
    status: str = "FILLED",
    price: float = 100.0,
    filled_price: float = 100.0,
    volume: float = 10.0,
    position_id: int | None = None,
) -> dict:
    amount = filled_price * volume
    return {
        "request_id": request_id,
        "session_id": 1,
        "candidate_strategy_id": 7,
        "exchange": "bithumb",
        "market": "KRW-BTC",
        "side": side,
        "order_type": "LIMIT",
        "price": price,
        "volume": volume,
        "amount_krw": amount,
        "fee_estimate": 0.5,
        "risk_result": "ALLOWED",
        "status": status,
        "order_uuid": order_uuid,
        "executed_volume": volume,
        "filled_amount_krw": amount,
        "paid_fee": 0.5,
        "position_id": position_id,
        "order_purpose": "EXIT" if side == "SELL" else "ENTRY",
        "strategy_name": "trend_pullback",
        "order_preview_payload": {
            "market_regime": "TREND_UP",
            "action_hint": "BUY_MORE",
            "legacy_signal": "BUY",
            "attack_mode": "BALANCED",
            "target_source": "ADAPTIVE",
            "spread_pct": 0.12,
        },
        "created_at": "2026-06-24T00:00:00Z",
        "updated_at": "2026-06-24T00:00:30Z",
    }


class TradeOutcomeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        database.init_db()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_filled_buy_creates_idempotent_outcome_row(self) -> None:
        order_id = database.insert_live_order_log(live_order("buy-1", order_uuid="buy-order-1"))
        order = database.get_live_order_log("buy-1")

        first = record_filled_order_outcome(order, position_id=11)
        second = record_filled_order_outcome(order, position_id=11)
        rows = database.load_trade_outcome_logs(exchange="bithumb", market="KRW-BTC")

        self.assertEqual(len(rows), 1)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(rows[0]["live_order_log_id"], order_id)
        self.assertEqual(rows[0]["position_id"], 11)
        self.assertEqual(rows[0]["side"], "BUY")
        self.assertAlmostEqual(rows[0]["filled_price"], 100.0)

    def test_post_fill_returns_and_buy_adverse_selection_are_updated(self) -> None:
        record_filled_order_outcome(live_order("buy-returns", order_uuid="buy-return-order"), position_id=12)
        database.insert_candles(
            [
                candle("2026-06-24T00:01:30Z", 101.0),
                candle("2026-06-24T00:02:30Z", 98.0),
                candle("2026-06-24T00:03:30Z", 103.0),
                candle("2026-06-24T00:05:30Z", 105.0),
                candle("2026-06-24T00:15:30Z", 115.0),
            ]
        )

        result = refresh_trade_outcome_post_fill_returns(order_uuid="buy-return-order", now_utc="2026-06-24T00:16:00Z")
        outcome = database.load_trade_outcome_log_by_order_uuid("buy-return-order")

        self.assertEqual(result["updated"], 1)
        self.assertAlmostEqual(outcome["post_fill_return_1m"], 1.0)
        self.assertAlmostEqual(outcome["post_fill_return_5m"], 5.0)
        self.assertAlmostEqual(outcome["post_fill_return_15m"], 15.0)
        self.assertAlmostEqual(outcome["max_favorable_excursion_pct"], 15.0)
        self.assertAlmostEqual(outcome["max_adverse_excursion_pct"], 2.0)
        self.assertEqual(outcome["outcome_status"], "PENDING_REALIZED")

    def test_sell_outcome_treats_future_drop_as_favorable(self) -> None:
        record_filled_order_outcome(live_order("sell-returns", order_uuid="sell-return-order", side="SELL"), position_id=13)
        database.insert_candles(
            [
                candle("2026-06-24T00:01:30Z", 99.0),
                candle("2026-06-24T00:03:30Z", 102.0),
                candle("2026-06-24T00:05:30Z", 97.0),
                candle("2026-06-24T00:15:30Z", 95.0),
            ]
        )

        refresh_trade_outcome_post_fill_returns(order_uuid="sell-return-order", now_utc="2026-06-24T00:16:00Z")
        outcome = database.load_trade_outcome_log_by_order_uuid("sell-return-order")

        self.assertAlmostEqual(outcome["post_fill_return_1m"], -1.0)
        self.assertAlmostEqual(outcome["max_favorable_excursion_pct"], 5.0)
        self.assertAlmostEqual(outcome["max_adverse_excursion_pct"], 2.0)
        self.assertEqual(outcome["outcome_status"], "POST_FILL_COMPLETE")

    def test_missing_future_candles_keeps_pending_market_data(self) -> None:
        record_filled_order_outcome(live_order("buy-pending", order_uuid="buy-pending-order"), position_id=14)
        database.insert_candles([candle("2026-06-24T00:01:30Z", 101.0)])

        refresh_trade_outcome_post_fill_returns(order_uuid="buy-pending-order", now_utc="2026-06-24T00:16:00Z")
        outcome = database.load_trade_outcome_log_by_order_uuid("buy-pending-order")

        self.assertAlmostEqual(outcome["post_fill_return_1m"], 1.0)
        self.assertIsNone(outcome["post_fill_return_15m"])
        self.assertEqual(outcome["outcome_status"], "PENDING_MARKET_DATA")

    def test_closed_position_updates_realized_outcomes_for_entry_and_scale_in(self) -> None:
        position_id = database.create_live_position(
            {
                "session_id": 1,
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 7,
                "strategy_name": "trend_pullback",
                "status": "OPEN",
                "entry_order_uuid": "entry-order",
                "entry_price": 100.0,
                "entry_volume": 20.0,
                "entry_amount_krw": 2_000.0,
                "current_price": 110.0,
                "unrealized_pnl": 200.0,
                "realized_pnl": 0.0,
                "opened_at": "2026-06-24T00:00:00Z",
            }
        )
        record_filled_order_outcome(live_order("entry", order_uuid="entry-order", position_id=position_id), position_id=position_id)
        record_filled_order_outcome(live_order("scale", order_uuid="scale-order", position_id=position_id), position_id=position_id)
        database.update_live_position(position_id, {"status": "CLOSED", "realized_pnl": 150.0, "closed_at": "2026-06-24T00:30:00Z"})

        result = refresh_realized_outcomes_for_position(position_id)
        rows = database.load_trade_outcome_logs(position_id=position_id, limit=10)

        self.assertEqual(result["updated"], 2)
        self.assertTrue(all(row["outcome_status"] == "REALIZED" for row in rows))
        self.assertTrue(all(row["realized_pnl_krw"] == 150.0 for row in rows))
        self.assertTrue(all(row["realized_return_pct"] == 7.5 for row in rows))
        self.assertTrue(all(row["holding_minutes"] == 30.0 for row in rows))


if __name__ == "__main__":
    unittest.main()
