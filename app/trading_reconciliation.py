from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import re
from typing import Any


OPEN_POSITION_STATUSES = {"OPEN", "EXIT_CANDIDATE", "EXIT_PENDING", "CLOSING", "MANUAL_REVIEW_REQUIRED"}
REAL_EXCHANGE_UUID_PATTERN = re.compile(r"^[A-Z]\d{10,}$")


def build_equity_reconciliation(
    *,
    initial_equity: float,
    current_equity_from_exchange: float | None,
    realized_pnl_from_db: float,
    unrealized_pnl_from_positions: float,
    total_fee_from_db: float,
    db_orders: list[dict],
    db_positions: list[dict],
    exchange_orders: list[dict] | None = None,
    exchange_balances: dict | None = None,
    valuation_prices: dict[str, float] | None = None,
    period_start_utc: str | None = None,
    deposits: float = 0.0,
    withdrawals: float = 0.0,
    fee_rate: float = 0.0005,
    open_order_fee_adjustment: float = 0.0,
) -> dict:
    exchange_ledger_available = exchange_orders is not None
    exchange_fills = normalize_exchange_fills(exchange_orders or [])
    if period_start_utc:
        start = _parse_utc(period_start_utc)
        if start:
            exchange_fills = [
                fill for fill in exchange_fills
                if (_parse_utc(fill.get("executed_at_utc")) or start) >= start
            ]
    db_fills = [normalize_db_fill(row) for row in db_orders if str(row.get("status") or "").upper() == "FILLED"]
    db_fills = [row for row in db_fills if row["market"] and (row["exchange_order_uuid"] or row["client_order_id"])]

    match_report = match_fills(db_fills if exchange_ledger_available else [], exchange_fills)
    duplicate_exchange_uuid = duplicate_exchange_order_uuids(db_orders)
    duplicate_client_order_id = duplicate_client_order_ids(db_orders)
    duplicate_db_accounting = duplicate_position_accounting(db_positions, db_orders)

    realized_fee = sum(_float(row.get("paid_fee")) for row in db_orders if str(row.get("status") or "").upper() == "FILLED" and _is_sell(row))
    gross_realized = realized_pnl_from_db + realized_fee
    net_realized = realized_pnl_from_db
    estimated_exit_fee = estimate_exit_fee(db_positions, fee_rate)
    unrealized_after_fee = unrealized_pnl_from_positions - estimated_exit_fee
    total_fee_from_exchange = sum(fill["fee"] for fill in exchange_fills)
    fee_diff = total_fee_from_db - total_fee_from_exchange if exchange_ledger_available else None

    expected_equity = (
        initial_equity
        + deposits
        - withdrawals
        + net_realized
        + unrealized_after_fee
        - open_order_fee_adjustment
    )
    legacy_expected_equity_with_double_fee = (
        initial_equity + deposits - withdrawals + realized_pnl_from_db + unrealized_pnl_from_positions - total_fee_from_db
    )
    equity_diff = None if current_equity_from_exchange is None else current_equity_from_exchange - expected_equity
    equity_diff_rate = None if equity_diff is None else abs(equity_diff) / max(initial_equity, 1.0)

    locked_values = locked_balance_values(exchange_balances or {}, valuation_prices or {})
    valuation_diff = valuation_price_diff(db_positions, valuation_prices or {})
    timestamp_mismatch_amount = sum(fill["amount_krw"] for fill in db_fills if timestamp_is_non_utc(fill.get("executed_at_utc")))

    breakdown_values = {
        "missing_exchange_fill_in_db": match_report["missing_exchange_fill_in_db"]["amount_krw"],
        "db_only_trade": -match_report["db_only_trade"]["amount_krw"],
        "duplicate_db_accounting": -duplicate_db_accounting["amount_krw"],
        "duplicate_exchange_uuid_in_db": -duplicate_exchange_uuid["amount_krw"],
        "duplicate_client_order_id_in_db": -duplicate_client_order_id["amount_krw"],
        "fee_mismatch": -(fee_diff or 0.0),
        "locked_krw_excluded": 0.0,
        "locked_coin_excluded": 0.0,
        "valuation_price_diff": valuation_diff["amount_krw"],
        "deposit_withdrawal_mismatch": 0.0,
        "rounding_diff": 0.0,
        "timestamp_range_mismatch": -timestamp_mismatch_amount,
        "open_order_or_pending_reservation_effect": -open_order_fee_adjustment,
    }
    explained = sum(breakdown_values.values())
    unexplained = None if equity_diff is None else equity_diff - explained
    unexplained_rate = None if unexplained is None else abs(unexplained) / max(initial_equity, 1.0)

    return {
        "initial_equity": initial_equity,
        "current_equity_from_exchange": current_equity_from_exchange,
        "expected_equity": expected_equity,
        "legacy_expected_equity_with_double_fee": legacy_expected_equity_with_double_fee,
        "equity_diff": equity_diff,
        "equity_diff_rate": equity_diff_rate,
        "gross_realized_pnl_before_fee": gross_realized,
        "realized_fee": realized_fee,
        "net_realized_pnl_after_fee": net_realized,
        "realized_pnl_fee_treatment": "DB live_positions.realized_pnl is net after exit fee",
        "unrealized_pnl_before_fee": unrealized_pnl_from_positions,
        "estimated_exit_fee": estimated_exit_fee,
        "unrealized_pnl_after_estimated_fee": unrealized_after_fee,
        "total_fee_from_db": total_fee_from_db,
        "total_fee_from_exchange": total_fee_from_exchange if exchange_ledger_available else None,
        "fee_diff": fee_diff if exchange_ledger_available else None,
        "expected_equity_formula": (
            "initial_equity + deposits - withdrawals + net_realized_pnl_after_fee "
            "+ unrealized_pnl_after_estimated_fee - open_order_fee_adjustment"
        ),
        "current_equity_formula": "KRW available + KRW locked + sum((coin available + coin locked) * valuation_price)",
        "current_equity_uses_locked_balances": True,
        "locked_krw_value": locked_values["locked_krw_value"],
        "locked_coin_market_value": locked_values["locked_coin_market_value"],
        "open_order_fee_adjustment": open_order_fee_adjustment,
        "deposits": deposits,
        "withdrawals": withdrawals,
        "exchange_fill_match": match_report,
        "duplicate_exchange_uuid_in_db": duplicate_exchange_uuid,
        "duplicate_client_order_id_in_db": duplicate_client_order_id,
        "duplicate_db_accounting": duplicate_db_accounting,
        "valuation_price_diff_detail": valuation_diff,
        "equity_diff_breakdown": {
            **breakdown_values,
            "unexplained": unexplained,
            "unexplained_rate": unexplained_rate,
        },
        "gate_failed": bool(
            (equity_diff is not None and (abs(equity_diff) > 100.0 or (equity_diff_rate or 0.0) > 0.001))
            or (unexplained is not None and (abs(unexplained) > 100.0 or (unexplained_rate or 0.0) > 0.001))
            or (exchange_ledger_available and match_report["missing_exchange_fill_in_db"]["count"] > 0)
            or (exchange_ledger_available and match_report["db_only_trade"]["count"] > 0)
            or duplicate_exchange_uuid["count"] > 0
            or duplicate_client_order_id["count"] > 0
            or (fee_diff is not None and abs(fee_diff) > 100.0)
        ),
    }


