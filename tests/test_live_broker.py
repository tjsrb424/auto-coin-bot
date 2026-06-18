from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.live_broker import (
    BithumbBroker,
    LiveBroker,
    LiveTradingConfig,
    UpbitBroker,
    evaluate_live_order_risk,
    get_live_broker,
    reset_live_runtime_state,
    current_live_mode,
    LiveBrokerError,
)
from app.live_paper import PaperBroker


def balances(krw: float = 100_000, btc: float = 0.01) -> dict:
    return {
        "by_currency": {
            "KRW": {"balance": krw, "locked": 0.0},
            "BTC": {"balance": btc, "locked": 0.0},
        },
        "krw": {"balance": krw, "locked": 0.0},
        "btc": {"balance": btc, "locked": 0.0},
    }


def config() -> LiveTradingConfig:
    return LiveTradingConfig(
        exchange="upbit",
        access_key_loaded=True,
        secret_key_loaded=True,
        live_trading_enabled=True,
        base_url="https://api.upbit.com",
        max_live_order_krw=10_000,
        max_daily_live_loss_percent=1,
        min_order_krw=5_000,
        max_position_ratio=0.5,
        duplicate_window_seconds=30,
        fee_rate=0.0005,
        volatility_block_rate=0.03,
        min_volume=0,
    )


def risk(order: dict, *, mode: str = "LIVE_MANUAL_ONLY", request_exists: bool = False, recent_duplicate: bool = False, account: dict | None = None) -> dict:
    return evaluate_live_order_risk(
        order=order,
        config=config(),
        mode=mode,
        balances=account or balances(),
        request_exists=request_exists,
        recent_duplicate=recent_duplicate,
        market_snapshot={"price": 100_000_000, "range_rate": 0.01, "volume": 10},
    )


class LiveBrokerRiskTests(unittest.TestCase):
    def test_live_locked_blocks_orders(self) -> None:
        result = risk({"market": "KRW-BTC", "side": "BUY", "order_type": "LIMIT", "amount_krw": 5_000, "price": 100_000_000}, mode="LIVE_LOCKED")
        self.assertFalse(result["allowed"])
        self.assertEqual(result["risk_result"], "BLOCKED_LIVE_LOCKED")

    def test_auto_strategy_running_allows_auto_order_when_live_enabled(self) -> None:
        result = evaluate_live_order_risk(
            order={"market": "KRW-BTC", "side": "BUY", "order_type": "LIMIT", "amount_krw": 5_000, "price": 100_000_000},
            config=config(),
            mode="AUTO_STRATEGY_RUNNING",
            balances=balances(krw=100_000, btc=0),
            request_exists=False,
            recent_duplicate=False,
            market_snapshot={"price": 100_000_000, "range_rate": 0.01, "volume": 10},
            is_auto=True,
        )
        self.assertTrue(result["allowed"])
        self.assertEqual(result["risk_result"], "ALLOWED")

    def test_auto_strategy_running_without_auto_flag_stays_locked(self) -> None:
        result = evaluate_live_order_risk(
            order={"market": "KRW-BTC", "side": "BUY", "order_type": "LIMIT", "amount_krw": 5_000, "price": 100_000_000},
            config=config(),
            mode="AUTO_STRATEGY_RUNNING",
            balances=balances(krw=100_000, btc=0),
            request_exists=False,
            recent_duplicate=False,
            market_snapshot={"price": 100_000_000, "range_rate": 0.01, "volume": 10},
        )
        self.assertFalse(result["allowed"])
        self.assertEqual(result["risk_result"], "BLOCKED_LIVE_LOCKED")

    def test_live_disabled_blocks_orders(self) -> None:
        disabled = LiveTradingConfig(
            **{**config().__dict__, "live_trading_enabled": False}
        )
        result = evaluate_live_order_risk(
            order={"market": "KRW-BTC", "side": "BUY", "order_type": "LIMIT", "amount_krw": 5_000, "price": 100_000_000},
            config=disabled,
            mode="PAPER",
            balances=balances(),
            request_exists=False,
            recent_duplicate=False,
            market_snapshot={"price": 100_000_000, "range_rate": 0.01, "volume": 10},
        )
        self.assertFalse(result["allowed"])
        self.assertEqual(result["risk_result"], "BLOCKED_LIVE_DISABLED")

    def test_emergency_stop_blocks_orders(self) -> None:
        result = risk({"market": "KRW-BTC", "side": "BUY", "order_type": "LIMIT", "amount_krw": 5_000, "price": 100_000_000}, mode="EMERGENCY_STOPPED")
        self.assertEqual(result["risk_result"], "BLOCKED_EMERGENCY_STOP")

    def test_max_order_amount_blocks_large_buy(self) -> None:
        result = risk({"market": "KRW-BTC", "side": "BUY", "order_type": "LIMIT", "amount_krw": 20_000, "price": 100_000_000})
        self.assertEqual(result["risk_result"], "BLOCKED_MAX_ORDER_AMOUNT")

    def test_insufficient_balance_blocks_buy(self) -> None:
        result = risk(
            {"market": "KRW-BTC", "side": "BUY", "order_type": "LIMIT", "amount_krw": 5_000, "price": 100_000_000},
            account=balances(krw=1_000, btc=0),
        )
        self.assertEqual(result["risk_result"], "BLOCKED_INSUFFICIENT_BALANCE")

    def test_insufficient_position_blocks_sell(self) -> None:
        result = risk(
            {"market": "KRW-BTC", "side": "SELL", "order_type": "LIMIT", "volume": 0.01, "price": 100_000_000},
            account=balances(krw=100_000, btc=0),
        )
        self.assertEqual(result["risk_result"], "BLOCKED_INSUFFICIENT_POSITION")

    def test_duplicate_request_blocks_order(self) -> None:
        result = risk({"market": "KRW-BTC", "side": "BUY", "order_type": "LIMIT", "amount_krw": 5_000, "price": 100_000_000}, request_exists=True)
        self.assertEqual(result["risk_result"], "BLOCKED_DUPLICATE_ORDER")

    def test_live_and_paper_brokers_are_separate_classes(self) -> None:
        self.assertIsNot(LiveBroker, PaperBroker)
        self.assertIs(LiveBroker, UpbitBroker)
        self.assertIsNot(BithumbBroker, UpbitBroker)

    def test_bithumb_config_reads_bithumb_keys(self) -> None:
        with patch.dict(
            os.environ,
            {
                "EXCHANGE": "bithumb",
                "BITHUMB_ACCESS_KEY": "loaded",
                "BITHUMB_SECRET_KEY": "loaded",
                "UPBIT_ACCESS_KEY": "",
                "UPBIT_SECRET_KEY": "",
                "LIVE_TRADING_ENABLED": "false",
            },
            clear=False,
        ):
            loaded = LiveTradingConfig.from_env()
        self.assertEqual(loaded.exchange, "bithumb")
        self.assertTrue(loaded.api_key_loaded)
        self.assertFalse(loaded.live_trading_enabled)

    def test_broker_factory_rejects_unknown_exchange(self) -> None:
        with self.assertRaises(LiveBrokerError):
            get_live_broker("unknown")

    def test_server_restart_does_not_keep_live_manual_mode(self) -> None:
        with patch.dict(os.environ, {"ALLOW_DEV_LIVE_TRADING": "true", "LIVE_TRADING_ENABLED": "true"}, clear=False):
            reset_live_runtime_state()
            self.assertEqual(current_live_mode(), "LIVE_LOCKED")


if __name__ == "__main__":
    unittest.main()
