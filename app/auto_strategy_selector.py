from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from app.database import (
    count_strategy_switches_today,
    has_unresolved_live_order,
    has_unresolved_live_order_for_exchange,
    load_active_strategy_selection,
    load_global_bot_operation_policy,
    load_live_eligible_candidate_strategies,
    load_market_universe_item,
    load_open_live_positions,
    load_open_live_positions_for_exchange,
    load_strategy_switch_logs_with_candidates,
    market_is_live_allowed,
    market_is_auto_selectable,
    promote_candidate_strategy,
    record_strategy_switch,
    save_active_strategy_selection,
)
from app.live_broker import is_emergency_stopped
from app.risk_manager import compute_risk_state


MIN_SCORE_DELTA = float(os.getenv("AUTO_SELECTOR_MIN_SCORE_DELTA", "10"))
SWITCH_COOLDOWN_MINUTES = int(os.getenv("AUTO_SELECTOR_SWITCH_COOLDOWN_MINUTES", "60"))
MAX_SWITCHES_PER_DAY = int(os.getenv("AUTO_SELECTOR_MAX_SWITCHES_PER_DAY", "0"))
MAX_OPEN_POSITIONS = int(os.getenv("AUTO_SELECTOR_MAX_OPEN_POSITIONS", os.getenv("AUTO_MAX_OPEN_POSITION_COUNT", "5")))


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace(" ", "T")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    if "+" not in normalized[-6:] and "-" not in normalized[-6:]:
        normalized = f"{normalized}+00:00"
    try:
        return datetime.fromisoformat(normalized).astimezone(timezone.utc)
    except ValueError:
        return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _score(candidate: dict | None) -> float:
    return float((candidate or {}).get("score") or 0.0)


def _active_candidate_summary(active: dict | None, candidates: list[dict]) -> dict | None:
    if active is None:
        return None
    candidate_id = int(active.get("candidate_strategy_id") or 0)
    candidate = next((item for item in candidates if int(item.get("id") or 0) == candidate_id), None)
    return {**active, "candidate": candidate} if candidate else active


