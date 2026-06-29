from __future__ import annotations

import asyncio
import json
import logging
import os
import statistics
import uuid
from datetime import datetime, timezone
from typing import Any

from app.accounting_epoch import build_current_epoch_diagnostics, limited_auto_live_gate
from app.backtest import candles_to_frame
from app.database import (
    get_connection,
    get_live_order_log,
    insert_live_order_log,
    load_current_accounting_epoch,
    load_exchange_fills_ledger,
    load_global_bot_operation_policy,
    load_runtime_lock,
    load_unresolved_live_order_logs_for_exchange,
    update_live_order_log,
)
from app.exchange_fills_ledger import load_or_build_ledger_rows
from app.forward_paper import latest_completed_candle
from app.limited_auto_live import (
    ALLOWED_SYMBOLS,
    BLOCKED_SYMBOLS,
    MAX_NOTIONAL_KRW,
    _persist_limited_ledger,
)
from app.live_broker import LiveTradingConfig, get_live_broker, is_emergency_stopped, masked_exchange_request
from app.live_recovery import normalize_exchange_order, reconcile_order_log
from app.live_smoke_test import (
    EQUITY_TOLERANCE_KRW,
    FEE_TOLERANCE_KRW,
    _current_equity,
    _float,
    _orderbook_quote,
    _round_volume,
)
from app.risk_manager import compute_risk_state
from app.smart_decision import record_shadow_decision
from app.strategies import apply_strategy
from app.upbit import fetch_minute_candles

CONFIRMATION_PHRASE = "RUN CONTROLLED AUTO LIVE ONCE"
TRADE_PROBE_CONFIRMATION_PHRASE = "RUN CONTROLLED TRADE PROBE ONCE"
ENTRY_V3_WATCH_CONFIRMATION_PHRASE = "RUN CONTROLLED ENTRY V3 WATCH ONCE"
ENTRY_V3_POSITION_RUN_CONFIRMATION_PHRASE = "RUN CONTROLLED ENTRY V3 POSITION ONCE"
CONTROLLED_POSITION_LOOP_CONFIRMATION_PHRASE = "RUN CONTROLLED POSITION LOOP ONCE"
CONTROLLED_ENTRY_V2_STRATEGY = "controlled_entry_v2"
CONTROLLED_ENTRY_V3_STRATEGY = "controlled_entry_v3"
CONTROLLED_ALLOWED_STRATEGIES = {"ma_cross", "smart_autonomous", CONTROLLED_ENTRY_V2_STRATEGY, CONTROLLED_ENTRY_V3_STRATEGY}
CONTROLLED_BLOCKED_STRATEGIES = {"rsi"}
TRADE_PROBE_STRATEGY_SOURCE = "controlled_trade_probe"
MAX_ORDERS = 5
TRADE_PROBE_MAX_ORDERS = 3
MAX_OPEN_POSITIONS = 1
DEFAULT_RUNTIME_SECONDS = 600
MAX_RUNTIME_SECONDS = 1800
TICK_INTERVAL_SECONDS = 60
POSITION_RUN_MIN_HOLD_SECONDS = 60
POSITION_RUN_PRICE_CHECK_SECONDS = 30
POSITION_RUN_MIN_NET_PROFIT_MARGIN_RATE = 0.001
POSITION_RUN_STOP_LOSS_RATE = 0.003
POSITION_LOOP_MAX_TRADES = 3
MIN_EXPECTED_EDGE_RATE = 0.006
CONTROLLED_ENTRY_V2_MIN_SCORE = 60.0
CONTROLLED_ENTRY_V2_MIN_EDGE_AFTER_COST = 0.0002
CONTROLLED_ENTRY_V3_MIN_SCORE = 62.0
CONTROLLED_ENTRY_V3_MIN_EDGE_AFTER_COST = 0.0004
OBSERVED_CONTROLLED_ROUNDTRIP_COST_RATE_FLOOR = 0.005
DRY_RUN_CONFIRMATION_PHRASE = "RUN CONTROLLED DRY RUN FORCE BUY"
PROTECTED_FULL_AUTO_CONFIRMATION_PHRASE = "START PROTECTED FULL AUTO LIVE V1"
PROTECTED_FULL_AUTO_MODE = "PROTECTED_FULL_AUTO_LIVE_V1"
PROTECTED_SESSION_LOSS_LIMIT_RATE = 0.005
PROTECTED_SESSION_MAX_DAILY_LOSS_KRW = 1000.0

logger = logging.getLogger("uvicorn.error")

_controlled_jobs: dict[str, dict[str, Any]] = {}
_controlled_job_lock: asyncio.Lock | None = None


def _job_lock() -> asyncio.Lock:
    global _controlled_job_lock
    if _controlled_job_lock is None:
        _controlled_job_lock = asyncio.Lock()
    return _controlled_job_lock


def controlled_auto_live_gate(current_epoch: dict, smoke_preflight: dict, *, exchange: str = "bithumb") -> dict:
    limited_gate = limited_auto_live_gate(current_epoch, smoke_preflight, exchange=exchange)
    limited_run = latest_limited_auto_live_run()
    position_loop = latest_controlled_position_loop_run()
    protected_status = protected_session_gate_status(current_epoch, smoke_preflight, exchange=exchange)
    blockers = list(limited_gate.get("limited_auto_live_blockers") or [])
    if not limited_gate.get("limited_auto_live_allowed"):
        blockers.append({"code": "LIMITED_AUTO_GATE_NOT_READY", "count": 1})
    if not limited_run.get("passed"):
        blockers.append({"code": "LAST_LIMITED_AUTO_RUN_NOT_PASSED", "count": 1})
    if _float(limited_run.get("fee_diff")) != 0.0:
        blockers.append({"code": "LAST_LIMITED_AUTO_FEE_DIFF", "count": 1})
    if _float(limited_run.get("equity_diff_after")) != 0.0:
        blockers.append({"code": "LAST_LIMITED_AUTO_EQUITY_DIFF", "count": 1})
    protected_blockers = list(limited_gate.get("limited_auto_live_blockers") or [])
    final_loop_result = str(position_loop.get("status") or "").upper()
    protected_blockers.extend(_protected_position_loop_blockers(position_loop))
    protected_blockers.extend(protected_status.get("hard_blockers") or [])
    current_equity = _float(
        current_epoch.get("current_epoch_current_equity"),
        _float(current_epoch.get("current_equity_from_exchange")),
    )
    daily_max_loss = _protected_session_loss_limit_krw(current_equity)
    protected_config = {
        "mode": PROTECTED_FULL_AUTO_MODE,
        "allowed_symbols": ["BTC", "ETH"],
        "allowed_strategies": [CONTROLLED_ENTRY_V3_STRATEGY],
        "blocked_strategies": sorted(CONTROLLED_BLOCKED_STRATEGIES),
        "blocked_symbols": sorted(BLOCKED_SYMBOLS),
        "max_notional_per_order_krw": MAX_NOTIONAL_KRW,
        "max_open_positions": MAX_OPEN_POSITIONS,
        "max_daily_trades": 10,
        "max_consecutive_losses": 2,
        "daily_max_loss_krw": daily_max_loss,
        "daily_max_loss_rule": "min(1000 KRW, current_equity * 0.005)",
        "session_loss_limit_krw": daily_max_loss,
        "session_loss_limit_rate": PROTECTED_SESSION_LOSS_LIMIT_RATE,
        "session_loss_basis": "protected session PnL delta after user-confirmed start",
        "cooldown_after_trade_minutes": {"min": 5, "max": 10},
        "averaging_down_allowed": False,
        "reentry_allowed": False,
        "stop_on_accounting_error": True,
        "stop_on_missing_fill": True,
        "stop_on_duplicate_fill": True,
        "stop_on_fee_diff": True,
        "stop_on_equity_diff": True,
        "stop_on_stale_open_order": True,
        "requires_user_confirmation": True,
        "confirmation_phrase": PROTECTED_FULL_AUTO_CONFIRMATION_PHRASE,
    }
    return {
        "controlled_auto_live_allowed": len(blockers) == 0,
        "full_auto_live_allowed": False,
        "controlled_auto_live_blockers": blockers,
        "last_limited_auto_live_run": limited_run,
        "final_controlled_position_loop_result": final_loop_result or "MISSING",
        "last_controlled_position_loop_run": position_loop,
        "protected_full_auto_live_allowed": len(protected_blockers) == 0,
        "protected_full_auto_live_blockers": protected_blockers,
        "protected_full_auto_live_warnings": protected_status.get("warnings", []),
        "protected_session_start_allowed": len(protected_blockers) == 0,
        "protected_session_hard_blockers": protected_status.get("hard_blockers", []),
        "protected_session_warnings": protected_status.get("warnings", []),
        "protected_session_baseline_preview": protected_status.get("baseline"),
        "global_daily_loss_status": protected_status.get("global_daily_loss_status"),
        "protected_session_loss_status": protected_status.get("protected_session_loss_status"),
        "pre_existing_daily_realized_pnl": protected_status.get("pre_existing_daily_realized_pnl"),
        "pre_existing_daily_total_pnl": protected_status.get("pre_existing_daily_total_pnl"),
        "protected_session_loss_limit": protected_status.get("session_loss_limit_krw"),
        "protected_session_loss_limit_rate": protected_status.get("session_loss_limit_rate"),
        "protected_session_loss_limit_remaining": protected_status.get("session_loss_limit_remaining"),
        "protected_full_auto_live_config": protected_config,
        "protected_full_auto_next_action": "USER_CONFIRM_PROTECTED_FULL_AUTO_START" if len(protected_blockers) == 0 else "RESOLVE_PROTECTED_FULL_AUTO_BLOCKERS",
        "base_limited_auto_live_gate": limited_gate,
        "controlled_auto_constraints": {
            "allowed_symbols": sorted(ALLOWED_SYMBOLS),
            "blocked_symbols": sorted(BLOCKED_SYMBOLS),
            "allowed_strategies": sorted(CONTROLLED_ALLOWED_STRATEGIES),
            "blocked_strategies": sorted(CONTROLLED_BLOCKED_STRATEGIES),
            "max_notional_krw": MAX_NOTIONAL_KRW,
            "max_orders": MAX_ORDERS,
            "max_open_positions": MAX_OPEN_POSITIONS,
            "runtime_seconds_min": 600,
            "runtime_seconds_max": MAX_RUNTIME_SECONDS,
            "averaging_down_allowed": False,
            "reentry_allowed": False,
            "min_expected_edge_rate": MIN_EXPECTED_EDGE_RATE,
            "stop_on_accounting_error": True,
            "stop_on_missing_ledger_fill": True,
            "stop_on_duplicate_fill": True,
            "stop_on_fee_diff": True,
            "stop_on_equity_diff": True,
            "stop_on_open_order": True,
            "dry_run_force_buy_available": True,
        },
        "controlled_auto_next_action": "USER_CONFIRM_CONTROLLED_AUTO_LIVE_START" if len(blockers) == 0 else "RESOLVE_CONTROLLED_AUTO_BLOCKERS",
    }


def _protected_session_loss_limit_krw(current_equity: float) -> float:
    return min(PROTECTED_SESSION_MAX_DAILY_LOSS_KRW, current_equity * PROTECTED_SESSION_LOSS_LIMIT_RATE) if current_equity > 0 else PROTECTED_SESSION_MAX_DAILY_LOSS_KRW


def protected_session_gate_status(current_epoch: dict, smoke_preflight: dict | None = None, *, exchange: str = "bithumb") -> dict:
    current_equity = _float(
        current_epoch.get("current_epoch_current_equity"),
        _float(current_epoch.get("current_equity_from_exchange")),
    )
    try:
        risk_state = compute_risk_state(exchange, "KRW-BTC", account_equity_krw=current_equity if current_equity > 0 else None)
    except Exception as exc:
        risk_state = {"status": "UNAVAILABLE", "error": f"{exc.__class__.__name__}:{str(exc)[:160]}"}
    baseline = build_protected_session_baseline(
        current_epoch=current_epoch,
        risk_state=risk_state,
        exchange=exchange,
        protected_session_id=None,
    )
    hard_blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    trust = str(current_epoch.get("current_epoch_trust_level") or "").upper()
    open_summary = (smoke_preflight or {}).get("open_order_audit_summary") or {}
    if not current_epoch.get("current_epoch_sanity_passed"):
        hard_blockers.append({"code": "CURRENT_EPOCH_SANITY_FAILED", "count": 1})
    if trust == "LOW":
        hard_blockers.append({"code": "CURRENT_EPOCH_TRUST_LOW", "count": 1})
    if int(current_epoch.get("current_epoch_accounting_pending_count") or 0) > 0:
        hard_blockers.append({"code": "CURRENT_EPOCH_ACCOUNTING_PENDING", "count": 1})
    if int(current_epoch.get("current_epoch_accounting_failed_count") or 0) > 0:
        hard_blockers.append({"code": "CURRENT_EPOCH_ACCOUNTING_FAILED", "count": 1})
    if int(open_summary.get("exchange_open_order_count") or 0) > 0:
        hard_blockers.append({"code": "EXCHANGE_OPEN_ORDER_EXISTS", "count": int(open_summary.get("exchange_open_order_count") or 1)})
    if int(open_summary.get("current_epoch_open_order_count") or 0) > 0:
        hard_blockers.append({"code": "CURRENT_EPOCH_OPEN_ORDER_EXISTS", "count": int(open_summary.get("current_epoch_open_order_count") or 1)})
    if int(open_summary.get("unknown_open_order_count") or 0) > 0:
        hard_blockers.append({"code": "UNKNOWN_OPEN_ORDER_EXISTS", "count": int(open_summary.get("unknown_open_order_count") or 1)})
    if is_emergency_stopped():
        hard_blockers.append({"code": "EMERGENCY_STOP_ON", "count": 1})
    live_config = LiveTradingConfig.for_exchange(exchange)
    if not live_config.api_key_loaded or not live_config.live_trading_enabled:
        hard_blockers.append({"code": "BROKER_NOT_READY", "count": 1})
    if baseline["global_daily_loss_status"] == "EXCEEDED":
        warnings.append({"code": "GLOBAL_DAILY_LOSS_ALREADY_EXCEEDED", "count": 1})
    if _float(baseline.get("pre_existing_daily_realized_pnl")) < -_float(baseline.get("session_loss_limit_krw")):
        warnings.append({"code": "PRE_EXISTING_DAILY_REALIZED_LOSS_EXCEEDS_PROTECTED_LIMIT", "count": 1})
    return {
        "baseline": baseline,
        "hard_blockers": hard_blockers,
        "warnings": warnings,
        "global_daily_loss_status": baseline["global_daily_loss_status"],
        "protected_session_loss_status": baseline["protected_session_loss_status"],
        "pre_existing_daily_realized_pnl": baseline["pre_existing_daily_realized_pnl"],
        "pre_existing_daily_total_pnl": baseline["pre_existing_daily_total_pnl"],
        "session_loss_limit_krw": baseline["session_loss_limit_krw"],
        "session_loss_limit_rate": baseline["session_loss_limit_rate"],
        "session_loss_limit_remaining": baseline["session_loss_limit_remaining"],
    }


def build_protected_session_baseline(
    *,
    current_epoch: dict,
    risk_state: dict | None = None,
    exchange: str = "bithumb",
    protected_session_id: str | None = None,
    started_at_utc: str | None = None,
) -> dict:
    started_at = started_at_utc or _utc_now()
    current_equity = _float(
        current_epoch.get("current_epoch_current_equity"),
        _float(current_epoch.get("current_equity_from_exchange")),
    )
    if risk_state is None:
        try:
            risk_state = compute_risk_state(exchange, "KRW-BTC", account_equity_krw=current_equity if current_equity > 0 else None)
        except Exception:
            risk_state = {}
    start_realized = _float((risk_state or {}).get("daily_realized_pnl"))
    start_unrealized = _float((risk_state or {}).get("daily_unrealized_pnl"))
    start_total = _float((risk_state or {}).get("daily_total_pnl"), start_realized + start_unrealized)
    session_limit = _protected_session_loss_limit_krw(current_equity)
    global_daily_loss_status = "EXCEEDED" if min(start_realized, start_total) <= -session_limit else "OK"
    return {
        "protected_session_id": protected_session_id,
        "protected_session_started_at_utc": started_at,
        "session_start_equity": current_equity,
        "session_start_realized_pnl": start_realized,
        "session_start_unrealized_pnl": start_unrealized,
        "session_start_total_pnl": start_total,
        "pre_existing_daily_realized_pnl": start_realized,
        "pre_existing_daily_total_pnl": start_total,
        "pre_existing_daily_loss_limit_status": global_daily_loss_status,
        "global_daily_loss_status": global_daily_loss_status,
        "session_loss_limit_krw": session_limit,
        "session_loss_limit_rate": PROTECTED_SESSION_LOSS_LIMIT_RATE,
        "session_max_trades": 10,
        "session_status": "PENDING_USER_CONFIRMATION" if protected_session_id is None else "RUNNING",
        "protected_session_loss_status": "OK",
        "session_pnl_delta": 0.0,
        "session_realized_pnl_delta": 0.0,
        "session_unrealized_pnl_delta": 0.0,
        "session_loss_limit_remaining": session_limit,
        "protected_session_stop_reason": None,
    }


def update_protected_session_loss_status(report: dict, current_epoch: dict | None = None, risk_state: dict | None = None) -> dict:
    baseline = report.get("protected_session_baseline") or (report.get("controlled_auto_live_gate") or {}).get("active_protected_session_baseline")
    if not isinstance(baseline, dict):
        return {}
    if current_epoch is None:
        current_epoch = {}
    if risk_state is None:
        risk_state = {}
    current_total = _float((risk_state or {}).get("daily_total_pnl"), _float(current_epoch.get("current_epoch_total_pnl"), _float(baseline.get("session_start_total_pnl"))))
    current_realized = _float((risk_state or {}).get("daily_realized_pnl"), _float(baseline.get("session_start_realized_pnl")))
    current_unrealized = _float((risk_state or {}).get("daily_unrealized_pnl"), _float(baseline.get("session_start_unrealized_pnl")))
    total_delta = current_total - _float(baseline.get("session_start_total_pnl"))
    realized_delta = current_realized - _float(baseline.get("session_start_realized_pnl"))
    unrealized_delta = current_unrealized - _float(baseline.get("session_start_unrealized_pnl"))
    limit = _float(baseline.get("session_loss_limit_krw"), PROTECTED_SESSION_MAX_DAILY_LOSS_KRW)
    remaining = limit + total_delta if total_delta < 0 else limit
    status = "EXCEEDED" if total_delta <= -limit else "OK"
    stop_reason = "PROTECTED_SESSION_LOSS_LIMIT_REACHED" if status == "EXCEEDED" else None
    updates = {
        "global_daily_loss_status": baseline.get("global_daily_loss_status") or baseline.get("pre_existing_daily_loss_limit_status"),
        "protected_session_loss_status": status,
        "session_pnl_delta": total_delta,
        "session_realized_pnl_delta": realized_delta,
        "session_unrealized_pnl_delta": unrealized_delta,
        "session_loss_limit_remaining": remaining,
        "protected_session_stop_reason": stop_reason,
    }
    baseline.update(updates)
    report["protected_session_baseline"] = baseline
    report.update(updates)
    return updates


def latest_limited_auto_live_run() -> dict:
    with get_connection() as conn:
        rows = [
            _decode_json_fields(dict(row))
            for row in conn.execute(
                """
                SELECT *
                FROM live_order_logs
                WHERE order_purpose = 'LIMITED_AUTO_LIVE'
                ORDER BY id DESC
                LIMIT 10
                """
            ).fetchall()
        ]
    if not rows:
        return {"passed": False, "status": "MISSING"}
    run_ids = []
    for row in rows:
        preview = row.get("order_preview_payload") or {}
        run_id = str(preview.get("limited_auto_live_id") or "")
        if run_id and run_id not in run_ids:
            run_ids.append(run_id)
    run_id = run_ids[0] if run_ids else ""
    run_rows = [row for row in rows if str((row.get("order_preview_payload") or {}).get("limited_auto_live_id") or "") == run_id]
    uuids = [str(row.get("order_uuid") or "") for row in run_rows if row.get("order_uuid")]
    ledger_rows = _ledger_rows_for_order_uuids(uuids)
    fee_from_orders = sum(_float(row.get("paid_fee")) for row in run_rows)
    fee_from_ledger = sum(_float(row.get("fee")) for row in ledger_rows)
    missing = sum(1 for row in ledger_rows if str(row.get("match_status") or "") == "MISSING_CANONICAL_LOG")
    duplicate = sum(1 for row in ledger_rows if str(row.get("match_status") or "") == "DUPLICATE_FILL_KEY")
    passed = (
        len(run_rows) >= 2
        and all(str(row.get("status") or "").upper() == "FILLED" for row in run_rows)
        and len(ledger_rows) == len(uuids)
        and missing == 0
        and duplicate == 0
        and abs(fee_from_orders - fee_from_ledger) <= FEE_TOLERANCE_KRW
    )
    return {
        "passed": passed,
        "status": "PASSED" if passed else "FAILED",
        "limited_run_id": run_id,
        "order_count": len(run_rows),
        "exchange_fill_count": len(ledger_rows),
        "ledger_fill_count": len(ledger_rows),
        "missing_ledger_fill_count": missing,
        "duplicate_fill_count": duplicate,
        "fee_from_orders": fee_from_orders,
        "fee_from_ledger": fee_from_ledger,
        "fee_diff": fee_from_orders - fee_from_ledger,
        "equity_diff_after": 0.0 if passed else None,
    }


def persist_controlled_run_report(report: dict) -> None:
    run_id = str(report.get("loop_run_id") or report.get("position_run_id") or report.get("controlled_run_id") or "").strip()
    if not run_id:
        return
    now = _utc_now()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO controlled_run_reports (
                run_id, run_type, status, technical_result, profitability_result,
                started_at_utc, completed_at_utc, report_json, created_at_utc, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                run_type = excluded.run_type,
                status = excluded.status,
                technical_result = excluded.technical_result,
                profitability_result = excluded.profitability_result,
                started_at_utc = excluded.started_at_utc,
                completed_at_utc = excluded.completed_at_utc,
                report_json = excluded.report_json,
                updated_at_utc = excluded.updated_at_utc
            """,
            (
                run_id,
                str(report.get("run_type") or ""),
                str(report.get("controlled_auto_live_status") or report.get("status") or ""),
                str(report.get("technical_result") or ""),
                str(report.get("profitability_result") or ""),
                str(report.get("started_at_utc") or ""),
                str(report.get("completed_at_utc") or ""),
                json.dumps(report, ensure_ascii=False, default=str),
                now,
                now,
            ),
        )


def latest_controlled_position_loop_run() -> dict:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM controlled_run_reports
            WHERE run_type = 'CONTROLLED_POSITION_LOOP'
            ORDER BY completed_at_utc DESC, id DESC
            LIMIT 20
            """
        ).fetchall()
    if not rows:
        return {"present": False, "status": "MISSING"}

    selected: dict[str, Any] | None = None
    selected_report: dict[str, Any] = {}
    for row in rows:
        item = dict(row)
        report = _safe_json_loads(item.get("report_json"), {})
        status = str(report.get("controlled_auto_live_status") or item.get("status") or "").upper()
        if _is_ignorable_position_loop_stop_request(status, report):
            continue
        selected = item
        selected_report = report
        break
    if selected is None:
        selected = dict(rows[0])
        selected_report = _safe_json_loads(selected.get("report_json"), {})

    item = selected
    report = selected_report
    status = str(report.get("controlled_auto_live_status") or item.get("status") or "").upper()
    return _position_loop_run_summary(item, report, status)


