from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import database
from app.live_broker import LiveTradingConfig
from app.live_strategy_pilot import LiveStrategyConfig, _manage_open_order, _process_session, _smart_bid_cap_blocker, _submit_smart_intent_order, start_live_strategy_pilot


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
        self.env_patch = patch.dict("os.environ", {"DATABASE_URL": ""}, clear=False)
        self.env_patch.start()
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        database.init_db()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.env_patch.stop()
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

    def create_intent(
        self,
        *,
        amount_requested: float,
        current_value: float,
        max_total: float = 500_000,
        side: str = "BID",
        action_hint: str = "BUY_MORE",
        target_source: str = "CONSERVATIVE",
        core_exposure_pct: float = 0.0,
        core_exposure_applied: bool = False,
    ) -> tuple[int, dict]:
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
                "final_target_exposure_source": target_source,
                "core_exposure_pct": core_exposure_pct,
                "core_exposure_applied": core_exposure_applied,
            }
        )
        intent = {
            "decision_snapshot_id": snapshot_id,
            "exchange": "bithumb",
            "market": "KRW-BTC",
            "side": side,
            "action_hint": action_hint,
            "current_value_krw": current_value,
            "target_value_krw": current_value + amount_requested,
            "delta_value_krw": amount_requested,
            "target_qty": amount_requested / 100_000_000,
            "order_type": "LIMIT",
            "limit_price": 100_000_000,
            "urgency": "NORMAL",
            "status": "CREATED",
            "promotion_status": "READY_FOR_LIVE",
            "target_source": target_source,
            "policy_preview": {
                "target_source": target_source,
                "core_exposure_pct": core_exposure_pct,
                "core_exposure_applied": core_exposure_applied,
            },
        }
        intent_id = database.insert_order_intent(intent)
        return snapshot_id, {**intent, "id": intent_id}

    async def submit_bid_intent(
        self,
        *,
        amount_requested: float,
        current_value: float,
        available_krw: float,
        min_order_krw: str = "5000",
        auto_entry_offset: str = "0.3",
        core_entry_offset: str | None = None,
        target_source: str = "CONSERVATIVE",
        core_exposure_pct: float = 0.0,
        core_exposure_applied: bool = False,
        orderbook_top: dict | None = None,
        orderbook_error: bool = False,
    ) -> tuple[AsyncMock, dict]:
        database.update_bot_operation_policy("KRW-BTC", {"auto_trading_enabled": True, "max_total_exposure_krw": 500_000, "daily_loss_limit_pct": 3})
        session = self.create_smart_session()
        snapshot_id, intent = self.create_intent(
            amount_requested=amount_requested,
            current_value=current_value,
            target_source=target_source,
            core_exposure_pct=core_exposure_pct,
            core_exposure_applied=core_exposure_applied,
        )
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
            "AUTO_ENTRY_PRICE_OFFSET_PERCENT": auto_entry_offset,
        }
        if core_entry_offset is not None:
            env["SMART_CORE_ENTRY_PRICE_OFFSET_PERCENT"] = core_entry_offset
        orderbook_top = orderbook_top or {"best_bid": 99_900_000, "best_ask": 100_000_000, "spread_krw": 100_000, "spread_pct": 0.10005}
        orderbook_mock = AsyncMock(side_effect=RuntimeError("orderbook failed")) if orderbook_error else AsyncMock(return_value=orderbook_top)
        with (
            patch.dict("os.environ", env, clear=False),
            patch("app.live_strategy_pilot.get_live_broker", return_value=broker),
            patch("app.live_strategy_pilot._bithumb_orderbook_top", new=orderbook_mock),
        ):
            if core_entry_offset is None:
                os.environ.pop("SMART_CORE_ENTRY_PRICE_OFFSET_PERCENT", None)
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
                    "current_exposure_pct": current_value / 500_000 * 100,
                    "final_target_exposure_source": target_source,
                    "core_exposure_pct": core_exposure_pct,
                    "core_exposure_applied": core_exposure_applied,
                    "max_total_exposure_krw": 500_000,
                    "risk_score": 35,
                    "order_intents": [intent],
                },
            )
        return broker, database.load_decision_snapshot(snapshot_id)

    def live_order_log_count(self) -> int:
        with database.get_connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM live_order_logs").fetchone()
        return int(row["count"])

    def test_smart_bid_cap_blocker_classifies_below_min_requested_amount(self) -> None:
        self.assertEqual(
            _smart_bid_cap_blocker(
                amount_requested=0.08,
                available_krw=287_000,
                remaining_exposure=987_000,
                hard_cap=100_000,
                min_order_krw=5_000,
            ),
            "SMART_ORDER_AMOUNT_BELOW_MIN",
        )

    def test_smart_bid_cap_blocker_classifies_zero_hard_cap(self) -> None:
        self.assertEqual(
            _smart_bid_cap_blocker(
                amount_requested=10_000,
                available_krw=287_000,
                remaining_exposure=987_000,
                hard_cap=0,
                min_order_krw=5_000,
            ),
            "SMART_ORDER_CAP_ZERO",
        )

    def test_smart_bid_cap_blocker_classifies_insufficient_krw(self) -> None:
        self.assertEqual(
            _smart_bid_cap_blocker(
                amount_requested=10_000,
                available_krw=4_999,
                remaining_exposure=987_000,
                hard_cap=4_999,
                min_order_krw=5_000,
            ),
            "SMART_INSUFFICIENT_KRW_BALANCE",
        )

    def test_smart_bid_cap_blocker_classifies_remaining_exposure_below_min(self) -> None:
        self.assertEqual(
            _smart_bid_cap_blocker(
                amount_requested=10_000,
                available_krw=287_000,
                remaining_exposure=4_999,
                hard_cap=4_999,
                min_order_krw=5_000,
            ),
            "SMART_REMAINING_EXPOSURE_BELOW_MIN",
        )

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

    async def test_dust_bid_updates_intent_without_live_order_log(self) -> None:
        broker, snapshot = await self.submit_bid_intent(amount_requested=0.08, current_value=12_000, available_krw=287_000)

        broker.get_balances.assert_not_awaited()
        broker.place_order.assert_not_awaited()
        self.assertEqual(self.live_order_log_count(), 0)
        intent = snapshot["order_intents"][0]
        self.assertEqual(intent["status"], "BLOCKED")
        self.assertEqual(intent["promotion_status"], "DUST_HOLD")
        self.assertEqual(intent["promotion_blockers"], ["SMART_ORDER_AMOUNT_BELOW_MIN"])
        self.assertEqual(intent["policy_preview"]["amount_requested_krw"], 0.08)

    async def test_dust_ask_updates_intent_without_live_order_log(self) -> None:
        database.update_bot_operation_policy("KRW-BTC", {"auto_trading_enabled": True, "max_total_exposure_krw": 500_000, "daily_loss_limit_pct": 3})
        session = self.create_smart_session()
        snapshot_id, intent = self.create_intent(amount_requested=-0.49, current_value=12_000, side="ASK", action_hint="HOLD_POSITION")
        broker = AsyncMock()
        latest = candle()
        env = {
            "APP_ENV": "production",
            "LIVE_TRADING_ENABLED": "true",
            "LIVE_AUTO_TRADING_ENABLED": "true",
            "SMART_AUTONOMOUS_TRADING_ENABLED": "true",
            "SMART_ENGINE_LIVE_MODE": "live",
            "MIN_LIVE_ORDER_KRW": "5000",
        }
        with patch.dict("os.environ", env, clear=False), patch("app.live_strategy_pilot.get_live_broker", return_value=broker):
            await _submit_smart_intent_order(
                session,
                latest,
                {"signal": "HOLD", "reason": "smart test"},
                LiveStrategyConfig.from_env(),
                LiveTradingConfig.for_exchange("bithumb"),
                {
                    "id": snapshot_id,
                    "exchange": "bithumb",
                    "market": "KRW-BTC",
                    "current_bot_position_value_krw": 12_000,
                    "current_bot_position_qty": 0.00012415,
                    "max_total_exposure_krw": 500_000,
                    "risk_score": 35,
                    "order_intents": [intent],
                },
            )

        broker.get_balances.assert_not_awaited()
        self.assertEqual(self.live_order_log_count(), 0)
        updated = database.load_decision_snapshot(snapshot_id)
        assert updated is not None
        updated_intent = updated["order_intents"][0]
        self.assertEqual(updated_intent["status"], "BLOCKED")
        self.assertEqual(updated_intent["promotion_status"], "DUST_HOLD")
        self.assertEqual(updated_intent["promotion_blockers"], ["SMART_SELL_AMOUNT_BELOW_MIN"])

    async def test_normal_bid_still_uses_live_order_flow(self) -> None:
        broker, snapshot = await self.submit_bid_intent(amount_requested=100_000, current_value=0, available_krw=200_000)

        broker.get_balances.assert_awaited_once()
        broker.place_order.assert_awaited_once()
        self.assertGreater(self.live_order_log_count(), 0)
        intent = snapshot["order_intents"][0]
        self.assertEqual(intent["status"], "SUBMITTED")
        self.assertEqual(intent["promotion_status"], "SUBMITTED")

    async def test_core_bid_uses_core_entry_offset(self) -> None:
        broker, snapshot = await self.submit_bid_intent(
            amount_requested=100_000,
            current_value=0,
            available_krw=200_000,
            auto_entry_offset="0.3",
            core_entry_offset="0.1",
            target_source="CORE",
            core_exposure_pct=30,
            core_exposure_applied=True,
        )

        order = broker.place_order.await_args.args[0]
        self.assertEqual(order["price"], 99_900_000)
        intent = snapshot["order_intents"][0]
        self.assertEqual(intent["policy_preview"]["price_policy"], "CORE_ACCUMULATION_LIMIT")
        self.assertEqual(intent["policy_preview"]["entry_offset_percent"], 0.1)

    async def test_general_bid_uses_auto_entry_offset(self) -> None:
        broker, snapshot = await self.submit_bid_intent(
            amount_requested=100_000,
            current_value=200_000,
            available_krw=200_000,
            auto_entry_offset="0.3",
            core_entry_offset="0.1",
            target_source="AGGRESSIVE",
            core_exposure_pct=30,
            core_exposure_applied=False,
        )

        order = broker.place_order.await_args.args[0]
        self.assertEqual(order["price"], 99_700_000)
        intent = snapshot["order_intents"][0]
        self.assertEqual(intent["policy_preview"]["price_policy"], "DEFAULT_PASSIVE_LIMIT")
        self.assertEqual(intent["policy_preview"]["entry_offset_percent"], 0.3)

    async def test_core_offset_falls_back_to_auto_offset_when_env_is_missing(self) -> None:
        broker, snapshot = await self.submit_bid_intent(
            amount_requested=100_000,
            current_value=0,
            available_krw=200_000,
            auto_entry_offset="0.3",
            core_entry_offset=None,
            target_source="CORE",
            core_exposure_pct=30,
            core_exposure_applied=True,
        )

        order = broker.place_order.await_args.args[0]
        self.assertEqual(order["price"], 99_700_000)
        intent = snapshot["order_intents"][0]
        self.assertEqual(intent["policy_preview"]["entry_offset_percent"], 0.3)
        self.assertEqual(intent["policy_preview"]["core_entry_offset_percent"], 0.3)

    async def test_bid_policy_preview_records_price_context(self) -> None:
        broker, snapshot = await self.submit_bid_intent(
            amount_requested=100_000,
            current_value=0,
            available_krw=200_000,
            auto_entry_offset="0.3",
            core_entry_offset="0.1",
            target_source="CORE",
            core_exposure_pct=30,
            core_exposure_applied=True,
        )

        order = broker.place_order.await_args.args[0]
        intent = snapshot["order_intents"][0]
        preview = intent["policy_preview"]
        self.assertEqual(preview["price_policy"], "CORE_ACCUMULATION_LIMIT")
        self.assertEqual(preview["entry_offset_percent"], 0.1)
        self.assertEqual(preview["current_price"], 100_000_000)
        self.assertEqual(preview["order_price"], order["price"])
        self.assertAlmostEqual(preview["price_gap_pct"], 0.1)
        log = database.get_live_order_log(order["request_id"])
        assert log is not None
        self.assertEqual(log["exchange_request_payload_masked"]["submitted_price"], order["price"])
        self.assertEqual(log["exchange_request_payload_masked"]["submitted_best_bid"], 99_900_000)

    async def test_orderbook_failure_keeps_bid_submission_alive_with_null_top_of_book(self) -> None:
        broker, snapshot = await self.submit_bid_intent(
            amount_requested=100_000,
            current_value=0,
            available_krw=200_000,
            auto_entry_offset="0.3",
            core_entry_offset="0.1",
            target_source="CORE",
            core_exposure_pct=30,
            core_exposure_applied=True,
            orderbook_error=True,
        )

        broker.place_order.assert_awaited_once()
        intent = snapshot["order_intents"][0]
        self.assertIsNone(intent["policy_preview"]["best_bid"])
        self.assertIsNone(intent["policy_preview"]["best_ask"])

    async def test_unfilled_limit_cancel_keeps_smart_runtime_running(self) -> None:
        session = self.create_smart_session()
        database.insert_live_order_log(
            {
                "request_id": "smart-test-unfilled",
                "session_id": session["id"],
                "candidate_strategy_id": 0,
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "side": "BUY",
                "order_type": "LIMIT",
                "price": 100_000_000,
                "volume": 0.0001,
                "amount_krw": 10_000,
                "risk_result": "ALLOWED",
                "status": "SUBMITTED",
                "order_uuid": "unfilled-order-uuid",
            }
        )
        database.update_live_strategy_session(
            int(session["id"]),
            {
                "status": "RUNNING",
                "auto_enabled": True,
                "current_open_order_uuid": "unfilled-order-uuid",
                "last_order_status": "SUBMITTED",
                "last_order_time_utc": "2020-01-01T00:00:00Z",
            },
        )
        session = database.load_latest_live_strategy_session()
        assert session is not None
        broker = AsyncMock()
        broker.get_order.return_value = {
            "uuid": "unfilled-order-uuid",
            "state": "wait",
            "volume": "0.0001",
            "remaining_volume": "0.0001",
            "executed_volume": "0",
        }
        broker.cancel_order.return_value = {
            "uuid": "unfilled-order-uuid",
            "state": "cancel",
            "volume": "0.0001",
            "remaining_volume": "0.0001",
            "executed_volume": "0",
        }
        config = LiveStrategyConfig(
            exchange="bithumb",
            live_auto_trading_enabled=True,
            auto_strategy_pilot_enabled=True,
            smart_autonomous_trading_enabled=True,
            allowed_exchange="bithumb",
            allowed_market="KRW-BTC",
            allowed_order_type="limit",
            max_order_krw=30_000,
            max_orders_per_day=0,
            max_open_position_count=1,
            cooldown_seconds=0,
            require_completed_candle=False,
            cancel_unfilled_after_seconds=60,
            entry_price_offset_percent=0.3,
            core_entry_price_offset_percent=0.3,
            stop_loss_percent=0.7,
            take_profit_percent=1.0,
            max_hold_minutes=60,
            exit_enabled=True,
            market_order_enabled=False,
        )

        with patch("app.live_strategy_pilot.BithumbBroker", return_value=broker):
            await _manage_open_order(session, config)

        broker.cancel_order.assert_awaited_once_with("unfilled-order-uuid")
        updated = database.load_latest_live_strategy_session()
        assert updated is not None
        self.assertEqual(updated["status"], "RUNNING")
        self.assertTrue(updated["auto_enabled"])
        self.assertIsNone(updated["current_open_order_uuid"])
        self.assertEqual(updated["last_order_status"], "CANCELED")
        self.assertEqual(updated["last_risk_result"], "AUTO_CANCELED_UNFILLED")
        self.assertIsNone(updated["stopped_at"])
        order_log = database.get_live_order_log("smart-test-unfilled")
        assert order_log is not None
        self.assertEqual(order_log["status"], "CANCELED")


if __name__ == "__main__":
    unittest.main()
