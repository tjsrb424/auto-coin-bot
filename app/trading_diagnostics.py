from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from app.database import get_connection, load_global_bot_operation_policy

OPEN_POSITION_STATUSES = ("OPEN", "EXIT_CANDIDATE", "EXIT_PENDING", "CLOSING", "MANUAL_REVIEW_REQUIRED")
ORDER_ACTIVE_STATUSES = ("SUBMITTED", "WAITING", "PARTIALLY_FILLED")
ORDER_FILLED_STATUSES = ("FILLED",)


def build_trading_diagnostics_report(
    *,
    exchange: str = "bithumb",
    days: int = 7,
    starting_asset_krw: float | None = None,
    asset_reconciliation: dict | None = None,
) -> dict:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    start = now - timedelta(days=max(int(days), 1))
    start_iso = _to_iso(start)
    policy = load_global_bot_operation_policy()
    start_asset = _float(
        starting_asset_krw,
        _float(os.getenv("DIAGNOSTIC_STARTING_ASSET_KRW"), _float(policy.get("max_total_exposure_krw"), 300000.0)),
    )

    with get_connection() as conn:
        orders = [dict(row) for row in conn.execute(
            """
            SELECT *
            FROM live_order_logs
            WHERE exchange = ?
              AND created_at >= ?
              AND request_id NOT LIKE '%-submitted%'
              AND request_id NOT LIKE '%-waiting-%'
              AND request_id NOT LIKE '%-partial%'
              AND request_id NOT LIKE '%-canceled-%'
              AND request_id NOT LIKE '%-filled-%'
              AND request_id NOT LIKE '%-failed-%'
            ORDER BY created_at ASC, id ASC
            """,
            (exchange, start_iso),
        ).fetchall()]
        positions = [dict(row) for row in conn.execute(
            """
            SELECT *
            FROM live_positions
            WHERE exchange = ?
              AND (created_at >= ? OR closed_at >= ? OR status IN ('OPEN', 'EXIT_CANDIDATE', 'EXIT_PENDING', 'CLOSING', 'MANUAL_REVIEW_REQUIRED'))
            ORDER BY created_at ASC, id ASC
            """,
            (exchange, start_iso, start_iso),
        ).fetchall()]
        sessions = [dict(row) for row in conn.execute("SELECT * FROM live_strategy_sessions WHERE exchange = ?", (exchange,)).fetchall()]
        reservations = [dict(row) for row in conn.execute(
            "SELECT * FROM order_reservations WHERE exchange = ? AND created_at >= ? ORDER BY created_at ASC, id ASC",
            (exchange, start_iso),
        ).fetchall()]
        duplicate_order_uuids = [dict(row) for row in conn.execute(
            """
            SELECT order_uuid, COUNT(*) AS count, COUNT(DISTINCT COALESCE(order_purpose, 'ENTRY')) AS purpose_count
            FROM live_order_logs
            WHERE exchange = ?
              AND order_uuid IS NOT NULL
              AND order_uuid != ''
              AND created_at >= ?
            GROUP BY order_uuid
            HAVING COUNT(*) > 1 OR COUNT(DISTINCT COALESCE(order_purpose, 'ENTRY')) > 1
            ORDER BY count DESC
            LIMIT 20
            """,
            (exchange, start_iso),
        ).fetchall()]
        duplicate_fill_events = [dict(row) for row in conn.execute(
            """
            SELECT order_uuid, COUNT(*) AS count, SUM(applied_volume) AS applied_volume
            FROM position_fill_events
            WHERE applied_volume > 0
            GROUP BY order_uuid
            HAVING COUNT(*) > 1
            ORDER BY count DESC
            LIMIT 20
            """
        ).fetchall()]
        open_positions = [dict(row) for row in conn.execute(
            """
            SELECT *
            FROM live_positions
            WHERE exchange = ?
              AND status IN ('OPEN', 'EXIT_CANDIDATE', 'EXIT_PENDING', 'CLOSING', 'MANUAL_REVIEW_REQUIRED')
            ORDER BY market ASC, id ASC
            """,
            (exchange,),
        ).fetchall()]
        active_orders = [dict(row) for row in conn.execute(
            """
            SELECT *
            FROM live_order_logs
            WHERE exchange = ?
              AND status IN ('SUBMITTED', 'WAITING', 'PARTIALLY_FILLED')
              AND request_id NOT LIKE '%-submitted%'
              AND request_id NOT LIKE '%-waiting-%'
              AND request_id NOT LIKE '%-partial%'
              AND request_id NOT LIKE '%-canceled-%'
              AND request_id NOT LIKE '%-filled-%'
              AND request_id NOT LIKE '%-failed-%'
            ORDER BY created_at DESC, id DESC
            """,
            (exchange,),
        ).fetchall()]

    filled_orders = [row for row in orders if str(row.get("status")) in ORDER_FILLED_STATUSES]
    buy_orders = [row for row in filled_orders if _is_buy(row)]
    sell_orders = [row for row in filled_orders if _is_sell(row)]
    realized_pnl = sum(_float(row.get("realized_pnl")) for row in positions)
    unrealized_pnl = sum(_float(row.get("unrealized_pnl")) for row in positions if str(row.get("status")) in OPEN_POSITION_STATUSES)
    fee_total = sum(_float(row.get("paid_fee")) for row in filled_orders)
    gross_pnl = realized_pnl + unrealized_pnl + fee_total
    net_pnl = realized_pnl + unrealized_pnl
    current_asset = start_asset + net_pnl

    summary = {
        "period_days": max(int(days), 1),
        "period_start_utc": start_iso,
        "period_end_utc": _to_iso(now),
        "starting_asset_krw": start_asset,
        "current_asset_krw": current_asset,
        "total_pnl_krw": net_pnl,
        "cumulative_return_pct": (net_pnl / start_asset * 100) if start_asset > 0 else 0.0,
        "trade_count": len(filled_orders),
        "buy_count": len(buy_orders),
        "sell_count": len(sell_orders),
        "total_buy_amount_krw": sum(_order_amount(row) for row in buy_orders),
        "total_sell_amount_krw": sum(_order_amount(row) for row in sell_orders),
        "total_fee_krw": fee_total,
        "gross_pnl_krw": gross_pnl,
        "realized_pnl_krw": realized_pnl,
        "unrealized_pnl_krw": unrealized_pnl,
    }

    symbol_pnl = _symbol_pnl(orders, positions)
    strategy_pnl = _strategy_pnl(orders, positions)
    risk_diagnostics = _risk_diagnostics(
        orders=orders,
        sessions=sessions,
        reservations=reservations,
        duplicate_order_uuids=duplicate_order_uuids,
        duplicate_fill_events=duplicate_fill_events,
        open_positions=open_positions,
        active_orders=active_orders,
        summary=summary,
    )
    safety_limits = _safety_limits(policy, summary, open_positions)
    asset_report = _asset_reconciliation_report(
        starting_asset_krw=start_asset,
        realized_pnl_from_db=realized_pnl,
        unrealized_pnl_from_positions=unrealized_pnl,
        total_fee=fee_total,
        payload=asset_reconciliation or {},
    )
    restart_gate = _restart_gate(risk_diagnostics, safety_limits, summary, asset_report)
    return {
        "generated_at_utc": _to_iso(now),
        "exchange": exchange,
        "summary": summary,
        "asset_reconciliation": asset_report,
        "symbol_pnl": symbol_pnl,
        "strategy_pnl": strategy_pnl,
        "risk_diagnostics": risk_diagnostics,
        "safety_limits": safety_limits,
        "restart_gate": restart_gate,
    }