def _is_ignorable_position_loop_stop_request(status: str, report: dict) -> bool:
    if status != "STOPPED":
        return False
    reasons = {str(reason) for reason in (report.get("pass_fail_reasons") or [])}
    if reasons != {"CONTROLLED_ENTRY_V3_POSITION_STOP_REQUESTED"}:
        return False
    return (
        int(report.get("trade_count") or 0) == 0
        and int(report.get("order_count") or 0) == 0
        and int(report.get("exchange_fill_count") or 0) == 0
        and int(report.get("ledger_fill_count") or 0) == 0
        and int(report.get("missing_ledger_fill_count") or 0) == 0
        and int(report.get("duplicate_fill_count") or 0) == 0
        and abs(_float(report.get("fee_diff"))) <= FEE_TOLERANCE_KRW
        and (report.get("equity_diff_after") is None or abs(_float(report.get("equity_diff_after"))) <= EQUITY_TOLERANCE_KRW)
        and int(report.get("current_epoch_accounting_pending_count") or 0) == 0
        and int(report.get("current_epoch_accounting_failed_count") or 0) == 0
        and int(report.get("open_order_count_after") or 0) == 0
    )


def _position_loop_run_summary(item: dict, report: dict, status: str) -> dict:
    return {
        "present": True,
        "run_id": item.get("run_id"),
        "run_type": item.get("run_type"),
        "status": status,
        "persisted_status": item.get("status"),
        "technical_result": item.get("technical_result"),
        "profitability_result": item.get("profitability_result"),
        "started_at_utc": item.get("started_at_utc"),
        "completed_at_utc": item.get("completed_at_utc"),
        "pass_fail_reasons": report.get("pass_fail_reasons") or [],
        "trade_count": report.get("trade_count"),
        "order_count": report.get("order_count"),
        "net_pnl_after_fee": report.get("net_pnl_after_fee"),
        "exchange_fill_count": report.get("exchange_fill_count"),
        "ledger_fill_count": report.get("ledger_fill_count"),
        "missing_ledger_fill_count": report.get("missing_ledger_fill_count"),
        "duplicate_fill_count": report.get("duplicate_fill_count"),
        "fee_diff": report.get("fee_diff"),
        "equity_diff_after": report.get("equity_diff_after"),
        "current_epoch_accounting_pending_count": report.get("current_epoch_accounting_pending_count"),
        "current_epoch_accounting_failed_count": report.get("current_epoch_accounting_failed_count"),
        "open_order_count_after": report.get("open_order_count_after"),
        "final_runtime_status": report.get("final_runtime_status"),
        "report": report,
    }


def _protected_position_loop_blockers(position_loop: dict) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    if not position_loop.get("present"):
        return [{"code": "FINAL_CONTROLLED_POSITION_LOOP_NOT_PASSED", "count": 1}]
    status = str(position_loop.get("status") or "").upper()
    allowed_results = {
        "PASS_IDLE",
        "PASSED_POSITION_TRADE",
        "PASSED_TECHNICAL_POSITION",
        "PASSED_PROFITABLE_POSITION",
    }
    if status not in allowed_results:
        blockers.append({"code": "FINAL_CONTROLLED_POSITION_LOOP_NOT_PASSED", "count": 1})
    metric_checks = [
        ("missing_ledger_fill_count", "CONTROLLED_POSITION_LOOP_MISSING_LEDGER_FILL"),
        ("duplicate_fill_count", "CONTROLLED_POSITION_LOOP_DUPLICATE_FILL"),
        ("current_epoch_accounting_pending_count", "CONTROLLED_POSITION_LOOP_ACCOUNTING_PENDING"),
        ("current_epoch_accounting_failed_count", "CONTROLLED_POSITION_LOOP_ACCOUNTING_FAILED"),
        ("open_order_count_after", "CONTROLLED_POSITION_LOOP_OPEN_ORDER_REMAINS"),
    ]
    for key, code in metric_checks:
        count = int(position_loop.get(key) or 0)
        if count > 0:
            blockers.append({"code": code, "count": count})
    if abs(_float(position_loop.get("fee_diff"))) > FEE_TOLERANCE_KRW:
        blockers.append({"code": "CONTROLLED_POSITION_LOOP_FEE_DIFF", "count": 1})
    equity_diff = position_loop.get("equity_diff_after")
    if equity_diff is not None and abs(_float(equity_diff)) > EQUITY_TOLERANCE_KRW:
        blockers.append({"code": "CONTROLLED_POSITION_LOOP_EQUITY_DIFF", "count": 1})
    if str(position_loop.get("final_runtime_status") or "").upper() != "STOPPED":
        blockers.append({"code": "CONTROLLED_POSITION_LOOP_RUNTIME_NOT_STOPPED", "count": 1})
    return blockers


async def run_controlled_auto_live(
    *,
    exchange: str = "bithumb",
    symbols: list[str] | None = None,
    amount_krw: float = MAX_NOTIONAL_KRW,
    runtime_seconds: int = DEFAULT_RUNTIME_SECONDS,
    confirmation: str,
    controlled_gate: dict | None = None,
    current_epoch: dict | None = None,
    controlled_run_id: str | None = None,
    stop_event: asyncio.Event | None = None,
) -> dict:
    exchange = (exchange or "bithumb").lower()
    symbols = [str(symbol).upper() for symbol in (symbols or ["BTC", "ETH"])]
    symbols = [symbol for symbol in symbols if symbol in ALLOWED_SYMBOLS and symbol not in BLOCKED_SYMBOLS]
    runtime_seconds = min(max(int(runtime_seconds), 600), MAX_RUNTIME_SECONDS)
    notional = min(_float(amount_krw, MAX_NOTIONAL_KRW), MAX_NOTIONAL_KRW)
    started_at = _utc_now()
    run_id = controlled_run_id or f"controlled-{started_at.replace(':', '').replace('-', '').replace('Z', '')}-{uuid.uuid4().hex[:6]}"
    report: dict[str, Any] = {
        "controlled_run_id": run_id,
        "controlled_auto_live_status": "FAILED",
        "started_at_utc": started_at,
        "completed_at_utc": None,
        "runtime_limit_seconds": runtime_seconds,
        "runtime_seconds": 0,
        "symbols": symbols,
        "strategies": sorted(CONTROLLED_ALLOWED_STRATEGIES),
        "used_symbols": [],
        "used_strategies": [],
        "order_count": 0,
        "buy_filled_count": 0,
        "sell_filled_count": 0,
        "exchange_order_uuid_list": [],
        "client_order_id_list": [],
        "gross_pnl": 0.0,
        "net_pnl_after_fee": 0.0,
        "run_realized_pnl": 0.0,
        "run_unrealized_pnl_delta": 0.0,
        "run_mark_to_market_delta": 0.0,
        "total_fee": 0.0,
        "spread_slippage_estimate": 0.0,
        "exchange_fill_count": 0,
        "ledger_fill_count": 0,
        "missing_ledger_fill_count": 0,
        "duplicate_fill_count": 0,
        "fee_diff": 0.0,
        "equity_before": None,
        "equity_after": None,
        "equity_diff_after": None,
        "current_epoch_pnl_before": None,
        "current_epoch_pnl_after": None,
        "current_epoch_pnl_delta": None,
        "account_epoch_pnl_before": None,
        "account_epoch_pnl_after": None,
        "account_epoch_pnl_delta": None,
        "run_pnl": 0.0,
        "pnl_explanation": "",
        "report_notes": [],
        "protected_session_baseline": None,
        "global_daily_loss_status": None,
        "protected_session_loss_status": None,
        "session_pnl_delta": 0.0,
        "session_realized_pnl_delta": 0.0,
        "session_unrealized_pnl_delta": 0.0,
        "session_loss_limit_remaining": None,
        "protected_session_stop_reason": None,
        "current_epoch_accounting_pending_count": None,
        "current_epoch_accounting_failed_count": None,
        "final_runtime_status": None,
        "pass_fail_reasons": [],
        "tick_reports": [],
        "signal_diagnostics": [],
        "signal_summary": {},
        "threshold_analysis": {},
    }
    try:
        if confirmation != CONFIRMATION_PHRASE:
            return _finalize(report, "ABORTED", ["CONTROLLED_AUTO_LIVE_CONFIRMATION_REQUIRED"], controlled_gate=controlled_gate)
        if not symbols:
            return _finalize(report, "ABORTED", ["CONTROLLED_AUTO_SYMBOLS_NOT_ALLOWED"], controlled_gate=controlled_gate)
        if controlled_gate is not None and not controlled_gate.get("controlled_auto_live_allowed"):
            reasons = [str(item.get("code")) for item in (controlled_gate.get("controlled_auto_live_blockers") or [])]
            return _finalize(report, "ABORTED", reasons or ["CONTROLLED_AUTO_GATE_BLOCKED"], controlled_gate=controlled_gate)
        if not _runtime_guards_pass(exchange):
            return _finalize(report, "ABORTED", ["RUNTIME_GUARD_FAILED"], controlled_gate=controlled_gate)
        if notional <= 0 or notional > MAX_NOTIONAL_KRW:
            return _finalize(report, "ABORTED", ["CONTROLLED_AUTO_AMOUNT_EXCEEDS_LIMIT"], controlled_gate=controlled_gate)

        before_equity = await _current_equity(exchange)
        report["equity_before"] = before_equity
        current_epoch = current_epoch or build_current_epoch_diagnostics(exchange=exchange, current_equity=before_equity)
        if not current_epoch.get("current_epoch_sanity_passed"):
            return _finalize(report, "ABORTED", ["CURRENT_EPOCH_SANITY_FAILED"], controlled_gate=controlled_gate)
        report["current_epoch_pnl_before"] = current_epoch.get("current_epoch_total_pnl")
        report["account_epoch_pnl_before"] = current_epoch.get("current_epoch_total_pnl")
        active_protected_baseline = (controlled_gate or {}).get("active_protected_session_baseline")
        if isinstance(active_protected_baseline, dict):
            report["protected_session_baseline"] = dict(active_protected_baseline)
            update_protected_session_loss_status(report, current_epoch)

        broker = get_live_broker(exchange)
        held_position: dict[str, Any] | None = None
        ordered_logs: list[dict] = []
        deadline = asyncio.get_running_loop().time() + runtime_seconds
        while asyncio.get_running_loop().time() < deadline:
            if stop_event is not None and stop_event.is_set():
                return await _finalize_after_orders(report, exchange, ordered_logs, "STOPPED", ["CONTROLLED_AUTO_STOP_REQUESTED"], before_equity, controlled_gate)
            open_blocker = await _open_order_blocker(exchange, symbols)
            if open_blocker:
                return await _finalize_after_orders(report, exchange, ordered_logs, "STOPPED", [open_blocker], before_equity, controlled_gate)
            if int(report["order_count"]) >= MAX_ORDERS:
                break

            if held_position is None:
                decisions = await _build_entry_decisions(exchange, symbols, notional)
                diagnostics = _signal_diagnostics_from_decisions(decisions, current_epoch=current_epoch, controlled_gate=controlled_gate)
                report["signal_diagnostics"].append(
                    {
                        "evaluated_at_utc": _utc_now(),
                        "diagnostics": diagnostics,
                        "summary": _summarize_signal_diagnostics(diagnostics),
                    }
                )
                report["signal_summary"] = _summarize_signal_diagnostics(diagnostics)
                report["threshold_analysis"] = _threshold_adjustment_report(diagnostics)
                decision = _select_best_decision(decisions)
                report["tick_reports"].append(decision)
                if decision.get("signal") == "BUY" and decision.get("edge_allowed"):
                    buy = await _submit_and_wait_controlled(
                        broker=broker,
                        run_id=run_id,
                        exchange=exchange,
                        market=decision["market"],
                        side="BUY",
                        price=_float(decision["entry_price"]),
                        volume=_round_volume(notional / _float(decision["entry_price"])),
                        amount_krw=notional,
                        order_index=int(report["order_count"]) + 1,
                        strategy_name=str(decision["strategy"]),
                        signal_reason=str(decision.get("reason") or "CONTROLLED_ENTRY"),
                    )
                    _merge_order_result(report, buy, prefix="buy")
                    if buy.get("order_log"):
                        ordered_logs.append(buy["order_log"])
                    if not buy.get("filled"):
                        return await _finalize_after_orders(report, exchange, ordered_logs, "STOPPED", ["BUY_NOT_FILLED"], before_equity, controlled_gate)
                    held_position = {
                        "market": decision["market"],
                        "symbol": decision["symbol"],
                        "strategy": decision["strategy"],
                        "quantity": _float(buy.get("executed_volume")),
                        "buy_value": _float(buy.get("filled_amount_krw")),
                        "buy_fee": _float(buy.get("paid_fee")),
                        "buy_price": _float(decision["entry_price"]),
                    }
                else:
                    await asyncio.sleep(min(TICK_INTERVAL_SECONDS, max(deadline - asyncio.get_running_loop().time(), 0)))
                    continue
            else:
                exit_decision = await _exit_decision(exchange, held_position)
                report["tick_reports"].append(exit_decision)
                if exit_decision.get("signal") == "SELL" or deadline - asyncio.get_running_loop().time() <= TICK_INTERVAL_SECONDS:
                    sell_price = _float(exit_decision.get("exit_price"))
                    sell = await _submit_and_wait_controlled(
                        broker=broker,
                        run_id=run_id,
                        exchange=exchange,
                        market=held_position["market"],
                        side="SELL",
                        price=sell_price,
                        volume=_round_volume(_float(held_position.get("quantity"))),
                        amount_krw=sell_price * _round_volume(_float(held_position.get("quantity"))),
                        order_index=int(report["order_count"]) + 1,
                        strategy_name=str(held_position["strategy"]),
                        signal_reason=str(exit_decision.get("reason") or "CONTROLLED_EXIT"),
                    )
                    _merge_order_result(report, sell, prefix="sell")
                    if sell.get("order_log"):
                        ordered_logs.append(sell["order_log"])
                    if not sell.get("filled"):
                        return await _finalize_after_orders(report, exchange, ordered_logs, "STOPPED", ["SELL_NOT_FILLED"], before_equity, controlled_gate)
                    held_position["sell_value"] = _float(sell.get("filled_amount_krw"))
                    held_position["sell_fee"] = _float(sell.get("paid_fee"))
                    held_position["sell_price"] = sell_price
                    _apply_realized_position_pnl(report, held_position)
                    held_position = None
                    break
            await asyncio.sleep(min(TICK_INTERVAL_SECONDS, max(deadline - asyncio.get_running_loop().time(), 0)))
            if stop_event is not None and stop_event.is_set():
                return await _finalize_after_orders(report, exchange, ordered_logs, "STOPPED", ["CONTROLLED_AUTO_STOP_REQUESTED"], before_equity, controlled_gate)
        if held_position is not None and int(report["order_count"]) < MAX_ORDERS:
            quote = await _orderbook_quote(exchange, held_position["market"])
            sell = await _submit_and_wait_controlled(
                broker=broker,
                run_id=run_id,
                exchange=exchange,
                market=held_position["market"],
                side="SELL",
                price=_float(quote.get("best_bid")),
                volume=_round_volume(_float(held_position.get("quantity"))),
                amount_krw=_float(quote.get("best_bid")) * _round_volume(_float(held_position.get("quantity"))),
                order_index=int(report["order_count"]) + 1,
                strategy_name=str(held_position["strategy"]),
                signal_reason="CONTROLLED_RUNTIME_END_FLATTEN",
            )
            _merge_order_result(report, sell, prefix="sell")
            if sell.get("order_log"):
                ordered_logs.append(sell["order_log"])
            if sell.get("filled"):
                held_position["sell_value"] = _float(sell.get("filled_amount_krw"))
                held_position["sell_fee"] = _float(sell.get("paid_fee"))
                held_position["sell_price"] = _float(quote.get("best_bid"))
                _apply_realized_position_pnl(report, held_position)
            else:
                return await _finalize_after_orders(report, exchange, ordered_logs, "STOPPED", ["RUNTIME_END_SELL_NOT_FILLED"], before_equity, controlled_gate)
        return await _finalize_after_orders(report, exchange, ordered_logs, "PASSED", [], before_equity, controlled_gate)
    except Exception as exc:
        return _finalize(report, "FAILED", [f"CONTROLLED_AUTO_EXCEPTION:{exc.__class__.__name__}:{str(exc)[:160]}"], controlled_gate=controlled_gate)


async def start_controlled_auto_live_job(
    *,
    exchange: str = "bithumb",
    symbols: list[str] | None = None,
    amount_krw: float = MAX_NOTIONAL_KRW,
    runtime_seconds: int = DEFAULT_RUNTIME_SECONDS,
    confirmation: str,
    controlled_gate: dict,
    current_epoch: dict,
) -> dict:
    async with _job_lock():
        active = _active_controlled_job_locked()
        if active is not None:
            return {
                "ok": False,
                "status": "ABORTED",
                "message": "A controlled auto live run is already active.",
                "active_controlled_run_id": active["controlled_run_id"],
                "active_status": active["status"],
            }
        started_at = _utc_now()
        run_id = f"controlled-{started_at.replace(':', '').replace('-', '').replace('Z', '')}-{uuid.uuid4().hex[:6]}"
        stop_event = asyncio.Event()
        job = {
            "controlled_run_id": run_id,
            "status": "STARTING",
            "started_at_utc": started_at,
            "completed_at_utc": None,
            "runtime_limit_seconds": min(max(int(runtime_seconds), 600), MAX_RUNTIME_SECONDS),
            "exchange": exchange,
            "symbols": [str(symbol).upper() for symbol in (symbols or ["BTC", "ETH"])],
            "amount_krw": min(_float(amount_krw, MAX_NOTIONAL_KRW), MAX_NOTIONAL_KRW),
            "report": None,
            "error": None,
            "_stop_event": stop_event,
            "run_type": "CONTROLLED_AUTO_LIVE",
        }
        _controlled_jobs[run_id] = job
        task = asyncio.create_task(
            _run_controlled_job(
                run_id=run_id,
                exchange=exchange,
                symbols=symbols or ["BTC", "ETH"],
                amount_krw=amount_krw,
                runtime_seconds=runtime_seconds,
                confirmation=confirmation,
                controlled_gate=controlled_gate,
                current_epoch=current_epoch,
                stop_event=stop_event,
            )
        )
        job["_task"] = task
        return _public_job(job)


def controlled_auto_live_job_status(controlled_run_id: str | None = None) -> dict:
    if controlled_run_id:
        job = _controlled_jobs.get(controlled_run_id)
        if job is None:
            return {"ok": False, "status": "NOT_FOUND", "controlled_run_id": controlled_run_id}
        return {"ok": True, **_public_job(job)}
    jobs = [_public_job(job) for job in sorted(_controlled_jobs.values(), key=lambda item: str(item.get("started_at_utc") or ""), reverse=True)]
    return {"ok": True, "jobs": jobs, "active_job": _public_job(_active_controlled_job_locked()) if _active_controlled_job_locked() else None}


async def stop_controlled_auto_live_job(controlled_run_id: str) -> dict:
    async with _job_lock():
        job = _controlled_jobs.get(controlled_run_id)
        if job is None:
            return {"ok": False, "status": "NOT_FOUND", "controlled_run_id": controlled_run_id}
        if str(job.get("status") or "").upper() not in {"STARTING", "RUNNING"}:
            return {"ok": True, **_public_job(job)}
        stop_event = job.get("_stop_event")
        if stop_event is not None:
            stop_event.set()
        job["stop_requested_at_utc"] = _utc_now()
        return {"ok": True, **_public_job(job)}


async def _run_controlled_job(
    *,
    run_id: str,
    exchange: str,
    symbols: list[str],
    amount_krw: float,
    runtime_seconds: int,
    confirmation: str,
    controlled_gate: dict,
    current_epoch: dict,
    stop_event: asyncio.Event,
) -> None:
    job = _controlled_jobs[run_id]
    job["status"] = "RUNNING"
    try:
        report = await run_controlled_auto_live(
            exchange=exchange,
            symbols=symbols,
            amount_krw=amount_krw,
            runtime_seconds=runtime_seconds,
            confirmation=confirmation,
            controlled_gate=controlled_gate,
            current_epoch=current_epoch,
            controlled_run_id=run_id,
            stop_event=stop_event,
        )
        job["report"] = report
        job["status"] = str(report.get("controlled_auto_live_status") or "FAILED")
        job["completed_at_utc"] = report.get("completed_at_utc") or _utc_now()
    except Exception as exc:
        logger.exception("[controlled-auto-live] job failed run_id=%s", run_id)
        job["status"] = "FAILED"
        job["error"] = f"{exc.__class__.__name__}:{str(exc)[:240]}"
        job["completed_at_utc"] = _utc_now()