def normalize_db_fill(order: dict) -> dict:
    volume = _float(order.get("executed_volume")) or _float(order.get("volume"))
    price = _float(order.get("price"))
    amount = _float(order.get("filled_amount_krw")) or _float(order.get("amount_krw")) or price * volume
    if not price and volume:
        price = amount / volume
    return {
        "source": "db",
        "id": order.get("id"),
        "request_id": order.get("request_id"),
        "exchange_order_uuid": str(order.get("order_uuid") or ""),
        "client_order_id": str(order.get("client_order_id") or ""),
        "market": str(order.get("market") or ""),
        "symbol": _symbol(str(order.get("market") or "")),
        "side": _side(order),
        "price": price,
        "quantity": volume,
        "amount_krw": amount,
        "fee": _float(order.get("paid_fee")),
        "fee_currency": "KRW",
        "executed_at_utc": str(order.get("order_executed_at_utc") or order.get("updated_at") or order.get("created_at") or ""),
        "strategy": order.get("strategy_name"),
        "session_id": order.get("session_id"),
        "order_purpose": order.get("order_purpose"),
    }


def normalize_exchange_fills(exchange_orders: list[dict]) -> list[dict]:
    fills: list[dict] = []
    for order in exchange_orders:
        trades = order.get("trades") if isinstance(order, dict) else None
        if isinstance(trades, list) and trades:
            for trade in trades:
                fills.append(_normalize_exchange_trade(order, trade))
            continue
        fill = _normalize_exchange_trade(order, None)
        if fill["quantity"] > 0 or fill["amount_krw"] > 0:
            fills.append(fill)
    return fills


