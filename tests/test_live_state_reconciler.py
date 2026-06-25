import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import database
from app.capital_allocator import run_capital_allocator_once
from app.live_state_reconciler import (
    MISMATCHED_SLOT_REASON,
    ORPHAN_REASON,
    STALE_POINTER_REASON,
    live_state_warnings,
    reconcile_live_state,
    reconcile_orphan_live_active_candidates,
    reconcile_stale_live_strategy_sessions,
)


def candidate_payload(market: str = "KRW-ETH", status: str = "LIVE_ACTIVE", score: float = 95.0) -> dict:
    return {
        "name": f"{market} state reconcile test",
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
        "open_position_count": 0,
        "max_open_position_count": 5,
        "empty_slot_count": len([slot for slot in slots if slot["status"] == "EMPTY"]),
        "balance_mismatch_detected": False,
        "open_order_mismatch_detected": False,
        "positions": [],
        "balances": {"by_currency": {"KRW": {"balance": available_krw, "locked": 0}}},
        "open_orders": [],
        "db_open_orders": [],
        "reservations": database.load_active_order_reservations("bithumb"),
        "slots": slots,
    }


def live_position(session_id: int, candidate_id: int, *, status: str = "OPEN") -> dict:
    return {
        "session_id": session_id,
        "exchange": "bithumb",
        "market": "KRW-ETH",
        "candidate_strategy_id": candidate_id,
        "strategy_name": "ma_cross",
        "status": status,
        "entry_order_uuid": f"entry-{status.lower()}",
        "entry_price": 1000,
        "entry_volume": 10,
        "entry_amount_krw": 10_000,
        "current_price": 1000,
        "stop_loss_price": 900,
        "take_profit_price": 1100,
        "opened_at": "2026-06-22T00:00:00Z",
    }


def load_session(session_id: int) -> dict:
    with database.get_connection() as conn:
        row = conn.execute("SELECT * FROM live_strategy_sessions WHERE id = ?", (session_id,)).fetchone()
    item = dict(row)
    item["auto_enabled"] = bool(item.get("auto_enabled"))
    return item


def load_promotions(candidate_id: int) -> list[dict]:
    with database.get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM candidate_strategy_promotions WHERE candidate_strategy_id = ? ORDER BY id ASC",
            (candidate_id,),
        ).fetchall()
    return [dict(row) for row in rows]