def restart_block_reason(exchange: str = "bithumb") -> dict:
    report = build_trading_diagnostics_report(exchange=exchange)
    gate = report["restart_gate"]
    return {
        "allowed": bool(gate.get("allowed")),
        "block_code": None if gate.get("allowed") else "LIVE_RESTART_BLOCKED_BY_DIAGNOSTICS",
        "reasons": gate.get("reasons", []),
        "report": report,
    }


def _symbol_pnl(orders: list[dict], positions: list[dict]) -> list[dict]:
    rows: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "symbol": "",
        "trade_count": 0,
        "buy_count": 0,
        "sell_count": 0,
        "gross_pnl": 0.0,
        "fee_total": 0.0,
        "net_pnl": 0.0,
        "win_count": 0,
        "loss_count": 0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "max_loss_trade": 0.0,
        "_wins": [],
        "_losses": [],
    })
    for order in orders:
        if str(order.get("status")) != "FILLED":
            continue
        market = str(order.get("market") or "")
        symbol = _symbol(market)
        row = rows[symbol]
        row["symbol"] = symbol
        row["trade_count"] += 1
        row["fee_total"] += _float(order.get("paid_fee"))
        if _is_buy(order):
            row["buy_count"] += 1
        if _is_sell(order):
            row["sell_count"] += 1
    for position in positions:
        symbol = _symbol(str(position.get("market") or ""))
        row = rows[symbol]
        row["symbol"] = symbol
        pnl = _float(position.get("realized_pnl")) + (
            _float(position.get("unrealized_pnl")) if str(position.get("status")) in OPEN_POSITION_STATUSES else 0.0
        )
        row["net_pnl"] += pnl
        row["gross_pnl"] += pnl
        if str(position.get("status")) == "CLOSED":
            realized = _float(position.get("realized_pnl"))
            if realized > 0:
                row["_wins"].append(realized)
            elif realized < 0:
                row["_losses"].append(realized)
    return _finalize_pnl_rows(rows.values(), "symbol")


