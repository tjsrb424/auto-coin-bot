import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database
from app.capital_allocator import _create_allocator_session, capital_allocator_status, run_capital_allocator_once


def candidate_payload(market: str = "KRW-ETH", status: str = "LIVE_ELIGIBLE", score: float = 95.0) -> dict:
    return {
        "name": f"{market} allocator test",
        "description": "",
        "strategy": "ma_cross",
        "parameters": {"short_window": 5, "long_window": 20},
        "unit": 5,
        "market": market,
        "backtest_period": "30d",
        "score": score,
        "backtest_total_return": 0.04,
        "backtest_mdd": 0.04,
        "backtest_win_rate": 0.55,
        "backtest_profit_factor": 1.4,
        "backtest_trade_count": 12,
        "backtest_average_trade_pnl": 0.002,
        "warning": "",
        "status": status,
    }


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


def snapshot_payload(*, available_budget: float = 300_000, available_krw: float = 350_000) -> dict:
    slots = database.load_position_slots(5, "bithumb")
    positions = database.load_open_live_positions_for_exchange("bithumb")
    reservations = database.load_active_order_reservations("bithumb")
    return {
        "exchange": "bithumb",
        "created_at": "2026-06-22T00:00:00Z",
        "snapshot_error": "",
        "warnings": [],
        "blockers": [],
        "auto_trading_enabled": database.load_bot_operation_policy("KRW-BTC")["auto_trading_enabled"],
        "max_total_exposure_krw": 500_000,
        "daily_loss_limit_pct": 5,
        "available_krw_balance": available_krw,
        "cash_reserve_krw": 25_000,
        "db_open_position_value_krw": 0,
        "exchange_position_value_krw": 0,
        "pending_buy_reserved_krw": 0,
        "pending_exchange_buy_order_krw": 0,
        "remaining_exposure_krw": 500_000,
        "available_budget_krw": available_budget,
        "open_position_count": len(positions),
        "max_open_position_count": 5,
        "empty_slot_count": len([slot for slot in slots if slot["status"] == "EMPTY"]),
        "balance_mismatch_detected": False,
        "open_order_mismatch_detected": False,
        "positions": positions,
        "balances": {"by_currency": {"KRW": {"balance": available_krw, "locked": 0}}},
        "open_orders": [],
        "db_open_orders": [],
        "reservations": reservations,
        "slots": slots,
    }


class CapitalAllocatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        self.env_patch = patch.dict(
            os.environ,
            {
                "DATABASE_URL": "",
                "AUTO_CAPITAL_ALLOCATOR_ENABLED": "true",
                "AUTO_ALLOWED_EXCHANGE": "bithumb",
                "AUTO_MAX_OPEN_POSITION_COUNT": "5",
                "AUTO_MAX_NEW_ENTRIES_PER_TICK": "2",
                "AUTO_MAX_ORDER_KRW": "30000",
                "AUTO_MIN_ORDER_KRW": "5000",
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

    def test_auto_trading_off_queues_candidate_without_live_active_or_policy_mutation(self) -> None:
        database.save_candidate_strategy(candidate_payload())
        database.update_bot_operation_policy(
            "KRW-BTC",
            {"auto_trading_enabled": False, "max_total_exposure_krw": 500_000, "daily_loss_limit_pct": 5},
        )
        before = database.load_bot_operation_policy("KRW-BTC")

        with patch("app.capital_allocator.is_emergency_stopped", return_value=False), \
            patch("app.capital_allocator.build_capital_snapshot", return_value=snapshot_payload()):
            result = run_capital_allocator_once("TEST", exchange="bithumb")

        after = database.load_bot_operation_policy("KRW-BTC")
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["accepted"]), 0)
        self.assertEqual(result["blocked"][0]["blocked_reason"], "POLICY_AUTO_TRADING_DISABLED")
        self.assertIsNone(database.load_active_strategy_selection())
        self.assertEqual(before["auto_trading_enabled"], after["auto_trading_enabled"])
        self.assertEqual(before["max_total_exposure_krw"], after["max_total_exposure_krw"])
        self.assertEqual(before["daily_loss_limit_pct"], after["daily_loss_limit_pct"])

    def test_auto_trading_on_assigns_live_eligible_candidate_to_slot_and_session(self) -> None:
        candidate_id = database.save_candidate_strategy(candidate_payload())
        database.update_bot_operation_policy(
            "KRW-BTC",
            {"auto_trading_enabled": True, "max_total_exposure_krw": 500_000, "daily_loss_limit_pct": 5},
        )

        with patch("app.capital_allocator.is_emergency_stopped", return_value=False), \
            patch("app.capital_allocator.build_capital_snapshot", return_value=snapshot_payload()):
            result = run_capital_allocator_once("TEST", exchange="bithumb")

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["accepted"]), 1)
        self.assertEqual(database.load_candidate_strategy(candidate_id)["status"], "LIVE_ACTIVE")
        self.assertEqual(database.load_active_strategy_selection()["candidate_strategy_id"], candidate_id)
        sessions = database.load_running_live_strategy_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["candidate_strategy_id"], candidate_id)
        self.assertLessEqual(float(sessions[0]["max_order_krw"]), 30_000)
        slots = database.load_position_slots(5, "bithumb")
        self.assertEqual(slots[0]["status"], "RESERVED")
        self.assertEqual(slots[0]["candidate_strategy_id"], candidate_id)

    def test_allocator_session_creation_rejects_candidate_mismatch(self) -> None:
        candidate_id = database.save_candidate_strategy(candidate_payload(market="KRW-ETH"))
        wrong_session_id = database.create_live_strategy_session(
            {
                "exchange": "bithumb",
                "market": "KRW-WLD",
                "candidate_strategy_id": 999,
                "strategy_name": "rsi",
                "strategy_parameters": {},
                "status": "READY",
                "auto_enabled": True,
                "initial_balance_krw": 0.0,
                "max_order_krw": 10_000,
                "max_orders_per_day": 3,
            }
        )

        with patch("app.capital_allocator.create_live_strategy_session", return_value=wrong_session_id):
            with self.assertRaisesRegex(RuntimeError, "ALLOCATOR_SESSION_CANDIDATE_MISMATCH"):
                _create_allocator_session(
                    candidate=database.load_candidate_strategy(candidate_id),
                    approved_order=10_000,
                    exchange="bithumb",
                )

    def test_next_entry_queue_upsert_updates_existing_candidate_status_row(self) -> None:
        candidate_id = database.save_candidate_strategy(candidate_payload())
        candidate = database.load_candidate_strategy(candidate_id)

        first_id = database.enqueue_next_entry(candidate, allocation_score=10, blocked_reason="FIRST")
        second_id = database.enqueue_next_entry({**candidate, "score": 99}, allocation_score=20, blocked_reason="SECOND")

        self.assertEqual(first_id, second_id)
        queue = database.load_next_entry_queue(statuses=["QUEUED"])
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["candidate_strategy_id"], candidate_id)
        self.assertEqual(queue[0]["blocked_reason"], "SECOND")
        self.assertEqual(queue[0]["allocation_score"], 20)

    def test_allocator_run_does_not_fail_when_candidate_is_already_queued(self) -> None:
        candidate_id = database.save_candidate_strategy(candidate_payload())
        candidate = database.load_candidate_strategy(candidate_id)
        database.enqueue_next_entry(candidate, allocation_score=1, blocked_reason="OLD_BLOCK")
        database.update_bot_operation_policy(
            "KRW-BTC",
            {"auto_trading_enabled": False, "max_total_exposure_krw": 500_000, "daily_loss_limit_pct": 5},
        )

        with patch("app.capital_allocator.is_emergency_stopped", return_value=False), \
            patch("app.capital_allocator.build_capital_snapshot", return_value=snapshot_payload()):
            result = run_capital_allocator_once("TEST_QUEUE_UPSERT", exchange="bithumb")

        self.assertTrue(result["ok"])
        self.assertNotEqual(result["run"]["status"], "FAILED")
        queue = database.load_next_entry_queue(statuses=["QUEUED"])
        self.assertEqual(len([row for row in queue if row["candidate_strategy_id"] == candidate_id]), 1)
        self.assertEqual(queue[0]["blocked_reason"], "POLICY_AUTO_TRADING_DISABLED")

    def test_next_entry_status_update_merges_existing_target_status_row(self) -> None:
        candidate_id = database.save_candidate_strategy(candidate_payload())
        candidate = database.load_candidate_strategy(candidate_id)

        first_id = database.enqueue_next_entry(candidate, allocation_score=10, blocked_reason="FIRST")
        database.update_next_entry_status(candidate_id, "PROMOTED_TO_SLOT", "FIRST_PROMOTED")
        second_id = database.enqueue_next_entry(candidate, allocation_score=20, blocked_reason="SECOND")

        self.assertIsNotNone(first_id)
        self.assertIsNotNone(second_id)
        database.update_next_entry_status(candidate_id, "PROMOTED_TO_SLOT", "SECOND_PROMOTED")

        with database.get_connection() as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM next_entry_queue WHERE candidate_strategy_id = ? ORDER BY id",
                    (candidate_id,),
                ).fetchall()
            ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "PROMOTED_TO_SLOT")
        self.assertEqual(rows[0]["allocation_score"], 20)
        self.assertEqual(rows[0]["blocked_reason"], "SECOND_PROMOTED")

    def test_next_entry_queue_insert_is_centralized_in_database_helper(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        insert_sites = []
        for path in (repo_root / "app").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "INSERT INTO next_entry_queue" in text:
                insert_sites.append(path.name)

        self.assertEqual(insert_sites, ["database.py"])

    def test_allocator_uses_market_opportunity_score_for_candidate_ordering(self) -> None:
        base_id = database.save_candidate_strategy(candidate_payload(market="KRW-ETH", score=95))
        allow_market("KRW-XRP")
        edge_payload = candidate_payload(market="KRW-XRP", score=90)
        edge_payload["strategy"] = "rsi"
        edge_id = database.save_candidate_strategy(edge_payload)
        database.upsert_adaptive_edge_stat(
            {
                "exchange": "bithumb",
                "market": "KRW-XRP",
                "strategy_name": "rsi",
                "candidate_strategy_id": edge_id,
                "unit": 5,
                "market_regime": "TREND_UP",
                "action_hint": "BUY_MORE",
                "legacy_signal": "BUY",
                "attack_mode": "BALANCED",
                "target_source": "ADAPTIVE",
                "order_purpose": "ENTRY",
                "sample_count": 30,
                "win_count": 22,
                "loss_count": 8,
                "win_rate": 0.73,
                "avg_post_fill_return_1m": 1.0,
                "avg_post_fill_return_5m": 2.0,
                "avg_post_fill_return_15m": 2.5,
                "avg_realized_return_pct": 2.0,
                "avg_realized_pnl_krw": 2000,
                "profit_factor": 2.0,
                "avg_adverse_selection_pct": 0.1,
                "avg_slippage_pct": 0.05,
                "avg_fill_time_seconds": 4,
                "max_drawdown_pct": -0.2,
                "confidence_score": 85,
                "edge_score": 3.0,
                "last_updated_at": "2026-06-24T00:00:00Z",
            }
        )
        database.update_bot_operation_policy(
            "KRW-BTC",
            {"auto_trading_enabled": True, "max_total_exposure_krw": 500_000, "daily_loss_limit_pct": 5},
        )

        with patch.dict(os.environ, {"AUTO_MAX_NEW_ENTRIES_PER_TICK": "1"}, clear=False), \
            patch("app.capital_allocator.is_emergency_stopped", return_value=False), \
            patch("app.capital_allocator.build_capital_snapshot", return_value=snapshot_payload()):
            result = run_capital_allocator_once("TEST_OPPORTUNITY", exchange="bithumb")

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["accepted"]), 1)
        self.assertEqual(result["accepted"][0]["candidate"]["id"], edge_id)
        self.assertGreater(result["accepted"][0]["market_opportunity_score"], 0)
        self.assertEqual(database.load_candidate_strategy(edge_id)["status"], "LIVE_ACTIVE")
        self.assertEqual(database.load_candidate_strategy(base_id)["status"], "LIVE_ELIGIBLE")

    def test_same_market_stronger_candidate_replaces_reserved_slot_before_entry(self) -> None:
        first_id = database.save_candidate_strategy(candidate_payload(score=80))
        database.update_bot_operation_policy(
            "KRW-BTC",
            {"auto_trading_enabled": True, "max_total_exposure_krw": 500_000, "daily_loss_limit_pct": 5},
        )

        with patch("app.capital_allocator.is_emergency_stopped", return_value=False), \
            patch("app.capital_allocator.build_capital_snapshot", return_value=snapshot_payload()):
            first = run_capital_allocator_once("TEST", exchange="bithumb")

        stronger = candidate_payload(score=95)
        stronger["strategy"] = "volume_breakout"
        stronger["name"] = "KRW-ETH volume_breakout 5m 95pt"
        stronger_id = database.save_candidate_strategy(stronger)

        with patch("app.capital_allocator.is_emergency_stopped", return_value=False), \
            patch("app.capital_allocator.build_capital_snapshot", return_value=snapshot_payload()):
            second = run_capital_allocator_once("TEST_REPLACE", exchange="bithumb")

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual(len(second["accepted"]), 1)
        self.assertEqual(second["accepted"][0]["replaced_candidate_strategy_id"], first_id)
        self.assertEqual(database.load_candidate_strategy(first_id)["status"], "LIVE_ELIGIBLE")
        self.assertEqual(database.load_candidate_strategy(stronger_id)["status"], "LIVE_ACTIVE")
        slots = database.load_position_slots(5, "bithumb")
        self.assertEqual(slots[0]["status"], "RESERVED")
        self.assertEqual(slots[0]["candidate_strategy_id"], stronger_id)
        sessions = database.load_running_live_strategy_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["candidate_strategy_id"], stronger_id)
        self.assertEqual(sessions[0]["strategy_name"], "volume_breakout")

    def test_same_market_candidate_is_not_replaced_when_position_is_open(self) -> None:
        active_id = database.save_candidate_strategy(candidate_payload(status="LIVE_ACTIVE", score=80))
        session_id = database.create_live_strategy_session(
            {
                "exchange": "bithumb",
                "market": "KRW-ETH",
                "candidate_strategy_id": active_id,
                "strategy_name": "ma_cross",
                "strategy_parameters": {"short_window": 5, "long_window": 20},
                "status": "RUNNING",
                "auto_enabled": True,
                "initial_balance_krw": 0,
                "max_order_krw": 20_000,
                "max_orders_per_day": 3,
            }
        )
        database.create_live_position(
            {
                "session_id": session_id,
                "exchange": "bithumb",
                "market": "KRW-ETH",
                "candidate_strategy_id": active_id,
                "strategy_name": "ma_cross",
                "status": "OPEN",
                "entry_order_uuid": "open-position-test",
                "entry_price": 1000,
                "entry_volume": 10,
                "entry_amount_krw": 10_000,
                "current_price": 1010,
                "stop_loss_price": 900,
                "take_profit_price": 1100,
            }
        )
        stronger = candidate_payload(score=99)
        stronger["strategy"] = "volume_breakout"
        stronger_id = database.save_candidate_strategy(stronger)
        database.update_bot_operation_policy(
            "KRW-BTC",
            {"auto_trading_enabled": True, "max_total_exposure_krw": 500_000, "daily_loss_limit_pct": 5},
        )

        with patch("app.capital_allocator.is_emergency_stopped", return_value=False), \
            patch("app.capital_allocator.build_capital_snapshot", return_value=snapshot_payload()):
            result = run_capital_allocator_once("TEST_OPEN_POSITION", exchange="bithumb")

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["accepted"]), 0)
        self.assertEqual(result["blocked"][0]["blocked_reason"], "BLOCKED_DUPLICATE_MARKET_POSITION")
        self.assertEqual(database.load_candidate_strategy(stronger_id)["status"], "LIVE_ELIGIBLE")

    def test_available_krw_shortage_blocks_candidate(self) -> None:
        database.save_candidate_strategy(candidate_payload())
        database.update_bot_operation_policy(
            "KRW-BTC",
            {"auto_trading_enabled": True, "max_total_exposure_krw": 500_000, "daily_loss_limit_pct": 5},
        )

        with patch("app.capital_allocator.is_emergency_stopped", return_value=False), \
            patch("app.capital_allocator.build_capital_snapshot", return_value=snapshot_payload(available_budget=0, available_krw=1_000)):
            result = run_capital_allocator_once("TEST", exchange="bithumb")

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["accepted"]), 0)
        self.assertEqual(result["blocked"][0]["blocked_reason"], "BLOCKED_INSUFFICIENT_KRW_BALANCE")

    def test_expired_order_reservation_releases_reserved_slot(self) -> None:
        candidate_id = database.save_candidate_strategy(candidate_payload())
        slots = database.ensure_position_slots(5, "bithumb")
        slot_id = int(slots[0]["id"])
        database.create_order_reservation(
            {
                "request_id": "expired-test",
                "exchange": "bithumb",
                "market": "KRW-ETH",
                "candidate_strategy_id": candidate_id,
                "slot_id": slot_id,
                "amount_krw": 10_000,
                "status": "RESERVED",
                "expires_at": "2000-01-01T00:00:00Z",
            }
        )
        database.reserve_position_slot(
            slot_id=slot_id,
            exchange="bithumb",
            market="KRW-ETH",
            candidate_strategy_id=candidate_id,
            live_strategy_session_id=None,
            amount_krw=10_000,
            reason="test",
        )

        slots = database.load_position_slots(5, "bithumb")

        self.assertEqual(slots[0]["status"], "EMPTY")

    def test_status_reconciles_existing_open_position(self) -> None:
        candidate_id = database.save_candidate_strategy(candidate_payload(status="LIVE_ACTIVE"))
        session_id = database.create_live_strategy_session(
            {
                "exchange": "bithumb",
                "market": "KRW-ETH",
                "candidate_strategy_id": candidate_id,
                "strategy_name": "ma_cross",
                "strategy_parameters": {"short_window": 5, "long_window": 20},
                "status": "RUNNING",
                "auto_enabled": True,
                "initial_balance_krw": 0,
                "max_order_krw": 20_000,
                "max_orders_per_day": 3,
            }
        )
        database.create_live_position(
            {
                "session_id": session_id,
                "exchange": "bithumb",
                "market": "KRW-ETH",
                "candidate_strategy_id": candidate_id,
                "strategy_name": "ma_cross",
                "status": "OPEN",
                "entry_order_uuid": "open-position-test",
                "entry_price": 1000,
                "entry_volume": 10,
                "entry_amount_krw": 10_000,
                "current_price": 1010,
                "stop_loss_price": 900,
                "take_profit_price": 1100,
            }
        )

        status = capital_allocator_status("bithumb")

        self.assertEqual(status["open_slot_count"], 1)
        self.assertEqual(status["slots"][0]["status"], "OPEN")
        self.assertEqual(status["slots"][0]["market"], "KRW-ETH")


if __name__ == "__main__":
    unittest.main()
