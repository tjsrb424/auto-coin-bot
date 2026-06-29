from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import database
from app.controlled_auto_live import (
    CONFIRMATION_PHRASE,
    DRY_RUN_CONFIRMATION_PHRASE,
    TRADE_PROBE_CONFIRMATION_PHRASE,
    _controlled_jobs,
    _finalize_after_orders,
    _ma_cross_decision,
    run_controlled_auto_live,
    run_controlled_auto_live_dry_run_force_buy,
    run_controlled_trade_probe,
    start_controlled_auto_live_job,
)


class ControlledAutoLiveTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        database.init_db()
        database.update_bot_operation_policy(
            "KRW-BTC",
            {"auto_trading_enabled": False, "max_total_exposure_krw": 300_000, "daily_loss_limit_pct": 3},
        )
        with database.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO runtime_locks (
                    lock_id, instance_id, hostname, app_env, runtime_owner,
                    status, acquired_at, expires_at, updated_at
                ) VALUES ('auto-trading', 'test', 'test-host', 'test', 'test', 'STOPPED',
                    '2026-06-26T00:00:00Z', '2026-06-26T01:00:00Z', '2026-06-26T00:00:00Z')
                """
            )
        database.create_accounting_epoch(
            {
                "exchange_name": "bithumb",
                "epoch_id": "epoch-controlled",
                "epoch_started_at_utc": "2026-06-26T00:00:00Z",
                "starting_exchange_equity": 263_000,
                "starting_cash_krw": 263_000,
                "starting_positions": [],
                "starting_position_count": 0,
                "cost_basis_policy": "MARK_TO_MARKET",
                "epoch_trust_level": "MEDIUM",
                "legacy_history_isolated": True,
            }
        )

    def tearDown(self) -> None:
        _controlled_jobs.clear()
        self.db_patch.stop()
        self.tmp.cleanup()

    async def test_confirmation_required_before_any_order(self) -> None:
        broker = AsyncMock()
        with patch("app.controlled_auto_live.get_live_broker", return_value=broker):
            result = await run_controlled_auto_live(confirmation="NOPE")

        self.assertEqual(result["controlled_auto_live_status"], "ABORTED")
        broker.place_order.assert_not_awaited()
        with database.get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) AS count FROM live_order_logs").fetchone()["count"]
        self.assertEqual(count, 0)

    def test_ma_cross_buy_below_expected_edge_is_blocked(self) -> None:
        candles = []
        prices = [100.0] * 21
        prices[-2] = 100.0
        prices[-1] = 100.05
        for index, price in enumerate(prices):
            candles.append(
                {
                    "candle_time_utc": f"2026-06-26T00:{index:02d}:00Z",
                    "opening_price": price,
                    "high_price": price,
                    "low_price": price,
                    "trade_price": price,
                    "candle_acc_trade_volume": 1,
                }
            )

        decision = _ma_cross_decision(
            "BTC",
            "KRW-BTC",
            candles,
            {"best_bid": 100.0, "best_ask": 100.05},
            6000,
        )

        if decision["signal"] == "BUY":
            self.assertFalse(decision["edge_allowed"])
            self.assertEqual(decision["blocker"], "BLOCKED_EXPECTED_EDGE_BELOW_COST")
        self.assertGreaterEqual(decision["min_expected_edge_rate"], 0.006)

    async def test_forced_dry_run_force_buy_creates_preview_without_exchange_or_ledger_fill(self) -> None:
        current_epoch = {
            "current_epoch_sanity_passed": True,
            "current_epoch_trust_level": "MEDIUM",
            "current_epoch_accounting_pending_count": 0,
            "current_epoch_accounting_failed_count": 0,
            "current_epoch_total_pnl": 0.0,
        }

        with (
            patch.dict(
                "os.environ",
                {
                    "FULL_AUTO_LIVE": "false",
                    "FULL_AUTO_LIVE_ENABLED": "false",
                    "AUTO_FULL_LIVE_ENABLED": "false",
                    "MIN_LIVE_ORDER_KRW": "5000",
                },
            ),
            patch("app.controlled_auto_live._current_equity", return_value=263_000.0),
            patch("app.controlled_auto_live._orderbook_quote", return_value={"best_bid": 100_000_000.0, "best_ask": 100_010_000.0}),
        ):
            result = await run_controlled_auto_live_dry_run_force_buy(
                symbol="BTC",
                amount_krw=6000,
                runtime_seconds=600,
                confirmation=DRY_RUN_CONFIRMATION_PHRASE,
                current_epoch=current_epoch,
            )

        self.assertEqual(result["controlled_auto_live_status"], "PASSED")
        self.assertTrue(result["dry_run"])
        self.assertTrue(result["forced_signal"])
        self.assertEqual(result["order_count"], 0)
        self.assertEqual(result["order_preview_count"], 1)
        self.assertEqual(result["exchange_fill_count"], 0)
        self.assertEqual(result["ledger_fill_count"], 0)
        self.assertEqual(result["missing_ledger_fill_count"], 0)
        self.assertEqual(result["duplicate_fill_count"], 0)
        self.assertTrue(result["risk_decision"]["allowed"])
        self.assertEqual(result["risk_decision"]["risk_result"], "DRY_RUN_ALLOWED")
        self.assertEqual(result["order_preview"]["market"], "KRW-BTC")
        self.assertGreater(result["order_preview"]["estimated_fee"], 0)
        self.assertIn("estimated_slippage", result["order_preview"])
        with database.get_connection() as conn:
            order_count = conn.execute("SELECT COUNT(*) AS count FROM live_order_logs").fetchone()["count"]
            ledger_count = conn.execute("SELECT COUNT(*) AS count FROM exchange_fills_ledger").fetchone()["count"]
        self.assertEqual(order_count, 0)
        self.assertEqual(ledger_count, 0)

    async def test_forced_dry_run_force_buy_blocks_when_full_auto_live_is_enabled(self) -> None:
        with patch.dict("os.environ", {"FULL_AUTO_LIVE": "true"}):
            result = await run_controlled_auto_live_dry_run_force_buy(
                symbol="BTC",
                amount_krw=6000,
                runtime_seconds=600,
                confirmation=DRY_RUN_CONFIRMATION_PHRASE,
                current_epoch={"current_epoch_sanity_passed": True},
            )

        self.assertEqual(result["controlled_auto_live_status"], "ABORTED")
        self.assertIn("FULL_AUTO_LIVE_MUST_REMAIN_FALSE", result["pass_fail_reasons"])
        self.assertEqual(result["order_preview_count"], 0)

    async def test_zero_order_epoch_pnl_move_is_reported_as_existing_position_valuation(self) -> None:
        report = {
            "controlled_run_id": "controlled-test-zero-order",
            "controlled_auto_live_status": "FAILED",
            "started_at_utc": "2026-06-26T00:00:00Z",
            "order_count": 0,
            "run_pnl": 0.0,
            "run_realized_pnl": 0.0,
            "run_mark_to_market_delta": 0.0,
            "missing_ledger_fill_count": 0,
            "duplicate_fill_count": 0,
            "fee_diff": 0.0,
            "account_epoch_pnl_before": 0.0,
            "report_notes": [],
        }

        with (
            patch("app.controlled_auto_live._current_equity", return_value=262_900.0),
            patch("app.controlled_auto_live._open_order_count", return_value=0),
        ):
            result = await _finalize_after_orders(
                report,
                "bithumb",
                [],
                "PASSED",
                [],
                263_000.0,
                None,
            )

        self.assertEqual(result["controlled_auto_live_status"], "PASS_IDLE")
        self.assertEqual(result["order_count"], 0)
        self.assertEqual(result["run_pnl"], 0.0)
        self.assertEqual(result["account_epoch_pnl_delta"], -100.0)
        self.assertIn("기존 보유자산 평가손익 변화", result["pnl_explanation"])
        self.assertTrue(result["report_notes"])

    async def test_trade_run_is_reported_as_passed_trade(self) -> None:
        report = {
            "controlled_run_id": "controlled-test-trade",
            "controlled_auto_live_status": "FAILED",
            "started_at_utc": "2026-06-26T00:00:00Z",
            "order_count": 1,
            "run_pnl": 0.0,
            "run_realized_pnl": 0.0,
            "run_mark_to_market_delta": 0.0,
            "missing_ledger_fill_count": 0,
            "duplicate_fill_count": 0,
            "fee_diff": 0.0,
            "account_epoch_pnl_before": 0.0,
            "report_notes": [],
        }

        with (
            patch("app.controlled_auto_live._current_equity", return_value=263_000.0),
            patch("app.controlled_auto_live._open_order_count", return_value=0),
        ):
            result = await _finalize_after_orders(
                report,
                "bithumb",
                [],
                "PASSED",
                [],
                263_000.0,
                None,
            )

        self.assertEqual(result["controlled_auto_live_status"], "PASSED_TRADE")
        self.assertEqual(result["order_count"], 1)

    async def test_trade_probe_places_buy_sell_and_records_risk_decision(self) -> None:
        broker = AsyncMock()
        broker.list_open_orders.return_value = {"orders": []}
        broker.place_order.side_effect = [
            {"uuid": "C0101000003129835701", "market": "KRW-BTC", "side": "bid"},
            {"uuid": "C0101000003129835702", "market": "KRW-BTC", "side": "ask"},
        ]

        async def reconcile(log: dict, source: str) -> SimpleNamespace:
            side = str(log["side"]).upper()
            order_uuid = str(log["order_uuid"])
            volume = 0.00001
            amount = 6000.0 if side == "BUY" else 5990.0
            fee = 3.0 if side == "BUY" else 2.995
            database.update_live_order_log(
                str(log["request_id"]),
                {
                    "status": "FILLED",
                    "risk_result": "CONTROLLED_TRADE_PROBE_FILLED",
                    "exchange_response_payload": {
                        "uuid": order_uuid,
                        "client_order_id": log["client_order_id"],
                        "market": "KRW-BTC",
                        "side": "bid" if side == "BUY" else "ask",
                        "price": str(amount / volume),
                        "executed_volume": str(volume),
                        "executed_funds": str(amount),
                        "paid_fee": str(fee),
                        "created_at": "2026-06-26T17:00:00+09:00",
                        "trades": [
                            {
                                "uuid": f"{order_uuid}-fill",
                                "price": str(amount / volume),
                                "volume": str(volume),
                                "funds": str(amount),
                                "fee": str(fee),
                                "created_at": "2026-06-26T17:00:00+09:00",
                            }
                        ],
                    },
                    "executed_volume": volume,
                    "remaining_volume": 0,
                    "filled_amount_krw": amount,
                    "paid_fee": fee,
                },
            )
            return SimpleNamespace(
                status="FILLED",
                executed_volume=volume,
                remaining_volume=0.0,
                filled_amount_krw=amount,
                paid_fee=fee,
                raw={},
            )

        current_epoch = {
            "current_epoch_exists": True,
            "current_epoch_id": "epoch-controlled",
            "current_epoch_started_at_utc": "2026-06-26T00:00:00Z",
            "current_epoch_trust_level": "MEDIUM",
            "current_epoch_sanity_passed": True,
            "current_epoch_total_pnl": 0.0,
            "current_epoch_accounting_pending_count": 0,
            "current_epoch_accounting_failed_count": 0,
        }
        gate = {"controlled_auto_live_allowed": True, "controlled_auto_live_blockers": []}
        with (
            patch.dict("os.environ", {"MIN_LIVE_ORDER_KRW": "5000"}, clear=False),
            patch("app.controlled_auto_live.get_live_broker", return_value=broker),
            patch("app.controlled_auto_live._full_auto_live_disabled", return_value=True),
            patch("app.controlled_auto_live._runtime_guards_pass", return_value=True),
            patch("app.controlled_auto_live._current_equity", side_effect=[263_000.0, 262_990.0]),
            patch("app.controlled_auto_live._orderbook_quote", side_effect=[{"best_ask": 600_000_000.0, "best_bid": 599_000_000.0}, {"best_ask": 600_000_000.0, "best_bid": 599_000_000.0}]),
            patch("app.controlled_auto_live.reconcile_order_log", side_effect=reconcile),
        ):
            result = await run_controlled_trade_probe(
                confirmation=TRADE_PROBE_CONFIRMATION_PHRASE,
                controlled_gate=gate,
                current_epoch=current_epoch,
            )

        self.assertEqual(result["controlled_auto_live_status"], "PASSED_TRADE_PROBE", result.get("pass_fail_reasons"))
        self.assertEqual(result["run_type"], "CONTROLLED_TRADE_PROBE")
        self.assertEqual(result["order_count"], 2)
        self.assertEqual(result["buy_filled_count"], 1)
        self.assertEqual(result["sell_filled_count"], 1)
        self.assertEqual(result["exchange_fill_count"], result["ledger_fill_count"])
        self.assertEqual(result["missing_ledger_fill_count"], 0)
        self.assertEqual(result["duplicate_fill_count"], 0)
        self.assertEqual(result["fee_diff"], 0.0)
        self.assertEqual(result["open_order_count_after"], 0)
        self.assertEqual(result["risk_decision"]["strategy_source"], "controlled_trade_probe")
        self.assertTrue(result["risk_decision"]["risk_allowed"])
        self.assertEqual(broker.place_order.await_count, 2)
        with database.get_connection() as conn:
            rows = conn.execute("SELECT order_purpose, strategy_name, order_preview_payload FROM live_order_logs ORDER BY id").fetchall()
        self.assertEqual([row["order_purpose"] for row in rows], ["CONTROLLED_TRADE_PROBE", "CONTROLLED_TRADE_PROBE"])
        previews = [json.loads(row["order_preview_payload"]) for row in rows]
        self.assertEqual(previews[0]["run_type"], "CONTROLLED_TRADE_PROBE")
        self.assertEqual(previews[0]["risk_decision"]["strategy_source"], "controlled_trade_probe")

    async def test_async_controlled_job_blocks_duplicate_active_run(self) -> None:
        async def fake_run(**kwargs):
            await asyncio.sleep(0.05)
            return {
                "controlled_run_id": kwargs["controlled_run_id"],
                "controlled_auto_live_status": "PASS_IDLE",
                "completed_at_utc": "2026-06-26T00:10:00Z",
                "order_count": 0,
                "final_runtime_status": "STOPPED",
            }

        current_epoch = {
            "current_epoch_sanity_passed": True,
            "current_epoch_trust_level": "MEDIUM",
            "current_epoch_accounting_pending_count": 0,
            "current_epoch_accounting_failed_count": 0,
            "current_epoch_total_pnl": 0.0,
        }
        gate = {"controlled_auto_live_allowed": True, "controlled_auto_live_blockers": []}
        with patch("app.controlled_auto_live.run_controlled_auto_live", side_effect=fake_run):
            first = await start_controlled_auto_live_job(
                exchange="bithumb",
                symbols=["BTC", "ETH"],
                amount_krw=6000,
                runtime_seconds=600,
                confirmation=CONFIRMATION_PHRASE,
                controlled_gate=gate,
                current_epoch=current_epoch,
            )
            second = await start_controlled_auto_live_job(
                exchange="bithumb",
                symbols=["BTC", "ETH"],
                amount_krw=6000,
                runtime_seconds=600,
                confirmation=CONFIRMATION_PHRASE,
                controlled_gate=gate,
                current_epoch=current_epoch,
            )
            await asyncio.sleep(0.1)

        self.assertEqual(first["status"], "STARTING")
        self.assertFalse(second["ok"])
        self.assertEqual(second["status"], "ABORTED")
        final = _controlled_jobs[first["controlled_run_id"]]
        self.assertEqual(final["status"], "PASS_IDLE")


if __name__ == "__main__":
    unittest.main()
