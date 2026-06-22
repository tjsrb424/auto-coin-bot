from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

from app.database import (
    acquire_scheduler_task_lock,
    finish_scheduler_task,
    load_active_strategy_selection,
    load_candidate_strategies,
    load_scheduler_task_state,
    load_scheduler_task_states,
)
from app.strategy_discovery_scheduler import (
    DEEP_VALIDATION_TASK,
    FAST_VALIDATION_TASK,
    PROMOTION_TASK,
    SCAN_TASK,
    discovery_scheduler_config,
    discovery_scheduler_status,
    run_deep_validation_scheduler_once,
    run_fast_validation_scheduler_once,
    run_market_scan_scheduler_once,
    run_promotion_selector_scheduler_once,
)

ORCHESTRATOR_TASK = "autonomous_orchestrator"
ORCHESTRATOR_REASONS = {
    "SERVER_STARTUP",
    "RUNTIME_STARTED",
    "SCHEDULED",
    "CANDIDATE_CREATED",
    "LIVE_ELIGIBLE_CREATED",
    "MANUAL_RUN_NOW",
}


def _utc_now_dt() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _utc_now() -> str:
    return _utc_now_dt().isoformat().replace("+00:00", "Z")


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(value, maximum)
    return value


def autonomous_orchestrator_config() -> dict:
    return {
        "enabled": _bool_env("AUTO_AUTONOMOUS_ORCHESTRATOR_ENABLED", True),
        "bootstrap_enabled": _bool_env("AUTO_AUTONOMOUS_ORCHESTRATOR_BOOTSTRAP_ENABLED", True),
        "on_start_enabled": _bool_env("AUTO_AUTONOMOUS_ORCHESTRATOR_ON_START_ENABLED", True),
        "interval_minutes": _int_env("AUTO_AUTONOMOUS_ORCHESTRATOR_INTERVAL_MINUTES", 5, minimum=1),
        "lock_ttl_seconds": _int_env("AUTO_AUTONOMOUS_ORCHESTRATOR_LOCK_TTL_SECONDS", 1800, minimum=60, maximum=1800),
    }


def _next_orchestrator_run_at(config: dict | None = None) -> str:
    cfg = config or autonomous_orchestrator_config()
    return (_utc_now_dt() + timedelta(minutes=int(cfg["interval_minutes"]))).isoformat().replace("+00:00", "Z")


def _state_is_running(task_name: str) -> bool:
    state = load_scheduler_task_state(task_name)
    if not state or str(state.get("status") or "").upper() != "RUNNING":
        return False
    lock_until = _parse_utc(state.get("lock_until"))
    return bool(lock_until and lock_until > _utc_now_dt())


def _is_due(task_name: str) -> bool:
    state = load_scheduler_task_state(task_name)
    if not state:
        return True
    next_run_at = _parse_utc(state.get("next_run_at"))
    return next_run_at is None or next_run_at <= _utc_now_dt()


def _deep_due() -> bool:
    state = load_scheduler_task_state(DEEP_VALIDATION_TASK)
    next_run_at = _parse_utc((state or {}).get("next_run_at"))
    if next_run_at:
        return next_run_at <= _utc_now_dt()
    now_kst = datetime.now(timezone(timedelta(hours=9))).replace(microsecond=0)
    today_4am = now_kst.replace(hour=4, minute=0, second=0)
    if now_kst < today_4am:
        return False
    last_finished_at = _parse_utc((state or {}).get("last_finished_at"))
    if not last_finished_at:
        return True
    return last_finished_at.astimezone(timezone(timedelta(hours=9))).date() != now_kst.date()


def _task_status(task_name: str, status: str, **extra: object) -> dict:
    return {"task_name": task_name, "status": status, **extra}


async def _run_child(task_name: str, runner) -> dict:
    if _state_is_running(task_name):
        return _task_status(task_name, "SKIPPED", reason="CHILD_RUNNING")
    return await runner()


def _saved_candidate_count(child_result: dict) -> int:
    result = child_result.get("last_result") if isinstance(child_result.get("last_result"), dict) else child_result
    try:
        return int((result or {}).get("saved_candidate_count") or 0)
    except (TypeError, ValueError):
        return 0


def _scheduled_children(reason: str) -> list[tuple[str, object]]:
    normalized = reason.upper()
    children: list[tuple[str, object]] = []
    if normalized in {"SERVER_STARTUP", "MANUAL_RUN_NOW"}:
        return [
            (SCAN_TASK, run_market_scan_scheduler_once),
            (FAST_VALIDATION_TASK, run_fast_validation_scheduler_once),
            (PROMOTION_TASK, run_promotion_selector_scheduler_once),
        ]
    if normalized == "RUNTIME_STARTED":
        if _is_due(SCAN_TASK):
            children.append((SCAN_TASK, run_market_scan_scheduler_once))
        if _is_due(FAST_VALIDATION_TASK):
            children.append((FAST_VALIDATION_TASK, run_fast_validation_scheduler_once))
        children.append((PROMOTION_TASK, run_promotion_selector_scheduler_once))
        return children
    if normalized in {"CANDIDATE_CREATED", "LIVE_ELIGIBLE_CREATED"}:
        return [(PROMOTION_TASK, run_promotion_selector_scheduler_once)]
    if normalized == "SCHEDULED":
        if _is_due(SCAN_TASK):
            children.append((SCAN_TASK, run_market_scan_scheduler_once))
        if _is_due(FAST_VALIDATION_TASK):
            children.append((FAST_VALIDATION_TASK, run_fast_validation_scheduler_once))
        if _is_due(PROMOTION_TASK):
            children.append((PROMOTION_TASK, run_promotion_selector_scheduler_once))
        if _deep_due():
            children.append((DEEP_VALIDATION_TASK, run_deep_validation_scheduler_once))
    return children