def _strategy_pnl(orders: list[dict], positions: list[dict]) -> list[dict]:
    rows: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "strategy_name": "",
        "trade_count": 0,
        "gross_pnl": 0.0,
        "fee_total": 0.0,
        "net_pnl": 0.0,
        "win_count": 0,
        "loss_count": 0,
        "avg_holding_minutes": 0.0,
        "_wins": [],
        "_losses": [],
        "_holding": [],
    })
    for order in orders:
        if str(order.get("status")) != "FILLED":
            continue
        name = str(order.get("strategy_name") or "unknown")
        row = rows[name]
        row["strategy_name"] = name
        row["trade_count"] += 1
        row["fee_total"] += _float(order.get("paid_fee"))
    for position in positions:
        name = str(position.get("strategy_name") or "unknown")
        row = rows[name]
        row["strategy_name"] = name
        pnl = _float(position.get("realized_pnl")) + (
            _float(position.get("unrealized_pnl")) if str(position.get("status")) in OPEN_POSITION_STATUSES else 0.0
        )
        row["net_pnl"] += pnl
        row["gross_pnl"] += pnl
        if str(position.get("status")) == "CLOSED":
            realized = _float(position.get("realized_pnl"))
            if realized > 0:
                row["_wins"].append(realized)
            elif realized < 0:
                row["_losses"].append(realized)
            opened = _parse_utc(position.get("opened_at") or position.get("created_at"))
            closed = _parse_utc(position.get("closed_at"))
            if opened and closed and closed >= opened:
                row["_holding"].append((closed - opened).total_seconds() / 60)
    result = []
    for row in rows.values():
        wins = row.pop("_wins")
        losses = row.pop("_losses")
        holding = row.pop("_holding")
        total = len(wins) + len(losses)
        row["win_count"] = len(wins)
        row["loss_count"] = len(losses)
        row["win_rate"] = len(wins) / total if total else 0.0
        row["avg_holding_minutes"] = sum(holding) / len(holding) if holding else 0.0
        row["gross_pnl"] = row["net_pnl"] + row["fee_total"]
        result.append(row)
    return sorted(result, key=lambda item: item["net_pnl"])


def _finalize_pnl_rows(rows: Any, key: str) -> list[dict]:
    result = []
    for row in rows:
        wins = row.pop("_wins")
        losses = row.pop("_losses")
        total = len(wins) + len(losses)
        row["win_count"] = len(wins)
        row["loss_count"] = len(losses)
        row["win_rate"] = len(wins) / total if total else 0.0
        row["avg_win"] = sum(wins) / len(wins) if wins else 0.0
        row["avg_loss"] = sum(losses) / len(losses) if losses else 0.0
        row["max_loss_trade"] = min(losses) if losses else 0.0
        row["gross_pnl"] = row["net_pnl"] + row["fee_total"]
        result.append(row)
    return sorted(result, key=lambda item: item["net_pnl"])


