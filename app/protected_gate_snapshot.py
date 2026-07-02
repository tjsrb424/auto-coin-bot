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
from app.controlled_auto_live import MAX_OPEN_POSITIONS, controlled_auto_live_gate, protected_position_scope_status
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
from app.protected_equity_snapshot import load_cached_protected_equity_snapshot, refresh_protected_equity_snapshot

SNAPSHOT_TTL_SECONDS = int(os.getenv("PROTECTED_GATE_SNAPSHOT_TTL_SECONDS", "60"))
EXCHANGE_OPEN_ORDER_TIMEOUT_SECONDS = float(os.getenv("PROTECTED_GATE_OPEN_ORDER_TIMEOUT_SECONDS", "3"))
BROKER_STATUS_TIMEOUT_SECONDS = float(os.getenv("PROTECTED_GATE_BROKER_TIMEOUT_SECONDS", "2"))
CURRENT_EPOCH_TIMEOUT_SECONDS = float(os.getenv("PROTECTED_GATE_EPOCH_TIMEOUT_SECONDS", "3"))
REFRESH_TIMEOUT_SECONDS = float(os.getenv("PROTECTED_GATE_REFRESH_TIMEOUT_SECONDS", "8"))
CRITICAL_REFRESH_TIMEOUT_SECONDS = float(os.getenv("PROTECTED_GATE_CRITICAL_REFRESH_TIMEOUT_SECONDS", "5"))
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


def _new_step(name: str) -> dict:
    return {
        "step_name": name,
        "status": "SUCCESS",
        "duration_ms": 0,
        "error_message": "",
        "started_at_utc": utc_now(),
        "finished_at_utc": None,
    }


async def _timed_async_step(
    timings: list[dict],
    name: str,
    coro_factory,
    *,
    timeout_seconds: float | None = None,
):
    step = _new_step(name)
    started = time.monotonic()
    timings.append(step)
    try:
        coro = coro_factory()
        if timeout_seconds is None:
            result = await coro
        else:
            result = await asyncio.wait_for(coro, timeout=max(timeout_seconds, 0.1))
        step["status"] = "SUCCESS"
        return result
    except asyncio.TimeoutError:
        step["status"] = "TIMEOUT"
        step["error_message"] = f"{name} timed out"
        raise
    except Exception as exc:
        step["status"] = "FAILED"
        step["error_message"] = str(exc)[:240]
        raise
    finally:
        step["duration_ms"] = _duration_ms(started)
        step["finished_at_utc"] = utc_now()


def _timed_sync_step(timings: list[dict], name: str, func):
    step = _new_step(name)
    started = time.monotonic()
    timings.append(step)
    try:
        result = func()
        step["status"] = "SUCCESS"
        return result
    except Exception as exc:
        step["status"] = "FAILED"
        step["error_message"] = str(exc)[:240]
        raise
    finally:
        step["duration_ms"] = _duration_ms(started)
        step["finished_at_utc"] = utc_now()


def _step_summary(timings: list[dict]) -> tuple[dict, dict]:
    slowest = max(timings, key=lambda item: int(item.get("duration_ms") or 0), default={})
    timeout = next((item for item in timings if str(item.get("status") or "").upper() == "TIMEOUT"), {})
    return slowest, timeout