async def run_controlled_trade_probe(
    *,
    exchange: str = "bithumb",
    symbol: str = "BTC",
    amount_krw: float = MAX_NOTIONAL_KRW,
    confirmation: str,
    controlled_gate: dict | None = None,
    current_epoch: dict | None = None,
    controlled_run_id: str | None = None,
    stop_event: asyncio.Event | None = None,
) -> dict:
    exchange = (exchange or "bithumb").lower()
    symbol = (symbol or "BTC").upper()
    market = f"KRW-{symbol}"
    notional = min(_float(amount_krw, MAX_NOTIONAL_KRW), MAX_NOTIONAL_KRW)
    started_at = _utc_now()
    run_id = controlled_run_id or f"probe-{started_at.replace(':', '').replace('-', '').replace('Z', '')}-{uuid.uuid4().hex[:6]}"
    report: dict[str, Any] = {
        "controlled_run_id": run_id,
        "run_id": run_id,
        "run_type": "CONTROLLED_TRADE_PROBE",
        "controlled_auto_live_status": "FAILED",
        "started_at_utc": started_at,
        "completed_at_utc": None,
        "runtime_limit_seconds": 300,
        "runtime_seconds": 0,
        "symbols": [symbol],
        "strategies": [TRADE_PROBE_STRATEGY_SOURCE],
        "used_symbols": [],
        "used_strategies": [],
        "order_count": 0,
        "buy_filled_count": 0,
        "sell_filled_count": 0,
        "exchange_order_uuid_list": [],
        "client_order_id_list": [],
        "gross_pnl": 0.0,
        "net_pnl_after_fee": 0.0,
        "run_realized_pnl": 0.0,
        "run_unrealized_pnl_delta": 0.0,
        "run_mark_to_market_delta": 0.0,
        "total_fee": 0.0,
        "spread_slippage_estimate": 0.0,
        "exchange_fill_count": 0,
        "ledger_fill_count": 0,
        "missing_ledger_fill_count": 0,
        "duplicate_fill_count": 0,
        "fee_diff": 0.0,
        "open_order_count_after": None,
        "equity_before": None,
        "equity_after": None,
        "equity_diff_after": None,
        "current_epoch_pnl_before": None,
        "current_epoch_pnl_after": None,
        "current_epoch_pnl_delta": None,
        "account_epoch_pnl_before": None,
        "account_epoch_pnl_after": None,
        "account_epoch_pnl_delta": None,
        "run_pnl": 0.0,
        "pnl_explanation": "",
        "report_notes": [],
        "current_epoch_accounting_pending_count": None,
        "current_epoch_accounting_failed_count": None,
        "final_runtime_status": None,
        "risk_decision": {},
        "pass_fail_reasons": [],
        "tick_reports": [],
    }
    try:
        if confirmation != TRADE_PROBE_CONFIRMATION_PHRASE:
            return _finalize(report, "ABORTED", ["CONTROLLED_TRADE_PROBE_CONFIRMATION_REQUIRED"], controlled_gate=controlled_gate)
        if not _full_auto_live_disabled():
            return _finalize(report, "ABORTED", ["FULL_AUTO_LIVE_MUST_REMAIN_FALSE"], controlled_gate=controlled_gate)
        if symbol not in ALLOWED_SYMBOLS or symbol in BLOCKED_SYMBOLS:
            return _finalize(report, "ABORTED", ["CONTROLLED_TRADE_PROBE_SYMBOL_NOT_ALLOWED"], controlled_gate=controlled_gate)
        if controlled_gate is not None and not controlled_gate.get("controlled_auto_live_allowed"):
            reasons = [str(item.get("code")) for item in (controlled_gate.get("controlled_auto_live_blockers") or [])]
            return _finalize(report, "ABORTED", reasons or ["CONTROLLED_AUTO_GATE_BLOCKED"], controlled_gate=controlled_gate)
        if not _runtime_guards_pass(exchange):
            return _finalize(report, "ABORTED", ["RUNTIME_GUARD_FAILED"], controlled_gate=controlled_gate)
        if notional <= 0 or notional > MAX_NOTIONAL_KRW:
            return _finalize(report, "ABORTED", ["CONTROLLED_TRADE_PROBE_AMOUNT_EXCEEDS_LIMIT"], controlled_gate=controlled_gate)

        before_equity = await _current_equity(exchange)
        report["equity_before"] = before_equity
        current_epoch = current_epoch or build_current_epoch_diagnostics(exchange=exchange, current_equity=before_equity)
        if not current_epoch.get("current_epoch_sanity_passed"):
            return _finalize(report, "ABORTED", ["CURRENT_EPOCH_SANITY_FAILED"], controlled_gate=controlled_gate)
        report["current_epoch_pnl_before"] = current_epoch.get("current_epoch_total_pnl")
        report["account_epoch_pnl_before"] = current_epoch.get("current_epoch_total_pnl")

        open_blocker = await _open_order_blocker(exchange, [symbol])
        if open_blocker:
            return await _finalize_after_orders(report, exchange, [], "ABORTED", [open_blocker], before_equity, controlled_gate)
        if stop_event is not None and stop_event.is_set():
            return await _finalize_after_orders(report, exchange, [], "STOPPED", ["CONTROLLED_TRADE_PROBE_STOP_REQUESTED"], before_equity, controlled_gate)

        quote = await _orderbook_quote(exchange, market)
        buy_price = _float(quote.get("best_ask"))
        bid_price = _float(quote.get("best_bid"))
        volume = _round_volume(notional / buy_price) if buy_price > 0 else 0.0
        fee_rate = LiveTradingConfig.for_exchange(exchange).fee_rate
        estimated_fee = notional * fee_rate
        estimated_spread = max(buy_price - bid_price, 0.0) * volume
        estimated_cost_rate = _round_trip_cost_rate(quote)
        blockers: list[str] = []
        if buy_price <= 0 or bid_price <= 0 or volume <= 0:
            blockers.append("ORDERBOOK_UNAVAILABLE")
        if notional < LiveTradingConfig.for_exchange(exchange).min_order_krw:
            blockers.append("ORDER_BELOW_MINIMUM")
        risk_decision = {
            "strategy_source": TRADE_PROBE_STRATEGY_SOURCE,
            "symbol": symbol,
            "market": market,
            "notional_krw": notional,
            "expected_edge_rate": 0.0,
            "estimated_round_trip_cost_rate": estimated_cost_rate,
            "estimated_fee_krw": estimated_fee,
            "estimated_spread_krw": estimated_spread,
            "reason": "CONTROLLED_TRADE_PROBE_FORCED_BUY_FOR_LEDGER_ACCOUNTING_VALIDATION",
            "risk_allowed": len(blockers) == 0,
            "allowed": len(blockers) == 0,
            "blockers": blockers,
            "preflight_snapshot": {
                "controlled_auto_live_allowed": bool((controlled_gate or {}).get("controlled_auto_live_allowed", True)),
                "full_auto_live_allowed": False,
                "runtime_lock_status": str((load_runtime_lock("auto-trading") or {}).get("status") or "UNKNOWN").upper(),
                "db_auto_trading_enabled": bool(load_global_bot_operation_policy().get("auto_trading_enabled")),
                "current_epoch_sanity_passed": bool(current_epoch.get("current_epoch_sanity_passed")),
                "current_epoch_trust_level": current_epoch.get("current_epoch_trust_level"),
                "current_epoch_accounting_pending_count": int(current_epoch.get("current_epoch_accounting_pending_count") or 0),
                "current_epoch_accounting_failed_count": int(current_epoch.get("current_epoch_accounting_failed_count") or 0),
            },
            "current_epoch_id": current_epoch.get("current_epoch_id"),
        }
        report["risk_decision"] = risk_decision
        report["tick_reports"].append(risk_decision)
        if blockers:
            return await _finalize_after_orders(report, exchange, [], "ABORTED", blockers, before_equity, controlled_gate)

        broker = get_live_broker(exchange)
        ordered_logs: list[dict] = []
        buy = await _submit_and_wait_controlled(
            broker=broker,
            run_id=run_id,
            exchange=exchange,
            market=market,
            side="BUY",
            price=buy_price,
            volume=volume,
            amount_krw=notional,
            order_index=1,
            strategy_name=TRADE_PROBE_STRATEGY_SOURCE,
            signal_reason="CONTROLLED_TRADE_PROBE_BUY",
            order_purpose="CONTROLLED_TRADE_PROBE",
            preview_payload_extra={"run_type": "CONTROLLED_TRADE_PROBE", "risk_decision": risk_decision},
            prepared_risk_result="CONTROLLED_TRADE_PROBE_PREPARED",
            submitted_risk_result="CONTROLLED_TRADE_PROBE_SUBMITTED",
            status_recheck_source="CONTROLLED_TRADE_PROBE_STATUS_RECHECK",
            timeout_seconds=300,
        )
        _merge_order_result(report, buy, prefix="buy")
        if buy.get("order_log"):
            ordered_logs.append(buy["order_log"])
        if not buy.get("filled"):
            return await _finalize_after_orders(report, exchange, ordered_logs, "STOPPED", ["BUY_NOT_FILLED"], before_equity, controlled_gate)

        if stop_event is not None and stop_event.is_set():
            return await _finalize_after_orders(report, exchange, ordered_logs, "STOPPED", ["CONTROLLED_TRADE_PROBE_STOP_REQUESTED_AFTER_BUY"], before_equity, controlled_gate)

        sell_quote = await _orderbook_quote(exchange, market)
        sell_price = _float(sell_quote.get("best_bid") or bid_price)
        sell_volume = _round_volume(_float(buy.get("executed_volume")))
        sell = await _submit_and_wait_controlled(
            broker=broker,
            run_id=run_id,
            exchange=exchange,
            market=market,
            side="SELL",
            price=sell_price,
            volume=sell_volume,
            amount_krw=sell_price * sell_volume,
            order_index=2,
            strategy_name=TRADE_PROBE_STRATEGY_SOURCE,
            signal_reason="CONTROLLED_TRADE_PROBE_FLATTEN",
            order_purpose="CONTROLLED_TRADE_PROBE",
            preview_payload_extra={"run_type": "CONTROLLED_TRADE_PROBE", "risk_decision": risk_decision},
            prepared_risk_result="CONTROLLED_TRADE_PROBE_PREPARED",
            submitted_risk_result="CONTROLLED_TRADE_PROBE_SUBMITTED",
            status_recheck_source="CONTROLLED_TRADE_PROBE_STATUS_RECHECK",
            timeout_seconds=300,
        )
        _merge_order_result(report, sell, prefix="sell")
        if sell.get("order_log"):
            ordered_logs.append(sell["order_log"])
        if not sell.get("filled"):
            return await _finalize_after_orders(report, exchange, ordered_logs, "STOPPED", ["SELL_NOT_FILLED"], before_equity, controlled_gate)
        _apply_realized_position_pnl(
            report,
            {
                "quantity": sell_volume,
                "buy_value": _float(buy.get("filled_amount_krw")),
                "buy_fee": _float(buy.get("paid_fee")),
                "buy_price": buy_price,
                "sell_value": _float(sell.get("filled_amount_krw")),
                "sell_fee": _float(sell.get("paid_fee")),
                "sell_price": sell_price,
            },
        )
        return await _finalize_after_orders(report, exchange, ordered_logs, "PASSED_TRADE_PROBE", [], before_equity, controlled_gate)
    except Exception as exc:
        return _finalize(report, "FAILED", [f"CONTROLLED_TRADE_PROBE_EXCEPTION:{exc.__class__.__name__}:{str(exc)[:160]}"], controlled_gate=controlled_gate)


async def start_controlled_trade_probe_job(
    *,
    exchange: str = "bithumb",
    symbol: str = "BTC",
    amount_krw: float = MAX_NOTIONAL_KRW,
    confirmation: str,
    controlled_gate: dict,
    current_epoch: dict,
) -> dict:
    async with _job_lock():
        active = _active_controlled_job_locked()
        if active is not None:
            return {
                "ok": False,
                "status": "ABORTED",
                "message": "A controlled auto live run is already active.",
                "active_controlled_run_id": active["controlled_run_id"],
                "active_status": active["status"],
            }
        started_at = _utc_now()
        run_id = f"probe-{started_at.replace(':', '').replace('-', '').replace('Z', '')}-{uuid.uuid4().hex[:6]}"
        stop_event = asyncio.Event()
        job = {
            "controlled_run_id": run_id,
            "run_type": "CONTROLLED_TRADE_PROBE",
            "status": "STARTING",
            "started_at_utc": started_at,
            "completed_at_utc": None,
            "runtime_limit_seconds": 300,
            "exchange": exchange,
            "symbols": [str(symbol).upper()],
            "amount_krw": min(_float(amount_krw, MAX_NOTIONAL_KRW), MAX_NOTIONAL_KRW),
            "report": None,
            "error": None,
            "_stop_event": stop_event,
        }
        _controlled_jobs[run_id] = job
        task = asyncio.create_task(
            _run_controlled_trade_probe_job(
                run_id=run_id,
                exchange=exchange,
                symbol=symbol,
                amount_krw=amount_krw,
                confirmation=confirmation,
                controlled_gate=controlled_gate,
                current_epoch=current_epoch,
                stop_event=stop_event,
            )
        )
        job["_task"] = task
        return _public_job(job)


async def _run_controlled_trade_probe_job(
    *,
    run_id: str,
    exchange: str,
    symbol: str,
    amount_krw: float,
    confirmation: str,
    controlled_gate: dict,
    current_epoch: dict,
    stop_event: asyncio.Event,
) -> None:
    job = _controlled_jobs[run_id]
    job["status"] = "RUNNING"
    try:
        report = await run_controlled_trade_probe(
            exchange=exchange,
            symbol=symbol,
            amount_krw=amount_krw,
            confirmation=confirmation,
            controlled_gate=controlled_gate,
            current_epoch=current_epoch,
            controlled_run_id=run_id,
            stop_event=stop_event,
        )
        job["report"] = report
        job["status"] = str(report.get("controlled_auto_live_status") or "FAILED")
        job["completed_at_utc"] = report.get("completed_at_utc") or _utc_now()
    except Exception as exc:
        logger.exception("[controlled-trade-probe] job failed run_id=%s", run_id)
        job["status"] = "FAILED"
        job["error"] = f"{exc.__class__.__name__}:{str(exc)[:240]}"
        job["completed_at_utc"] = _utc_now()


async def start_controlled_entry_v3_watch_job(
    *,
    exchange: str = "bithumb",
    symbols: list[str] | None = None,
    amount_krw: float = MAX_NOTIONAL_KRW,
    runtime_seconds: int = 900,
    scan_interval_seconds: int = 60,
    confirmation: str,
    controlled_gate: dict,
    current_epoch: dict,
) -> dict:
    async with _job_lock():
        active = _active_controlled_job_locked()
        if active is not None:
            return {
                "ok": False,
                "status": "ABORTED",
                "message": "A controlled auto live run is already active.",
                "active_controlled_run_id": active["controlled_run_id"],
                "active_status": active["status"],
            }
        started_at = _utc_now()
        run_id = f"v3watch-{started_at.replace(':', '').replace('-', '').replace('Z', '')}-{uuid.uuid4().hex[:6]}"
        stop_event = asyncio.Event()
        job = {
            "controlled_run_id": run_id,
            "watch_run_id": run_id,
            "run_type": "CONTROLLED_ENTRY_V3_WATCH",
            "status": "STARTING",
            "started_at_utc": started_at,
            "completed_at_utc": None,
            "runtime_limit_seconds": min(max(int(runtime_seconds), 900), MAX_RUNTIME_SECONDS),
            "scan_interval_seconds": max(30, int(scan_interval_seconds)),
            "exchange": exchange,
            "symbols": [str(symbol).upper() for symbol in (symbols or ["BTC", "ETH"])],
            "amount_krw": min(_float(amount_krw, MAX_NOTIONAL_KRW), MAX_NOTIONAL_KRW),
            "report": None,
            "error": None,
            "_stop_event": stop_event,
        }
        _controlled_jobs[run_id] = job
        task = asyncio.create_task(
            _run_controlled_entry_v3_watch_job(
                run_id=run_id,
                exchange=exchange,
                symbols=symbols or ["BTC", "ETH"],
                amount_krw=amount_krw,
                runtime_seconds=runtime_seconds,
                scan_interval_seconds=scan_interval_seconds,
                confirmation=confirmation,
                controlled_gate=controlled_gate,
                current_epoch=current_epoch,
                stop_event=stop_event,
            )
        )
        job["_task"] = task
        return _public_job(job)


async def _run_controlled_entry_v3_watch_job(
    *,
    run_id: str,
    exchange: str,
    symbols: list[str],
    amount_krw: float,
    runtime_seconds: int,
    scan_interval_seconds: int,
    confirmation: str,
    controlled_gate: dict,
    current_epoch: dict,
    stop_event: asyncio.Event,
) -> None:
    job = _controlled_jobs[run_id]
    job["status"] = "RUNNING"
    try:
        report = await run_controlled_entry_v3_watch(
            exchange=exchange,
            symbols=symbols,
            amount_krw=amount_krw,
            runtime_seconds=runtime_seconds,
            scan_interval_seconds=scan_interval_seconds,
            confirmation=confirmation,
            controlled_gate=controlled_gate,
            current_epoch=current_epoch,
            controlled_run_id=run_id,
            stop_event=stop_event,
        )
        job["report"] = report
        job["status"] = str(report.get("controlled_auto_live_status") or "FAILED")
        job["completed_at_utc"] = report.get("completed_at_utc") or _utc_now()
    except Exception as exc:
        logger.exception("[controlled-entry-v3-watch] job failed run_id=%s", run_id)
        job["status"] = "FAILED"
        job["error"] = f"{exc.__class__.__name__}:{str(exc)[:240]}"
        job["completed_at_utc"] = _utc_now()


async def start_controlled_entry_v3_position_run_job(
    *,
    exchange: str = "bithumb",
    symbols: list[str] | None = None,
    amount_krw: float = MAX_NOTIONAL_KRW,
    runtime_seconds: int = 900,
    scan_interval_seconds: int = 60,
    max_holding_minutes: int = 10,
    confirmation: str,
    controlled_gate: dict,
    current_epoch: dict,
) -> dict:
    async with _job_lock():
        active = _active_controlled_job_locked()
        if active is not None:
            return {
                "ok": False,
                "status": "ABORTED",
                "message": "A controlled auto live run is already active.",
                "active_controlled_run_id": active["controlled_run_id"],
                "active_status": active["status"],
            }
        started_at = _utc_now()
        run_id = f"v3pos-{started_at.replace(':', '').replace('-', '').replace('Z', '')}-{uuid.uuid4().hex[:6]}"
        stop_event = asyncio.Event()
        job = {
            "controlled_run_id": run_id,
            "position_run_id": run_id,
            "run_type": "CONTROLLED_ENTRY_V3_POSITION_RUN",
            "status": "STARTING",
            "started_at_utc": started_at,
            "completed_at_utc": None,
            "runtime_limit_seconds": min(max(int(runtime_seconds), 900), MAX_RUNTIME_SECONDS),
            "scan_interval_seconds": max(30, int(scan_interval_seconds)),
            "max_holding_minutes": min(max(int(max_holding_minutes), 10), 30),
            "exchange": exchange,
            "symbols": [str(symbol).upper() for symbol in (symbols or ["BTC", "ETH"])],
            "amount_krw": min(_float(amount_krw, MAX_NOTIONAL_KRW), MAX_NOTIONAL_KRW),
            "report": None,
            "error": None,
            "_stop_event": stop_event,
        }
        _controlled_jobs[run_id] = job
        task = asyncio.create_task(
            _run_controlled_entry_v3_position_run_job(
                run_id=run_id,
                exchange=exchange,
                symbols=symbols or ["BTC", "ETH"],
                amount_krw=amount_krw,
                runtime_seconds=runtime_seconds,
                scan_interval_seconds=scan_interval_seconds,
                max_holding_minutes=max_holding_minutes,
                confirmation=confirmation,
                controlled_gate=controlled_gate,
                current_epoch=current_epoch,
                stop_event=stop_event,
            )
        )
        job["_task"] = task
        return _public_job(job)


async def _run_controlled_entry_v3_position_run_job(
    *,
    run_id: str,
    exchange: str,
    symbols: list[str],
    amount_krw: float,
    runtime_seconds: int,
    scan_interval_seconds: int,
    max_holding_minutes: int,
    confirmation: str,
    controlled_gate: dict,
    current_epoch: dict,
    stop_event: asyncio.Event,
) -> None:
    job = _controlled_jobs[run_id]
    job["status"] = "RUNNING"
    try:
        report = await run_controlled_entry_v3_position_run(
            exchange=exchange,
            symbols=symbols,
            amount_krw=amount_krw,
            runtime_seconds=runtime_seconds,
            scan_interval_seconds=scan_interval_seconds,
            max_holding_minutes=max_holding_minutes,
            confirmation=confirmation,
            controlled_gate=controlled_gate,
            current_epoch=current_epoch,
            controlled_run_id=run_id,
            stop_event=stop_event,
        )
        job["report"] = report
        job["status"] = str(report.get("controlled_auto_live_status") or "FAILED")
        job["completed_at_utc"] = report.get("completed_at_utc") or _utc_now()
    except Exception as exc:
        logger.exception("[controlled-entry-v3-position] job failed run_id=%s", run_id)
        job["status"] = "FAILED"
        job["error"] = f"{exc.__class__.__name__}:{str(exc)[:240]}"
        job["completed_at_utc"] = _utc_now()


async def start_controlled_position_loop_job(
    *,
    exchange: str = "bithumb",
    symbols: list[str] | None = None,
    amount_krw: float = MAX_NOTIONAL_KRW,
    runtime_seconds: int = MAX_RUNTIME_SECONDS,
    scan_interval_seconds: int = 60,
    max_holding_minutes: int = 10,
    max_position_trades: int = POSITION_LOOP_MAX_TRADES,
    confirmation: str,
    controlled_gate: dict,
    current_epoch: dict,
) -> dict:
    async with _job_lock():
        active = _active_controlled_job_locked()
        if active is not None:
            return {
                "ok": False,
                "status": "ABORTED",
                "message": "A controlled auto live run is already active.",
                "active_controlled_run_id": active["controlled_run_id"],
                "active_status": active["status"],
            }
        started_at = _utc_now()
        run_id = f"posloop-{started_at.replace(':', '').replace('-', '').replace('Z', '')}-{uuid.uuid4().hex[:6]}"
        stop_event = asyncio.Event()
        job = {
            "controlled_run_id": run_id,
            "loop_run_id": run_id,
            "run_type": "CONTROLLED_POSITION_LOOP",
            "status": "STARTING",
            "started_at_utc": started_at,
            "completed_at_utc": None,
            "runtime_limit_seconds": min(max(int(runtime_seconds), 900), MAX_RUNTIME_SECONDS),
            "scan_interval_seconds": max(30, int(scan_interval_seconds)),
            "max_holding_minutes": min(max(int(max_holding_minutes), 10), 30),
            "max_position_trades": min(max(int(max_position_trades), 1), POSITION_LOOP_MAX_TRADES),
            "exchange": exchange,
            "symbols": [str(symbol).upper() for symbol in (symbols or ["BTC", "ETH"])],
            "amount_krw": min(_float(amount_krw, MAX_NOTIONAL_KRW), MAX_NOTIONAL_KRW),
            "report": None,
            "error": None,
            "_stop_event": stop_event,
        }
        _controlled_jobs[run_id] = job
        task = asyncio.create_task(
            _run_controlled_position_loop_job(
                run_id=run_id,
                exchange=exchange,
                symbols=symbols or ["BTC", "ETH"],
                amount_krw=amount_krw,
                runtime_seconds=runtime_seconds,
                scan_interval_seconds=scan_interval_seconds,
                max_holding_minutes=max_holding_minutes,
                max_position_trades=max_position_trades,
                confirmation=confirmation,
                controlled_gate=controlled_gate,
                current_epoch=current_epoch,
                stop_event=stop_event,
            )
        )
        job["_task"] = task
        return _public_job(job)


async def _run_controlled_position_loop_job(
    *,
    run_id: str,
    exchange: str,
    symbols: list[str],
    amount_krw: float,
    runtime_seconds: int,
    scan_interval_seconds: int,
    max_holding_minutes: int,
    max_position_trades: int,
    confirmation: str,
    controlled_gate: dict,
    current_epoch: dict,
    stop_event: asyncio.Event,
) -> None:
    job = _controlled_jobs[run_id]
    job["status"] = "RUNNING"
    try:
        report = await run_controlled_position_loop(
            exchange=exchange,
            symbols=symbols,
            amount_krw=amount_krw,
            runtime_seconds=runtime_seconds,
            scan_interval_seconds=scan_interval_seconds,
            max_holding_minutes=max_holding_minutes,
            max_position_trades=max_position_trades,
            confirmation=confirmation,
            controlled_gate=controlled_gate,
            current_epoch=current_epoch,
            loop_run_id=run_id,
            stop_event=stop_event,
        )
        job["report"] = report
        job["status"] = str(report.get("controlled_auto_live_status") or "FAILED")
        job["completed_at_utc"] = report.get("completed_at_utc") or _utc_now()
    except Exception as exc:
        logger.exception("[controlled-position-loop] job failed run_id=%s", run_id)
        job["status"] = "FAILED"
        job["error"] = f"{exc.__class__.__name__}:{str(exc)[:240]}"
        job["completed_at_utc"] = _utc_now()


