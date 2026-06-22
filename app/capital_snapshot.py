from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

from app.database import (
    load_active_order_reservations,
    load_global_bot_operation_policy,
    load_open_live_positions_for_exchange,
    load_position_slots,
    load_unresolved_live_order_logs_for_exchange,
)
from app.live_broker import _available_balance, _balance_amount, get_live_broker


def _utc_now_dt() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _utc_now() -> str:
    return _utc_now_dt().isoformat().replace("+00:00", "Z")


def _int_env(name: str, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(value, maximum)
    return value


def _float_env(name: str, default: float, *, minimum: float = 0.0) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, value)


def _market_symbol(market: str) -> str:
    return str(market or "").split("-")[-1].upper()


def _position_value(position: dict) -> float:
    current_value = float(position.get("current_value_krw") or 0.0)
    if current_value > 0:
        return current_value
    return float(position.get("current_price") or position.get("entry_price") or 0.0) * float(position.get("entry_volume") or 0.0)


def _order_krw(order: dict) -> float:
    explicit = order.get("remaining_fee") or order.get("reserved_fee") or order.get("locked") or order.get("amount_krw")
    try:
        explicit_value = float(explicit)
    except (TypeError, ValueError):
        explicit_value = 0.0
    if explicit_value > 0:
        return explicit_value
    try:
        price = float(order.get("price") or 0.0)
        volume = float(order.get("remaining_volume") or order.get("volume") or 0.0)
        return max(price * volume, 0.0)
    except (TypeError, ValueError):
        return 0.0


def _is_buy_order(order: dict) -> bool:
    side = str(order.get("side") or "").lower()
    return side in {"bid", "buy"}


def _balance_total(balances: dict | None, currency: str) -> float:
    if not balances:
        return 0.0
    return _balance_amount(balances, currency.upper())


def _balance_available(balances: dict | None, currency: str) -> float:
    if not balances:
        return 0.0
    return _available_balance(balances, currency.upper())


def _open_order_markets(positions: list[dict], reservations: list[dict], db_orders: list[dict]) -> list[str]:
    markets = {
        str(item.get("market") or "")
        for item in [*positions, *reservations, *db_orders]
        if item.get("market")
    }
    markets.add(os.getenv("AUTO_ALLOWED_MARKET", "KRW-BTC"))
    return sorted(markets)


