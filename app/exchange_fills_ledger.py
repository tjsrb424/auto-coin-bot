from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from typing import Any

from app.database import load_exchange_fills_ledger, upsert_exchange_fill_ledger
from app.trading_reconciliation import normalize_db_fill, normalize_exchange_fills


REAL_EXCHANGE_UUID_PATTERN = re.compile(r"^[A-Z]\d{10,}$")
OPEN_POSITION_STATUSES = {"OPEN", "EXIT_CANDIDATE", "EXIT_PENDING", "CLOSING", "MANUAL_REVIEW_REQUIRED"}


def is_real_exchange_order_uuid(value: Any) -> bool:
    return bool(REAL_EXCHANGE_UUID_PATTERN.match(str(value or "")))


def is_synthetic_order_uuid(value: Any) -> bool:
    raw = str(value or "")
    return bool(raw and not is_real_exchange_order_uuid(raw))


def canonical_fill_key(fill: dict) -> str:
    raw = "|".join(
        [
            str(fill.get("exchange_name") or fill.get("exchange") or "bithumb"),
            str(fill.get("exchange_order_uuid") or ""),
            str(fill.get("symbol") or ""),
            str(fill.get("side") or ""),
            _num(fill.get("price")),
            _num(fill.get("quantity")),
            _num(fill.get("fee")),
            str(fill.get("executed_at_utc") or ""),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_exchange_fill_records(
    *,
    exchange_name: str,
    exchange_orders: list[dict],
    db_orders: list[dict],
    source: str = "exchange_api",
    period_start_utc: str | None = None,
) -> list[dict]:
    exchange_fills = normalize_exchange_fills(exchange_orders)
    if period_start_utc:
        start = _parse_utc(period_start_utc)
        if start:
            exchange_fills = [fill for fill in exchange_fills if (_parse_utc(fill.get("executed_at_utc")) or start) >= start]
    db_fills = [normalize_db_fill(order) for order in db_orders]
    db_by_uuid = _index_by(db_fills, "exchange_order_uuid")
    db_by_client = _index_by(db_fills, "client_order_id")
    uuid_counts = Counter(fill.get("exchange_order_uuid") for fill in exchange_fills if fill.get("exchange_order_uuid"))
    records = []
    for fill in exchange_fills:
        order_uuid = str(fill.get("exchange_order_uuid") or "")
        client_order_id = str(fill.get("client_order_id") or "")
        match, reason = _match_fill(fill, db_by_uuid.get(order_uuid, []) + db_by_client.get(client_order_id, []))
        match_status = "UNMATCHED"
        if uuid_counts.get(order_uuid, 0) > 1 and order_uuid:
            match_status = "DUPLICATE_EXCHANGE_UUID"
            reason = "same exchange_order_uuid appears more than once in the exchange ledger payload"
        elif match:
            match_status = "MATCHED_DB_ORDER"
        elif order_uuid and is_real_exchange_order_uuid(order_uuid):
            match_status = "MISSING_CANONICAL_LOG"
            reason = "exchange fill exists but no canonical live_order_logs row matched"
        elif order_uuid:
            match_status = "SYNTHETIC_UUID_ONLY"
            reason = "order uuid is not a real exchange uuid"

        record = {
            "exchange_name": exchange_name,
            "exchange_order_uuid": order_uuid if is_real_exchange_order_uuid(order_uuid) else None,
            "internal_order_ref": order_uuid if is_synthetic_order_uuid(order_uuid) else None,
            "exchange_fill_id": fill.get("trade_uuid") or order_uuid,
            "client_order_id": client_order_id or None,
            "symbol": fill.get("symbol") or _symbol(str(fill.get("market") or "")),
            "market": fill["market"],
            "side": fill["side"],
            "price": fill["price"],
            "quantity": fill["quantity"],
            "executed_value": fill["amount_krw"],
            "fee": fill["fee"],
            "fee_currency": fill.get("fee_currency") or "KRW",
            "executed_at_utc": _to_utc_string(fill.get("executed_at_utc")),
            "source": source,
            "matched_db_order_id": (match or {}).get("id"),
            "matched_live_order_log_id": (match or {}).get("id"),
            "matched_session_id": (match or {}).get("session_id"),
            "matched_strategy_name": (match or {}).get("strategy"),
            "match_status": match_status,
            "match_reason": reason,
        }
        record["fill_key"] = canonical_fill_key(record)
        records.append(record)
    return records


def persist_exchange_fill_records(records: list[dict]) -> dict:
    upserted = []
    for record in records:
        row = upsert_exchange_fill_ledger(record)
        if row:
            upserted.append(row)
    return summarize_ledger_rows(upserted)


def load_or_build_ledger_rows(
    *,
    exchange_name: str,
    period_start_utc: str,
    exchange_orders: list[dict],
    db_orders: list[dict],
    persist: bool = False,
) -> tuple[list[dict], dict]:
    records = build_exchange_fill_records(
        exchange_name=exchange_name,
        exchange_orders=exchange_orders,
        db_orders=db_orders,
        source="exchange_api",
        period_start_utc=period_start_utc,
    )
    if persist:
        summary = persist_exchange_fill_records(records)
        return load_exchange_fills_ledger(exchange_name, since_utc=period_start_utc), {**summary, "persisted": True}
    return records, {**summarize_ledger_rows(records), "persisted": False}


def summarize_ledger_rows(rows: list[dict]) -> dict:
    by_status = Counter(str(row.get("match_status") or "UNMATCHED") for row in rows)
    return {
        "row_count": len(rows),
        "status_counts": dict(sorted(by_status.items())),
        "missing_canonical_log_count": by_status.get("MISSING_CANONICAL_LOG", 0),
        "synthetic_uuid_count": by_status.get("SYNTHETIC_UUID_ONLY", 0),
        "duplicate_exchange_uuid_count": by_status.get("DUPLICATE_EXCHANGE_UUID", 0),
        "missing_exchange_fill_value": sum(float(row.get("executed_value") or 0.0) for row in rows if row.get("match_status") == "MISSING_CANONICAL_LOG"),
    }


def compute_realized_pnl_from_ledger(rows: list[dict]) -> dict:
    lots: dict[str, deque[dict]] = defaultdict(deque)
    gross = 0.0
    fees = 0.0
    unpaired_sell_value = 0.0
    realized_trades = []
    for row in sorted(rows, key=lambda item: (str(item.get("executed_at_utc") or ""), int(item.get("id") or 0))):
        market = str(row.get("market") or "")
        side = str(row.get("side") or "").upper()
        quantity = _float(row.get("quantity"))
        value = _float(row.get("executed_value"))
        fee = _float(row.get("fee")) if str(row.get("fee_currency") or "KRW").upper() == "KRW" else 0.0
        if quantity <= 0:
            continue
        if side == "BUY":
            lots[market].append({"quantity": quantity, "cost": value, "fee": fee})
            continue
        if side != "SELL":
            continue
        remaining = quantity
        basis = 0.0
        buy_fee = 0.0
        while remaining > 0 and lots[market]:
            lot = lots[market][0]
            used = min(remaining, lot["quantity"])
            ratio = used / lot["quantity"] if lot["quantity"] > 0 else 0.0
            basis += lot["cost"] * ratio
            buy_fee += lot["fee"] * ratio
            lot["quantity"] -= used
            lot["cost"] -= lot["cost"] * ratio
            lot["fee"] -= lot["fee"] * ratio
            remaining -= used
            if lot["quantity"] <= 0.000000000001:
                lots[market].popleft()
        if remaining > 0.000000000001:
            unpaired_sell_value += value * (remaining / quantity)
        sell_fee = fee
        trade_gross = value - basis
        trade_fee = buy_fee + sell_fee
        gross += trade_gross
        fees += trade_fee
        realized_trades.append(
            {
                "market": market,
                "exchange_order_uuid": row.get("exchange_order_uuid"),
                "executed_value": value,
                "basis_before_fee": basis,
                "gross_pnl_before_fee": trade_gross,
                "allocated_buy_fee": buy_fee,
                "sell_fee": sell_fee,
                "net_pnl_after_fee": trade_gross - trade_fee,
                "unpaired_quantity": remaining,
            }
        )
    return {
        "exchange_gross_realized_pnl_before_fee": gross,
        "exchange_realized_fee": fees,
        "exchange_net_realized_pnl_after_fee": gross - fees,
        "unpaired_sell_value_krw": unpaired_sell_value,
        "realized_trade_count": len(realized_trades),
        "realized_trades": realized_trades[:50],
    }


def build_position_valuation_summary(
    *,
    positions: list[dict],
    balances: dict,
    valuation_prices: dict[str, float],
    balance_snapshot_at_utc: str,
    valuation_price_snapshot_at_utc: str,
    valuation_source: str = "exchange_ticker",
) -> dict:
    by_currency = balances.get("by_currency") if isinstance(balances, dict) else {}
    if not isinstance(by_currency, dict):
        by_currency = {}
    items = []
    stale_effect = 0.0
    for position in positions:
        if str(position.get("status") or "").upper() not in OPEN_POSITION_STATUSES:
            continue
        market = str(position.get("market") or "")
        symbol = _symbol(market)
        balance = by_currency.get(symbol) or {}
        available = _float(balance.get("balance"))
        locked = _float(balance.get("locked"))
        exchange_total = available + locked
        db_quantity = _float(position.get("entry_volume"))
        total_quantity = exchange_total if exchange_total > 0 else db_quantity
        valuation_price = _float(valuation_prices.get(market))
        db_price = _float(position.get("current_price"))
        valuation_value = total_quantity * valuation_price
        db_stale_value = db_quantity * db_price
        entry_amount = _float(position.get("entry_amount_krw"))
        snapshot_unrealized = valuation_value - entry_amount
        diff = valuation_value - db_stale_value
        stale_effect += diff
        items.append(
            {
                "balance_snapshot_at_utc": balance_snapshot_at_utc,
                "valuation_price_snapshot_at_utc": valuation_price_snapshot_at_utc,
                "valuation_source": valuation_source,
                "position_id": position.get("id"),
                "position_symbol": symbol,
                "market": market,
                "exchange_available_quantity": available,
                "exchange_locked_quantity": locked,
                "total_quantity": total_quantity,
                "db_position_quantity": db_quantity,
                "valuation_price": valuation_price,
                "valuation_value_krw": valuation_value,
                "entry_amount_krw": entry_amount,
                "snapshot_unrealized_pnl": snapshot_unrealized,
                "db_stale_valuation_value_krw": db_stale_value,
                "valuation_diff_krw": diff,
            }
        )
    return {
        "balance_snapshot_at_utc": balance_snapshot_at_utc,
        "valuation_price_snapshot_at_utc": valuation_price_snapshot_at_utc,
        "valuation_source": valuation_source,
        "position_count": len(items),
        "stale_valuation_effect": stale_effect,
        "snapshot_unrealized_pnl": sum(_float(item.get("snapshot_unrealized_pnl")) for item in items),
        "items": items,
    }


def real_duplicate_exchange_uuid_groups(db_orders: list[dict]) -> dict:
    groups = defaultdict(list)
    synthetic = []
    for order in db_orders:
        uuid = str(order.get("order_uuid") or "")
        if not uuid:
            continue
        if is_synthetic_order_uuid(uuid):
            synthetic.append(order)
            continue
        groups[uuid].append(order)
    duplicates = [
        {
            "exchange_order_uuid": uuid,
            "count": len(rows),
            "order_ids": [row.get("id") for row in rows[:10]],
        }
        for uuid, rows in groups.items()
        if len(rows) > 1
    ]
    return {
        "count": len(duplicates),
        "items": duplicates[:20],
        "synthetic_uuid_count": len(synthetic),
        "synthetic_uuid_items": [
            {"order_uuid": row.get("order_uuid"), "order_id": row.get("id"), "request_id": row.get("request_id")}
            for row in synthetic[:20]
        ],
    }


def _match_fill(fill: dict, candidates: list[tuple[int, dict]]) -> tuple[dict | None, str]:
    for _, candidate in candidates:
        if candidate.get("market") != fill.get("market") or candidate.get("side") != fill.get("side"):
            continue
        quantity_match = abs(_float(candidate.get("quantity")) - _float(fill.get("quantity"))) <= 0.00000001
        value_match = abs(_float(candidate.get("amount_krw")) - _float(fill.get("amount_krw"))) <= 5.0
        fee_match = abs(_float(candidate.get("fee")) - _float(fill.get("fee"))) <= 1.0
        if quantity_match and value_match and fee_match:
            return candidate, "matched by uuid/client id, side, quantity, value, and fee"
        if quantity_match and value_match:
            return candidate, "matched by uuid/client id, side, quantity, and value; fee differs"
    if candidates:
        return None, "uuid/client id candidates existed but price/quantity/fee did not match"
    return None, "no db order matched exchange uuid/client id"


def _index_by(rows: list[dict], field: str) -> dict[str, list[tuple[int, dict]]]:
    result: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    for index, row in enumerate(rows):
        value = str(row.get(field) or "")
        if value:
            result[value].append((index, row))
    return result


def _symbol(market: str) -> str:
    return market.split("-")[-1] if "-" in market else market


def _to_utc_string(value: Any) -> str:
    parsed = _parse_utc(value)
    if parsed is None:
        return str(value or "")
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def _num(value: Any) -> str:
    return f"{_float(value):.12f}"


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
