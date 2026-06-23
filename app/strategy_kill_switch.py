from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from app.execution_quality import summarize_execution_quality


@dataclass(frozen=True)
class StrategyKillSwitchConfig:
    enabled: bool
    expectancy_window: int
    max_consecutive_losses: int
    market_cooldown_hours: int
    max_cancel_rate: float
    max_average_slippage_pct: float

    @classmethod
    def from_env(cls) -> "StrategyKillSwitchConfig":
        return cls(
            enabled=_env_bool("STRATEGY_KILL_SWITCH_ENABLED", True),
            expectancy_window=_int_env("STRATEGY_KILL_EXPECTANCY_WINDOW", 10),
            max_consecutive_losses=_int_env("STRATEGY_KILL_MAX_CONSECUTIVE_LOSSES", 3),
            market_cooldown_hours=_int_env("STRATEGY_KILL_MARKET_COOLDOWN_HOURS", 6),
            max_cancel_rate=_float(os.getenv("STRATEGY_KILL_MAX_CANCEL_RATE"), 0.6),
            max_average_slippage_pct=_float(os.getenv("STRATEGY_KILL_MAX_AVERAGE_SLIPPAGE_PCT"), 0.5),
        )


def evaluate_strategy_kill_switch(
    *,
    orders: list[dict],
    execution_rows: list[dict] | None = None,
    balance_mismatch_detected: bool = False,
    exit_failed: bool = False,
    config: StrategyKillSwitchConfig | None = None,
) -> dict:
    cfg = config or StrategyKillSwitchConfig.from_env()
    if not cfg.enabled:
        return {"enabled": False, "action": "NONE", "blockers": [], "kill_switch_status": "DISABLED"}
    recent = list(orders)[-cfg.expectancy_window :]
    pnls = [_float(row.get("actual_pnl", row.get("realized_pnl"))) for row in recent]
    expectancy = sum(pnls) / len(pnls) if pnls else 0.0
    consecutive_losses = _consecutive_losses(list(reversed(recent)))
    stop_loss_streak = _stop_loss_streak(list(reversed(recent)))
    execution_summary = summarize_execution_quality(execution_rows or [])
    blockers: list[str] = []
    if len(recent) >= cfg.expectancy_window and expectancy < 0:
        blockers.append("KILL_EXPECTANCY_NEGATIVE")
    if consecutive_losses >= cfg.max_consecutive_losses:
        blockers.append("KILL_CONSECUTIVE_LOSSES")
    if stop_loss_streak >= 2:
        blockers.append("KILL_REPEATED_STOP_LOSS")
    if execution_summary["order_count"] > 0 and execution_summary["cancel_rate"] > cfg.max_cancel_rate:
        blockers.append("KILL_CANCEL_RATE_HIGH")
    if execution_summary["order_count"] > 0 and execution_summary["average_slippage_pct"] > cfg.max_average_slippage_pct:
        blockers.append("KILL_SLIPPAGE_HIGH")
    if exit_failed:
        blockers.append("KILL_EXIT_FAILED")
    if balance_mismatch_detected:
        blockers.append("KILL_BALANCE_MISMATCH")
    action = "PAUSE_STRATEGY" if blockers else "NONE"
    return {
        "enabled": True,
        "action": action,
        "blockers": blockers,
        "kill_switch_status": "PAUSED" if blockers else "OK",
        "expectancy_after_fee": expectancy,
        "consecutive_losses": consecutive_losses,
        "stop_loss_streak": stop_loss_streak,
        "execution_quality": execution_summary,
        "market_cooldown_hours": cfg.market_cooldown_hours if blockers else 0,
    }


def _consecutive_losses(rows_newest_first: list[dict]) -> int:
    count = 0
    for row in rows_newest_first:
        if _float(row.get("actual_pnl", row.get("realized_pnl"))) < 0:
            count += 1
        else:
            break
    return count


def _stop_loss_streak(rows_newest_first: list[dict]) -> int:
    count = 0
    for row in rows_newest_first:
        reason = str(row.get("exit_reason") or row.get("risk_result") or "").upper()
        if "STOP_LOSS" in reason:
            count += 1
        else:
            break
    return count


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