def _risk_diagnostics(
    *,
    orders: list[dict],
    sessions: list[dict],
    reservations: list[dict],
    duplicate_order_uuids: list[dict],
    duplicate_fill_events: list[dict],
    open_positions: list[dict],
    active_orders: list[dict],
    summary: dict,
) -> dict:
    sessions_by_id = {int(row["id"]): row for row in sessions if row.get("id") is not None}
    reservations_by_candidate = defaultdict(list)
    for reservation in reservations:
        reservations_by_candidate[int(reservation.get("candidate_strategy_id") or 0)].append(reservation)

    symbol_open = defaultdict(list)
    for position in open_positions:
        symbol_open[str(position.get("market") or "")].append(position)
    duplicate_symbols = [
        {"market": market, "count": len(rows), "position_ids": [row["id"] for row in rows]}
        for market, rows in symbol_open.items()
        if len(rows) > 1
    ]

    duplicate_session_orders = _group_orders(
        orders,
        ("session_id", "market", "side", "candle_time_utc"),
        lambda grouped: len(grouped) > 1 and grouped[0].get("session_id") is not None and grouped[0].get("candle_time_utc"),
    )
    duplicate_candle_executions = _group_orders(
        [row for row in orders if str(row.get("status")) in {"FILLED", "SUBMITTED", "WAITING"}],
        ("session_id", "market", "candle_time_utc"),
        lambda grouped: len(grouped) > 1 and grouped[0].get("session_id") is not None and grouped[0].get("candle_time_utc"),
    )

    stopped_session_trades = []
    expired_reservation_executions = []
    incomplete_candle_usage = []
    timestamp_mismatches = []
    for order in orders:
        created = _parse_utc(order.get("created_at"))
        candle = _parse_utc(order.get("candle_time_utc"))
        session = sessions_by_id.get(int(order.get("session_id") or 0))
        stopped = _parse_utc((session or {}).get("stopped_at"))
        if session and stopped and created and created > stopped and str(order.get("status")) in {"SUBMITTED", "WAITING", "FILLED"}:
            stopped_session_trades.append(_order_sample(order))
        for reservation in reservations_by_candidate.get(int(order.get("candidate_strategy_id") or 0), []):
            expires = _parse_utc(reservation.get("expires_at"))
            if expires and created and created > expires and str(order.get("status")) in {"SUBMITTED", "WAITING", "FILLED"}:
                expired_reservation_executions.append(_order_sample(order) | {"reservation_id": reservation.get("id")})
                break
        if created and candle and created < candle + timedelta(minutes=1) and str(order.get("status")) in {"SUBMITTED", "WAITING", "FILLED"}:
            incomplete_candle_usage.append(_order_sample(order))
        candle_raw = str(order.get("candle_close_at_utc") or order.get("candle_time_utc") or "")
        signal_raw = str(order.get("signal_generated_at_utc") or "")
        requested_raw = str(order.get("order_requested_at_utc") or "")
        if any(_timestamp_is_non_utc(raw) for raw in (candle_raw, signal_raw, requested_raw)):
            timestamp_mismatches.append(_order_sample(order))

    max_trade_count = _int_env("MAX_TRADE_COUNT_PER_DAY", _int_env("RISK_MAX_ORDERS_PER_DAY", 0))
    fee_turnover = summary["total_buy_amount_krw"] + summary["total_sell_amount_krw"]
    fee_rate = summary["total_fee_krw"] / fee_turnover if fee_turnover > 0 else 0.0
    return {
        "duplicate_open_symbols": {"count": len(duplicate_symbols), "items": duplicate_symbols[:10]},
        "duplicate_session_orders": {"count": len(duplicate_session_orders), "items": duplicate_session_orders[:10]},
        "stopped_session_trades": {"count": len(stopped_session_trades), "items": stopped_session_trades[:10]},
        "expired_reservation_executions": {"count": len(expired_reservation_executions), "items": expired_reservation_executions[:10]},
        "duplicate_candle_executions": {"count": len(duplicate_candle_executions), "items": duplicate_candle_executions[:10]},
        "incomplete_candle_usage": {"count": len(incomplete_candle_usage), "items": incomplete_candle_usage[:10]},
        "timestamp_mismatches": {"count": len(timestamp_mismatches), "items": timestamp_mismatches[:10]},
        "duplicate_order_uuid": {"count": len(duplicate_order_uuids), "items": duplicate_order_uuids[:10]},
        "duplicate_fill_events": {"count": len(duplicate_fill_events), "items": duplicate_fill_events[:10]},
        "active_open_order_count": len(active_orders),
        "open_position_count": len(open_positions),
        "overtrade": {
            "limit": max_trade_count,
            "trade_count": summary["trade_count"],
            "breached": bool(max_trade_count > 0 and summary["trade_count"] >= max_trade_count),
        },
        "fee_pressure": {
            "fee_total_krw": summary["total_fee_krw"],
            "gross_turnover_krw": fee_turnover,
            "fee_to_turnover_pct": fee_rate * 100,
            "warning": fee_rate >= _float(os.getenv("FEE_PRESSURE_WARNING_RATE"), 0.002),
        },
    }


