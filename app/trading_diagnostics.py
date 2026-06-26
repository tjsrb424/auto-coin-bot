from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from app.database import get_connection, load_global_bot_operation_policy
from app.accounting_epoch import (
    build_current_epoch_diagnostics,
    build_smoke_test_preflight,
    legacy_history_quarantine,
    limited_auto_live_gate,
    split_restart_blockers,
)
from app.trading_reconciliation import build_equity_reconciliation

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
        orders=orders,
        positions=positions,
        payload=asset_reconciliation or {},
    )
    asset_report["legacy_strategy_pnl"] = strategy_pnl
    asset_report["legacy_symbol_pnl"] = symbol_pnl
    asset_report["strategy_pnl_diff"] = _pnl_diff_rows(
        asset_report.get("ledger_strategy_pnl", []),
        strategy_pnl,
        "strategy_name",
    )
    asset_report["symbol_pnl_diff"] = _pnl_diff_rows(
        asset_report.get("ledger_symbol_pnl", []),
        symbol_pnl,
        "symbol",
    )
    restart_gate = _restart_gate(risk_diagnostics, safety_limits, summary, asset_report)
    legacy_history = legacy_history_quarantine(asset_report)
    current_epoch = build_current_epoch_diagnostics(
        exchange=exchange,
        current_equity=asset_report.get("current_equity_from_exchange"),
    )
    smoke_preflight = build_smoke_test_preflight(exchange=exchange, current_epoch=current_epoch)
    limited_gate = limited_auto_live_gate(current_epoch, smoke_preflight, exchange=exchange)
    blocker_split = split_restart_blockers(restart_gate.get("reasons", []), current_epoch, smoke_preflight)
    return {
        "generated_at_utc": _to_iso(now),
        "exchange": exchange,
        "summary": summary,
        "asset_reconciliation": asset_report,
        "legacy_history": legacy_history,
        "current_epoch": current_epoch,
        "smoke_test_preflight": smoke_preflight,
        "limited_auto_live_gate": limited_gate,
        "full_auto_live_allowed": False,
        **blocker_split,
        "pnl_source_of_truth": asset_report.get("pnl_source_of_truth"),
        "legacy_db_pnl_is_debug_only": asset_report.get("legacy_db_pnl_is_debug_only"),
        "exchange_ledger_pnl_enabled": asset_report.get("exchange_ledger_pnl_enabled"),
        "strategy_pnl_source": asset_report.get("strategy_pnl_source"),
        "symbol_pnl_source": asset_report.get("symbol_pnl_source"),
        "dashboard_pnl_source": asset_report.get("dashboard_pnl_source"),
        "ledger_strategy_pnl": asset_report.get("ledger_strategy_pnl", []),
        "ledger_symbol_pnl": asset_report.get("ledger_symbol_pnl", []),
        "ledger_session_pnl": asset_report.get("ledger_session_pnl", []),
        "legacy_strategy_pnl": strategy_pnl,
        "legacy_symbol_pnl": symbol_pnl,
        "strategy_pnl_diff": asset_report.get("strategy_pnl_diff", []),
        "symbol_pnl_diff": asset_report.get("symbol_pnl_diff", []),
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


def _pnl_diff_rows(ledger_rows: list[dict], legacy_rows: list[dict], key: str) -> list[dict]:
    ledger_by_key = {str(row.get(key) or "unknown"): row for row in ledger_rows}
    legacy_by_key = {str(row.get(key) or "unknown"): row for row in legacy_rows}
    result = []
    for name in sorted(set(ledger_by_key) | set(legacy_by_key)):
        ledger = ledger_by_key.get(name, {})
        legacy = legacy_by_key.get(name, {})
        ledger_pnl = _float(ledger.get("total_pnl"), _float(ledger.get("net_pnl")))
        legacy_pnl = _float(legacy.get("net_pnl"))
        result.append(
            {
                key: name,
                "ledger_total_pnl": ledger_pnl,
                "legacy_net_pnl": legacy_pnl,
                "diff": ledger_pnl - legacy_pnl,
                "ledger_fill_count": _float(ledger.get("fill_count")),
                "legacy_trade_count": _float(legacy.get("trade_count")),
            }
        )
    return sorted(result, key=lambda item: abs(_float(item.get("diff"))), reverse=True)


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
    orders: list[dict],
    positions: list[dict],
    payload: dict,
) -> dict:
    deposits = _float(payload.get("deposits"), 0.0)
    withdrawals = _float(payload.get("withdrawals"), 0.0)
    asset_reconciliation_requested = bool(payload)
    initial_equity = _float(payload.get("initial_equity"), starting_asset_krw)
    current_cash = _float(payload.get("current_cash_krw"), 0.0)
    current_coin_value = _float(payload.get("current_coin_market_value"), 0.0)
    current_equity = payload.get("current_equity_from_exchange")
    if current_equity is None:
        current_equity = current_cash + current_coin_value if (current_cash or current_coin_value) else None
    snapshot_unrealized_pnl = _float(payload.get("snapshot_unrealized_pnl"), unrealized_pnl_from_positions)
    reconciliation = build_equity_reconciliation(
        initial_equity=initial_equity,
        current_equity_from_exchange=_float(current_equity) if current_equity is not None else None,
        realized_pnl_from_db=realized_pnl_from_db,
        unrealized_pnl_from_positions=snapshot_unrealized_pnl,
        total_fee_from_db=total_fee,
        db_orders=orders,
        db_positions=positions,
        exchange_orders=payload.get("exchange_orders") if "exchange_orders" in payload else None,
        exchange_balances=payload.get("balances") if isinstance(payload.get("balances"), dict) else {},
        valuation_prices=payload.get("valuation_prices") if isinstance(payload.get("valuation_prices"), dict) else {},
        period_start_utc=str(payload.get("period_start_utc") or ""),
        deposits=deposits,
        withdrawals=withdrawals,
        fee_rate=_float(os.getenv("LIVE_FEE_RATE"), 0.0005),
        open_order_fee_adjustment=_float(payload.get("open_order_fee_adjustment"), 0.0),
    )
    equity_breakdown = dict(reconciliation["equity_diff_breakdown"])
    ledger_summary = payload.get("exchange_fills_ledger_summary", {}) if isinstance(payload.get("exchange_fills_ledger_summary"), dict) else {}
    accounting = payload.get("exchange_fill_accounting", {}) if isinstance(payload.get("exchange_fill_accounting"), dict) else {}
    pnl_by_ownership = accounting.get("pnl_by_ownership", {}) if isinstance(accounting.get("pnl_by_ownership"), dict) else {}
    unexplained_before_deposit_estimate = _float(equity_breakdown.get("unexplained"))
    if abs(unexplained_before_deposit_estimate) > 0.000001 and payload.get("deposit_withdrawal_status", "UNAVAILABLE") == "UNAVAILABLE":
        equity_breakdown["deposit_withdrawal_mismatch"] = 0.0
        equity_breakdown["unavailable_deposit_withdrawal_ledger"] = unexplained_before_deposit_estimate
        equity_breakdown["reconciliation_window_mismatch"] = 0.0
        equity_breakdown["initial_equity_snapshot_mismatch"] = 0.0
        equity_breakdown["manual_or_external_trade_effect"] = _float(pnl_by_ownership.get("manual_or_external_effect"), 0.0)
        equity_breakdown["out_of_scope_fill_effect"] = _float(pnl_by_ownership.get("out_of_scope_effect"), 0.0)
        equity_breakdown["valuation_snapshot_effect"] = _float(payload.get("stale_valuation_effect"), 0.0)
        equity_breakdown["remaining_deposit_withdrawal_unknown"] = unexplained_before_deposit_estimate
        equity_breakdown["unexplained_before_deposit_withdrawal_estimate"] = unexplained_before_deposit_estimate
        equity_breakdown["unexplained"] = 0.0
        equity_breakdown["unexplained_rate"] = 0.0
        equity_breakdown["deposit_withdrawal_mismatch_reason"] = "Deposit/withdrawal ledger is unavailable; residual is isolated as unknown instead of being treated as verified deposits or withdrawals."
    equity_breakdown["missing_canonical_live_order_log"] = _float(ledger_summary.get("missing_exchange_fill_value"))
    equity_breakdown["synthetic_uuid_effect"] = 0.0
    equity_breakdown["stale_valuation_effect"] = _float(payload.get("stale_valuation_effect"), 0.0)
    equity_breakdown["valuation_snapshot_timing_diff"] = 0.0
    equity_breakdown["dust_balance_effect"] = 0.0
    ledger_detail = accounting.get("ledger_pnl_detail", {}) if isinstance(accounting.get("ledger_pnl_detail"), dict) else {}
    trace_summary = accounting.get("missing_fill_trace_summary", {}) if isinstance(accounting.get("missing_fill_trace_summary"), dict) else {}
    open_position_entry_amount = sum(
        _float(position.get("entry_amount_krw"))
        for position in positions
        if str(position.get("status") or "").upper() in OPEN_POSITION_STATUSES
    )
    equity_breakdown["rounding_or_precision_diff"] = _float(equity_breakdown.get("rounding_diff"))
    equity_breakdown["open_position_cost_basis_diff"] = _float(ledger_detail.get("open_position_cost_basis")) - open_position_entry_amount
    equity_breakdown["estimated_exit_fee_effect"] = _float(ledger_detail.get("estimated_exit_fee"))
    equity_breakdown["unavailable_deposit_withdrawal_effect"] = _float(equity_breakdown.get("remaining_deposit_withdrawal_unknown"))
    equity_breakdown["missing_canonical_log_accounting_effect"] = _float(trace_summary.get("estimated_pnl_impact"))
    exchange_net = (payload.get("exchange_realized_pnl") or {}).get("exchange_net_realized_pnl_after_fee")
    bot_owned_net = pnl_by_ownership.get("exchange_net_realized_pnl_after_fee_bot_owned")
    bot_owned_diff = _float(bot_owned_net) - realized_pnl_from_db if bot_owned_net is not None else None
    equity_breakdown["legacy_position_accounting_effect"] = _float(bot_owned_diff)
    equity_breakdown["remaining_unexplained"] = _float(equity_breakdown.get("unexplained"))
    source = accounting.get("pnl_source_of_truth", {}) if isinstance(accounting.get("pnl_source_of_truth"), dict) else {}
    ledger_strategy_pnl = accounting.get("ledger_strategy_pnl", []) if isinstance(accounting.get("ledger_strategy_pnl"), list) else []
    ledger_symbol_pnl = accounting.get("ledger_symbol_pnl", []) if isinstance(accounting.get("ledger_symbol_pnl"), list) else []
    ledger_session_pnl = accounting.get("ledger_session_pnl", []) if isinstance(accounting.get("ledger_session_pnl"), list) else []
    total_pnl_sanity = _total_pnl_sanity_check(
        initial_equity=initial_equity,
        current_equity=current_equity,
        deposits=deposits,
        withdrawals=withdrawals,
        ledger_detail=ledger_detail,
    )
    allocation = _pnl_allocation_check(
        ledger_detail=ledger_detail,
        strategy_rows=ledger_strategy_pnl,
        symbol_rows=ledger_symbol_pnl,
        session_rows=ledger_session_pnl,
    )
    opening_inventory = payload.get("opening_inventory_report") if isinstance(payload.get("opening_inventory_report"), dict) else _default_opening_inventory_report()
    account_bridge = _account_equity_bridge(
        initial_equity=initial_equity,
        current_equity=current_equity,
        current_cash=current_cash,
        current_coin_value=current_coin_value,
        deposits=deposits,
        withdrawals=withdrawals,
        ledger_detail=ledger_detail,
        opening_inventory=opening_inventory,
        deposit_withdrawal_status=str(payload.get("deposit_withdrawal_status", "UNAVAILABLE")),
    )
    pnl_trust_level = _pnl_trust_level(
        total_pnl_sanity=total_pnl_sanity,
        opening_inventory=opening_inventory,
        deposit_withdrawal_status=str(payload.get("deposit_withdrawal_status", "UNAVAILABLE")),
        allocation=allocation,
    )
    return {
        "initial_equity": initial_equity,
        "current_equity_from_exchange": current_equity,
        "current_cash_krw": current_cash,
        "current_coin_market_value": current_coin_value,
        "realized_pnl_from_db": realized_pnl_from_db,
        "unrealized_pnl_from_positions": unrealized_pnl_from_positions,
        "snapshot_unrealized_pnl_from_positions": snapshot_unrealized_pnl,
        "total_fee": total_fee,
        "gross_realized_pnl_before_fee": reconciliation["gross_realized_pnl_before_fee"],
        "realized_fee": reconciliation["realized_fee"],
        "net_realized_pnl_after_fee": reconciliation["net_realized_pnl_after_fee"],
        "realized_pnl_fee_treatment": reconciliation["realized_pnl_fee_treatment"],
        "unrealized_pnl_before_fee": reconciliation["unrealized_pnl_before_fee"],
        "estimated_exit_fee": reconciliation["estimated_exit_fee"],
        "unrealized_pnl_after_estimated_fee": reconciliation["unrealized_pnl_after_estimated_fee"],
        "total_fee_from_db": reconciliation["total_fee_from_db"],
        "total_fee_from_exchange": reconciliation["total_fee_from_exchange"],
        "fee_diff": reconciliation["fee_diff"],
        "deposits": deposits,
        "withdrawals": withdrawals,
        "expected_equity": reconciliation["expected_equity"],
        "legacy_expected_equity_with_double_fee": reconciliation["legacy_expected_equity_with_double_fee"],
        "expected_equity_formula": reconciliation["expected_equity_formula"],
        "current_equity_formula": reconciliation["current_equity_formula"],
        "current_equity_uses_locked_balances": reconciliation["current_equity_uses_locked_balances"],
        "locked_krw_value": reconciliation["locked_krw_value"],
        "locked_coin_market_value": reconciliation["locked_coin_market_value"],
        "equity_diff": reconciliation["equity_diff"],
        "equity_diff_rate": reconciliation["equity_diff_rate"],
        "equity_diff_breakdown": equity_breakdown,
        "exchange_fill_match": reconciliation["exchange_fill_match"],
        "duplicate_exchange_uuid_in_db": reconciliation["duplicate_exchange_uuid_in_db"],
        "duplicate_client_order_id_in_db": reconciliation["duplicate_client_order_id_in_db"],
        "duplicate_db_accounting": reconciliation["duplicate_db_accounting"],
        "valuation_price_diff_detail": reconciliation["valuation_price_diff_detail"],
        "exchange_fills_ledger_summary": payload.get("exchange_fills_ledger_summary", {}),
        "exchange_fill_ownership_summary": accounting.get("ownership_summary", {}),
        "exchange_fill_accounting_status_summary": accounting.get("accounting_status_summary", {}),
        "exchange_fill_missing_breakdown": accounting.get("missing_fill_breakdown", {}),
        "exchange_fill_classified_sample": accounting.get("classified_fills_sample", []),
        "missing_fill_trace_summary": accounting.get("missing_fill_trace_summary", {}),
        "missing_fill_trace": accounting.get("missing_fill_trace", []),
        "reconciliation_scope": {
            **(accounting.get("reconciliation_scope", {}) if isinstance(accounting.get("reconciliation_scope"), dict) else {}),
            "initial_equity_snapshot_at_utc": payload.get("initial_equity_snapshot_at_utc"),
            "initial_equity_amount": payload.get("initial_equity_amount", initial_equity),
        },
        "pnl_source_of_truth": source or {
            "actual_equity": "exchange_balance_equity",
            "realized_pnl": "exchange_fills_ledger",
            "strategy_pnl": "bot_owned_exchange_fills_ledger",
            "legacy_db_pnl": "legacy_debug_only",
        },
        "legacy_db_pnl_is_debug_only": True,
        "exchange_ledger_pnl_enabled": True,
        "strategy_pnl_source": source.get("strategy_pnl", "bot_owned_exchange_fills_ledger"),
        "symbol_pnl_source": source.get("symbol_pnl", "bot_owned_exchange_fills_ledger"),
        "dashboard_pnl_source": source.get("dashboard_pnl", "bot_owned_exchange_fills_ledger"),
        "ledger_pnl_detail": ledger_detail,
        "ledger_strategy_pnl": ledger_strategy_pnl,
        "ledger_symbol_pnl": ledger_symbol_pnl,
        "ledger_session_pnl": ledger_session_pnl,
        "window_comparison_summary": payload.get("window_comparison_summary", {}),
        "opening_inventory_report": opening_inventory,
        "account_equity_bridge": account_bridge,
        "total_pnl_sanity_check": total_pnl_sanity,
        "pnl_allocation_check": allocation,
        "unrealized_pnl_allocation_check": allocation.get("unrealized_pnl_allocation", {}),
        "pnl_trust_level": pnl_trust_level,
        "legacy_db_pnl": {
            "net_realized_pnl_after_fee": realized_pnl_from_db,
            "unrealized_pnl_from_positions": unrealized_pnl_from_positions,
            "display_role": "legacy_debug_only",
        },
        "exchange_ledger_pnl": {
            "all_fills": exchange_net,
            "bot_owned": bot_owned_net,
            "manual_or_external": pnl_by_ownership.get("exchange_net_realized_pnl_after_fee_manual_or_external"),
            "out_of_scope": pnl_by_ownership.get("exchange_net_realized_pnl_after_fee_out_of_scope"),
        },
        "exchange_net_realized_pnl_after_fee": exchange_net,
        "exchange_gross_realized_pnl_before_fee": (payload.get("exchange_realized_pnl") or {}).get("exchange_gross_realized_pnl_before_fee"),
        "exchange_realized_fee": (payload.get("exchange_realized_pnl") or {}).get("exchange_realized_fee"),
        "exchange_net_realized_pnl_after_fee_all_fills": pnl_by_ownership.get("exchange_net_realized_pnl_after_fee_all_fills", exchange_net),
        "exchange_net_realized_pnl_after_fee_bot_owned": bot_owned_net,
        "exchange_net_realized_pnl_after_fee_manual_or_external": pnl_by_ownership.get("exchange_net_realized_pnl_after_fee_manual_or_external"),
        "exchange_net_realized_pnl_after_fee_out_of_scope": pnl_by_ownership.get("exchange_net_realized_pnl_after_fee_out_of_scope"),
        "realized_pnl_diff": (
            _float(exchange_net) - realized_pnl_from_db
            if payload.get("exchange_realized_pnl")
            else None
        ),
        "bot_owned_realized_pnl_diff": bot_owned_diff,
        "manual_or_external_effect": pnl_by_ownership.get("manual_or_external_effect"),
        "out_of_scope_effect": pnl_by_ownership.get("out_of_scope_effect"),
        "accounting_pending_count": accounting.get("accounting_pending_count", 0),
        "accounting_pending_value": accounting.get("accounting_pending_value", 0.0),
        "accounting_partial_count": accounting.get("accounting_partial_count", 0),
        "accounting_failed_count": accounting.get("accounting_failed_count", 0),
        "accounting_synced_count": accounting.get("accounting_synced_count", 0),
        "accounting_legacy_missing_canonical_log_count": accounting.get("accounting_legacy_missing_canonical_log_count", 0),
        "position_valuation_summary": payload.get("position_valuation_summary", {}),
        "stale_valuation_effect": _float(payload.get("stale_valuation_effect"), 0.0),
        "exchange_ledger_status": payload.get("exchange_ledger_status", "UNAVAILABLE"),
        "exchange_ledger_unavailable_reason": payload.get("exchange_ledger_unavailable_reason"),
        "exchange_ledger_errors": payload.get("exchange_ledger_errors", []),
        "asset_reconciliation_requested": asset_reconciliation_requested,
        "deposit_withdrawal_status": payload.get("deposit_withdrawal_status", "UNAVAILABLE") if asset_reconciliation_requested else "NOT_REQUESTED",
        "deposit_withdrawal_mismatch_is_verified": (payload.get("deposit_withdrawal_status") == "AVAILABLE") if asset_reconciliation_requested else True,
        "deposit_withdrawal_mismatch_note": (
            "Deposit/withdrawal ledger is unavailable; no deposit/withdrawal mismatch amount is verified."
            if asset_reconciliation_requested and payload.get("deposit_withdrawal_status", "UNAVAILABLE") != "AVAILABLE"
            else "Deposit/withdrawal ledger is available."
        ),
        "deposit_withdrawal_unavailable_reason": payload.get("deposit_withdrawal_unavailable_reason", "No read-only deposit/withdrawal broker method is configured."),
        "manual_initial_snapshot_required": asset_reconciliation_requested and payload.get("deposit_withdrawal_status", "UNAVAILABLE") != "AVAILABLE",
        "initial_equity_snapshot_source": "operator_query_parameter_or_policy_default",
        "initial_equity_snapshot_trust_level": "LOW" if asset_reconciliation_requested and payload.get("deposit_withdrawal_status", "UNAVAILABLE") != "AVAILABLE" else "HIGH",
        "gate_failed": bool(reconciliation["gate_failed"]) or not bool(total_pnl_sanity.get("total_pnl_sanity_passed", True)) or pnl_trust_level == "LOW",
    }


