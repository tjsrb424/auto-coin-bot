from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from math import ceil

from app.database import (
    acquire_scheduler_task_lock,
    count_candidate_strategies_created_since,
    find_duplicate_candidate_strategy,
    finish_scheduler_task,
    ensure_required_schema,
    insert_candles,
    load_bot_operation_policy,
    load_candidate_strategies,
    load_candles_between,
    load_market_universe,
    load_scheduler_task_states,
    save_candidate_strategy,
    save_strategy_validation_run,
)
from app.market_scanner import scan_market_universe
from app.strategy_promotion_pipeline import run_strategy_promotion_pipeline_async
from app.strategy_validation import run_strategy_validation
from app.upbit import fetch_minute_candles

SCAN_TASK = "market_scan"
FAST_VALIDATION_TASK = "fast_validation"
DEEP_VALIDATION_TASK = "deep_validation"
PROMOTION_TASK = "promotion_selector"
DISCOVERY_TASKS = [SCAN_TASK, FAST_VALIDATION_TASK, DEEP_VALIDATION_TASK, PROMOTION_TASK]
ALLOWED_STRATEGIES = {"ma_cross", "rsi", "volatility_breakout"}
AUTO_CANDIDATE_STATUSES = ["BACKTEST_PASSED", "SHADOW_RUNNING", "SHADOW_PASSED", "LIVE_ELIGIBLE"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(value, maximum)
    return value


def _float_env(name: str, default: float, *, minimum: float = 0.0) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, value)


def _csv_env(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


def _int_csv_env(name: str, default: list[int]) -> list[int]:
    values: list[int] = []
    for item in _csv_env(name, [str(value) for value in default]):
        try:
            values.append(int(item))
        except ValueError:
            continue
    return values or default


def _minutes_env(name: str, default: int, *, minimum: int = 1) -> int:
    return _int_env(name, default, minimum=minimum)


def _is_database_locked_error(exc: Exception) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "database is locked" in str(exc).lower()


def _missing_table_from_error(exc: Exception) -> str:
    message = str(exc)
    marker = "no such table:"
    if marker in message.lower():
        return message.lower().split(marker, 1)[1].strip().split()[0].strip("`'\"")
    return ""


async def _run_with_sqlite_retry(task_name: str, operation):
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            return await operation()
        except Exception as exc:
            if not _is_database_locked_error(exc):
                raise
            last_error = exc
            if attempt < 3:
                await asyncio.sleep(0.25 * attempt)
    assert last_error is not None
    raise last_error


def _next_run_after_minutes(minutes: int) -> str:
    return (datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=max(1, minutes))).isoformat().replace("+00:00", "Z")


def _next_deep_run_at() -> str:
    now_kst = datetime.now(timezone(timedelta(hours=9))).replace(microsecond=0)
    next_kst = now_kst.replace(hour=4, minute=0, second=0)
    if next_kst <= now_kst:
        next_kst += timedelta(days=1)
    return next_kst.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _today_utc_start() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")


def _is_weekly_deep_day(config: dict) -> bool:
    weekday = _int_env("AUTO_DEEP_VALIDATION_180D_WEEKDAY", 0, minimum=0, maximum=6)
    now_kst = datetime.now(timezone(timedelta(hours=9)))
    return now_kst.weekday() == weekday


