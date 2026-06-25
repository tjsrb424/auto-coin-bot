from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.capital_snapshot import build_capital_snapshot_async
from app.database import get_connection, insert_live_recovery_event
from app.live_broker import _balance_amount, get_live_broker


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _float(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict]:
    with get_connection() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _row(sql: str, params: tuple[Any, ...] = ()) -> dict | None:
    rows = _rows(sql, params)
    return rows[0] if rows else None


async def _exchange_total(exchange: str, market: str) -> dict:
    symbol = str(market or "").split("-")[-1].upper()
    balances = await get_live_broker(exchange).get_balances()
    by_currency = balances.get("by_currency") or {}
    item = by_currency.get(symbol) or {}
    return {
        "symbol": symbol,
        "available": _float(item.get("balance")),
        "locked": _float(item.get("locked")),
        "total": _balance_amount(balances, symbol),
        "raw": item,
    }


def _position_slot(position_id: int) -> dict | None:
    return _row(
        """
        SELECT *
        FROM position_slots
        WHERE live_position_id = ?
           OR entry_order_uuid = (SELECT entry_order_uuid FROM live_positions WHERE id = ?)
        ORDER BY id
        LIMIT 1
        """,
        (position_id, position_id),
    )


def _target_state(position_id: int, order_uuid: str) -> dict:
    position = _row("SELECT * FROM live_positions WHERE id = ?", (position_id,))
    if not position:
        raise ValueError(f"POSITION_NOT_FOUND:{position_id}")
    if str(position.get("entry_order_uuid") or "") != order_uuid:
        raise ValueError(f"POSITION_ORDER_UUID_MISMATCH:{position.get('entry_order_uuid')}:{order_uuid}")
    entry_event = _row(
        "SELECT * FROM position_fill_events WHERE position_id = ? AND order_uuid = ? AND fill_type = 'ENTRY'",
        (position_id, order_uuid),
    )
    if not entry_event:
        raise ValueError(f"ENTRY_FILL_EVENT_NOT_FOUND:{position_id}:{order_uuid}")
    scale_events = _rows(
        "SELECT * FROM position_fill_events WHERE position_id = ? AND order_uuid = ? AND fill_type = 'SCALE_IN' ORDER BY id",
        (position_id, order_uuid),
    )
    logs = _rows("SELECT * FROM live_order_logs WHERE order_uuid = ? ORDER BY id", (order_uuid,))
    slot = _position_slot(position_id)
    duplicate_sessions = _rows(
        """
        SELECT *
        FROM live_strategy_sessions
        WHERE current_position_id = ?
          AND id != COALESCE(?, 0)
        ORDER BY id
        """,
        (position_id, int((slot or {}).get("live_strategy_session_id") or 0)),
    )
    return {
        "position": position,
        "slot": slot,
        "entry_event": entry_event,
        "scale_events": scale_events,
        "logs": logs,
        "duplicate_sessions": duplicate_sessions,
    }


