from __future__ import annotations

import asyncio
import multiprocessing
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.accounting_epoch import build_current_epoch_diagnostics, build_open_order_audit, build_smoke_test_preflight
from app.controlled_auto_live import controlled_auto_live_gate, protected_position_scope_status
from app.database import (
    get_connection,
    insert_notification_log,
    insert_protected_auto_safety_snapshot,
    load_current_accounting_epoch,
    load_protected_auto_safety_snapshot,
    load_unresolved_live_order_logs_for_exchange,
)
from app.live_broker import LiveTradingConfig, get_live_broker, is_emergency_stopped
from app.live_smoke_test import _current_equity

SNAPSHOT_TTL_SECONDS = int(os.getenv("PROTECTED_GATE_SNAPSHOT_TTL_SECONDS", "60"))
EXCHANGE_OPEN_ORDER_TIMEOUT_SECONDS = float(os.getenv("PROTECTED_GATE_OPEN_ORDER_TIMEOUT_SECONDS", "3"))
BROKER_STATUS_TIMEOUT_SECONDS = float(os.getenv("PROTECTED_GATE_BROKER_TIMEOUT_SECONDS", "2"))
CURRENT_EPOCH_TIMEOUT_SECONDS = float(os.getenv("PROTECTED_GATE_EPOCH_TIMEOUT_SECONDS", "3"))
REFRESH_TIMEOUT_SECONDS = float(os.getenv("PROTECTED_GATE_REFRESH_TIMEOUT_SECONDS", "8"))
SERVER_LOAD_BLOCK_THRESHOLD = float(os.getenv("PROTECTED_GATE_LOAD_BLOCK_THRESHOLD", "3.0"))
SERVER_MEMORY_BLOCK_RATIO = float(os.getenv("PROTECTED_GATE_MEMORY_BLOCK_RATIO", "0.92"))
RECENT_TIMEOUT_COOLDOWN_SECONDS = int(os.getenv("PROTECTED_GATE_RECENT_TIMEOUT_COOLDOWN_SECONDS", "30"))

_REFRESH_LOCK = threading.Lock()


class SafetySnapshotTimeout(TimeoutError):
    pass


def _use_subprocess_refresh() -> bool:
    configured = os.getenv("PROTECTED_GATE_USE_SUBPROCESS_REFRESH", "").strip().lower()
    if configured:
        return configured in {"1", "true", "yes", "on"}
    return os.getenv("APP_ENV", "development").strip().lower() == "production"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _plus_seconds(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def _duration_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _blocker(code: str, count: int = 1, **extra: Any) -> dict:
    return {"code": code, "count": count, **extra}


def _resource_snapshot() -> dict:
    load_average = None
    try:
        load_average = list(os.getloadavg())
    except (AttributeError, OSError):
        pass
    memory = _linux_memory_snapshot()
    return {
        "load_average": load_average,
        "memory": memory,
    }


def _linux_memory_snapshot() -> dict:
    path = "/proc/meminfo"
    if not os.path.exists(path):
        return {"status": "UNAVAILABLE"}
    values: dict[str, int] = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) >= 2 and parts[0].endswith(":"):
                    values[parts[0][:-1]] = int(parts[1]) * 1024
    except OSError as exc:
        return {"status": "UNAVAILABLE", "error": exc.__class__.__name__}
    total = values.get("MemTotal") or 0
    available = values.get("MemAvailable") or 0
    used_ratio = 1.0 - (available / total) if total > 0 else None
    return {
        "status": "OK",
        "mem_total_bytes": total,
        "mem_available_bytes": available,
        "mem_used_ratio": used_ratio,
        "swap_total_bytes": values.get("SwapTotal") or 0,
        "swap_free_bytes": values.get("SwapFree") or 0,
    }