def discovery_scheduler_config() -> dict:
    exchange = os.getenv("AUTO_DISCOVERY_EXCHANGE", os.getenv("AUTO_ALLOWED_EXCHANGE", "bithumb")).strip().lower() or "bithumb"
    strategies = [strategy for strategy in ["ma_cross", "rsi", "volatility_breakout"] if strategy in ALLOWED_STRATEGIES]
    return {
        "enabled": _bool_env("AUTO_DISCOVERY_SCHEDULER_ENABLED", True),
        "exchange": exchange if exchange in {"upbit", "bithumb"} else "bithumb",
        "scan_enabled": _bool_env("AUTO_MARKET_SCAN_SCHEDULER_ENABLED", True),
        "scan_interval_minutes": _minutes_env("AUTO_MARKET_SCAN_INTERVAL_MINUTES", 20, minimum=5),
        "scan_top_n": _int_env("AUTO_MARKET_SCAN_TOP_N", 10, minimum=1, maximum=20),
        "scan_max_candidates": _int_env("AUTO_MARKET_SCAN_MAX_CANDIDATES", 20, minimum=1, maximum=40),
        "scan_min_24h_trade_price_krw": _float_env("AUTO_MARKET_SCAN_MIN_24H_TRADE_PRICE_KRW", 500_000_000.0),
        "fast_enabled": _bool_env("AUTO_FAST_VALIDATION_SCHEDULER_ENABLED", True),
        "fast_interval_minutes": _minutes_env("AUTO_FAST_VALIDATION_INTERVAL_MINUTES", 60, minimum=15),
        "fast_max_markets": _int_env("AUTO_FAST_VALIDATION_MAX_MARKETS", 8, minimum=5, maximum=8),
        "fast_strategies": strategies,
        "fast_timeframes": _int_csv_env("AUTO_FAST_VALIDATION_TIMEFRAMES", [5, 15]),
        "fast_periods": _csv_env("AUTO_FAST_VALIDATION_PERIODS", ["7d", "30d"]),
        "deep_enabled": _bool_env("AUTO_DEEP_VALIDATION_ENABLED", True),
        "deep_max_markets": _int_env("AUTO_DEEP_VALIDATION_MAX_MARKETS", 20, minimum=10, maximum=20),
        "deep_timeframes": _int_csv_env("AUTO_DEEP_VALIDATION_TIMEFRAMES", [5, 15, 60]),
        "deep_periods": _csv_env("AUTO_DEEP_VALIDATION_PERIODS", ["7d", "30d", "90d"]),
        "deep_weekly_period": os.getenv("AUTO_DEEP_VALIDATION_WEEKLY_PERIOD", "180d").strip() or "180d",
        "validation_request_delay_seconds": _float_env("AUTO_VALIDATION_REQUEST_DELAY_SECONDS", 0.2),
        "validation_min_score": _float_env("AUTO_VALIDATION_MIN_SCORE", 70.0),
        "max_auto_save_candidates_per_run": _int_env("AUTO_VALIDATION_MAX_SAVE_PER_RUN", 3, minimum=0, maximum=20),
        "max_auto_save_candidates_per_day": _int_env("AUTO_VALIDATION_MAX_SAVE_PER_DAY", 20, minimum=1, maximum=100),
        "max_backtest_passed_candidates": _int_env("AUTO_VALIDATION_MAX_BACKTEST_PASSED", 50, minimum=1),
        "promotion_enabled": _bool_env("AUTO_PROMOTION_PIPELINE_ENABLED", True),
        "promotion_interval_minutes": _minutes_env("AUTO_PROMOTION_INTERVAL_MINUTES", 5, minimum=1),
        "lock_ttl_seconds": _int_env("AUTO_DISCOVERY_LOCK_TTL_SECONDS", 7200, minimum=60),
    }


