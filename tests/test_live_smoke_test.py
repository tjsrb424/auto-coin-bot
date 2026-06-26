from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import database
from app.live_smoke_test import CONFIRMATION_PHRASE, recalculate_smoke_test_report, run_one_shot_live_smoke_test


class LiveSmokeTestTests(unittest.IsolatedAsyncioTestCase):
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
                "epoch_id": "epoch-test",
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
        with patch("app.live_smoke_test.get_live_broker", return_value=broker):
            result = await run_one_shot_live_smoke_test(confirmation="NOPE")

        self.assertEqual(result["smoke_test_status"], "ABORTED")
        broker.place_order.assert_not_awaited()
        with database.get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) AS count FROM live_order_logs").fetchone()["count"]
        self.assertEqual(count, 0)

    async def test_disabled_smoke_test_does_not_place_order(self) -> None:
        broker = AsyncMock()
        with patch("app.live_smoke_test.get_live_broker", return_value=broker), patch("app.live_smoke_test._current_equity", return_value=263_000):
            result = await run_one_shot_live_smoke_test(confirmation=CONFIRMATION_PHRASE)

        self.assertEqual(result["smoke_test_status"], "ABORTED")
        self.assertIn("LIVE_SMOKE_TEST_DISABLED", result["pass_fail_reasons"])
        broker.place_order.assert_not_awaited()
        with database.get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) AS count FROM live_order_logs").fetchone()["count"]
        self.assertEqual(count, 0)

    async def test_existing_smoke_test_can_pass_after_fee_and_duplicate_recalc(self) -> None:
        buy_uuid = "C0101000003129835396"
        sell_uuid = "C0101000003129835406"
        self._insert_smoke_order(
            request_id="smoke-buy",
            client_order_id="smoke-20260626T075434-3f-buy",
            order_uuid=buy_uuid,
            side="BUY",
            paid_fee=14.99,
            executed_volume=0.00005,
            filled_amount_krw=29980,
            trades=[
                {"uuid": "buy-fill-1", "price": "599600000", "volume": "0.000025", "funds": "14990", "created_at": "2026-06-26T16:54:35+09:00"},
                {"uuid": "buy-fill-2", "price": "599600000", "volume": "0.000025", "funds": "14990", "created_at": "2026-06-26T16:54:36+09:00"},
            ],
        )
        self._insert_smoke_order(
            request_id="smoke-sell",
            client_order_id="smoke-20260626T075434-3f-sell",
            order_uuid=sell_uuid,
            side="SELL",
            paid_fee=14.99,
            executed_volume=0.00005,
            filled_amount_krw=29980,
            trades=[
                {"uuid": "sell-fill-1", "price": "599600000", "volume": "0.00005", "funds": "29980", "created_at": "2026-06-26T16:54:39+09:00"},
            ],
        )
        database.insert_smoke_test_run(
            {
                "smoke_test_id": "smoke-20260626T075434-3f09c9",
                "exchange_name": "bithumb",
                "symbol": "BTC",
                "market": "KRW-BTC",
                "status": "PARTIAL",
                "started_at_utc": "2026-06-26T07:54:34Z",
                "completed_at_utc": "2026-06-26T07:54:40Z",
                "max_notional_krw": 6000,
                "report": {
                    "smoke_test_id": "smoke-20260626T075434-3f09c9",
                    "smoke_test_status": "PARTIAL",
                    "buy_order_filled": True,
                    "sell_order_filled": True,
                    "exchange_order_uuid_list": [buy_uuid, sell_uuid],
                    "equity_diff_after": 0,
                    "current_epoch_accounting_pending_count": 0,
                    "current_epoch_accounting_failed_count": 0,
                    "duplicate_fill_count": 2,
                    "fee_diff": -14.99,
                },
            }
        )

        result = recalculate_smoke_test_report("smoke-20260626T075434-3f09c9")
        report = result["report"]

        self.assertTrue(result["ok"])
        self.assertEqual(result["recalculated_status"], "PASSED_AFTER_RECALC")
        self.assertEqual(report["exchange_fill_count"], 3)
        self.assertEqual(report["ledger_fill_count"], 3)
        self.assertEqual(report["missing_ledger_fill_count"], 0)
        self.assertEqual(report["duplicate_fill_count"], 0)
        self.assertAlmostEqual(report["fee_from_exchange"], 29.98)
        self.assertAlmostEqual(report["fee_from_ledger"], 29.98)
        self.assertAlmostEqual(report["fee_diff"], 0.0)
        self.assertEqual(report["equity_diff_after"], 0)

    def _insert_smoke_order(
        self,
        *,
        request_id: str,
        client_order_id: str,
        order_uuid: str,
        side: str,
        paid_fee: float,
        executed_volume: float,
        filled_amount_krw: float,
        trades: list[dict],
    ) -> None:
        database.insert_live_order_log(
            {
                "request_id": request_id,
                "client_order_id": client_order_id,
                "idempotency_key": f"test:{request_id}",
                "exchange": "bithumb",
                "market": "KRW-BTC",
                "side": side,
                "order_type": "LIMIT",
                "price": filled_amount_krw / executed_volume,
                "volume": executed_volume,
                "amount_krw": filled_amount_krw,
                "fee_estimate": paid_fee,
                "risk_result": "SMOKE_TEST_FILLED",
                "order_preview_payload": {},
                "exchange_request_payload_masked": {},
                "exchange_response_payload": {
                    "uuid": order_uuid,
                    "client_order_id": client_order_id,
                    "market": "KRW-BTC",
                    "side": "bid" if side == "BUY" else "ask",
                    "price": str(filled_amount_krw / executed_volume),
                    "executed_volume": str(executed_volume),
                    "executed_funds": str(filled_amount_krw),
                    "paid_fee": str(paid_fee),
                    "created_at": "2026-06-26T16:54:34+09:00",
                    "trades": trades,
                },
                "status": "FILLED",
                "order_uuid": order_uuid,
                "executed_volume": executed_volume,
                "remaining_volume": 0,
                "filled_amount_krw": filled_amount_krw,
                "paid_fee": paid_fee,
                "order_purpose": "SMOKE_TEST",
                "strategy_name": "smoke_test",
                "signal_type": side,
                "manual_confirmed": True,
            }
        )


if __name__ == "__main__":
    unittest.main()