class LiveStateReconcilerTests(unittest.TestCase):
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

    def _session(self, candidate_id: int, *, status: str = "RUNNING", market: str = "KRW-ETH") -> int:
        return database.create_live_strategy_session(
            {
                "exchange": "bithumb",
                "market": market,
                "candidate_strategy_id": candidate_id,
                "strategy_name": "ma_cross",
                "strategy_parameters": {"short_window": 5, "long_window": 20},
                "status": status,
                "auto_enabled": status in {"READY", "RUNNING"},
                "initial_balance_krw": 0,
                "max_order_krw": 20_000,
                "max_orders_per_day": 3,
            }
        )

    def test_live_active_orphan_demotes_to_live_eligible_and_records_promotion(self) -> None:
        candidate_id = database.save_candidate_strategy(candidate_payload())
        candidate = database.load_candidate_strategy(candidate_id)
        database.save_active_strategy_selection(candidate, reason="old")
        with database.get_connection() as conn:
            conn.execute("UPDATE active_strategy_selection SET status='REPLACED' WHERE candidate_strategy_id=?", (candidate_id,))

        result = reconcile_orphan_live_active_candidates(dry_run=False)

        self.assertEqual(result["demoted_count"], 1)
        self.assertEqual(database.load_candidate_strategy(candidate_id)["status"], "LIVE_ELIGIBLE")
        promotions = load_promotions(candidate_id)
        self.assertEqual(promotions[-1]["reason"], ORPHAN_REASON)

    def test_live_active_with_running_session_is_kept(self) -> None:
        candidate_id = database.save_candidate_strategy(candidate_payload())
        self._session(candidate_id)

        result = reconcile_orphan_live_active_candidates(dry_run=False)

        self.assertEqual(result["demoted_count"], 0)
        self.assertEqual(database.load_candidate_strategy(candidate_id)["status"], "LIVE_ACTIVE")

    def test_allocator_reconsiles_orphan_before_assignment(self) -> None:
        candidate_id = database.save_candidate_strategy(candidate_payload())
        database.update_bot_operation_policy(
            "KRW-BTC",
            {"auto_trading_enabled": True, "max_total_exposure_krw": 500_000, "daily_loss_limit_pct": 5},
        )

        with patch("app.capital_allocator.is_emergency_stopped", return_value=False), patch(
            "app.capital_allocator.build_capital_snapshot", return_value=snapshot_payload()
        ):
            result = run_capital_allocator_once("TEST_ORPHAN", exchange="bithumb")

        self.assertTrue(result["ok"])
        self.assertEqual(result["accepted"][0]["candidate"]["id"], candidate_id)
        self.assertEqual(database.load_candidate_strategy(candidate_id)["status"], "LIVE_ACTIVE")
        slots = database.load_position_slots(5, "bithumb")
        self.assertEqual(slots[0]["candidate_strategy_id"], candidate_id)

    def test_closed_position_pointer_without_replacement_stops_session(self) -> None:
        candidate_id = database.save_candidate_strategy(candidate_payload())
        session_id = self._session(candidate_id)
        closed_position_id = database.create_live_position(live_position(session_id, candidate_id, status="CLOSED"))
        database.update_live_strategy_session(session_id, {"current_position_id": closed_position_id})

        result = reconcile_stale_live_strategy_sessions(dry_run=False)

        session = load_session(session_id)
        self.assertEqual(result["fixed_count"], 1)
        self.assertIsNone(session["current_position_id"])
        self.assertEqual(session["status"], "STOPPED")
        self.assertFalse(session["auto_enabled"])

    def test_closed_position_pointer_replaces_with_open_position(self) -> None:
        candidate_id = database.save_candidate_strategy(candidate_payload())
        session_id = self._session(candidate_id)
        closed_position_id = database.create_live_position(live_position(session_id, candidate_id, status="CLOSED"))
        open_position_id = database.create_live_position(live_position(session_id, candidate_id, status="OPEN"))
        database.update_live_strategy_session(session_id, {"current_position_id": closed_position_id})

        result = reconcile_stale_live_strategy_sessions(dry_run=False)

        session = load_session(session_id)
        self.assertEqual(result["fixed_count"], 1)
        self.assertEqual(session["current_position_id"], open_position_id)
        self.assertEqual(session["status"], "RUNNING")
        self.assertEqual(session["last_risk_result"], STALE_POINTER_REASON)

    def test_live_state_warnings_report_orphan_and_stale_pointer(self) -> None:
        candidate_id = database.save_candidate_strategy(candidate_payload())
        session_id = self._session(candidate_id)
        closed_position_id = database.create_live_position(live_position(session_id, candidate_id, status="CLOSED"))
        database.update_live_strategy_session(session_id, {"current_position_id": closed_position_id})
        # Running session keeps the candidate non-orphan, so add a separate orphan candidate.
        database.save_candidate_strategy(candidate_payload(market="KRW-XRP", status="LIVE_ACTIVE"))
        allow_market("KRW-XRP")

        warnings = live_state_warnings()
        self.assertIn("ORPHAN_LIVE_ACTIVE_CANDIDATES_DETECTED", warnings["warnings"])
        self.assertIn("STALE_SESSION_POSITION_POINTER_DETECTED", warnings["warnings"])

        reconcile_live_state(dry_run=False)
        warnings_after = live_state_warnings()
        self.assertNotIn("STALE_SESSION_POSITION_POINTER_DETECTED", warnings_after["warnings"])

    def test_reconcile_releases_mismatched_reserved_slot_session(self) -> None:
        candidate_id = database.save_candidate_strategy(candidate_payload(market="KRW-ETH"))
        wrong_candidate_id = database.save_candidate_strategy(candidate_payload(market="KRW-XRP"))
        allow_market("KRW-XRP")
        wrong_session_id = self._session(wrong_candidate_id, market="KRW-XRP")
        slot = database.load_position_slots(5, "bithumb")[0]
        database.reserve_position_slot(
            slot_id=int(slot["id"]),
            exchange="bithumb",
            market="KRW-ETH",
            candidate_strategy_id=candidate_id,
            live_strategy_session_id=wrong_session_id,
            amount_krw=20_000,
            reason="TEST",
        )
        database.create_order_reservation(
            {
                "request_id": "mismatch-test",
                "exchange": "bithumb",
                "market": "KRW-ETH",
                "candidate_strategy_id": candidate_id,
                "slot_id": int(slot["id"]),
                "amount_krw": 20_000,
                "status": "RESERVED",
                "expires_at": "2099-01-01T00:00:00Z",
            }
        )

        warnings = live_state_warnings()
        self.assertIn("MISMATCHED_POSITION_SLOT_SESSION_DETECTED", warnings["warnings"])

        result = reconcile_live_state(dry_run=False)

        self.assertEqual(result["mismatched_position_slot_sessions"]["released_count"], 1)
        released_slot = database.load_position_slots(5, "bithumb")[0]
        self.assertEqual(released_slot["status"], "EMPTY")
        self.assertIsNone(released_slot["candidate_strategy_id"])
        self.assertEqual(database.load_candidate_strategy(candidate_id)["status"], "LIVE_ELIGIBLE")
        self.assertEqual(load_session(wrong_session_id)["status"], "STOPPED")
        self.assertEqual(load_session(wrong_session_id)["last_risk_result"], MISMATCHED_SLOT_REASON)
        queue = database.load_next_entry_queue(statuses=["QUEUED"])
        self.assertTrue(any(row["candidate_strategy_id"] == candidate_id for row in queue))
