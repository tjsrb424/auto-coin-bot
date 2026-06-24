from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from app.database import (
    get_connection,
    insert_live_recovery_event,
    mark_rebalance_delta_accumulators,
    update_live_position,
    update_live_strategy_session,
)
from app.live_broker import _available_balance, _balance_amount, get_live_broker


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _float(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _symbol(market: str) -> str:
    return str(market or "").split("-")[-1].upper()


def _json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "{}")
    except Exception:
        return {}


def _scale_target_from_logs(logs: list[dict]) -> int | None:
    for log in logs:
        preview = _json(log.get("order_preview_payload"))
        policy_preview = preview.get("policy_preview") if isinstance(preview, dict) else {}
        scale_preview = (policy_preview or {}).get("scale_in") if isinstance(policy_preview, dict) else {}
        if isinstance(scale_preview, dict) and scale_preview.get("scale_in") and scale_preview.get("position_id"):
            return int(scale_preview["position_id"])
    return None


async def _exchange_balance(exchange: str, market: str) -> dict:
    symbol = _symbol(market)
    try:
        balances = await get_live_broker(exchange).get_balances()
        available = _available_balance(balances, symbol)
        total = _balance_amount(balances, symbol)
        return {
            "ok": True,
            "currency": symbol,
            "available": available,
            "locked": max(total - available, 0.0),
            "total": total,
            "raw": balances.get("by_currency", {}).get(symbol, {}),
        }
    except Exception as exc:
        return {"ok": False, "currency": symbol, "error": str(exc), "available": None, "locked": None, "total": None}


def _rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict]:
    with get_connection() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _duplicate_groups(exchange: str, market: str) -> list[dict]:
    groups = _rows(
        """
        SELECT entry_order_uuid AS order_uuid, COUNT(*) AS position_count
        FROM live_positions
        WHERE exchange = ?
          AND market = ?
          AND entry_order_uuid IS NOT NULL
          AND entry_order_uuid != ''
        GROUP BY entry_order_uuid
        HAVING COUNT(*) > 1
        ORDER BY MAX(updated_at) DESC
        """,
        (exchange, market),
    )
    return groups


def _open_volume(exchange: str, market: str) -> dict:
    rows = _rows(
        """
        SELECT id, status, entry_volume, current_price
        FROM live_positions
        WHERE exchange = ?
          AND market = ?
          AND status IN ('OPEN', 'EXIT_CANDIDATE', 'EXIT_PENDING', 'CLOSING', 'MANUAL_REVIEW_REQUIRED')
        ORDER BY id
        """,
        (exchange, market),
    )
    volume = sum(_float(row.get("entry_volume")) for row in rows)
    value = sum(_float(row.get("entry_volume")) * _float(row.get("current_price")) for row in rows)
    return {"volume": volume, "value_krw": value, "positions": rows}


