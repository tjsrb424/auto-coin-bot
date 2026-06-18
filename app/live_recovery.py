from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from app.database import (
    create_live_position,
    get_live_order_log_by_uuid,
    has_unresolved_live_order,
    insert_live_recovery_event,
    load_filled_entry_order_logs_without_position,
    load_live_position_by_entry_order_uuid,
    load_live_recovery_events,
    load_latest_live_strategy_session,
    load_open_live_positions,
    load_reconcilable_live_order_logs,
    pause_running_auto_live_pilot_sessions_on_startup,
    pause_running_live_strategy_sessions_on_startup,
    update_live_strategy_session,
    update_live_order_log,
)
from app.live_broker import _balance_amount, get_live_broker
from app.upbit import fetch_tickers

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
    if status.status == "FILLED":
        updated = {
            **log,
            "status": status.status,
            "order_uuid": _order_uuid(status.raw) or log.get("order_uuid"),
            "executed_volume": status.executed_volume,
            "remaining_volume": status.remaining_volume,
            "filled_amount_krw": status.filled_amount_krw,
            "paid_fee": status.paid_fee,
            "exchange_response_payload": status.raw,
        }
        ensure_filled_entry_order_position(updated, source=f"{source}_POSITION_SYNC")


async def reconcile_balances(exchange: str = "bithumb", market: str = "KRW-BTC") -> dict:
    broker = get_live_broker(exchange)
    try:
        balances = await broker.get_balances()
    except Exception as exc:
        log_recovery_event("API_ERROR", "ERROR", "Balance reconciliation failed.", exchange=exchange, market=market, payload={"error": str(exc)})
        return {"status": "FAILED", "ok": False, "blocking": True, "message": str(exc)}

    position_sync = ensure_filled_entry_order_positions(exchange, market)
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
        "position_sync": position_sync,
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


def ensure_filled_entry_order_positions(exchange: str = "bithumb", market: str = "KRW-BTC") -> dict:
    created = 0
    attached = 0
    skipped = 0
    for log in load_filled_entry_order_logs_without_position(exchange, market):
        result = ensure_filled_entry_order_position(log, source="FILLED_ENTRY_POSITION_RECOVERY")
        if result == "CREATED":
            created += 1
        elif result == "ATTACHED":
            attached += 1
        else:
            skipped += 1
    return {"created": created, "attached": attached, "skipped": skipped}


def ensure_filled_entry_order_position(log: dict, *, source: str = "FILLED_ENTRY_POSITION_SYNC") -> str:
    if str(log.get("status") or "").upper() != "FILLED":
        return "SKIPPED"
    if str(log.get("side") or "").upper() != "BUY" or str(log.get("order_purpose") or "ENTRY").upper() != "ENTRY":
        return "SKIPPED"
    if log.get("position_id"):
        return "SKIPPED"

    exchange = str(log.get("exchange") or "bithumb")
    market = str(log.get("market") or "KRW-BTC")
    order_uuid = str(log.get("order_uuid") or _order_uuid(log.get("exchange_response_payload") or ""))
    if not order_uuid:
        return "SKIPPED"

    existing = load_live_position_by_entry_order_uuid(exchange, market, order_uuid)
    if existing:
        position_id = int(existing["id"])
        update_live_order_log(str(log["request_id"]), {"position_id": position_id})
        _adopt_position_in_relevant_session(log, position_id, "POSITION_ATTACHED_TO_FILLED_ORDER")
        log_recovery_event(
            source,
            "INFO",
            "Filled entry order was attached to an existing live position.",
            exchange=exchange,
            market=market,
            session_id=log.get("session_id"),
            request_id=log.get("request_id"),
            order_uuid=order_uuid,
            payload={"position_id": position_id},
        )
        return "ATTACHED"

    entry_price = _entry_price_from_log(log)
    entry_volume = _float(log.get("executed_volume")) or _float(log.get("volume"))
    if entry_price <= 0 or entry_volume <= 0:
        return "SKIPPED"

    position_id = create_live_position(
        {
            "session_id": int(log["session_id"]),
            "exchange": exchange,
            "market": market,
            "candidate_strategy_id": int(log["candidate_strategy_id"]),
            "strategy_name": str(log.get("strategy_name") or "live_strategy"),
            "status": "OPEN",
            "entry_order_uuid": order_uuid,
            "entry_price": entry_price,
            "entry_volume": entry_volume,
            "entry_amount_krw": _filled_amount_from_log(log, entry_price, entry_volume),
            "current_price": entry_price,
            "unrealized_pnl": 0.0,
            "realized_pnl": 0.0,
            "stop_loss_price": entry_price * (1 - _env_float("AUTO_STOP_LOSS_PERCENT", 0.7) / 100),
            "take_profit_price": entry_price * (1 + _env_float("AUTO_TAKE_PROFIT_PERCENT", 1.0) / 100),
            "opened_at": str(log.get("updated_at") or log.get("created_at") or _utc_now()),
        }
    )
    update_live_order_log(str(log["request_id"]), {"position_id": position_id})
    _adopt_position_in_relevant_session(log, position_id, "POSITION_OPEN_SYNCED")
    log_recovery_event(
        source,
        "WARNING",
        "Filled entry order without position_id was recovered as a live position.",
        exchange=exchange,
        market=market,
        session_id=log.get("session_id"),
        request_id=log.get("request_id"),
        order_uuid=order_uuid,
        payload={
            "position_id": position_id,
            "entry_price": entry_price,
            "entry_volume": entry_volume,
            "entry_amount_krw": _filled_amount_from_log(log, entry_price, entry_volume),
        },
    )
    return "CREATED"