def _remaining_critical_budget_seconds(started: float) -> float:
    elapsed = time.monotonic() - started
    return max(0.1, CRITICAL_REFRESH_TIMEOUT_SECONDS - elapsed)


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
        snapshot["refresh_step_timings"] = snapshot.get("critical_step_timings") or []
        snapshot["slowest_step"] = snapshot.get("slowest_step") or {}
        snapshot["timeout_step"] = snapshot.get("timeout_step") or {}
        snapshot["refresh_error_detail"] = snapshot.get("refresh_error_detail") or snapshot.get("refresh_error") or ""
    critical_gate_allowed = bool(snapshot and snapshot.get("is_fresh") and snapshot.get("critical_gate_allowed"))
    protected_start_allowed = bool(snapshot and snapshot.get("is_fresh") and snapshot.get("protected_start_allowed"))
    return {
        "ok": True,
        "exchange": exchange,
        "refresh_in_progress": _REFRESH_LOCK.locked(),
        "snapshot": snapshot,
        "gate_allowed": bool(snapshot and snapshot.get("is_fresh") and snapshot.get("gate_allowed")),
        "critical_gate_allowed": critical_gate_allowed,
        "protected_start_allowed": protected_start_allowed,
        "protected_start_blockers": (snapshot or {}).get("protected_start_blockers") or [],
        "protected_start_warnings": (snapshot or {}).get("protected_start_warnings") or [],
        "optional_diagnostics_status": (snapshot or {}).get("optional_diagnostics_status") or "UNKNOWN",
        "refresh_step_timings": (snapshot or {}).get("refresh_step_timings") or [],
        "slowest_step": (snapshot or {}).get("slowest_step") or {},
        "timeout_step": (snapshot or {}).get("timeout_step") or {},
        "refresh_duration_ms": (snapshot or {}).get("refresh_duration_ms"),
        "refresh_error_detail": (snapshot or {}).get("refresh_error_detail") or "",
        "gate_status": _gate_status_from_snapshot(snapshot),
    }


def _gate_status_from_snapshot(snapshot: dict | None) -> str:
    if not snapshot:
        return "GATE_REFRESH_REQUIRED"
    if not snapshot.get("is_fresh"):
        return "CRITICAL_SNAPSHOT_STALE" if "critical_gate_allowed" in snapshot else "GATE_SNAPSHOT_STALE"
    if "protected_start_allowed" in snapshot and not snapshot.get("protected_start_allowed"):
        return "GATE_BLOCKED"
    if "critical_gate_allowed" in snapshot and not snapshot.get("critical_gate_allowed"):
        return "GATE_BLOCKED"
    if not snapshot.get("gate_allowed") and "critical_gate_allowed" not in snapshot:
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
    return await _exchange_open_orders_for_markets(exchange, _open_order_markets(exchange), broker=broker)


async def _exchange_open_orders_for_markets(exchange: str, markets: list[str], *, broker=None) -> dict:
    live_broker = broker or get_live_broker(exchange)
    orders_by_key: dict[str, dict] = {}
    errors = []
    for market in sorted({str(item) for item in markets if item}):
        try:
            response = await live_broker.list_open_orders(market)
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


def _critical_open_order_markets(exchange: str) -> list[str]:
    markets = {"KRW-BTC", "KRW-ETH"}
    for order in load_unresolved_live_order_logs_for_exchange(exchange):
        market = str(order.get("market") or "")
        if market:
            markets.add(market)
    return sorted(markets)


def _db_open_order_count(exchange: str) -> int:
    return len(load_unresolved_live_order_logs_for_exchange(exchange))


def _current_epoch_accounting_counts(exchange: str, started_at_utc: str) -> dict:
    with get_connection() as conn:
        pending = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM exchange_fills_ledger
            WHERE exchange_name = ?
              AND executed_at_utc >= ?
              AND match_status IN ('UNMATCHED', 'MISSING_CANONICAL_LOG')
            """,
            (exchange, started_at_utc),
        ).fetchone()["count"]
        failed = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM live_order_logs
            WHERE exchange = ? AND created_at >= ? AND status = 'FAILED'
            """,
            (exchange, started_at_utc),
        ).fetchone()["count"]
    return {"pending": int(pending or 0), "failed": int(failed or 0)}


async def _fresh_or_refresh_equity_snapshot(exchange: str) -> dict | None:
    cached = load_cached_protected_equity_snapshot(exchange)
    if cached.get("equity_snapshot_fresh"):
        return cached.get("snapshot")
    refreshed = await refresh_protected_equity_snapshot(exchange=exchange)
    if refreshed.get("equity_snapshot_fresh") and str(refreshed.get("status") or "").upper() == "SUCCESS":
        return refreshed.get("snapshot")
    return None