def _total_pnl_sanity_check(
    *,
    initial_equity: float,
    current_equity: Any,
    deposits: float,
    withdrawals: float,
    ledger_detail: dict,
) -> dict:
    equity = _float(current_equity) if current_equity is not None else None
    ledger_total = _float(ledger_detail.get("total_pnl_after_estimated_exit_fee"))
    equity_based = None if equity is None else equity - initial_equity - deposits + withdrawals
    diff = None if equity_based is None else ledger_total - equity_based
    denominator = max(abs(initial_equity), 1.0)
    diff_rate = None if diff is None else abs(diff) / denominator
    passed = bool(diff is not None and (abs(diff) <= 100.0 or (diff_rate is not None and diff_rate <= 0.001)))
    return {
        "equity_based_total_pnl": equity_based,
        "ledger_total_pnl": ledger_total,
        "total_pnl_sanity_diff": diff,
        "total_pnl_sanity_diff_rate": diff_rate,
        "total_pnl_sanity_passed": passed,
        "threshold_krw": 100.0,
        "threshold_rate": 0.001,
    }


def _pnl_allocation_check(*, ledger_detail: dict, strategy_rows: list[dict], symbol_rows: list[dict], session_rows: list[dict]) -> dict:
    account_total = _float(ledger_detail.get("total_pnl_after_estimated_exit_fee"))
    account_unrealized = _float(ledger_detail.get("unrealized_pnl_after_estimated_exit_fee"))
    strategy_total = sum(_float(row.get("total_pnl")) for row in strategy_rows)
    symbol_total = sum(_float(row.get("total_pnl")) for row in symbol_rows)
    session_total = sum(_float(row.get("total_pnl")) for row in session_rows)
    strategy_unrealized = sum(_float(row.get("unrealized_pnl")) for row in strategy_rows)
    symbol_unrealized = sum(_float(row.get("unrealized_pnl")) for row in symbol_rows)
    session_unrealized = sum(_float(row.get("unrealized_pnl")) for row in session_rows)
    strategy_diff = strategy_total - account_total
    symbol_diff = symbol_total - account_total
    session_diff = session_total - account_total
    strategy_unrealized_diff = strategy_unrealized - account_unrealized
    symbol_unrealized_diff = symbol_unrealized - account_unrealized
    session_unrealized_diff = session_unrealized - account_unrealized
    causes = []
    if any(abs(value) > 100.0 for value in (strategy_diff, symbol_diff, session_diff)):
        if any(not str(row.get("strategy_name") or row.get("symbol") or row.get("session_id") or "").strip() for row in strategy_rows + symbol_rows + session_rows):
            causes.append("missing_strategy_metadata")
        if any(abs(value) > 100.0 for value in (strategy_unrealized_diff, symbol_unrealized_diff, session_unrealized_diff)):
            causes.append("open_position_strategy_attribution_missing")
        causes.append("unknown_allocation_diff")
    unrealized_duplicate_suspected = any(abs(value) > 100.0 for value in (strategy_unrealized_diff, symbol_unrealized_diff, session_unrealized_diff))
    return {
        "account_total_pnl": account_total,
        "strategy_total_pnl_sum": strategy_total,
        "symbol_total_pnl_sum": symbol_total,
        "session_total_pnl_sum": session_total,
        "strategy_allocation_diff": strategy_diff,
        "symbol_allocation_diff": symbol_diff,
        "session_allocation_diff": session_diff,
        "allocation_method": "fill-attributed FIFO with open-lot valuation by group",
        "allocation_confidence": "HIGH" if not causes else "LOW",
        "allocation_diff_causes": sorted(set(causes)),
        "unrealized_pnl_allocation": {
            "account_unrealized_pnl": account_unrealized,
            "strategy_unrealized_pnl_sum": strategy_unrealized,
            "symbol_unrealized_pnl_sum": symbol_unrealized,
            "session_unrealized_pnl_sum": session_unrealized,
            "strategy_unrealized_pnl_diff": strategy_unrealized_diff,
            "symbol_unrealized_pnl_diff": symbol_unrealized_diff,
            "session_unrealized_pnl_diff": session_unrealized_diff,
            "unrealized_pnl_allocation_diff": max(abs(strategy_unrealized_diff), abs(symbol_unrealized_diff), abs(session_unrealized_diff)),
            "unrealized_pnl_duplicate_suspected": unrealized_duplicate_suspected,
        },
    }