async def run_controlled_entry_v3_watch(
    *,
    exchange: str = "bithumb",
    symbols: list[str] | None = None,
    amount_krw: float = MAX_NOTIONAL_KRW,
    runtime_seconds: int = 900,
    scan_interval_seconds: int = 60,
    confirmation: str,
    controlled_gate: dict | None = None,
    current_epoch: dict | None = None,
    controlled_run_id: str | None = None,
    stop_event: asyncio.Event | None = None,
) -> dict:
    exchange = (exchange or "bithumb").lower()
    symbols = [str(symbol).upper() for symbol in (symbols or ["BTC", "ETH"])]
    symbols = [symbol for symbol in symbols if symbol in {"BTC", "ETH"}]
    runtime_seconds = min(max(int(runtime_seconds), 900), MAX_RUNTIME_SECONDS)
    scan_interval_seconds = max(30, int(scan_interval_seconds))
    notional = min(_float(amount_krw, MAX_NOTIONAL_KRW), MAX_NOTIONAL_KRW)
    started_at = _utc_now()
    run_id = controlled_run_id or f"v3watch-{started_at.replace(':', '').replace('-', '').replace('Z', '')}-{uuid.uuid4().hex[:6]}"
    report: dict[str, Any] = {
        "controlled_run_id": run_id,
        "watch_run_id": run_id,
        "run_type": "CONTROLLED_ENTRY_V3_WATCH",
        "controlled_auto_live_status": "FAILED",
        "started_at_utc": started_at,
        "completed_at_utc": None,
        "runtime_limit_seconds": runtime_seconds,
        "runtime_seconds": 0,
        "watch_duration_seconds": 0,
        "scan_interval_seconds": scan_interval_seconds,
        "scan_count": 0,
        "trade_candidate_detected": False,
        "selected_candidate": None,
        "selected_symbol": None,
        "selected_timeframe": None,
        "selected_edge_after_cost": None,
        "selected_signal_score": None,
        "selected_trigger_reason": None,
        "symbols": symbols,
        "strategies": [CONTROLLED_ENTRY_V3_STRATEGY],
        "used_symbols": [],
        "used_strategies": [],
        "order_count": 0,
        "buy_filled_count": 0,
        "sell_filled_count": 0,
        "exchange_order_uuid_list": [],
        "client_order_id_list": [],
        "gross_pnl": 0.0,
        "net_pnl_after_fee": 0.0,
        "run_realized_pnl": 0.0,
        "run_unrealized_pnl_delta": 0.0,
        "run_mark_to_market_delta": 0.0,
        "total_fee": 0.0,
        "spread_slippage_estimate": 0.0,
        "exchange_fill_count": 0,
        "ledger_fill_count": 0,
        "missing_ledger_fill_count": 0,
        "duplicate_fill_count": 0,
        "fee_diff": 0.0,
        "open_order_count_after": None,
        "equity_before": None,
        "equity_after": None,
        "equity_diff_after": None,
        "current_epoch_pnl_before": None,
        "current_epoch_pnl_after": None,
        "current_epoch_pnl_delta": None,
        "account_epoch_pnl_before": None,
        "account_epoch_pnl_after": None,
        "account_epoch_pnl_delta": None,
        "run_pnl": 0.0,
        "pnl_explanation": "",
        "report_notes": [],
        "current_epoch_accounting_pending_count": None,
        "current_epoch_accounting_failed_count": None,
        "final_runtime_status": None,
        "watch_scans": [],
        "tick_reports": [],
        "pass_fail_reasons": [],
    }
    try:
        if confirmation != ENTRY_V3_WATCH_CONFIRMATION_PHRASE:
            return _finalize(report, "ABORTED", ["CONTROLLED_ENTRY_V3_WATCH_CONFIRMATION_REQUIRED"], controlled_gate=controlled_gate)
        if not _full_auto_live_disabled():
            return _finalize(report, "ABORTED", ["FULL_AUTO_LIVE_MUST_REMAIN_FALSE"], controlled_gate=controlled_gate)
        if not symbols:
            return _finalize(report, "ABORTED", ["CONTROLLED_ENTRY_V3_WATCH_SYMBOLS_NOT_ALLOWED"], controlled_gate=controlled_gate)
        if controlled_gate is not None and not controlled_gate.get("controlled_auto_live_allowed"):
            reasons = [str(item.get("code")) for item in (controlled_gate.get("controlled_auto_live_blockers") or [])]
            return _finalize(report, "ABORTED", reasons or ["CONTROLLED_AUTO_GATE_BLOCKED"], controlled_gate=controlled_gate)
        if not _runtime_guards_pass(exchange):
            return _finalize(report, "ABORTED", ["RUNTIME_GUARD_FAILED"], controlled_gate=controlled_gate)
        if notional <= 0 or notional > MAX_NOTIONAL_KRW:
            return _finalize(report, "ABORTED", ["CONTROLLED_ENTRY_V3_WATCH_AMOUNT_EXCEEDS_LIMIT"], controlled_gate=controlled_gate)

        before_equity = await _current_equity(exchange)
        report["equity_before"] = before_equity
        current_epoch = current_epoch or build_current_epoch_diagnostics(exchange=exchange, current_equity=before_equity)
        if not current_epoch.get("current_epoch_sanity_passed"):
            return _finalize(report, "ABORTED", ["CURRENT_EPOCH_SANITY_FAILED"], controlled_gate=controlled_gate)
        report["current_epoch_pnl_before"] = current_epoch.get("current_epoch_total_pnl")
        report["account_epoch_pnl_before"] = current_epoch.get("current_epoch_total_pnl")

        broker = get_live_broker(exchange)
        ordered_logs: list[dict] = []
        deadline = asyncio.get_running_loop().time() + runtime_seconds
        while asyncio.get_running_loop().time() < deadline:
            if stop_event is not None and stop_event.is_set():
                return await _finalize_after_orders(report, exchange, ordered_logs, "STOPPED", ["CONTROLLED_ENTRY_V3_WATCH_STOP_REQUESTED"], before_equity, controlled_gate)
            open_blocker = await _open_order_blocker(exchange, symbols)
            if open_blocker:
                return await _finalize_after_orders(report, exchange, ordered_logs, "STOPPED", [open_blocker], before_equity, controlled_gate)
            scan = await _scan_controlled_entry_v3_watch(exchange=exchange, symbols=symbols, notional=notional, current_epoch=current_epoch, controlled_gate=controlled_gate)
            report["scan_count"] = int(report.get("scan_count") or 0) + 1
            report["watch_scans"].append(scan)
            report["tick_reports"].append(scan)
            selected = scan.get("selected_candidate")
            if selected:
                report["trade_candidate_detected"] = True
                report["selected_candidate"] = selected
                report["selected_symbol"] = selected.get("symbol")
                report["selected_timeframe"] = selected.get("timeframe")
                report["selected_edge_after_cost"] = selected.get("expected_edge_after_cost")
                report["selected_signal_score"] = selected.get("signal_score")
                report["selected_trigger_reason"] = selected.get("trade_candidate_reason")
                market = str(selected.get("market") or f"KRW-{selected.get('symbol')}")
                buy_price = _float(selected.get("entry_price"))
                if buy_price <= 0:
                    quote = await _orderbook_quote(exchange, market)
                    buy_price = _float(quote.get("best_ask"))
                volume = _round_volume(notional / buy_price) if buy_price > 0 else 0.0
                if volume <= 0:
                    return await _finalize_after_orders(report, exchange, ordered_logs, "FAILED", ["CONTROLLED_ENTRY_V3_WATCH_VOLUME_INVALID"], before_equity, controlled_gate)
                buy = await _submit_and_wait_controlled(
                    broker=broker,
                    run_id=run_id,
                    exchange=exchange,
                    market=market,
                    side="BUY",
                    price=buy_price,
                    volume=volume,
                    amount_krw=notional,
                    order_index=1,
                    strategy_name=CONTROLLED_ENTRY_V3_STRATEGY,
                    signal_reason=str(selected.get("trade_candidate_reason") or "CONTROLLED_ENTRY_V3_WATCH_BUY"),
                    order_purpose="CONTROLLED_ENTRY_V3_WATCH",
                    preview_payload_extra={"run_type": "CONTROLLED_ENTRY_V3_WATCH", "selected_candidate": selected},
                    prepared_risk_result="CONTROLLED_ENTRY_V3_WATCH_PREPARED",
                    submitted_risk_result="CONTROLLED_ENTRY_V3_WATCH_SUBMITTED",
                    status_recheck_source="CONTROLLED_ENTRY_V3_WATCH_STATUS_RECHECK",
                    timeout_seconds=300,
                )
                _merge_order_result(report, buy, prefix="buy")
                if buy.get("order_log"):
                    ordered_logs.append(buy["order_log"])
                if not buy.get("filled"):
                    return await _finalize_after_orders(report, exchange, ordered_logs, "STOPPED", ["BUY_NOT_FILLED"], before_equity, controlled_gate)
                if stop_event is not None and stop_event.is_set():
                    return await _finalize_after_orders(report, exchange, ordered_logs, "STOPPED", ["CONTROLLED_ENTRY_V3_WATCH_STOP_REQUESTED_AFTER_BUY"], before_equity, controlled_gate)
                sell_quote = await _orderbook_quote(exchange, market)
                sell_price = _float(sell_quote.get("best_bid"))
                sell_volume = _round_volume(_float(buy.get("executed_volume")))
                sell = await _submit_and_wait_controlled(
                    broker=broker,
                    run_id=run_id,
                    exchange=exchange,
                    market=market,
                    side="SELL",
                    price=sell_price,
                    volume=sell_volume,
                    amount_krw=sell_price * sell_volume,
                    order_index=2,
                    strategy_name=CONTROLLED_ENTRY_V3_STRATEGY,
                    signal_reason="CONTROLLED_ENTRY_V3_WATCH_FLATTEN",
                    order_purpose="CONTROLLED_ENTRY_V3_WATCH",
                    preview_payload_extra={"run_type": "CONTROLLED_ENTRY_V3_WATCH", "selected_candidate": selected},
                    prepared_risk_result="CONTROLLED_ENTRY_V3_WATCH_PREPARED",
                    submitted_risk_result="CONTROLLED_ENTRY_V3_WATCH_SUBMITTED",
                    status_recheck_source="CONTROLLED_ENTRY_V3_WATCH_STATUS_RECHECK",
                    timeout_seconds=300,
                )
                _merge_order_result(report, sell, prefix="sell")
                if sell.get("order_log"):
                    ordered_logs.append(sell["order_log"])
                if not sell.get("filled"):
                    return await _finalize_after_orders(report, exchange, ordered_logs, "STOPPED", ["SELL_NOT_FILLED"], before_equity, controlled_gate)
                _apply_realized_position_pnl(
                    report,
                    {
                        "quantity": sell_volume,
                        "buy_value": _float(buy.get("filled_amount_krw")),
                        "buy_fee": _float(buy.get("paid_fee")),
                        "buy_price": buy_price,
                        "sell_value": _float(sell.get("filled_amount_krw")),
                        "sell_fee": _float(sell.get("paid_fee")),
                        "sell_price": sell_price,
                    },
                )
                return await _finalize_after_orders(report, exchange, ordered_logs, "PASSED", [], before_equity, controlled_gate)
            await asyncio.sleep(min(scan_interval_seconds, max(deadline - asyncio.get_running_loop().time(), 0)))
        return await _finalize_after_orders(report, exchange, ordered_logs, "PASSED", [], before_equity, controlled_gate)
    except Exception as exc:
        return _finalize(report, "FAILED", [f"CONTROLLED_ENTRY_V3_WATCH_EXCEPTION:{exc.__class__.__name__}:{str(exc)[:160]}"], controlled_gate=controlled_gate)


async def _scan_controlled_entry_v3_watch(
    *,
    exchange: str,
    symbols: list[str],
    notional: float,
    current_epoch: dict | None = None,
    controlled_gate: dict | None = None,
) -> dict:
    decisions: list[dict] = []
    for symbol in symbols:
        market = f"KRW-{symbol}"
        quote = await _orderbook_quote(exchange, market)
        for timeframe in (5, 15):
            candles = await _load_strategy_candles(exchange, market, timeframe)
            decisions.append(_controlled_entry_v3_decision(symbol, market, timeframe, candles, quote, notional))
    diagnostics = _signal_diagnostics_from_decisions(decisions, current_epoch=current_epoch, controlled_gate=controlled_gate)
    selected = _select_controlled_entry_v3_watch_candidate(decisions)
    selected_diag = None
    if selected:
        for item in diagnostics:
            if item.get("symbol") == selected.get("symbol") and item.get("timeframe") == selected.get("timeframe"):
                selected_diag = item
                break
    return {
        "evaluated_at_utc": _utc_now(),
        "diagnostics": diagnostics,
        "summary": _summarize_signal_diagnostics(diagnostics),
        "selected_candidate": selected_diag,
        "priority": ["ETH:15m", "BTC:15m", "ETH:5m", "BTC:5m"],
    }


async def run_controlled_entry_v3_position_run(
    *,
    exchange: str = "bithumb",
    symbols: list[str] | None = None,
    amount_krw: float = MAX_NOTIONAL_KRW,
    runtime_seconds: int = 900,
    scan_interval_seconds: int = 60,
    max_holding_minutes: int = 10,
    confirmation: str,
    controlled_gate: dict | None = None,
    current_epoch: dict | None = None,
    controlled_run_id: str | None = None,
    stop_event: asyncio.Event | None = None,
) -> dict:
    exchange = (exchange or "bithumb").lower()
    symbols = [str(symbol).upper() for symbol in (symbols or ["BTC", "ETH"])]
    symbols = [symbol for symbol in symbols if symbol in {"BTC", "ETH"}]
    runtime_seconds = min(max(int(runtime_seconds), 900), MAX_RUNTIME_SECONDS)
    scan_interval_seconds = max(30, int(scan_interval_seconds))
    max_holding_minutes = min(max(int(max_holding_minutes), 10), 30)
    max_holding_seconds = max_holding_minutes * 60
    notional = min(_float(amount_krw, MAX_NOTIONAL_KRW), MAX_NOTIONAL_KRW)
    started_at = _utc_now()
    run_id = controlled_run_id or f"v3pos-{started_at.replace(':', '').replace('-', '').replace('Z', '')}-{uuid.uuid4().hex[:6]}"
    report: dict[str, Any] = {
        "controlled_run_id": run_id,
        "position_run_id": run_id,
        "run_type": "CONTROLLED_ENTRY_V3_POSITION_RUN",
        "controlled_auto_live_status": "FAILED",
        "started_at_utc": started_at,
        "completed_at_utc": None,
        "runtime_limit_seconds": runtime_seconds,
        "runtime_seconds": 0,
        "scan_interval_seconds": scan_interval_seconds,
        "scan_count": 0,
        "max_holding_minutes": max_holding_minutes,
        "min_holding_seconds": POSITION_RUN_MIN_HOLD_SECONDS,
        "trade_candidate_detected": False,
        "selected_candidate": None,
        "selected_symbol": None,
        "selected_timeframe": None,
        "selected_edge_after_cost": None,
        "selected_signal_score": None,
        "selected_trigger_reason": None,
        "symbols": symbols,
        "strategies": [CONTROLLED_ENTRY_V3_STRATEGY],
        "used_symbols": [],
        "used_strategies": [],
        "order_count": 0,
        "max_orders": 2,
        "buy_filled_count": 0,
        "sell_filled_count": 0,
        "exchange_order_uuid_list": [],
        "client_order_id_list": [],
        "entry_price": None,
        "entry_fee": 0.0,
        "exit_price": None,
        "exit_fee": 0.0,
        "holding_minutes": 0.0,
        "exit_reason": None,
        "take_profit_rate": None,
        "stop_loss_rate": POSITION_RUN_STOP_LOSS_RATE,
        "estimated_total_cost_rate": None,
        "realized_edge_after_cost": None,
        "max_unrealized_pnl": 0.0,
        "min_unrealized_pnl": 0.0,
        "gross_pnl": 0.0,
        "net_pnl_after_fee": 0.0,
        "run_realized_pnl": 0.0,
        "run_unrealized_pnl_delta": 0.0,
        "run_mark_to_market_delta": 0.0,
        "total_fee": 0.0,
        "spread_slippage_estimate": 0.0,
        "exchange_fill_count": 0,
        "ledger_fill_count": 0,
        "missing_ledger_fill_count": 0,
        "duplicate_fill_count": 0,
        "fee_diff": 0.0,
        "open_order_count_after": None,
        "equity_before": None,
        "equity_after": None,
        "equity_diff_after": None,
        "current_epoch_pnl_before": None,
        "current_epoch_pnl_after": None,
        "current_epoch_pnl_delta": None,
        "account_epoch_pnl_before": None,
        "account_epoch_pnl_after": None,
        "account_epoch_pnl_delta": None,
        "run_pnl": 0.0,
        "pnl_explanation": "",
        "report_notes": [],
        "current_epoch_accounting_pending_count": None,
        "current_epoch_accounting_failed_count": None,
        "final_runtime_status": None,
        "watch_scans": [],
        "tick_reports": [],
        "position_ticks": [],
        "pass_fail_reasons": [],
    }
    try:
        if confirmation != ENTRY_V3_POSITION_RUN_CONFIRMATION_PHRASE:
            return _finalize(report, "ABORTED", ["CONTROLLED_ENTRY_V3_POSITION_CONFIRMATION_REQUIRED"], controlled_gate=controlled_gate)
        if not _full_auto_live_disabled():
            return _finalize(report, "ABORTED", ["FULL_AUTO_LIVE_MUST_REMAIN_FALSE"], controlled_gate=controlled_gate)
        if not symbols:
            return _finalize(report, "ABORTED", ["CONTROLLED_ENTRY_V3_POSITION_SYMBOLS_NOT_ALLOWED"], controlled_gate=controlled_gate)
        if controlled_gate is not None and not controlled_gate.get("controlled_auto_live_allowed"):
            reasons = [str(item.get("code")) for item in (controlled_gate.get("controlled_auto_live_blockers") or [])]
            return _finalize(report, "ABORTED", reasons or ["CONTROLLED_AUTO_GATE_BLOCKED"], controlled_gate=controlled_gate)
        if not _runtime_guards_pass(exchange):
            return _finalize(report, "ABORTED", ["RUNTIME_GUARD_FAILED"], controlled_gate=controlled_gate)
        if notional <= 0 or notional > MAX_NOTIONAL_KRW:
            return _finalize(report, "ABORTED", ["CONTROLLED_ENTRY_V3_POSITION_AMOUNT_EXCEEDS_LIMIT"], controlled_gate=controlled_gate)

        before_equity = await _current_equity(exchange)
        report["equity_before"] = before_equity
        current_epoch = current_epoch or build_current_epoch_diagnostics(exchange=exchange, current_equity=before_equity)
        if not current_epoch.get("current_epoch_sanity_passed"):
            return _finalize(report, "ABORTED", ["CURRENT_EPOCH_SANITY_FAILED"], controlled_gate=controlled_gate)
        report["current_epoch_pnl_before"] = current_epoch.get("current_epoch_total_pnl")
        report["account_epoch_pnl_before"] = current_epoch.get("current_epoch_total_pnl")

        broker = get_live_broker(exchange)
        ordered_logs: list[dict] = []
        deadline = asyncio.get_running_loop().time() + runtime_seconds
        while asyncio.get_running_loop().time() < deadline:
            if stop_event is not None and stop_event.is_set():
                return await _finalize_after_orders(report, exchange, ordered_logs, "STOPPED", ["CONTROLLED_ENTRY_V3_POSITION_STOP_REQUESTED"], before_equity, controlled_gate)
            if report.get("protected_session_baseline"):
                latest_equity = await _current_equity(exchange)
                latest_epoch = build_current_epoch_diagnostics(exchange=exchange, current_equity=latest_equity)
                protected_updates = update_protected_session_loss_status(report, latest_epoch)
                if protected_updates.get("protected_session_loss_status") == "EXCEEDED":
                    return await _finalize_after_orders(report, exchange, ordered_logs, "STOPPED", ["PROTECTED_SESSION_LOSS_LIMIT_REACHED"], before_equity, controlled_gate)
            open_blocker = await _open_order_blocker(exchange, symbols)
            if open_blocker:
                return await _finalize_after_orders(report, exchange, ordered_logs, "STOPPED", [open_blocker], before_equity, controlled_gate)
            scan = await _scan_controlled_entry_v3_watch(exchange=exchange, symbols=symbols, notional=notional, current_epoch=current_epoch, controlled_gate=controlled_gate)
            report["scan_count"] = int(report.get("scan_count") or 0) + 1
            report["watch_scans"].append(scan)
            report["tick_reports"].append(scan)
            selected = scan.get("selected_candidate")
            if not selected:
                await asyncio.sleep(min(scan_interval_seconds, max(deadline - asyncio.get_running_loop().time(), 0)))
                continue

            report["trade_candidate_detected"] = True
            report["selected_candidate"] = selected
            report["selected_symbol"] = selected.get("symbol")
            report["selected_timeframe"] = selected.get("timeframe")
            report["selected_edge_after_cost"] = selected.get("expected_edge_after_cost")
            report["selected_signal_score"] = selected.get("signal_score")
            report["selected_trigger_reason"] = selected.get("trade_candidate_reason")
            report["estimated_total_cost_rate"] = _float(selected.get("estimated_total_cost_rate"))
            report["take_profit_rate"] = report["estimated_total_cost_rate"] + POSITION_RUN_MIN_NET_PROFIT_MARGIN_RATE
            market = str(selected.get("market") or f"KRW-{selected.get('symbol')}")
            buy_price = _float(selected.get("entry_price"))
            if buy_price <= 0:
                quote = await _orderbook_quote(exchange, market)
                buy_price = _float(quote.get("best_ask"))
            volume = _round_volume(notional / buy_price) if buy_price > 0 else 0.0
            if volume <= 0:
                return await _finalize_after_orders(report, exchange, ordered_logs, "FAILED", ["CONTROLLED_ENTRY_V3_POSITION_VOLUME_INVALID"], before_equity, controlled_gate)

            buy = await _submit_and_wait_controlled(
                broker=broker,
                run_id=run_id,
                exchange=exchange,
                market=market,
                side="BUY",
                price=buy_price,
                volume=volume,
                amount_krw=notional,
                order_index=1,
                strategy_name=CONTROLLED_ENTRY_V3_STRATEGY,
                signal_reason=str(selected.get("trade_candidate_reason") or "CONTROLLED_ENTRY_V3_POSITION_BUY"),
                order_purpose="CONTROLLED_ENTRY_V3_POSITION_RUN",
                preview_payload_extra={
                    "run_type": "CONTROLLED_ENTRY_V3_POSITION_RUN",
                    "selected_candidate": selected,
                    "take_profit_rate": report["take_profit_rate"],
                    "stop_loss_rate": report["stop_loss_rate"],
                    "max_holding_minutes": max_holding_minutes,
                },
                prepared_risk_result="CONTROLLED_ENTRY_V3_POSITION_PREPARED",
                submitted_risk_result="CONTROLLED_ENTRY_V3_POSITION_SUBMITTED",
                status_recheck_source="CONTROLLED_ENTRY_V3_POSITION_STATUS_RECHECK",
                timeout_seconds=300,
            )
            _merge_order_result(report, buy, prefix="buy")
            if buy.get("order_log"):
                ordered_logs.append(buy["order_log"])
            if not buy.get("filled"):
                return await _finalize_after_orders(report, exchange, ordered_logs, "STOPPED", ["BUY_NOT_FILLED"], before_equity, controlled_gate)

            executed_volume = _round_volume(_float(buy.get("executed_volume")))
            entry_value = _float(buy.get("filled_amount_krw"))
            entry_fee = _float(buy.get("paid_fee"))
            entry_price = entry_value / executed_volume if executed_volume > 0 and entry_value > 0 else buy_price
            report["entry_price"] = entry_price
            report["entry_fee"] = entry_fee
            report["max_unrealized_pnl"] = -entry_fee
            report["min_unrealized_pnl"] = -entry_fee

            buy_time = asyncio.get_running_loop().time()
            hold_deadline = buy_time + max_holding_seconds
            exit_reason = "TIME_STOP"
            while asyncio.get_running_loop().time() < hold_deadline:
                elapsed = asyncio.get_running_loop().time() - buy_time
                if stop_event is not None and stop_event.is_set():
                    exit_reason = "MANUAL_STOP"
                    break
                quote = await _orderbook_quote(exchange, market)
                mark_price = _float(quote.get("best_bid"))
                if mark_price <= 0:
                    await asyncio.sleep(POSITION_RUN_PRICE_CHECK_SECONDS)
                    continue
                mark_value = mark_price * executed_volume
                estimated_exit_fee = mark_value * LiveTradingConfig.for_exchange(exchange).fee_rate
                unrealized_net = mark_value - entry_value - entry_fee - estimated_exit_fee
                report["max_unrealized_pnl"] = max(_float(report.get("max_unrealized_pnl")), unrealized_net)
                report["min_unrealized_pnl"] = min(_float(report.get("min_unrealized_pnl")), unrealized_net)
                report["position_ticks"].append(
                    {
                        "checked_at_utc": _utc_now(),
                        "holding_seconds": int(elapsed),
                        "mark_price": mark_price,
                        "unrealized_pnl_after_estimated_fee": unrealized_net,
                    }
                )
                if report.get("protected_session_baseline"):
                    latest_equity = await _current_equity(exchange)
                    latest_epoch = build_current_epoch_diagnostics(exchange=exchange, current_equity=latest_equity)
                    protected_updates = update_protected_session_loss_status(report, latest_epoch)
                    if protected_updates.get("protected_session_loss_status") == "EXCEEDED":
                        exit_reason = "ACCOUNTING_STOP"
                        break
                move_rate = (mark_price - entry_price) / entry_price if entry_price > 0 else 0.0
                if elapsed >= POSITION_RUN_MIN_HOLD_SECONDS and move_rate >= _float(report.get("take_profit_rate")):
                    exit_reason = "TAKE_PROFIT"
                    break
                if elapsed >= POSITION_RUN_MIN_HOLD_SECONDS and move_rate <= -_float(report.get("stop_loss_rate")):
                    exit_reason = "STOP_LOSS"
                    break
                await asyncio.sleep(min(POSITION_RUN_PRICE_CHECK_SECONDS, max(hold_deadline - asyncio.get_running_loop().time(), 0)))

            sell_quote = await _orderbook_quote(exchange, market)
            sell_price = _float(sell_quote.get("best_bid"))
            if sell_price <= 0:
                return await _finalize_after_orders(report, exchange, ordered_logs, "FAILED", ["CONTROLLED_ENTRY_V3_POSITION_EXIT_PRICE_INVALID"], before_equity, controlled_gate)
            sell = await _submit_and_wait_controlled(
                broker=broker,
                run_id=run_id,
                exchange=exchange,
                market=market,
                side="SELL",
                price=sell_price,
                volume=executed_volume,
                amount_krw=sell_price * executed_volume,
                order_index=2,
                strategy_name=CONTROLLED_ENTRY_V3_STRATEGY,
                signal_reason=f"CONTROLLED_ENTRY_V3_POSITION_{exit_reason}",
                order_purpose="CONTROLLED_ENTRY_V3_POSITION_RUN",
                preview_payload_extra={
                    "run_type": "CONTROLLED_ENTRY_V3_POSITION_RUN",
                    "selected_candidate": selected,
                    "exit_reason": exit_reason,
                },
                prepared_risk_result="CONTROLLED_ENTRY_V3_POSITION_PREPARED",
                submitted_risk_result="CONTROLLED_ENTRY_V3_POSITION_SUBMITTED",
                status_recheck_source="CONTROLLED_ENTRY_V3_POSITION_STATUS_RECHECK",
                timeout_seconds=300,
            )
            _merge_order_result(report, sell, prefix="sell")
            if sell.get("order_log"):
                ordered_logs.append(sell["order_log"])
            if not sell.get("filled"):
                return await _finalize_after_orders(report, exchange, ordered_logs, "STOPPED", ["SELL_NOT_FILLED"], before_equity, controlled_gate)
            exit_value = _float(sell.get("filled_amount_krw"))
            exit_fee = _float(sell.get("paid_fee"))
            exit_price = exit_value / executed_volume if executed_volume > 0 and exit_value > 0 else sell_price
            report["exit_price"] = exit_price
            report["exit_fee"] = exit_fee
            report["exit_reason"] = exit_reason
            report["holding_minutes"] = round((asyncio.get_running_loop().time() - buy_time) / 60.0, 4)
            _apply_realized_position_pnl(
                report,
                {
                    "quantity": executed_volume,
                    "buy_value": entry_value,
                    "buy_fee": entry_fee,
                    "buy_price": entry_price,
                    "sell_value": exit_value,
                    "sell_fee": exit_fee,
                    "sell_price": exit_price,
                },
            )
            if entry_value > 0:
                report["realized_edge_after_cost"] = _float(report.get("net_pnl_after_fee")) / entry_value
            return await _finalize_after_orders(report, exchange, ordered_logs, "PASSED", [], before_equity, controlled_gate)

        return await _finalize_after_orders(report, exchange, ordered_logs, "PASSED", [], before_equity, controlled_gate)
    except Exception as exc:
        return _finalize(report, "FAILED", [f"CONTROLLED_ENTRY_V3_POSITION_EXCEPTION:{exc.__class__.__name__}:{str(exc)[:160]}"], controlled_gate=controlled_gate)