def _lightweight_epoch_sanity(exchange: str, accounting_counts: dict, equity_snapshot: dict | None) -> dict:
    epoch = load_current_accounting_epoch(exchange)
    if not epoch:
        return {
            "current_epoch_exists": False,
            "current_epoch_id": None,
            "current_epoch_status": "MISSING",
            "current_epoch_trust_level": "LOW",
            "current_epoch_accounting_pending_count": int(accounting_counts.get("pending") or 0),
            "current_epoch_accounting_failed_count": int(accounting_counts.get("failed") or 0),
            "current_epoch_sanity_passed": False,
            "current_epoch_blockers": [_blocker("CURRENT_EPOCH_MISSING")],
            "equity_snapshot_status": "MISSING",
        }
    blockers: list[dict] = []
    status = str(epoch.get("epoch_status") or "").upper()
    trust = str(epoch.get("epoch_trust_level") or "LOW").upper()
    if status != "ACTIVE":
        blockers.append(_blocker("CURRENT_EPOCH_NOT_ACTIVE", epoch_status=status or "UNKNOWN"))
    if trust == "LOW":
        blockers.append(_blocker("CURRENT_EPOCH_TRUST_LOW"))
    pending = int(accounting_counts.get("pending") or 0)
    failed = int(accounting_counts.get("failed") or 0)
    if pending:
        blockers.append(_blocker("CURRENT_EPOCH_ACCOUNTING_PENDING", pending))
    if failed:
        blockers.append(_blocker("CURRENT_EPOCH_ACCOUNTING_FAILED", failed))
    equity_snapshot_status = "FRESH" if equity_snapshot else "MISSING"
    if equity_snapshot is None:
        blockers.append(_blocker("EQUITY_SNAPSHOT_UNAVAILABLE"))
    current_equity = equity_snapshot.get("total_equity_krw") if equity_snapshot else None
    return {
        "current_epoch_exists": True,
        "current_epoch_id": epoch.get("epoch_id"),
        "current_epoch_started_at_utc": epoch.get("epoch_started_at_utc"),
        "current_epoch_status": status,
        "current_epoch_trust_level": trust,
        "current_epoch_starting_equity": epoch.get("starting_exchange_equity"),
        "current_epoch_current_equity": current_equity,
        "current_epoch_equity_diff": None,
        "current_epoch_equity_diff_rate": None,
        "current_epoch_accounting_pending_count": pending,
        "current_epoch_accounting_failed_count": failed,
        "current_epoch_sanity_passed": not blockers,
        "current_epoch_restart_allowed": not blockers,
        "current_epoch_blockers": blockers,
        "cost_basis_policy": epoch.get("cost_basis_policy"),
        "legacy_history_isolated": bool(epoch.get("legacy_history_isolated")),
        "equity_snapshot_status": equity_snapshot_status,
        "equity_snapshot_id": equity_snapshot.get("equity_snapshot_id") if equity_snapshot else None,
    }


def _optional_diagnostics_status(latest: dict | None) -> str:
    if not latest:
        return "MISSING"
    status = str(latest.get("optional_diagnostics_status") or "UNKNOWN").upper()
    if status == "UNKNOWN":
        status = str(latest.get("refresh_status") or "UNKNOWN").upper()
    if str(latest.get("snapshot_id") or "").startswith("critical-") and status == "SUCCESS":
        return "CACHED_CRITICAL_ONLY"
    return status


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


