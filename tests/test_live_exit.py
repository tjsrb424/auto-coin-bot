from __future__ import annotations

import tempfile
import unittest
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import database
from app.live_broker import LiveTradingConfig
from app.live_exit import (
    approve_exit_candidate,
    create_exit_candidate_for_position,
    create_exit_order_preview,
    evaluate_exit_order,
    manage_exit_order_timeout,
    maybe_create_price_exit_candidate,
    submit_exit_order,
)
from app.live_strategy_pilot import LiveStrategyConfig, _process_open_position


class LiveExitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.env_patch = patch.dict(
            os.environ,
            {
                "MIN_LIVE_ORDER_KRW": "5000",
                "AUTO_EXIT_ORDER_TYPE": "limit",
                "AUTO_MARKET_ORDER_ENABLED": "false",
                "AUTO_EXIT_REQUIRE_MANUAL_CONFIRM": "false",
                "AUTO_MAX_EXIT_RETRY_COUNT": "2",
            },
            clear=False,
        )
        self.env_patch.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        database.init_db()
        self.session_id = database.create_live_strategy_session(
            {
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 1,
                "strategy_name": "ma_cross",
                "strategy_parameters": {},
                "status": "RUNNING",
                "auto_enabled": True,
                "initial_balance_krw": 0,
                "max_order_krw": 10_000,
                "max_orders_per_day": 1,
            }
        )

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.env_patch.stop()
        self.tmp.cleanup()

    def create_position(self, *, entry_price: float = 100_000_000, opened_minutes_ago: int = 10, volume: float = 0.0001) -> dict:
        opened_at = (datetime.now(timezone.utc) - timedelta(minutes=opened_minutes_ago)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        position_id = database.create_live_position(
            {
                "session_id": self.session_id,
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": 1,
                "strategy_name": "ma_cross",
                "status": "OPEN",
                "entry_order_uuid": "entry-1",
                "entry_price": entry_price,
                "entry_volume": volume,
                "entry_amount_krw": entry_price * volume,
                "current_price": entry_price,
                "unrealized_pnl": 0,
                "realized_pnl": 0,
                "stop_loss_price": entry_price * 0.993,
                "take_profit_price": entry_price * 1.01,
                "opened_at": opened_at,
            }
        )
        position = database.load_live_position(position_id)
        assert position is not None
        return position

    def test_stop_loss_exit_candidate_created_without_order(self) -> None:
        position = self.create_position()
        candidate = maybe_create_price_exit_candidate(position, 99_300_000, "2026-06-16T00:00:00Z")

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate["reason"], "STOP_LOSS")
        self.assertEqual(candidate["status"], "PENDING")
        self.assertFalse(database.has_open_exit_order(position["id"]))

    def test_take_profit_exit_candidate_created_without_order(self) -> None:
        position = self.create_position()
        candidate = maybe_create_price_exit_candidate(position, 101_000_000, "2026-06-16T00:00:00Z")

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate["reason"], "TAKE_PROFIT")
        self.assertFalse(database.has_open_exit_order(position["id"]))

    def test_max_hold_time_exit_candidate_created(self) -> None:
        position = self.create_position(opened_minutes_ago=91)
        candidate = maybe_create_price_exit_candidate(position, 100_100_000, "2026-06-16T00:00:00Z")

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate["reason"], "MAX_HOLD_TIME")

    async def test_auto_exit_disabled_blocks_auto_order(self) -> None:
        position = self.create_position()
        candidate = create_exit_candidate_for_position(position, "STRATEGY_SELL", 100_000_000)
        assert candidate is not None

        with patch.dict(os.environ, {"AUTO_EXIT_ENABLED": "false"}, clear=False):
            risk = await evaluate_exit_order(candidate, position, manual_confirmed=False, is_auto_exit=True)

        self.assertFalse(risk["allowed"])
        self.assertEqual(risk["risk_result"], "BLOCKED_EXIT_DISABLED")

    async def test_exit_preview_blocks_single_dust_position_below_min_order(self) -> None:
        position = self.create_position(volume=0.00002)
        candidate = create_exit_candidate_for_position(position, "STOP_LOSS", 100_000_000)
        assert candidate is not None
        approve_exit_candidate(int(candidate["id"]))
        broker = AsyncMock()
        broker.get_balances.return_value = {
            "by_currency": {"BTC": {"balance": 0.00002, "locked": 0.0}},
            "btc": {"balance": 0.00002, "locked": 0.0},
            "krw": {"balance": 0.0, "locked": 0.0},
        }
        broker.get_order_chance.return_value = {"market": "KRW-BTC"}

        with (
            patch("app.live_exit.reconcile_balances", new=AsyncMock(return_value={"ok": True, "blocking": False})),
            patch("app.live_exit.get_live_broker", return_value=broker),
            patch("app.capital_snapshot.get_live_broker", return_value=broker),
        ):
            result = await create_exit_order_preview(int(candidate["id"]), manual_confirmed=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["preview"]["risk_result"], "BLOCKED_SELL_VOLUME_BELOW_MIN")

    async def test_exit_preview_aggregates_same_market_dust_positions(self) -> None:
        larger = self.create_position(volume=0.0001)
        dust = self.create_position(volume=0.00002)
        candidate = create_exit_candidate_for_position(dust, "STOP_LOSS", 100_000_000)
        assert candidate is not None
        approve_exit_candidate(int(candidate["id"]))
        broker = AsyncMock()
        broker.get_balances.return_value = {
            "by_currency": {"BTC": {"balance": 0.00012, "locked": 0.0}},
            "btc": {"balance": 0.00012, "locked": 0.0},
            "krw": {"balance": 0.0, "locked": 0.0},
        }
        broker.get_order_chance.return_value = {"market": "KRW-BTC"}

        with (
            patch("app.live_exit.reconcile_balances", new=AsyncMock(return_value={"ok": True, "blocking": False})),
            patch("app.live_exit.get_live_broker", return_value=broker),
            patch("app.capital_snapshot.get_live_broker", return_value=broker),
        ):
            result = await create_exit_order_preview(int(candidate["id"]), manual_confirmed=True)

        self.assertTrue(result["ok"])
        self.assertAlmostEqual(result["preview"]["volume"], 0.00012)
        self.assertEqual(result["preview"]["amount_krw"], 11_976)
        self.assertTrue(result["preview"]["aggregate_exit"])
        self.assertEqual(result["preview"]["aggregate_exit_position_ids"], [larger["id"], dust["id"]])

    async def test_manual_exit_preview_records_exit_order_log(self) -> None:
        position = self.create_position()
        candidate = create_exit_candidate_for_position(position, "STRATEGY_SELL", 100_000_000)
        assert candidate is not None
        approve_exit_candidate(int(candidate["id"]))
        broker = AsyncMock()
        broker.get_balances.return_value = {
            "by_currency": {"BTC": {"balance": 0.0001, "locked": 0.0}},
            "btc": {"balance": 0.0001, "locked": 0.0},
            "krw": {"balance": 0.0, "locked": 0.0},
        }
        broker.get_order_chance.return_value = {"market": "KRW-BTC"}

        with (
            patch("app.live_recovery.get_live_broker", return_value=broker),
            patch("app.live_exit.get_live_broker", return_value=broker),
            patch("app.capital_snapshot.get_live_broker", return_value=broker),
        ):
            result = await create_exit_order_preview(int(candidate["id"]), manual_confirmed=True)

        self.assertTrue(result["ok"])
        log = database.get_live_order_log(result["request_id"])
        assert log is not None
        self.assertEqual(log["order_purpose"], "EXIT")
        self.assertEqual(log["side"], "SELL")
        self.assertEqual(log["exit_reason"], "STRATEGY_SELL")
        self.assertTrue(log["manual_confirmed"])

    async def test_manual_exit_submit_reuses_preview_request_for_duplicate_check(self) -> None:
        position = self.create_position()
        candidate = create_exit_candidate_for_position(position, "STRATEGY_SELL", 100_000_000, "2026-06-18T13:45:00Z")
        assert candidate is not None
        approve_exit_candidate(int(candidate["id"]))
        broker = AsyncMock()
        broker.get_balances.return_value = {
            "by_currency": {"BTC": {"balance": 0.0001, "locked": 0.0}},
            "btc": {"balance": 0.0001, "locked": 0.0},
            "krw": {"balance": 0.0, "locked": 0.0},
        }
        broker.get_order_chance.return_value = {"market": "KRW-BTC"}
        broker.place_order.return_value = {}

        with (
            patch("app.live_recovery.get_live_broker", return_value=broker),
            patch("app.live_exit.get_live_broker", return_value=broker),
            patch("app.capital_snapshot.get_live_broker", return_value=broker),
        ):
            preview = await create_exit_order_preview(int(candidate["id"]), manual_confirmed=True)
            result = await submit_exit_order(preview["request_id"], final_confirmation="SUBMIT LIMIT EXIT ORDER")

        self.assertTrue(result["ok"])
        broker.place_order.assert_awaited_once()

    async def test_candidate_less_smart_exit_waiting_order_is_canceled_after_timeout(self) -> None:
        position = self.create_position()
        database.update_live_position(int(position["id"]), {"status": "CLOSING", "exit_order_uuid": "exit-waiting"})
        database.insert_live_order_log(
            {
                "request_id": "smart-exit-waiting",
                "session_id": self.session_id,
                "candidate_strategy_id": 1,
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "side": "SELL",
                "order_type": "LIMIT",
                "price": 99_000_000,
                "volume": 0.0001,
                "amount_krw": 9_900,
                "fee_estimate": 4.95,
                "risk_result": "ALLOWED",
                "status": "WAITING",
                "order_uuid": "exit-waiting",
                "position_id": position["id"],
                "order_purpose": "EXIT",
                "is_auto_exit": True,
            }
        )
        broker = AsyncMock()
        broker.get_order.return_value = {
            "uuid": "exit-waiting",
            "state": "wait",
            "created_at": "2020-01-01T00:00:00+00:00",
            "volume": "0.0001",
            "remaining_volume": "0.0001",
            "executed_volume": "0",
            "executed_funds": "0",
            "paid_fee": "0",
        }
        broker.cancel_order.return_value = {
            "uuid": "exit-waiting",
            "state": "cancel",
            "volume": "0.0001",
            "remaining_volume": "0.0001",
            "executed_volume": "0",
        }

        with patch("app.live_exit.get_live_broker", return_value=broker):
            await manage_exit_order_timeout(database.load_live_position(int(position["id"])), LiveExitConfigForTest())

        broker.cancel_order.assert_awaited_once_with("exit-waiting")
        updated_log = database.get_live_order_log("smart-exit-waiting")
        updated_position = database.load_live_position(int(position["id"]))
        updated_session = database.load_latest_live_strategy_session()
        assert updated_log is not None and updated_position is not None and updated_session is not None
        self.assertEqual(updated_log["status"], "CANCELED")
        self.assertEqual(updated_position["status"], "OPEN")
        self.assertIsNone(updated_position["exit_order_uuid"])
        self.assertEqual(updated_session["last_risk_result"], "AUTO_CANCELED_STALE_EXIT_ORDER")

    async def test_open_position_smart_limited_uses_live_trading_config(self) -> None:
        position = self.create_position()
        session = database.load_latest_live_strategy_session()
        assert session is not None
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
            core_order_cooldown_seconds=60,
            core_marketable_limit_enabled=False,
            core_marketable_limit_max_slippage_pct=0.15,
            core_marketable_limit_price_buffer_pct=0.02,
            stop_loss_percent=0.7,
            take_profit_percent=1.0,
            max_hold_minutes=60,
            exit_enabled=False,
            market_order_enabled=False,
        )
        live_config = LiveTradingConfig.for_exchange("bithumb")
        candle = {
            "market": "KRW-BTC",
            "unit": 15,
            "candle_time_utc": "2026-06-18T13:45:00Z",
            "trade_price": 100_000_000,
            "opening_price": 100_000_000,
            "high_price": 100_000_000,
            "low_price": 100_000_000,
            "candle_acc_trade_volume": 1.0,
            "candle_acc_trade_price": 100_000_000,
        }
        submit_mock = AsyncMock(return_value=True)

        with (
            patch("app.live_strategy_pilot.load_candidate_strategy", return_value={"id": 1, "unit": 15, "market": "KRW-BTC", "strategy": "ma_cross", "parameters": {}}),
            patch("app.live_strategy_pilot.fetch_minute_candles", new=AsyncMock(return_value=[candle])),
            patch("app.live_strategy_pilot.insert_candles"),
            patch("app.live_strategy_pilot.load_candles", return_value=[candle]),
            patch("app.live_strategy_pilot._latest_signal", return_value={"signal": "SELL", "reason": "test"}),
            patch("app.live_strategy_pilot._record_smart_decision", new=AsyncMock(return_value={"order_intents": [{"side": "ASK"}]})),
            patch("app.live_strategy_pilot.smart_engine_live_mode", return_value="limited"),
            patch("app.live_strategy_pilot._submit_smart_intent_order", new=submit_mock),
        ):
            await _process_open_position(session, position, config, live_config)

        submit_mock.assert_awaited_once()
        passed_live_config = submit_mock.await_args.args[4]
        self.assertTrue(hasattr(passed_live_config, "fee_rate"))
        self.assertIs(passed_live_config, live_config)


class LiveExitConfigForTest:
    cancel_exit_order_after_seconds = 45
    max_exit_retry_count = 2


if __name__ == "__main__":
    unittest.main()
