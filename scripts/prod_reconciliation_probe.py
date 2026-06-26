from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import httpx

from app.live_broker import BithumbBroker


DB_PATH = os.environ["ANALYSIS_DB_PATH"]
DAYS = int(os.getenv("RECONCILIATION_DAYS", "7"))
INITIAL = float(os.getenv("RECONCILIATION_INITIAL_EQUITY", "300000"))
FEE_RATE = float(os.getenv("LIVE_FEE_RATE", "0.0005"))
BASE_URL = os.getenv("BITHUMB_BASE_URL", "https://api.bithumb.com").rstrip("/")
OPEN_STATUSES = {"OPEN", "EXIT_CANDIDATE", "EXIT_PENDING", "CLOSING", "MANUAL_REVIEW_REQUIRED"}


def f(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def rows(sql: str, args: tuple = ()) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    result = [dict(row) for row in conn.execute(sql, args).fetchall()]
    conn.close()
    return result


def side(row: dict) -> str:
    value = str(row.get("side") or "").upper()
    if value == "BID":
        return "BUY"
    if value == "ASK":
        return "SELL"
    return value


def order_amount(row: dict) -> float:
    return f(row.get("filled_amount_krw")) or f(row.get("amount_krw")) or f(row.get("price")) * (
        f(row.get("executed_volume")) or f(row.get("volume"))
    )


def normalize_db_order(row: dict) -> dict:
    volume = f(row.get("executed_volume")) or f(row.get("volume"))
    amount = order_amount(row)
    price = f(row.get("price")) or (amount / volume if volume else 0.0)
    return {
        "source": "db",
        "id": row.get("id"),
        "request_id": row.get("request_id"),
        "exchange_order_uuid": str(row.get("order_uuid") or ""),
        "client_order_id": str(row.get("client_order_id") or ""),
        "market": row.get("market"),
        "symbol": str(row.get("market") or "").split("-")[-1],
        "strategy": row.get("strategy_name"),
        "session_id": row.get("session_id"),
        "side": side(row),
        "order_purpose": row.get("order_purpose"),
        "price": price,
        "quantity": volume,
        "amount_krw": amount,
        "fee": f(row.get("paid_fee")),
        "created_at_utc": row.get("created_at"),
        "executed_at_utc": row.get("order_executed_at_utc") or row.get("updated_at"),
    }


def normalize_exchange_order(order: dict, trade: dict | None = None) -> dict:
    source = trade or order
    volume = f(source.get("volume")) or f(source.get("executed_volume")) or f(order.get("executed_volume"))
    amount = (
        f(source.get("funds"))
        or f(source.get("executed_funds"))
        or f(order.get("executed_funds"))
        or f(source.get("filled_amount_krw"))
    )
    price = f(source.get("price")) or (amount / volume if volume else 0.0)
    if not amount and price and volume:
        amount = price * volume
    market = str(order.get("market") or source.get("market") or "")
    return {
        "source": "exchange",
        "exchange_order_uuid": str(order.get("uuid") or order.get("order_uuid") or ""),
        "client_order_id": str(order.get("client_order_id") or order.get("identifier") or ""),
        "trade_uuid": str(source.get("uuid") or source.get("trade_uuid") or ""),
        "market": market,
        "symbol": market.split("-")[-1],
        "side": side(order),
        "price": price,
        "quantity": volume,
        "amount_krw": amount,
        "fee": f(source.get("fee")) or f(source.get("paid_fee")) or f(order.get("paid_fee")),
        "fee_currency": source.get("fee_currency") or "KRW",
        "executed_at_utc": source.get("created_at") or source.get("executed_at") or order.get("created_at"),
    }


def normalize_exchange_orders(exchange_orders: list[dict]) -> list[dict]:
    fills = []
    for order in exchange_orders:
        trades = order.get("trades")
        if isinstance(trades, list) and trades:
            fills.extend(normalize_exchange_order(order, trade) for trade in trades)
            continue
        fill = normalize_exchange_order(order)
        if fill["quantity"] or fill["amount_krw"]:
            fills.append(fill)
    return fills


def parse_utc(value: object) -> datetime | None:
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


def duplicate_groups(items: list[dict], field: str) -> list[dict]:
    counts = Counter(str(item.get(field) or "") for item in items if str(item.get(field) or ""))
    result = []
    for value, count in counts.items():
        if count <= 1:
            continue
        group = [item for item in items if str(item.get(field) or "") == value]
        result.append(
            {
                "value": value,
                "count": count,
                "order_ids": [item.get("id") for item in group[:10]],
                "amount_krw": sum(order_amount(item) for item in group[1:]),
            }
        )
    return result


def match_fills(db_fills: list[dict], exchange_fills: list[dict]) -> tuple[list[dict], list[dict], int]:
    db_by_uuid = defaultdict(list)
    db_by_client = defaultdict(list)
    for index, fill in enumerate(db_fills):
        if fill["exchange_order_uuid"]:
            db_by_uuid[fill["exchange_order_uuid"]].append((index, fill))
        if fill["client_order_id"]:
            db_by_client[fill["client_order_id"]].append((index, fill))
    matched_db = set()
    matched_exchange = set()
    for ex_index, ex_fill in enumerate(exchange_fills):
        candidates = db_by_uuid.get(ex_fill["exchange_order_uuid"], []) + db_by_client.get(ex_fill["client_order_id"], [])
        for db_index, db_fill in candidates:
            if db_index in matched_db:
                continue
            if (
                db_fill["market"] == ex_fill["market"]
                and db_fill["side"] == ex_fill["side"]
                and abs(db_fill["quantity"] - ex_fill["quantity"]) <= 0.00000001
                and abs(db_fill["amount_krw"] - ex_fill["amount_krw"]) <= 5
            ):
                matched_db.add(db_index)
                matched_exchange.add(ex_index)
                break
    return (
        [exchange_fills[index] for index in range(len(exchange_fills)) if index not in matched_exchange],
        [db_fills[index] for index in range(len(db_fills)) if index not in matched_db],
        len(matched_exchange),
    )


async def fetch_prices(markets: set[str]) -> tuple[dict[str, float], list[dict]]:
    prices: dict[str, float] = {}
    errors = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for market in sorted(market for market in markets if market):
            try:
                response = await client.get(f"{BASE_URL}/v1/ticker", params={"markets": market})
                payload = response.json()
                item = payload[0] if isinstance(payload, list) and payload else payload if isinstance(payload, dict) else {}
                prices[market] = f(item.get("trade_price") or item.get("prev_closing_price") or item.get("close_price"))
            except Exception as exc:
                prices[market] = 0.0
                errors.append({"market": market, "error": str(exc)[:120]})
    return prices, errors


async def collect_exchange_orders(broker: BithumbBroker, markets: set[str], db_orders: list[dict]) -> tuple[list[dict], list[dict]]:
    exchange_orders_by_uuid: dict[str, dict] = {}
    errors = []
    for market in sorted(market for market in markets if market):
        for state in ("done", "cancel", "wait"):
            try:
                payload = await broker._request("GET", "/v1/orders", {"market": market, "state": state, "page": 1, "limit": 100})
                if isinstance(payload, dict):
                    payload = [payload]
                for order in payload or []:
                    if isinstance(order, dict):
                        key = str(order.get("uuid") or f"{market}:{state}:{len(exchange_orders_by_uuid)}")
                        exchange_orders_by_uuid[key] = order
            except Exception as exc:
                errors.append({"market": market, "state": state, "error": str(exc)[:180]})
    for order_uuid in dict.fromkeys(str(order.get("order_uuid") or "") for order in db_orders if order.get("order_uuid")):
        if order_uuid in exchange_orders_by_uuid:
            continue
        try:
            order = await broker.get_order(order_uuid)
            if isinstance(order, dict):
                exchange_orders_by_uuid[order_uuid] = order
        except Exception as exc:
            errors.append({"order_uuid": order_uuid, "error": str(exc)[:180]})
    return list(exchange_orders_by_uuid.values()), errors


def order_sample(order: dict) -> dict:
    return {
        "id": order.get("id"),
        "request_id": order.get("request_id"),
        "related_exchange_order_uuid": order.get("order_uuid"),
        "symbol": str(order.get("market") or "").split("-")[-1],
        "market": order.get("market"),
        "strategy": order.get("strategy_name"),
        "side": order.get("side"),
        "amount": order_amount(order),
        "price": order.get("price"),
        "fee": order.get("paid_fee"),
        "created_at_utc": order.get("created_at"),
        "executed_at_utc": order.get("order_executed_at_utc") or order.get("updated_at"),
        "risk_result": order.get("risk_result"),
        "status": order.get("status"),
        "exchange_linked": bool(order.get("order_uuid")),
        "estimated_pnl_impact_krw": order.get("actual_pnl") if order.get("actual_pnl") is not None else order.get("expected_pnl"),
    }


async def main() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    cutoff = (now - timedelta(days=DAYS)).isoformat().replace("+00:00", "Z")
    orders = rows(
        """
        SELECT * FROM live_order_logs
        WHERE exchange='bithumb' AND created_at >= ?
          AND request_id NOT LIKE '%-submitted%' AND request_id NOT LIKE '%-waiting-%'
          AND request_id NOT LIKE '%-partial%' AND request_id NOT LIKE '%-canceled-%'
          AND request_id NOT LIKE '%-filled-%' AND request_id NOT LIKE '%-failed-%'
        ORDER BY created_at ASC, id ASC
        """,
        (cutoff,),
    )
    positions = rows(
        """
        SELECT * FROM live_positions
        WHERE exchange='bithumb'
          AND (created_at >= ? OR closed_at >= ? OR status IN ('OPEN','EXIT_CANDIDATE','EXIT_PENDING','CLOSING','MANUAL_REVIEW_REQUIRED'))
        ORDER BY id ASC
        """,
        (cutoff, cutoff),
    )
    open_positions = rows(
        "SELECT * FROM live_positions WHERE exchange='bithumb' AND status IN ('OPEN','EXIT_CANDIDATE','EXIT_PENDING','CLOSING','MANUAL_REVIEW_REQUIRED') ORDER BY market,id"
    )
    sessions = rows("SELECT id,status,current_position_id,current_open_order_uuid,market,updated_at FROM live_strategy_sessions ORDER BY id DESC LIMIT 12")
    policy = rows("SELECT market,auto_trading_enabled FROM bot_operation_policy ORDER BY market")
    locks = rows("SELECT status,updated_at,expires_at FROM runtime_locks ORDER BY updated_at DESC LIMIT 3")
    fill_events = rows("SELECT * FROM position_fill_events ORDER BY id")
    application_ledger = rows("SELECT * FROM order_application_ledger ORDER BY id")
    recovery_events = rows(
        "SELECT event_type,severity,market,session_id,request_id,order_uuid,message,created_at,payload_json FROM live_recovery_events WHERE created_at >= ? ORDER BY id DESC LIMIT 200",
        (cutoff,),
    )

    filled_orders = [order for order in orders if str(order.get("status")) == "FILLED"]
    db_fills = [normalize_db_order(order) for order in filled_orders]
    realized = sum(f(position.get("realized_pnl")) for position in positions)
    unrealized = sum(f(position.get("unrealized_pnl")) for position in positions if str(position.get("status")) in OPEN_STATUSES)
    total_fee = sum(f(order.get("paid_fee")) for order in filled_orders)
    realized_fee = sum(
        f(order.get("paid_fee"))
        for order in filled_orders
        if side(order) == "SELL" or str(order.get("order_purpose") or "").upper() == "EXIT"
    )
    estimated_exit_fee = sum(
        max(f(position.get("current_price")) * f(position.get("entry_volume")) * FEE_RATE, 0.0)
        for position in open_positions
    )

    broker = BithumbBroker()
    balances = await broker.get_balances()
    by_currency = balances.get("by_currency", {})
    markets = {order.get("market") for order in orders if order.get("market")} | {position.get("market") for position in positions if position.get("market")}
    for currency, balance in by_currency.items():
        if currency != "KRW" and f(balance.get("balance")) + f(balance.get("locked")) > 0:
            markets.add(f"KRW-{currency}")
    prices, price_errors = await fetch_prices(markets)

    krw = by_currency.get("KRW", {})
    krw_available = f(krw.get("balance"))
    krw_locked = f(krw.get("locked"))
    coin_value = 0.0
    coin_locked_value = 0.0
    coins = []
    for currency, balance in sorted(by_currency.items()):
        if currency == "KRW":
            continue
        market = f"KRW-{currency}"
        total = f(balance.get("balance")) + f(balance.get("locked"))
        price = prices.get(market, 0.0)
        value = total * price
        locked_value = f(balance.get("locked")) * price
        if total or locked_value:
            coin_value += value
            coin_locked_value += locked_value
            coins.append(
                {
                    "market": market,
                    "available": f(balance.get("balance")),
                    "locked": f(balance.get("locked")),
                    "total": total,
                    "price": price,
                    "value_krw": value,
                    "locked_value_krw": locked_value,
                }
            )
    current_equity = krw_available + krw_locked + coin_value

    exchange_orders, exchange_errors = await collect_exchange_orders(broker, markets, orders)
    exchange_fills = normalize_exchange_orders(exchange_orders)
    start_dt = parse_utc(cutoff)
    if start_dt:
        exchange_fills = [fill for fill in exchange_fills if (parse_utc(fill.get("executed_at_utc")) or start_dt) >= start_dt]
    missing_exchange, db_only, matched_count = match_fills(db_fills, exchange_fills)

    db_by_market = defaultdict(float)
    for position in open_positions:
        db_by_market[position["market"]] += f(position.get("entry_volume"))
    exchange_by_market = {f"KRW-{currency}": f(balance.get("balance")) + f(balance.get("locked")) for currency, balance in by_currency.items() if currency != "KRW"}
    quantity_diffs = []
    for market in sorted(set(db_by_market) | set(exchange_by_market)):
        diff = db_by_market.get(market, 0.0) - exchange_by_market.get(market, 0.0)
        if abs(diff) > 0.00000001:
            quantity_diffs.append(
                {
                    "market": market,
                    "db_open_volume": db_by_market.get(market, 0.0),
                    "exchange_total": exchange_by_market.get(market, 0.0),
                    "diff": diff,
                    "price": prices.get(market, 0.0),
                    "diff_value_krw": diff * prices.get(market, 0.0),
                }
            )

    duplicate_fill_events = []
    fill_counts = Counter(event.get("order_uuid") for event in fill_events if f(event.get("applied_volume")) > 0)
    for order_uuid, count in fill_counts.items():
        if order_uuid and count > 1:
            duplicate_fill_events.append(
                {
                    "order_uuid": order_uuid,
                    "count": count,
                    "applied_volume": sum(f(event.get("applied_volume")) for event in fill_events if event.get("order_uuid") == order_uuid),
                }
            )

    legacy_expected = INITIAL + realized + unrealized - total_fee
    corrected_expected = INITIAL + realized + (unrealized - estimated_exit_fee)
    exchange_fee = sum(fill["fee"] for fill in exchange_fills)
    timestamp_samples = [
        order_sample(order)
        for order in orders
        if any(
            value and not (str(value).endswith("Z") or str(value).endswith("+00:00"))
            for value in (order.get("candle_time_utc"), order.get("candle_close_at_utc"), order.get("signal_generated_at_utc"), order.get("order_requested_at_utc"))
        )
    ]

    output = {
        "backup_db_path": DB_PATH,
        "period_start_utc": cutoff,
        "safety": {"policy": policy, "runtime_locks": locks, "recent_sessions": sessions},
        "balances": {
            "krw_available": krw_available,
            "krw_locked": krw_locked,
            "coin_value": coin_value,
            "coin_locked_value": coin_locked_value,
            "current_equity_from_exchange": current_equity,
            "coins": coins,
            "price_errors": price_errors[:20],
        },
        "db_pnl": {
            "filled_count": len(filled_orders),
            "realized_pnl_from_db": realized,
            "unrealized_pnl_from_positions": unrealized,
            "total_fee": total_fee,
            "realized_fee": realized_fee,
            "estimated_exit_fee": estimated_exit_fee,
            "buy_amount": sum(order_amount(order) for order in filled_orders if side(order) == "BUY"),
            "sell_amount": sum(order_amount(order) for order in filled_orders if side(order) == "SELL"),
        },
        "expected": {
            "legacy_expected_equity_with_double_fee": legacy_expected,
            "corrected_expected_equity_no_double_fee": corrected_expected,
            "legacy_equity_diff": current_equity - legacy_expected,
            "corrected_equity_diff": current_equity - corrected_expected,
        },
        "exchange_ledger": {
            "order_count": len(exchange_orders),
            "fill_count": len(exchange_fills),
            "matched_fill_count": matched_count,
            "errors": exchange_errors[:30],
            "deposits_withdrawals": "unavailable: no configured read-only broker method",
        },
        "mismatches": {
            "exchange_fill_missing_in_db": {
                "count": len(missing_exchange),
                "amount_krw": sum(item["amount_krw"] for item in missing_exchange),
                "items": missing_exchange[:20],
            },
            "db_only_trade": {
                "count": len(db_only),
                "amount_krw": sum(item["amount_krw"] for item in db_only),
                "items": db_only[:20],
            },
            "duplicate_exchange_uuid_in_db": duplicate_groups([order for order in orders if order.get("order_uuid")], "order_uuid")[:20],
            "duplicate_client_order_id_in_db": duplicate_groups([order for order in orders if order.get("client_order_id")], "client_order_id")[:20],
            "duplicate_position_fill_events": duplicate_fill_events[:20],
            "position_quantity_diff": quantity_diffs,
        },
        "fee": {"total_fee_from_db": total_fee, "total_fee_from_exchange": exchange_fee, "fee_diff": total_fee - exchange_fee},
        "integrity_samples": {
            "TIMESTAMP_FORMAT_MISMATCH": {"count": len(timestamp_samples), "items": timestamp_samples[:10]},
            "DUPLICATE_ORDER_UUID": duplicate_groups([order for order in orders if order.get("order_uuid")], "order_uuid")[:10],
            "FEE_PRESSURE_WARNING": {"fee_total": total_fee, "turnover": sum(order_amount(order) for order in filled_orders)},
            "EQUITY_RECONCILIATION_DIFF": {"legacy_amount": current_equity - legacy_expected, "corrected_amount": current_equity - corrected_expected},
            "recent_recovery_events": recovery_events[:20],
        },
        "order_application_ledger": {
            "count": len(application_ledger),
            "duplicate_order_uuid_count": len(
                [value for value, count in Counter(row.get("order_uuid") for row in application_ledger).items() if value and count > 1]
            ),
        },
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