async def refresh_protected_gate_critical_snapshot(
    *,
    exchange: str = "bithumb",
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
    timings: list[dict] = []
    latest = load_protected_auto_safety_snapshot(exchange=exchange)
    resources: dict = {}
    guard_status = "UNKNOWN"
    blockers: list[dict] = []
    warnings: list[dict] = []
    refresh_status = "SUCCESS"
    refresh_error = ""
    broker: dict = {"broker_status": "UNKNOWN", "emergency_status": "UNKNOWN"}
    exchange_orders: dict = {"status": "UNKNOWN", "orders": [], "errors": []}
    accounting_counts = {"pending": 0, "failed": 0}
    db_open_order_count = 0
    scope: dict = {}
    current_epoch: dict = {}
    equity_snapshot: dict | None = None

    def build_snapshot() -> dict:
        slowest_step, timeout_step = _step_summary(timings)
        critical_allowed = not blockers and refresh_status == "SUCCESS"
        protected_start_blockers = [] if critical_allowed else list(blockers)
        protected_start_allowed = critical_allowed
        controlled_gate = {
            "protected_full_auto_live_allowed": protected_start_allowed,
            "protected_session_start_allowed": protected_start_allowed,
            "protected_full_auto_live_blockers": protected_start_blockers,
            "protected_full_auto_live_warnings": warnings,
            "critical_gate_only": True,
        }
        exchange_count = len(exchange_orders.get("orders") or [])
        payload = {
            "snapshot_id": f"critical-{utc_now().replace(':', '').replace('-', '').replace('Z', '')}-{uuid.uuid4().hex[:6]}",
            "exchange": exchange,
            "created_at_utc": utc_now(),
            "expires_at_utc": _plus_seconds(SNAPSHOT_TTL_SECONDS),
            "broker_status": broker.get("broker_status") or "UNKNOWN",
            "emergency_status": broker.get("emergency_status") or "UNKNOWN",
            "exchange_open_order_count": exchange_count,
            "db_open_order_count": db_open_order_count,
            "accounting_pending_count": int(accounting_counts.get("pending") or 0),
            "accounting_failed_count": int(accounting_counts.get("failed") or 0),
            "current_epoch_id": current_epoch.get("current_epoch_id"),
            "current_epoch_sanity_passed": bool(current_epoch.get("current_epoch_sanity_passed")),
            "current_epoch_trust_level": current_epoch.get("current_epoch_trust_level") or "LOW",
            "equity_diff_rate": current_epoch.get("current_epoch_equity_diff_rate"),
            "protected_open_position_count": int(scope.get("protected_open_position_count") or 0),
            "legacy_open_position_count": int(scope.get("legacy_open_position_count") or 0),
            "protected_empty_slot_count": int(scope.get("protected_empty_slot_count") or 0),
            "gate_allowed": protected_start_allowed,
            "gate_blockers": protected_start_blockers,
            "gate_warnings": warnings,
            "critical_gate_allowed": critical_allowed,
            "critical_gate_blockers": list(blockers),
            "critical_gate_warnings": warnings,
            "protected_start_allowed": protected_start_allowed,
            "protected_start_blockers": protected_start_blockers,
            "protected_start_warnings": warnings,
            "optional_diagnostics_status": _optional_diagnostics_status(latest),
            "critical_refresh_duration_ms": _duration_ms(started),
            "critical_step_timings": timings,
            "slowest_step": slowest_step,
            "timeout_step": timeout_step,
            "refresh_duration_ms": _duration_ms(started),
            "refresh_status": refresh_status if critical_allowed else ("TIMEOUT" if timeout_step else "PARTIAL"),
            "refresh_error": refresh_error,
            "refresh_error_detail": refresh_error,
            "server_load_guard_status": guard_status,
            "server_resource_snapshot": resources,
            "consecutive_refresh_failures": 0 if critical_allowed else int((latest or {}).get("consecutive_refresh_failures") or 0) + 1,
            "current_epoch": current_epoch,
            "controlled_gate": controlled_gate,
            "open_order_audit": {
                "critical_gate_only": True,
                "queried_markets": _critical_open_order_markets(exchange),
                "exchange_open_order_status": exchange_orders.get("status"),
                "exchange_open_order_errors": exchange_orders.get("errors") or [],
                "open_order_audit_summary": {
                    "exchange_open_order_count": exchange_count,
                    "db_open_order_count": db_open_order_count,
                },
            },
            "smoke_preflight": {
                "critical_gate_only": True,
                "open_order_count": exchange_count + db_open_order_count,
                "open_order_audit_summary": {
                    "exchange_open_order_count": exchange_count,
                    "db_open_order_count": db_open_order_count,
                },
            },
        }
        return insert_protected_auto_safety_snapshot(payload)

    try:
        guard_status, guard_blockers, resources = _timed_sync_step(
            timings,
            "server_load_guard",
            lambda: _server_load_guard(latest, force=force),
        )
        if guard_status == "BLOCKED":
            blockers.extend(guard_blockers)
            refresh_status = "FAILED"
            refresh_error = "Server load guard blocked critical safety refresh."
            snapshot = build_snapshot()
            _record_refresh_notification("SAFETY_SNAPSHOT_REFRESH_FAILED", snapshot, refresh_error)
            return _critical_refresh_response(snapshot, ok=False)
        warnings.extend(guard_blockers)

        epoch = load_current_accounting_epoch(exchange)
        started_at = str((epoch or {}).get("epoch_started_at_utc") or "")
        accounting_counts = _timed_sync_step(
            timings,
            "db_accounting_check",
            lambda: _current_epoch_accounting_counts(exchange, started_at) if started_at else {"pending": 0, "failed": 0},
        )
        db_open_order_count = _timed_sync_step(timings, "db_open_order_check", lambda: _db_open_order_count(exchange))
        full_scope = _timed_sync_step(timings, "protected_position_count_check", lambda: protected_position_scope_status(exchange=exchange))
        scope = dict(full_scope)
        _timed_sync_step(timings, "legacy_position_count_check", lambda: int(scope.get("legacy_open_position_count") or 0))

        broker = await _timed_async_step(
            timings,
            "broker_status_check",
            lambda: _broker_status(exchange),
            timeout_seconds=min(BROKER_STATUS_TIMEOUT_SECONDS, _remaining_critical_budget_seconds(started)),
        )
        markets = _critical_open_order_markets(exchange)
        try:
            exchange_orders = await _timed_async_step(
                timings,
                "exchange_open_order_check",
                lambda: _exchange_open_orders_for_markets(exchange, markets),
                timeout_seconds=min(EXCHANGE_OPEN_ORDER_TIMEOUT_SECONDS, 3.0, _remaining_critical_budget_seconds(started)),
            )
        except asyncio.TimeoutError:
            blockers.append(_blocker("EXCHANGE_OPEN_ORDER_CHECK_TIMEOUT"))
            refresh_status = "TIMEOUT"
            refresh_error = "Critical exchange open order check timed out."
            snapshot = build_snapshot()
            _record_refresh_notification("SAFETY_SNAPSHOT_REFRESH_TIMEOUT", snapshot, refresh_error)
            return _critical_refresh_response(snapshot, ok=False)

        equity_snapshot = await _timed_async_step(
            timings,
            "equity_snapshot_check",
            lambda: _fresh_or_refresh_equity_snapshot(exchange),
            timeout_seconds=_remaining_critical_budget_seconds(started),
        )
        current_epoch = _timed_sync_step(
            timings,
            "current_epoch_sanity_check",
            lambda: _lightweight_epoch_sanity(exchange, accounting_counts, equity_snapshot),
        )

        def decide() -> bool:
            if broker.get("broker_status") != "READY":
                blockers.append(_blocker("BROKER_NOT_READY"))
            if broker.get("emergency_status") == "ON":
                blockers.append(_blocker("EMERGENCY_STOP_ON"))
            if int(db_open_order_count or 0) != 0:
                blockers.append(_blocker("DB_OPEN_ORDER_EXISTS", int(db_open_order_count)))
            if len(exchange_orders.get("orders") or []) != 0:
                blockers.append(_blocker("EXCHANGE_OPEN_ORDER_EXISTS", len(exchange_orders.get("orders") or [])))
            if str(exchange_orders.get("status") or "").upper() not in {"SUCCESS"}:
                blockers.append(_blocker("EXCHANGE_OPEN_ORDER_CHECK_UNAVAILABLE", status=exchange_orders.get("status")))
            if int(accounting_counts.get("pending") or 0) != 0:
                blockers.append(_blocker("ACCOUNTING_PENDING", int(accounting_counts.get("pending") or 0)))
            if int(accounting_counts.get("failed") or 0) != 0:
                blockers.append(_blocker("ACCOUNTING_FAILED", int(accounting_counts.get("failed") or 0)))
            if int(scope.get("protected_open_position_count") or 0) > MAX_OPEN_POSITIONS:
                blockers.append(_blocker("PROTECTED_MAX_OPEN_POSITIONS_EXCEEDED", int(scope.get("protected_open_position_count") or 0)))
            if int(scope.get("protected_empty_slot_count") or 0) <= 0:
                blockers.append(_blocker("NO_PROTECTED_EMPTY_SLOT"))
            if not current_epoch.get("current_epoch_sanity_passed"):
                blockers.extend(current_epoch.get("current_epoch_blockers") or [_blocker("CURRENT_EPOCH_SANITY_FAILED")])
            return not blockers

        _timed_sync_step(timings, "final_gate_decision", decide)
        snapshot = build_snapshot()
        return _critical_refresh_response(snapshot, ok=bool(snapshot.get("critical_gate_allowed")))
    except asyncio.TimeoutError:
        blockers.append(_blocker("CRITICAL_SAFETY_SNAPSHOT_REFRESH_TIMEOUT"))
        refresh_status = "TIMEOUT"
        refresh_error = "Critical safety snapshot refresh timed out."
        snapshot = build_snapshot()
        _record_refresh_notification("SAFETY_SNAPSHOT_REFRESH_TIMEOUT", snapshot, refresh_error)
        return _critical_refresh_response(snapshot, ok=False)
    except Exception as exc:
        blockers.append(_blocker(f"CRITICAL_SAFETY_SNAPSHOT_REFRESH_FAILED:{exc.__class__.__name__}"))
        refresh_status = "FAILED"
        refresh_error = str(exc)[:240]
        snapshot = build_snapshot()
        _record_refresh_notification("SAFETY_SNAPSHOT_REFRESH_FAILED", snapshot, refresh_error)
        return _critical_refresh_response(snapshot, ok=False)
    finally:
        _REFRESH_LOCK.release()


def _critical_refresh_response(snapshot: dict, *, ok: bool) -> dict:
    fresh = _with_freshness(snapshot)
    return {
        "ok": ok,
        "status": (fresh or {}).get("refresh_status"),
        "snapshot": fresh,
        "critical_gate_allowed": bool((fresh or {}).get("critical_gate_allowed")),
        "protected_start_allowed": bool((fresh or {}).get("protected_start_allowed")),
        "protected_start_blockers": (fresh or {}).get("protected_start_blockers") or [],
        "protected_start_warnings": (fresh or {}).get("protected_start_warnings") or [],
        "optional_diagnostics_status": (fresh or {}).get("optional_diagnostics_status") or "UNKNOWN",
        "refresh_step_timings": (fresh or {}).get("refresh_step_timings") or [],
        "slowest_step": (fresh or {}).get("slowest_step") or {},
        "timeout_step": (fresh or {}).get("timeout_step") or {},
        "refresh_duration_ms": (fresh or {}).get("refresh_duration_ms"),
        "refresh_error_detail": (fresh or {}).get("refresh_error_detail") or "",
    }


def _with_freshness(snapshot: dict | None) -> dict | None:
    if not snapshot:
        return None
    now = datetime.now(timezone.utc)
    created = _parse_utc(str(snapshot.get("created_at_utc") or ""))
    expires = _parse_utc(str(snapshot.get("expires_at_utc") or ""))
    snapshot["is_fresh"] = bool(expires and expires > now)
    snapshot["age_seconds"] = int((now - created).total_seconds()) if created else None
    snapshot["refresh_in_progress"] = _REFRESH_LOCK.locked()
    snapshot["refresh_step_timings"] = snapshot.get("critical_step_timings") or []
    snapshot["slowest_step"] = snapshot.get("slowest_step") or {}
    snapshot["timeout_step"] = snapshot.get("timeout_step") or {}
    snapshot["refresh_error_detail"] = snapshot.get("refresh_error_detail") or snapshot.get("refresh_error") or ""
    return snapshot