def match_fills(db_fills: list[dict], exchange_fills: list[dict]) -> dict:
    db_by_uuid = _index_by(db_fills, "exchange_order_uuid")
    db_by_client = _index_by(db_fills, "client_order_id")
    exchange_by_uuid = _index_by(exchange_fills, "exchange_order_uuid")
    exchange_by_client = _index_by(exchange_fills, "client_order_id")
    matched_db: set[int] = set()
    matched_exchange: set[int] = set()

    for index, fill in enumerate(exchange_fills):
        candidates = []
        if fill["exchange_order_uuid"]:
            candidates.extend(db_by_uuid.get(fill["exchange_order_uuid"], []))
        if fill["client_order_id"]:
            candidates.extend(db_by_client.get(fill["client_order_id"], []))
        match_index = _best_fill_match(fill, candidates, matched_db)
        if match_index is not None:
            matched_exchange.add(index)
            matched_db.add(match_index)

    exchange_missing = [fill for index, fill in enumerate(exchange_fills) if index not in matched_exchange]
    db_only = [fill for index, fill in enumerate(db_fills) if index not in matched_db]
    return {
        "exchange_fill_count": len(exchange_fills),
        "db_fill_count": len(db_fills),
        "matched_fill_count": len(matched_exchange),
        "missing_exchange_fill_in_db": _fill_group(exchange_missing),
        "db_only_trade": _fill_group(db_only),
        "fee_mismatches": fee_mismatches(db_fills, exchange_fills),
    }


def duplicate_exchange_order_uuids(db_orders: list[dict]) -> dict:
    return _duplicate_field(db_orders, "order_uuid", real_exchange_uuid_only=True)


def duplicate_client_order_ids(db_orders: list[dict]) -> dict:
    return _duplicate_field(db_orders, "client_order_id")


def duplicate_position_accounting(db_positions: list[dict], db_orders: list[dict]) -> dict:
    order_amounts = defaultdict(float)
    for order in db_orders:
        uuid = str(order.get("order_uuid") or "")
        if uuid:
            order_amounts[uuid] += _order_amount(order)
    seen: set[str] = set()
    items = []
    amount = 0.0
    for position in db_positions:
        uuid = str(position.get("entry_order_uuid") or "")
        if not uuid:
            continue
        if uuid in seen:
            extra = _float(position.get("entry_amount_krw")) or order_amounts.get(uuid, 0.0)
            amount += extra
            items.append({"order_uuid": uuid, "position_id": position.get("id"), "amount_krw": extra})
        seen.add(uuid)
    return {"count": len(items), "amount_krw": amount, "items": items[:20]}


