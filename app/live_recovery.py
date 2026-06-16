from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from app.database import (
    get_live_order_log_by_uuid,
    has_unresolved_live_order,
    insert_live_recovery_event,
    load_live_recovery_events,
    load_open_live_positions,
    load_reconcilable_live_order_logs,
    pause_running_auto_live_pilot_sessions_on_startup,
    pause_running_live_strategy_sessions_on_startup,
    update_live_order_log,
)
from app.live_broker import _balance_amount, get_live_broker

logger = logging.getLogger("uvicorn.error")

OPEN_ORDER_STATES = {"SUBMITTED", "WAITING", "PARTIALLY_FILLED"}
BALANCE_MISMATCH_VOLUME_TOLERANCE = 0.000001
BALANCE_MISMATCH_RELATIVE_TOLERANCE = 0.01


@dataclass(frozen=True)
class ReconciledOrderStatus:
    status: str
    executed_volume: float
    remaining_volume: float
    filled_amount_krw: float
    paid_fee: float
    raw: dict


def run_startup_live_recovery() -> dict:
    return asyncio.run(run_startup_live_recovery_async())


async def run_startup_live_recovery_async() -> dict:
    paused_auto = pause_running_auto_live_pilot_sessions_on_startup()
    paused_strategy = pause_running_live_strategy_sessions_on_startup()
    if paused_auto or paused_strategy:
        log_recovery_event(
            "SERVER_RESTART_LIVE_PAUSED",
            "WARNING",
            "Server restart moved running live sessions to LIVE_PAUSED. Manual resume is required.",
            payload={"auto_live_pilot_sessions": paused_auto, "live_strategy_sessions": paused_strategy},
        )

    try:
        sync_result = await sync_open_orders()
    except Exception as exc:
        sync_result = {"ok": False, "status": "FAILED", "message": str(exc)}
        log_recovery_event("API_ERROR", "ERROR", "Startup open order sync failed.", payload={"error": str(exc)})

    return {"paused_auto_sessions": paused_auto, "paused_strategy_sessions": paused_strategy, "open_order_sync": sync_result}


async def sync_open_orders(exchange: str = "bithumb", market: str = "KRW-BTC") -> dict:
    broker = get_live_broker(exchange)
    internal = load_reconcilable_live_order_logs(exchange, market)
    result = {
        "ok": True,
        "exchange": exchange,
        "market": market,
        "internal_open_count": len(internal),
        "exchange_open_count": 0,
        "reconciled_count": 0,
        "warnings": [],
    }
    try:
        open_response = await broker.list_open_orders(market)
        exchange_orders = _extract_orders(open_response)
        result["exchange_open_count"] = len(exchange_orders)
    except Exception as exc:
        log_recovery_event("API_ERROR", "ERROR", "Open order sync list_open_orders failed.", exchange=exchange, market=market, payload={"error": str(exc)})
        return {**result, "ok": False, "status": "FAILED", "message": str(exc)}

    exchange_by_uuid = {_order_uuid(order): order for order in exchange_orders if _order_uuid(order)}
    exchange_by_client_id = {_client_order_id(order): order for order in exchange_orders if _client_order_id(order)}

    for log in internal:
        exchange_order = None
        if log.get("order_uuid"):
            exchange_order = exchange_by_uuid.get(str(log["order_uuid"]))
        if exchange_order is None:
            exchange_order = exchange_by_client_id.get(str(log["request_id"])[:36])
        if exchange_order is not None:
            apply_reconciled_order_status(log, normalize_exchange_order(exchange_order), "OPEN_ORDER_SYNC")
            result["reconciled_count"] += 1
            continue
        if log.get("order_uuid"):
            await reconcile_order_log(log, source="OPEN_ORDER_SYNC_MISSING")
            result["reconciled_count"] += 1
        else:
            update_live_order_log(
                str(log["request_id"]),
                {
                    "status": "SUBMITTED",
                    "risk_result": "ORDER_STATUS_UNKNOWN_TIMEOUT",
                    "error_message": "Order status is unknown; blocked until exchange reconciliation succeeds.",
                },
            )
            log_recovery_event(
                "ORDER_STATUS_UNKNOWN_TIMEOUT",
                "ERROR",
                "Internal order has no exchange uuid and must not be retried.",
                exchange=exchange,
                market=market,
                session_id=log.get("session_id"),
                request_id=log.get("request_id"),
                payload={"status": log.get("status")},
            )

    for order_uuid, exchange_order in exchange_by_uuid.items():
        if get_live_order_log_by_uuid(order_uuid) is None:
            warning = f"Exchange open order {order_uuid} is missing from internal LiveOrderLog."
            result["warnings"].append(warning)
            log_recovery_event(
                "EXCHANGE_OPEN_ORDER_NOT_IN_DB",
                "WARNING",
                warning,
                exchange=exchange,
                market=market,
                order_uuid=order_uuid,
                payload={"exchange_order": _safe_order_payload(exchange_order)},
            )

    result["status"] = "SUCCESS"
    return result