def _format_candle_time(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _format_upbit_to(value: datetime) -> str:
    return value.replace(tzinfo=None, microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


async def _load_period_candles(market: str, unit: int, start_time_utc: str, end_time_utc: str) -> list[dict]:
    start = _parse_utc(start_time_utc)
    end = _parse_utc(end_time_utc)
    if end <= start:
        raise ValueError("end time must be later than start time")
    expected_count = ceil((end - start).total_seconds() / (unit * 60)) + 5
    fetch_count = min(max(expected_count, 30), 20000)
    fresh = await fetch_minute_candles(market=market, unit=unit, count=fetch_count, to=_format_upbit_to(end))
    insert_candles([{**candle, "unit": candle.get("unit", unit)} for candle in fresh])
    candles = load_candles_between(market, unit, _format_candle_time(start), _format_candle_time(end))
    if len(candles) < 30:
        raise ValueError("not enough candles for scheduled validation")
    return candles


def _fatal_validation_warnings(warnings: list[str]) -> list[str]:
    fatal_keywords = ["MDD", "loss", "loss after fees", "insufficient", "API", "volatility", "liquidity"]
    return [warning for warning in warnings if any(keyword.lower() in str(warning).lower() for keyword in fatal_keywords)]


def _validation_row_passes_candidate_gate(row: dict, *, min_score: float = 70.0) -> bool:
    metrics = row.get("metrics") or {}
    warnings = [str(item) for item in row.get("warnings") or []]
    return (
        float(row.get("stability_score") or 0.0) >= min_score
        and float(metrics.get("total_return") or 0.0) > 0
        and float(metrics.get("mdd") or 0.0) <= 0.15
        and not _fatal_validation_warnings(warnings)
    )


def _candidate_from_validation_row(row: dict, *, status: str = "BACKTEST_PASSED") -> dict:
    metrics = row.get("metrics") or {}
    warnings = [str(item) for item in row.get("warnings") or []]
    return {
        "name": f"{row['market']} {row['strategy']} {row['unit']}m {float(row.get('stability_score') or 0):.2f}pt",
        "description": "Auto-saved from scheduled multi-market validation.",
        "strategy": row["strategy"],
        "parameters": row.get("parameters") or {},
        "unit": int(row["unit"]),
        "market": row["market"],
        "backtest_period": str(row.get("period_label") or "multi-market"),
        "score": float(row.get("stability_score") or metrics.get("score") or 0.0),
        "backtest_total_return": float(metrics.get("total_return") or 0.0),
        "backtest_mdd": float(metrics.get("mdd") or 0.0),
        "backtest_win_rate": float(metrics.get("win_rate") or 0.0),
        "backtest_profit_factor": float(metrics.get("profit_factor") or 0.0),
        "backtest_trade_count": int(metrics.get("trade_count") or 0),
        "backtest_average_trade_pnl": float(metrics.get("total_return") or 0.0) / int(metrics.get("trade_count") or 1),
        "warning": ", ".join(warnings),
        "status": status,
    }


def _annotate_validation_decisions(rows: list[dict], *, min_score: float) -> list[dict]:
    return [
        {**row, "decision": "AUTO_SAVE" if _validation_row_passes_candidate_gate(row, min_score=min_score) else "REJECT"}
        for row in rows
    ]


def _candidate_pool_count() -> int:
    return len(load_candidate_strategies(1000, statuses=AUTO_CANDIDATE_STATUSES))


def _save_validation_candidates(ranking: list[dict], config: dict) -> tuple[list[dict], list[dict]]:
    saved: list[dict] = []
    skipped: list[dict] = []
    pool_count = _candidate_pool_count()
    daily_count = count_candidate_strategies_created_since(_today_utc_start(), statuses=AUTO_CANDIDATE_STATUSES)
    max_pool = int(config["max_backtest_passed_candidates"])
    max_daily = int(config["max_auto_save_candidates_per_day"])
    max_per_run = int(config["max_auto_save_candidates_per_run"])
    if max_per_run <= 0:
        return saved, [{"reason": "AUTO_SAVE_DISABLED"}]

    for row in ranking:
        if len(saved) >= max_per_run:
            break
        if pool_count >= max_pool:
            skipped.append({"market": row.get("market"), "reason": "CANDIDATE_POOL_LIMIT"})
            break
        if daily_count + len(saved) >= max_daily:
            skipped.append({"market": row.get("market"), "reason": "DAILY_CANDIDATE_SAVE_LIMIT"})
            break
        if row.get("decision") != "AUTO_SAVE":
            continue
        candidate = _candidate_from_validation_row(row, status="BACKTEST_PASSED")
        duplicate = find_duplicate_candidate_strategy(candidate, statuses=AUTO_CANDIDATE_STATUSES)
        if duplicate:
            skipped.append({"market": candidate["market"], "candidate_id": duplicate["id"], "reason": "DUPLICATE_CANDIDATE"})
            continue
        candidate_id = save_candidate_strategy(candidate)
        saved.append({**candidate, "id": candidate_id})
        pool_count += 1
    return saved, skipped


def _validation_task_config(config: dict, mode: str) -> dict:
    if mode == "deep":
        periods = list(config["deep_periods"])
        if _is_weekly_deep_day(config) and config["deep_weekly_period"] not in periods:
            periods.append(config["deep_weekly_period"])
        return {
            **config,
            "task_name": DEEP_VALIDATION_TASK,
            "mode": "deep",
            "enabled": bool(config["enabled"] and config["deep_enabled"]),
            "max_markets": config["deep_max_markets"],
            "strategies": ["ma_cross", "rsi", "volatility_breakout"],
            "timeframes": config["deep_timeframes"],
            "periods": periods,
            "next_run_at": _next_deep_run_at(),
        }
    return {
        **config,
        "task_name": FAST_VALIDATION_TASK,
        "mode": "fast",
        "enabled": bool(config["enabled"] and config["fast_enabled"]),
        "max_markets": config["fast_max_markets"],
        "strategies": config["fast_strategies"],
        "timeframes": config["fast_timeframes"],
        "periods": config["fast_periods"],
        "next_run_at": _next_run_after_minutes(int(config["fast_interval_minutes"])),
    }


async def run_market_scan_scheduler_once() -> dict:
    config = discovery_scheduler_config()
    if not config["enabled"] or not config["scan_enabled"]:
        return finish_scheduler_task(
            SCAN_TASK,
            status="DISABLED",
            result={"reason": "SCHEDULER_DISABLED"},
            next_run_at=_next_run_after_minutes(int(config["scan_interval_minutes"])),
        )
    acquired, current = acquire_scheduler_task_lock(SCAN_TASK, ttl_seconds=int(config["lock_ttl_seconds"]))
    if not acquired:
        return {"task_name": SCAN_TASK, "status": "SKIPPED", "reason": "LOCKED", "current": current}
    try:
        result = await _run_with_sqlite_retry(
            SCAN_TASK,
            lambda: scan_market_universe(
                exchange=str(config["exchange"]),
                top_n=int(config["scan_top_n"]),
                max_candidates=int(config["scan_max_candidates"]),
                min_24h_trade_price_krw=float(config["scan_min_24h_trade_price_krw"]),
            ),
        )
        summary = {
            "accepted_count": len(result.get("accepted") or []),
            "rejected_count": len(result.get("rejected") or []),
            "market_count": result.get("market_count", 0),
            "persisted_count": result.get("persisted_count", 0),
            "scanned_at": result.get("scanned_at"),
            "skip_reason": "",
        }
        return finish_scheduler_task(SCAN_TASK, status="COMPLETED", result=summary, next_run_at=_next_run_after_minutes(int(config["scan_interval_minutes"])))
    except Exception as exc:
        return finish_scheduler_task(
            SCAN_TASK,
            status="FAILED",
            result={"error_type": exc.__class__.__name__},
            error=str(exc),
            next_run_at=_next_run_after_minutes(int(config["scan_interval_minutes"])),
        )


async def _run_validation_scheduler_once(mode: str) -> dict:
    config = _validation_task_config(discovery_scheduler_config(), mode)
    task_name = str(config["task_name"])
    if not config["enabled"]:
        return finish_scheduler_task(task_name, status="DISABLED", result={"reason": "SCHEDULER_DISABLED"}, next_run_at=str(config["next_run_at"]))
    acquired, current = acquire_scheduler_task_lock(task_name, ttl_seconds=int(config["lock_ttl_seconds"]))
    if not acquired:
        return {"task_name": task_name, "status": "SKIPPED", "reason": "LOCKED", "current": current}
    started_at = _utc_now()
    try:
        markets = [
            str(item["market"])
            for item in load_market_universe(
                exchange=str(config["exchange"]),
                enabled_only=True,
                auto_selectable_only=True,
                limit=int(config["max_markets"]),
            )
        ][: int(config["max_markets"])]
        if not markets:
            return finish_scheduler_task(task_name, status="SKIPPED", result={"reason": "NO_AUTO_SELECTABLE_MARKETS"}, next_run_at=str(config["next_run_at"]))

        all_rows: list[dict] = []
        errors: list[dict] = []
        for market in markets:
            for strategy in config["strategies"]:
                try:
                    result = await _run_with_sqlite_retry(
                        task_name,
                        lambda market=market, strategy=strategy: run_strategy_validation(
                            market=market,
                            strategy=strategy,
                            timeframes=list(config["timeframes"]),
                            periods=list(config["periods"]),
                            custom_start_time_utc=None,
                            custom_end_time_utc=None,
                            base_settings={},
                            risk={"initial_cash": 1_000_000, "fee_rate": 0.0005, "slippage_rate": 0.0005},
                            load_period_candles=_load_period_candles,
                        ),
                    )
                    all_rows.extend(_annotate_validation_decisions(result["rows"], min_score=float(config["validation_min_score"])))
                except Exception as exc:
                    errors.append({"market": market, "strategy": strategy, "error": str(exc), "error_type": exc.__class__.__name__})
                delay = float(config["validation_request_delay_seconds"])
                if delay > 0:
                    await asyncio.sleep(delay)

        ranking = sorted(all_rows, key=lambda row: float(row.get("stability_score") or 0.0), reverse=True)
        saved_candidates, skipped_candidates = _save_validation_candidates(ranking, config)
        summary = {
            "mode": config["mode"],
            "market_count": len(markets),
            "strategy_count": len(config["strategies"]),
            "timeframes": config["timeframes"],
            "periods": config["periods"],
            "row_count": len(ranking),
            "saved_candidate_count": len(saved_candidates),
            "skipped_candidate_count": len(skipped_candidates),
            "error_count": len(errors),
        }
        run_id = save_strategy_validation_run(
            {
                "exchange": config["exchange"],
                "market_count": len(markets),
                "strategy_count": len(config["strategies"]),
                "timeframes": config["timeframes"],
                "periods": config["periods"],
                "risk": {"initial_cash": 1_000_000, "fee_rate": 0.0005, "slippage_rate": 0.0005},
                "request": {"source": f"{config['mode']}_scheduler", **config},
                "summary": summary,
                "status": "COMPLETED_WITH_ERRORS" if errors else "COMPLETED",
                "started_at": started_at,
                "finished_at": _utc_now(),
            },
            ranking,
        )
        result_summary = {
            **summary,
            "run_id": run_id,
            "markets": markets,
            "saved_candidates": [{"id": item["id"], "market": item["market"], "strategy": item["strategy"], "score": item["score"]} for item in saved_candidates],
            "skipped_candidates": skipped_candidates[:10],
            "skip_reason": skipped_candidates[0]["reason"] if skipped_candidates else "",
        }
        return finish_scheduler_task(
            task_name,
            status="COMPLETED_WITH_ERRORS" if errors else "COMPLETED",
            result=result_summary,
            next_run_at=str(config["next_run_at"]),
        )
    except Exception as exc:
        return finish_scheduler_task(
            task_name,
            status="FAILED",
            result={"error_type": exc.__class__.__name__},
            error=str(exc),
            next_run_at=str(config["next_run_at"]),
        )


async def run_fast_validation_scheduler_once() -> dict:
    return await _run_validation_scheduler_once("fast")


async def run_deep_validation_scheduler_once() -> dict:
    return await _run_validation_scheduler_once("deep")


async def run_promotion_selector_scheduler_once() -> dict:
    config = discovery_scheduler_config()
    if not config["enabled"] or not config["promotion_enabled"]:
        return finish_scheduler_task(
            PROMOTION_TASK,
            status="DISABLED",
            result={"reason": "SCHEDULER_DISABLED"},
            next_run_at=_next_run_after_minutes(int(config["promotion_interval_minutes"])),
        )
    acquired, current = acquire_scheduler_task_lock(PROMOTION_TASK, ttl_seconds=int(config["lock_ttl_seconds"]))
    if not acquired:
        return {"task_name": PROMOTION_TASK, "status": "SKIPPED", "reason": "LOCKED", "current": current}
    try:
        try:
            result = await _run_with_sqlite_retry(
                PROMOTION_TASK,
                lambda: run_strategy_promotion_pipeline_async(exchange=str(config["exchange"])),
            )
        except Exception as exc:
            missing_table = _missing_table_from_error(exc)
            if not missing_table:
                raise
            schema = ensure_required_schema(repair=True)
            return finish_scheduler_task(
                PROMOTION_TASK,
                status="FAILED",
                result={
                    "error_type": exc.__class__.__name__,
                    "skip_reason": f"DB_SCHEMA_MISSING: {missing_table}",
                    "missing_table": missing_table,
                    "schema_status": schema.get("schema_status"),
                    "missing_tables": schema.get("missing_tables", []),
                    "repair_status": schema.get("repair_status"),
                },
                error=f"DB_SCHEMA_MISSING: {missing_table}",
                next_run_at=_next_run_after_minutes(int(config["promotion_interval_minutes"])),
            )
        selector = result.get("selector", {})
        best = selector.get("best_candidate") or {}
        policy = load_bot_operation_policy(best.get("market") or "KRW-BTC")
        summary = {
            "enrolled_count": len(result.get("enrolled", {}).get("enrolled", [])),
            "promoted_count": len(result.get("promoted", {}).get("promoted", [])),
            "blocked_count": len(result.get("promoted", {}).get("blocked", [])),
            "selector_decision": selector.get("decision"),
            "selector_blockers": selector.get("blockers", [])[:6],
            "auto_trading_enabled": bool(policy.get("auto_trading_enabled")),
            "skip_reason": "AUTO_TRADING_DISABLED_SELECTOR_NOT_APPLIED" if not policy.get("auto_trading_enabled") else "",
        }
        return finish_scheduler_task(PROMOTION_TASK, status="COMPLETED", result=summary, next_run_at=_next_run_after_minutes(int(config["promotion_interval_minutes"])))
    except Exception as exc:
        return finish_scheduler_task(
            PROMOTION_TASK,
            status="FAILED",
            result={"error_type": exc.__class__.__name__},
            error=str(exc),
            next_run_at=_next_run_after_minutes(int(config["promotion_interval_minutes"])),
        )


def discovery_scheduler_status() -> dict:
    config = discovery_scheduler_config()
    states = {item["task_name"]: item for item in load_scheduler_task_states(DISCOVERY_TASKS)}
    return {
        "enabled": bool(config["enabled"]),
        "exchange": config["exchange"],
        "scan": {
            "enabled": bool(config["scan_enabled"]),
            "interval_minutes": config["scan_interval_minutes"],
            **(states.get(SCAN_TASK) or {"task_name": SCAN_TASK, "status": "IDLE", "last_result": {}}),
        },
        "fast_validation": {
            "enabled": bool(config["fast_enabled"]),
            "interval_minutes": config["fast_interval_minutes"],
            "max_markets": config["fast_max_markets"],
            "max_save_per_run": config["max_auto_save_candidates_per_run"],
            "max_save_per_day": config["max_auto_save_candidates_per_day"],
            **(states.get(FAST_VALIDATION_TASK) or {"task_name": FAST_VALIDATION_TASK, "status": "IDLE", "last_result": {}}),
        },
        "deep_validation": {
            "enabled": bool(config["deep_enabled"]),
            "interval_minutes": 1440,
            "max_markets": config["deep_max_markets"],
            "max_save_per_run": config["max_auto_save_candidates_per_run"],
            "max_save_per_day": config["max_auto_save_candidates_per_day"],
            **(states.get(DEEP_VALIDATION_TASK) or {"task_name": DEEP_VALIDATION_TASK, "status": "IDLE", "last_result": {}}),
        },
        "promotion_selector": {
            "enabled": bool(config["promotion_enabled"]),
            "interval_minutes": config["promotion_interval_minutes"],
            **(states.get(PROMOTION_TASK) or {"task_name": PROMOTION_TASK, "status": "IDLE", "last_result": {}}),
        },
    }


def run_market_scan_scheduler_tick() -> None:
    asyncio.run(run_market_scan_scheduler_once())


def run_fast_validation_scheduler_tick() -> None:
    asyncio.run(run_fast_validation_scheduler_once())


def run_deep_validation_scheduler_tick() -> None:
    asyncio.run(run_deep_validation_scheduler_once())


def run_promotion_selector_scheduler_tick() -> None:
    asyncio.run(run_promotion_selector_scheduler_once())