def estimate_exit_fee(positions: list[dict], fee_rate: float) -> float:
    total = 0.0
    for position in positions:
        if str(position.get("status") or "").upper() not in OPEN_POSITION_STATUSES:
            continue
        total += max(_float(position.get("current_price")) * _float(position.get("entry_volume")) * fee_rate, 0.0)
    return total


def locked_balance_values(exchange_balances: dict, valuation_prices: dict[str, float]) -> dict:
    by_currency = exchange_balances.get("by_currency") if isinstance(exchange_balances, dict) else {}
    if not isinstance(by_currency, dict):
        by_currency = {}
    locked_krw = _float((by_currency.get("KRW") or {}).get("locked"))
    locked_coin_value = 0.0
    for currency, balance in by_currency.items():
        symbol = str(currency or "").upper()
        if not symbol or symbol == "KRW":
            continue
        market = f"KRW-{symbol}"
        locked_coin_value += _float((balance or {}).get("locked")) * _float(valuation_prices.get(market))
    return {"locked_krw_value": locked_krw, "locked_coin_market_value": locked_coin_value}


def valuation_price_diff(db_positions: list[dict], valuation_prices: dict[str, float]) -> dict:
    items = []
    total = 0.0
    for position in db_positions:
        if str(position.get("status") or "").upper() not in OPEN_POSITION_STATUSES:
            continue
        market = str(position.get("market") or "")
        exchange_price = _float(valuation_prices.get(market))
        db_price = _float(position.get("current_price"))
        volume = _float(position.get("entry_volume"))
        if exchange_price <= 0 or db_price <= 0 or volume <= 0:
            continue
        diff = (exchange_price - db_price) * volume
        if abs(diff) > 0.000001:
            items.append({"market": market, "position_id": position.get("id"), "db_price": db_price, "exchange_price": exchange_price, "volume": volume, "amount_krw": diff})
            total += diff
    return {"count": len(items), "amount_krw": total, "items": items[:20]}


def fee_mismatches(db_fills: list[dict], exchange_fills: list[dict], *, tolerance: float = 1.0) -> dict:
    exchange_by_uuid = _index_by(exchange_fills, "exchange_order_uuid")
    items = []
    total = 0.0
    for db_fill in db_fills:
        uuid = db_fill.get("exchange_order_uuid")
        matches = exchange_by_uuid.get(uuid, []) if uuid else []
        if not matches:
            continue
        diff = db_fill["fee"] - matches[0]["fee"]
        if abs(diff) > tolerance:
            items.append({"exchange_order_uuid": uuid, "db_fee": db_fill["fee"], "exchange_fee": matches[0]["fee"], "fee_diff": diff})
            total += diff
    return {"count": len(items), "amount_krw": total, "items": items[:20]}


def timestamp_is_non_utc(value: Any) -> bool:
    if not value:
        return False
    raw = str(value).replace(" ", "T")
    if raw.endswith("Z") or raw.endswith("+00:00"):
        return False
    return True


def _normalize_exchange_trade(order: dict, trade: dict | None) -> dict:
    source = trade or order or {}
    volume = _float(source.get("volume")) or _float(source.get("executed_volume")) or _float(order.get("executed_volume"))
    amount = _float(source.get("funds")) or _float(source.get("executed_funds")) or _float(source.get("filled_amount_krw")) or _float(order.get("executed_funds"))
    price = _float(source.get("price")) or (_float(amount) / volume if volume else 0.0)
    if not amount and price and volume:
        amount = price * volume
    return {
        "source": "exchange",
        "exchange_order_uuid": str(order.get("uuid") or order.get("order_uuid") or ""),
        "client_order_id": str(order.get("client_order_id") or order.get("identifier") or ""),
        "trade_uuid": str(source.get("uuid") or source.get("trade_uuid") or ""),
        "market": str(order.get("market") or source.get("market") or ""),
        "symbol": _symbol(str(order.get("market") or source.get("market") or "")),
        "side": _side(order),
        "price": price,
        "quantity": volume,
        "amount_krw": amount,
        "fee": _float(source.get("fee")) or _float(source.get("paid_fee")) or _float(order.get("paid_fee")),
        "fee_currency": str(source.get("fee_currency") or "KRW"),
        "executed_at_utc": _canonical_utc(source.get("created_at") or source.get("executed_at") or order.get("created_at")),
    }