def evaluate_auto_strategy_selector(*, exchange: str = "bithumb", apply: bool = False) -> dict:
    candidates = load_live_eligible_candidate_strategies(100)
    active = load_active_strategy_selection()
    active_candidate = next((item for item in candidates if active and int(item["id"]) == int(active["candidate_strategy_id"])), None)
    best = candidates[0] if candidates else None
    score_delta = _score(best) - _score(active_candidate)
    blockers: list[str] = []
    warnings: list[str] = []
    policy = load_global_bot_operation_policy()
    daily_switch_count = count_strategy_switches_today()

    if not candidates:
        blockers.append("NO_LIVE_ELIGIBLE_CANDIDATE")
    if not policy.get("auto_trading_enabled"):
        blockers.append("POLICY_AUTO_TRADING_DISABLED")
    if is_emergency_stopped():
        blockers.append("EMERGENCY_STOP_ACTIVE")
    if best:
        market = str(best.get("market") or "")
        if not market_is_auto_selectable(exchange, market):
            blockers.append("MARKET_NOT_AUTO_SELECTABLE")
        if not market_is_live_allowed(exchange, market):
            blockers.append("MARKET_NOT_LIVE_ALLOWED")
        risk_state = compute_risk_state(exchange, market)
        if risk_state.get("status") in {"BLOCKED", "EMERGENCY_STOPPED"}:
            blockers.append("RISK_STATE_BLOCKED")
        if risk_state.get("open_order_count", 0) > 0 or has_unresolved_live_order(exchange, market) or has_unresolved_live_order_for_exchange(exchange):
            blockers.append("UNRESOLVED_OPEN_ORDER")
        open_positions = load_open_live_positions_for_exchange(exchange)
        if len(open_positions) >= MAX_OPEN_POSITIONS:
            blockers.append("OPEN_POSITION_LIMIT")
        elif load_open_live_positions(exchange, market):
            blockers.append("DUPLICATE_MARKET_POSITION")
    else:
        risk_state = {}

    if active and best and int(active["candidate_strategy_id"]) != int(best["id"]):
        selected_at = _parse_utc(str(active.get("selected_at") or ""))
        cooldown_until = selected_at + timedelta(minutes=SWITCH_COOLDOWN_MINUTES) if selected_at else None
        now = datetime.now(timezone.utc)
        if cooldown_until and now < cooldown_until:
            blockers.append("SWITCH_COOLDOWN_ACTIVE")
        if active_candidate and score_delta < MIN_SCORE_DELTA:
            blockers.append("SCORE_DELTA_TOO_SMALL")
    elif active and best:
        warnings.append("BEST_CANDIDATE_ALREADY_ACTIVE")

    if MAX_SWITCHES_PER_DAY > 0 and daily_switch_count >= MAX_SWITCHES_PER_DAY:
        blockers.append("DAILY_SWITCH_LIMIT")

    can_apply = bool(best) and not blockers
    decision = "APPLY" if can_apply else "BLOCKED"
    result = {
        "exchange": exchange,
        "evaluated_at": _utc_now(),
        "decision": decision,
        "can_apply": can_apply,
        "blockers": blockers,
        "warnings": warnings,
        "best_candidate": best,
        "active_selection": _active_candidate_summary(active, candidates),
        "score_delta": score_delta,
        "daily_switch_count": daily_switch_count,
        "limits": {
            "min_score_delta": MIN_SCORE_DELTA,
            "switch_cooldown_minutes": SWITCH_COOLDOWN_MINUTES,
            "max_switches_per_day": MAX_SWITCHES_PER_DAY,
            "max_open_positions": MAX_OPEN_POSITIONS,
        },
        "risk_state": risk_state,
        "recent_switch_logs": load_strategy_switch_logs_with_candidates(10),
    }
    if not apply or not can_apply or best is None:
        if apply and not can_apply:
            record_strategy_switch(
                from_candidate_strategy_id=int(active["candidate_strategy_id"]) if active else None,
                to_candidate_strategy_id=int(best["id"]) if best else None,
                from_market=str(active.get("market")) if active else None,
                to_market=str(best.get("market")) if best else None,
                decision="BLOCKED",
                blocked_reason=", ".join(blockers),
                score_delta=score_delta,
            )
        return result

    replaced_id = int(active["candidate_strategy_id"]) if active else None
    if replaced_id and replaced_id != int(best["id"]):
        promote_candidate_strategy(replaced_id, "LIVE_ELIGIBLE", reason="Replaced by auto strategy selector")
    promote_candidate_strategy(int(best["id"]), "LIVE_ACTIVE", reason="Selected by auto strategy selector")
    cooldown_until = (datetime.now(timezone.utc) + timedelta(minutes=SWITCH_COOLDOWN_MINUTES)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    selection = save_active_strategy_selection(
        best,
        reason="Best LIVE_ELIGIBLE candidate selected",
        replaced_candidate_strategy_id=replaced_id,
        cooldown_until=cooldown_until,
    )
    log = record_strategy_switch(
        from_candidate_strategy_id=replaced_id,
        to_candidate_strategy_id=int(best["id"]),
        from_market=str(active.get("market")) if active else None,
        to_market=str(best.get("market")),
        decision="APPLIED",
        reason="Best LIVE_ELIGIBLE candidate selected",
        score_delta=score_delta,
    )
    return {**result, "active_selection": selection, "switch_log": log}


def auto_strategy_selector_status(*, exchange: str = "bithumb") -> dict:
    result = evaluate_auto_strategy_selector(exchange=exchange, apply=False)
    markets = []
    for candidate in result.get("best_candidate"), (result.get("active_selection") or {}).get("candidate"):
        if candidate and candidate.get("market"):
            item = load_market_universe_item(exchange, str(candidate["market"]))
            if item:
                markets.append(item)
    return {**result, "related_markets": markets}