async def run_controlled_position_loop(
    *,
    exchange: str = "bithumb",
    symbols: list[str] | None = None,
    amount_krw: float = MAX_NOTIONAL_KRW,
    runtime_seconds: int = MAX_RUNTIME_SECONDS,
    scan_interval_seconds: int = 60,
    max_holding_minutes: int = 10,
    max_position_trades: int = POSITION_LOOP_MAX_TRADES,
    confirmation: str,
    controlled_gate: dict | None = None,
    current_epoch: dict | None = None,
    loop_run_id: str | None = None,
    stop_event: asyncio.Event | None = None,
) -> dict:
    exchange = (exchange or "bithumb").lower()
    symbols = [str(symbol).upper() for symbol in (symbols or ["BTC", "ETH"])]
    symbols = [symbol for symbol in symbols if symbol in {"BTC", "ETH"}]
    runtime_seconds = min(max(int(runtime_seconds), 900), MAX_RUNTIME_SECONDS)
    scan_interval_seconds = max(30, int(scan_interval_seconds))
    max_holding_minutes = min(max(int(max_holding_minutes), 10), 30)
    max_position_trades = min(max(int(max_position_trades), 1), POSITION_LOOP_MAX_TRADES)
    notional = min(_float(amount_krw, MAX_NOTIONAL_KRW), MAX_NOTIONAL_KRW)
    started_at = _utc_now()
    run_id = loop_run_id or f"posloop-{started_at.replace(':', '').replace('-', '').replace('Z', '')}-{uuid.uuid4().hex[:6]}"
    report: dict[str, Any] = {
        "controlled_run_id": run_id,
        "loop_run_id": run_id,
        "run_type": "CONTROLLED_POSITION_LOOP",
        "controlled_auto_live_status": "FAILED",
        "technical_result": "NOT_EVALUATED",
        "profitability_result": "NOT_EVALUATED",
        "started_at_utc": started_at,
        "completed_at_utc": None,
        "runtime_limit_seconds": runtime_seconds,
        "runtime_seconds": 0,
        "scan_interval_seconds": scan_interval_seconds,
        "max_holding_minutes": max_holding_minutes,
        "max_position_trades": max_position_trades,
        "symbols": symbols,
        "strategies": [CONTROLLED_ENTRY_V3_STRATEGY],
        "selected_symbols": [],
        "selected_timeframes": [],
        "selected_symbol": None,
        "selected_timeframe": None,
        "trade_count": 0,
        "position_trade_count": 0,
        "order_count": 0,
        "buy_filled_count": 0,
        "sell_filled_count": 0,
        "gross_pnl": 0.0,
        "net_pnl_after_fee": 0.0,
        "run_realized_pnl": 0.0,
        "run_unrealized_pnl_delta": 0.0,
        "run_mark_to_market_delta": 0.0,
        "run_pnl": 0.0,
        "total_fee": 0.0,
        "exit_reason_counts": {},
        "time_stop_count": 0,
        "profitable_trade_count": 0,
        "losing_trade_count": 0,
        "exchange_fill_count": 0,
        "ledger_fill_count": 0,
        "missing_ledger_fill_count": 0,
        "duplicate_fill_count": 0,
        "fee_diff": 0.0,
        "equity_before": None,
        "equity_after": None,
        "equity_diff_after": 0.0,
        "account_epoch_pnl_before": None,
        "account_epoch_pnl_after": None,
        "account_epoch_pnl_delta": None,
        "current_epoch_pnl_before": None,
        "current_epoch_pnl_after": None,
        "current_epoch_pnl_delta": None,
        "current_epoch_accounting_pending_count": None,
        "current_epoch_accounting_failed_count": None,
        "open_order_count_after": None,
        "final_runtime_status": None,
        "protected_session_baseline": None,
        "global_daily_loss_status": None,
        "protected_session_loss_status": None,
        "session_pnl_delta": 0.0,
        "session_realized_pnl_delta": 0.0,
        "session_unrealized_pnl_delta": 0.0,
        "session_loss_limit_remaining": None,
        "protected_session_stop_reason": None,
        "sub_runs": [],
        "pass_fail_reasons": [],
    }
    try:
        if confirmation != CONTROLLED_POSITION_LOOP_CONFIRMATION_PHRASE:
            return _finalize_position_loop(report, "ABORTED", ["CONTROLLED_POSITION_LOOP_CONFIRMATION_REQUIRED"], controlled_gate)
        if not _full_auto_live_disabled():
            return _finalize_position_loop(report, "ABORTED", ["FULL_AUTO_LIVE_MUST_REMAIN_FALSE"], controlled_gate)
        if not symbols:
            return _finalize_position_loop(report, "ABORTED", ["CONTROLLED_POSITION_LOOP_SYMBOLS_NOT_ALLOWED"], controlled_gate)
        if controlled_gate is not None and not controlled_gate.get("controlled_auto_live_allowed"):
            reasons = [str(item.get("code")) for item in (controlled_gate.get("controlled_auto_live_blockers") or [])]
            return _finalize_position_loop(report, "ABORTED", reasons or ["CONTROLLED_AUTO_GATE_BLOCKED"], controlled_gate)
        if not _runtime_guards_pass(exchange):
            return _finalize_position_loop(report, "ABORTED", ["RUNTIME_GUARD_FAILED"], controlled_gate)
        if notional <= 0 or notional > MAX_NOTIONAL_KRW:
            return _finalize_position_loop(report, "ABORTED", ["CONTROLLED_POSITION_LOOP_AMOUNT_EXCEEDS_LIMIT"], controlled_gate)

        before_equity = await _current_equity(exchange)
        report["equity_before"] = before_equity
        current_epoch = current_epoch or build_current_epoch_diagnostics(exchange=exchange, current_equity=before_equity)
        if not current_epoch.get("current_epoch_sanity_passed"):
            return _finalize_position_loop(report, "ABORTED", ["CURRENT_EPOCH_SANITY_FAILED"], controlled_gate)
        report["account_epoch_pnl_before"] = current_epoch.get("current_epoch_total_pnl")
        report["current_epoch_pnl_before"] = current_epoch.get("current_epoch_total_pnl")
        protected_baseline = None
        if controlled_gate and controlled_gate.get("protected_full_auto_live_config"):
            protected_baseline = build_protected_session_baseline(
                current_epoch=current_epoch,
                exchange=exchange,
                protected_session_id=run_id,
                started_at_utc=started_at,
            )
            report["protected_session_baseline"] = protected_baseline
            update_protected_session_loss_status(report, current_epoch)

        deadline = asyncio.get_running_loop().time() + runtime_seconds
        traded_symbols: set[str] = set()
        while asyncio.get_running_loop().time() < deadline and int(report["trade_count"]) < max_position_trades:
            if stop_event is not None and stop_event.is_set():
                break
            if protected_baseline:
                latest_equity = await _current_equity(exchange)
                latest_epoch = build_current_epoch_diagnostics(exchange=exchange, current_equity=latest_equity)
                protected_updates = update_protected_session_loss_status(report, latest_epoch)
                if protected_updates.get("protected_session_loss_status") == "EXCEEDED":
                    return _finalize_position_loop(report, "STOPPED", ["PROTECTED_SESSION_LOSS_LIMIT_REACHED"], controlled_gate)
            remaining_symbols = [symbol for symbol in symbols if symbol not in traded_symbols]
            if not remaining_symbols:
                break
            remaining_seconds = int(max(deadline - asyncio.get_running_loop().time(), 0))
            if remaining_seconds <= 0:
                break
            sub_runtime = min(MAX_RUNTIME_SECONDS, max(900, remaining_seconds))
            sub_run_id = f"{run_id}-trade-{int(report['trade_count']) + 1}"
            sub_gate = controlled_gate
            if protected_baseline and controlled_gate is not None:
                sub_gate = {**controlled_gate, "active_protected_session_baseline": protected_baseline}
            sub = await run_controlled_entry_v3_position_run(
                exchange=exchange,
                symbols=remaining_symbols,
                amount_krw=notional,
                runtime_seconds=sub_runtime,
                scan_interval_seconds=scan_interval_seconds,
                max_holding_minutes=max_holding_minutes,
                confirmation=ENTRY_V3_POSITION_RUN_CONFIRMATION_PHRASE,
                controlled_gate=sub_gate,
                current_epoch=current_epoch,
                controlled_run_id=sub_run_id,
                stop_event=stop_event,
            )
            report["sub_runs"].append(sub)
            _merge_position_loop_subrun(report, sub)
            if protected_baseline:
                latest_equity = await _current_equity(exchange)
                latest_epoch = build_current_epoch_diagnostics(exchange=exchange, current_equity=latest_equity)
                protected_updates = update_protected_session_loss_status(report, latest_epoch)
                if protected_updates.get("protected_session_loss_status") == "EXCEEDED":
                    return _finalize_position_loop(report, "STOPPED", ["PROTECTED_SESSION_LOSS_LIMIT_REACHED"], controlled_gate)
            status = str(sub.get("controlled_auto_live_status") or "").upper()
            if status == "PASSED_POSITION_TRADE":
                symbol = str(sub.get("selected_symbol") or "").upper()
                if symbol:
                    traded_symbols.add(symbol)
                current_epoch = build_current_epoch_diagnostics(exchange=exchange, current_equity=sub.get("equity_after"))
                continue
            if status == "PASS_IDLE":
                break
            return _finalize_position_loop(report, "STOPPED", list(sub.get("pass_fail_reasons") or [status]), controlled_gate)

        after_equity = await _current_equity(exchange)
        report["equity_after"] = after_equity
        after_epoch = build_current_epoch_diagnostics(exchange=exchange, current_equity=after_equity)
        report["account_epoch_pnl_after"] = after_epoch.get("current_epoch_total_pnl")
        report["current_epoch_pnl_after"] = after_epoch.get("current_epoch_total_pnl")
        epoch_delta = _float(report.get("account_epoch_pnl_after")) - _float(report.get("account_epoch_pnl_before"))
        report["account_epoch_pnl_delta"] = epoch_delta
        report["current_epoch_pnl_delta"] = epoch_delta
        report["current_epoch_accounting_pending_count"] = int(after_epoch.get("current_epoch_accounting_pending_count") or 0)
        report["current_epoch_accounting_failed_count"] = int(after_epoch.get("current_epoch_accounting_failed_count") or 0)
        report["open_order_count_after"] = await _open_order_count(exchange, symbols or ["BTC", "ETH"])
        update_protected_session_loss_status(report, after_epoch)
        reasons = _position_loop_fail_reasons(report)
        if reasons:
            return _finalize_position_loop(report, "STOPPED", reasons, controlled_gate)
        if int(report.get("trade_count") or 0) <= 0:
            return _finalize_position_loop(report, "PASS_IDLE", [], controlled_gate)
        report["technical_result"] = "PASSED"
        if _float(report.get("net_pnl_after_fee")) > 0:
            report["profitability_result"] = "PROFITABLE"
            return _finalize_position_loop(report, "PASSED_PROFITABLE_POSITION", [], controlled_gate)
        report["profitability_result"] = "NOT_PROFITABLE"
        return _finalize_position_loop(report, "PASSED_TECHNICAL_POSITION", [], controlled_gate)
    except Exception as exc:
        return _finalize_position_loop(report, "FAILED", [f"CONTROLLED_POSITION_LOOP_EXCEPTION:{exc.__class__.__name__}:{str(exc)[:160]}"], controlled_gate)


def _merge_position_loop_subrun(report: dict, sub: dict) -> None:
    if str(sub.get("controlled_auto_live_status") or "").upper() != "PASSED_POSITION_TRADE":
        return
    report["trade_count"] = int(report.get("trade_count") or 0) + 1
    report["position_trade_count"] = int(report.get("position_trade_count") or 0) + 1
    report["order_count"] = int(report.get("order_count") or 0) + int(sub.get("order_count") or 0)
    report["buy_filled_count"] = int(report.get("buy_filled_count") or 0) + int(sub.get("buy_filled_count") or 0)
    report["sell_filled_count"] = int(report.get("sell_filled_count") or 0) + int(sub.get("sell_filled_count") or 0)
    report["gross_pnl"] = _float(report.get("gross_pnl")) + _float(sub.get("gross_pnl"))
    report["net_pnl_after_fee"] = _float(report.get("net_pnl_after_fee")) + _float(sub.get("net_pnl_after_fee"))
    report["run_realized_pnl"] = _float(report.get("run_realized_pnl")) + _float(sub.get("run_realized_pnl"))
    report["run_pnl"] = report["run_realized_pnl"]
    report["total_fee"] = _float(report.get("total_fee")) + _float(sub.get("total_fee"))
    report["exchange_fill_count"] = int(report.get("exchange_fill_count") or 0) + int(sub.get("exchange_fill_count") or 0)
    report["ledger_fill_count"] = int(report.get("ledger_fill_count") or 0) + int(sub.get("ledger_fill_count") or 0)
    report["missing_ledger_fill_count"] = int(report.get("missing_ledger_fill_count") or 0) + int(sub.get("missing_ledger_fill_count") or 0)
    report["duplicate_fill_count"] = int(report.get("duplicate_fill_count") or 0) + int(sub.get("duplicate_fill_count") or 0)
    report["fee_diff"] = _float(report.get("fee_diff")) + _float(sub.get("fee_diff"))
    symbol = str(sub.get("selected_symbol") or "").upper()
    timeframe = str(sub.get("selected_timeframe") or "")
    if symbol and symbol not in report["selected_symbols"]:
        report["selected_symbols"].append(symbol)
    if timeframe and timeframe not in report["selected_timeframes"]:
        report["selected_timeframes"].append(timeframe)
    report["selected_symbol"] = symbol or report.get("selected_symbol")
    report["selected_timeframe"] = timeframe or report.get("selected_timeframe")
    reason = str(sub.get("exit_reason") or "UNKNOWN")
    counts = dict(report.get("exit_reason_counts") or {})
    counts[reason] = int(counts.get(reason) or 0) + 1
    report["exit_reason_counts"] = counts
    report["time_stop_count"] = int(report.get("time_stop_count") or 0) + (1 if reason == "TIME_STOP" else 0)
    if _float(sub.get("net_pnl_after_fee")) > 0:
        report["profitable_trade_count"] = int(report.get("profitable_trade_count") or 0) + 1
    else:
        report["losing_trade_count"] = int(report.get("losing_trade_count") or 0) + 1


def _position_loop_fail_reasons(report: dict) -> list[str]:
    reasons = []
    if int(report.get("exchange_fill_count") or 0) != int(report.get("ledger_fill_count") or 0):
        reasons.append("EXCHANGE_LEDGER_FILL_COUNT_MISMATCH")
    if int(report.get("missing_ledger_fill_count") or 0) != 0:
        reasons.append("MISSING_LEDGER_FILL")
    if int(report.get("duplicate_fill_count") or 0) != 0:
        reasons.append("DUPLICATE_FILL")
    if abs(_float(report.get("fee_diff"))) > FEE_TOLERANCE_KRW:
        reasons.append("FEE_DIFF_EXCEEDS_TOLERANCE")
    if report.get("equity_diff_after") is not None and abs(_float(report.get("equity_diff_after"))) > EQUITY_TOLERANCE_KRW:
        reasons.append("EQUITY_DIFF_EXCEEDS_TOLERANCE")
    if int(report.get("current_epoch_accounting_pending_count") or 0) != 0:
        reasons.append("CURRENT_EPOCH_ACCOUNTING_PENDING")
    if int(report.get("current_epoch_accounting_failed_count") or 0) != 0:
        reasons.append("CURRENT_EPOCH_ACCOUNTING_FAILED")
    if int(report.get("open_order_count_after") or 0) != 0:
        reasons.append("OPEN_ORDER_REMAINS_AFTER_RUN")
    if str(report.get("protected_session_loss_status") or "").upper() == "EXCEEDED":
        reasons.append(str(report.get("protected_session_stop_reason") or "PROTECTED_SESSION_LOSS_LIMIT_REACHED"))
    runtime = load_runtime_lock("auto-trading")
    report["final_runtime_status"] = str((runtime or {}).get("status") or "UNKNOWN").upper()
    if report["final_runtime_status"] != "STOPPED":
        reasons.append("RUNTIME_NOT_STOPPED")
    return reasons


def _finalize_position_loop(report: dict, status: str, reasons: list[str], controlled_gate: dict | None = None) -> dict:
    if status in {"PASSED_PROFITABLE_POSITION", "PASSED_TECHNICAL_POSITION"}:
        report["technical_result"] = "PASSED"
    elif status == "PASS_IDLE":
        report["technical_result"] = "PASS_IDLE"
        report["profitability_result"] = "NO_TRADE"
    else:
        report["technical_result"] = "FAILED" if status in {"FAILED", "STOPPED"} else status
        if report.get("profitability_result") == "NOT_EVALUATED":
            report["profitability_result"] = "NOT_EVALUATED"
    report["controlled_auto_live_status"] = status
    report["completed_at_utc"] = _utc_now()
    report["runtime_seconds"] = max(0, int((_parse_utc(report["completed_at_utc"]) - _parse_utc(str(report["started_at_utc"]))).total_seconds()))
    report["pass_fail_reasons"] = reasons
    if controlled_gate is not None:
        report["controlled_auto_live_gate"] = controlled_gate
    runtime = load_runtime_lock("auto-trading")
    report["final_runtime_status"] = str((runtime or {}).get("status") or "UNKNOWN").upper()
    persist_controlled_run_report(report)
    return {key: value for key, value in report.items() if not key.startswith("_")}