def _best_fill_match(fill: dict, candidates: list[tuple[int, dict]], matched: set[int]) -> int | None:
    for index, candidate in candidates:
        if index in matched:
            continue
        if candidate.get("market") != fill.get("market") or candidate.get("side") != fill.get("side"):
            continue
        if _close(candidate.get("quantity"), fill.get("quantity"), 0.00000001) and _close(candidate.get("amount_krw"), fill.get("amount_krw"), 5.0):
            return index
    return None


def _index_by(rows: list[dict], field: str) -> dict[str, list[tuple[int, dict]]]:
    result: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    for index, row in enumerate(rows):
        value = str(row.get(field) or "")
        if value:
            result[value].append((index, row))
    return result


def _fill_group(fills: list[dict]) -> dict:
    return {
        "count": len(fills),
        "amount_krw": sum(_float(fill.get("amount_krw")) for fill in fills),
        "fee_krw": sum(_float(fill.get("fee")) for fill in fills),
        "items": fills[:20],
    }


def _duplicate_field(db_orders: list[dict], field: str, *, real_exchange_uuid_only: bool = False) -> dict:
    rows = [
        row for row in db_orders
        if str(row.get(field) or "") and "-filled-" not in str(row.get("request_id") or "") and "-waiting-" not in str(row.get("request_id") or "")
    ]
    synthetic_rows = []
    if real_exchange_uuid_only:
        synthetic_rows = [row for row in rows if not REAL_EXCHANGE_UUID_PATTERN.match(str(row.get(field) or ""))]
        rows = [row for row in rows if REAL_EXCHANGE_UUID_PATTERN.match(str(row.get(field) or ""))]
    counts = Counter(str(row.get(field) or "") for row in rows)
    duplicate_values = {value for value, count in counts.items() if count > 1}
    items = []
    amount = 0.0
    for value in duplicate_values:
        group = [row for row in rows if str(row.get(field) or "") == value]
        duplicate_amount = sum(_order_amount(row) for row in group[1:])
        amount += duplicate_amount
        items.append({field: value, "count": len(group), "amount_krw": duplicate_amount, "order_ids": [row.get("id") for row in group[:10]]})
    return {
        "count": len(items),
        "amount_krw": amount,
        "items": items[:20],
        "synthetic_uuid_count": len(synthetic_rows) if real_exchange_uuid_only else 0,
        "synthetic_uuid_items": [
            {"order_uuid": row.get(field), "order_id": row.get("id"), "request_id": row.get("request_id")}
            for row in synthetic_rows[:20]
        ] if real_exchange_uuid_only else [],
    }


def _order_amount(order: dict) -> float:
    return _float(order.get("filled_amount_krw")) or _float(order.get("amount_krw")) or _float(order.get("price")) * (_float(order.get("executed_volume")) or _float(order.get("volume")))


def _side(order: dict) -> str:
    side = str(order.get("side") or "").upper()
    if side == "BID":
        return "BUY"
    if side == "ASK":
        return "SELL"
    return side


def _is_sell(order: dict) -> bool:
    return _side(order) == "SELL" or str(order.get("order_purpose") or "").upper() == "EXIT"


def _symbol(market: str) -> str:
    return market.split("-")[-1] if "-" in market else market


def _canonical_utc(value: Any) -> str:
    if not value:
        return ""
    raw = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except ValueError:
        return str(value)


def _parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value).replace(" ", "T")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _close(left: Any, right: Any, tolerance: float) -> bool:
    return abs(_float(left) - _float(right)) <= tolerance


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
