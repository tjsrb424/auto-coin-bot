from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from app.database import (
    create_live_position,
    insert_live_recovery_event,
    insert_position_fill_event,
    load_live_order_logs_by_uuid,
    load_live_position,
    load_live_position_by_entry_order_uuid,
    load_open_live_position_for_strategy,
    load_position_fill_event,
    update_live_order_log,
    update_live_position,
    update_live_strategy_session,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _float(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _order_uuid(order_log: dict | None, raw_order: dict | None, session: dict | None) -> str:
    return str(
        (order_log or {}).get("order_uuid")
        or (raw_order or {}).get("uuid")
        or (raw_order or {}).get("order_id")
        or (raw_order or {}).get("id")
        or (session or {}).get("current_open_order_uuid")
        or ""
    )


def _scale_preview(log: dict | None) -> dict:
    if not log:
        return {}
    preview = log.get("order_preview_payload") or {}
    policy_preview = preview.get("policy_preview") if isinstance(preview, dict) else {}
    scale_preview = (policy_preview or {}).get("scale_in") if isinstance(policy_preview, dict) else {}
    return scale_preview if isinstance(scale_preview, dict) else {}


def _canonical_log(order_log: dict | None, order_uuid: str) -> tuple[dict | None, list[dict]]:
    logs = load_live_order_logs_by_uuid(order_uuid) if order_uuid else []
    if not logs:
        return order_log, [order_log] if order_log else []
    with_scale = [log for log in logs if _scale_preview(log).get("scale_in")]
    if with_scale:
        return with_scale[0], logs
    return logs[0], logs


def _filled_price(order_log: dict | None, raw_order: dict | None) -> float:
    return _float((raw_order or {}).get("price")) or _float((order_log or {}).get("price"))


def _filled_volume(order_log: dict | None, raw_order: dict | None) -> float:
    return (
        _float((raw_order or {}).get("executed_volume"))
        or _float((raw_order or {}).get("volume"))
        or _float((order_log or {}).get("executed_volume"))
        or _float((order_log or {}).get("volume"))
    )


def _filled_amount(order_log: dict | None, raw_order: dict | None, price: float, volume: float) -> float:
    return (
        _float((raw_order or {}).get("executed_funds"))
        or _float((raw_order or {}).get("filled_amount_krw"))
        or _float((order_log or {}).get("filled_amount_krw"))
        or (price * volume)
    )


def _paid_fee(order_log: dict | None, raw_order: dict | None) -> float:
    return _float((raw_order or {}).get("paid_fee")) or _float((order_log or {}).get("paid_fee"))


def _attach_logs(logs: list[dict], position_id: int) -> None:
    seen: set[str] = set()
    for log in logs:
        request_id = str((log or {}).get("request_id") or "")
        if not request_id or request_id in seen:
            continue
        seen.add(request_id)
        update_live_order_log(request_id, {"position_id": position_id})


def _log_event(
    event_type: str,
    severity: str,
    message: str,
    *,
    order_log: dict | None,
    order_uuid: str,
    position_id: int | None = None,
    payload: dict | None = None,
) -> None:
    insert_live_recovery_event(
        {
            "event_type": event_type,
            "severity": severity,
            "exchange": (order_log or {}).get("exchange", "bithumb"),
            "market": (order_log or {}).get("market", "KRW-BTC"),
            "session_id": (order_log or {}).get("session_id"),
            "request_id": (order_log or {}).get("request_id"),
            "order_uuid": order_uuid,
            "message": message,
            "payload": {"position_id": position_id, **(payload or {})},
        }
    )


def _new_position_payload(
    session: dict,
    order_log: dict | None,
    order_uuid: str,
    price: float,
    volume: float,
    amount: float,
) -> dict:
    stop_loss_percent = _float(getattr(session.get("config"), "stop_loss_percent", None)) or _float(os.getenv("AUTO_STOP_LOSS_PERCENT", "0.7"))
    take_profit_percent = _float(getattr(session.get("config"), "take_profit_percent", None)) or _float(os.getenv("AUTO_TAKE_PROFIT_PERCENT", "1.0"))
    trailing_stop_pct = _float(os.getenv("AUTO_TRAILING_STOP_PERCENT", "0.7"))
    opened_at = str((order_log or {}).get("updated_at") or (order_log or {}).get("created_at") or _utc_now())
    return {
        "session_id": int(session["id"]),
        "exchange": session["exchange"],
        "market": session["market"],
        "candidate_strategy_id": int(session["candidate_strategy_id"]),
        "strategy_name": str(session.get("strategy_name") or (order_log or {}).get("strategy_name") or "live_strategy"),
        "status": "OPEN",
        "entry_order_uuid": order_uuid,
        "entry_price": price,
        "entry_volume": volume,
        "entry_amount_krw": amount,
        "current_price": price,
        "unrealized_pnl": 0.0,
        "realized_pnl": 0.0,
        "stop_loss_price": price * (1 - stop_loss_percent / 100) if price > 0 else 0.0,
        "take_profit_price": price * (1 + take_profit_percent / 100) if price > 0 else 0.0,
        "highest_price_since_entry": price,
        "trailing_stop_price": price * (1 - trailing_stop_pct / 100) if price > 0 and trailing_stop_pct > 0 else 0.0,
        "trailing_stop_pct": trailing_stop_pct,
        "last_trailing_update_at": _utc_now(),
        "opened_at": opened_at,
    }


def _resolve_scale_in_target(session: dict, canonical_log: dict | None, scale_preview: dict) -> int | None:
    position_id = scale_preview.get("position_id") or (canonical_log or {}).get("position_id")
    if position_id:
        position = load_live_position(int(position_id))
        if position and str(position.get("status") or "").upper() == "OPEN":
            return int(position["id"])
    if scale_preview.get("scale_in"):
        fallback = load_open_live_position_for_strategy(
            str(session.get("exchange") or (canonical_log or {}).get("exchange") or "bithumb"),
            str(session.get("market") or (canonical_log or {}).get("market") or "KRW-BTC"),
            int(session.get("candidate_strategy_id") or (canonical_log or {}).get("candidate_strategy_id") or 0),
        )
        if fallback:
            return int(fallback["id"])
    return None


def sync_filled_entry_order_to_position(
    order_log: dict | None,
    raw_order: dict | None,
    source: str,
    *,
    session: dict,
) -> dict:
    order_uuid = _order_uuid(order_log, raw_order, session)
    if not order_uuid:
        return {"status": "SKIPPED", "reason": "MISSING_ORDER_UUID"}
    canonical_log, logs = _canonical_log(order_log, order_uuid)
    if canonical_log and str(canonical_log.get("side") or "").upper() != "BUY":
        return {"status": "SKIPPED", "reason": "NOT_BUY"}
    if canonical_log and str(canonical_log.get("order_purpose") or "ENTRY").upper() != "ENTRY":
        return {"status": "SKIPPED", "reason": "NOT_ENTRY"}

    price = _filled_price(canonical_log, raw_order)
    volume = _filled_volume(canonical_log, raw_order)
    amount = _filled_amount(canonical_log, raw_order, price, volume)
    fee = _paid_fee(canonical_log, raw_order)
    if price <= 0 or volume <= 0 or amount <= 0:
        return {"status": "SKIPPED", "reason": "EMPTY_FILL"}

    scale_preview = _scale_preview(canonical_log)
    scale_target_id = _resolve_scale_in_target(session, canonical_log, scale_preview)
    fill_type = "SCALE_IN" if scale_target_id else "ENTRY"

    existing_event = load_position_fill_event(order_uuid, fill_type)
    if existing_event:
        position_id = int(existing_event["position_id"])
        _attach_logs(logs, position_id)
        update_live_strategy_session(
            int(session["id"]),
            {
                "current_open_order_uuid": None,
                "current_position_id": position_id,
                "last_order_status": "FILLED",
                "last_risk_result": "POSITION_FILL_ALREADY_SYNCED",
            },
        )
        return {"status": "ATTACHED", "position_id": position_id, "fill_type": fill_type, "idempotent": True}

    if scale_target_id:
        position = load_live_position(scale_target_id)
        if not position or str(position.get("status") or "").upper() != "OPEN":
            return {"status": "SKIPPED", "reason": "SCALE_TARGET_NOT_OPEN"}
        if not insert_position_fill_event(
            {
                "order_uuid": order_uuid,
                "position_id": scale_target_id,
                "fill_type": "SCALE_IN",
                "source": source,
                "order_log_id": (canonical_log or {}).get("id"),
                "request_id": (canonical_log or {}).get("request_id"),
                "applied_volume": volume,
                "applied_amount_krw": amount,
                "applied_fee": fee,
                "applied_at": str((canonical_log or {}).get("updated_at") or _utc_now()),
            }
        ):
            return sync_filled_entry_order_to_position(order_log, raw_order, source, session=session)
        previous_volume = _float(position.get("entry_volume"))
        previous_amount = _float(position.get("entry_amount_krw"))
        new_volume = previous_volume + volume
        new_amount = previous_amount + amount
        new_price = new_amount / new_volume if new_volume > 0 else price
        update_live_position(
            scale_target_id,
            {
                "status": "OPEN",
                "entry_price": new_price,
                "entry_volume": new_volume,
                "entry_amount_krw": new_amount,
                "current_price": price,
                "unrealized_pnl": (price * new_volume) - new_amount,
                "scale_in_count": int(position.get("scale_in_count") or 0) + 1,
                "last_scale_in_at": _utc_now(),
            },
        )
        _attach_logs(logs, scale_target_id)
        update_live_strategy_session(
            int(session["id"]),
            {
                "current_open_order_uuid": None,
                "current_position_id": scale_target_id,
                "last_order_status": "FILLED",
                "last_risk_result": "SCALE_IN_POSITION_SYNCED",
            },
        )
        _log_event(
            "SCALE_IN_POSITION_SYNCED",
            "INFO",
            "Filled scale-in entry order was merged into the existing live position.",
            order_log=canonical_log,
            order_uuid=order_uuid,
            position_id=scale_target_id,
            payload={"source": source, "applied_volume": volume, "applied_amount_krw": amount},
        )
        return {"status": "MERGED", "position_id": scale_target_id, "fill_type": "SCALE_IN", "idempotent": False}

    existing = load_live_position_by_entry_order_uuid(session["exchange"], session["market"], order_uuid)
    if existing:
        position_id = int(existing["id"])
        _attach_logs(logs, position_id)
        if load_position_fill_event(order_uuid, "ENTRY") is None:
            insert_position_fill_event(
                {
                    "order_uuid": order_uuid,
                    "position_id": position_id,
                    "fill_type": "ENTRY",
                    "source": f"{source}_ADOPT_EXISTING",
                    "order_log_id": (canonical_log or {}).get("id"),
                    "request_id": (canonical_log or {}).get("request_id"),
                    "applied_volume": _float(existing.get("entry_volume")),
                    "applied_amount_krw": _float(existing.get("entry_amount_krw")),
                    "applied_fee": fee,
                    "applied_at": str(existing.get("opened_at") or _utc_now()),
                }
            )
        update_live_strategy_session(
            int(session["id"]),
            {
                "current_open_order_uuid": None,
                "current_position_id": position_id,
                "last_order_status": "FILLED",
                "last_risk_result": "POSITION_OPEN_SYNCED",
            },
        )
        return {"status": "ATTACHED", "position_id": position_id, "fill_type": "ENTRY", "idempotent": True}

    position_id = create_live_position(_new_position_payload(session, canonical_log, order_uuid, price, volume, amount))
    insert_position_fill_event(
        {
            "order_uuid": order_uuid,
            "position_id": position_id,
            "fill_type": "ENTRY",
            "source": source,
            "order_log_id": (canonical_log or {}).get("id"),
            "request_id": (canonical_log or {}).get("request_id"),
            "applied_volume": volume,
            "applied_amount_krw": amount,
            "applied_fee": fee,
            "applied_at": str((canonical_log or {}).get("updated_at") or _utc_now()),
        }
    )
    _attach_logs(logs, position_id)
    update_live_strategy_session(
        int(session["id"]),
        {
            "current_open_order_uuid": None,
            "current_position_id": position_id,
            "last_order_status": "FILLED",
            "last_risk_result": "POSITION_OPEN_SYNCED",
        },
    )
    _log_event(
        "POSITION_OPEN_SYNCED",
        "INFO",
        "Filled entry order was synced to a live position.",
        order_log=canonical_log,
        order_uuid=order_uuid,
        position_id=position_id,
        payload={"source": source, "applied_volume": volume, "applied_amount_krw": amount},
    )
    return {"status": "CREATED", "position_id": position_id, "fill_type": "ENTRY", "idempotent": False}