def _select_controlled_entry_v3_watch_candidate(decisions: list[dict]) -> dict | None:
    priority = {("ETH", "15m"): 0, ("BTC", "15m"): 1, ("ETH", "5m"): 2, ("BTC", "5m"): 3}
    candidates = [
        decision for decision in decisions
        if decision.get("edge_allowed")
        and str(decision.get("signal") or "").upper() == "BUY"
        and str(decision.get("signal_state") or "") == "TRADE_CANDIDATE"
    ]
    candidates.sort(
        key=lambda item: (
            priority.get((str(item.get("symbol") or ""), str(item.get("timeframe") or "")), 99),
            -_float(item.get("expected_edge_after_cost")),
            -_float(item.get("signal_score")),
        )
    )
    return candidates[0] if candidates else None


def _active_controlled_job_locked() -> dict | None:
    for job in _controlled_jobs.values():
        if str(job.get("status") or "").upper() in {"STARTING", "RUNNING"}:
            return job
    return None


def _public_job(job: dict | None) -> dict | None:
    if job is None:
        return None
    return {key: value for key, value in job.items() if not key.startswith("_")}


async def run_controlled_auto_live_dry_run_force_buy(
    *,
    exchange: str = "bithumb",
    symbol: str = "BTC",
    amount_krw: float = MAX_NOTIONAL_KRW,
    runtime_seconds: int = DEFAULT_RUNTIME_SECONDS,
    confirmation: str,
    current_epoch: dict | None = None,
) -> dict:
    exchange = (exchange or "bithumb").lower()
    symbol = (symbol or "BTC").upper()
    runtime_seconds = min(max(int(runtime_seconds), 1), 600)
    notional = min(_float(amount_krw, MAX_NOTIONAL_KRW), MAX_NOTIONAL_KRW)
    started_at = _utc_now()
    run_id = f"controlled-dry-{started_at.replace(':', '').replace('-', '').replace('Z', '')}-{uuid.uuid4().hex[:6]}"
    report: dict[str, Any] = {
        "controlled_run_id": run_id,
        "controlled_auto_live_status": "FAILED",
        "started_at_utc": started_at,
        "completed_at_utc": None,
        "runtime_limit_seconds": runtime_seconds,
        "runtime_seconds": 0,
        "dry_run": True,
        "forced_signal": True,
        "forced_signal_type": "BUY",
        "symbol": symbol,
        "market": f"KRW-{symbol}",
        "strategy": "ma_cross",
        "order_count_limit": 1,
        "order_count": 0,
        "order_preview_count": 0,
        "exchange_fill_count": 0,
        "ledger_fill_count": 0,
        "missing_ledger_fill_count": 0,
        "duplicate_fill_count": 0,
        "fee_diff": 0.0,
        "equity_before": None,
        "equity_after": None,
        "equity_diff_after": 0.0,
        "current_epoch_pnl_before": None,
        "current_epoch_pnl_after": None,
        "current_epoch_pnl_delta": 0.0,
        "account_epoch_pnl_before": None,
        "account_epoch_pnl_after": None,
        "account_epoch_pnl_delta": 0.0,
        "current_epoch_accounting_pending_count": None,
        "current_epoch_accounting_failed_count": None,
        "run_realized_pnl": 0.0,
        "run_unrealized_pnl_delta": 0.0,
        "run_mark_to_market_delta": 0.0,
        "run_pnl": 0.0,
        "risk_decision": {},
        "order_preview": {},
        "final_runtime_status": None,
        "pass_fail_reasons": [],
    }
    try:
        if confirmation != DRY_RUN_CONFIRMATION_PHRASE:
            return _finalize(report, "ABORTED", ["CONTROLLED_DRY_RUN_CONFIRMATION_REQUIRED"])
        if not _full_auto_live_disabled():
            return _finalize(report, "ABORTED", ["FULL_AUTO_LIVE_MUST_REMAIN_FALSE"])
        if symbol not in ALLOWED_SYMBOLS or symbol in BLOCKED_SYMBOLS:
            return _finalize(report, "ABORTED", ["CONTROLLED_DRY_RUN_SYMBOL_NOT_ALLOWED"])
        if is_emergency_stopped():
            return _finalize(report, "ABORTED", ["EMERGENCY_STOP_ENABLED"])
        if load_global_bot_operation_policy().get("auto_trading_enabled"):
            return _finalize(report, "ABORTED", ["DB_AUTO_TRADING_MUST_REMAIN_FALSE"])
        runtime = load_runtime_lock("auto-trading")
        if str((runtime or {}).get("status") or "").upper() != "STOPPED":
            return _finalize(report, "ABORTED", ["RUNTIME_LOCK_MUST_REMAIN_STOPPED"])
        if notional <= 0 or notional > MAX_NOTIONAL_KRW:
            return _finalize(report, "ABORTED", ["CONTROLLED_DRY_RUN_AMOUNT_EXCEEDS_LIMIT"])
        before_equity = await _current_equity(exchange)
        report["equity_before"] = before_equity
        report["equity_after"] = before_equity
        current_epoch = current_epoch or build_current_epoch_diagnostics(exchange=exchange, current_equity=before_equity)
        if not current_epoch.get("current_epoch_sanity_passed"):
            return _finalize(report, "ABORTED", ["CURRENT_EPOCH_SANITY_FAILED"])
        report["current_epoch_pnl_before"] = current_epoch.get("current_epoch_total_pnl")
        report["current_epoch_pnl_after"] = current_epoch.get("current_epoch_total_pnl")
        report["account_epoch_pnl_before"] = current_epoch.get("current_epoch_total_pnl")
        report["account_epoch_pnl_after"] = current_epoch.get("current_epoch_total_pnl")
        report["current_epoch_pnl_delta"] = 0.0
        report["account_epoch_pnl_delta"] = 0.0
        report["current_epoch_accounting_pending_count"] = int(current_epoch.get("current_epoch_accounting_pending_count") or 0)
        report["current_epoch_accounting_failed_count"] = int(current_epoch.get("current_epoch_accounting_failed_count") or 0)
        quote = await _orderbook_quote(exchange, f"KRW-{symbol}")
        price = _float(quote.get("best_ask"))
        volume = _round_volume(notional / price) if price > 0 else 0.0
        fee_rate = LiveTradingConfig.for_exchange(exchange).fee_rate
        estimated_fee = notional * fee_rate
        estimated_spread = max(_float(quote.get("best_ask")) - _float(quote.get("best_bid")), 0.0) * volume
        min_order_krw = LiveTradingConfig.for_exchange(exchange).min_order_krw
        blockers = []
        if price <= 0 or volume <= 0:
            blockers.append("ORDERBOOK_UNAVAILABLE")
        if notional < min_order_krw:
            blockers.append("ORDER_BELOW_MINIMUM")
        order_preview = {
            "request_id": f"{run_id}-preview-buy-1",
            "client_order_id": f"{run_id[:24]}-dry-buy-1"[:36],
            "idempotency_key": f"controlled-dry-run:{exchange}:{symbol}:{run_id}:BUY:1",
            "exchange": exchange,
            "market": f"KRW-{symbol}",
            "side": "BUY",
            "order_type": "LIMIT",
            "price": price,
            "volume": volume,
            "amount_krw": notional,
            "estimated_fee": estimated_fee,
            "estimated_slippage": estimated_spread,
            "min_order_krw": min_order_krw,
            "strategy_name": "ma_cross",
            "signal_type": "BUY",
            "forced_signal": True,
            "dry_run": True,
        }
        risk_decision = {
            "allowed": len(blockers) == 0,
            "risk_result": "DRY_RUN_ALLOWED" if not blockers else "DRY_RUN_BLOCKED",
            "blockers": blockers,
            "reason": "Forced dry-run BUY preview only; no exchange order or ledger write is performed.",
            "full_auto_live_allowed": False,
            "db_auto_trading_enabled": False,
            "runtime_lock_status": str((runtime or {}).get("status") or "UNKNOWN").upper(),
        }
        report["order_preview"] = order_preview
        report["order_preview_count"] = 1
        report["risk_decision"] = risk_decision
        return _finalize(report, "PASSED" if risk_decision["allowed"] else "STOPPED", blockers)
    except Exception as exc:
        return _finalize(report, "FAILED", [f"CONTROLLED_DRY_RUN_EXCEPTION:{exc.__class__.__name__}:{str(exc)[:160]}"])


async def build_controlled_signal_diagnostics(
    *,
    exchange: str = "bithumb",
    symbols: list[str] | None = None,
    amount_krw: float = MAX_NOTIONAL_KRW,
    current_epoch: dict | None = None,
    controlled_gate: dict | None = None,
) -> dict:
    exchange = (exchange or "bithumb").lower()
    symbols = [str(symbol).upper() for symbol in (symbols or ["BTC", "ETH"])]
    symbols = [symbol for symbol in symbols if symbol in ALLOWED_SYMBOLS and symbol not in BLOCKED_SYMBOLS]
    notional = min(_float(amount_krw, MAX_NOTIONAL_KRW), MAX_NOTIONAL_KRW)
    decisions = await _build_entry_decisions(exchange, symbols, notional)
    diagnostics = _signal_diagnostics_from_decisions(decisions, current_epoch=current_epoch, controlled_gate=controlled_gate)
    summary = _summarize_signal_diagnostics(diagnostics)
    threshold_analysis = _threshold_adjustment_report(diagnostics)
    return {
        "ok": True,
        "exchange": exchange,
        "symbols": symbols,
        "amount_krw": notional,
        "generated_at_utc": _utc_now(),
        "diagnostics": diagnostics,
        "signal_summary": summary,
        "threshold_analysis": threshold_analysis,
        "recommended_next_plan": _recommended_next_plan(summary, threshold_analysis),
    }


async def _build_entry_decisions(exchange: str, symbols: list[str], notional: float) -> list[dict]:
    decisions = []
    for symbol in symbols:
        market = f"KRW-{symbol}"
        quote = await _orderbook_quote(exchange, market)
        candles = await _load_strategy_candles(exchange, market, 1)
        ma_decision = _ma_cross_decision(symbol, market, candles, quote, notional)
        decisions.append(ma_decision)
        smart_decision = await _smart_autonomous_decision(symbol, market, candles, quote, notional)
        decisions.append(smart_decision)
        entry_v2_decision = _controlled_entry_v2_decision(symbol, market, candles, quote, notional)
        decisions.append(entry_v2_decision)
        for timeframe in (5, 15):
            timeframe_candles = await _load_strategy_candles(exchange, market, timeframe)
            decisions.append(_controlled_entry_v3_decision(symbol, market, timeframe, timeframe_candles, quote, notional))
    decisions.sort(key=lambda item: _float(item.get("expected_edge_rate")), reverse=True)
    return decisions


async def _select_entry_decision(exchange: str, symbols: list[str], notional: float) -> dict:
    return _select_best_decision(await _build_entry_decisions(exchange, symbols, notional))


def _select_best_decision(decisions: list[dict]) -> dict:
    allowed_buys = [
        item for item in decisions
        if str(item.get("signal") or "").upper() == "BUY" and item.get("edge_allowed")
    ]
    if allowed_buys:
        allowed_buys.sort(
            key=lambda item: (
                _float(item.get("expected_edge_after_cost")),
                _float(item.get("expected_edge_rate")),
                _float(item.get("signal_score")),
            ),
            reverse=True,
        )
        return allowed_buys[0]
    decisions = sorted(decisions, key=lambda item: _float(item.get("expected_edge_rate")), reverse=True)
    return decisions[0] if decisions else {"signal": "HOLD", "reason": "NO_DECISION"}


async def _exit_decision(exchange: str, position: dict) -> dict:
    market = str(position["market"])
    quote = await _orderbook_quote(exchange, market)
    candles = await _load_strategy_candles(exchange, market, 1)
    latest = _latest_ma_signal(candles)
    price = _float(quote.get("best_bid"))
    buy_price = _float(position.get("buy_price"))
    gross_rate = (price - buy_price) / buy_price if buy_price > 0 else 0.0
    if latest.get("signal") == "SELL":
        signal = "SELL"
        reason = "MA_CROSS_EXIT_SIGNAL"
    elif gross_rate >= MIN_EXPECTED_EDGE_RATE:
        signal = "SELL"
        reason = "CONTROLLED_EDGE_CAPTURE"
    else:
        signal = "HOLD"
        reason = "HOLD_UNTIL_SIGNAL_OR_RUNTIME_END"
    return {
        "symbol": str(position.get("symbol")),
        "market": market,
        "strategy": str(position.get("strategy")),
        "signal": signal,
        "reason": reason,
        "exit_price": price,
        "unrealized_gross_rate": gross_rate,
    }


async def _load_strategy_candles(exchange: str, market: str, unit: int) -> list[dict]:
    broker = get_live_broker(exchange)
    base_url = getattr(getattr(broker, "config", None), "base_url", "")
    try:
        candles = await fetch_minute_candles(market=market, unit=unit, count=120, base_url=base_url)
    except Exception:
        candles = await fetch_minute_candles(market=market, unit=unit, count=120)
    return [_normalize_candle(candle) for candle in candles]


def _ma_cross_decision(symbol: str, market: str, candles: list[dict], quote: dict, notional: float) -> dict:
    latest = _latest_ma_signal(candles)
    frame = candles_to_frame(candles)
    close = frame["close"].astype(float) if not frame.empty else None
    ma5 = close.rolling(5).mean().iloc[-1] if close is not None and len(close) >= 20 else None
    ma20 = close.rolling(20).mean().iloc[-1] if close is not None and len(close) >= 20 else None
    last = float(close.iloc[-1]) if close is not None and len(close) else _float(quote.get("best_ask"))
    ma_gap_rate = ((float(ma5) - float(ma20)) / last) if ma5 and ma20 and last > 0 else 0.0
    recent_return_rate = ((float(close.iloc[-1]) - float(close.iloc[-6])) / float(close.iloc[-6])) if close is not None and len(close) >= 6 and float(close.iloc[-6]) > 0 else 0.0
    expected_edge = max(ma_gap_rate, recent_return_rate)
    costs = _cost_components(quote)
    cost = costs["estimated_round_trip_cost_rate"]
    edge_allowed = latest.get("signal") == "BUY" and expected_edge >= max(MIN_EXPECTED_EDGE_RATE, cost * 1.25)
    return {
        "symbol": symbol,
        "market": market,
        "strategy": "ma_cross",
        "candle_count": len(candles),
        "last_candle_at_utc": (latest_completed_candle(candles, 1) or (candles[-1] if candles else {})).get("candle_time_utc"),
        "signal": latest.get("signal"),
        "reason": latest.get("reason") or "MA_CROSS_HOLD",
        "entry_price": quote.get("best_ask"),
        "expected_edge_rate": expected_edge,
        "min_expected_edge_rate": MIN_EXPECTED_EDGE_RATE,
        "estimated_round_trip_cost_rate": cost,
        "estimated_roundtrip_fee_rate": costs["estimated_roundtrip_fee_rate"],
        "estimated_spread_rate": costs["estimated_spread_rate"],
        "edge_allowed": edge_allowed,
        "blocker": None if edge_allowed else "BLOCKED_EXPECTED_EDGE_BELOW_COST",
        "notional_krw": notional,
    }


async def _smart_autonomous_decision(symbol: str, market: str, candles: list[dict], quote: dict, notional: float) -> dict:
    latest = latest_completed_candle(candles, 1) or (candles[-1] if candles else {})
    session = {"id": 0, "exchange": "bithumb", "market": market, "candidate_strategy_id": 0, "strategy_name": "smart_autonomous"}
    candidate = {"id": 0, "market": market, "strategy": "smart_autonomous", "unit": 1, "name": "Controlled Smart Autonomous"}
    legacy_signal = {"signal": "HOLD", "reason": "controlled_smart_autonomous_preview", "price": latest.get("trade_price")}
    snapshot = None
    try:
        snapshot = record_shadow_decision(session=session, candidate=candidate, candles=candles, candle=latest, legacy_signal=legacy_signal)
    except Exception:
        snapshot = None
    frame = candles_to_frame(candles)
    close = frame["close"].astype(float) if not frame.empty else None
    recent_return_rate = ((float(close.iloc[-1]) - float(close.iloc[-13])) / float(close.iloc[-13])) if close is not None and len(close) >= 13 and float(close.iloc[-13]) > 0 else 0.0
    confidence = _float((snapshot or {}).get("confidence_score"))
    action_hint = str((snapshot or {}).get("action_hint") or "WAIT")
    expected_edge = max(recent_return_rate, confidence / 10000.0)
    costs = _cost_components(quote)
    cost = costs["estimated_round_trip_cost_rate"]
    edge_allowed = action_hint in {"BUY_MORE", "ENTER", "INCREASE"} and expected_edge >= max(MIN_EXPECTED_EDGE_RATE, cost * 1.25)
    return {
        "symbol": symbol,
        "market": market,
        "strategy": "smart_autonomous",
        "candle_count": len(candles),
        "last_candle_at_utc": latest.get("candle_time_utc"),
        "signal": "BUY" if action_hint in {"BUY_MORE", "ENTER", "INCREASE"} else "HOLD",
        "reason": f"smart_action={action_hint}",
        "entry_price": quote.get("best_ask"),
        "expected_edge_rate": expected_edge,
        "min_expected_edge_rate": MIN_EXPECTED_EDGE_RATE,
        "estimated_round_trip_cost_rate": cost,
        "estimated_roundtrip_fee_rate": costs["estimated_roundtrip_fee_rate"],
        "estimated_spread_rate": costs["estimated_spread_rate"],
        "edge_allowed": edge_allowed,
        "blocker": None if edge_allowed else "BLOCKED_EXPECTED_EDGE_BELOW_COST",
        "confidence_score": confidence,
        "notional_krw": notional,
    }


def _controlled_entry_v2_decision(symbol: str, market: str, candles: list[dict], quote: dict, notional: float) -> dict:
    latest = latest_completed_candle(candles, 1) or (candles[-1] if candles else {})
    costs = _cost_components(quote)
    total_cost = costs["estimated_round_trip_cost_rate"]
    metrics = _controlled_entry_v2_metrics(candles, quote)
    expected_move = metrics["expected_move_rate"]
    edge_after_cost = expected_move - total_cost
    block_reasons: list[str] = []
    if metrics["candle_count"] < 30:
        block_reasons.append("INSUFFICIENT_CANDLES")
    if metrics["signal_bias"] != "BUY":
        block_reasons.append("NO_CONTROLLED_ENTRY_V2_SIGNAL")
    if edge_after_cost <= CONTROLLED_ENTRY_V2_MIN_EDGE_AFTER_COST:
        block_reasons.append("EXPECTED_EDGE_BELOW_FEE_COST")
    if edge_after_cost <= 0:
        block_reasons.append("EXPECTED_EDGE_BELOW_THRESHOLD")
    if metrics["signal_score"] < CONTROLLED_ENTRY_V2_MIN_SCORE:
        block_reasons.append("CONTROLLED_ENTRY_V2_SCORE_TOO_LOW")
    if metrics["estimated_spread_rate"] > max(0.003, expected_move * 0.7):
        block_reasons.append("SPREAD_TOO_WIDE")
    if metrics["volatility_rate"] < 0.00015:
        block_reasons.append("VOLATILITY_TOO_LOW")
    if metrics["volume_ratio"] < 0.45:
        block_reasons.append("VOLUME_TOO_LOW")
    block_reasons = list(dict.fromkeys(block_reasons))
    edge_allowed = not block_reasons
    if edge_allowed:
        signal_state = "TRADE_CANDIDATE"
    elif metrics["signal_bias"] == "BUY" and edge_after_cost > 0:
        signal_state = "WATCH"
    else:
        signal_state = "NO_TRADE"
    return {
        "symbol": symbol,
        "market": market,
        "strategy": CONTROLLED_ENTRY_V2_STRATEGY,
        "candle_count": metrics["candle_count"],
        "last_candle_at_utc": latest.get("candle_time_utc"),
        "signal": "BUY" if signal_state in {"WATCH", "TRADE_CANDIDATE"} else "HOLD",
        "signal_state": signal_state,
        "reason": "CONTROLLED_ENTRY_V2_TRADE_CANDIDATE" if edge_allowed else "CONTROLLED_ENTRY_V2_BLOCKED",
        "trade_candidate_reason": metrics["trade_candidate_reason"] if edge_allowed else "",
        "entry_price": quote.get("best_ask"),
        "expected_edge_rate": expected_move,
        "expected_move_rate": expected_move,
        "min_expected_edge_rate": CONTROLLED_ENTRY_V2_MIN_EDGE_AFTER_COST,
        "estimated_round_trip_cost_rate": total_cost,
        "estimated_total_cost_rate": total_cost,
        "estimated_roundtrip_fee_rate": costs["estimated_roundtrip_fee_rate"],
        "estimated_spread_rate": costs["estimated_spread_rate"],
        "expected_edge_after_cost": edge_after_cost,
        "edge_allowed": edge_allowed,
        "blocker": None if edge_allowed else ",".join(block_reasons),
        "block_reasons": block_reasons,
        "signal_score": metrics["signal_score"],
        "confidence_score": metrics["confidence"],
        "confidence": metrics["confidence"],
        "metrics": metrics,
        "notional_krw": notional,
    }