def _server_load_guard(latest: dict | None = None, *, force: bool = False) -> tuple[str, list[dict], dict]:
    resources = _resource_snapshot()
    blockers: list[dict] = []
    load_average = resources.get("load_average") or []
    if load_average and float(load_average[0]) > SERVER_LOAD_BLOCK_THRESHOLD:
        blockers.append(_blocker("SERVER_LOAD_GUARD_HIGH", load_average_1m=float(load_average[0])))
    memory = resources.get("memory") or {}
    used_ratio = memory.get("mem_used_ratio")
    if used_ratio is not None and float(used_ratio) > SERVER_MEMORY_BLOCK_RATIO:
        blockers.append(_blocker("SERVER_MEMORY_GUARD_HIGH", memory_used_ratio=float(used_ratio)))
    if latest and str(latest.get("refresh_status") or "").upper() == "TIMEOUT":
        created = _parse_utc(str(latest.get("created_at_utc") or ""))
        if created and (datetime.now(timezone.utc) - created).total_seconds() < RECENT_TIMEOUT_COOLDOWN_SECONDS:
            blockers.append(_blocker("RECENT_REFRESH_TIMEOUT_COOLDOWN"))
    if blockers and not force:
        return "BLOCKED", blockers, resources
    if blockers:
        return "WARNING_FORCE_REFRESH", blockers, resources
    return "OK", [], resources


def load_cached_protected_gate_snapshot(exchange: str = "bithumb") -> dict:
    snapshot = load_protected_auto_safety_snapshot(exchange=exchange)
    now = datetime.now(timezone.utc)
    if snapshot:
        expires = _parse_utc(str(snapshot.get("expires_at_utc") or ""))
        created = _parse_utc(str(snapshot.get("created_at_utc") or ""))
        snapshot["is_fresh"] = bool(expires and expires > now)
        snapshot["age_seconds"] = int((now - created).total_seconds()) if created else None
    return {
        "ok": True,
        "exchange": exchange,
        "refresh_in_progress": _REFRESH_LOCK.locked(),
        "snapshot": snapshot,
        "gate_allowed": bool(snapshot and snapshot.get("is_fresh") and snapshot.get("gate_allowed")),
        "gate_status": _gate_status_from_snapshot(snapshot),
    }


def _gate_status_from_snapshot(snapshot: dict | None) -> str:
    if not snapshot:
        return "GATE_REFRESH_REQUIRED"
    if not snapshot.get("is_fresh"):
        return "GATE_SNAPSHOT_STALE"
    if not snapshot.get("gate_allowed"):
        return "GATE_BLOCKED"
    return "GATE_ALLOWED"


def _record_refresh_notification(event_type: str, snapshot: dict, summary: str) -> None:
    try:
        insert_notification_log(
            {
                "event_id": f"{event_type.lower()}-{snapshot.get('snapshot_id') or uuid.uuid4().hex[:12]}",
                "event_type": event_type,
                "provider": "db",
                "status": "SKIPPED",
                "dedupe_status": "LOGGED",
                "title": event_type,
                "summary": summary,
                "payload": {
                    "snapshot_id": snapshot.get("snapshot_id"),
                    "refresh_status": snapshot.get("refresh_status"),
                    "gate_blockers": snapshot.get("gate_blockers", []),
                    "refresh_error": snapshot.get("refresh_error", ""),
                },
                "created_at_utc": snapshot.get("created_at_utc") or utc_now(),
            }
        )
    except Exception:
        pass


def _failure_snapshot(
    *,
    exchange: str,
    started: float,
    refresh_status: str,
    blocker_code: str,
    error: str = "",
    resources: dict | None = None,
    guard_status: str = "UNKNOWN",
    latest: dict | None = None,
) -> dict:
    failures = int((latest or {}).get("consecutive_refresh_failures") or 0) + 1
    snapshot = insert_protected_auto_safety_snapshot(
        {
            "snapshot_id": f"safety-{utc_now().replace(':', '').replace('-', '').replace('Z', '')}-{uuid.uuid4().hex[:6]}",
            "exchange": exchange,
            "created_at_utc": utc_now(),
            "expires_at_utc": utc_now(),
            "refresh_status": refresh_status,
            "refresh_error": error,
            "refresh_duration_ms": _duration_ms(started),
            "server_load_guard_status": guard_status,
            "server_resource_snapshot": resources or {},
            "consecutive_refresh_failures": failures,
            "gate_allowed": False,
            "gate_blockers": [_blocker(blocker_code)],
            "gate_warnings": [],
        }
    )
    _record_refresh_notification(
        "SAFETY_SNAPSHOT_REFRESH_TIMEOUT" if refresh_status == "TIMEOUT" else "SAFETY_SNAPSHOT_REFRESH_FAILED",
        snapshot,
        error or blocker_code,
    )
    return snapshot