async def repair_duplicate_order_uuid_application(
    *,
    position_id: int,
    order_uuid: str,
    exchange: str = "bithumb",
    dry_run: bool = True,
) -> dict:
    state = _target_state(position_id, order_uuid)
    position = state["position"]
    market = str(position["market"])
    entry_event = state["entry_event"]
    scale_events = state["scale_events"]
    expected_volume = _float(entry_event.get("applied_volume"))
    expected_amount = _float(entry_event.get("applied_amount_krw"))
    expected_fee = _float(entry_event.get("applied_fee"))
    current_price = _float(position.get("current_price") or position.get("entry_price"))
    expected_price = expected_amount / expected_volume if expected_volume > 0 else _float(position.get("entry_price"))
    expected_value = expected_volume * current_price
    expected_unrealized = expected_value - expected_amount
    exchange_balance = await _exchange_total(exchange, market)
    db_open_volume_before = _float(
        (_row(
            """
            SELECT SUM(entry_volume) AS volume
            FROM live_positions
            WHERE exchange = ?
              AND market = ?
              AND status IN ('OPEN', 'CLOSING', 'EXIT_PENDING', 'EXIT_CANDIDATE', 'MANUAL_REVIEW_REQUIRED')
            """,
            (exchange, market),
        )
        or {}).get("volume")
    )
    proposed_updates = []
    if abs(_float(position.get("entry_volume")) - expected_volume) > 0.000000000001 or abs(_float(position.get("entry_amount_krw")) - expected_amount) > 0.000001:
        proposed_updates.append(
            {
                "table": "live_positions",
                "id": position_id,
                "set": {
                    "entry_volume": expected_volume,
                    "entry_amount_krw": expected_amount,
                    "entry_price": expected_price,
                    "current_price": current_price,
                    "unrealized_pnl": expected_unrealized,
                    "scale_in_count": 0,
                    "last_scale_in_at": None,
                },
            }
        )
    for event in scale_events:
        if _float(event.get("applied_volume")) or _float(event.get("applied_amount_krw")) or _float(event.get("applied_fee")):
            proposed_updates.append(
                {
                    "table": "position_fill_events",
                    "id": int(event["id"]),
                    "set": {
                        "source": "DUPLICATE_ORDER_UUID_ALREADY_APPLIED_REPAIRED",
                        "applied_volume": 0.0,
                        "applied_amount_krw": 0.0,
                        "applied_fee": 0.0,
                    },
                }
            )
    if state["slot"] and (
        abs(_float(state["slot"].get("allocated_krw")) - expected_amount) > 0.000001
        or abs(_float(state["slot"].get("current_value_krw")) - expected_value) > 0.000001
        or abs(_float(state["slot"].get("unrealized_pnl")) - expected_unrealized) > 0.000001
        or abs(_float(state["slot"].get("reserved_krw"))) > 0.000001
    ):
        proposed_updates.append(
            {
                "table": "position_slots",
                "id": int(state["slot"]["id"]),
                "set": {
                    "allocated_krw": expected_amount,
                    "current_value_krw": expected_value,
                    "unrealized_pnl": expected_unrealized,
                    "reserved_krw": 0.0,
                },
            }
        )
    ledger = _row("SELECT * FROM order_application_ledger WHERE order_uuid = ?", (order_uuid,))
    if not ledger or abs(_float(ledger.get("applied_volume")) - expected_volume) > 0.000000000001:
        proposed_updates.append(
            {
                "table": "order_application_ledger",
                "order_uuid": order_uuid,
                "set": {
                    "position_id": position_id,
                    "fill_type": "ENTRY",
                    "applied_volume": expected_volume,
                    "applied_amount_krw": expected_amount,
                    "applied_fee": expected_fee,
                    "last_exchange_executed_volume": expected_volume,
                    "last_exchange_filled_amount_krw": expected_amount,
                    "last_exchange_paid_fee": expected_fee,
                },
            }
        )
    for session in state["duplicate_sessions"]:
        if not session.get("current_open_order_uuid"):
            proposed_updates.append(
                {
                    "table": "live_strategy_sessions",
                    "id": int(session["id"]),
                    "set": {
                        "status": "STOPPED",
                        "auto_enabled": False,
                        "current_position_id": None,
                        "last_risk_result": "DUPLICATE_ORDER_UUID_REPAIR_SESSION_POINTER_CLEARED",
                        "last_order_status": "REPLACED",
                    },
                }
            )

    result = {
        "ok": True,
        "dry_run": dry_run,
        "exchange": exchange,
        "market": market,
        "position_id": position_id,
        "order_uuid": order_uuid,
        "entry_event": entry_event,
        "scale_events": scale_events,
        "position_before": position,
        "slot_before": state["slot"],
        "duplicate_sessions": state["duplicate_sessions"],
        "exchange_balance": exchange_balance,
        "db_open_volume_before": db_open_volume_before,
        "expected": {
            "entry_volume": expected_volume,
            "entry_amount_krw": expected_amount,
            "entry_price": expected_price,
            "current_value_krw": expected_value,
            "unrealized_pnl": expected_unrealized,
        },
        "proposed_updates": proposed_updates,
        "applied": False,
    }
    if dry_run or not proposed_updates:
        return result

    now = _utc_now()
    applied_updates: list[dict] = []
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE live_positions
            SET entry_volume = ?,
                entry_amount_krw = ?,
                entry_price = ?,
                current_price = ?,
                unrealized_pnl = ?,
                scale_in_count = 0,
                last_scale_in_at = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (expected_volume, expected_amount, expected_price, current_price, expected_unrealized, now, position_id),
        )
        applied_updates.append({"live_positions": position_id})
        for event in scale_events:
            conn.execute(
                """
                UPDATE position_fill_events
                SET source = 'DUPLICATE_ORDER_UUID_ALREADY_APPLIED_REPAIRED',
                    applied_volume = 0,
                    applied_amount_krw = 0,
                    applied_fee = 0
                WHERE id = ?
                """,
                (int(event["id"]),),
            )
            applied_updates.append({"position_fill_events": int(event["id"])})
        if state["slot"]:
            conn.execute(
                """
                UPDATE position_slots
                SET allocated_krw = ?,
                    reserved_krw = 0,
                    current_value_krw = ?,
                    unrealized_pnl = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (expected_amount, expected_value, expected_unrealized, now, int(state["slot"]["id"])),
            )
            applied_updates.append({"position_slots": int(state["slot"]["id"])})
        conn.execute(
            """
            INSERT INTO order_application_ledger (
                order_uuid, position_id, fill_type, side, order_purpose,
                applied_volume, applied_amount_krw, applied_fee,
                last_exchange_executed_volume, last_exchange_filled_amount_krw,
                last_exchange_paid_fee, source, order_log_id, request_id,
                created_at, updated_at
            ) VALUES (?, ?, 'ENTRY', 'BUY', 'ENTRY', ?, ?, ?, ?, ?, ?, 'DUPLICATE_ORDER_UUID_REPAIR', ?, ?, ?, ?)
            ON CONFLICT(order_uuid) DO UPDATE SET
                position_id = excluded.position_id,
                fill_type = 'ENTRY',
                applied_volume = excluded.applied_volume,
                applied_amount_krw = excluded.applied_amount_krw,
                applied_fee = excluded.applied_fee,
                last_exchange_executed_volume = excluded.last_exchange_executed_volume,
                last_exchange_filled_amount_krw = excluded.last_exchange_filled_amount_krw,
                last_exchange_paid_fee = excluded.last_exchange_paid_fee,
                source = excluded.source,
                order_log_id = excluded.order_log_id,
                request_id = excluded.request_id,
                updated_at = excluded.updated_at
            """,
            (
                order_uuid,
                position_id,
                expected_volume,
                expected_amount,
                expected_fee,
                expected_volume,
                expected_amount,
                expected_fee,
                entry_event.get("order_log_id"),
                entry_event.get("request_id"),
                now,
                now,
            ),
        )
        applied_updates.append({"order_application_ledger": order_uuid})
        for session in state["duplicate_sessions"]:
            if session.get("current_open_order_uuid"):
                continue
            conn.execute(
                """
                UPDATE live_strategy_sessions
                SET status = 'STOPPED',
                    auto_enabled = 0,
                    current_position_id = NULL,
                    current_open_order_uuid = NULL,
                    last_risk_result = 'DUPLICATE_ORDER_UUID_REPAIR_SESSION_POINTER_CLEARED',
                    last_order_status = 'REPLACED',
                    stopped_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, now, int(session["id"])),
            )
            applied_updates.append({"live_strategy_sessions": int(session["id"])})
    after_snapshot = await build_capital_snapshot_async(exchange)
    after_volume = _float(
        (_row(
            """
            SELECT SUM(entry_volume) AS volume
            FROM live_positions
            WHERE exchange = ?
              AND market = ?
              AND status IN ('OPEN', 'CLOSING', 'EXIT_PENDING', 'EXIT_CANDIDATE', 'MANUAL_REVIEW_REQUIRED')
            """,
            (exchange, market),
        )
        or {}).get("volume")
    )
    insert_live_recovery_event(
        {
            "event_type": "DUPLICATE_ORDER_UUID_POSITION_REPAIR_APPLIED",
            "severity": "ERROR",
            "exchange": exchange,
            "market": market,
            "session_id": int(position.get("session_id") or 0) or None,
            "request_id": entry_event.get("request_id"),
            "order_uuid": order_uuid,
            "message": "Duplicate BUY order_uuid application was repaired; position volume was reset to the ENTRY fill.",
            "payload": {
                "position_id": position_id,
                "applied_updates": applied_updates,
                "db_open_volume_before": db_open_volume_before,
                "db_open_volume_after": after_volume,
                "exchange_balance": exchange_balance,
                "capital_snapshot_balance_mismatch_detected": after_snapshot.get("balance_mismatch_detected"),
            },
        }
    )
    result.update(
        {
            "applied": True,
            "applied_updates": applied_updates,
            "db_open_volume_after": after_volume,
            "capital_snapshot_after": {
                "balance_mismatch_detected": after_snapshot.get("balance_mismatch_detected"),
                "open_order_mismatch_detected": after_snapshot.get("open_order_mismatch_detected"),
                "warnings": after_snapshot.get("warnings"),
                "blockers": after_snapshot.get("blockers"),
                "created_at": after_snapshot.get("created_at"),
            },
        }
    )
    return result


def repair_duplicate_order_uuid_application_sync(
    *,
    position_id: int,
    order_uuid: str,
    exchange: str = "bithumb",
    dry_run: bool = True,
) -> dict:
    return asyncio.run(
        repair_duplicate_order_uuid_application(
            position_id=position_id,
            order_uuid=order_uuid,
            exchange=exchange,
            dry_run=dry_run,
        )
    )