async def reconcile_order_log(log: dict, source: str = "ORDER_STATUS_RECONCILIATION") -> ReconciledOrderStatus | None:
    exchange = str(log.get("exchange") or "bithumb")
    broker = get_live_broker(exchange)
    order_uuid = str(log.get("order_uuid") or "")
    if not order_uuid:
        log_recovery_event(
            "ORDER_STATUS_UNKNOWN_TIMEOUT",
            "ERROR",
            "Cannot fetch order status without exchange uuid. New orders remain blocked.",
            exchange=exchange,
            market=str(log.get("market") or "KRW-BTC"),
            session_id=log.get("session_id"),
            request_id=log.get("request_id"),
            payload={"status": log.get("status")},
        )
        return None
    try:
        raw_status = await broker.get_order(order_uuid)
    except Exception as exc:
        log_recovery_event(
            "API_ERROR",
            "ERROR",
            "Order status fetch failed during reconciliation.",
            exchange=exchange,
            market=str(log.get("market") or "KRW-BTC"),
            session_id=log.get("session_id"),
            request_id=log.get("request_id"),
            order_uuid=order_uuid,
            payload={"error": str(exc)},
        )
        raise
    reconciled = normalize_exchange_order(raw_status)
    apply_reconciled_order_status(log, reconciled, source)
    return reconciled


def apply_reconciled_order_status(log: dict, status: ReconciledOrderStatus, source: str) -> None:
    update_live_order_log(
        str(log["request_id"]),
        {
            "status": status.status,
            "risk_result": _risk_result_for_status(status.status, str(log.get("risk_result") or "ALLOWED")),
            "exchange_response_payload": status.raw,
            "order_uuid": _order_uuid(status.raw) or log.get("order_uuid"),
            "executed_volume": status.executed_volume,
            "remaining_volume": status.remaining_volume,
            "filled_amount_krw": status.filled_amount_krw,
            "paid_fee": status.paid_fee,
            "error_message": None if status.status in {"WAITING", "FILLED", "CANCELED", "PARTIALLY_FILLED"} else log.get("error_message"),
        },
    )
    severity = "WARNING" if status.status == "PARTIALLY_FILLED" else "INFO"
    log_recovery_event(
        source,
        severity,
        f"Order reconciled as {status.status}.",
        exchange=str(log.get("exchange") or "bithumb"),
        market=str(log.get("market") or "KRW-BTC"),
        session_id=log.get("session_id"),
        request_id=log.get("request_id"),
        order_uuid=_order_uuid(status.raw) or log.get("order_uuid"),
        payload={
            "status": status.status,
            "executed_volume": status.executed_volume,
            "remaining_volume": status.remaining_volume,
            "filled_amount_krw": status.filled_amount_krw,
        },
    )


async def reconcile_balances(exchange: str = "bithumb", market: str = "KRW-BTC") -> dict:
    broker = get_live_broker(exchange)
    try:
        balances = await broker.get_balances()
    except Exception as exc:
        log_recovery_event("API_ERROR", "ERROR", "Balance reconciliation failed.", exchange=exchange, market=market, payload={"error": str(exc)})
        return {"status": "FAILED", "ok": False, "blocking": True, "message": str(exc)}

    internal_positions = load_open_live_positions(exchange, market)
    internal_btc = sum(_float(position.get("entry_volume")) for position in internal_positions)
    exchange_btc = _balance_amount(balances, "BTC")
    tolerance = max(BALANCE_MISMATCH_VOLUME_TOLERANCE, abs(internal_btc) * BALANCE_MISMATCH_RELATIVE_TOLERANCE)
    difference = exchange_btc - internal_btc
    mismatch = abs(difference) > tolerance
    status = {
        "status": "BALANCE_MISMATCH" if mismatch else "OK",
        "ok": not mismatch,
        "blocking": mismatch,
        "exchange": exchange,
        "market": market,
        "internal_btc_position": internal_btc,
        "exchange_btc_total": exchange_btc,
        "difference_btc": difference,
        "tolerance_btc": tolerance,
        "open_position_count": len(internal_positions),
        "checked_at": _utc_now(),
    }
    if mismatch:
        log_recovery_event(
            "BALANCE_MISMATCH",
            "ERROR",
            "Exchange BTC balance and internal LivePosition volume differ. New auto orders are blocked.",
            exchange=exchange,
            market=market,
            payload=status,
        )
    return status