def _proposed_duplicate_updates(group: dict, exchange_balance: dict) -> dict:
    order_uuid = str(group["order_uuid"])
    logs = _rows("SELECT * FROM live_order_logs WHERE order_uuid = ? ORDER BY id ASC", (order_uuid,))
    positions = _rows("SELECT * FROM live_positions WHERE entry_order_uuid = ? ORDER BY id ASC", (order_uuid,))
    sell_logs = _rows(
        """
        SELECT *
        FROM live_order_logs
        WHERE order_purpose = 'EXIT'
          AND position_id IN (
              SELECT id FROM live_positions WHERE entry_order_uuid = ?
          )
        ORDER BY id ASC
        """,
        (order_uuid,),
    )
    target_position_id = _scale_target_from_logs(logs)
    duplicate_positions = [pos for pos in positions if not target_position_id or int(pos["id"]) != int(target_position_id)]
    open_duplicates = [pos for pos in duplicate_positions if str(pos.get("status") or "").upper() in {"OPEN", "EXIT_CANDIDATE", "EXIT_PENDING", "CLOSING", "MANUAL_REVIEW_REQUIRED"}]
    buy_volume = max((_float(log.get("executed_volume")) for log in logs if str(log.get("side")).upper() == "BUY"), default=0.0)
    buy_amount = max((_float(log.get("filled_amount_krw")) for log in logs if str(log.get("side")).upper() == "BUY"), default=0.0)
    sell_volume = sum(_float(log.get("executed_volume")) for log in sell_logs)
    proposed_updates = []
    for pos in open_duplicates:
        proposed_updates.append(
            {
                "table": "live_positions",
                "id": pos["id"],
                "set": {
                    "status": "DUPLICATE_RECONCILED",
                    "closed_at": _utc_now(),
                    "unrealized_pnl": 0.0,
                },
                "reason": "duplicate scale-in entry_order_uuid; not the target position",
            }
        )
    if target_position_id:
        for log in logs:
            if str(log.get("side") or "").upper() == "BUY" and str(log.get("order_purpose") or "ENTRY").upper() == "ENTRY":
                if int(log.get("position_id") or 0) != int(target_position_id):
                    proposed_updates.append(
                        {
                            "table": "live_order_logs",
                            "id": log["id"],
                            "request_id": log["request_id"],
                            "set": {"position_id": int(target_position_id)},
                            "reason": "attach duplicate scale-in BUY log to target position",
                        }
                    )
        existing_fill_event = _rows(
            "SELECT id FROM position_fill_events WHERE order_uuid = ? AND fill_type = 'SCALE_IN' LIMIT 1",
            (order_uuid,),
        )
        if not existing_fill_event:
            proposed_updates.append(
                {
                    "table": "position_fill_events",
                    "order_uuid": order_uuid,
                    "set": {
                        "position_id": int(target_position_id),
                        "fill_type": "SCALE_IN",
                        "applied_volume": 0.0,
                        "applied_amount_krw": 0.0,
                    },
                    "reason": "idempotency guard; duplicate BUY was already netted by later SELL",
                }
            )
    active_sessions = _rows(
        """
        SELECT *
        FROM live_strategy_sessions
        WHERE exchange = ?
          AND market = ?
          AND current_position_id IN (
              SELECT id FROM live_positions
              WHERE status = 'CLOSED'
                 OR entry_order_uuid = ?
          )
        ORDER BY id ASC
        """,
        (group.get("exchange", "bithumb"), group.get("market", "KRW-BTC"), order_uuid),
    )
    return {
        "duplicate_entry_order_uuid": order_uuid,
        "target_scale_in_position_id": target_position_id,
        "affected_live_order_logs": logs,
        "affected_positions": positions,
        "sell_logs": sell_logs,
        "buy_fill": {"executed_volume": buy_volume, "filled_amount_krw": buy_amount},
        "sell_fill_after_buy": {"executed_volume": sell_volume},
        "duplicate_positions_to_close_or_reconcile": open_duplicates,
        "sessions_to_repoint": active_sessions,
        "exchange_actual_balance": exchange_balance,
        "proposed_updates": proposed_updates,
    }