async def import_exchange_btc_position(exchange: str = "bithumb", market: str = "KRW-BTC", *, confirmation: str) -> dict:
    if confirmation != "IMPORT BTC POSITION":
        return {"ok": False, "status": "CONFIRMATION_REQUIRED", "message": "확인 문구 IMPORT BTC POSITION이 필요합니다."}

    session = load_latest_live_strategy_session()
    if not session or str(session.get("status")) not in {"READY", "RUNNING", "PAUSED", "LIVE_PAUSED"}:
        return {"ok": False, "status": "NO_LIVE_STRATEGY_SESSION", "message": "편입할 자동매매 전략 세션이 없습니다."}
    if str(session.get("exchange") or exchange) != exchange or str(session.get("market") or market) != market:
        return {"ok": False, "status": "SESSION_MARKET_MISMATCH", "message": "현재 자동매매 세션의 거래소/마켓과 일치하지 않습니다."}

    balance_status = await reconcile_balances(exchange, market)
    if not balance_status.get("blocking"):
        return {"ok": False, "status": "NO_BALANCE_MISMATCH", "message": "편입할 거래소 BTC 잔고 불일치가 없습니다.", "balance_reconciliation": balance_status}

    internal_btc = _float(balance_status.get("internal_btc_position"))
    exchange_btc = _float(balance_status.get("exchange_btc_total"))
    if internal_btc > 0:
        return {"ok": False, "status": "INTERNAL_POSITION_EXISTS", "message": "이미 내부 포지션이 있어 자동 편입하지 않습니다.", "balance_reconciliation": balance_status}
    if exchange_btc <= 0:
        return {"ok": False, "status": "NO_EXCHANGE_BTC", "message": "거래소 BTC 잔고가 없습니다.", "balance_reconciliation": balance_status}

    broker = get_live_broker(exchange)
    balances = await broker.get_balances()
    btc_entry = (balances.get("by_currency") or {}).get("BTC", {})
    avg_buy_price = _float(btc_entry.get("avg_buy_price"))
    current_price = await _market_price(exchange, market)
    entry_price = avg_buy_price if avg_buy_price > 0 else current_price
    if entry_price <= 0 or current_price <= 0:
        return {"ok": False, "status": "PRICE_UNAVAILABLE", "message": "포지션 편입 기준가를 확인할 수 없습니다.", "balance_reconciliation": balance_status}

    stop_loss_price = entry_price * 0.993
    take_profit_price = entry_price * 1.01
    position_id = create_live_position(
        {
            "session_id": session["id"],
            "exchange": exchange,
            "market": market,
            "candidate_strategy_id": session["candidate_strategy_id"],
            "strategy_name": f"{session['strategy_name']} · 거래소잔고편입",
            "status": "OPEN",
            "entry_order_uuid": "IMPORTED_EXCHANGE_BALANCE",
            "entry_price": entry_price,
            "entry_volume": exchange_btc,
            "entry_amount_krw": entry_price * exchange_btc,
            "current_price": current_price,
            "unrealized_pnl": (current_price - entry_price) * exchange_btc,
            "realized_pnl": 0.0,
            "stop_loss_price": stop_loss_price,
            "take_profit_price": take_profit_price,
            "opened_at": _utc_now(),
        }
    )
    update_live_strategy_session(
        int(session["id"]),
        {
            "current_position_id": position_id,
            "last_risk_result": "IMPORTED_EXCHANGE_BALANCE",
            "last_order_status": "POSITION_IMPORTED",
        },
    )
    after = await reconcile_balances(exchange, market)
    payload = {
        "position_id": position_id,
        "exchange_btc_total": exchange_btc,
        "entry_price": entry_price,
        "current_price": current_price,
        "balance_reconciliation_before": balance_status,
        "balance_reconciliation_after": after,
    }
    log_recovery_event(
        "EXCHANGE_BALANCE_IMPORTED",
        "WARNING",
        "Exchange BTC balance was imported as an internal live position by explicit admin action.",
        exchange=exchange,
        market=market,
        session_id=int(session["id"]),
        payload=payload,
    )
    return {"ok": after.get("ok", False), "status": "IMPORTED", **payload}


