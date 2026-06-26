from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import database
from app.limited_auto_live import CONFIRMATION_PHRASE, run_one_shot_limited_auto_live


class LimitedAutoLiveTests(unittest.IsolatedAsyncioTestCase):
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
                "epoch_id": "epoch-limited",
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
        self.db_patch.stop()
        self.tmp.cleanup()

    async def test_confirmation_required_before_any_order(self) -> None:
        broker = AsyncMock()
        with patch("app.limited_auto_live.get_live_broker", return_value=broker):
            result = await run_one_shot_limited_auto_live(confirmation="NOPE")

        self.assertEqual(result["limited_auto_live_status"], "ABORTED")
        broker.place_order.assert_not_awaited()
        with database.get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) AS count FROM live_order_logs").fetchone()["count"]
        self.assertEqual(count, 0)

    async def test_limited_orders_use_separate_purpose_and_strategy(self) -> None:
        broker = AsyncMock()
        broker.place_order.side_effect = [
            {"uuid": "C0101000003129835501", "market": "KRW-BTC", "side": "bid"},
            {"uuid": "C0101000003129835502", "market": "KRW-BTC", "side": "ask"},
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
                    "risk_result": "LIMITED_AUTO_LIVE_FILLED",
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

        gate = {"limited_auto_live_allowed": True, "limited_auto_live_blockers": []}
        current_epoch = database.load_current_accounting_epoch("bithumb")
        current_epoch = {
            "current_epoch_exists": True,
            "current_epoch_started_at_utc": current_epoch["epoch_started_at_utc"],
            "current_epoch_trust_level": "MEDIUM",
            "current_epoch_sanity_passed": True,
            "current_epoch_fill_count": 0,
            "current_epoch_accounting_pending_count": 0,
            "current_epoch_accounting_failed_count": 0,
        }
        with (
            patch("app.limited_auto_live.get_live_broker", return_value=broker),
            patch("app.limited_auto_live._runtime_guards_pass", return_value=True),
            patch("app.limited_auto_live._current_equity", side_effect=[263_000.0, 262_990.0]),
            patch("app.limited_auto_live._orderbook_quote", side_effect=[{"best_ask": 600_000_000.0, "best_bid": 599_000_000.0}, {"best_ask": 600_000_000.0, "best_bid": 599_000_000.0}]),
            patch("app.limited_auto_live.reconcile_order_log", side_effect=reconcile),
        ):
            result = await run_one_shot_limited_auto_live(
                confirmation=CONFIRMATION_PHRASE,
                limited_gate=gate,
                current_epoch=current_epoch,
            )

        self.assertEqual(result["limited_auto_live_status"], "PASSED")
        self.assertEqual(result["order_count"], 2)
        self.assertEqual(result["missing_ledger_fill_count"], 0)
        self.assertEqual(result["duplicate_fill_count"], 0)
        self.assertEqual(broker.place_order.await_count, 2)
        with database.get_connection() as conn:
            rows = conn.execute(
                "SELECT order_purpose, strategy_name, signal_type FROM live_order_logs ORDER BY id"
            ).fetchall()
        self.assertEqual([row["order_purpose"] for row in rows], ["LIMITED_AUTO_LIVE", "LIMITED_AUTO_LIVE"])
        self.assertEqual({row["strategy_name"] for row in rows}, {"limited_auto_live"})


if __name__ == "__main__":
    unittest.main()