async def auto_order_recovery_block_reason(exchange: str = "bithumb", market: str = "KRW-BTC") -> str | None:
    if has_unresolved_live_order(exchange, market):
        return "BLOCKED_UNRESOLVED_LIVE_ORDER"
    balance = await reconcile_balances(exchange, market)
    if balance.get("blocking"):
        return "BLOCKED_BALANCE_MISMATCH" if balance.get("status") == "BALANCE_MISMATCH" else "BLOCKED_BALANCE_RECONCILIATION_FAILED"
    return None


def is_timeout_exception(exc: Exception) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    message = str(exc).lower()
    return "timeout" in message or "timed out" in message


def normalize_exchange_order(order: dict) -> ReconciledOrderStatus:
    raw_state = str(order.get("state") or order.get("status") or "").lower()
    executed_volume = _float(order.get("executed_volume"))
    remaining_volume = _float(order.get("remaining_volume"))
    requested_volume = _float(order.get("volume"))
    price = _float(order.get("price"))
    if remaining_volume <= 0 and requested_volume > 0 and executed_volume > 0:
        remaining_volume = max(requested_volume - executed_volume, 0.0)
    filled_amount = _filled_amount_krw(order, executed_volume, price)
    paid_fee = _float(order.get("paid_fee")) or _float(order.get("reserved_fee"))

    if executed_volume > 0 and remaining_volume > 0:
        status = "PARTIALLY_FILLED"
    elif raw_state in {"done", "filled", "completed"} or (executed_volume > 0 and remaining_volume <= 0):
        status = "FILLED"
    elif raw_state in {"cancel", "canceled", "cancelled"}:
        status = "CANCELED"
    elif raw_state in {"wait", "waiting", "watch"} or remaining_volume > 0:
        status = "WAITING"
    else:
        status = "SUBMITTED"
    return ReconciledOrderStatus(status, executed_volume, remaining_volume, filled_amount, paid_fee, order)


def recent_recovery_events(limit: int = 20) -> list[dict]:
    return load_live_recovery_events(limit)


def log_recovery_event(
    event_type: str,
    severity: str,
    message: str,
    *,
    exchange: str = "bithumb",
    market: str = "KRW-BTC",
    session_id: int | None = None,
    request_id: str | None = None,
    order_uuid: str | None = None,
    payload: dict | None = None,
) -> None:
    insert_live_recovery_event(
        {
            "event_type": event_type,
            "severity": severity,
            "exchange": exchange,
            "market": market,
            "session_id": session_id,
            "request_id": request_id,
            "order_uuid": order_uuid,
            "message": message,
            "payload": payload or {},
        }
    )


def _risk_result_for_status(status: str, fallback: str) -> str:
    if status == "PARTIALLY_FILLED":
        return "PARTIAL_FILL_REQUIRES_RECOVERY"
    if status in {"WAITING", "FILLED", "CANCELED"}:
        return "ALLOWED"
    return fallback


def _extract_orders(response: dict | list) -> list[dict]:
    raw = response.get("orders", []) if isinstance(response, dict) else response
    return [item for item in raw if isinstance(item, dict)]


def _order_uuid(order: dict) -> str:
    return str(order.get("uuid") or order.get("order_id") or order.get("id") or "")


def _client_order_id(order: dict) -> str:
    return str(order.get("client_order_id") or order.get("identifier") or "")


def _safe_order_payload(order: dict) -> dict:
    blocked = {"authorization", "Authorization", "jwt", "secret", "access_key", "secret_key"}
    return {key: value for key, value in order.items() if key not in blocked}


def _filled_amount_krw(order: dict, executed_volume: float, price: float) -> float:
    explicit = _float(order.get("paid_amount")) or _float(order.get("executed_funds")) or _float(order.get("locked"))
    if explicit > 0:
        return explicit
    trades = order.get("trades")
    if isinstance(trades, list):
        total = 0.0
        for trade in trades:
            if isinstance(trade, dict):
                total += _float(trade.get("price")) * _float(trade.get("volume"))
        if total > 0:
            return total
    return executed_volume * price


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