async def auto_order_recovery_block_reason(exchange: str = "bithumb", market: str = "KRW-BTC") -> str | None:
    if has_unresolved_live_order(exchange, market):
        return "BLOCKED_UNRESOLVED_LIVE_ORDER"
    balance = await reconcile_balances(exchange, market)
    if balance.get("blocking"):
        return "BLOCKED_BALANCE_MISMATCH" if balance.get("status") == "BALANCE_MISMATCH" else "BLOCKED_BALANCE_RECONCILIATION_FAILED"
    return None


async def _market_price(exchange: str, market: str) -> float:
    broker = get_live_broker(exchange)
    base_url = getattr(getattr(broker, "config", None), "base_url", "")
    try:
        tickers = await fetch_tickers([market], base_url=base_url) if base_url else await fetch_tickers([market])
        if tickers:
            return _float(tickers[0].get("trade_price") or tickers[0].get("tradePrice") or tickers[0].get("close_price"))
    except Exception:
        tickers = await fetch_tickers([market])
        if tickers:
            return _float(tickers[0].get("trade_price") or tickers[0].get("tradePrice") or tickers[0].get("close_price"))
    return 0.0


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


def _adopt_position_in_relevant_session(log: dict, position_id: int, risk_result: str) -> None:
    session_id = log.get("session_id")
    if session_id:
        update_live_strategy_session(
            int(session_id),
            {
                "current_position_id": position_id,
                "current_open_order_uuid": None,
                "last_order_status": "FILLED",
                "last_risk_result": risk_result,
            },
        )

    latest = load_latest_live_strategy_session()
    if not latest:
        return
    if str(latest.get("exchange") or "") != str(log.get("exchange") or "bithumb"):
        return
    if str(latest.get("market") or "") != str(log.get("market") or "KRW-BTC"):
        return
    if int(latest.get("candidate_strategy_id") or 0) != int(log.get("candidate_strategy_id") or 0):
        return
    update_live_strategy_session(
        int(latest["id"]),
        {
            "current_position_id": position_id,
            "current_open_order_uuid": None,
            "last_order_status": "FILLED",
            "last_risk_result": risk_result,
        },
    )


def _entry_price_from_log(log: dict) -> float:
    price = _float(log.get("price"))
    if price > 0:
        return price
    amount = _float(log.get("filled_amount_krw")) or _float(log.get("amount_krw"))
    volume = _float(log.get("executed_volume")) or _float(log.get("volume"))
    return amount / volume if amount > 0 and volume > 0 else 0.0


def _filled_amount_from_log(log: dict, entry_price: float, entry_volume: float) -> float:
    return _float(log.get("filled_amount_krw")) or _float(log.get("amount_krw")) or entry_price * entry_volume


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _extract_orders(response: dict | list) -> list[dict]:
    raw = response.get("orders", []) if isinstance(response, dict) else response
    return [item for item in raw if isinstance(item, dict)]


def _order_uuid(order: dict) -> str:
    if not isinstance(order, dict):
        return ""
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
