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
    CONTROLLED_POSITION_LOOP_CONFIRMATION_PHRASE,
    DRY_RUN_CONFIRMATION_PHRASE,
    ENTRY_V3_POSITION_RUN_CONFIRMATION_PHRASE,
    ENTRY_V3_WATCH_CONFIRMATION_PHRASE,
    TRADE_PROBE_CONFIRMATION_PHRASE,
    _controlled_client_order_id,
    _diagnose_signal_decision,
    _controlled_order_seq_by_run,
    _controlled_entry_v2_decision,
    _controlled_entry_v3_decision,
    _controlled_jobs,
    _finalize_after_orders,
    _ma_cross_decision,
    _summarize_signal_diagnostics,
    _select_best_decision,
    _select_controlled_entry_v3_watch_candidate,
    _threshold_adjustment_report,
    build_protected_session_baseline,
    controlled_auto_live_gate,
    persist_controlled_run_report,
    protected_position_scope_status,
    record_resolved_duplicate_client_order_safety_event,
    run_controlled_entry_v3_watch,
    run_controlled_entry_v3_position_run,
    run_controlled_position_loop,
    run_controlled_auto_live,
    run_controlled_auto_live_dry_run_force_buy,
    run_controlled_trade_probe,
    start_controlled_entry_v3_watch_job,
    start_controlled_entry_v3_position_run_job,
    start_controlled_position_loop_job,
    start_controlled_auto_live_job,
    update_protected_session_loss_status,
)