def _safety_limits(policy: dict, summary: dict, open_positions: list[dict]) -> dict:
    max_total = _float(policy.get("max_total_exposure_krw"), 300000.0)
    daily_max_loss_krw = _float(os.getenv("DAILY_MAX_LOSS_KRW"), _float(os.getenv("RISK_MAX_DAILY_LOSS_KRW"), 10000.0))
    daily_max_loss_rate = _loss_rate_env()
    max_open_positions = _int_env("MAX_OPEN_POSITIONS", _int_env("AUTO_MAX_OPEN_POSITION_COUNT", 5))
    max_symbol_rate = _float(os.getenv("MAX_SYMBOL_ALLOCATION_RATE"), _float(os.getenv("AUTO_SINGLE_POSITION_MAX_EXPOSURE_PCT"), 45.0) / 100.0)
    max_trade_count = _int_env("MAX_TRADE_COUNT_PER_DAY", _int_env("RISK_MAX_ORDERS_PER_DAY", 0))
    consecutive_stop = _int_env("STOP_AFTER_CONSECUTIVE_LOSSES", _int_env("RISK_MAX_CONSECUTIVE_LOSSES", 4))
    cooldown_minutes = _int_env("COOLDOWN_AFTER_LOSS_MINUTES", _int_env("RISK_MIN_COOLDOWN_SECONDS", 1800) // 60)
    loss_abs = abs(min(_float(summary.get("total_pnl_krw")), 0.0))
    loss_rate = loss_abs / max(_float(summary.get("starting_asset_krw")), 1.0)
    return {
        "daily_max_loss_krw": daily_max_loss_krw,
        "daily_max_loss_rate": daily_max_loss_rate,
        "daily_loss_limit_reached": loss_abs >= daily_max_loss_krw or loss_rate >= daily_max_loss_rate,
        "max_open_positions": max_open_positions,
        "open_position_count": len(open_positions),
        "max_open_positions_reached": bool(max_open_positions > 0 and len(open_positions) >= max_open_positions),
        "max_symbol_allocation_rate": max_symbol_rate,
        "max_symbol_allocation_krw": max_total * max_symbol_rate if max_total > 0 else 0.0,
        "max_trade_count_per_day": max_trade_count,
        "cooldown_after_loss_minutes": cooldown_minutes,
        "stop_after_consecutive_losses": consecutive_stop,
        "paper_mode_recommended": True,
    }


def _asset_reconciliation_report(
    *,
    starting_asset_krw: float,
    realized_pnl_from_db: float,
    unrealized_pnl_from_positions: float,
    total_fee: float,
    payload: dict,
) -> dict:
    deposits = _float(payload.get("deposits"), 0.0)
    withdrawals = _float(payload.get("withdrawals"), 0.0)
    initial_equity = _float(payload.get("initial_equity"), starting_asset_krw)
    current_cash = _float(payload.get("current_cash_krw"), 0.0)
    current_coin_value = _float(payload.get("current_coin_market_value"), 0.0)
    current_equity = payload.get("current_equity_from_exchange")
    if current_equity is None:
        current_equity = current_cash + current_coin_value if (current_cash or current_coin_value) else None
    expected_equity = initial_equity + deposits - withdrawals + realized_pnl_from_db + unrealized_pnl_from_positions - total_fee
    equity_diff = None if current_equity is None else _float(current_equity) - expected_equity
    equity_diff_rate = None if equity_diff is None else abs(equity_diff) / max(initial_equity, 1.0)
    return {
        "initial_equity": initial_equity,
        "current_equity_from_exchange": current_equity,
        "current_cash_krw": current_cash,
        "current_coin_market_value": current_coin_value,
        "realized_pnl_from_db": realized_pnl_from_db,
        "unrealized_pnl_from_positions": unrealized_pnl_from_positions,
        "total_fee": total_fee,
        "deposits": deposits,
        "withdrawals": withdrawals,
        "expected_equity": expected_equity,
        "equity_diff": equity_diff,
        "equity_diff_rate": equity_diff_rate,
        "gate_failed": bool(equity_diff is not None and (abs(equity_diff) > 100.0 or (equity_diff_rate or 0.0) > 0.001)),
    }


def _restart_gate(risk: dict, limits: dict, summary: dict, asset_reconciliation: dict | None = None) -> dict:
    reasons = []
    critical_checks = [
        ("DUPLICATE_OPEN_SYMBOL", risk["duplicate_open_symbols"]["count"]),
        ("DUPLICATE_SESSION_ORDER", risk["duplicate_session_orders"]["count"]),
        ("STOPPED_SESSION_TRADED", risk["stopped_session_trades"]["count"]),
        ("EXPIRED_RESERVATION_EXECUTED", risk["expired_reservation_executions"]["count"]),
        ("DUPLICATE_CANDLE_EXECUTION", risk["duplicate_candle_executions"]["count"]),
        ("INCOMPLETE_CANDLE_USED", risk["incomplete_candle_usage"]["count"]),
        ("TIMESTAMP_FORMAT_MISMATCH", risk["timestamp_mismatches"]["count"]),
        ("DUPLICATE_ORDER_UUID", risk["duplicate_order_uuid"]["count"]),
        ("DUPLICATE_FILL_EVENT", risk["duplicate_fill_events"]["count"]),
    ]
    for code, count in critical_checks:
        if count:
            reasons.append({"code": code, "count": count})
    if limits.get("daily_loss_limit_reached"):
        reasons.append({"code": "DAILY_LOSS_LIMIT_REACHED", "count": 1})
    if risk["overtrade"]["breached"]:
        reasons.append({"code": "MAX_TRADE_COUNT_REACHED", "count": risk["overtrade"]["trade_count"]})
    if risk["fee_pressure"]["warning"]:
        reasons.append({"code": "FEE_PRESSURE_WARNING", "count": 1})
    if (asset_reconciliation or {}).get("gate_failed"):
        reasons.append({"code": "EQUITY_RECONCILIATION_DIFF", "count": 1})
    if _float(summary.get("total_pnl_krw")) < 0:
        reasons.append({"code": "SEVEN_DAY_PNL_NEGATIVE", "count": 1})
    return {
        "allowed": len(reasons) == 0,
        "mode": "PAPER_FIRST" if reasons else "LIVE_ALLOWED_BY_DIAGNOSTICS",
        "stop_reason": reasons[0]["code"] if reasons else None,
        "reasons": reasons,
    }


def _group_orders(orders: list[dict], keys: tuple[str, ...], predicate: Any) -> list[dict]:
    grouped: dict[tuple[Any, ...], list[dict]] = defaultdict(list)
    for order in orders:
        grouped[tuple(order.get(key) for key in keys)].append(order)
    result = []
    for key, rows in grouped.items():
        if predicate(rows):
            result.append({"key": dict(zip(keys, key)), "count": len(rows), "orders": [_order_sample(row) for row in rows[:5]]})
    return result


def _order_sample(order: dict) -> dict:
    return {
        "id": order.get("id"),
        "request_id": order.get("request_id"),
        "order_uuid": order.get("order_uuid"),
        "session_id": order.get("session_id"),
        "market": order.get("market"),
        "side": order.get("side"),
        "order_purpose": order.get("order_purpose"),
        "status": order.get("status"),
        "risk_result": order.get("risk_result"),
        "created_at": order.get("created_at"),
        "candle_time_utc": order.get("candle_time_utc"),
        "candle_close_at_utc": order.get("candle_close_at_utc"),
    }


def _order_amount(order: dict) -> float:
    return _float(order.get("filled_amount_krw")) or _float(order.get("amount_krw")) or (_float(order.get("price")) * _float(order.get("executed_volume") or order.get("volume")))


def _is_buy(order: dict) -> bool:
    return str(order.get("side") or "").upper() in {"BUY", "BID"} or str(order.get("order_purpose") or "").upper() in {"ENTRY", "SCALE_IN"}


def _is_sell(order: dict) -> bool:
    return str(order.get("side") or "").upper() in {"SELL", "ASK"} or str(order.get("order_purpose") or "").upper() == "EXIT"


def _symbol(market: str) -> str:
    return market.split("-")[-1] if "-" in market else market or "UNKNOWN"


def _loss_rate_env() -> float:
    raw = _float(os.getenv("DAILY_MAX_LOSS_RATE"), 0.03)
    return raw / 100.0 if raw > 1 else raw


def _int_env(key: str, default: int) -> int:
    try:
        return int(float(os.getenv(key, str(default))))
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_utc(value: Any) -> datetime | None:
    if not value:
        return None


def _timestamp_is_non_utc(value: str) -> bool:
    if not value:
        return False
    normalized = str(value).replace(" ", "T")
    if normalized.endswith("Z"):
        return False
    if normalized.endswith("+00:00"):
        return False
    return True
    normalized = str(value).replace(" ", "T")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    if "+" not in normalized[-6:] and "-" not in normalized[-6:]:
        normalized = f"{normalized}+00:00"
    try:
        return datetime.fromisoformat(normalized).astimezone(timezone.utc)
    except ValueError:
        return None


def _to_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
