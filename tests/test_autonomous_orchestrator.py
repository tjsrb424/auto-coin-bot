from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks

from app import database
from app.autonomous_orchestrator import (
    ORCHESTRATOR_TASK,
    run_autonomous_orchestrator_once,
)
from app.live_strategy_pilot import AUTO_STRATEGY_CONFIRMATION
from app.strategy_discovery_scheduler import PROMOTION_TASK
from app.strategy_discovery_scheduler import DEEP_VALIDATION_TASK


def allow_market(market: str = "KRW-ETH") -> None:
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
                "last_volatility_score": 20,
                "last_liquidity_score": 60,
                "last_risk_score": 10,
                "last_scanned_at": "2026-06-22T00:00:00Z",
            }
        ]
    )


def candidate(status: str = "LIVE_ELIGIBLE", market: str = "KRW-ETH", score: float = 95.0) -> dict:
    return {
        "name": f"{market} orchestrator test",
        "description": "",
        "strategy": "ma_cross",
        "parameters": {"short_window": 5, "long_window": 20},
        "unit": 5,
        "market": market,
        "backtest_period": "30d",
        "score": score,
        "backtest_total_return": 0.03,
        "backtest_mdd": 0.04,
        "backtest_win_rate": 0.55,
        "backtest_profit_factor": 1.4,
        "backtest_trade_count": 12,
        "backtest_average_trade_pnl": 0.002,
        "warning": "",
        "status": status,
    }


class AutonomousOrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        self.env_patch = patch.dict(
            os.environ,
            {
                "DATABASE_URL": "",
                "AUTO_AUTONOMOUS_ORCHESTRATOR_ENABLED": "true",
                "AUTO_AUTONOMOUS_ORCHESTRATOR_INTERVAL_MINUTES": "5",
                "AUTO_AUTONOMOUS_ORCHESTRATOR_LOCK_TTL_SECONDS": "300",
                "AUTO_DISCOVERY_EXCHANGE": "bithumb",
                "AUTO_PROMOTION_PIPELINE_ENABLED": "true",
                "AUTO_SELECTOR_APPLY_BEST_ENABLED": "true",
            },
            clear=False,
        )
        self.env_patch.start()
        database.init_db()
        allow_market()

    def tearDown(self) -> None:
        self.env_patch.stop()
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_auto_trading_off_promotes_context_but_does_not_apply_live_active(self) -> None:
        database.save_candidate_strategy(candidate("LIVE_ELIGIBLE"))
        database.update_bot_operation_policy(
            "KRW-ETH",
            {"auto_trading_enabled": False, "max_total_exposure_krw": 700_000, "daily_loss_limit_pct": 7},
        )
        before = database.load_bot_operation_policy("KRW-ETH")

        with patch("app.strategy_promotion_pipeline.is_emergency_stopped", return_value=False), \
            patch("app.auto_strategy_selector.is_emergency_stopped", return_value=False):
            result = run_autonomous_orchestrator_once("LIVE_ELIGIBLE_CREATED")

        after = database.load_bot_operation_policy("KRW-ETH")
        child = result["last_result"]["children"][0]
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(child["last_result"]["selector_decision"], "BLOCKED")
        self.assertIsNone(database.load_active_strategy_selection())
        self.assertEqual(before["auto_trading_enabled"], after["auto_trading_enabled"])
        self.assertEqual(before["max_total_exposure_krw"], after["max_total_exposure_krw"])
        self.assertEqual(before["daily_loss_limit_pct"], after["daily_loss_limit_pct"])

    def test_auto_trading_on_applies_live_eligible_candidate(self) -> None:
        candidate_id = database.save_candidate_strategy(candidate("LIVE_ELIGIBLE"))
        database.update_bot_operation_policy(
            "KRW-BTC",
            {"auto_trading_enabled": True, "max_total_exposure_krw": 700_000, "daily_loss_limit_pct": 7},
        )
        before = database.load_bot_operation_policy("KRW-BTC")

        with patch("app.strategy_promotion_pipeline.is_emergency_stopped", return_value=False), \
            patch("app.auto_strategy_selector.is_emergency_stopped", return_value=False):
            result = run_autonomous_orchestrator_once("LIVE_ELIGIBLE_CREATED")

        after = database.load_bot_operation_policy("KRW-BTC")
        active = database.load_active_strategy_selection()
        self.assertEqual(result["status"], "COMPLETED")
        self.assertIsNotNone(active)
        assert active is not None
        self.assertEqual(active["candidate_strategy_id"], candidate_id)
        self.assertEqual(before["auto_trading_enabled"], after["auto_trading_enabled"])
        self.assertEqual(before["max_total_exposure_krw"], after["max_total_exposure_krw"])
        self.assertEqual(before["daily_loss_limit_pct"], after["daily_loss_limit_pct"])

    def test_run_now_returns_skipped_locked_when_orchestrator_lock_is_active(self) -> None:
        acquired, _ = database.acquire_scheduler_task_lock(ORCHESTRATOR_TASK, owner="test", ttl_seconds=300)
        self.assertTrue(acquired)

        result = run_autonomous_orchestrator_once("MANUAL_RUN_NOW")

        self.assertEqual(result["status"], "SKIPPED_LOCKED")
        self.assertEqual(result["reason"], "MANUAL_RUN_NOW")

    def test_child_running_is_skipped_without_failing_orchestrator(self) -> None:
        acquired, _ = database.acquire_scheduler_task_lock(PROMOTION_TASK, owner="test", ttl_seconds=300)
        self.assertTrue(acquired)

        result = run_autonomous_orchestrator_once("LIVE_ELIGIBLE_CREATED")

        child = result["last_result"]["children"][0]
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(child["task_name"], PROMOTION_TASK)
        self.assertEqual(child["status"], "SKIPPED")
        self.assertEqual(child["reason"], "CHILD_RUNNING")

    def test_server_startup_runs_with_single_orchestrator_lock_cycle(self) -> None:
        with patch("app.autonomous_orchestrator.run_market_scan_scheduler_once", new=AsyncMock(return_value={"task_name": "market_scan", "status": "COMPLETED"})), \
            patch("app.autonomous_orchestrator.run_fast_validation_scheduler_once", new=AsyncMock(return_value={"task_name": "fast_validation", "status": "COMPLETED"})), \
            patch("app.autonomous_orchestrator.run_promotion_selector_scheduler_once", new=AsyncMock(return_value={"task_name": "promotion_selector", "status": "COMPLETED"})):
            result = run_autonomous_orchestrator_once("SERVER_STARTUP")

        state = database.load_scheduler_task_state(ORCHESTRATOR_TASK)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["last_result"]["executed_count"], 3)
        self.assertEqual(state["status"], "COMPLETED")
        self.assertIsNone(state["lock_until"])

    def test_fast_validation_saved_candidate_triggers_immediate_promotion(self) -> None:
        database.finish_scheduler_task("market_scan", status="COMPLETED", result={}, next_run_at="2099-01-01T00:00:00Z")
        database.finish_scheduler_task(PROMOTION_TASK, status="COMPLETED", result={}, next_run_at="2099-01-01T00:00:00Z")
        database.finish_scheduler_task(DEEP_VALIDATION_TASK, status="COMPLETED", result={}, next_run_at="2099-01-01T00:00:00Z")
        fast = AsyncMock(
            return_value={
                "task_name": "fast_validation",
                "status": "COMPLETED",
                "last_result": {"saved_candidate_count": 1},
            }
        )
        promotion = AsyncMock(return_value={"task_name": PROMOTION_TASK, "status": "COMPLETED"})

        with patch("app.autonomous_orchestrator.run_fast_validation_scheduler_once", new=fast), \
            patch("app.autonomous_orchestrator.run_promotion_selector_scheduler_once", new=promotion):
            result = run_autonomous_orchestrator_once("SCHEDULED")

        self.assertEqual(result["status"], "COMPLETED")
        fast.assert_awaited_once()
        promotion.assert_awaited_once()

    def test_runtime_start_schedules_runtime_started_orchestrator(self) -> None:
        from app.main import RuntimeStartRequest, start_runtime_endpoint

        background_tasks = BackgroundTasks()
        payload = RuntimeStartRequest(confirmation=AUTO_STRATEGY_CONFIRMATION)
        with patch("app.main._effective_auto_trading_status", return_value={"effective_auto_trading_enabled": True}), \
            patch("app.main._asset_reconciliation_from_exchange", new=AsyncMock(return_value={})), \
            patch("app.main.build_trading_diagnostics_report", return_value={"restart_gate": {"allowed": True}}), \
            patch("app.main._try_acquire_runtime_lock_for_start", return_value=(True, {}, None)), \
            patch("app.main.start_live_strategy_pilot", return_value={"ok": True}), \
            patch("app.main._runtime_status_payload", return_value={"runtime_status": "RUNNING"}), \
            patch("app.main.autonomous_orchestrator_config", return_value={"on_start_enabled": True}), \
            patch("app.main.run_autonomous_orchestrator_background") as orchestrator:
            response = start_runtime_endpoint(payload, request=object(), background_tasks=background_tasks)
            asyncio.run(background_tasks())

        self.assertTrue(response["ok"])
        orchestrator.assert_called_once_with("RUNTIME_STARTED")


if __name__ == "__main__":
    unittest.main()
