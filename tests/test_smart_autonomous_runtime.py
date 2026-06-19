from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import database
from app.live_broker import LiveTradingConfig
from app.live_strategy_pilot import LiveStrategyConfig, _process_session, _submit_smart_intent_order, start_live_strategy_pilot


def candle() -> dict:
    return {
        "market": "KRW-BTC",
        "unit": 5,
        "candle_time_utc": "2026-06-19T03:00:00Z",
        "trade_price": 100_000_000,
        "opening_price": 100_000_000,
        "high_price": 100_000_000,
        "low_price": 100_000_000,
        "candle_acc_trade_volume": 1.0,
        "candle_acc_trade_price": 100_000_000,
    }


class SmartAutonomousRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        database.init_db()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.tmp.cleanup()

    def create_smart_session(self) -> dict:
        session_id = database.create_live_strategy_session(
            {
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 0,
                "strategy_name": "smart_autonomous",
                "strategy_parameters": {},
                "status": "READY",
                "auto_enabled": True,
                "initial_balance_krw": 0.0,
                "max_order_krw": 30_000,
                "max_orders_per_day": 0,
            }
        )
        session = database.load_latest_live_strategy_session()
        assert session is not None
        return {**session, "id": session_id}

    def create_intent(self, *, amount_requested: float, current_value: float, max_total: float = 500_000) -> tuple[int, dict]:
        snapshot_id = database.insert_decision_snapshot(
            {
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "current_bot_position_value_krw": current_value,
                "current_bot_position_qty": current_value / 100_000_000,
                "current_exposure_pct": current_value / max_total * 100,
                "target_exposure_pct": (current_value + amount_requested) / max_total * 100,
                "max_total_exposure_krw": max_total,
                "daily_loss_limit_pct": 3,
                "daily_loss_limit_krw": max_total * 0.03,
                "risk_score": 35,
            }
        )
        intent = {
            "decision_snapshot_id": snapshot_id,
            "exchange": "bithumb",
            "market": "KRW-BTC",
            "side": "BID",
            "action_hint": "BUY_MORE",
            "current_value_krw": current_value,
            "target_value_krw": current_value + amount_requested,
            "delta_value_krw": amount_requested,
            "target_qty": amount_requested / 100_000_000,
            "order_type": "LIMIT",
            "limit_price": 100_000_000,
            "urgency": "NORMAL",
            "status": "CREATED",
            "promotion_status": "READY_FOR_LIVE",
        }
        intent_id = database.insert_order_intent(intent)
        return snapshot_id, {**intent, "id": intent_id}

    async def submit_bid_intent(self, *, amount_requested: float, current_value: float, available_krw: float, min_order_krw: str = "5000") -> tuple[AsyncMock, dict]:
        database.update_bot_operation_policy("KRW-BTC", {"auto_trading_enabled": True, "max_total_exposure_krw": 500_000, "daily_loss_limit_pct": 3})
        session = self.create_smart_session()
        snapshot_id, intent = self.create_intent(amount_requested=amount_requested, current_value=current_value)
        broker = AsyncMock()
        broker.get_balances.return_value = {
            "by_currency": {
                "KRW": {"balance": available_krw, "locked": 0.0},
                "BTC": {"balance": 0.0, "locked": 0.0},
            }
        }
        broker.get_order_chance.return_value = {"market": "KRW-BTC"}
        broker.place_order.return_value = {"uuid": "smart-order-uuid"}
        latest = candle()
        env = {
            "APP_ENV": "production",
            "LIVE_TRADING_ENABLED": "true",
            "LIVE_AUTO_TRADING_ENABLED": "true",
            "SMART_AUTONOMOUS_TRADING_ENABLED": "true",
            "SMART_ENGINE_LIVE_MODE": "live",
            "AUTO_MAX_ORDER_KRW": "30000",
            "MAX_LIVE_ORDER_KRW": "30000",
            "MIN_LIVE_ORDER_KRW": min_order_krw,
            "RISK_MAX_ORDER_KRW": "30000",
            "RISK_MAX_ORDERS_PER_DAY": "0",
            "RISK_MAX_ENTRY_ORDERS_PER_DAY": "0",
            "RISK_MIN_COOLDOWN_SECONDS": "0",
            "RISK_BLOCK_ON_OPEN_ORDER": "false",
            "RISK_BLOCK_ON_OPEN_POSITION": "false",
            "RISK_MIN_CURRENT_1M_VOLUME_KRW": "0",
            "RISK_MIN_AVG_5M_VOLUME_KRW": "0",
            "RISK_REQUIRE_ORDER_CHANCE_SUCCESS": "false",
        }
        with patch.dict("os.environ", env, clear=False), patch("app.live_strategy_pilot.get_live_broker", return_value=broker):
            config = LiveStrategyConfig.from_env()
            live_config = LiveTradingConfig.for_exchange("bithumb")
            await _submit_smart_intent_order(
                session,
                latest,
                {"signal": "HOLD", "reason": "smart test"},
                config,
                live_config,
                {
                    "id": snapshot_id,
                    "exchange": "bithumb",
                    "market": "KRW-BTC",
                    "current_bot_position_value_krw": current_value,
                    "current_bot_position_qty": current_value / 100_000_000,
                    "max_total_exposure_krw": 500_000,
                    "risk_score": 35,
                    "order_intents": [intent],
                },
            )
        return broker, database.load_decision_snapshot(snapshot_id)

    def test_start_runtime_without_candidate_uses_smart_autonomous_session(self) -> None:
        database.update_bot_operation_policy("KRW-BTC", {"auto_trading_enabled": True})
        with (
            patch.dict(
                "os.environ",
                {
                    "APP_ENV": "production",
                    "LIVE_AUTO_TRADING_ENABLED": "true",
                    "SMART_AUTONOMOUS_TRADING_ENABLED": "true",
                    "SMART_ENGINE_LIVE_MODE": "live",
                },
                clear=False,
            ),
            patch("app.live_strategy_pilot.run_live_strategy_tick"),
        ):
            result = start_live_strategy_pilot(
                confirmation="AUTO STRATEGY ENABLE",
                order_confirmation="PLACE AUTO LIVE ORDER",
            )

        self.assertTrue(result["ok"])
        session = database.load_latest_live_strategy_session()
        assert session is not None
        self.assertEqual(session["candidate_strategy_id"], 0)
        self.assertEqual(session["strategy_name"], "smart_autonomous")

    async def test_smart_live_mode_does_not_route_legacy_buy_to_entry_submit(self) -> None:
        candidate_id = database.save_candidate_strategy(
            {
                "strategy": "rsi",
                "parameters": {"period": 14, "oversold": 30, "overbought": 70},
                "unit": 5,
                "market": "KRW-BTC",
                "backtest_period": "30d",
                "score": 1,
            }
        )
        session_id = database.create_live_strategy_session(
            {
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": candidate_id,
                "strategy_name": "rsi",
                "strategy_parameters": {},
                "status": "READY",
                "auto_enabled": True,
                "initial_balance_krw": 0.0,
                "max_order_krw": 30_000,
                "max_orders_per_day": 0,
            }
        )
        session = database.load_latest_live_strategy_session()
        assert session is not None
        latest = candle()
        submit_smart = AsyncMock(return_value=True)
        submit_entry = AsyncMock()

        with (
            patch.dict(
                "os.environ",
                {
                    "APP_ENV": "production",
                    "LIVE_AUTO_TRADING_ENABLED": "true",
                    "AUTO_STRATEGY_PILOT_ENABLED": "true",
                    "SMART_ENGINE_LIVE_MODE": "live",
                },
                clear=False,
            ),
            patch("app.live_strategy_pilot._precheck_block_reason", new=AsyncMock(return_value=None)),
            patch("app.live_strategy_pilot.fetch_minute_candles", new=AsyncMock(return_value=[latest])),
            patch("app.live_strategy_pilot.insert_candles"),
            patch("app.live_strategy_pilot.load_candles", return_value=[latest]),
            patch("app.live_strategy_pilot._latest_signal", return_value={"signal": "BUY", "reason": "legacy buy"}),
            patch("app.live_strategy_pilot._record_smart_decision", new=AsyncMock(return_value={"order_intents": [{"side": "BID", "delta_value_krw": 20_000}]})),
            patch("app.live_strategy_pilot._submit_smart_intent_order", new=submit_smart),
            patch("app.live_strategy_pilot._submit_entry_order", new=submit_entry),
        ):
            await _process_session({**session, "id": session_id})

        submit_smart.assert_awaited_once()
        submit_entry.assert_not_awaited()

    async def test_bid_order_amount_is_capped_by_order_limits(self) -> None:
        broker, snapshot = await self.submit_bid_intent(amount_requested=120_000, current_value=0, available_krw=200_000)

        broker.place_order.assert_awaited_once()
        order = broker.place_order.await_args.args[0]
        self.assertEqual(order["amount_krw"], 30_000)
        intent = snapshot["order_intents"][0]
        self.assertTrue(intent["policy_preview"]["cap_applied"])
        self.assertEqual(intent["policy_preview"]["amount_requested_krw"], 120_000)
        self.assertEqual(intent["policy_preview"]["capped_order_amount_krw"], 30_000)
        self.assertEqual(intent["policy_preview"]["hard_cap_krw"], 30_000)

    async def test_bid_order_amount_is_capped_by_remaining_exposure(self) -> None:
        broker, snapshot = await self.submit_bid_intent(amount_requested=120_000, current_value=490_000, available_krw=200_000)

        broker.place_order.assert_awaited_once()
        order = broker.place_order.await_args.args[0]
        self.assertLessEqual(order["amount_krw"], 10_000)
        intent = snapshot["order_intents"][0]
        self.assertTrue(intent["policy_preview"]["cap_applied"])
        self.assertEqual(intent["policy_preview"]["remaining_exposure_krw"], 10_000)
        self.assertEqual(intent["policy_preview"]["capped_order_amount_krw"], 10_000)

    async def test_bid_order_blocks_when_available_krw_is_below_minimum(self) -> None:
        broker, snapshot = await self.submit_bid_intent(amount_requested=30_000, current_value=0, available_krw=5_000, min_order_krw="10000")

        broker.place_order.assert_not_awaited()
        intent = snapshot["order_intents"][0]
        self.assertEqual(intent["status"], "BLOCKED")
        self.assertEqual(intent["promotion_status"], "BLOCKED")
        self.assertIn("SMART_INSUFFICIENT_KRW_BALANCE", intent["promotion_blockers"])
        self.assertTrue(intent["policy_preview"]["cap_applied"])
        self.assertEqual(intent["policy_preview"]["available_krw_balance"], 5_000)


if __name__ == "__main__":
    unittest.main()
