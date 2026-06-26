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


def build_exchange_fill_accounting_report(
    *,
    ledger_rows: list[dict],
    canonical_db_orders: list[dict],
    all_db_orders: list[dict],
    sessions: list[dict],
    position_fill_events: list[dict],
    trade_outcome_logs: list[dict],
    period_start_utc: str,
    period_end_utc: str,
) -> dict:
    canonical_filled_orders = [row for row in canonical_db_orders if str(row.get("status") or "").upper() == "FILLED"]
    canonical_by_uuid = _rows_by(canonical_filled_orders, "order_uuid")
    canonical_by_client = _rows_by(canonical_filled_orders, "client_order_id")
    all_by_uuid = _rows_by(all_db_orders, "order_uuid")
    all_by_client = _rows_by(all_db_orders, "client_order_id")
    fill_events_by_uuid = _rows_by(position_fill_events, "order_uuid")
    outcomes_by_uuid = _rows_by(trade_outcome_logs, "order_uuid")
    allowed_symbols = sorted({_symbol(str(row.get("market") or "")) for row in all_db_orders if row.get("market")})
    allowed_markets = sorted({str(row.get("market") or "") for row in all_db_orders if row.get("market")})
    allowed_strategies = sorted({str(row.get("strategy_name") or "") for row in all_db_orders if row.get("strategy_name")})
    session_start, session_end = _session_time_range(sessions)

    classified = []
    for row in ledger_rows:
        item = dict(row)
        order_uuid = str(item.get("exchange_order_uuid") or "")
        client_order_id = str(item.get("client_order_id") or "")
        canonical_match = _first(canonical_by_uuid.get(order_uuid)) or _first(canonical_by_client.get(client_order_id))
        db_order_match = _first(all_by_uuid.get(order_uuid)) or _first(all_by_client.get(client_order_id))
        session_match = _matching_session(item, sessions)
        ownership, reason = _ownership_for_fill(
            item,
            db_order_match=db_order_match,
            session_match=session_match,
            allowed_markets=allowed_markets,
            period_start_utc=period_start_utc,
            period_end_utc=period_end_utc,
        )
        position_event = _first(fill_events_by_uuid.get(order_uuid))
        outcome = _first(outcomes_by_uuid.get(order_uuid))
        accounting_status = _accounting_status(
            ownership=ownership,
            canonical_match=canonical_match,
            position_event=position_event,
            outcome=outcome,
        )
        item.update(
            {
                "ownership": ownership,
                "ownership_reason": reason,
                "matched_any_db_order_id": (db_order_match or {}).get("id"),
                "matched_canonical_live_order_log_id": (canonical_match or {}).get("id"),
                "matched_position_fill_event_id": (position_event or {}).get("id"),
                "matched_trade_outcome_log_id": (outcome or {}).get("id"),
                "accounting_status": accounting_status,
            }
        )
        classified.append(item)

    bot_owned = [row for row in classified if row["ownership"] in {"BOT_LIVE_CONFIRMED", "BOT_LIVE_SUSPECTED"}]
    manual_or_external = [row for row in classified if row["ownership"] == "MANUAL_OR_EXTERNAL"]
    out_of_scope = [row for row in classified if row["ownership"] == "OUT_OF_RECONCILIATION_SCOPE"]
    all_pnl = compute_realized_pnl_from_ledger(classified)
    bot_pnl = compute_realized_pnl_from_ledger(bot_owned)
    manual_pnl = compute_realized_pnl_from_ledger(manual_or_external)
    out_of_scope_pnl = compute_realized_pnl_from_ledger(out_of_scope)

    missing_live_position = [row for row in bot_owned if not row.get("matched_position_fill_event_id")]
    missing_strategy_pnl = [row for row in bot_owned if not row.get("matched_trade_outcome_log_id")]
    pending = [row for row in classified if row.get("accounting_status") in {"ACCOUNTING_PENDING", "ACCOUNTING_PARTIAL", "ACCOUNTING_FAILED"}]
    return {
        "reconciliation_scope": {
            "reconciliation_start_at_utc": period_start_utc,
            "reconciliation_end_at_utc": period_end_utc,
            "bot_live_session_started_at_utc": session_start,
            "bot_live_session_ended_at_utc": session_end,
            "allowed_live_symbols": allowed_symbols,
            "allowed_live_strategies": allowed_strategies,
        },
        "ownership_summary": _summary_by(classified, "ownership"),
        "accounting_status_summary": _summary_by(classified, "accounting_status"),
        "missing_fill_breakdown": {
            "exchange_fill_count": len(classified),
            "db_order_matched_fill_count": _count_with(classified, "matched_any_db_order_id"),
            "db_order_matched_fill_value": _sum_with(classified, "matched_any_db_order_id"),
            "db_trade_matched_fill_count": _count_with(classified, "matched_position_fill_event_id"),
            "db_trade_matched_fill_value": _sum_with(classified, "matched_position_fill_event_id"),
            "db_trade_source": "position_fill_events",
            "canonical_live_log_matched_fill_count": _count_with(classified, "matched_canonical_live_order_log_id"),
            "canonical_live_log_matched_fill_value": _sum_with(classified, "matched_canonical_live_order_log_id"),
            "missing_db_order_fill_count": _count_without(classified, "matched_any_db_order_id"),
            "missing_db_order_fill_value": _sum_without(classified, "matched_any_db_order_id"),
            "missing_db_trade_fill_count": _count_without(classified, "matched_position_fill_event_id"),
            "missing_db_trade_fill_value": _sum_without(classified, "matched_position_fill_event_id"),
            "missing_canonical_live_log_fill_count": _count_without(classified, "matched_canonical_live_order_log_id"),
            "missing_canonical_live_log_fill_value": _sum_without(classified, "matched_canonical_live_order_log_id"),
            "missing_live_position_accounting_fill_count": len(missing_live_position),
            "missing_live_position_accounting_fill_value": _sum_value(missing_live_position),
            "missing_strategy_pnl_fill_count": len(missing_strategy_pnl),
            "missing_strategy_pnl_fill_value": _sum_value(missing_strategy_pnl),
        },
        "pnl_by_ownership": {
            "exchange_net_realized_pnl_after_fee_all_fills": all_pnl["exchange_net_realized_pnl_after_fee"],
            "exchange_net_realized_pnl_after_fee_bot_owned": bot_pnl["exchange_net_realized_pnl_after_fee"],
            "exchange_net_realized_pnl_after_fee_manual_or_external": manual_pnl["exchange_net_realized_pnl_after_fee"],
            "exchange_net_realized_pnl_after_fee_out_of_scope": out_of_scope_pnl["exchange_net_realized_pnl_after_fee"],
            "manual_or_external_effect": manual_pnl["exchange_net_realized_pnl_after_fee"],
            "out_of_scope_effect": out_of_scope_pnl["exchange_net_realized_pnl_after_fee"],
        },
        "pnl_source_of_truth": {
            "actual_equity": "exchange_balance_equity",
            "realized_pnl": "exchange_fills_ledger",
            "strategy_pnl": "bot_owned_exchange_fills_ledger",
            "legacy_db_pnl": "legacy_debug_only",
        },
        "classified_fills_sample": _classified_samples(classified),
        "accounting_pending_count": len(pending),
        "accounting_pending_value": _sum_value(pending),
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


def _ownership_for_fill(
    row: dict,
    *,
    db_order_match: dict | None,
    session_match: dict | None,
    allowed_markets: list[str],
    period_start_utc: str,
    period_end_utc: str,
) -> tuple[str, str]:
    executed = _parse_utc(row.get("executed_at_utc"))
    start = _parse_utc(period_start_utc)
    end = _parse_utc(period_end_utc)
    if start and executed and executed < start:
        return "OUT_OF_RECONCILIATION_SCOPE", "before reconciliation_start_at_utc"
    if end and executed and executed > end:
        return "OUT_OF_RECONCILIATION_SCOPE", "after reconciliation_end_at_utc"
    if db_order_match:
        if str(db_order_match.get("client_order_id") or "") and str(db_order_match.get("client_order_id") or "") == str(row.get("client_order_id") or ""):
            return "BOT_LIVE_CONFIRMED", "matched by client_order_id"
        return "BOT_LIVE_CONFIRMED", "matched by exchange_order_uuid in DB order"
    if session_match:
        return "BOT_LIVE_SUSPECTED", "matched by live session time range and market"
    if allowed_markets and str(row.get("market") or "") not in allowed_markets:
        return "MANUAL_OR_EXTERNAL", "symbol not traded by bot in reconciliation window"
    return "MANUAL_OR_EXTERNAL", "no DB evidence"


def _accounting_status(
    *,
    ownership: str,
    canonical_match: dict | None,
    position_event: dict | None,
    outcome: dict | None,
) -> str:
    if ownership in {"MANUAL_OR_EXTERNAL", "OUT_OF_RECONCILIATION_SCOPE"}:
        return "ACCOUNTING_OUT_OF_SCOPE"
    if canonical_match and position_event and outcome:
        return "ACCOUNTING_SYNCED"
    if canonical_match or position_event or outcome:
        return "ACCOUNTING_PARTIAL"
    return "ACCOUNTING_PENDING"


def _matching_session(row: dict, sessions: list[dict]) -> dict | None:
    executed = _parse_utc(row.get("executed_at_utc"))
    if executed is None:
        return None
    market = str(row.get("market") or "")
    for session in sessions:
        if str(session.get("market") or "") != market:
            continue
        start = _parse_utc(session.get("created_at"))
        end = _parse_utc(session.get("stopped_at")) or _parse_utc(session.get("updated_at"))
        if start and executed < start:
            continue
        if end and executed > end:
            continue
        return session
    return None


def _session_time_range(sessions: list[dict]) -> tuple[str | None, str | None]:
    starts = [_parse_utc(row.get("created_at")) for row in sessions]
    ends = [_parse_utc(row.get("stopped_at")) or _parse_utc(row.get("updated_at")) for row in sessions]
    starts = [value for value in starts if value]
    ends = [value for value in ends if value]
    return (_to_utc_string(min(starts)) if starts else None, _to_utc_string(max(ends)) if ends else None)


def _rows_by(rows: list[dict], field: str) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        value = str(row.get(field) or "")
        if value:
            grouped[value].append(row)
    return grouped


def _first(rows: list[dict] | None) -> dict | None:
    return rows[0] if rows else None


def _summary_by(rows: list[dict], field: str) -> dict:
    counts = Counter(str(row.get(field) or "UNKNOWN") for row in rows)
    result = {}
    for key in sorted(counts):
        group = [row for row in rows if str(row.get(field) or "UNKNOWN") == key]
        result[key] = {"count": len(group), "value": _sum_value(group)}
    return result


def _count_with(rows: list[dict], field: str) -> int:
    return sum(1 for row in rows if row.get(field) is not None)


def _count_without(rows: list[dict], field: str) -> int:
    return sum(1 for row in rows if row.get(field) is None)


def _sum_with(rows: list[dict], field: str) -> float:
    return _sum_value([row for row in rows if row.get(field) is not None])


def _sum_without(rows: list[dict], field: str) -> float:
    return _sum_value([row for row in rows if row.get(field) is None])


def _sum_value(rows: list[dict]) -> float:
    return sum(_float(row.get("executed_value")) for row in rows)


def _classified_samples(rows: list[dict]) -> list[dict]:
    return [
        {
            "exchange_order_uuid": row.get("exchange_order_uuid"),
            "market": row.get("market"),
            "side": row.get("side"),
            "executed_value": row.get("executed_value"),
            "executed_at_utc": row.get("executed_at_utc"),
            "ownership": row.get("ownership"),
            "ownership_reason": row.get("ownership_reason"),
            "accounting_status": row.get("accounting_status"),
        }
        for row in rows[:50]
    ]


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