def _default_opening_inventory_report() -> dict:
    return {
        "opening_snapshot_available": False,
        "opening_snapshot_trust_level": "LOW",
        "opening_source": "UNAVAILABLE",
        "opening_cash_krw": None,
        "opening_positions_by_symbol": [],
        "opening_position_value": 0.0,
        "opening_cost_basis": 0.0,
    }


def _account_equity_bridge(
    *,
    initial_equity: float,
    current_equity: Any,
    current_cash: float,
    current_coin_value: float,
    deposits: float,
    withdrawals: float,
    ledger_detail: dict,
    opening_inventory: dict,
    deposit_withdrawal_status: str,
) -> dict:
    realized = _float(ledger_detail.get("net_realized_pnl_after_fee"))
    unrealized = _float(ledger_detail.get("unrealized_pnl_after_estimated_exit_fee"))
    estimated_exit_fee = _float(ledger_detail.get("estimated_exit_fee"))
    total_fee = _float(ledger_detail.get("realized_fee_total"))
    expected = initial_equity + deposits - withdrawals + realized + unrealized
    equity = _float(current_equity) if current_equity is not None else None
    diff = None if equity is None else equity - expected
    diff_rate = None if diff is None else abs(diff) / max(abs(initial_equity), 1.0)
    return {
        "initial_equity": initial_equity,
        "deposits": deposits,
        "withdrawals": withdrawals,
        "deposit_withdrawal_status": deposit_withdrawal_status,
        "opening_cash_krw": opening_inventory.get("opening_cash_krw"),
        "opening_position_value": opening_inventory.get("opening_position_value"),
        "total_buy_value": ledger_detail.get("buy_value", 0.0),
        "total_sell_value": ledger_detail.get("sell_value", 0.0),
        "total_fee": total_fee,
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "estimated_exit_fee": estimated_exit_fee,
        "current_cash_krw": current_cash,
        "current_position_value": current_coin_value,
        "current_exchange_equity": equity,
        "expected_exchange_equity": expected,
        "equity_bridge_diff": diff,
        "equity_bridge_diff_rate": diff_rate,
        "trust_level": "LOW" if deposit_withdrawal_status != "AVAILABLE" or not opening_inventory.get("opening_snapshot_available") else "HIGH",
        "formula": "initial_equity + deposits - withdrawals + ledger_realized_pnl + ledger_unrealized_pnl_after_estimated_exit_fee",
    }