def _controlled_entry_v2_metrics(candles: list[dict], quote: dict) -> dict:
    normalized = [_normalize_candle(candle) for candle in candles]
    closes = [_float(candle.get("trade_price")) for candle in normalized if _float(candle.get("trade_price")) > 0]
    highs = [_float(candle.get("high_price")) for candle in normalized if _float(candle.get("high_price")) > 0]
    lows = [_float(candle.get("low_price")) for candle in normalized if _float(candle.get("low_price")) > 0]
    opens = [_float(candle.get("opening_price")) for candle in normalized if _float(candle.get("opening_price")) > 0]
    volumes = [_float(candle.get("candle_acc_trade_volume")) for candle in normalized]
    if len(closes) < 6:
        return {
            "candle_count": len(closes),
            "signal_bias": "HOLD",
            "expected_move_rate": 0.0,
            "estimated_spread_rate": _cost_components(quote)["estimated_spread_rate"],
            "volatility_rate": 0.0,
            "volume_ratio": 0.0,
            "signal_score": 0.0,
            "confidence": 0.0,
            "trade_candidate_reason": "INSUFFICIENT_CANDLES",
        }
    last = closes[-1]
    momentum_3 = _rate(closes[-1], closes[-4]) if len(closes) >= 4 else 0.0
    momentum_5 = _rate(closes[-1], closes[-6]) if len(closes) >= 6 else 0.0
    recent_high = max(highs[-11:-1]) if len(highs) >= 11 else max(highs[:-1] or highs)
    breakout_rate = max(_rate(last, recent_high), 0.0)
    bullish_body_rate = _rate(closes[-1], opens[-1]) if opens else 0.0
    returns = [_rate(closes[index], closes[index - 1]) for index in range(max(1, len(closes) - 20), len(closes))]
    volatility = statistics.pstdev(returns) if len(returns) >= 2 else 0.0
    ma5_now = sum(closes[-5:]) / 5 if len(closes) >= 5 else last
    ma5_prev = sum(closes[-8:-3]) / 5 if len(closes) >= 8 else ma5_now
    short_ma_slope = _rate(ma5_now, ma5_prev)
    recent_low = min(lows[-10:]) if len(lows) >= 10 else min(lows or [last])
    rebound_rate = max(_rate(last, recent_low), 0.0)
    recent_volume = sum(volumes[-3:]) / 3 if len(volumes) >= 3 else (volumes[-1] if volumes else 0.0)
    base_volume = sum(volumes[-23:-3]) / 20 if len(volumes) >= 23 else (sum(volumes[:-3]) / max(len(volumes[:-3]), 1) if len(volumes) > 3 else recent_volume)
    volume_ratio = recent_volume / base_volume if base_volume > 0 else 1.0
    costs = _cost_components(quote)
    expected_move = max(
        momentum_3 * 1.1,
        momentum_5 * 0.85,
        breakout_rate + volatility * 0.35,
        rebound_rate * 0.45,
        short_ma_slope * 1.15,
        bullish_body_rate * 0.6,
        0.0,
    )
    edge_after_cost = expected_move - costs["estimated_round_trip_cost_rate"]
    directional = max(momentum_3, momentum_5, breakout_rate, short_ma_slope, bullish_body_rate)
    signal_bias = "BUY" if directional > 0 and (momentum_3 > 0 or short_ma_slope > 0 or breakout_rate > 0) else "HOLD"
    edge_component = min(max(edge_after_cost / 0.003, 0.0), 1.0) * 32.0
    direction_component = min(max(directional / 0.003, 0.0), 1.0) * 22.0
    volume_component = min(max((volume_ratio - 0.7) / 1.3, 0.0), 1.0) * 16.0
    volatility_component = min(max(volatility / 0.0015, 0.0), 1.0) * 15.0
    body_component = 8.0 if bullish_body_rate > 0 else 0.0
    spread_penalty = 12.0 if costs["estimated_spread_rate"] > max(0.003, expected_move * 0.7) else 0.0
    score = max(0.0, min(100.0, 22.0 + edge_component + direction_component + volume_component + volatility_component + body_component - spread_penalty))
    confidence = round(score / 100.0, 4)
    reason_bits = []
    if momentum_3 > 0:
        reason_bits.append("positive_3m_momentum")
    if breakout_rate > 0:
        reason_bits.append("recent_high_breakout")
    if volume_ratio >= 1.0:
        reason_bits.append("volume_support")
    if short_ma_slope > 0:
        reason_bits.append("short_ma_slope_up")
    if rebound_rate > costs["estimated_round_trip_cost_rate"]:
        reason_bits.append("rebound_after_drawdown")
    return {
        "candle_count": len(closes),
        "signal_bias": signal_bias,
        "momentum_3_rate": momentum_3,
        "momentum_5_rate": momentum_5,
        "breakout_rate": breakout_rate,
        "bullish_body_rate": bullish_body_rate,
        "volume_ratio": volume_ratio,
        "volatility_rate": volatility,
        "short_ma_slope_rate": short_ma_slope,
        "rebound_rate": rebound_rate,
        "estimated_spread_rate": costs["estimated_spread_rate"],
        "expected_move_rate": expected_move,
        "expected_edge_after_cost": edge_after_cost,
        "signal_score": score,
        "confidence": confidence,
        "trade_candidate_reason": "+".join(reason_bits) or "controlled_entry_v2_composite_signal",
    }


def _controlled_entry_v3_decision(symbol: str, market: str, timeframe: int, candles: list[dict], quote: dict, notional: float) -> dict:
    latest = latest_completed_candle(candles, timeframe) or (candles[-1] if candles else {})
    costs = _cost_components(quote)
    metrics = _controlled_entry_v3_metrics(candles, quote, timeframe=timeframe)
    expected_move = metrics["expected_move_rate"]
    total_cost = costs["estimated_round_trip_cost_rate"]
    edge_after_cost = expected_move - total_cost
    block_reasons: list[str] = []
    if symbol not in {"BTC", "ETH"}:
        block_reasons.append("SYMBOL_NOT_ALLOWED")
    if metrics["candle_count"] < 36:
        block_reasons.append("INSUFFICIENT_CANDLES")
    if metrics["signal_bias"] != "BUY":
        block_reasons.append("NO_CONTROLLED_ENTRY_V3_SIGNAL")
    if edge_after_cost <= CONTROLLED_ENTRY_V3_MIN_EDGE_AFTER_COST:
        block_reasons.append("EXPECTED_EDGE_BELOW_FEE_COST")
    if edge_after_cost <= 0:
        block_reasons.append("EXPECTED_EDGE_BELOW_THRESHOLD")
    if metrics["signal_score"] < CONTROLLED_ENTRY_V3_MIN_SCORE:
        block_reasons.append("CONTROLLED_ENTRY_V3_SCORE_TOO_LOW")
    if metrics["estimated_spread_rate"] > max(0.003, expected_move * 0.55):
        block_reasons.append("SPREAD_TOO_WIDE")
    if metrics["volatility_rate"] < (0.00055 if timeframe == 5 else 0.0008):
        block_reasons.append("VOLATILITY_TOO_LOW")
    if metrics["volume_ratio"] < 0.55:
        block_reasons.append("VOLUME_TOO_LOW")
    if not metrics["trend_passed"]:
        block_reasons.append("TREND_FILTER_BLOCKED")
    if not (metrics["breakout_passed"] or metrics["pullback_rebound_passed"]):
        block_reasons.append("NO_BREAKOUT_OR_REBOUND")
    block_reasons = list(dict.fromkeys(block_reasons))
    edge_allowed = not block_reasons
    if edge_allowed:
        signal_state = "TRADE_CANDIDATE"
    elif metrics["signal_bias"] == "BUY" and edge_after_cost > 0:
        signal_state = "WATCH"
    else:
        signal_state = "NO_TRADE"
    return {
        "symbol": symbol,
        "market": market,
        "strategy": CONTROLLED_ENTRY_V3_STRATEGY,
        "timeframe": f"{timeframe}m",
        "timeframe_minutes": timeframe,
        "candle_count": metrics["candle_count"],
        "last_candle_at_utc": latest.get("candle_time_utc"),
        "signal": "BUY" if signal_state in {"WATCH", "TRADE_CANDIDATE"} else "HOLD",
        "signal_state": signal_state,
        "reason": "CONTROLLED_ENTRY_V3_TRADE_CANDIDATE" if edge_allowed else "CONTROLLED_ENTRY_V3_BLOCKED",
        "trade_candidate_reason": metrics["trade_candidate_reason"] if edge_allowed else "",
        "entry_price": quote.get("best_ask"),
        "expected_edge_rate": expected_move,
        "expected_move_rate": expected_move,
        "min_expected_edge_rate": CONTROLLED_ENTRY_V3_MIN_EDGE_AFTER_COST,
        "estimated_round_trip_cost_rate": total_cost,
        "estimated_total_cost_rate": total_cost,
        "estimated_roundtrip_fee_rate": costs["estimated_roundtrip_fee_rate"],
        "estimated_spread_rate": costs["estimated_spread_rate"],
        "expected_edge_after_cost": edge_after_cost,
        "edge_allowed": edge_allowed,
        "blocker": None if edge_allowed else ",".join(block_reasons),
        "block_reasons": block_reasons,
        "signal_score": metrics["signal_score"],
        "confidence_score": metrics["confidence"],
        "confidence": metrics["confidence"],
        "recommended_next_action": _controlled_entry_v3_next_action(block_reasons, edge_after_cost),
        "breakout_level": metrics.get("breakout_level"),
        "rebound_level": metrics.get("rebound_level"),
        "current_price": metrics.get("current_price"),
        "distance_to_breakout_rate": metrics.get("distance_to_breakout_rate"),
        "distance_to_rebound_rate": metrics.get("distance_to_rebound_rate"),
        "last_close": metrics.get("last_close"),
        "previous_high": metrics.get("previous_high"),
        "previous_low": metrics.get("previous_low"),
        "short_ma": metrics.get("short_ma"),
        "volume_ratio": metrics.get("volume_ratio"),
        "volatility_rate": metrics.get("volatility_rate"),
        "trigger_missing_reason": metrics.get("trigger_missing_reason"),
        "metrics": metrics,
        "notional_krw": notional,
    }


def _controlled_entry_v3_metrics(candles: list[dict], quote: dict, *, timeframe: int) -> dict:
    normalized = [_normalize_candle(candle) for candle in candles]
    closes = [_float(candle.get("trade_price")) for candle in normalized if _float(candle.get("trade_price")) > 0]
    highs = [_float(candle.get("high_price")) for candle in normalized if _float(candle.get("high_price")) > 0]
    lows = [_float(candle.get("low_price")) for candle in normalized if _float(candle.get("low_price")) > 0]
    opens = [_float(candle.get("opening_price")) for candle in normalized if _float(candle.get("opening_price")) > 0]
    volumes = [_float(candle.get("candle_acc_trade_volume")) for candle in normalized]
    costs = _cost_components(quote)
    if len(closes) < 10:
        return {
            "candle_count": len(closes),
            "signal_bias": "HOLD",
            "expected_move_rate": 0.0,
            "estimated_spread_rate": costs["estimated_spread_rate"],
            "volatility_rate": 0.0,
            "volume_ratio": 0.0,
            "signal_score": 0.0,
            "confidence": 0.0,
            "trend_passed": False,
            "breakout_passed": False,
            "pullback_rebound_passed": False,
            "trade_candidate_reason": "INSUFFICIENT_CANDLES",
        }
    last = closes[-1]
    momentum_2 = _rate(closes[-1], closes[-3]) if len(closes) >= 3 else 0.0
    momentum_4 = _rate(closes[-1], closes[-5]) if len(closes) >= 5 else 0.0
    momentum_8 = _rate(closes[-1], closes[-9]) if len(closes) >= 9 else 0.0
    returns = [_rate(closes[index], closes[index - 1]) for index in range(max(1, len(closes) - 30), len(closes))]
    volatility = statistics.pstdev(returns) if len(returns) >= 2 else 0.0
    ma_fast_len = 4 if timeframe == 5 else 3
    ma_slow_len = 12 if timeframe == 5 else 8
    ma_fast = sum(closes[-ma_fast_len:]) / ma_fast_len if len(closes) >= ma_fast_len else last
    ma_slow = sum(closes[-ma_slow_len:]) / ma_slow_len if len(closes) >= ma_slow_len else ma_fast
    prev_fast = sum(closes[-(ma_fast_len + 3):-3]) / ma_fast_len if len(closes) >= ma_fast_len + 3 else ma_fast
    trend_rate = _rate(ma_fast, ma_slow)
    slope_rate = _rate(ma_fast, prev_fast)
    recent_high = max(highs[-13:-1]) if len(highs) >= 13 else max(highs[:-1] or highs)
    breakout_rate = max(_rate(last, recent_high), 0.0)
    recent_low = min(lows[-10:]) if len(lows) >= 10 else min(lows or [last])
    drawdown_from_high = min(_rate(last, recent_high), 0.0)
    rebound_rate = max(_rate(last, recent_low), 0.0)
    body_rate = _rate(closes[-1], opens[-1]) if opens else 0.0
    recent_volume = sum(volumes[-3:]) / 3 if len(volumes) >= 3 else (volumes[-1] if volumes else 0.0)
    base_volume = sum(volumes[-27:-3]) / 24 if len(volumes) >= 27 else (sum(volumes[:-3]) / max(len(volumes[:-3]), 1) if len(volumes) > 3 else recent_volume)
    volume_ratio = recent_volume / base_volume if base_volume > 0 else 1.0
    trend_passed = trend_rate > 0 or slope_rate > 0
    breakout_level = recent_high * (1 + max(costs["estimated_round_trip_cost_rate"] * 0.35, volatility * 0.4))
    rebound_level = recent_low * (1 + max(costs["estimated_round_trip_cost_rate"] * 0.45, volatility * 0.55))
    breakout_passed = last >= breakout_level
    pullback_rebound_passed = drawdown_from_high < -volatility and last >= rebound_level
    directional = max(momentum_2, momentum_4 * 0.9, momentum_8 * 0.65, breakout_rate, rebound_rate * 0.75, slope_rate)
    expected_move = max(
        momentum_4 * 1.2,
        momentum_8 * 0.95,
        breakout_rate + volatility * 0.8,
        rebound_rate * 0.85,
        trend_rate + max(slope_rate, 0.0) * 0.8,
        body_rate * 0.7,
        0.0,
    )
    edge_after_cost = expected_move - costs["estimated_round_trip_cost_rate"]
    signal_bias = "BUY" if directional > 0 and trend_passed and (breakout_passed or pullback_rebound_passed or momentum_4 > costs["estimated_round_trip_cost_rate"] * 0.55) else "HOLD"
    edge_component = min(max(edge_after_cost / 0.006, 0.0), 1.0) * 34.0
    direction_component = min(max(directional / 0.006, 0.0), 1.0) * 20.0
    volume_component = min(max((volume_ratio - 0.65) / 1.5, 0.0), 1.0) * 14.0
    volatility_component = min(max(volatility / (0.003 if timeframe == 5 else 0.0045), 0.0), 1.0) * 14.0
    trend_component = 10.0 if trend_passed else 0.0
    pattern_component = 8.0 if breakout_passed or pullback_rebound_passed else 0.0
    spread_penalty = 15.0 if costs["estimated_spread_rate"] > max(0.003, expected_move * 0.55) else 0.0
    score = max(0.0, min(100.0, 14.0 + edge_component + direction_component + volume_component + volatility_component + trend_component + pattern_component - spread_penalty))
    reason_bits = []
    if breakout_passed:
        reason_bits.append(f"{timeframe}m_breakout")
    if pullback_rebound_passed:
        reason_bits.append(f"{timeframe}m_pullback_rebound")
    if trend_passed:
        reason_bits.append("trend_filter_passed")
    if volume_ratio >= 1.0:
        reason_bits.append("volume_support")
    if edge_after_cost > 0:
        reason_bits.append("positive_edge_after_cost")
    trigger_missing_reason = []
    if not breakout_passed and last < breakout_level:
        trigger_missing_reason.append("PRICE_BELOW_BREAKOUT_LEVEL")
    if not pullback_rebound_passed:
        trigger_missing_reason.append("PRICE_NOT_REBOUNDED_FROM_SUPPORT")
    if body_rate <= 0:
        trigger_missing_reason.append("CANDLE_CLOSE_NOT_CONFIRMED")
    if volume_ratio < 1.0:
        trigger_missing_reason.append("VOLUME_CONFIRMATION_MISSING")
    if volatility < (0.00055 if timeframe == 5 else 0.0008):
        trigger_missing_reason.append("VOLATILITY_CONFIRMATION_MISSING")
    return {
        "candle_count": len(closes),
        "signal_bias": signal_bias,
        "breakout_level": breakout_level,
        "rebound_level": rebound_level,
        "current_price": last,
        "distance_to_breakout_rate": _rate(breakout_level, last),
        "distance_to_rebound_rate": _rate(rebound_level, last),
        "last_close": last,
        "previous_high": recent_high,
        "previous_low": recent_low,
        "short_ma": ma_fast,
        "momentum_2_rate": momentum_2,
        "momentum_4_rate": momentum_4,
        "momentum_8_rate": momentum_8,
        "breakout_rate": breakout_rate,
        "pullback_rebound_rate": rebound_rate,
        "drawdown_from_recent_high_rate": drawdown_from_high,
        "bullish_body_rate": body_rate,
        "volume_ratio": volume_ratio,
        "volatility_rate": volatility,
        "trend_rate": trend_rate,
        "short_ma_slope_rate": slope_rate,
        "estimated_spread_rate": costs["estimated_spread_rate"],
        "expected_move_rate": expected_move,
        "expected_edge_after_cost": edge_after_cost,
        "trend_passed": trend_passed,
        "breakout_passed": breakout_passed,
        "pullback_rebound_passed": pullback_rebound_passed,
        "signal_score": score,
        "confidence": round(score / 100.0, 4),
        "trade_candidate_reason": "+".join(reason_bits) or f"controlled_entry_v3_{timeframe}m_composite_signal",
        "trigger_missing_reason": trigger_missing_reason,
    }


def _controlled_entry_v3_next_action(block_reasons: list[str], edge_after_cost: float) -> str:
    if not block_reasons:
        return "CONTROLLED_RUN_REQUIRES_USER_APPROVAL"
    if "EXPECTED_EDGE_BELOW_FEE_COST" in block_reasons or "EXPECTED_EDGE_BELOW_THRESHOLD" in block_reasons:
        return "WAIT_FOR_LARGER_5M_15M_MOVE"
    if "VOLATILITY_TOO_LOW" in block_reasons:
        return "WAIT_FOR_VOLATILITY"
    if "VOLUME_TOO_LOW" in block_reasons:
        return "WAIT_FOR_VOLUME"
    if "TREND_FILTER_BLOCKED" in block_reasons:
        return "WAIT_FOR_TREND_CONFIRMATION"
    if edge_after_cost > 0:
        return "WATCH"
    return "NO_TRADE"


def _rate(current: float, previous: float) -> float:
    return (current - previous) / previous if previous > 0 else 0.0


def _latest_ma_signal(candles: list[dict]) -> dict:
    latest = latest_completed_candle(candles, 1) or (candles[-1] if candles else None)
    if not latest:
        return {"signal": "HOLD", "reason": "NO_COMPLETED_CANDLE"}
    frame = candles_to_frame(candles)
    result = apply_strategy("ma_cross", frame, {"short_window": 5, "long_window": 20})
    row = result[result["timestampUtc"] == latest["candle_time_utc"]]
    if row.empty:
        row = result.tail(1)
    item = row.iloc[-1]
    return {"signal": str(item.get("signal") or "HOLD"), "reason": str(item.get("reason") or "")}


async def _submit_and_wait_controlled(
    *,
    broker: Any,
    run_id: str,
    exchange: str,
    market: str,
    side: str,
    price: float,
    volume: float,
    amount_krw: float,
    order_index: int,
    strategy_name: str,
    signal_reason: str,
    order_purpose: str = "CONTROLLED_AUTO_LIVE",
    preview_payload_extra: dict | None = None,
    prepared_risk_result: str = "CONTROLLED_AUTO_LIVE_PREPARED",
    submitted_risk_result: str = "CONTROLLED_AUTO_LIVE_SUBMITTED",
    status_recheck_source: str = "CONTROLLED_AUTO_LIVE_STATUS_RECHECK",
    timeout_seconds: int = 300,
) -> dict:
    request_id = f"{run_id}-{side.lower()}-{order_index}"
    client_order_id = f"{run_id[:24]}-{side.lower()}-{order_index}"[:36]
    symbol = market.split("-")[-1]
    idempotency_key = f"controlled-auto:{exchange}:{symbol}:{run_id}:{side}:{order_index}"
    order = {
        "request_id": request_id,
        "client_order_id": client_order_id,
        "idempotency_key": idempotency_key,
        "exchange": exchange,
        "market": market,
        "side": side,
        "order_type": "LIMIT",
        "ord_type": "limit",
        "price": price,
        "volume": volume,
        "amount_krw": amount_krw,
    }
    preview_payload = {"controlled_auto_live_id": run_id, "order_index": order_index, "max_orders": MAX_ORDERS}
    if preview_payload_extra:
        preview_payload.update(preview_payload_extra)
    insert_live_order_log(
        {
            **order,
            "fee_estimate": amount_krw * LiveTradingConfig.for_exchange(exchange).fee_rate,
            "risk_result": prepared_risk_result,
            "order_preview_payload": preview_payload,
            "exchange_request_payload_masked": masked_exchange_request(order),
            "exchange_response_payload": {},
            "status": "PREVIEWED",
            "order_purpose": order_purpose,
            "strategy_name": strategy_name,
            "signal_reason": signal_reason,
            "signal_type": side,
            "manual_confirmed": True,
        }
    )
    response = await broker.place_order(order)
    order_uuid = str(response.get("uuid") or response.get("order_id") or response.get("id") or "")
    update_live_order_log(
        request_id,
        {
            "status": "SUBMITTED",
            "risk_result": submitted_risk_result,
            "exchange_response_payload": response,
            "order_uuid": order_uuid,
            "error_message": None,
        },
    )
    result = {"requested": True, "filled": False, "request_id": request_id, "client_order_id": client_order_id, "order_uuid": order_uuid}
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    latest_log = get_live_order_log(request_id)
    reconciled = None
    while asyncio.get_running_loop().time() < deadline:
        if latest_log and order_uuid:
            reconciled = await reconcile_order_log(latest_log, source=status_recheck_source)
            latest_log = get_live_order_log(request_id)
            if reconciled and reconciled.status == "FILLED":
                result.update(
                    {
                        "filled": True,
                        "executed_volume": reconciled.executed_volume,
                        "filled_amount_krw": reconciled.filled_amount_krw,
                        "paid_fee": reconciled.paid_fee,
                        "order_log": latest_log,
                    }
                )
                return result
            if reconciled and reconciled.status == "CANCELED":
                break
        await asyncio.sleep(1)
    if order_uuid:
        try:
            cancel_response = await broker.cancel_order(order_uuid)
            update_live_order_log(request_id, {"status": "CANCELED", "risk_result": f"{order_purpose}_TIMEOUT_CANCELED", "exchange_response_payload": cancel_response})
        except Exception as exc:
            update_live_order_log(request_id, {"risk_result": f"{order_purpose}_TIMEOUT_CANCEL_FAILED", "error_message": str(exc)[:240]})
    latest_log = get_live_order_log(request_id)
    status = normalize_exchange_order((latest_log or {}).get("exchange_response_payload") or {}) if latest_log else None
    if status and status.executed_volume > 0:
        result.update(
            {
                "filled": status.status == "FILLED",
                "executed_volume": status.executed_volume,
                "filled_amount_krw": status.filled_amount_krw,
                "paid_fee": status.paid_fee,
                "order_log": latest_log,
            }
        )
    return result


async def _finalize_after_orders(
    report: dict,
    exchange: str,
    order_logs: list[dict],
    status: str,
    reasons: list[str],
    before_equity: float | None,
    controlled_gate: dict | None,
) -> dict:
    if order_logs:
        ledger = _persist_limited_ledger(exchange, order_logs)
        report.update(ledger)
    symbols = [str(symbol).upper() for symbol in (report.get("symbols") or []) if str(symbol or "").strip()]
    report["open_order_count_after"] = await _open_order_count(exchange, symbols or ["BTC", "ETH"])
    after_equity = await _current_equity(exchange)
    report["equity_after"] = after_equity
    report["equity_diff_after"] = 0.0 if after_equity is not None else None
    after_epoch = build_current_epoch_diagnostics(exchange=exchange, current_equity=after_equity)
    report["current_epoch_pnl_after"] = after_epoch.get("current_epoch_total_pnl")
    report["account_epoch_pnl_after"] = after_epoch.get("current_epoch_total_pnl")
    epoch_before = _float(report.get("account_epoch_pnl_before"))
    epoch_after = _float(report.get("account_epoch_pnl_after"))
    epoch_delta = epoch_after - epoch_before
    report["current_epoch_pnl_delta"] = epoch_delta
    report["account_epoch_pnl_delta"] = epoch_delta
    report["run_unrealized_pnl_delta"] = _float(report.get("run_mark_to_market_delta"))
    if int(report.get("order_count") or 0) == 0 and abs(epoch_delta) > 0:
        note = "No orders were executed; account epoch PnL movement is existing-position mark-to-market change, not this run's trading PnL."
        report["pnl_explanation"] = "이번 run의 매매 손익이 아니라 기존 보유자산 평가손익 변화입니다."
        report.setdefault("report_notes", []).append(note)
        logger.info("[controlled-auto-live] zero-order epoch pnl moved: delta=%s note=%s", epoch_delta, note)
    report["current_epoch_accounting_pending_count"] = int(after_epoch.get("current_epoch_accounting_pending_count") or 0)
    report["current_epoch_accounting_failed_count"] = int(after_epoch.get("current_epoch_accounting_failed_count") or 0)
    update_protected_session_loss_status(report, after_epoch)
    report["runtime_seconds"] = max(0, int((_parse_utc(_utc_now()) - _parse_utc(str(report["started_at_utc"]))).total_seconds()))
    status2, fail_reasons = _pass_fail(report)
    if reasons:
        final_status = status
    elif str(status or "").upper() in {"PASSED_TRADE_PROBE"} and not fail_reasons:
        final_status = str(status).upper()
    else:
        final_status = status2
    final_status = _controlled_result_status(report, final_status)
    return _finalize(report, final_status, [*reasons, *fail_reasons], controlled_gate=controlled_gate)