async def repair_scale_in_duplicate(
    *,
    exchange: str = "bithumb",
    market: str = "KRW-XLM",
    dry_run: bool = True,
) -> dict:
    before = _open_volume(exchange, market)
    exchange_balance = await _exchange_balance(exchange, market)
    groups = _duplicate_groups(exchange, market)
    previews = [_proposed_duplicate_updates({**group, "exchange": exchange, "market": market}, exchange_balance) for group in groups]
    stale_accumulators = _rows(
        """
        SELECT a.*
        FROM rebalance_delta_accumulators a
        LEFT JOIN live_strategy_sessions s ON s.id = a.session_id
        WHERE a.exchange = ?
          AND a.market = ?
          AND a.side = 'ASK'
          AND a.status = 'ACCUMULATING'
          AND (s.id IS NULL OR s.status NOT IN ('READY', 'RUNNING'))
        ORDER BY a.id ASC
        """,
        (exchange, market),
    )
    result = {
        "ok": True,
        "dry_run": dry_run,
        "exchange": exchange,
        "market": market,
        "duplicate_groups": previews,
        "exchange_actual_balance": exchange_balance,
        "db_open_volume_before": before,
        "inactive_ask_accumulators_to_stale": stale_accumulators,
        "applied": False,
    }
    if dry_run:
        return result

    applied_updates: list[dict] = []
    now = _utc_now()
    with get_connection() as conn:
        for preview in previews:
            for pos in preview["duplicate_positions_to_close_or_reconcile"]:
                conn.execute(
                    """
                    UPDATE live_positions
                    SET status = 'DUPLICATE_RECONCILED',
                        unrealized_pnl = 0,
                        closed_at = COALESCE(closed_at, ?),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now, now, int(pos["id"])),
                )
                applied_updates.append({"live_positions": int(pos["id"]), "status": "DUPLICATE_RECONCILED"})
            target_position_id = preview.get("target_scale_in_position_id")
            if target_position_id:
                for log in preview["affected_live_order_logs"]:
                    if str(log.get("side") or "").upper() == "BUY" and str(log.get("order_purpose") or "ENTRY").upper() == "ENTRY":
                        conn.execute(
                            """
                            UPDATE live_order_logs
                            SET position_id = ?,
                                updated_at = ?
                            WHERE request_id = ?
                            """,
                            (int(target_position_id), now, str(log["request_id"])),
                        )
                        applied_updates.append({"live_order_logs": int(log["id"]), "position_id": int(target_position_id)})
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO position_fill_events (
                        order_uuid, position_id, fill_type, source, order_log_id, request_id,
                        applied_volume, applied_amount_krw, applied_fee, applied_at, created_at
                    ) VALUES (?, ?, 'SCALE_IN', 'SCALE_IN_DUPLICATE_REPAIR_NETTED_BY_EXIT', ?, ?, 0, 0, 0, ?, ?)
                    """,
                    (
                        preview["duplicate_entry_order_uuid"],
                        int(target_position_id),
                        preview["affected_live_order_logs"][0]["id"] if preview["affected_live_order_logs"] else None,
                        preview["affected_live_order_logs"][0]["request_id"] if preview["affected_live_order_logs"] else None,
                        now,
                        now,
                    ),
                )
                applied_updates.append(
                    {
                        "position_fill_events": preview["duplicate_entry_order_uuid"],
                        "fill_type": "SCALE_IN",
                        "inserted": cursor.rowcount > 0,
                    }
                )
                conn.execute(
                    """
                    UPDATE live_strategy_sessions
                    SET current_position_id = ?,
                        current_open_order_uuid = NULL,
                        last_risk_result = 'SCALE_IN_DUPLICATE_REPAIRED',
                        updated_at = ?
                    WHERE exchange = ?
                      AND market = ?
                      AND status IN ('READY', 'RUNNING', 'PAUSED', 'STOPPED', 'LIVE_PAUSED')
                      AND (
                          current_position_id IS NULL
                          OR current_position_id IN (
                              SELECT id FROM live_positions WHERE entry_order_uuid = ?
                          )
                          OR current_position_id IN (
                              SELECT id FROM live_positions WHERE status = 'CLOSED'
                          )
                      )
                    """,
                    (int(target_position_id), now, exchange, market, preview["duplicate_entry_order_uuid"]),
                )
                applied_updates.append({"live_strategy_sessions": "repointed", "current_position_id": int(target_position_id)})

    stale_count = 0
    for acc in stale_accumulators:
        stale_count += mark_rebalance_delta_accumulators(
            session_id=int(acc["session_id"]),
            candidate_strategy_id=acc.get("candidate_strategy_id"),
            exchange=exchange,
            market=market,
            side=str(acc["side"]),
            status="STALE",
            metadata={**_json(acc.get("metadata_json")), "stale_reason": "INACTIVE_SESSION_REPAIR", "repaired_at": now},
        )
    after = _open_volume(exchange, market)
    insert_live_recovery_event(
        {
            "event_type": "SCALE_IN_DUPLICATE_REPAIR_APPLIED",
            "severity": "WARNING",
            "exchange": exchange,
            "market": market,
            "message": "Scale-in duplicate position repair was applied.",
            "payload": {
                "duplicate_groups": [preview["duplicate_entry_order_uuid"] for preview in previews],
                "applied_updates": applied_updates,
                "stale_accumulators": stale_count,
                "db_open_volume_before": before,
                "db_open_volume_after": after,
                "exchange_actual_balance": exchange_balance,
            },
        }
    )
    result.update(
        {
            "applied": True,
            "applied_updates": applied_updates,
            "stale_accumulators_marked": stale_count,
            "db_open_volume_after": after,
        }
    )
    return result


def repair_scale_in_duplicate_sync(*, exchange: str = "bithumb", market: str = "KRW-XLM", dry_run: bool = True) -> dict:
    return asyncio.run(repair_scale_in_duplicate(exchange=exchange, market=market, dry_run=dry_run))