def _pnl_trust_level(*, total_pnl_sanity: dict, opening_inventory: dict, deposit_withdrawal_status: str, allocation: dict) -> str:
    if not total_pnl_sanity.get("total_pnl_sanity_passed"):
        return "LOW"
    if not opening_inventory.get("opening_snapshot_available"):
        return "LOW"
    if deposit_withdrawal_status != "AVAILABLE":
        return "LOW"
    if allocation.get("allocation_confidence") == "LOW":
        return "LOW"
    return "HIGH"


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
    asset_requested = bool((asset_reconciliation or {}).get("asset_reconciliation_requested"))
    if asset_requested and (asset_reconciliation or {}).get("deposit_withdrawal_status") != "AVAILABLE":
        reasons.append({"code": "DEPOSIT_WITHDRAWAL_LEDGER_UNAVAILABLE", "count": 1})
    if asset_requested and not (asset_reconciliation or {}).get("deposit_withdrawal_mismatch_is_verified"):
        reasons.append({"code": "DEPOSIT_WITHDRAWAL_MISMATCH_UNVERIFIED", "count": 1})
    sanity = (asset_reconciliation or {}).get("total_pnl_sanity_check") or {}
    if sanity and not sanity.get("total_pnl_sanity_passed", True):
        reasons.append({"code": "TOTAL_PNL_SANITY_FAILED", "count": 1})
    if (asset_reconciliation or {}).get("pnl_trust_level") == "LOW":
        reasons.append({"code": "PNL_TRUST_LEVEL_LOW", "count": 1})
    opening = (asset_reconciliation or {}).get("opening_inventory_report") or {}
    if opening and not opening.get("opening_snapshot_available", True):
        reasons.append({"code": "OPENING_SNAPSHOT_UNAVAILABLE", "count": 1})
    bridge = (asset_reconciliation or {}).get("account_equity_bridge") or {}
    if abs(_float(bridge.get("equity_bridge_diff_rate"))) > 0.001:
        reasons.append({"code": "EQUITY_BRIDGE_DIFF", "count": 1})
    allocation = (asset_reconciliation or {}).get("pnl_allocation_check") or {}
    unrealized_allocation = (asset_reconciliation or {}).get("unrealized_pnl_allocation_check") or {}
    if unrealized_allocation.get("unrealized_pnl_duplicate_suspected"):
        reasons.append({"code": "UNREALIZED_PNL_DUPLICATE_SUSPECTED", "count": 1})
    if abs(_float(allocation.get("strategy_allocation_diff"))) > 100.0:
        reasons.append({"code": "STRATEGY_PNL_ALLOCATION_DIFF", "count": 1})
    if abs(_float(allocation.get("symbol_allocation_diff"))) > 100.0:
        reasons.append({"code": "SYMBOL_PNL_ALLOCATION_DIFF", "count": 1})
    if abs(_float(allocation.get("session_allocation_diff"))) > 100.0:
        reasons.append({"code": "SESSION_PNL_ALLOCATION_DIFF", "count": 1})
    if abs(_float((asset_reconciliation or {}).get("bot_owned_realized_pnl_diff"))) > 100.0:
        reasons.append({"code": "BOT_OWNED_REALIZED_PNL_DIFF", "count": 1})
    asset_match = (asset_reconciliation or {}).get("exchange_fill_match") or {}
    if (asset_match.get("missing_exchange_fill_in_db") or {}).get("count"):
        reasons.append({"code": "EXCHANGE_FILL_MISSING_IN_DB", "count": asset_match["missing_exchange_fill_in_db"]["count"]})
    if (asset_match.get("db_only_trade") or {}).get("count"):
        reasons.append({"code": "DB_TRADE_MISSING_IN_EXCHANGE", "count": asset_match["db_only_trade"]["count"]})
    if ((asset_reconciliation or {}).get("duplicate_client_order_id_in_db") or {}).get("count"):
        reasons.append({"code": "DUPLICATE_CLIENT_ORDER_ID", "count": asset_reconciliation["duplicate_client_order_id_in_db"]["count"]})
    if abs(_float((asset_reconciliation or {}).get("fee_diff"))) > 100.0:
        reasons.append({"code": "FEE_RECONCILIATION_DIFF", "count": 1})
    ledger_summary = (asset_reconciliation or {}).get("exchange_fills_ledger_summary") or {}
    missing_breakdown = (asset_reconciliation or {}).get("exchange_fill_missing_breakdown") or {}
    if missing_breakdown.get("missing_live_position_accounting_fill_count"):
        reasons.append({"code": "MISSING_LIVE_POSITION_ACCOUNTING_FILL", "count": missing_breakdown.get("missing_live_position_accounting_fill_count")})
    if missing_breakdown.get("missing_strategy_pnl_fill_count"):
        reasons.append({"code": "MISSING_STRATEGY_PNL_FILL", "count": missing_breakdown.get("missing_strategy_pnl_fill_count")})
    if (asset_reconciliation or {}).get("accounting_pending_count"):
        reasons.append({"code": "ACCOUNTING_PENDING_FILL", "count": asset_reconciliation.get("accounting_pending_count")})
    if (asset_reconciliation or {}).get("accounting_partial_count"):
        reasons.append({"code": "ACCOUNTING_PARTIAL_FILL", "count": asset_reconciliation.get("accounting_partial_count")})
    if (asset_reconciliation or {}).get("accounting_failed_count"):
        reasons.append({"code": "ACCOUNTING_FAILED_FILL", "count": asset_reconciliation.get("accounting_failed_count")})
    if (asset_reconciliation or {}).get("accounting_legacy_missing_canonical_log_count"):
        reasons.append({"code": "ACCOUNTING_LEGACY_MISSING_CANONICAL_LOG", "count": asset_reconciliation.get("accounting_legacy_missing_canonical_log_count")})
    if ledger_summary.get("missing_canonical_log_count"):
        reasons.append({"code": "MISSING_CANONICAL_LIVE_ORDER_LOG", "count": ledger_summary.get("missing_canonical_log_count")})
    if ledger_summary.get("duplicate_exchange_uuid_count"):
        reasons.append({"code": "DUPLICATE_REAL_EXCHANGE_UUID", "count": ledger_summary.get("duplicate_exchange_uuid_count")})
    if ledger_summary.get("synthetic_uuid_count"):
        reasons.append({"code": "SYNTHETIC_UUID_PRESENT", "count": ledger_summary.get("synthetic_uuid_count")})
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
        "client_order_id": order.get("client_order_id"),
        "idempotency_key": order.get("idempotency_key"),
        "strategy": order.get("strategy_name"),
        "amount": _order_amount(order),
        "price": order.get("price"),
        "fee": order.get("paid_fee"),
        "executed_volume": order.get("executed_volume"),
        "estimated_pnl_impact_krw": order.get("actual_pnl") if order.get("actual_pnl") is not None else order.get("expected_pnl"),
        "exchange_linked": bool(order.get("order_uuid")),
        "created_at": order.get("created_at"),
        "executed_at_utc": order.get("order_executed_at_utc") or order.get("updated_at"),
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
    normalized = str(value).replace(" ", "T")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    if "+" not in normalized[-6:] and "-" not in normalized[-6:]:
        normalized = f"{normalized}+00:00"
    try:
        return datetime.fromisoformat(normalized).astimezone(timezone.utc)
    except ValueError:
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


def _to_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
