from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any


DUST_POSITION = "DUST_POSITION"
SMALL_POSITION = "SMALL_POSITION"
NORMAL_POSITION = "NORMAL_POSITION"
FULL_EXIT_CANDIDATE = "FULL_EXIT_CANDIDATE"
DUST_HOLD = "DUST_HOLD"
HOLD = "HOLD"

EXIT_ACTION_HINTS = {"REDUCE", "EXIT", "HOLD_POSITION", "TAKE_PROFIT_PARTIAL", "CUT_LOSS"}
BUY_ACTION_HINTS = {"BUY_MORE", "INCREASE_EXPOSURE", "SCALE_IN"}
TREND_BLOCK_REGIMES = {"TREND_UP", "BREAKOUT"}


def small_position_resolver_enabled() -> bool:
    return _env_bool("SMART_SMALL_POSITION_RESOLVER_ENABLED", True)


def small_position_thresholds() -> dict:
    return {
        "dust_position_threshold_krw": _env_float("SMART_DUST_POSITION_THRESHOLD_KRW", 5_000.0),
        "small_position_threshold_krw": _env_float("SMART_SMALL_POSITION_THRESHOLD_KRW", 15_000.0),
        "min_hold_minutes": _env_float("SMART_SMALL_POSITION_MIN_HOLD_MINUTES", 30.0),
        "positive_edge_threshold": _env_float("SMART_SMALL_POSITION_POSITIVE_EDGE_THRESHOLD", 0.0),
        "max_recovery_pnl_pct": _env_float("SMART_SMALL_POSITION_MAX_RECOVERY_PNL_PCT", 0.0),
        "post_exit_cooldown_seconds": _env_float("SMART_SMALL_POSITION_EXIT_COOLDOWN_SECONDS", 1_800.0),
    }


def classify_position_size(position_value_krw: float, *, min_order_krw: float | None = None) -> str:
    thresholds = small_position_thresholds()
    dust_threshold = max(_float(min_order_krw), _float(thresholds["dust_position_threshold_krw"]))
    small_threshold = max(_float(thresholds["small_position_threshold_krw"]), dust_threshold)
    if _float(position_value_krw) < dust_threshold:
        return DUST_POSITION
    if _float(position_value_krw) < small_threshold:
        return SMALL_POSITION
    return NORMAL_POSITION


def evaluate_small_position_resolution(
    *,
    position: dict | None,
    current_price: float,
    min_order_krw: float,
    smart_snapshot: dict | None = None,
    intent: dict | None = None,
    sellable_value_krw: float | None = None,
    now_utc: datetime | None = None,
) -> dict:
    thresholds = small_position_thresholds()
    snapshot = smart_snapshot if isinstance(smart_snapshot, dict) else {}
    order_intent = intent if isinstance(intent, dict) else {}
    policy_preview = order_intent.get("policy_preview") if isinstance(order_intent.get("policy_preview"), dict) else {}
    adaptive_edge = policy_preview.get("adaptive_edge") if isinstance(policy_preview.get("adaptive_edge"), dict) else {}

    position_value = _position_value(position, current_price)
    sellable_value = _float(sellable_value_krw, position_value)
    classification = classify_position_size(position_value, min_order_krw=min_order_krw)
    action_hint = str(order_intent.get("action_hint") or snapshot.get("action_hint") or "").upper()
    market_regime = str(snapshot.get("market_regime") or policy_preview.get("market_regime") or "").upper()
    edge_score = _float(
        adaptive_edge.get("adaptive_edge_score", policy_preview.get("adaptive_edge_score")),
        _float(snapshot.get("adaptive_edge_score")),
    )
    edge_confidence = _float(adaptive_edge.get("edge_confidence", policy_preview.get("edge_confidence")))
    pnl_pct = _position_pnl_pct(position, current_price)
    hold_minutes = _holding_minutes((position or {}).get("opened_at"), now_utc=now_utc)

    blockers: list[str] = []
    if not small_position_resolver_enabled():
        blockers.append("SMALL_POSITION_RESOLVER_DISABLED")
    if classification == DUST_POSITION:
        blockers.append("DUST_POSITION_BELOW_MIN_ORDER")
    elif classification == NORMAL_POSITION:
        blockers.append("NORMAL_POSITION")
    if sellable_value < min_order_krw:
        blockers.append("SELLABLE_VALUE_BELOW_MIN_ORDER")
    if action_hint in BUY_ACTION_HINTS:
        blockers.append("BUY_MORE_CONTEXT")
    elif action_hint not in EXIT_ACTION_HINTS:
        blockers.append("ACTION_HINT_NOT_EXIT_OR_HOLD")
    if market_regime in TREND_BLOCK_REGIMES:
        blockers.append(f"MARKET_REGIME_{market_regime}")
    if edge_score > _float(thresholds["positive_edge_threshold"]):
        blockers.append("ADAPTIVE_EDGE_POSITIVE")
    if pnl_pct > _float(thresholds["max_recovery_pnl_pct"]):
        blockers.append("POSITION_PNL_RECOVERING")
    if hold_minutes < _float(thresholds["min_hold_minutes"]):
        blockers.append("MIN_HOLD_TIME_NOT_MET")
    if bool(snapshot.get("balance_mismatch_detected")):
        blockers.append("BALANCE_MISMATCH")
    if bool(snapshot.get("open_order_mismatch_detected")):
        blockers.append("OPEN_ORDER_MISMATCH")

    full_exit_allowed = classification == SMALL_POSITION and not blockers
    recommended_action = FULL_EXIT_CANDIDATE if full_exit_allowed else (DUST_HOLD if classification == DUST_POSITION else HOLD)
    return {
        "enabled": small_position_resolver_enabled(),
        "classification": classification,
        "recommended_action": recommended_action,
        "full_exit_allowed": full_exit_allowed,
        "blockers": blockers,
        "position_value_krw": round(position_value, 8),
        "sellable_value_krw": round(sellable_value, 8),
        "min_order_krw": min_order_krw,
        "small_position_threshold_krw": thresholds["small_position_threshold_krw"],
        "action_hint": action_hint,
        "market_regime": market_regime,
        "adaptive_edge_score": edge_score,
        "edge_confidence": edge_confidence,
        "position_pnl_pct": round(pnl_pct, 8),
        "holding_minutes": round(hold_minutes, 4),
        "min_hold_minutes": thresholds["min_hold_minutes"],
        "post_exit_cooldown_seconds": thresholds["post_exit_cooldown_seconds"],
    }


def _position_value(position: dict | None, current_price: float) -> float:
    if not position:
        return 0.0
    return max(_float(position.get("entry_volume")), 0.0) * max(_float(current_price), 0.0)


def _position_pnl_pct(position: dict | None, current_price: float) -> float:
    if not position:
        return 0.0
    entry_amount = _float(position.get("entry_amount_krw"))
    entry_volume = _float(position.get("entry_volume"))
    if entry_amount <= 0 or entry_volume <= 0:
        return 0.0
    current_value = entry_volume * _float(current_price)
    return (current_value - entry_amount) / entry_amount * 100


def _holding_minutes(opened_at: Any, *, now_utc: datetime | None = None) -> float:
    if not opened_at:
        return 0.0
    try:
        opened = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=timezone.utc)
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max((now - opened).total_seconds() / 60.0, 0.0)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