def _candidate_summary() -> dict:
    eligible = load_candidate_strategies(5, statuses=["LIVE_ELIGIBLE"])
    active = load_candidate_strategies(5, statuses=["LIVE_ACTIVE"])
    active_selection = load_active_strategy_selection()
    return {
        "recent_live_eligible": [
            {
                "id": item["id"],
                "market": item["market"],
                "strategy": item["strategy"],
                "unit": item.get("unit"),
                "status": item.get("status"),
                "score": item.get("score"),
            }
            for item in eligible
        ],
        "recent_live_active": [
            {
                "id": item["id"],
                "market": item["market"],
                "strategy": item["strategy"],
                "unit": item.get("unit"),
                "status": item.get("status"),
                "score": item.get("score"),
            }
            for item in active
        ],
        "active_selection": active_selection,
    }


async def run_autonomous_orchestrator_once_async(reason: str = "SCHEDULED") -> dict:
    config = autonomous_orchestrator_config()
    normalized_reason = str(reason or "SCHEDULED").upper()
    if normalized_reason not in ORCHESTRATOR_REASONS:
        normalized_reason = "SCHEDULED"
    if not config["enabled"]:
        return finish_scheduler_task(
            ORCHESTRATOR_TASK,
            status="DISABLED",
            result={"reason": normalized_reason, "skip_reason": "ORCHESTRATOR_DISABLED"},
            next_run_at=_next_orchestrator_run_at(config),
        )

    acquired, current = acquire_scheduler_task_lock(
        ORCHESTRATOR_TASK,
        owner=f"orchestrator:{normalized_reason}",
        ttl_seconds=int(config["lock_ttl_seconds"]),
    )
    if not acquired:
        return {
            "task_name": ORCHESTRATOR_TASK,
            "status": "SKIPPED_LOCKED",
            "reason": normalized_reason,
            "current": current,
        }

    children = _scheduled_children(normalized_reason)
    child_results: list[dict] = []
    try:
        executed_tasks: set[str] = set()
        for task_name, runner in children:
            child_result = await _run_child(task_name, runner)
            child_results.append(child_result)
            executed_tasks.add(task_name)
            if (
                task_name == FAST_VALIDATION_TASK
                and _saved_candidate_count(child_result) > 0
                and PROMOTION_TASK not in executed_tasks
            ):
                promotion_result = await _run_child(PROMOTION_TASK, run_promotion_selector_scheduler_once)
                child_results.append(promotion_result)
                executed_tasks.add(PROMOTION_TASK)
        discovery_states = {
            item["task_name"]: item
            for item in load_scheduler_task_states([SCAN_TASK, FAST_VALIDATION_TASK, DEEP_VALIDATION_TASK, PROMOTION_TASK])
        }
        result = {
            "reason": normalized_reason,
            "executed_count": sum(1 for item in child_results if str(item.get("status", "")).upper() not in {"SKIPPED", "SKIPPED_LOCKED"}),
            "skipped_count": sum(1 for item in child_results if str(item.get("status", "")).upper().startswith("SKIPPED")),
            "children": child_results,
            "discovery": discovery_states,
            **_candidate_summary(),
        }
        return finish_scheduler_task(
            ORCHESTRATOR_TASK,
            status="COMPLETED",
            result=result,
            next_run_at=_next_orchestrator_run_at(config),
        )
    except Exception as exc:
        return finish_scheduler_task(
            ORCHESTRATOR_TASK,
            status="FAILED",
            result={"reason": normalized_reason, "children": child_results, "error_type": exc.__class__.__name__},
            error=str(exc),
            next_run_at=_next_orchestrator_run_at(config),
        )


def run_autonomous_orchestrator_once(reason: str = "SCHEDULED") -> dict:
    return asyncio.run(run_autonomous_orchestrator_once_async(reason=reason))


def run_autonomous_orchestrator_background(reason: str = "SCHEDULED") -> None:
    run_autonomous_orchestrator_once(reason=reason)


def autonomous_orchestrator_status() -> dict:
    states = {
        item["task_name"]: item
        for item in load_scheduler_task_states([ORCHESTRATOR_TASK, SCAN_TASK, FAST_VALIDATION_TASK, DEEP_VALIDATION_TASK, PROMOTION_TASK])
    }
    discovery = discovery_scheduler_status()
    config = autonomous_orchestrator_config()
    orchestrator = {
        "task_name": ORCHESTRATOR_TASK,
        "enabled": config["enabled"],
        "interval_minutes": config["interval_minutes"],
        "status": "IDLE",
        "last_result": {},
        **(states.get(ORCHESTRATOR_TASK) or {}),
    }
    return {
        "config": config,
        "orchestrator": orchestrator,
        "scan": {**discovery["scan"], **(states.get(SCAN_TASK) or {})},
        "fast_validation": {**discovery["fast_validation"], **(states.get(FAST_VALIDATION_TASK) or {})},
        "deep_validation": {**discovery["deep_validation"], **(states.get(DEEP_VALIDATION_TASK) or {})},
        "promotion_selector": {**discovery["promotion_selector"], **(states.get(PROMOTION_TASK) or {})},
        **_candidate_summary(),
    }