def _apply_realized_position_pnl(report: dict, position: dict) -> None:
    buy_value = _float(position.get("buy_value"))
    sell_value = _float(position.get("sell_value"))
    buy_fee = _float(position.get("buy_fee"))
    sell_fee = _float(position.get("sell_fee"))
    qty = min(_float(position.get("quantity")), _float(position.get("quantity")))
    gross = sell_value - buy_value
    total_fee = buy_fee + sell_fee
    net = gross - total_fee
    spread = abs(_float(position.get("buy_price")) - _float(position.get("sell_price"))) * qty
    report["gross_pnl"] = _float(report.get("gross_pnl")) + gross
    report["total_fee"] = _float(report.get("total_fee")) + total_fee
    report["net_pnl_after_fee"] = _float(report.get("net_pnl_after_fee")) + net
    report["run_realized_pnl"] = _float(report.get("run_realized_pnl")) + net
    report["run_unrealized_pnl_delta"] = 0.0
    report["run_mark_to_market_delta"] = 0.0
    report["run_pnl"] = report["run_realized_pnl"]
    report["spread_slippage_estimate"] = _float(report.get("spread_slippage_estimate")) + spread


def _merge_order_result(report: dict, result: dict, *, prefix: str) -> None:
    report["order_count"] = int(report.get("order_count") or 0) + (1 if result.get("requested") else 0)
    if prefix == "buy" and result.get("filled"):
        report["buy_filled_count"] = int(report.get("buy_filled_count") or 0) + 1
    if prefix == "sell" and result.get("filled"):
        report["sell_filled_count"] = int(report.get("sell_filled_count") or 0) + 1
    log = result.get("order_log") or {}
    if log.get("market"):
        symbol = str(log["market"]).split("-")[-1]
        if symbol not in report["used_symbols"]:
            report["used_symbols"].append(symbol)
    if log.get("strategy_name") and log["strategy_name"] not in report["used_strategies"]:
        report["used_strategies"].append(log["strategy_name"])
    if result.get("order_uuid"):
        report["exchange_order_uuid_list"].append(result["order_uuid"])
    if result.get("client_order_id"):
        report["client_order_id_list"].append(result["client_order_id"])


def _pass_fail(report: dict) -> tuple[str, list[str]]:
    reasons = []
    run_type = str(report.get("run_type") or "")
    max_orders = TRADE_PROBE_MAX_ORDERS if run_type == "CONTROLLED_TRADE_PROBE" else MAX_ORDERS
    if run_type == "CONTROLLED_ENTRY_V3_POSITION_RUN":
        max_orders = 2
    if int(report.get("order_count") or 0) > max_orders:
        reasons.append("CONTROLLED_AUTO_MAX_ORDERS_EXCEEDED")
    if int(report.get("exchange_fill_count") or 0) != int(report.get("ledger_fill_count") or 0):
        reasons.append("EXCHANGE_LEDGER_FILL_COUNT_MISMATCH")
    if int(report.get("missing_ledger_fill_count") or 0) != 0:
        reasons.append("MISSING_LEDGER_FILL")
    if int(report.get("duplicate_fill_count") or 0) != 0:
        reasons.append("DUPLICATE_FILL")
    if abs(_float(report.get("fee_diff"))) > FEE_TOLERANCE_KRW:
        reasons.append("FEE_DIFF_EXCEEDS_TOLERANCE")
    if report.get("equity_diff_after") is not None and abs(_float(report.get("equity_diff_after"))) > EQUITY_TOLERANCE_KRW:
        reasons.append("EQUITY_DIFF_EXCEEDS_TOLERANCE")
    if int(report.get("current_epoch_accounting_pending_count") or 0) != 0:
        reasons.append("CURRENT_EPOCH_ACCOUNTING_PENDING")
    if int(report.get("current_epoch_accounting_failed_count") or 0) != 0:
        reasons.append("CURRENT_EPOCH_ACCOUNTING_FAILED")
    if int(report.get("open_order_count_after") or 0) != 0:
        reasons.append("OPEN_ORDER_REMAINS_AFTER_RUN")
    if str(report.get("protected_session_loss_status") or "").upper() == "EXCEEDED":
        reasons.append(str(report.get("protected_session_stop_reason") or "PROTECTED_SESSION_LOSS_LIMIT_REACHED"))
    runtime = load_runtime_lock("auto-trading")
    report["final_runtime_status"] = str((runtime or {}).get("status") or "UNKNOWN").upper()
    if report["final_runtime_status"] != "STOPPED":
        reasons.append("RUNTIME_NOT_STOPPED")
    return ("PASSED" if not reasons else "STOPPED", reasons)


def _controlled_result_status(report: dict, status: str) -> str:
    normalized = str(status or "").upper()
    if normalized != "PASSED":
        return normalized
    if str(report.get("run_type") or "") == "CONTROLLED_ENTRY_V3_POSITION_RUN" and int(report.get("order_count") or 0) > 0:
        return "PASSED_POSITION_TRADE"
    return "PASSED_TRADE" if int(report.get("order_count") or 0) > 0 else "PASS_IDLE"


def _finalize(report: dict, status: str, reasons: list[str], *, controlled_gate: dict | None = None) -> dict:
    report["controlled_auto_live_status"] = status
    report["completed_at_utc"] = _utc_now()
    report["pass_fail_reasons"] = reasons
    if controlled_gate is not None:
        report["controlled_auto_live_gate"] = controlled_gate
    runtime = load_runtime_lock("auto-trading")
    report["final_runtime_status"] = str((runtime or {}).get("status") or "UNKNOWN").upper()
    return {key: value for key, value in report.items() if not key.startswith("_")}


async def _open_order_blocker(exchange: str, symbols: list[str]) -> str | None:
    if load_unresolved_live_order_logs_for_exchange(exchange):
        return "DB_UNRESOLVED_OPEN_ORDER_EXISTS"
    broker = get_live_broker(exchange)
    for symbol in symbols:
        market = f"KRW-{symbol}"
        try:
            response = await broker.list_open_orders(market)
        except Exception:
            return "EXCHANGE_OPEN_ORDER_AUDIT_FAILED"
        orders = response.get("orders", []) if isinstance(response, dict) else []
        if isinstance(orders, dict):
            orders = [orders]
        if orders:
            return "EXCHANGE_OPEN_ORDER_EXISTS"
    return None


async def _open_order_count(exchange: str, symbols: list[str]) -> int:
    total = len(load_unresolved_live_order_logs_for_exchange(exchange))
    broker = get_live_broker(exchange)
    for symbol in symbols:
        market = f"KRW-{symbol}"
        try:
            response = await broker.list_open_orders(market)
        except Exception:
            return total + 1
        orders = response.get("orders", []) if isinstance(response, dict) else []
        if isinstance(orders, dict):
            orders = [orders]
        total += len(orders)
    return total


def _runtime_guards_pass(exchange: str) -> bool:
    policy = load_global_bot_operation_policy()
    runtime = load_runtime_lock("auto-trading")
    live_config = LiveTradingConfig.for_exchange(exchange)
    return (
        not bool(policy.get("auto_trading_enabled"))
        and str((runtime or {}).get("status") or "").upper() == "STOPPED"
        and not is_emergency_stopped()
        and live_config.api_key_loaded
        and live_config.live_trading_enabled
    )


def _full_auto_live_disabled() -> bool:
    truthy = {"1", "true", "yes", "on", "enabled"}
    for key in ("FULL_AUTO_LIVE", "FULL_AUTO_LIVE_ENABLED", "AUTO_FULL_LIVE_ENABLED"):
        if str(os.getenv(key, "false")).strip().lower() in truthy:
            return False
    return True


def _signal_diagnostics_from_decisions(
    decisions: list[dict],
    *,
    current_epoch: dict | None = None,
    controlled_gate: dict | None = None,
) -> list[dict]:
    return [
        _diagnose_signal_decision(decision, current_epoch=current_epoch, controlled_gate=controlled_gate)
        for decision in decisions
    ]


def _diagnose_signal_decision(
    decision: dict,
    *,
    current_epoch: dict | None = None,
    controlled_gate: dict | None = None,
) -> dict:
    symbol = str(decision.get("symbol") or "").upper()
    strategy = str(decision.get("strategy") or "")
    signal_side = str(decision.get("signal") or "NONE").upper()
    expected_edge = _float(decision.get("expected_edge_rate"))
    min_edge = _float(decision.get("min_expected_edge_rate"), MIN_EXPECTED_EDGE_RATE)
    fee_rate = _float(decision.get("estimated_roundtrip_fee_rate"))
    spread_rate = _float(decision.get("estimated_spread_rate"))
    total_cost = _float(decision.get("estimated_round_trip_cost_rate"), fee_rate + spread_rate)
    expected_after_cost = expected_edge - total_cost
    risk_blockers: list[str] = []
    block_reasons: list[str] = []

    if symbol not in ALLOWED_SYMBOLS:
        block_reasons.append("SYMBOL_NOT_ALLOWED")
    if symbol in BLOCKED_SYMBOLS:
        block_reasons.append("SYMBOL_NOT_ALLOWED")
    if strategy in CONTROLLED_BLOCKED_STRATEGIES or strategy not in CONTROLLED_ALLOWED_STRATEGIES:
        block_reasons.append("STRATEGY_BLOCKED")
    if current_epoch is not None:
        if not current_epoch.get("current_epoch_sanity_passed", True):
            risk_blockers.append("ACCOUNTING_GATE_BLOCKED")
        if int(current_epoch.get("current_epoch_accounting_pending_count") or 0) > 0:
            risk_blockers.append("ACCOUNTING_GATE_BLOCKED")
        if int(current_epoch.get("current_epoch_accounting_failed_count") or 0) > 0:
            risk_blockers.append("ACCOUNTING_GATE_BLOCKED")
    if controlled_gate is not None and not controlled_gate.get("controlled_auto_live_allowed", True):
        risk_blockers.extend(
            str(item.get("code") or "RISK_BLOCKED")
            for item in (controlled_gate.get("controlled_auto_live_blockers") or [])
        )
    if risk_blockers:
        block_reasons.append("RISK_BLOCKED")
        if "ACCOUNTING_GATE_BLOCKED" in risk_blockers:
            block_reasons.append("ACCOUNTING_GATE_BLOCKED")
    block_reasons.extend(str(reason) for reason in (decision.get("block_reasons") or []) if reason)

    if strategy == "ma_cross" and signal_side != "BUY":
        block_reasons.append("NO_MA_CROSS_SIGNAL")
    if strategy == "smart_autonomous" and signal_side != "BUY":
        block_reasons.append("SMART_SCORE_TOO_LOW")
    if strategy == CONTROLLED_ENTRY_V2_STRATEGY and str(decision.get("signal_state") or "") != "TRADE_CANDIDATE" and signal_side != "BUY":
        block_reasons.append("NO_CONTROLLED_ENTRY_V2_SIGNAL")
    if strategy == CONTROLLED_ENTRY_V3_STRATEGY and str(decision.get("signal_state") or "") != "TRADE_CANDIDATE" and signal_side != "BUY":
        block_reasons.append("NO_CONTROLLED_ENTRY_V3_SIGNAL")
    if expected_edge < min_edge:
        block_reasons.append("EXPECTED_EDGE_BELOW_THRESHOLD")
    if expected_edge < total_cost:
        block_reasons.append("EXPECTED_EDGE_BELOW_FEE_COST")
    if spread_rate > max(0.003, expected_edge):
        block_reasons.append("SPREAD_TOO_WIDE")
    if signal_side == "BUY" and not block_reasons and not decision.get("edge_allowed"):
        block_reasons.append("UNKNOWN")

    block_reasons = list(dict.fromkeys(block_reasons))
    risk_blockers = list(dict.fromkeys(risk_blockers))
    recommended_threshold = max(min_edge, total_cost * 1.25)
    would_order_if_threshold_relaxed = (
        signal_side == "BUY"
        and not risk_blockers
        and symbol in ALLOWED_SYMBOLS
        and symbol not in BLOCKED_SYMBOLS
        and strategy in CONTROLLED_ALLOWED_STRATEGIES
        and expected_edge >= total_cost
        and expected_edge < min_edge
    )
    return {
        "symbol": symbol,
        "strategy_name": strategy,
        "timeframe": decision.get("timeframe"),
        "candle_count": int(decision.get("candle_count") or 0),
        "last_candle_at_utc": decision.get("last_candle_at_utc"),
        "signal_generated": signal_side in {"BUY", "SELL"},
        "signal_side": signal_side if signal_side in {"BUY", "SELL", "HOLD"} else "NONE",
        "signal_state": decision.get("signal_state") or ("TRADE_CANDIDATE" if decision.get("edge_allowed") else "NO_TRADE"),
        "signal_score": _float(decision.get("signal_score"), _float(decision.get("confidence_score"), expected_edge)),
        "confidence": _float(decision.get("confidence"), _float(decision.get("confidence_score"))),
        "expected_edge_rate": expected_edge,
        "expected_move_rate": _float(decision.get("expected_move_rate"), expected_edge),
        "min_expected_edge_rate": min_edge,
        "estimated_roundtrip_fee_rate": fee_rate,
        "estimated_spread_rate": spread_rate,
        "estimated_total_cost_rate": _float(decision.get("estimated_total_cost_rate"), total_cost),
        "expected_edge_after_cost": expected_after_cost,
        "blocked": bool(block_reasons) or not bool(decision.get("edge_allowed")),
        "block_reasons": block_reasons or ([] if decision.get("edge_allowed") else ["UNKNOWN"]),
        "would_order_if_threshold_relaxed": would_order_if_threshold_relaxed,
        "recommended_threshold": recommended_threshold,
        "current_threshold": min_edge,
        "risk_allowed": not risk_blockers,
        "risk_blockers": risk_blockers,
        "raw_reason": decision.get("reason"),
        "trade_candidate_reason": decision.get("trade_candidate_reason") or "",
        "recommended_next_action": decision.get("recommended_next_action") or ("CONTROLLED_RUN_REQUIRES_USER_APPROVAL" if decision.get("edge_allowed") else "NO_TRADE"),
        "breakout_level": decision.get("breakout_level"),
        "rebound_level": decision.get("rebound_level"),
        "current_price": decision.get("current_price"),
        "distance_to_breakout_rate": decision.get("distance_to_breakout_rate"),
        "distance_to_rebound_rate": decision.get("distance_to_rebound_rate"),
        "last_close": decision.get("last_close"),
        "previous_high": decision.get("previous_high"),
        "previous_low": decision.get("previous_low"),
        "short_ma": decision.get("short_ma"),
        "volume_ratio": decision.get("volume_ratio"),
        "volatility_rate": decision.get("volatility_rate"),
        "trigger_missing_reason": decision.get("trigger_missing_reason") or [],
    }


def _summarize_signal_diagnostics(diagnostics: list[dict]) -> dict:
    symbols = {str(item.get("symbol") or "") for item in diagnostics if item.get("symbol")}
    strategies = {str(item.get("strategy_name") or "") for item in diagnostics if item.get("strategy_name")}
    candidate_signals = [item for item in diagnostics if item.get("signal_generated")]
    blocked_signals = [item for item in diagnostics if item.get("blocked")]
    near_misses = [item for item in diagnostics if item.get("would_order_if_threshold_relaxed")]
    reason_counts: dict[str, int] = {}
    for item in diagnostics:
        for reason in item.get("block_reasons") or []:
            reason_counts[str(reason)] = reason_counts.get(str(reason), 0) + 1
    closest = None
    for item in diagnostics:
        gap = _float(item.get("current_threshold")) - _float(item.get("expected_edge_rate"))
        score = abs(gap)
        if closest is None or score < closest["_score"]:
            closest = {**item, "_gap": gap, "_score": score}
    summary = {
        "evaluated_symbol_count": len(symbols),
        "evaluated_strategy_count": len(strategies),
        "candidate_signal_count": len(candidate_signals),
        "blocked_signal_count": len(blocked_signals),
        "near_miss_signal_count": len(near_misses),
        "top_block_reasons": [
            {"code": code, "count": count}
            for code, count in sorted(reason_counts.items(), key=lambda pair: (-pair[1], pair[0]))[:10]
        ],
        "closest_candidate": None,
        "closest_candidate_expected_edge_rate": None,
        "closest_candidate_threshold": None,
        "closest_candidate_gap": None,
        "recommended_next_action": "WAIT_FOR_SIGNAL",
    }
    if closest is not None:
        summary.update(
            {
                "closest_candidate": {
                    "symbol": closest.get("symbol"),
                    "strategy_name": closest.get("strategy_name"),
                    "signal_side": closest.get("signal_side"),
                    "block_reasons": closest.get("block_reasons"),
                },
                "closest_candidate_expected_edge_rate": closest.get("expected_edge_rate"),
                "closest_candidate_threshold": closest.get("current_threshold"),
                "closest_candidate_gap": closest["_gap"],
            }
        )
    if any("RISK_BLOCKED" in (item.get("block_reasons") or []) or "ACCOUNTING_GATE_BLOCKED" in (item.get("block_reasons") or []) for item in diagnostics):
        summary["recommended_next_action"] = "RISK_BLOCKER_FIX_REQUIRED"
    elif near_misses:
        summary["recommended_next_action"] = "LOWER_THRESHOLD_SLIGHTLY"
    elif diagnostics and all(_float(item.get("expected_edge_after_cost")) < 0 for item in diagnostics):
        summary["recommended_next_action"] = "COST_TOO_HIGH_FOR_CURRENT_TIMEFRAME"
    elif not candidate_signals:
        summary["recommended_next_action"] = "STRATEGY_TOO_INACTIVE"
    else:
        summary["recommended_next_action"] = "KEEP_THRESHOLDS"
    return summary


def _threshold_adjustment_report(diagnostics: list[dict]) -> dict:
    current = MIN_EXPECTED_EDGE_RATE
    costs = [_float(item.get("estimated_roundtrip_fee_rate")) + _float(item.get("estimated_spread_rate")) for item in diagnostics]
    observed_cost = max(costs) if costs else 0.0
    return {
        "current_min_expected_edge_rate": current,
        "observed_roundtrip_cost_rate": observed_cost,
        "safe_min_expected_edge_rate_suggestion": max(current, observed_cost * 1.25),
        "aggressive_min_expected_edge_rate_suggestion": max(observed_cost * 1.05, observed_cost, 0.005),
        "risk_note": "Do not lower live thresholds below observed round-trip fee plus spread cost; for 6000 KRW probes this cost has been about 0.5%.",
        "operating_threshold_changed": False,
    }


def _recommended_next_plan(summary: dict, threshold_analysis: dict) -> dict:
    action = str(summary.get("recommended_next_action") or "WAIT_FOR_SIGNAL")
    if action == "LOWER_THRESHOLD_SLIGHTLY":
        plan = "B"
        reason = "Near-miss BUY signal exists and expected edge remains above observed cost."
    elif action == "STRATEGY_TOO_INACTIVE":
        plan = "C"
        reason = "No candidate BUY signal was generated by the allowed strategies."
    elif action == "COST_TOO_HIGH_FOR_CURRENT_TIMEFRAME":
        plan = "D"
        reason = "Expected edge is below fee and spread cost."
    elif action == "RISK_BLOCKER_FIX_REQUIRED":
        plan = "D"
        reason = "Risk or accounting gate blocks controlled execution."
    else:
        plan = "A"
        reason = "Thresholds are appropriate; wait for a natural signal."
    return {
        "plan": plan,
        "reason": reason,
        "recommended_next_action": action,
        "threshold_change_required": False,
        "suggested_threshold": threshold_analysis.get("safe_min_expected_edge_rate_suggestion"),
    }


def _cost_components(quote: dict, *, exchange: str = "bithumb") -> dict:
    bid = _float(quote.get("best_bid"))
    ask = _float(quote.get("best_ask"))
    mid = (bid + ask) / 2 if bid > 0 and ask > 0 else ask
    spread_rate = ((ask - bid) / mid) if mid > 0 else 0.0
    fee_rate = max(2 * LiveTradingConfig.for_exchange(exchange).fee_rate, OBSERVED_CONTROLLED_ROUNDTRIP_COST_RATE_FLOOR)
    return {
        "estimated_roundtrip_fee_rate": fee_rate,
        "estimated_spread_rate": spread_rate,
        "estimated_round_trip_cost_rate": spread_rate + fee_rate,
    }


def _round_trip_cost_rate(quote: dict) -> float:
    return _cost_components(quote)["estimated_round_trip_cost_rate"]


def _ledger_rows_for_order_uuids(order_uuids: list[str]) -> list[dict]:
    if not order_uuids:
        return []
    with get_connection() as conn:
        placeholders = ",".join("?" for _ in order_uuids)
        return [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT *
                FROM exchange_fills_ledger
                WHERE exchange_order_uuid IN ({placeholders})
                ORDER BY executed_at_utc, id
                """,
                order_uuids,
            ).fetchall()
        ]


def _decode_json_fields(row: dict) -> dict:
    for key in ("order_preview_payload", "exchange_request_payload_masked", "exchange_response_payload"):
        value = row.get(key)
        if isinstance(value, str):
            try:
                row[key] = json.loads(value or "{}")
            except json.JSONDecodeError:
                row[key] = {}
    return row


def _safe_json_loads(raw: Any, default: Any) -> Any:
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except Exception:
        return default


def _normalize_candle(candle: dict) -> dict:
    candle_time = str(candle.get("candle_time_utc") or candle.get("candle_date_time_utc") or "")
    if candle_time and not candle_time.endswith("Z") and "+" not in candle_time[-6:]:
        candle_time = f"{candle_time}Z"
    return {
        **candle,
        "candle_time_utc": candle_time,
        "opening_price": candle.get("opening_price") or candle.get("open") or 0,
        "high_price": candle.get("high_price") or candle.get("high") or 0,
        "low_price": candle.get("low_price") or candle.get("low") or 0,
        "trade_price": candle.get("trade_price") or candle.get("close") or 0,
        "candle_acc_trade_volume": candle.get("candle_acc_trade_volume") or candle.get("volume") or 0,
    }


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
