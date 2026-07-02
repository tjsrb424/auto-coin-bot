from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.accounting_epoch import build_current_epoch_diagnostics, build_open_order_audit, build_smoke_test_preflight
from app.controlled_auto_live import (
    CONTROLLED_ENTRY_V3_STRATEGY,
    CONTROLLED_POSITION_LOOP_CONFIRMATION_PHRASE,
    PROTECTED_FULL_AUTO_MODE,
    PROTECTED_RUNTIME_LOCK_ID,
    build_protected_session_baseline,
    controlled_auto_live_gate,
    controlled_auto_live_job_status,
    protected_position_scope_status,
    start_controlled_position_loop_job,
    stop_controlled_auto_live_job,
)
from app.database import (
    acquire_runtime_lock,
    get_connection,
    load_protected_auto_notifications,
    load_global_bot_operation_policy,
    load_runtime_lock,
    release_runtime_lock,
)
from app.live_broker import LiveTradingConfig, is_emergency_stopped
from app.live_recovery import log_recovery_event
from app.live_smoke_test import _current_equity
from app.protected_notifications import latest_protected_auto_notification, notify_protected_auto_event

logger = logging.getLogger("uvicorn.error")
_WORKER_INSTANCE_ID = os.getenv("RUNTIME_INSTANCE_ID", f"{socket.gethostname()}-{uuid.uuid4().hex[:12]}")

PROTECTED_AUTO_RUNTIME_ID = "protected-full-auto-live-v1"
PROTECTED_ALLOWED_SYMBOLS = ("BTC", "ETH")
PROTECTED_MAX_NOTIONAL_KRW = 6000.0
PROTECTED_MAX_OPEN_POSITIONS = 1
PROTECTED_SESSION_LOSS_LIMIT_KRW = 1000.0
PROTECTED_SCAN_INTERVAL_SECONDS = 60
PROTECTED_LOCK_TTL_SECONDS = 180
PROTECTED_STALE_AFTER_SECONDS = 180
PROTECTED_SCAN_TIMEOUT_SECONDS = float(os.getenv("PROTECTED_AUTO_SCAN_TIMEOUT_SECONDS", "25"))
PROTECTED_EXCHANGE_TIMEOUT_SECONDS = float(os.getenv("PROTECTED_AUTO_EXCHANGE_TIMEOUT_SECONDS", "8"))

_WORKER_TICK_LOCK = threading.Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _plus_seconds(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _kst_date_text() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=9)).date().isoformat()


def _parse_utc(value: str | None) -> datetime | None:
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


def _json_loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return fallback


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _instance_id() -> str:
    return _WORKER_INSTANCE_ID


def _hostname() -> str:
    return socket.gethostname()


def _row_to_state(row: dict | None) -> dict:
    if not row:
        return {
            "runtime_id": PROTECTED_AUTO_RUNTIME_ID,
            "worker_status": "STOPPED",
            "session_status": "STOPPED",
            "protected_session_id": None,
            "exchange": "bithumb",
            "symbols": list(PROTECTED_ALLOWED_SYMBOLS),
            "amount_krw": PROTECTED_MAX_NOTIONAL_KRW,
            "scan_interval_seconds": PROTECTED_SCAN_INTERVAL_SECONDS,
            "max_holding_minutes": 10,
            "max_position_trades": PROTECTED_MAX_OPEN_POSITIONS,
            "session_loss_limit_krw": PROTECTED_SESSION_LOSS_LIMIT_KRW,
            "trade_count": 0,
            "protected_open_position_count": 0,
            "legacy_open_position_count": 0,
            "protected_strategy_pnl": 0.0,
            "account_session_pnl_delta": 0.0,
            "last_scan_error": "",
            "consecutive_scan_failures": 0,
            "worker_loop_duration_ms": 0,
            "last_scan_result": {},
            "latest_report": {},
            "baseline": {},
        }
    state = dict(row)
    state["symbols"] = _json_loads(state.pop("symbols_json", "[]"), list(PROTECTED_ALLOWED_SYMBOLS))
    state["last_scan_result"] = _json_loads(state.pop("last_scan_result_json", "{}"), {})
    state["latest_report"] = _json_loads(state.pop("latest_report_json", "{}"), {})
    state["baseline"] = _json_loads(state.pop("baseline_json", "{}"), {})
    return state


def load_protected_auto_state() -> dict:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM protected_auto_runtime WHERE runtime_id = ?",
            (PROTECTED_AUTO_RUNTIME_ID,),
        ).fetchone()
    return _row_to_state(dict(row) if row else None)