async def _broker_status(exchange: str) -> dict:
    config = LiveTradingConfig.for_exchange(exchange)
    ready = bool(config.api_key_loaded and config.live_trading_enabled and not is_emergency_stopped())
    return {
        "broker_status": "READY" if ready else "NOT_READY",
        "api_key_loaded": bool(config.api_key_loaded),
        "live_trading_enabled": bool(config.live_trading_enabled),
        "emergency_status": "ON" if is_emergency_stopped() else "OFF",
    }


async def _current_epoch_snapshot(exchange: str) -> dict:
    equity = await _current_equity(exchange)
    return build_current_epoch_diagnostics(exchange=exchange, current_equity=equity)


def _open_order_markets(exchange: str) -> list[str]:
    markets = {"KRW-BTC", "KRW-ETH", "KRW-WLD", "KRW-XLM", "KRW-RE", "KRW-STRAX", "KRW-ID"}
    for position in (load_current_accounting_epoch(exchange) or {}).get("starting_positions") or []:
        if position.get("market"):
            markets.add(str(position["market"]))
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT market
            FROM live_order_logs
            WHERE exchange = ?
              AND status IN ('SUBMITTED', 'WAITING', 'PARTIALLY_FILLED')
              AND request_id NOT LIKE '%-submitted%'
              AND request_id NOT LIKE '%-waiting-%'
              AND request_id NOT LIKE '%-partial%'
              AND request_id NOT LIKE '%-canceled-%'
              AND request_id NOT LIKE '%-filled-%'
              AND request_id NOT LIKE '%-failed-%'
            """,
            (exchange,),
        ).fetchall()
    for row in rows:
        if row["market"]:
            markets.add(str(row["market"]))
    return sorted(market for market in markets if market)


async def _exchange_open_orders(exchange: str) -> dict:
    broker = get_live_broker(exchange)
    orders_by_key: dict[str, dict] = {}
    errors = []
    for market in _open_order_markets(exchange):
        try:
            response = await broker.list_open_orders(market)
            raw_orders = response.get("orders", []) if isinstance(response, dict) else []
            if isinstance(raw_orders, dict):
                raw_orders = [raw_orders]
            for order in raw_orders if isinstance(raw_orders, list) else []:
                if not isinstance(order, dict):
                    continue
                order["market"] = order.get("market") or market
                key = str(order.get("uuid") or order.get("identifier") or order.get("client_order_id") or f"{market}:{len(orders_by_key)}")
                orders_by_key[key] = order
        except Exception as exc:
            errors.append({"market": market, "error": str(exc)[:240]})
    return {
        "status": "SUCCESS" if not errors else ("PARTIAL" if orders_by_key else "UNAVAILABLE"),
        "orders": list(orders_by_key.values()),
        "errors": errors[:50],
    }


async def _refresh_impl(exchange: str, *, amount_krw: float) -> dict:
    broker = await asyncio.wait_for(_broker_status(exchange), timeout=max(BROKER_STATUS_TIMEOUT_SECONDS, 0.1))
    current_epoch = await asyncio.wait_for(_current_epoch_snapshot(exchange), timeout=max(CURRENT_EPOCH_TIMEOUT_SECONDS, 0.1))
    try:
        exchange_orders = await asyncio.wait_for(_exchange_open_orders(exchange), timeout=max(EXCHANGE_OPEN_ORDER_TIMEOUT_SECONDS, 0.1))
    except asyncio.TimeoutError as exc:
        raise SafetySnapshotTimeout("exchange open order refresh timed out") from exc
    open_order_audit = build_open_order_audit(
        exchange=exchange,
        current_epoch=current_epoch,
        exchange_open_orders=exchange_orders.get("orders") or [],
        exchange_open_order_status=exchange_orders.get("status") or "UNAVAILABLE",
        exchange_open_order_errors=exchange_orders.get("errors") or [],
    )
    smoke_preflight = build_smoke_test_preflight(
        exchange=exchange,
        symbol="BTC",
        strategy_name="protected_full_auto_live_v1",
        amount_krw=amount_krw,
        current_epoch=current_epoch,
        open_order_audit=open_order_audit,
    )
    gate = controlled_auto_live_gate(current_epoch, smoke_preflight, exchange=exchange)
    return {
        "broker": broker,
        "current_epoch": current_epoch,
        "open_order_audit": open_order_audit,
        "smoke_preflight": smoke_preflight,
        "controlled_gate": gate,
    }


def _refresh_worker_entry(exchange: str, amount_krw: float, queue: multiprocessing.Queue) -> None:
    try:
        result = asyncio.run(_refresh_impl(exchange, amount_krw=amount_krw))
        queue.put(("ok", result))
    except BaseException as exc:
        queue.put(("error", {"type": exc.__class__.__name__, "message": str(exc)[:240]}))


def _refresh_impl_subprocess(exchange: str, amount_krw: float, timeout_seconds: float) -> dict:
    start_method = "fork" if "fork" in multiprocessing.get_all_start_methods() else "spawn"
    ctx = multiprocessing.get_context(start_method)
    queue: multiprocessing.Queue = ctx.Queue(maxsize=1)
    process = ctx.Process(target=_refresh_worker_entry, args=(exchange, amount_krw, queue), daemon=True)
    process.start()
    process.join(max(timeout_seconds, 0.1))
    if process.is_alive():
        process.terminate()
        process.join(2)
        if process.is_alive():
            try:
                process.kill()
            except AttributeError:
                pass
            process.join(1)
        raise SafetySnapshotTimeout("Safety snapshot subprocess timed out.")
    if queue.empty():
        raise RuntimeError(f"Safety snapshot subprocess exited without result: {process.exitcode}")
    status, payload = queue.get()
    if status == "ok":
        return payload
    error_type = str((payload or {}).get("type") or "RuntimeError")
    message = str((payload or {}).get("message") or "Safety snapshot subprocess failed.")
    if error_type in {"SafetySnapshotTimeout", "TimeoutError"}:
        raise SafetySnapshotTimeout(message)
    raise RuntimeError(f"{error_type}:{message}")


async def _refresh_details(exchange: str, amount_krw: float) -> dict:
    if _use_subprocess_refresh():
        return await asyncio.to_thread(_refresh_impl_subprocess, exchange, amount_krw, REFRESH_TIMEOUT_SECONDS)
    return await asyncio.wait_for(
        _refresh_impl(exchange, amount_krw=amount_krw),
        timeout=max(REFRESH_TIMEOUT_SECONDS, 0.1),
    )


async def refresh_protected_gate_safety_snapshot(
    *,
    exchange: str = "bithumb",
    amount_krw: float = 6000.0,
    force: bool = False,
) -> dict:
    if not _REFRESH_LOCK.acquire(blocking=False):
        cached = load_cached_protected_gate_snapshot(exchange)
        return {
            **cached,
            "ok": False,
            "status": "REFRESH_IN_PROGRESS",
            "message": "Protected gate safety snapshot refresh is already running.",
        }
    started = time.monotonic()
    latest = load_protected_auto_safety_snapshot(exchange=exchange)
    try:
        guard_status, guard_blockers, resources = _server_load_guard(latest, force=force)
        if guard_status == "BLOCKED":
            snapshot = _failure_snapshot(
                exchange=exchange,
                started=started,
                refresh_status="FAILED",
                blocker_code=str(guard_blockers[0].get("code") or "SERVER_LOAD_GUARD_BLOCKED"),
                error="Server load guard blocked safety snapshot refresh.",
                resources=resources,
                guard_status=guard_status,
                latest=latest,
            )
            return {"ok": False, "status": "FAILED", "snapshot": _with_freshness(snapshot), "gate_allowed": False}
        try:
            details = await _refresh_details(exchange, amount_krw)
        except asyncio.TimeoutError as exc:
            snapshot = _failure_snapshot(
                exchange=exchange,
                started=started,
                refresh_status="TIMEOUT",
                blocker_code="SAFETY_SNAPSHOT_REFRESH_TIMEOUT",
                error=str(exc) or "Safety snapshot refresh timed out.",
                resources=resources,
                guard_status=guard_status,
                latest=latest,
            )
            return {"ok": False, "status": "TIMEOUT", "snapshot": _with_freshness(snapshot), "gate_allowed": False}
        except SafetySnapshotTimeout as exc:
            snapshot = _failure_snapshot(
                exchange=exchange,
                started=started,
                refresh_status="TIMEOUT",
                blocker_code="SAFETY_SNAPSHOT_REFRESH_TIMEOUT",
                error=str(exc),
                resources=resources,
                guard_status=guard_status,
                latest=latest,
            )
            return {"ok": False, "status": "TIMEOUT", "snapshot": _with_freshness(snapshot), "gate_allowed": False}
        except Exception as exc:
            snapshot = _failure_snapshot(
                exchange=exchange,
                started=started,
                refresh_status="FAILED",
                blocker_code=f"SAFETY_SNAPSHOT_REFRESH_FAILED:{exc.__class__.__name__}",
                error=str(exc)[:240],
                resources=resources,
                guard_status=guard_status,
                latest=latest,
            )
            return {"ok": False, "status": "FAILED", "snapshot": _with_freshness(snapshot), "gate_allowed": False}

        current_epoch = details["current_epoch"]
        open_order_audit = details["open_order_audit"]
        gate = details["controlled_gate"]
        scope = protected_position_scope_status(exchange=exchange)
        audit_summary = open_order_audit.get("open_order_audit_summary") or {}
        blockers = list(gate.get("protected_full_auto_live_blockers") or [])
        warnings = list(gate.get("protected_full_auto_live_warnings") or [])
        if guard_blockers:
            warnings.extend(guard_blockers)
        gate_allowed = bool(gate.get("protected_full_auto_live_allowed")) and guard_status != "BLOCKED"
        snapshot = insert_protected_auto_safety_snapshot(
            {
                "snapshot_id": f"safety-{utc_now().replace(':', '').replace('-', '').replace('Z', '')}-{uuid.uuid4().hex[:6]}",
                "exchange": exchange,
                "created_at_utc": utc_now(),
                "expires_at_utc": _plus_seconds(SNAPSHOT_TTL_SECONDS),
                "broker_status": details["broker"].get("broker_status"),
                "emergency_status": details["broker"].get("emergency_status"),
                "exchange_open_order_count": int(audit_summary.get("exchange_open_order_count") or 0),
                "db_open_order_count": len(load_unresolved_live_order_logs_for_exchange(exchange)),
                "accounting_pending_count": int(current_epoch.get("current_epoch_accounting_pending_count") or 0),
                "accounting_failed_count": int(current_epoch.get("current_epoch_accounting_failed_count") or 0),
                "current_epoch_id": current_epoch.get("current_epoch_id"),
                "current_epoch_sanity_passed": bool(current_epoch.get("current_epoch_sanity_passed")),
                "current_epoch_trust_level": current_epoch.get("current_epoch_trust_level") or "LOW",
                "equity_diff_rate": current_epoch.get("current_epoch_equity_diff_rate"),
                "protected_open_position_count": int(scope.get("protected_open_position_count") or 0),
                "legacy_open_position_count": int(scope.get("legacy_open_position_count") or 0),
                "protected_empty_slot_count": int(scope.get("protected_empty_slot_count") or 0),
                "gate_allowed": gate_allowed,
                "gate_blockers": blockers,
                "gate_warnings": warnings,
                "refresh_duration_ms": _duration_ms(started),
                "refresh_status": "SUCCESS" if gate_allowed else "PARTIAL",
                "refresh_error": "",
                "server_load_guard_status": guard_status,
                "server_resource_snapshot": resources,
                "consecutive_refresh_failures": 0 if gate_allowed else int((latest or {}).get("consecutive_refresh_failures") or 0),
                "current_epoch": current_epoch,
                "smoke_preflight": details["smoke_preflight"],
                "controlled_gate": gate,
                "open_order_audit": open_order_audit,
            }
        )
        return {"ok": True, "status": snapshot.get("refresh_status"), "snapshot": _with_freshness(snapshot), "gate_allowed": gate_allowed}
    finally:
        _REFRESH_LOCK.release()


def _with_freshness(snapshot: dict | None) -> dict | None:
    if not snapshot:
        return None
    now = datetime.now(timezone.utc)
    created = _parse_utc(str(snapshot.get("created_at_utc") or ""))
    expires = _parse_utc(str(snapshot.get("expires_at_utc") or ""))
    snapshot["is_fresh"] = bool(expires and expires > now)
    snapshot["age_seconds"] = int((now - created).total_seconds()) if created else None
    snapshot["refresh_in_progress"] = _REFRESH_LOCK.locked()
    return snapshot