def candidate_payload(market: str = "KRW-BTC", status: str = "LIVE_ACTIVE", strategy: str = "controlled_entry_v3") -> dict:
    return {
        "name": f"{market} controlled test",
        "description": "",
        "strategy": strategy,
        "parameters": {},
        "unit": 5,
        "market": market,
        "backtest_period": "30d",
        "score": 95.0,
        "backtest_total_return": 0.04,
        "backtest_mdd": 0.04,
        "backtest_win_rate": 0.55,
        "backtest_profit_factor": 1.4,
        "backtest_trade_count": 12,
        "backtest_average_trade_pnl": 0.002,
        "warning": "",
        "status": status,
    }


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
        _controlled_order_seq_by_run.clear()
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

    def test_signal_diagnostics_records_no_signal_blockers(self) -> None:
        ma = _diagnose_signal_decision(
            {
                "symbol": "BTC",
                "strategy": "ma_cross",
                "signal": "HOLD",
                "expected_edge_rate": 0.001,
                "min_expected_edge_rate": 0.006,
                "estimated_roundtrip_fee_rate": 0.005,
                "estimated_spread_rate": 0.0001,
                "estimated_round_trip_cost_rate": 0.0051,
                "edge_allowed": False,
                "candle_count": 30,
            }
        )
        smart = _diagnose_signal_decision(
            {
                "symbol": "ETH",
                "strategy": "smart_autonomous",
                "signal": "HOLD",
                "expected_edge_rate": 0.001,
                "min_expected_edge_rate": 0.006,
                "estimated_roundtrip_fee_rate": 0.005,
                "estimated_spread_rate": 0.0001,
                "estimated_round_trip_cost_rate": 0.0051,
                "edge_allowed": False,
                "candle_count": 30,
            }
        )

        self.assertIn("NO_MA_CROSS_SIGNAL", ma["block_reasons"])
        self.assertIn("SMART_SCORE_TOO_LOW", smart["block_reasons"])
        self.assertTrue(ma["blocked"])
        self.assertTrue(smart["blocked"])

    def test_signal_diagnostics_records_threshold_and_fee_cost_blockers(self) -> None:
        below_threshold = _diagnose_signal_decision(
            {
                "symbol": "BTC",
                "strategy": "ma_cross",
                "signal": "BUY",
                "expected_edge_rate": 0.0055,
                "min_expected_edge_rate": 0.006,
                "estimated_roundtrip_fee_rate": 0.004,
                "estimated_spread_rate": 0.001,
                "estimated_round_trip_cost_rate": 0.005,
                "edge_allowed": False,
            }
        )
        below_cost = _diagnose_signal_decision(
            {
                "symbol": "BTC",
                "strategy": "ma_cross",
                "signal": "BUY",
                "expected_edge_rate": 0.004,
                "min_expected_edge_rate": 0.006,
                "estimated_roundtrip_fee_rate": 0.004,
                "estimated_spread_rate": 0.001,
                "estimated_round_trip_cost_rate": 0.005,
                "edge_allowed": False,
            }
        )

        self.assertIn("EXPECTED_EDGE_BELOW_THRESHOLD", below_threshold["block_reasons"])
        self.assertNotIn("EXPECTED_EDGE_BELOW_FEE_COST", below_threshold["block_reasons"])
        self.assertIn("EXPECTED_EDGE_BELOW_THRESHOLD", below_cost["block_reasons"])
        self.assertIn("EXPECTED_EDGE_BELOW_FEE_COST", below_cost["block_reasons"])

    def test_pass_idle_signal_summary_contains_counts_and_block_reasons(self) -> None:
        diagnostics = [
            _diagnose_signal_decision(
                {
                    "symbol": "BTC",
                    "strategy": "ma_cross",
                    "signal": "HOLD",
                    "expected_edge_rate": 0.001,
                    "min_expected_edge_rate": 0.006,
                    "estimated_roundtrip_fee_rate": 0.005,
                    "estimated_spread_rate": 0.0001,
                    "estimated_round_trip_cost_rate": 0.0051,
                    "edge_allowed": False,
                }
            )
        ]

        summary = _summarize_signal_diagnostics(diagnostics)

        self.assertEqual(summary["candidate_signal_count"], 0)
        self.assertEqual(summary["blocked_signal_count"], 1)
        self.assertTrue(summary["top_block_reasons"])
        reason_codes = {item["code"] for item in summary["top_block_reasons"]}
        self.assertIn("NO_MA_CROSS_SIGNAL", reason_codes)
        self.assertIn("EXPECTED_EDGE_BELOW_THRESHOLD", reason_codes)

    def test_threshold_adjustment_report_does_not_mutate_operating_threshold(self) -> None:
        diagnostics = [
            {
                "estimated_roundtrip_fee_rate": 0.005,
                "estimated_spread_rate": 0.0002,
                "expected_edge_rate": 0.0055,
                "current_threshold": 0.006,
            }
        ]

        report = _threshold_adjustment_report(diagnostics)

        self.assertFalse(report["operating_threshold_changed"])
        self.assertGreaterEqual(report["safe_min_expected_edge_rate_suggestion"], 0.006)
        self.assertGreaterEqual(report["aggressive_min_expected_edge_rate_suggestion"], report["observed_roundtrip_cost_rate"])

    def test_controlled_entry_v2_marks_cost_positive_setup_as_trade_candidate(self) -> None:
        candles = []
        price = 100.0
        for index in range(40):
            if index >= 34:
                price *= 1.0014
            candle_open = price * 0.999
            candles.append(
                {
                    "candle_time_utc": f"2026-06-26T00:{index:02d}:00Z",
                    "opening_price": candle_open,
                    "high_price": price * 1.001,
                    "low_price": candle_open * 0.999,
                    "trade_price": price,
                    "candle_acc_trade_volume": 4 if index >= 37 else 1,
                }
            )

        decision = _controlled_entry_v2_decision(
            "BTC",
            "KRW-BTC",
            candles,
            {"best_bid": 100.10, "best_ask": 100.11},
            6000,
        )
        diagnostic = _diagnose_signal_decision(decision)

        self.assertEqual(decision["strategy"], "controlled_entry_v2")
        self.assertEqual(decision["signal_state"], "TRADE_CANDIDATE")
        self.assertEqual(decision["signal"], "BUY")
        self.assertTrue(decision["edge_allowed"])
        self.assertGreater(decision["expected_edge_after_cost"], 0)
        self.assertGreaterEqual(decision["signal_score"], 60)
        self.assertEqual(diagnostic["signal_state"], "TRADE_CANDIDATE")
        self.assertEqual(diagnostic["trade_candidate_reason"], decision["trade_candidate_reason"])

    def test_controlled_entry_v2_blocks_when_expected_edge_does_not_cover_cost(self) -> None:
        candles = []
        for index in range(40):
            price = 100.0 + (0.002 if index % 2 == 0 else 0.0)
            candles.append(
                {
                    "candle_time_utc": f"2026-06-26T00:{index:02d}:00Z",
                    "opening_price": 100.0,
                    "high_price": 100.01,
                    "low_price": 99.99,
                    "trade_price": price,
                    "candle_acc_trade_volume": 1,
                }
            )

        decision = _controlled_entry_v2_decision(
            "ETH",
            "KRW-ETH",
            candles,
            {"best_bid": 100.0, "best_ask": 100.4},
            6000,
        )

        self.assertNotEqual(decision["signal_state"], "TRADE_CANDIDATE")
        self.assertFalse(decision["edge_allowed"])
        self.assertIn("EXPECTED_EDGE_BELOW_FEE_COST", decision["block_reasons"])

    def test_controlled_entry_v3_marks_5m_cost_positive_breakout_as_trade_candidate(self) -> None:
        candles = []
        price = 100.0
        for index in range(60):
            if index >= 50:
                price *= 1.007
            candle_open = price * 0.996
            candles.append(
                {
                    "candle_time_utc": f"2026-06-26T{index // 12:02d}:{(index % 12) * 5:02d}:00Z",
                    "opening_price": candle_open,
                    "high_price": price * 1.003,
                    "low_price": candle_open * 0.997,
                    "trade_price": price,
                    "candle_acc_trade_volume": 5 if index >= 52 else 1,
                }
            )

        decision = _controlled_entry_v3_decision(
            "BTC",
            "KRW-BTC",
            5,
            candles,
            {"best_bid": 100.10, "best_ask": 100.11},
            6000,
        )
        diagnostic = _diagnose_signal_decision(decision)

        self.assertEqual(decision["strategy"], "controlled_entry_v3")
        self.assertEqual(decision["timeframe"], "5m")
        self.assertEqual(decision["signal_state"], "TRADE_CANDIDATE")
        self.assertEqual(decision["signal"], "BUY")
        self.assertTrue(decision["edge_allowed"])
        self.assertGreater(decision["expected_edge_after_cost"], 0)
        self.assertGreaterEqual(decision["signal_score"], 62)
        self.assertEqual(diagnostic["timeframe"], "5m")
        self.assertEqual(diagnostic["signal_state"], "TRADE_CANDIDATE")
        self.assertEqual(diagnostic["recommended_next_action"], "CONTROLLED_RUN_REQUIRES_USER_APPROVAL")

    def test_controlled_entry_v3_marks_3m_cost_positive_breakout_as_trade_candidate(self) -> None:
        candles = []
        price = 100.0
        for index in range(60):
            if index >= 50:
                price *= 1.006
            candle_open = price * 0.996
            candles.append(
                {
                    "candle_time_utc": f"2026-06-26T{index // 20:02d}:{(index % 20) * 3:02d}:00Z",
                    "opening_price": candle_open,
                    "high_price": price * 1.003,
                    "low_price": candle_open * 0.997,
                    "trade_price": price,
                    "candle_acc_trade_volume": 5 if index >= 52 else 1,
                }
            )

        decision = _controlled_entry_v3_decision(
            "ETH",
            "KRW-ETH",
            3,
            candles,
            {"best_bid": 100.10, "best_ask": 100.11},
            6000,
        )
        diagnostic = _diagnose_signal_decision(decision)
        summary = _summarize_signal_diagnostics([diagnostic])

        self.assertEqual(decision["timeframe"], "3m")
        self.assertEqual(decision["signal_state"], "TRADE_CANDIDATE")
        self.assertEqual(summary["trade_candidate_count_by_timeframe"]["3m"], 1)
        self.assertEqual(summary["latest_signal_by_timeframe"]["3m"]["signal_state"], "TRADE_CANDIDATE")

    def test_controlled_entry_v3_blocks_low_volatility_low_volume_15m_setup(self) -> None:
        candles = []
        for index in range(60):
            price = 100.0 + (0.01 if index % 2 == 0 else 0.0)
            candles.append(
                {
                    "candle_time_utc": f"2026-06-26T{index // 4:02d}:{(index % 4) * 15:02d}:00Z",
                    "opening_price": 100.0,
                    "high_price": 100.02,
                    "low_price": 99.99,
                    "trade_price": price,
                    "candle_acc_trade_volume": 0.1,
                }
            )

        decision = _controlled_entry_v3_decision(
            "ETH",
            "KRW-ETH",
            15,
            candles,
            {"best_bid": 100.0, "best_ask": 100.5},
            6000,
        )

        self.assertEqual(decision["timeframe"], "15m")
        self.assertNotEqual(decision["signal_state"], "TRADE_CANDIDATE")
        self.assertFalse(decision["edge_allowed"])
        self.assertIn("EXPECTED_EDGE_BELOW_FEE_COST", decision["block_reasons"])
        self.assertIn("VOLATILITY_TOO_LOW", decision["block_reasons"])
        self.assertIn("breakout_level", decision)
        self.assertIn("rebound_level", decision)
        self.assertIn("distance_to_breakout_rate", decision)
        self.assertIn("trigger_missing_reason", decision)
        self.assertIn("PRICE_BELOW_BREAKOUT_LEVEL", decision["trigger_missing_reason"])

    def test_best_decision_prefers_allowed_buy_over_higher_hold_edge(self) -> None:
        selected = _select_best_decision(
            [
                {"symbol": "BTC", "strategy": "smart_autonomous", "signal": "HOLD", "expected_edge_rate": 0.02, "edge_allowed": False},
                {
                    "symbol": "ETH",
                    "strategy": "controlled_entry_v3",
                    "timeframe": "15m",
                    "signal": "BUY",
                    "expected_edge_rate": 0.01,
                    "expected_edge_after_cost": 0.003,
                    "signal_score": 80,
                    "edge_allowed": True,
                },
            ]
        )

        self.assertEqual(selected["strategy"], "controlled_entry_v3")
        self.assertEqual(selected["symbol"], "ETH")

    def test_v3_watch_candidate_priority_prefers_eth_15m(self) -> None:
        selected = _select_controlled_entry_v3_watch_candidate(
            [
                {
                    "symbol": "BTC",
                    "timeframe": "5m",
                    "signal": "BUY",
                    "signal_state": "TRADE_CANDIDATE",
                    "edge_allowed": True,
                    "expected_edge_after_cost": 0.02,
                    "signal_score": 99,
                },
                {
                    "symbol": "ETH",
                    "timeframe": "3m",
                    "signal": "BUY",
                    "signal_state": "TRADE_CANDIDATE",
                    "edge_allowed": True,
                    "expected_edge_after_cost": 0.03,
                    "signal_score": 100,
                },
                {
                    "symbol": "ETH",
                    "timeframe": "15m",
                    "signal": "BUY",
                    "signal_state": "TRADE_CANDIDATE",
                    "edge_allowed": True,
                    "expected_edge_after_cost": 0.006,
                    "signal_score": 80,
                },
            ]
        )

        self.assertEqual(selected["symbol"], "ETH")
        self.assertEqual(selected["timeframe"], "15m")

    async def test_entry_v3_watch_executes_once_when_trade_candidate_is_detected(self) -> None:
        broker = AsyncMock()
        broker.list_open_orders.return_value = {"orders": []}
        broker.place_order.side_effect = [
            {"uuid": "C0101000003129835801", "market": "KRW-ETH", "side": "bid"},
            {"uuid": "C0101000003129835802", "market": "KRW-ETH", "side": "ask"},
        ]

        async def reconcile(log: dict, source: str) -> SimpleNamespace:
            side = str(log["side"]).upper()
            order_uuid = str(log["order_uuid"])
            volume = 0.001
            amount = 6000.0 if side == "BUY" else 5988.0
            fee = 3.0 if side == "BUY" else 2.994
            database.update_live_order_log(
                str(log["request_id"]),
                {
                    "status": "FILLED",
                    "risk_result": "CONTROLLED_ENTRY_V3_WATCH_FILLED",
                    "exchange_response_payload": {
                        "uuid": order_uuid,
                        "client_order_id": log["client_order_id"],
                        "market": "KRW-ETH",
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
            "current_epoch_trust_level": "MEDIUM",
            "current_epoch_sanity_passed": True,
            "current_epoch_total_pnl": 0.0,
            "current_epoch_accounting_pending_count": 0,
            "current_epoch_accounting_failed_count": 0,
        }
        gate = {"controlled_auto_live_allowed": True, "controlled_auto_live_blockers": []}
        scan = {
            "evaluated_at_utc": "2026-06-26T00:00:00Z",
            "diagnostics": [],
            "summary": {},
            "priority": ["ETH:15m", "BTC:15m", "ETH:5m", "BTC:5m"],
            "selected_candidate": {
                "symbol": "ETH",
                "market": "KRW-ETH",
                "strategy_name": "controlled_entry_v3",
                "timeframe": "15m",
                "signal_side": "BUY",
                "signal_state": "TRADE_CANDIDATE",
                "expected_edge_after_cost": 0.007,
                "signal_score": 96.0,
                "trade_candidate_reason": "15m_breakout+positive_edge_after_cost",
                "entry_price": 6_000_000.0,
            },
        }
        with (
            patch.dict("os.environ", {"MIN_LIVE_ORDER_KRW": "5000"}, clear=False),
            patch("app.controlled_auto_live.get_live_broker", return_value=broker),
            patch("app.controlled_auto_live._full_auto_live_disabled", return_value=True),
            patch("app.controlled_auto_live._runtime_guards_pass", return_value=True),
            patch("app.controlled_auto_live._current_equity", side_effect=[263_000.0, 262_982.0]),
            patch("app.controlled_auto_live._open_order_count", return_value=0),
            patch("app.controlled_auto_live._scan_controlled_entry_v3_watch", return_value=scan),
            patch("app.controlled_auto_live._orderbook_quote", return_value={"best_ask": 6_000_000.0, "best_bid": 5_988_000.0}),
            patch("app.controlled_auto_live.reconcile_order_log", side_effect=reconcile),
        ):
            result = await run_controlled_entry_v3_watch(
                confirmation=ENTRY_V3_WATCH_CONFIRMATION_PHRASE,
                controlled_gate=gate,
                current_epoch=current_epoch,
                runtime_seconds=900,
                scan_interval_seconds=60,
            )

        self.assertEqual(result["controlled_auto_live_status"], "PASSED_TRADE", result.get("pass_fail_reasons"))
        self.assertEqual(result["run_type"], "CONTROLLED_ENTRY_V3_WATCH")
        self.assertTrue(result["trade_candidate_detected"])
        self.assertEqual(result["selected_symbol"], "ETH")
        self.assertEqual(result["selected_timeframe"], "15m")
        self.assertEqual(result["order_count"], 2)
        self.assertEqual(result["buy_filled_count"], 1)
        self.assertEqual(result["sell_filled_count"], 1)
        self.assertEqual(result["exchange_fill_count"], result["ledger_fill_count"])
        self.assertEqual(result["missing_ledger_fill_count"], 0)
        self.assertEqual(result["duplicate_fill_count"], 0)
        self.assertEqual(result["open_order_count_after"], 0)
        self.assertEqual(result["duplicate_client_order_id_count"], 0)
        self.assertEqual(result["protected_strategy_realized_pnl"], result["net_pnl_after_fee"])
        self.assertEqual(result["protected_strategy_unrealized_pnl"], 0.0)
        self.assertEqual(result["protected_strategy_total_pnl"], result["net_pnl_after_fee"])
        self.assertEqual(result["run_trade_pnl_after_fee"], result["net_pnl_after_fee"])
        self.assertEqual(result["account_session_pnl_delta"], result["current_epoch_pnl_delta"])
        self.assertAlmostEqual(
            result["legacy_holding_valuation_delta"],
            result["account_session_pnl_delta"] - result["protected_strategy_total_pnl"],
            places=6,
        )
        order_logs = database.load_trade_history_logs()
        run_logs = [log for log in order_logs if str(log["request_id"]).startswith(str(result["controlled_run_id"]))]
        run_logs.sort(key=lambda log: ((log.get("order_preview_payload") or {}).get("order_seq") or 0))
        client_ids = [log["client_order_id"] for log in run_logs]
        self.assertEqual(len(client_ids), 2)
        self.assertEqual(len(set(client_ids)), 2)
        self.assertTrue(all(len(client_id) <= 36 for client_id in client_ids))
        self.assertIn("-b-", client_ids[0])
        self.assertIn("-s-", client_ids[1])
        seqs = [((log.get("order_preview_payload") or {}).get("order_seq")) for log in run_logs]
        self.assertEqual(seqs, [1, 2])
        self.assertEqual(broker.place_order.await_count, 2)

    def test_controlled_client_order_id_is_globally_unique_within_exchange_limit(self) -> None:
        run_id = "posloop-20260629T121326-57c2fc-trade-1"
        first = _controlled_client_order_id(
            mode="PROTECTED_FULL_AUTO_LIVE_V1",
            run_id=run_id,
            symbol="ETH",
            side="BUY",
            order_seq=1,
        )
        second = _controlled_client_order_id(
            mode="PROTECTED_FULL_AUTO_LIVE_V1",
            run_id=run_id,
            symbol="ETH",
            side="SELL",
            order_seq=2,
        )
        third = _controlled_client_order_id(
            mode="PROTECTED_FULL_AUTO_LIVE_V1",
            run_id="posloop-20260629T121326-57c2fc-trade-2",
            symbol="ETH",
            side="BUY",
            order_seq=1,
        )
        self.assertEqual(len({first, second, third}), 3)
        self.assertTrue(all(len(item) <= 36 for item in [first, second, third]))
        self.assertIn("-b-", first)
        self.assertIn("-s-", second)
        self.assertIn("-0001-", first)
        self.assertIn("-0002-", second)

    async def test_entry_v3_position_duplicate_client_order_id_stops_before_exchange(self) -> None:
        database.insert_live_order_log(
            {
                "request_id": "existing-request",
                "client_order_id": "dup-client-id",
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "side": "BUY",
                "order_type": "LIMIT",
                "price": 90_000_000,
                "volume": 0.000066,
                "amount_krw": 6000,
                "fee_estimate": 3,
                "risk_result": "ALLOWED",
                "status": "PREVIEWED",
                "order_uuid": "",
                "strategy_name": "controlled_entry_v3",
            }
        )
        broker = AsyncMock()
        broker.list_open_orders.return_value = {"orders": []}
        current_epoch = {
            "current_epoch_exists": True,
            "current_epoch_id": "epoch-controlled",
            "current_epoch_trust_level": "MEDIUM",
            "current_epoch_sanity_passed": True,
            "current_epoch_total_pnl": 0.0,
            "current_epoch_accounting_pending_count": 0,
            "current_epoch_accounting_failed_count": 0,
        }
        gate = {"controlled_auto_live_allowed": True, "controlled_auto_live_blockers": []}
        scan = {
            "evaluated_at_utc": "2026-06-26T00:00:00Z",
            "diagnostics": [],
            "summary": {},
            "priority": ["ETH:15m", "BTC:15m", "ETH:5m", "BTC:5m"],
            "selected_candidate": {
                "symbol": "BTC",
                "market": "KRW-BTC",
                "strategy_name": "controlled_entry_v3",
                "timeframe": "5m",
                "signal_side": "BUY",
                "signal_state": "TRADE_CANDIDATE",
                "expected_edge_after_cost": 0.007,
                "estimated_total_cost_rate": 0.005,
                "signal_score": 82.0,
                "trade_candidate_reason": "5m_pullback_rebound+positive_edge_after_cost",
                "entry_price": 90_909_090.9,
            },
        }
        with (
            patch("app.controlled_auto_live._controlled_client_order_id", return_value="dup-client-id"),
            patch("app.controlled_auto_live.get_live_broker", return_value=broker),
            patch("app.controlled_auto_live._full_auto_live_disabled", return_value=True),
            patch("app.controlled_auto_live._runtime_guards_pass", return_value=True),
            patch("app.controlled_auto_live._current_equity", side_effect=[263_000.0, 263_000.0]),
            patch("app.controlled_auto_live._open_order_count", return_value=0),
            patch("app.controlled_auto_live._scan_controlled_entry_v3_watch", return_value=scan),
            patch("app.controlled_auto_live._orderbook_quote", return_value={"best_ask": 91_000_000.0, "best_bid": 91_000_000.0}),
        ):
            result = await run_controlled_entry_v3_position_run(
                confirmation=ENTRY_V3_POSITION_RUN_CONFIRMATION_PHRASE,
                controlled_gate=gate,
                current_epoch=current_epoch,
                runtime_seconds=900,
                scan_interval_seconds=60,
                max_holding_minutes=10,
            )

        self.assertEqual(result["controlled_auto_live_status"], "STOPPED")
        self.assertIn("DUPLICATE_CLIENT_ORDER_ID_BLOCKED", result["pass_fail_reasons"])
        self.assertEqual(result["duplicate_client_order_id_count"], 1)
        self.assertEqual(result["duplicate_client_order_ids"], ["dup-client-id"])
        self.assertEqual(result["open_order_count_after"], 0)
        self.assertEqual(result["final_runtime_status"], "STOPPED")
        broker.place_order.assert_not_awaited()

    async def test_entry_v3_watch_job_blocks_duplicate_active_run(self) -> None:
        async def fake_run(**kwargs):
            await asyncio.sleep(0.05)
            return {
                "controlled_run_id": kwargs["controlled_run_id"],
                "controlled_auto_live_status": "PASS_IDLE",
                "completed_at_utc": "2026-06-26T00:15:00Z",
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
        with patch("app.controlled_auto_live.run_controlled_entry_v3_watch", side_effect=fake_run):
            first = await start_controlled_entry_v3_watch_job(
                exchange="bithumb",
                symbols=["BTC", "ETH"],
                amount_krw=6000,
                runtime_seconds=900,
                scan_interval_seconds=60,
                confirmation=ENTRY_V3_WATCH_CONFIRMATION_PHRASE,
                controlled_gate=gate,
                current_epoch=current_epoch,
            )
            second = await start_controlled_entry_v3_watch_job(
                exchange="bithumb",
                symbols=["BTC", "ETH"],
                amount_krw=6000,
                runtime_seconds=900,
                scan_interval_seconds=60,
                confirmation=ENTRY_V3_WATCH_CONFIRMATION_PHRASE,
                controlled_gate=gate,
                current_epoch=current_epoch,
            )
            await asyncio.sleep(0.1)

        self.assertEqual(first["status"], "STARTING")
        self.assertFalse(second["ok"])
        self.assertEqual(second["status"], "ABORTED")
        final = _controlled_jobs[first["controlled_run_id"]]
        self.assertEqual(final["status"], "PASS_IDLE")

    async def test_entry_v3_position_run_buys_holds_and_exits_on_take_profit(self) -> None:
        broker = AsyncMock()
        broker.list_open_orders.return_value = {"orders": []}
        broker.place_order.side_effect = [
            {"uuid": "C0101000003129835901", "market": "KRW-BTC", "side": "bid"},
            {"uuid": "C0101000003129835902", "market": "KRW-BTC", "side": "ask"},
        ]

        async def reconcile(log: dict, source: str) -> SimpleNamespace:
            side = str(log["side"]).upper()
            order_uuid = str(log["order_uuid"])
            volume = 0.000066
            amount = 6000.0 if side == "BUY" else 6050.0
            fee = 3.0 if side == "BUY" else 3.025
            database.update_live_order_log(
                str(log["request_id"]),
                {
                    "status": "FILLED",
                    "risk_result": "CONTROLLED_ENTRY_V3_POSITION_FILLED",
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
            "current_epoch_trust_level": "MEDIUM",
            "current_epoch_sanity_passed": True,
            "current_epoch_total_pnl": 0.0,
            "current_epoch_accounting_pending_count": 0,
            "current_epoch_accounting_failed_count": 0,
        }
        gate = {"controlled_auto_live_allowed": True, "controlled_auto_live_blockers": []}
        scan = {
            "evaluated_at_utc": "2026-06-26T00:00:00Z",
            "diagnostics": [],
            "summary": {},
            "priority": ["ETH:15m", "BTC:15m", "ETH:5m", "BTC:5m"],
            "selected_candidate": {
                "symbol": "BTC",
                "market": "KRW-BTC",
                "strategy_name": "controlled_entry_v3",
                "timeframe": "5m",
                "signal_side": "BUY",
                "signal_state": "TRADE_CANDIDATE",
                "expected_edge_after_cost": 0.007,
                "estimated_total_cost_rate": 0.005,
                "signal_score": 82.0,
                "trade_candidate_reason": "5m_pullback_rebound+positive_edge_after_cost",
                "entry_price": 90_909_090.9,
            },
        }
        with (
            patch.dict("os.environ", {"MIN_LIVE_ORDER_KRW": "5000"}, clear=False),
            patch("app.controlled_auto_live.POSITION_RUN_MIN_HOLD_SECONDS", 0),
            patch("app.controlled_auto_live.get_live_broker", return_value=broker),
            patch("app.controlled_auto_live._full_auto_live_disabled", return_value=True),
            patch("app.controlled_auto_live._runtime_guards_pass", return_value=True),
            patch("app.controlled_auto_live._current_equity", side_effect=[263_000.0, 263_044.975]),
            patch("app.controlled_auto_live._open_order_count", return_value=0),
            patch("app.controlled_auto_live._scan_controlled_entry_v3_watch", return_value=scan),
            patch("app.controlled_auto_live._orderbook_quote", return_value={"best_ask": 91_000_000.0, "best_bid": 91_666_666.7}),
            patch("app.controlled_auto_live.reconcile_order_log", side_effect=reconcile),
        ):
            result = await run_controlled_entry_v3_position_run(
                confirmation=ENTRY_V3_POSITION_RUN_CONFIRMATION_PHRASE,
                controlled_gate=gate,
                current_epoch=current_epoch,
                runtime_seconds=900,
                scan_interval_seconds=60,
                max_holding_minutes=10,
            )

        self.assertEqual(result["controlled_auto_live_status"], "PASSED_POSITION_TRADE", result.get("pass_fail_reasons"))
        self.assertEqual(result["run_type"], "CONTROLLED_ENTRY_V3_POSITION_RUN")
        self.assertEqual(result["selected_symbol"], "BTC")
        self.assertEqual(result["selected_timeframe"], "5m")
        self.assertEqual(result["order_count"], 2)
        self.assertEqual(result["buy_filled_count"], 1)
        self.assertEqual(result["sell_filled_count"], 1)
        self.assertEqual(result["exit_reason"], "TAKE_PROFIT")
        self.assertGreater(result["entry_price"], 0)
        self.assertGreater(result["exit_price"], 0)
        self.assertEqual(result["exchange_fill_count"], result["ledger_fill_count"])
        self.assertEqual(result["missing_ledger_fill_count"], 0)
        self.assertEqual(result["duplicate_fill_count"], 0)
        self.assertEqual(result["open_order_count_after"], 0)
        self.assertEqual(broker.place_order.await_count, 2)

    async def test_entry_v3_position_run_job_blocks_duplicate_active_run(self) -> None:
        async def fake_run(**kwargs):
            await asyncio.sleep(0.05)
            return {
                "controlled_run_id": kwargs["controlled_run_id"],
                "controlled_auto_live_status": "PASS_IDLE",
                "completed_at_utc": "2026-06-26T00:15:00Z",
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
        with patch("app.controlled_auto_live.run_controlled_entry_v3_position_run", side_effect=fake_run):
            first = await start_controlled_entry_v3_position_run_job(
                exchange="bithumb",
                symbols=["BTC", "ETH"],
                amount_krw=6000,
                runtime_seconds=900,
                scan_interval_seconds=60,
                max_holding_minutes=10,
                confirmation=ENTRY_V3_POSITION_RUN_CONFIRMATION_PHRASE,
                controlled_gate=gate,
                current_epoch=current_epoch,
            )
            second = await start_controlled_entry_v3_position_run_job(
                exchange="bithumb",
                symbols=["BTC", "ETH"],
                amount_krw=6000,
                runtime_seconds=900,
                scan_interval_seconds=60,
                max_holding_minutes=10,
                confirmation=ENTRY_V3_POSITION_RUN_CONFIRMATION_PHRASE,
                controlled_gate=gate,
                current_epoch=current_epoch,
            )
            await asyncio.sleep(0.1)

        self.assertEqual(first["status"], "STARTING")
        self.assertFalse(second["ok"])
        self.assertEqual(second["status"], "ABORTED")
        final = _controlled_jobs[first["controlled_run_id"]]
        self.assertEqual(final["status"], "PASS_IDLE")

    async def test_controlled_position_loop_reports_profitable_and_technical_results_separately(self) -> None:
        current_epoch = {
            "current_epoch_exists": True,
            "current_epoch_id": "epoch-controlled",
            "current_epoch_trust_level": "MEDIUM",
            "current_epoch_sanity_passed": True,
            "current_epoch_total_pnl": 0.0,
            "current_epoch_accounting_pending_count": 0,
            "current_epoch_accounting_failed_count": 0,
        }
        gate = {"controlled_auto_live_allowed": True, "controlled_auto_live_blockers": []}

        async def fake_position_run(**kwargs):
            return {
                "controlled_auto_live_status": "PASSED_POSITION_TRADE",
                "selected_symbol": "BTC",
                "selected_timeframe": "15m",
                "order_count": 2,
                "buy_filled_count": 1,
                "sell_filled_count": 1,
                "gross_pnl": 40.0,
                "net_pnl_after_fee": 10.0,
                "run_realized_pnl": 10.0,
                "total_fee": 30.0,
                "exit_reason": "TAKE_PROFIT",
                "exchange_fill_count": 2,
                "ledger_fill_count": 2,
                "missing_ledger_fill_count": 0,
                "duplicate_fill_count": 0,
                "fee_diff": 0.0,
                "equity_after": 263010.0,
                "pass_fail_reasons": [],
            }

        with (
            patch("app.controlled_auto_live._full_auto_live_disabled", return_value=True),
            patch("app.controlled_auto_live._runtime_guards_pass", return_value=True),
            patch("app.controlled_auto_live._current_equity", side_effect=[263000.0, 263010.0]),
            patch("app.controlled_auto_live._open_order_count", return_value=0),
            patch("app.controlled_auto_live.run_controlled_entry_v3_position_run", side_effect=fake_position_run),
        ):
            result = await run_controlled_position_loop(
                confirmation=CONTROLLED_POSITION_LOOP_CONFIRMATION_PHRASE,
                controlled_gate=gate,
                current_epoch=current_epoch,
                runtime_seconds=900,
                scan_interval_seconds=60,
                max_position_trades=1,
            )

        self.assertEqual(result["controlled_auto_live_status"], "PASSED_PROFITABLE_POSITION")
        self.assertEqual(result["technical_result"], "PASSED")
        self.assertEqual(result["profitability_result"], "PROFITABLE")
        self.assertEqual(result["trade_count"], 1)
        self.assertEqual(result["net_pnl_after_fee"], 10.0)
        self.assertEqual(result["exit_reason_counts"], {"TAKE_PROFIT": 1})
        self.assertEqual(result["profitable_trade_count"], 1)
        self.assertEqual(result["losing_trade_count"], 0)
        self.assertEqual(result["exchange_fill_count"], result["ledger_fill_count"])
        self.assertEqual(result["open_order_count_after"], 0)

    async def test_controlled_position_loop_passes_technical_when_pnl_is_not_positive(self) -> None:
        current_epoch = {
            "current_epoch_exists": True,
            "current_epoch_id": "epoch-controlled",
            "current_epoch_trust_level": "MEDIUM",
            "current_epoch_sanity_passed": True,
            "current_epoch_total_pnl": 0.0,
            "current_epoch_accounting_pending_count": 0,
            "current_epoch_accounting_failed_count": 0,
        }
        gate = {"controlled_auto_live_allowed": True, "controlled_auto_live_blockers": []}

        async def fake_position_run(**kwargs):
            return {
                "controlled_auto_live_status": "PASSED_POSITION_TRADE",
                "selected_symbol": "ETH",
                "selected_timeframe": "15m",
                "order_count": 2,
                "buy_filled_count": 1,
                "sell_filled_count": 1,
                "gross_pnl": 2.0,
                "net_pnl_after_fee": -28.0,
                "run_realized_pnl": -28.0,
                "total_fee": 30.0,
                "exit_reason": "TIME_STOP",
                "exchange_fill_count": 2,
                "ledger_fill_count": 2,
                "missing_ledger_fill_count": 0,
                "duplicate_fill_count": 0,
                "fee_diff": 0.0,
                "equity_after": 262972.0,
                "pass_fail_reasons": [],
            }

        with (
            patch("app.controlled_auto_live._full_auto_live_disabled", return_value=True),
            patch("app.controlled_auto_live._runtime_guards_pass", return_value=True),
            patch("app.controlled_auto_live._current_equity", side_effect=[263000.0, 264000.0]),
            patch("app.controlled_auto_live._open_order_count", return_value=0),
            patch("app.controlled_auto_live.run_controlled_entry_v3_position_run", side_effect=fake_position_run),
        ):
            result = await run_controlled_position_loop(
                confirmation=CONTROLLED_POSITION_LOOP_CONFIRMATION_PHRASE,
                controlled_gate=gate,
                current_epoch=current_epoch,
                runtime_seconds=900,
                scan_interval_seconds=60,
                max_position_trades=1,
            )

        self.assertEqual(result["controlled_auto_live_status"], "PASSED_TECHNICAL_POSITION")
        self.assertEqual(result["technical_result"], "PASSED")
        self.assertEqual(result["profitability_result"], "NOT_PROFITABLE")
        self.assertEqual(result["exit_reason_counts"], {"TIME_STOP": 1})
        self.assertEqual(result["time_stop_count"], 1)
        self.assertEqual(result["profitable_trade_count"], 0)
        self.assertEqual(result["losing_trade_count"], 1)
        self.assertEqual(result["protected_strategy_total_pnl"], -28.0)
        self.assertEqual(result["run_trade_pnl_after_fee"], -28.0)
        self.assertEqual(result["account_session_pnl_delta"], 1000.0)
        self.assertEqual(result["current_epoch_pnl_delta"], 1000.0)
        self.assertEqual(result["legacy_holding_valuation_delta"], 1028.0)

    async def test_controlled_position_loop_job_blocks_duplicate_active_run(self) -> None:
        async def fake_run(**kwargs):
            await asyncio.sleep(0.05)
            return {
                "controlled_run_id": kwargs["loop_run_id"],
                "controlled_auto_live_status": "PASS_IDLE",
                "completed_at_utc": "2026-06-26T00:30:00Z",
                "trade_count": 0,
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
        with patch("app.controlled_auto_live.run_controlled_position_loop", side_effect=fake_run):
            first = await start_controlled_position_loop_job(
                exchange="bithumb",
                symbols=["BTC", "ETH"],
                amount_krw=6000,
                runtime_seconds=1800,
                scan_interval_seconds=60,
                max_holding_minutes=10,
                max_position_trades=3,
                confirmation=CONTROLLED_POSITION_LOOP_CONFIRMATION_PHRASE,
                controlled_gate=gate,
                current_epoch=current_epoch,
            )
            second = await start_controlled_position_loop_job(
                exchange="bithumb",
                symbols=["BTC", "ETH"],
                amount_krw=6000,
                runtime_seconds=1800,
                scan_interval_seconds=60,
                max_holding_minutes=10,
                max_position_trades=3,
                confirmation=CONTROLLED_POSITION_LOOP_CONFIRMATION_PHRASE,
                controlled_gate=gate,
                current_epoch=current_epoch,
            )
            await asyncio.sleep(0.1)

        self.assertEqual(first["status"], "STARTING")
        self.assertFalse(second["ok"])
        self.assertEqual(second["status"], "ABORTED")
        final = _controlled_jobs[first["controlled_run_id"]]
        self.assertEqual(final["status"], "PASS_IDLE")

    def test_protected_full_auto_gate_allows_pass_idle_position_loop_but_not_full_auto(self) -> None:
        database.insert_smoke_test_run(
            {
                "smoke_test_id": "smoke-protected-pass",
                "exchange_name": "bithumb",
                "symbol": "BTC",
                "market": "KRW-BTC",
                "status": "PASSED_AFTER_RECALC",
                "started_at_utc": "2026-06-26T00:00:00Z",
                "completed_at_utc": "2026-06-26T00:01:00Z",
                "max_notional_krw": 6000,
                "report": {
                    "duplicate_fill_count": 0,
                    "fee_diff": 0.0,
                    "equity_diff_after": 0.0,
                    "current_epoch_accounting_pending_count": 0,
                    "current_epoch_accounting_failed_count": 0,
                    "final_runtime_status": "STOPPED",
                },
            }
        )
        persist_controlled_run_report(
            {
                "controlled_run_id": "posloop-pass-idle",
                "loop_run_id": "posloop-pass-idle",
                "run_type": "CONTROLLED_POSITION_LOOP",
                "controlled_auto_live_status": "PASS_IDLE",
                "technical_result": "PASS_IDLE",
                "profitability_result": "NO_TRADE",
                "started_at_utc": "2026-06-29T07:37:46Z",
                "completed_at_utc": "2026-06-29T08:07:48Z",
                "trade_count": 0,
                "order_count": 0,
                "exchange_fill_count": 0,
                "ledger_fill_count": 0,
                "missing_ledger_fill_count": 0,
                "duplicate_fill_count": 0,
                "fee_diff": 0.0,
                "equity_diff_after": 0.0,
                "open_order_count_after": 0,
                "final_runtime_status": "STOPPED",
            }
        )
        current_epoch = {
            "current_epoch_exists": True,
            "current_epoch_id": "epoch-controlled",
            "current_epoch_trust_level": "MEDIUM",
            "current_epoch_sanity_passed": True,
            "current_epoch_current_equity": 260_000.0,
            "current_epoch_total_pnl": 0.0,
            "current_epoch_accounting_pending_count": 0,
            "current_epoch_accounting_failed_count": 0,
        }
        preflight = {
            "smoke_test_blockers": [],
            "open_order_audit_summary": {
                "exchange_open_order_count": 0,
                "current_epoch_open_order_count": 0,
                "unknown_open_order_count": 0,
            },
        }
        with (
            patch("app.accounting_epoch.is_emergency_stopped", return_value=False),
            patch("app.controlled_auto_live.is_emergency_stopped", return_value=False),
            patch("app.controlled_auto_live.LiveTradingConfig.for_exchange", return_value=SimpleNamespace(api_key_loaded=True, live_trading_enabled=True)),
        ):
            gate = controlled_auto_live_gate(current_epoch, preflight, exchange="bithumb")

        self.assertTrue(gate["protected_full_auto_live_allowed"], gate["protected_full_auto_live_blockers"])
        self.assertEqual(gate["protected_full_auto_live_blockers"], [])
        self.assertEqual(gate["final_controlled_position_loop_result"], "PASS_IDLE")
        self.assertEqual(gate["protected_full_auto_next_action"], "USER_CONFIRM_PROTECTED_FULL_AUTO_START")
        self.assertFalse(gate["full_auto_live_allowed"])
        config = gate["protected_full_auto_live_config"]
        self.assertEqual(config["allowed_symbols"], ["BTC", "ETH"])
        self.assertEqual(config["allowed_strategies"], ["controlled_entry_v3"])
        self.assertEqual(config["max_notional_per_order_krw"], 6000)
        self.assertEqual(config["max_open_positions"], 1)
        self.assertEqual(config["max_daily_trades"], 20)
        self.assertEqual(config["max_consecutive_losses"], 2)
        self.assertEqual(config["daily_max_loss_krw"], 1000.0)
        self.assertFalse(config["averaging_down_allowed"])
        self.assertFalse(config["reentry_allowed"])

    def test_protected_full_auto_ignores_legacy_holdings_for_slot_count(self) -> None:
        database.insert_smoke_test_run(
            {
                "smoke_test_id": "smoke-protected-legacy-slots",
                "exchange_name": "bithumb",
                "symbol": "BTC",
                "market": "KRW-BTC",
                "status": "PASSED_AFTER_RECALC",
                "started_at_utc": "2026-06-26T00:00:00Z",
                "completed_at_utc": "2026-06-26T00:01:00Z",
                "max_notional_krw": 6000,
                "report": {
                    "duplicate_fill_count": 0,
                    "fee_diff": 0.0,
                    "equity_diff_after": 0.0,
                    "current_epoch_accounting_pending_count": 0,
                    "current_epoch_accounting_failed_count": 0,
                    "final_runtime_status": "STOPPED",
                },
            }
        )
        persist_controlled_run_report(
            {
                "controlled_run_id": "posloop-legacy-pass",
                "run_type": "CONTROLLED_POSITION_LOOP",
                "controlled_auto_live_status": "PASS_IDLE",
                "technical_result": "PASS_IDLE",
                "profitability_result": "NO_TRADE",
                "started_at_utc": "2026-06-29T07:37:46Z",
                "completed_at_utc": "2026-06-29T08:07:48Z",
                "trade_count": 0,
                "order_count": 0,
                "exchange_fill_count": 0,
                "ledger_fill_count": 0,
                "missing_ledger_fill_count": 0,
                "duplicate_fill_count": 0,
                "fee_diff": 0.0,
                "equity_diff_after": 0.0,
                "open_order_count_after": 0,
                "final_runtime_status": "STOPPED",
            }
        )
        for market in ["KRW-RE", "KRW-STRAX", "KRW-ETH", "KRW-ID", "KRW-XLM"]:
            candidate_id = database.save_candidate_strategy(candidate_payload(market=market, strategy="ma_cross"))
            session_id = database.create_live_strategy_session(
                {
                    "exchange": "bithumb",
                    "market": market,
                    "candidate_strategy_id": candidate_id,
                    "strategy_name": "ma_cross",
                    "strategy_parameters": {},
                    "status": "STOPPED",
                    "auto_enabled": False,
                    "initial_balance_krw": 0,
                    "max_order_krw": 100000,
                    "max_orders_per_day": 1,
                }
            )
            database.create_live_position(
                {
                    "session_id": session_id,
                    "exchange": "bithumb",
                    "market": market,
                    "candidate_strategy_id": candidate_id,
                    "strategy_name": "ma_cross",
                    "status": "OPEN",
                    "entry_order_uuid": f"legacy-{market}",
                    "entry_price": 1000,
                    "entry_volume": 1,
                    "entry_amount_krw": 1000,
                    "current_price": 1000,
                    "unrealized_pnl": 0,
                    "realized_pnl": 0,
                    "stop_loss_price": 0,
                    "take_profit_price": 0,
                    "opened_at": "2026-06-25T00:00:00Z",
                }
            )

        current_epoch = {
            "current_epoch_exists": True,
            "current_epoch_id": "epoch-controlled",
            "current_epoch_trust_level": "MEDIUM",
            "current_epoch_sanity_passed": True,
            "current_epoch_current_equity": 260_000.0,
            "current_epoch_total_pnl": 0.0,
            "current_epoch_accounting_pending_count": 0,
            "current_epoch_accounting_failed_count": 0,
        }
        preflight = {
            "smoke_test_blockers": [],
            "open_order_audit_summary": {
                "exchange_open_order_count": 0,
                "current_epoch_open_order_count": 0,
                "unknown_open_order_count": 0,
            },
        }
        with (
            patch("app.accounting_epoch.is_emergency_stopped", return_value=False),
            patch("app.controlled_auto_live.is_emergency_stopped", return_value=False),
            patch("app.controlled_auto_live.LiveTradingConfig.for_exchange", return_value=SimpleNamespace(api_key_loaded=True, live_trading_enabled=True)),
        ):
            gate = controlled_auto_live_gate(current_epoch, preflight, exchange="bithumb")

        self.assertTrue(gate["protected_full_auto_live_allowed"], gate["protected_full_auto_live_blockers"])
        self.assertEqual(gate["total_open_position_count"], 5)
        self.assertEqual(gate["legacy_open_position_count"], 5)
        self.assertEqual(gate["protected_open_position_count"], 0)
        self.assertEqual(gate["protected_empty_slot_count"], 1)
        self.assertFalse(gate["allocator_blocked_by_legacy_positions"])
        self.assertFalse(gate["allocator_blocked_by_protected_positions"])
        classifications = {row["market"]: row["classification"] for row in gate["open_position_classifications"]}
        self.assertEqual(classifications["KRW-RE"], "LEGACY_HOLDING")
        self.assertEqual(classifications["KRW-STRAX"], "LEGACY_HOLDING")
        self.assertEqual(classifications["KRW-ID"], "LEGACY_HOLDING")
        self.assertEqual(classifications["KRW-XLM"], "LEGACY_HOLDING")
        self.assertEqual(classifications["KRW-ETH"], "LEGACY_HOLDING")

    def test_protected_position_scope_counts_current_session_position_only(self) -> None:
        candidate_id = database.save_candidate_strategy(candidate_payload(market="KRW-BTC"))
        session_id = database.create_live_strategy_session(
            {
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": candidate_id,
                "strategy_name": "controlled_entry_v3",
                "strategy_parameters": {},
                "status": "RUNNING",
                "auto_enabled": True,
                "initial_balance_krw": 0,
                "max_order_krw": 6000,
                "max_orders_per_day": 1,
            }
        )
        position_id = database.create_live_position(
            {
                "session_id": session_id,
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "candidate_strategy_id": candidate_id,
                "strategy_name": "controlled_entry_v3",
                "status": "OPEN",
                "entry_order_uuid": "protected-entry-uuid",
                "entry_price": 100000000,
                "entry_volume": 0.00006,
                "entry_amount_krw": 6000,
                "current_price": 100000000,
                "unrealized_pnl": 0,
                "realized_pnl": 0,
                "stop_loss_price": 0,
                "take_profit_price": 0,
                "opened_at": "2026-06-29T12:01:00Z",
            }
        )
        database.insert_live_order_log(
            {
                "request_id": "posloop-current-trade-1-buy-1",
                "session_id": session_id,
                "candidate_strategy_id": candidate_id,
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "side": "BUY",
                "order_type": "LIMIT",
                "price": 100000000,
                "volume": 0.00006,
                "amount_krw": 6000,
                "fee_estimate": 15,
                "risk_result": "CONTROLLED_ENTRY_V3_POSITION_SUBMITTED",
                "order_preview_payload": {},
                "exchange_request_payload_masked": {},
                "exchange_response_payload": {},
                "status": "FILLED",
                "order_uuid": "protected-entry-uuid",
                "position_id": position_id,
                "order_purpose": "CONTROLLED_ENTRY_V3_POSITION_RUN",
                "strategy_name": "controlled_entry_v3",
                "signal_reason": "test",
            }
        )

        status = protected_position_scope_status(
            exchange="bithumb",
            protected_session_id="posloop-current",
            protected_session_started_at_utc="2026-06-29T12:00:00Z",
        )

        self.assertEqual(status["total_open_position_count"], 1)
        self.assertEqual(status["legacy_open_position_count"], 0)
        self.assertEqual(status["protected_open_position_count"], 1)
        self.assertEqual(status["protected_empty_slot_count"], 0)
        self.assertTrue(status["allocator_blocked_by_protected_positions"])

    def test_protected_full_auto_uses_report_status_when_persisted_status_is_stale(self) -> None:
        database.insert_smoke_test_run(
            {
                "smoke_test_id": "smoke-protected-stale-status",
                "exchange_name": "bithumb",
                "symbol": "BTC",
                "market": "KRW-BTC",
                "status": "PASSED_AFTER_RECALC",
                "started_at_utc": "2026-06-26T00:00:00Z",
                "completed_at_utc": "2026-06-26T00:01:00Z",
                "max_notional_krw": 6000,
                "report": {
                    "duplicate_fill_count": 0,
                    "fee_diff": 0.0,
                    "equity_diff_after": 0.0,
                    "current_epoch_accounting_pending_count": 0,
                    "current_epoch_accounting_failed_count": 0,
                    "final_runtime_status": "STOPPED",
                },
            }
        )
        persist_controlled_run_report(
            {
                "controlled_run_id": "posloop-stale-status",
                "loop_run_id": "posloop-stale-status",
                "run_type": "CONTROLLED_POSITION_LOOP",
                "controlled_auto_live_status": "PASS_IDLE",
                "technical_result": "PASS_IDLE",
                "profitability_result": "NO_TRADE",
                "started_at_utc": "2026-06-29T07:37:46Z",
                "completed_at_utc": "2026-06-29T08:07:48Z",
                "trade_count": 0,
                "order_count": 0,
                "exchange_fill_count": 0,
                "ledger_fill_count": 0,
                "missing_ledger_fill_count": 0,
                "duplicate_fill_count": 0,
                "fee_diff": 0.0,
                "equity_diff_after": 0.0,
                "current_epoch_accounting_pending_count": 0,
                "current_epoch_accounting_failed_count": 0,
                "open_order_count_after": 0,
                "final_runtime_status": "STOPPED",
            }
        )
        with database.get_connection() as conn:
            conn.execute("UPDATE controlled_run_reports SET status = 'STOPPED' WHERE run_id = 'posloop-stale-status'")
        persist_controlled_run_report(
            {
                "controlled_run_id": "posloop-stop-requested",
                "loop_run_id": "posloop-stop-requested",
                "run_type": "CONTROLLED_POSITION_LOOP",
                "controlled_auto_live_status": "STOPPED",
                "technical_result": "FAILED",
                "profitability_result": "NOT_EVALUATED",
                "started_at_utc": "2026-06-29T09:18:46Z",
                "completed_at_utc": "2026-06-29T09:22:48Z",
                "pass_fail_reasons": ["CONTROLLED_ENTRY_V3_POSITION_STOP_REQUESTED"],
                "trade_count": 0,
                "order_count": 0,
                "exchange_fill_count": 0,
                "ledger_fill_count": 0,
                "missing_ledger_fill_count": 0,
                "duplicate_fill_count": 0,
                "fee_diff": 0.0,
                "equity_diff_after": 0.0,
                "current_epoch_accounting_pending_count": 0,
                "current_epoch_accounting_failed_count": 0,
                "open_order_count_after": 0,
                "final_runtime_status": "STOPPED",
            }
        )
        current_epoch = {
            "current_epoch_exists": True,
            "current_epoch_id": "epoch-controlled",
            "current_epoch_trust_level": "MEDIUM",
            "current_epoch_sanity_passed": True,
            "current_epoch_current_equity": 260_000.0,
            "current_epoch_total_pnl": 0.0,
            "current_epoch_accounting_pending_count": 0,
            "current_epoch_accounting_failed_count": 0,
        }
        preflight = {
            "smoke_test_blockers": [],
            "open_order_audit_summary": {
                "exchange_open_order_count": 0,
                "current_epoch_open_order_count": 0,
                "unknown_open_order_count": 0,
            },
        }
        with (
            patch("app.accounting_epoch.is_emergency_stopped", return_value=False),
            patch("app.controlled_auto_live.is_emergency_stopped", return_value=False),
            patch("app.controlled_auto_live.LiveTradingConfig.for_exchange", return_value=SimpleNamespace(api_key_loaded=True, live_trading_enabled=True)),
        ):
            gate = controlled_auto_live_gate(current_epoch, preflight, exchange="bithumb")

        self.assertEqual(gate["final_controlled_position_loop_result"], "PASS_IDLE")
        self.assertTrue(gate["protected_session_start_allowed"], gate["protected_full_auto_live_blockers"])
        self.assertTrue(gate["protected_full_auto_live_allowed"], gate["protected_full_auto_live_blockers"])
        self.assertEqual(gate["protected_full_auto_live_blockers"], [])
        self.assertEqual(gate["last_controlled_position_loop_run"]["run_id"], "posloop-stale-status")
        self.assertEqual(gate["last_controlled_position_loop_run"]["persisted_status"], "STOPPED")

    def test_protected_full_auto_blocks_pass_idle_with_loop_safety_error(self) -> None:
        database.insert_smoke_test_run(
            {
                "smoke_test_id": "smoke-protected-loop-error",
                "exchange_name": "bithumb",
                "symbol": "BTC",
                "market": "KRW-BTC",
                "status": "PASSED_AFTER_RECALC",
                "started_at_utc": "2026-06-26T00:00:00Z",
                "completed_at_utc": "2026-06-26T00:01:00Z",
                "max_notional_krw": 6000,
                "report": {
                    "duplicate_fill_count": 0,
                    "fee_diff": 0.0,
                    "equity_diff_after": 0.0,
                    "current_epoch_accounting_pending_count": 0,
                    "current_epoch_accounting_failed_count": 0,
                    "final_runtime_status": "STOPPED",
                },
            }
        )
        persist_controlled_run_report(
            {
                "controlled_run_id": "posloop-pass-idle-missing-fill",
                "loop_run_id": "posloop-pass-idle-missing-fill",
                "run_type": "CONTROLLED_POSITION_LOOP",
                "controlled_auto_live_status": "PASS_IDLE",
                "technical_result": "PASS_IDLE",
                "profitability_result": "NO_TRADE",
                "started_at_utc": "2026-06-29T07:37:46Z",
                "completed_at_utc": "2026-06-29T08:07:48Z",
                "trade_count": 0,
                "order_count": 0,
                "exchange_fill_count": 1,
                "ledger_fill_count": 0,
                "missing_ledger_fill_count": 1,
                "duplicate_fill_count": 0,
                "fee_diff": 0.0,
                "equity_diff_after": 0.0,
                "current_epoch_accounting_pending_count": 0,
                "current_epoch_accounting_failed_count": 0,
                "open_order_count_after": 0,
                "final_runtime_status": "STOPPED",
            }
        )
        current_epoch = {
            "current_epoch_exists": True,
            "current_epoch_id": "epoch-controlled",
            "current_epoch_trust_level": "MEDIUM",
            "current_epoch_sanity_passed": True,
            "current_epoch_current_equity": 260_000.0,
            "current_epoch_total_pnl": 0.0,
            "current_epoch_accounting_pending_count": 0,
            "current_epoch_accounting_failed_count": 0,
        }
        preflight = {
            "smoke_test_blockers": [],
            "open_order_audit_summary": {
                "exchange_open_order_count": 0,
                "current_epoch_open_order_count": 0,
                "unknown_open_order_count": 0,
            },
        }
        with (
            patch("app.accounting_epoch.is_emergency_stopped", return_value=False),
            patch("app.controlled_auto_live.is_emergency_stopped", return_value=False),
            patch("app.controlled_auto_live.LiveTradingConfig.for_exchange", return_value=SimpleNamespace(api_key_loaded=True, live_trading_enabled=True)),
        ):
            gate = controlled_auto_live_gate(current_epoch, preflight, exchange="bithumb")

        self.assertEqual(gate["final_controlled_position_loop_result"], "PASS_IDLE")
        self.assertFalse(gate["protected_full_auto_live_allowed"])
        self.assertIn(
            "CONTROLLED_POSITION_LOOP_MISSING_LEDGER_FILL",
            [item["code"] for item in gate["protected_full_auto_live_blockers"]],
        )

    def test_protected_full_auto_allows_resolved_duplicate_client_order_failure_as_warning(self) -> None:
        database.insert_smoke_test_run(
            {
                "smoke_test_id": "smoke-protected-resolved-duplicate-client-id",
                "exchange_name": "bithumb",
                "symbol": "BTC",
                "market": "KRW-BTC",
                "status": "PASSED_AFTER_RECALC",
                "started_at_utc": "2026-06-26T00:00:00Z",
                "completed_at_utc": "2026-06-26T00:01:00Z",
                "max_notional_krw": 6000,
                "report": {
                    "duplicate_fill_count": 0,
                    "fee_diff": 0.0,
                    "equity_diff_after": 0.0,
                    "current_epoch_accounting_pending_count": 0,
                    "current_epoch_accounting_failed_count": 0,
                    "final_runtime_status": "STOPPED",
                },
            }
        )
        persist_controlled_run_report(
            {
                "controlled_run_id": "posloop-20260629T121326-57c2fc",
                "loop_run_id": "posloop-20260629T121326-57c2fc",
                "run_type": "CONTROLLED_POSITION_LOOP",
                "controlled_auto_live_status": "STOPPED",
                "technical_result": "FAILED",
                "profitability_result": "NOT_EVALUATED",
                "started_at_utc": "2026-06-29T12:13:26Z",
                "completed_at_utc": "2026-06-29T12:23:30Z",
                "pass_fail_reasons": ["CONTROLLED_ENTRY_V3_POSITION_EXCEPTION:ValueError:DUPLICATE_CLIENT_ORDER_ID"],
                "trade_count": 1,
                "order_count": 2,
                "exchange_fill_count": 2,
                "ledger_fill_count": 2,
                "missing_ledger_fill_count": 0,
                "duplicate_fill_count": 0,
                "fee_diff": 0.0,
                "equity_diff_after": 0.0,
                "current_epoch_accounting_pending_count": 0,
                "current_epoch_accounting_failed_count": 0,
                "open_order_count_after": 0,
                "final_runtime_status": "STOPPED",
            }
        )
        current_epoch = {
            "current_epoch_exists": True,
            "current_epoch_id": "epoch-controlled",
            "current_epoch_trust_level": "MEDIUM",
            "current_epoch_sanity_passed": True,
            "current_epoch_current_equity": 260_000.0,
            "current_epoch_total_pnl": 0.0,
            "current_epoch_accounting_pending_count": 0,
            "current_epoch_accounting_failed_count": 0,
        }
        preflight = {
            "smoke_test_blockers": [],
            "open_order_audit_summary": {
                "exchange_open_order_count": 0,
                "current_epoch_open_order_count": 0,
                "unknown_open_order_count": 0,
            },
        }
        with (
            patch("app.accounting_epoch.is_emergency_stopped", return_value=False),
            patch("app.controlled_auto_live.is_emergency_stopped", return_value=False),
            patch("app.controlled_auto_live.LiveTradingConfig.for_exchange", return_value=SimpleNamespace(api_key_loaded=True, live_trading_enabled=True)),
        ):
            blocked_gate = controlled_auto_live_gate(current_epoch, preflight, exchange="bithumb")

        self.assertFalse(blocked_gate["protected_full_auto_live_allowed"])
        self.assertIn("FINAL_CONTROLLED_POSITION_LOOP_NOT_PASSED", [item["code"] for item in blocked_gate["protected_full_auto_live_blockers"]])

        event = record_resolved_duplicate_client_order_safety_event(admin_confirmed=True)
        self.assertEqual(event["related_run_id"], "posloop-20260629T121326-57c2fc")
        self.assertEqual(event["resolution_status"], "RESOLVED")
        self.assertEqual(int(event["admin_confirmed"]), 1)

        with (
            patch("app.accounting_epoch.is_emergency_stopped", return_value=False),
            patch("app.controlled_auto_live.is_emergency_stopped", return_value=False),
            patch("app.controlled_auto_live.LiveTradingConfig.for_exchange", return_value=SimpleNamespace(api_key_loaded=True, live_trading_enabled=True)),
        ):
            resolved_gate = controlled_auto_live_gate(current_epoch, preflight, exchange="bithumb")

        self.assertTrue(resolved_gate["protected_session_start_allowed"], resolved_gate["protected_full_auto_live_blockers"])
        self.assertTrue(resolved_gate["protected_full_auto_live_allowed"], resolved_gate["protected_full_auto_live_blockers"])
        self.assertEqual(resolved_gate["protected_full_auto_live_blockers"], [])
        self.assertIn("RESOLVED_PREVIOUS_DUPLICATE_CLIENT_ORDER_ID", [item["code"] for item in resolved_gate["protected_full_auto_live_warnings"]])
        self.assertEqual(resolved_gate["resolved_position_loop_safety_event"]["safety_event_id"], event["safety_event_id"])

    def test_protected_full_auto_global_daily_loss_is_warning_not_blocker(self) -> None:
        database.insert_smoke_test_run(
            {
                "smoke_test_id": "smoke-protected-warning",
                "exchange_name": "bithumb",
                "symbol": "BTC",
                "market": "KRW-BTC",
                "status": "PASSED_AFTER_RECALC",
                "started_at_utc": "2026-06-26T00:00:00Z",
                "completed_at_utc": "2026-06-26T00:01:00Z",
                "max_notional_krw": 6000,
                "report": {
                    "duplicate_fill_count": 0,
                    "fee_diff": 0.0,
                    "equity_diff_after": 0.0,
                    "current_epoch_accounting_pending_count": 0,
                    "current_epoch_accounting_failed_count": 0,
                    "final_runtime_status": "STOPPED",
                },
            }
        )
        persist_controlled_run_report(
            {
                "controlled_run_id": "posloop-warning",
                "run_type": "CONTROLLED_POSITION_LOOP",
                "controlled_auto_live_status": "PASS_IDLE",
                "technical_result": "PASS_IDLE",
                "profitability_result": "NO_TRADE",
                "started_at_utc": "2026-06-29T07:37:46Z",
                "completed_at_utc": "2026-06-29T08:07:48Z",
                "trade_count": 0,
                "order_count": 0,
                "exchange_fill_count": 0,
                "ledger_fill_count": 0,
                "missing_ledger_fill_count": 0,
                "duplicate_fill_count": 0,
                "fee_diff": 0.0,
                "equity_diff_after": 0.0,
                "open_order_count_after": 0,
                "final_runtime_status": "STOPPED",
            }
        )
        current_epoch = {
            "current_epoch_exists": True,
            "current_epoch_id": "epoch-controlled",
            "current_epoch_trust_level": "MEDIUM",
            "current_epoch_sanity_passed": True,
            "current_epoch_current_equity": 260_000.0,
            "current_epoch_total_pnl": -5000.0,
            "current_epoch_accounting_pending_count": 0,
            "current_epoch_accounting_failed_count": 0,
        }
        preflight = {
            "smoke_test_blockers": [],
            "open_order_audit_summary": {
                "exchange_open_order_count": 0,
                "current_epoch_open_order_count": 0,
                "unknown_open_order_count": 0,
            },
        }
        risk_state = {
            "daily_realized_pnl": -1516.7006,
            "daily_unrealized_pnl": 0.0,
            "daily_total_pnl": -1516.7006,
        }
        with (
            patch("app.accounting_epoch.is_emergency_stopped", return_value=False),
            patch("app.controlled_auto_live.is_emergency_stopped", return_value=False),
            patch("app.controlled_auto_live.LiveTradingConfig.for_exchange", return_value=SimpleNamespace(api_key_loaded=True, live_trading_enabled=True)),
            patch("app.controlled_auto_live.compute_risk_state", return_value=risk_state),
        ):
            gate = controlled_auto_live_gate(current_epoch, preflight, exchange="bithumb")

        self.assertTrue(gate["protected_full_auto_live_allowed"], gate["protected_full_auto_live_blockers"])
        self.assertEqual(gate["protected_session_hard_blockers"], [])
        self.assertEqual(gate["global_daily_loss_status"], "EXCEEDED")
        self.assertEqual(gate["protected_session_loss_status"], "OK")
        self.assertEqual(gate["pre_existing_daily_realized_pnl"], -1516.7006)
        self.assertIn("GLOBAL_DAILY_LOSS_ALREADY_EXCEEDED", [item["code"] for item in gate["protected_session_warnings"]])
        self.assertEqual(gate["protected_session_loss_limit"], 1000.0)
        self.assertEqual(gate["protected_full_auto_next_action"], "USER_CONFIRM_PROTECTED_FULL_AUTO_START")
        self.assertFalse(gate["full_auto_live_allowed"])

    def test_protected_session_delta_loss_triggers_session_stop(self) -> None:
        current_epoch = {
            "current_epoch_current_equity": 260_000.0,
            "current_epoch_total_pnl": -5000.0,
        }
        baseline = build_protected_session_baseline(
            current_epoch=current_epoch,
            risk_state={
                "daily_realized_pnl": -1516.7006,
                "daily_unrealized_pnl": 0.0,
                "daily_total_pnl": -1516.7006,
            },
            protected_session_id="protected-session-test",
        )
        report = {"protected_session_baseline": baseline}
        updates = update_protected_session_loss_status(
            report,
            {"current_epoch_total_pnl": -6400.0},
            {
                "daily_realized_pnl": -1516.7006,
                "daily_unrealized_pnl": -1001.0,
                "daily_total_pnl": -2517.7006,
            },
        )

        self.assertEqual(updates["global_daily_loss_status"], "EXCEEDED")
        self.assertEqual(updates["protected_session_loss_status"], "EXCEEDED")
        self.assertAlmostEqual(updates["session_pnl_delta"], -1001.0)
        self.assertLess(updates["session_loss_limit_remaining"], 0)
        self.assertEqual(updates["protected_session_stop_reason"], "PROTECTED_SESSION_LOSS_LIMIT_REACHED")


if __name__ == "__main__":
    unittest.main()