def _upsert_state(values: dict) -> dict:
    current = load_protected_auto_state()
    merged = {**current, **values}
    symbols = [str(symbol).upper() for symbol in merged.get("symbols") or PROTECTED_ALLOWED_SYMBOLS]
    symbols = [symbol for symbol in symbols if symbol in PROTECTED_ALLOWED_SYMBOLS] or list(PROTECTED_ALLOWED_SYMBOLS)
    now = _utc_now()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO protected_auto_runtime (
                runtime_id, worker_status, session_status, protected_session_id,
                exchange, symbols_json, amount_krw, scan_interval_seconds,
                max_holding_minutes, max_position_trades, session_loss_limit_krw,
                started_at_utc, stopped_at_utc, last_heartbeat_at_utc, last_tick_at_utc,
                last_tick_started_at_utc, last_tick_finished_at_utc, next_tick_at_utc,
                lock_expires_at_utc, last_scan_error, consecutive_scan_failures,
                worker_loop_duration_ms, last_scan_result_json,
                latest_report_json, baseline_json, stop_reason, trade_count,
                protected_open_position_count, legacy_open_position_count,
                protected_strategy_pnl, account_session_pnl_delta,
                startup_recovery_action, startup_recovery_reason,
                created_at_utc, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(runtime_id) DO UPDATE SET
                worker_status = excluded.worker_status,
                session_status = excluded.session_status,
                protected_session_id = excluded.protected_session_id,
                exchange = excluded.exchange,
                symbols_json = excluded.symbols_json,
                amount_krw = excluded.amount_krw,
                scan_interval_seconds = excluded.scan_interval_seconds,
                max_holding_minutes = excluded.max_holding_minutes,
                max_position_trades = excluded.max_position_trades,
                session_loss_limit_krw = excluded.session_loss_limit_krw,
                started_at_utc = excluded.started_at_utc,
                stopped_at_utc = excluded.stopped_at_utc,
                last_heartbeat_at_utc = excluded.last_heartbeat_at_utc,
                last_tick_at_utc = excluded.last_tick_at_utc,
                last_tick_started_at_utc = excluded.last_tick_started_at_utc,
                last_tick_finished_at_utc = excluded.last_tick_finished_at_utc,
                next_tick_at_utc = excluded.next_tick_at_utc,
                lock_expires_at_utc = excluded.lock_expires_at_utc,
                last_scan_error = excluded.last_scan_error,
                consecutive_scan_failures = excluded.consecutive_scan_failures,
                worker_loop_duration_ms = excluded.worker_loop_duration_ms,
                last_scan_result_json = excluded.last_scan_result_json,
                latest_report_json = excluded.latest_report_json,
                baseline_json = excluded.baseline_json,
                stop_reason = excluded.stop_reason,
                trade_count = excluded.trade_count,
                protected_open_position_count = excluded.protected_open_position_count,
                legacy_open_position_count = excluded.legacy_open_position_count,
                protected_strategy_pnl = excluded.protected_strategy_pnl,
                account_session_pnl_delta = excluded.account_session_pnl_delta,
                startup_recovery_action = excluded.startup_recovery_action,
                startup_recovery_reason = excluded.startup_recovery_reason,
                updated_at_utc = excluded.updated_at_utc
            """,
            (
                PROTECTED_AUTO_RUNTIME_ID,
                str(merged.get("worker_status") or "STOPPED"),
                str(merged.get("session_status") or "STOPPED"),
                merged.get("protected_session_id"),
                str(merged.get("exchange") or "bithumb"),
                _json_dumps(symbols),
                min(float(merged.get("amount_krw") or PROTECTED_MAX_NOTIONAL_KRW), PROTECTED_MAX_NOTIONAL_KRW),
                int(merged.get("scan_interval_seconds") or PROTECTED_SCAN_INTERVAL_SECONDS),
                int(merged.get("max_holding_minutes") or 10),
                min(int(merged.get("max_position_trades") or PROTECTED_MAX_OPEN_POSITIONS), PROTECTED_MAX_OPEN_POSITIONS),
                float(merged.get("session_loss_limit_krw") or PROTECTED_SESSION_LOSS_LIMIT_KRW),
                merged.get("started_at_utc"),
                merged.get("stopped_at_utc"),
                merged.get("last_heartbeat_at_utc"),
                merged.get("last_tick_at_utc"),
                merged.get("last_tick_started_at_utc"),
                merged.get("last_tick_finished_at_utc"),
                merged.get("next_tick_at_utc"),
                merged.get("lock_expires_at_utc"),
                str(merged.get("last_scan_error") or ""),
                int(merged.get("consecutive_scan_failures") or 0),
                int(merged.get("worker_loop_duration_ms") or 0),
                _json_dumps(merged.get("last_scan_result") or {}),
                _json_dumps(merged.get("latest_report") or {}),
                _json_dumps(merged.get("baseline") or {}),
                str(merged.get("stop_reason") or ""),
                int(merged.get("trade_count") or 0),
                int(merged.get("protected_open_position_count") or 0),
                int(merged.get("legacy_open_position_count") or 0),
                float(merged.get("protected_strategy_pnl") or 0.0),
                float(merged.get("account_session_pnl_delta") or 0.0),
                str(merged.get("startup_recovery_action") or ""),
                str(merged.get("startup_recovery_reason") or ""),
                merged.get("created_at_utc") or now,
                now,
            ),
        )
    return load_protected_auto_state()


def _is_stale(state: dict) -> bool:
    if str(state.get("worker_status") or "").upper() != "RUNNING":
        return False
    heartbeat = _parse_utc(state.get("last_heartbeat_at_utc"))
    if heartbeat is None:
        return True
    return (datetime.now(timezone.utc) - heartbeat).total_seconds() > PROTECTED_STALE_AFTER_SECONDS


def _active_protected_job() -> dict | None:
    status = controlled_auto_live_job_status()
    active = status.get("active_job") or {}
    if str(active.get("mode") or "") == PROTECTED_FULL_AUTO_MODE and str(active.get("status") or "").upper() in {"STARTING", "RUNNING"}:
        return active
    return None


def _latest_protected_job() -> dict | None:
    status = controlled_auto_live_job_status()
    jobs = [job for job in status.get("jobs") or [] if str((job or {}).get("mode") or "") == PROTECTED_FULL_AUTO_MODE]
    jobs.sort(key=lambda item: str(item.get("started_at_utc") or ""), reverse=True)
    return jobs[0] if jobs else None


def _event_id(*parts: Any) -> str:
    return ":".join(str(part).replace(":", "-") for part in parts if part not in (None, ""))


def _notify_protected_event(
    event_type: str,
    *,
    state: dict | None = None,
    severity: str = "INFO",
    message: str = "",
    payload: dict | None = None,
    controlled_run_id: str | None = None,
    event_id: str | None = None,
) -> dict | None:
    state = state or load_protected_auto_state()
    event_payload = {
        "runtime_id": PROTECTED_AUTO_RUNTIME_ID,
        "worker_status": state.get("worker_status"),
        "session_status": state.get("session_status"),
        "status": state.get("session_status") or state.get("worker_status"),
        "protected_session_id": state.get("protected_session_id"),
        "last_heartbeat_at_utc": state.get("last_heartbeat_at_utc"),
        "last_tick_at_utc": state.get("last_tick_at_utc"),
        "next_tick_at_utc": state.get("next_tick_at_utc"),
        "lock_expires_at_utc": state.get("lock_expires_at_utc"),
        "trade_count": state.get("trade_count"),
        "protected_strategy_pnl": state.get("protected_strategy_pnl"),
        "account_session_pnl_delta": state.get("account_session_pnl_delta"),
        "stop_reason": state.get("stop_reason"),
        **(payload or {}),
    }
    try:
        return notify_protected_auto_event(
            event_type,
            severity=severity,
            exchange=str(state.get("exchange") or "bithumb"),
            protected_session_id=str(state.get("protected_session_id") or "") or None,
            controlled_run_id=controlled_run_id,
            message=message,
            payload=event_payload,
            event_id=event_id,
        )
    except Exception as exc:
        logger.warning("[protected-auto] notification failed event=%s error=%s", event_type, exc)
        return None


def _mapped_stop_events(reason: str) -> list[str]:
    upper = str(reason or "").upper()
    events: list[str] = []
    if "PROTECTED_HEARTBEAT_STALE" in upper or "STALE" in upper:
        events.append("PROTECTED_AUTO_STALE")
    if "SESSION_LOSS_LIMIT" in upper or "PROTECTED_SESSION_LOSS_LIMIT_REACHED" in upper:
        events.append("SESSION_LOSS_LIMIT_REACHED")
    if "ACCOUNTING" in upper:
        events.append("ACCOUNTING_ERROR")
    if "MISSING_LEDGER" in upper or "MISSING LEDGER" in upper:
        events.append("MISSING_LEDGER_FILL")
    if "DUPLICATE_FILL" in upper or "DUPLICATE FILL" in upper:
        events.append("DUPLICATE_FILL")
    if "FEE_DIFF" in upper or "FEE DIFF" in upper:
        events.append("FEE_DIFF_ERROR")
    if "EQUITY_DIFF" in upper or "EQUITY DIFF" in upper:
        events.append("EQUITY_DIFF_ERROR")
    if "OPEN_ORDER" in upper or "OPEN ORDER" in upper:
        events.append("OPEN_ORDER_STALE")
    return list(dict.fromkeys(events))


def _notify_stop_events(state: dict, reason: str, worker_status: str, failed: bool) -> None:
    severity = "ERROR" if failed or worker_status == "FAILED" else "WARNING"
    for event_type in _mapped_stop_events(reason):
        _notify_protected_event(
            event_type,
            state=state,
            severity=severity,
            message=f"Protected auto stop condition observed: {reason}",
            payload={"reason": reason, "worker_status": worker_status},
            event_id=_event_id(state.get("protected_session_id"), event_type, worker_status, reason),
        )
    _notify_protected_event(
        "PROTECTED_AUTO_STOPPED",
        state=state,
        severity=severity,
        message=f"Protected auto daemon stopped: {reason}",
        payload={"reason": reason, "worker_status": worker_status},
        event_id=_event_id(state.get("protected_session_id"), "PROTECTED_AUTO_STOPPED", worker_status, reason),
    )


def _notify_report_events(state: dict, report: dict, status: str, stop_reasons: list[str]) -> None:
    run_id = str(report.get("controlled_run_id") or report.get("loop_run_id") or report.get("position_run_id") or "")
    trade_payload = {
        "controlled_run_id": run_id,
        "controlled_auto_live_status": status,
        "trade_count": report.get("trade_count"),
        "buy_filled_count": report.get("buy_filled_count"),
        "sell_filled_count": report.get("sell_filled_count"),
        "protected_strategy_pnl": report.get("protected_strategy_total_pnl"),
        "account_session_pnl_delta": report.get("account_session_pnl_delta"),
        "session_loss_remaining": report.get("session_loss_limit_remaining"),
    }
    if int(report.get("buy_filled_count") or 0) > 0:
        _notify_protected_event(
            "TRADE_OPENED",
            state=state,
            severity="INFO",
            message="Protected controlled_entry_v3 trade opened.",
            payload=trade_payload,
            controlled_run_id=run_id or None,
            event_id=_event_id(state.get("protected_session_id"), run_id, "TRADE_OPENED"),
        )
    if int(report.get("sell_filled_count") or 0) > 0:
        _notify_protected_event(
            "TRADE_CLOSED",
            state=state,
            severity="INFO",
            message="Protected controlled_entry_v3 trade closed.",
            payload=trade_payload,
            controlled_run_id=run_id or None,
            event_id=_event_id(state.get("protected_session_id"), run_id, "TRADE_CLOSED"),
        )
    for reason in stop_reasons:
        for event_type in _mapped_stop_events(reason):
            _notify_protected_event(
                event_type,
                state=state,
                severity="ERROR",
                message=f"Protected controlled_entry_v3 report stop condition: {reason}",
                payload={**trade_payload, "reason": reason},
                controlled_run_id=run_id or None,
                event_id=_event_id(state.get("protected_session_id"), run_id, event_type, reason),
            )


def _notify_daily_summary_if_due(state: dict) -> None:
    _notify_protected_event(
        "DAILY_SUMMARY",
        state=state,
        severity="INFO",
        message="Protected auto daily summary.",
        payload={
            "summary_date_kst": _kst_date_text(),
            "trade_count": state.get("trade_count"),
            "protected_open_position_count": state.get("protected_open_position_count"),
            "legacy_open_position_count": state.get("legacy_open_position_count"),
            "protected_strategy_pnl": state.get("protected_strategy_pnl"),
            "account_session_pnl_delta": state.get("account_session_pnl_delta"),
            "session_loss_remaining": _session_loss_remaining(state),
        },
        event_id=_event_id(state.get("protected_session_id"), "DAILY_SUMMARY", _kst_date_text()),
    )


def _session_loss_remaining(state: dict) -> Any:
    active = _active_protected_job() or {}
    active_report = active.get("report") or {}
    latest_report = state.get("latest_report") or {}
    baseline = state.get("baseline") or {}
    return (
        active_report.get("session_loss_limit_remaining")
        if active_report.get("session_loss_limit_remaining") is not None
        else latest_report.get("session_loss_limit_remaining")
        if latest_report.get("session_loss_limit_remaining") is not None
        else baseline.get("session_loss_limit_remaining")
    )


def _latest_signal_summary(signals: dict) -> dict:
    for timeframe in ("3m", "5m", "15m"):
        signal = signals.get(timeframe)
        if isinstance(signal, dict) and signal:
            return {"timeframe": timeframe, **signal}
    return {}


def _sync_latest_report_into_state(state: dict) -> dict:
    latest = _latest_protected_job()
    if not latest or str(latest.get("status") or "").upper() in {"STARTING", "RUNNING"}:
        return state
    report = latest.get("report") or {}
    if not report:
        return state
    if state.get("latest_report", {}).get("controlled_run_id") == report.get("controlled_run_id"):
        return state
    stop_reasons = [str(item) for item in report.get("pass_fail_reasons") or []]
    status = str(report.get("controlled_auto_live_status") or latest.get("status") or "").upper()
    updates = {
        "latest_report": report,
        "trade_count": int(state.get("trade_count") or 0) + int(report.get("trade_count") or 0),
        "protected_strategy_pnl": float(state.get("protected_strategy_pnl") or 0.0) + float(report.get("protected_strategy_total_pnl") or 0.0),
        "account_session_pnl_delta": float(report.get("account_session_pnl_delta") or state.get("account_session_pnl_delta") or 0.0),
        "last_scan_result": {
            "result": status,
            "controlled_run_id": report.get("controlled_run_id"),
            "trade_count": report.get("trade_count", 0),
            "scan_timeframes": report.get("scan_timeframes") or [],
            "latest_signal_by_timeframe": report.get("latest_signal_by_timeframe") or {},
            "trade_candidate_count_by_timeframe": report.get("trade_candidate_count_by_timeframe") or {},
            "protected_strategy_pnl": report.get("protected_strategy_total_pnl", 0.0),
            "legacy_holding_valuation_delta": report.get("legacy_holding_valuation_delta", 0.0),
            "stop_reasons": stop_reasons,
            "completed_at_utc": report.get("completed_at_utc"),
        },
    }
    state = _upsert_state(updates)
    _notify_report_events(state, report, status, stop_reasons)
    hard_status = status in {"FAILED", "STOPPED", "ABORTED"}
    hard_reason = next((reason for reason in stop_reasons if reason), "")
    if hard_status and hard_reason:
        return protected_auto_safe_stop(hard_reason, failed=status == "FAILED")
    return state


def _position_scope(state: dict) -> dict:
    return protected_position_scope_status(
        exchange=str(state.get("exchange") or "bithumb"),
        protected_session_id=str(state.get("protected_session_id") or "") or None,
        protected_session_started_at_utc=str(state.get("started_at_utc") or "") or None,
    )


def _compact_position_scope(scope: dict) -> dict:
    return {
        key: value
        for key, value in (scope or {}).items()
        if key != "open_position_classifications"
    }


def _compact_notification(event: dict | None) -> dict | None:
    if not event:
        return None
    payload = event.get("payload") or {}
    compact_payload = {
        key: payload.get(key)
        for key in (
            "status",
            "worker_status",
            "session_status",
            "protected_session_id",
            "controlled_run_id",
            "trade_count",
            "protected_strategy_pnl",
            "session_loss_remaining",
            "reason",
            "message",
        )
        if payload.get(key) is not None
    }
    return {
        "id": event.get("id"),
        "event_id": event.get("event_id"),
        "event_type": event.get("event_type"),
        "severity": event.get("severity"),
        "exchange": event.get("exchange"),
        "protected_session_id": event.get("protected_session_id"),
        "controlled_run_id": event.get("controlled_run_id"),
        "message": event.get("message"),
        "channel": event.get("channel"),
        "webhook_configured": event.get("webhook_configured"),
        "delivery_status": event.get("delivery_status"),
        "delivery_error": event.get("delivery_error"),
        "sent_at_utc": event.get("sent_at_utc"),
        "created_at_utc": event.get("created_at_utc"),
        "payload": compact_payload,
    }


def _startup_recovery_status(state: dict | None = None, lock: dict | None = None) -> dict:
    """Return a startup-safe status summary without exchange/accounting scans."""
    current = dict(state or load_protected_auto_state() or {})
    runtime_lock = dict(lock or load_runtime_lock(PROTECTED_RUNTIME_LOCK_ID) or {})
    stale = _is_stale(current)
    stale_lock = (
        str(runtime_lock.get("status") or "").upper() == "RUNNING"
        and str(runtime_lock.get("expires_at") or "") <= _utc_now()
    )
    return {
        "runtime_id": current.get("runtime_id") or PROTECTED_AUTO_RUNTIME_ID,
        "protected_session_id": current.get("protected_session_id"),
        "protected_auto_runtime_status": str(current.get("session_status") or "STOPPED").upper(),
        "protected_worker_status": "STALE" if stale else str(current.get("worker_status") or "STOPPED").upper(),
        "protected_session_status": str(current.get("session_status") or "STOPPED").upper(),
        "protected_runtime_lock_status": "STALE" if stale_lock else str(runtime_lock.get("status") or "STOPPED").upper(),
        "protected_last_heartbeat_at_utc": current.get("last_heartbeat_at_utc"),
        "protected_last_tick_at_utc": current.get("last_tick_at_utc"),
        "protected_next_scan_at_utc": current.get("next_tick_at_utc"),
        "protected_lock_expires_at_utc": current.get("lock_expires_at_utc") or runtime_lock.get("expires_at"),
        "last_heartbeat_at_utc": current.get("last_heartbeat_at_utc"),
        "last_tick_at_utc": current.get("last_tick_at_utc"),
        "next_tick_at_utc": current.get("next_tick_at_utc"),
        "lock_expires_at_utc": current.get("lock_expires_at_utc") or runtime_lock.get("expires_at"),
        "trade_count": int(current.get("trade_count") or 0),
        "stop_reason": current.get("stop_reason"),
        "startup_recovery_action": current.get("startup_recovery_action"),
        "startup_recovery_reason": current.get("startup_recovery_reason"),
        "stale": stale,
        "stale_lock": stale or stale_lock,
    }


def protected_auto_status() -> dict:
    state = _sync_latest_report_into_state(load_protected_auto_state())
    session_status = str(state.get("session_status") or "STOPPED").upper()
    worker_status = str(state.get("worker_status") or "STOPPED").upper()
    if session_status in {"STOPPED", "FAILED"} and worker_status in {"STOPPED", "FAILED"}:
        return {
            **state,
            **_startup_recovery_status(state=state),
            "protected_open_position_count": int(state.get("protected_open_position_count") or 0),
            "legacy_open_position_count": int(state.get("legacy_open_position_count") or 0),
            "protected_strategy_pnl": float(state.get("protected_strategy_pnl") or 0.0),
            "account_session_pnl_delta": float(state.get("account_session_pnl_delta") or 0.0),
            "session_loss_remaining": _session_loss_remaining(state),
            "last_scan_result": state.get("last_scan_result") or {},
            "latest_signal": {},
            "latest_signal_by_timeframe": {},
            "trade_candidate_count_by_timeframe": {},
            "active_controlled_job": None,
            "last_alert": None,
            "recent_notifications": [],
            "allowed_symbols": list(PROTECTED_ALLOWED_SYMBOLS),
            "allowed_strategy": CONTROLLED_ENTRY_V3_STRATEGY,
            "max_notional_krw": PROTECTED_MAX_NOTIONAL_KRW,
            "max_open_positions": PROTECTED_MAX_OPEN_POSITIONS,
            "session_loss_limit_krw": PROTECTED_SESSION_LOSS_LIMIT_KRW,
        }
    stale = _is_stale(state)
    active = _active_protected_job()
    active_signal_by_timeframe = (active or {}).get("latest_signal_by_timeframe") or {}
    active_candidate_counts = (active or {}).get("trade_candidate_count_by_timeframe") or {}
    last_scan = state.get("last_scan_result") or {}
    latest_report = state.get("latest_report") or {}
    scope = _compact_position_scope(_position_scope(state))
    lock = load_runtime_lock(PROTECTED_RUNTIME_LOCK_ID) or {}
    recent_notifications = [_compact_notification(item) for item in load_protected_auto_notifications(5)]
    recent_notifications = [item for item in recent_notifications if item]
    signal_by_timeframe = active_signal_by_timeframe or last_scan.get("latest_signal_by_timeframe") or latest_report.get("latest_signal_by_timeframe") or {}
    stale_lock = (
        str(lock.get("status") or "").upper() == "RUNNING"
        and str(lock.get("expires_at") or "") <= _utc_now()
    )
    derived_worker = "STALE" if stale else str(state.get("worker_status") or "STOPPED").upper()
    derived_lock_status = "STALE" if stale_lock else str(lock.get("status") or "STOPPED").upper()
    return {
        **state,
        **scope,
        "protected_auto_runtime_status": str(state.get("session_status") or "STOPPED").upper(),
        "protected_worker_status": derived_worker,
        "protected_session_status": str(state.get("session_status") or "STOPPED").upper(),
        "protected_runtime_lock_status": derived_lock_status,
        "protected_runtime_lock": lock,
        "protected_last_heartbeat_at_utc": state.get("last_heartbeat_at_utc"),
        "protected_last_tick_at_utc": state.get("last_tick_at_utc"),
        "protected_last_tick_started_at_utc": state.get("last_tick_started_at_utc"),
        "protected_last_tick_finished_at_utc": state.get("last_tick_finished_at_utc"),
        "protected_next_scan_at_utc": state.get("next_tick_at_utc"),
        "protected_lock_expires_at_utc": state.get("lock_expires_at_utc") or lock.get("expires_at"),
        "protected_last_scan_error": state.get("last_scan_error") or "",
        "protected_consecutive_scan_failures": int(state.get("consecutive_scan_failures") or 0),
        "protected_worker_loop_duration_ms": int(state.get("worker_loop_duration_ms") or 0),
        "stale": stale,
        "stale_lock": stale or stale_lock,
        "active_controlled_job": active,
        "latest_signal": _latest_signal_summary(signal_by_timeframe),
        "latest_signal_by_timeframe": signal_by_timeframe,
        "trade_candidate_count_by_timeframe": active_candidate_counts or last_scan.get("trade_candidate_count_by_timeframe") or latest_report.get("trade_candidate_count_by_timeframe") or {},
        "session_loss_remaining": _session_loss_remaining(state),
        "last_alert": recent_notifications[0] if recent_notifications else _compact_notification(latest_protected_auto_notification()),
        "recent_notifications": recent_notifications,
        "allowed_symbols": list(PROTECTED_ALLOWED_SYMBOLS),
        "allowed_strategy": CONTROLLED_ENTRY_V3_STRATEGY,
        "max_notional_krw": PROTECTED_MAX_NOTIONAL_KRW,
        "max_open_positions": PROTECTED_MAX_OPEN_POSITIONS,
        "session_loss_limit_krw": PROTECTED_SESSION_LOSS_LIMIT_KRW,
    }


def _acquire_protected_lock() -> tuple[bool, dict | None]:
    return acquire_runtime_lock(
        lock_id=PROTECTED_RUNTIME_LOCK_ID,
        instance_id=_instance_id(),
        hostname=_hostname(),
        app_env=os.getenv("APP_ENV", "development"),
        runtime_owner="protected-full-auto-live-v1-daemon",
        ttl_seconds=PROTECTED_LOCK_TTL_SECONDS,
    )


def _release_protected_lock(status: str = "STOPPED") -> None:
    lock = load_runtime_lock(PROTECTED_RUNTIME_LOCK_ID) or {}
    release_runtime_lock(
        lock_id=PROTECTED_RUNTIME_LOCK_ID,
        instance_id=str(lock.get("instance_id") or _instance_id()),
        status=status,
    )


def protected_auto_safe_stop(reason: str, *, failed: bool = False) -> dict:
    active = _active_protected_job()
    if active:
        try:
            asyncio.run(stop_controlled_auto_live_job(str(active["controlled_run_id"])))
        except RuntimeError:
            # Called from an event loop; the active job will also observe DB STOP on the next scheduler tick.
            pass
    worker_status = "FAILED" if failed else "STOPPED"
    now = _utc_now()
    _release_protected_lock(worker_status)
    state = _upsert_state(
        {
            "worker_status": worker_status,
            "session_status": worker_status,
            "stopped_at_utc": now,
            "next_tick_at_utc": None,
            "lock_expires_at_utc": now,
            "stop_reason": reason,
            "last_scan_result": {"result": worker_status, "reason": reason, "at_utc": now},
        }
    )
    log_recovery_event(
        event_type="PROTECTED_AUTO_SAFE_STOP",
        exchange=str(state.get("exchange") or "bithumb"),
        market="KRW-BTC",
        severity="ERROR" if failed else "WARNING",
        message=f"Protected auto daemon safe-stopped: {reason}",
        payload={"reason": reason, "worker_status": worker_status},
    )
    _notify_stop_events(state, reason, worker_status, failed)
    return _startup_recovery_status(state=state)


async def protected_auto_safe_stop_async(reason: str, *, failed: bool = False) -> dict:
    active = _active_protected_job()
    if active:
        await stop_controlled_auto_live_job(str(active["controlled_run_id"]))
    worker_status = "FAILED" if failed else "STOPPED"
    now = _utc_now()
    _release_protected_lock(worker_status)
    state = _upsert_state(
        {
            "worker_status": worker_status,
            "session_status": worker_status,
            "stopped_at_utc": now,
            "next_tick_at_utc": None,
            "lock_expires_at_utc": now,
            "stop_reason": reason,
            "last_scan_result": {"result": worker_status, "reason": reason, "at_utc": now},
        }
    )
    log_recovery_event(
        event_type="PROTECTED_AUTO_SAFE_STOP",
        exchange=str(state.get("exchange") or "bithumb"),
        market="KRW-BTC",
        severity="ERROR" if failed else "WARNING",
        message=f"Protected auto daemon safe-stopped: {reason}",
        payload={"reason": reason, "worker_status": worker_status},
    )
    _notify_stop_events(state, reason, worker_status, failed)
    return _startup_recovery_status(state=state)


def _hard_stop_reasons(exchange: str, current_epoch: dict | None = None) -> list[str]:
    reasons: list[str] = []
    policy = load_global_bot_operation_policy()
    live_config = LiveTradingConfig.for_exchange(exchange)
    if bool(policy.get("auto_trading_enabled")):
        reasons.append("GENERAL_AUTO_TRADING_MUST_REMAIN_OFF")
    if is_emergency_stopped():
        reasons.append("EMERGENCY_STOP_ON")
    if not live_config.api_key_loaded or not live_config.live_trading_enabled:
        reasons.append("BROKER_NOT_READY")
    epoch = current_epoch or build_current_epoch_diagnostics(exchange=exchange)
    if int(epoch.get("current_epoch_accounting_pending_count") or 0) > 0:
        reasons.append("ACCOUNTING_PENDING")
    if int(epoch.get("current_epoch_accounting_failed_count") or 0) > 0:
        reasons.append("ACCOUNTING_FAILED")
    if not epoch.get("current_epoch_sanity_passed", True):
        reasons.append("ACCOUNTING_SANITY_FAILED")
    return reasons


async def _current_epoch_with_exchange_equity(exchange: str) -> dict:
    equity = await _current_equity(exchange)
    return build_current_epoch_diagnostics(exchange=exchange, current_equity=equity)


def start_protected_auto_daemon(
    *,
    exchange: str,
    symbols: list[str],
    amount_krw: float,
    scan_interval_seconds: int,
    max_holding_minutes: int,
    max_position_trades: int,
    current_epoch: dict,
    gate: dict,
) -> dict:
    if not gate.get("protected_full_auto_live_allowed"):
        return {
            "ok": False,
            "status": "ABORTED",
            "message": "Protected full auto daemon gate is blocked.",
            "controlled_auto_live_gate": gate,
            "protected_auto": protected_auto_status(),
        }
    symbols = [symbol for symbol in [str(item).upper() for item in symbols] if symbol in PROTECTED_ALLOWED_SYMBOLS]
    symbols = symbols or list(PROTECTED_ALLOWED_SYMBOLS)
    amount = min(float(amount_krw or PROTECTED_MAX_NOTIONAL_KRW), PROTECTED_MAX_NOTIONAL_KRW)
    acquired, lock = _acquire_protected_lock()
    if not acquired:
        return {
            "ok": False,
            "status": "ABORTED",
            "message": "Protected auto daemon lock is already active.",
            "protected_runtime_lock": lock,
            "protected_auto": protected_auto_status(),
        }
    started = _utc_now()
    protected_session_id = f"pv1-{started.replace(':', '').replace('-', '').replace('Z', '')}-{uuid.uuid4().hex[:6]}"
    baseline = build_protected_session_baseline(
        current_epoch=current_epoch,
        exchange=exchange,
        protected_session_id=protected_session_id,
        started_at_utc=started,
    )
    state = _upsert_state(
        {
            "worker_status": "RUNNING",
            "session_status": "RUNNING",
            "protected_session_id": protected_session_id,
            "exchange": exchange,
            "symbols": symbols,
            "amount_krw": amount,
            "scan_interval_seconds": max(60, int(scan_interval_seconds or PROTECTED_SCAN_INTERVAL_SECONDS)),
            "max_holding_minutes": max(10, int(max_holding_minutes or 10)),
            "max_position_trades": min(max(int(max_position_trades or 1), 1), PROTECTED_MAX_OPEN_POSITIONS),
            "session_loss_limit_krw": PROTECTED_SESSION_LOSS_LIMIT_KRW,
            "started_at_utc": started,
            "stopped_at_utc": None,
            "last_heartbeat_at_utc": started,
            "last_tick_at_utc": None,
            "next_tick_at_utc": started,
            "lock_expires_at_utc": (lock or {}).get("expires_at") or _plus_seconds(PROTECTED_LOCK_TTL_SECONDS),
            "baseline": baseline,
            "stop_reason": "",
            "startup_recovery_action": "STARTED_BY_API",
            "startup_recovery_reason": "",
            "last_scan_result": {"result": "STARTED", "at_utc": started, "gate_allowed": True},
            "latest_report": {},
            "trade_count": 0,
            "protected_strategy_pnl": 0.0,
            "account_session_pnl_delta": 0.0,
        }
    )
    _notify_protected_event(
        "PROTECTED_AUTO_STARTED",
        state=state,
        severity="INFO",
        message="Protected auto daemon started.",
        payload={"started_at_utc": started, "symbols": symbols, "amount_krw": amount},
        event_id=_event_id(protected_session_id, "PROTECTED_AUTO_STARTED", started),
    )
    return {"ok": True, "status": "RUNNING", "protected_auto": {**protected_auto_status(), **state}}


async def run_protected_auto_startup_recovery_async() -> dict:
    state = load_protected_auto_state()
    if str(state.get("worker_status") or "").upper() not in {"RUNNING", "STALE"}:
        lock = load_runtime_lock(PROTECTED_RUNTIME_LOCK_ID) or {}
        if str(lock.get("status") or "").upper() == "RUNNING" and str(lock.get("expires_at") or "") <= _utc_now():
            _release_protected_lock("STOPPED")
            _upsert_state(
                {
                    "startup_recovery_action": "CLEARED_STALE_LOCK",
                    "startup_recovery_reason": "NO_ACTIVE_PROTECTED_DAEMON_LOCK_EXPIRED",
                }
            )
        return {"action": "NO_ACTIVE_PROTECTED_DAEMON", "protected_auto": _startup_recovery_status()}
    try:
        result = await protected_auto_safe_stop_async("BACKEND_RESTART_SAFE_STOP", failed=False)
        _upsert_state({"startup_recovery_action": "SAFE_STOP", "startup_recovery_reason": "BACKEND_RESTART_SAFE_STOP"})
        logger.warning("[protected-auto] startup recovery safe-stopped active protected daemon; manual restart approval required")
        return {"action": "SAFE_STOP", "reasons": ["BACKEND_RESTART_SAFE_STOP"], "protected_auto": result}
    except Exception as exc:
        result = await protected_auto_safe_stop_async(f"STARTUP_RECOVERY_EXCEPTION:{exc.__class__.__name__}", failed=True)
        _upsert_state({"startup_recovery_action": "SAFE_STOP", "startup_recovery_reason": f"{exc.__class__.__name__}:{str(exc)[:160]}"})
        return {"action": "SAFE_STOP", "reasons": [f"{exc.__class__.__name__}:{str(exc)[:160]}"], "protected_auto": result}


def run_protected_auto_startup_recovery() -> dict:
    return asyncio.run(run_protected_auto_startup_recovery_async())


async def _open_order_blocker(exchange: str, symbols: list[str]) -> str | None:
    from app.controlled_auto_live import _open_order_blocker as controlled_open_order_blocker

    return await controlled_open_order_blocker(exchange, [symbol for symbol in symbols if symbol in PROTECTED_ALLOWED_SYMBOLS])


def _duration_ms(started_monotonic: float) -> int:
    return max(0, int((time.monotonic() - started_monotonic) * 1000))


def _finish_tick(started_monotonic: float, updates: dict | None = None) -> dict:
    return _upsert_state(
        {
            "last_tick_finished_at_utc": _utc_now(),
            "worker_loop_duration_ms": _duration_ms(started_monotonic),
            **(updates or {}),
        }
    )


def _record_scan_error(state: dict, started_monotonic: float, error_code: str, *, hard_stop: bool = False) -> dict:
    failures = int(state.get("consecutive_scan_failures") or 0) + 1
    now = _utc_now()
    result = {
        "result": "SCAN_ERROR",
        "error": error_code,
        "hard_stop": hard_stop,
        "at_utc": now,
    }
    return _finish_tick(
        started_monotonic,
        {
            "last_scan_error": error_code,
            "consecutive_scan_failures": failures,
            "last_scan_result": result,
        },
    )


async def protected_auto_tick_async() -> dict:
    started_monotonic = time.monotonic()
    state = _sync_latest_report_into_state(load_protected_auto_state())
    if str(state.get("worker_status") or "").upper() != "RUNNING":
        return protected_auto_status()
    exchange = str(state.get("exchange") or "bithumb")
    acquired, lock = _acquire_protected_lock()
    if not acquired:
        return await protected_auto_safe_stop_async("PROTECTED_RUNTIME_LOCK_CONFLICT", failed=True)
    now = _utc_now()
    next_tick = _plus_seconds(max(int(state.get("scan_interval_seconds") or PROTECTED_SCAN_INTERVAL_SECONDS), 60))
    scope = _position_scope(state)
    heartbeat = _upsert_state(
        {
            "last_heartbeat_at_utc": now,
            "last_tick_at_utc": now,
            "last_tick_started_at_utc": now,
            "next_tick_at_utc": next_tick,
            "lock_expires_at_utc": (lock or {}).get("expires_at") or _plus_seconds(PROTECTED_LOCK_TTL_SECONDS),
            "protected_open_position_count": scope.get("protected_open_position_count", 0),
            "legacy_open_position_count": scope.get("legacy_open_position_count", 0),
            "last_scan_error": "",
        }
    )
    active = _active_protected_job()
    _notify_daily_summary_if_due(heartbeat)
    try:
        current_epoch = await asyncio.wait_for(
            _current_epoch_with_exchange_equity(exchange),
            timeout=max(0.1, PROTECTED_EXCHANGE_TIMEOUT_SECONDS),
        )
    except asyncio.TimeoutError:
        _record_scan_error(heartbeat, started_monotonic, "EXCHANGE_EQUITY_TIMEOUT")
        return protected_auto_status()
    except Exception as exc:
        return await protected_auto_safe_stop_async(f"EXCHANGE_API_CRITICAL_FAILURE:{exc.__class__.__name__}", failed=True)
    hard_stops = _hard_stop_reasons(exchange, current_epoch)
    if hard_stops:
        return await protected_auto_safe_stop_async(",".join(hard_stops), failed="DB_WRITE_FAILURE" in hard_stops)
    if active:
        active_scan = {
            "result": "ACTIVE_CONTROLLED_ENTRY_V3_POSITION_JOB",
            "active_controlled_run_id": active.get("controlled_run_id"),
            "scan_count": active.get("scan_count"),
            "latest_scan_at_utc": active.get("latest_scan_at_utc"),
            "latest_signal_by_timeframe": active.get("latest_signal_by_timeframe") or {},
            "trade_candidate_count_by_timeframe": active.get("trade_candidate_count_by_timeframe") or {},
            "at_utc": now,
        }
        _finish_tick(started_monotonic, {"last_scan_result": active_scan, "consecutive_scan_failures": 0, "last_scan_error": ""})
        return {
            **protected_auto_status(),
            "latest_scan_result": active_scan,
        }
    try:
        open_blocker = await asyncio.wait_for(
            _open_order_blocker(exchange, state.get("symbols") or list(PROTECTED_ALLOWED_SYMBOLS)),
            timeout=max(0.1, PROTECTED_EXCHANGE_TIMEOUT_SECONDS),
        )
    except asyncio.TimeoutError:
        _record_scan_error(heartbeat, started_monotonic, "OPEN_ORDER_AUDIT_TIMEOUT")
        return protected_auto_status()
    if open_blocker:
        return await protected_auto_safe_stop_async(str(open_blocker))
    open_positions = int(scope.get("protected_open_position_count") or 0)
    if open_positions >= PROTECTED_MAX_OPEN_POSITIONS:
        return _finish_tick(
            started_monotonic,
            {
                "last_scan_result": {"result": "PROTECTED_MAX_OPEN_POSITIONS_REACHED", "protected_open_position_count": open_positions, "at_utc": now},
                "last_scan_error": "",
                "consecutive_scan_failures": 0,
            }
        )
    smoke_preflight = build_smoke_test_preflight(
        exchange=exchange,
        symbol="BTC",
        strategy_name="protected_full_auto_live_v1",
        amount_krw=min(float(state.get("amount_krw") or PROTECTED_MAX_NOTIONAL_KRW), PROTECTED_MAX_NOTIONAL_KRW),
        current_epoch=current_epoch,
        open_order_audit=build_open_order_audit(exchange=exchange, current_epoch=current_epoch),
    )
    gate = controlled_auto_live_gate(current_epoch, smoke_preflight, exchange=exchange)
    if not gate.get("protected_full_auto_live_allowed"):
        blockers = [str(item.get("code")) for item in gate.get("protected_full_auto_live_blockers") or []]
        return _finish_tick(
            started_monotonic,
            {
                "last_scan_result": {"result": "GATE_BLOCKED", "blockers": blockers, "at_utc": now},
                "last_scan_error": "",
                "consecutive_scan_failures": 0,
            }
        )
    gate = {
        **gate,
        "active_protected_session_baseline": heartbeat.get("baseline") or {},
        "protected_daemon_session_id": heartbeat.get("protected_session_id"),
    }
    job = await start_controlled_position_loop_job(
        exchange=exchange,
        symbols=[symbol for symbol in (state.get("symbols") or PROTECTED_ALLOWED_SYMBOLS) if symbol in PROTECTED_ALLOWED_SYMBOLS],
        amount_krw=min(float(state.get("amount_krw") or PROTECTED_MAX_NOTIONAL_KRW), PROTECTED_MAX_NOTIONAL_KRW),
        runtime_seconds=900,
        scan_interval_seconds=max(int(state.get("scan_interval_seconds") or PROTECTED_SCAN_INTERVAL_SECONDS), 60),
        max_holding_minutes=max(int(state.get("max_holding_minutes") or 10), 10),
        max_position_trades=PROTECTED_MAX_OPEN_POSITIONS,
        confirmation=CONTROLLED_POSITION_LOOP_CONFIRMATION_PHRASE,
        controlled_gate=gate,
        current_epoch=current_epoch,
        mode=PROTECTED_FULL_AUTO_MODE,
        protected_runtime_instance_id=None,
    )
    _finish_tick(
        started_monotonic,
        {
            "last_scan_result": {
                "result": "STARTED_CONTROLLED_ENTRY_V3_POSITION_JOB" if job.get("ok", True) is not False else "JOB_START_BLOCKED",
                "job": {key: value for key, value in job.items() if key not in {"controlled_auto_live_gate"}},
                "at_utc": now,
            },
            "last_scan_error": "",
            "consecutive_scan_failures": 0,
        }
    )
    return protected_auto_status()


def run_protected_auto_tick() -> dict:
    if not _WORKER_TICK_LOCK.acquire(blocking=False):
        logger.warning("[protected-auto] skipped overlapping worker tick")
        _upsert_state(
            {
                "last_scan_error": "WORKER_TICK_OVERLAP_SKIPPED",
                "last_scan_result": {"result": "SKIPPED", "reason": "WORKER_TICK_OVERLAP_SKIPPED", "at_utc": _utc_now()},
            }
        )
        return protected_auto_status()
    try:
        return asyncio.run(protected_auto_tick_async())
    except Exception as exc:
        logger.exception("[protected-auto] worker tick failed")
        try:
            return protected_auto_safe_stop(f"PROTECTED_WORKER_TICK_EXCEPTION:{exc.__class__.__name__}", failed=True)
        except Exception:
            raise
    finally:
        _WORKER_TICK_LOCK.release()