async def build_capital_snapshot_async(exchange: str = "bithumb") -> dict:
    created_at = _utc_now()
    exchange = (exchange or "bithumb").strip().lower()
    policy = load_global_bot_operation_policy()
    max_slots = _int_env("AUTO_MAX_OPEN_POSITION_COUNT", 5, minimum=1, maximum=20)
    cash_reserve_pct = _float_env("AUTO_CASH_RESERVE_PCT", 5.0)
    max_age_seconds = _int_env("AUTO_CAPITAL_SNAPSHOT_MAX_AGE_SECONDS", 10, minimum=1, maximum=300)
    positions = load_open_live_positions_for_exchange(exchange)
    reservations = load_active_order_reservations(exchange)
    db_orders = load_unresolved_live_order_logs_for_exchange(exchange)
    slots = load_position_slots(max_slots, exchange)
    warnings: list[str] = []
    blockers: list[str] = []
    snapshot_error = ""
    balances: dict | None = None
    exchange_orders: list[dict] = []

    try:
        broker = get_live_broker(exchange)
        balances = await broker.get_balances()
        seen_order_ids: set[str] = set()
        for market in _open_order_markets(positions, reservations, db_orders):
            try:
                response = await broker.list_open_orders(market)
                raw_orders = response.get("orders", []) if isinstance(response, dict) else []
                if isinstance(raw_orders, list):
                    for order in raw_orders:
                        if not isinstance(order, dict):
                            continue
                        order_id = str(order.get("uuid") or order.get("identifier") or order.get("client_order_id") or f"{market}:{len(exchange_orders)}")
                        if order_id in seen_order_ids:
                            continue
                        seen_order_ids.add(order_id)
                        exchange_orders.append(order)
            except Exception as exc:
                warnings.append(f"OPEN_ORDER_FETCH_FAILED:{market}:{exc.__class__.__name__}")
    except Exception as exc:
        snapshot_error = "BALANCE_FETCH_FAILED"
        blockers.append("BLOCKED_EXCHANGE_BALANCE_UNAVAILABLE")
        warnings.append(f"BALANCE_FETCH_FAILED:{exc.__class__.__name__}")

    available_krw = _balance_available(balances, "KRW") if balances else None
    max_total = float(policy.get("max_total_exposure_krw") or 0.0)
    cash_reserve = max_total * cash_reserve_pct / 100
    db_position_value = sum(_position_value(position) for position in positions)
    pending_reserved = sum(float(item.get("amount_krw") or 0.0) for item in reservations)
    pending_exchange_buy = sum(_order_krw(order) for order in exchange_orders if _is_buy_order(order))

    exchange_position_value = 0.0
    balance_mismatch = False
    position_markets = {_market_symbol(str(position.get("market") or "")) for position in positions}
    for position in positions:
        symbol = _market_symbol(str(position.get("market") or ""))
        exchange_total = _balance_total(balances, symbol) if balances else 0.0
        db_volume = float(position.get("entry_volume") or 0.0)
        price = float(position.get("current_price") or position.get("entry_price") or 0.0)
        exchange_position_value += exchange_total * price
        if balances and db_volume > 0 and exchange_total <= 0:
            balance_mismatch = True
            warnings.append(f"EXCHANGE_BALANCE_ZERO_FOR_OPEN_POSITION:{position.get('market')}")
    if balances:
        for symbol, item in (balances.get("by_currency") or {}).items():
            if symbol == "KRW" or symbol in position_markets:
                continue
            total = float(item.get("balance") or 0.0) + float(item.get("locked") or 0.0)
            if total > 0:
                balance_mismatch = True
                warnings.append(f"EXCHANGE_BALANCE_WITHOUT_DB_POSITION:{symbol}")

    db_order_ids = {str(order.get("order_uuid") or order.get("request_id") or "") for order in db_orders}
    exchange_order_ids = {str(order.get("uuid") or order.get("identifier") or order.get("client_order_id") or "") for order in exchange_orders}
    open_order_mismatch = bool(db_order_ids.symmetric_difference(exchange_order_ids)) if exchange_orders or db_orders else False
    if open_order_mismatch:
        warnings.append("OPEN_ORDER_MISMATCH_DETECTED")

    remaining_exposure = max(max_total - db_position_value - pending_reserved - pending_exchange_buy, 0.0)
    if available_krw is None:
        available_budget = 0.0
    else:
        available_budget = max(min(remaining_exposure, available_krw - cash_reserve), 0.0)
    if available_krw is not None and available_krw <= 0:
        blockers.append("BLOCKED_INSUFFICIENT_KRW_BALANCE")
    if remaining_exposure <= 0:
        blockers.append("BLOCKED_REMAINING_EXPOSURE_TOO_SMALL")
    if available_krw is not None and available_krw <= cash_reserve:
        blockers.append("BLOCKED_CASH_RESERVE_REQUIRED")
    if balance_mismatch:
        blockers.append("BLOCKED_BALANCE_MISMATCH")
    if open_order_mismatch:
        blockers.append("BLOCKED_OPEN_ORDER_MISMATCH")

    return {
        "exchange": exchange,
        "created_at": created_at,
        "max_age_seconds": max_age_seconds,
        "stale": False,
        "snapshot_error": snapshot_error,
        "warnings": list(dict.fromkeys(warnings)),
        "blockers": list(dict.fromkeys(blockers)),
        "auto_trading_enabled": bool(policy.get("auto_trading_enabled")),
        "max_total_exposure_krw": max_total,
        "daily_loss_limit_pct": float(policy.get("daily_loss_limit_pct") or 0.0),
        "available_krw_balance": available_krw,
        "cash_reserve_krw": cash_reserve,
        "db_open_position_value_krw": db_position_value,
        "exchange_position_value_krw": exchange_position_value,
        "pending_buy_reserved_krw": pending_reserved,
        "pending_exchange_buy_order_krw": pending_exchange_buy,
        "remaining_exposure_krw": remaining_exposure,
        "available_budget_krw": available_budget,
        "open_position_count": len(positions),
        "max_open_position_count": max_slots,
        "empty_slot_count": len([slot for slot in slots if str(slot.get("status") or "") == "EMPTY"]),
        "balance_mismatch_detected": balance_mismatch,
        "open_order_mismatch_detected": open_order_mismatch,
        "positions": positions,
        "balances": balances,
        "open_orders": exchange_orders,
        "db_open_orders": db_orders,
        "reservations": reservations,
        "slots": slots,
    }


def build_capital_snapshot(exchange: str = "bithumb") -> dict:
    return asyncio.run(build_capital_snapshot_async(exchange))


def snapshot_is_fresh(snapshot: dict) -> bool:
    if snapshot.get("snapshot_error"):
        return False
    try:
        created_at = datetime.fromisoformat(str(snapshot.get("created_at")).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return False
    age = (_utc_now_dt() - created_at).total_seconds()
    return age <= int(snapshot.get("max_age_seconds") or _int_env("AUTO_CAPITAL_SNAPSHOT_MAX_AGE_SECONDS", 10, minimum=1))


def sellable_volume_for_position(snapshot: dict, position: dict) -> float:
    symbol = _market_symbol(str(position.get("market") or ""))
    db_volume = float(position.get("entry_volume") or 0.0)
    exchange_available = _balance_available(snapshot.get("balances"), symbol)
    return max(min(db_volume, exchange_available), 0.0)
