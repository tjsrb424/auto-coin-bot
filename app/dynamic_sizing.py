from __future__ import annotations

import os
from typing import Any

from app.aggression_presets import runtime_setting_bool, runtime_setting_float, runtime_setting_str


def dynamic_sizing_enabled() -> bool:
    return runtime_setting_bool("SMART_DYNAMIC_SIZING_ENABLED", False)


def dynamic_sizing_mode() -> str:
    mode = runtime_setting_str("SMART_DYNAMIC_SIZING_MODE", "shadow").strip().lower()
    return mode if mode in {"shadow", "apply", "block"} else "shadow"


def build_dynamic_sizing_preview(
    *,
    original_amount_krw: float,
    adaptive_edge: dict | None,
    fee_pct: float,
    estimated_slippage_pct: float | None = None,
    adverse_selection_pct: float | None = None,
    max_allowed_amount_krw: float | None = None,
    min_order_krw: float = 0.0,
    recent_failure_penalty_pct: float = 0.0,
) -> dict:
    enabled = dynamic_sizing_enabled()
    mode = dynamic_sizing_mode()
    min_multiplier = runtime_setting_float("SMART_DYNAMIC_SIZING_MIN_MULTIPLIER", 0.5)
    max_multiplier = runtime_setting_float("SMART_DYNAMIC_SIZING_MAX_MULTIPLIER", 1.5)
    if min_multiplier <= 0:
        min_multiplier = 0.5
    if max_multiplier < min_multiplier:
        max_multiplier = min_multiplier
    min_confidence = runtime_setting_float("SMART_DYNAMIC_SIZING_MIN_CONFIDENCE", 30.0)
    require_positive = runtime_setting_bool("SMART_DYNAMIC_SIZING_REQUIRE_POSITIVE_NET_EDGE", True)

    edge = adaptive_edge if isinstance(adaptive_edge, dict) else {}
    edge_score = _float(edge.get("adaptive_edge_score"))
    confidence = _float(edge.get("edge_confidence"))
    expected_edge_pct = _expected_edge_pct(edge)
    slippage_pct = _first_number(estimated_slippage_pct, edge.get("avg_slippage_pct"), 0.0)
    adverse_pct = _first_number(adverse_selection_pct, edge.get("avg_adverse_selection_pct"), 0.0)
    net_edge_pct = expected_edge_pct - max(fee_pct, 0.0) - max(slippage_pct, 0.0) - max(adverse_pct, 0.0) - max(recent_failure_penalty_pct, 0.0)
    confidence_ok = confidence >= min_confidence
    multiplier = 1.0
    reason = "DYNAMIC_SIZING_DISABLED" if not enabled else "LOW_CONFIDENCE_DEFAULT_MULTIPLIER"
    allowed = True
    blocker = None

    if enabled and confidence_ok:
        if require_positive and net_edge_pct <= 0:
            multiplier = min_multiplier
            reason = "NEGATIVE_NET_EDGE_REDUCED"
            if mode == "block":
                allowed = False
                blocker = "SMART_DYNAMIC_SIZING_NET_EDGE_NON_POSITIVE"
        elif net_edge_pct <= 0:
            multiplier = min_multiplier
            reason = "NEGATIVE_NET_EDGE_REDUCED"
        elif net_edge_pct < 0.25:
            multiplier = max(min_multiplier, 0.75)
            reason = "LOW_POSITIVE_EDGE_REDUCED"
        elif net_edge_pct < 0.75:
            multiplier = 1.0
            reason = "MODERATE_EDGE_BASE_SIZE"
        elif net_edge_pct < 1.5:
            multiplier = min(max_multiplier, 1.2)
            reason = "POSITIVE_EDGE_INCREASED"
        else:
            multiplier = max_multiplier
            reason = "HIGH_POSITIVE_EDGE_MAX_INCREASE"
    elif enabled and not confidence_ok:
        multiplier = 1.0
        reason = "LOW_CONFIDENCE_DEFAULT_MULTIPLIER"

    adjusted_amount = max(original_amount_krw * multiplier, 0.0)
    if max_allowed_amount_krw is not None and max_allowed_amount_krw >= 0:
        adjusted_amount = min(adjusted_amount, max_allowed_amount_krw)
    dust_hold = 0 < adjusted_amount < min_order_krw
    if dust_hold and enabled and mode == "block":
        allowed = False
        blocker = blocker or "SMART_DYNAMIC_SIZING_ADJUSTED_BELOW_MIN"

    applied_amount = original_amount_krw
    if enabled and mode == "apply" and allowed:
        applied_amount = adjusted_amount
    elif enabled and mode == "block" and allowed:
        applied_amount = adjusted_amount

    return {
        "enabled": enabled,
        "mode": mode,
        "shadow_only": not enabled or mode == "shadow",
        "adaptive_edge_score": edge_score,
        "edge_confidence": confidence,
        "expected_edge_pct": round(expected_edge_pct, 6),
        "estimated_fee_pct": round(max(fee_pct, 0.0), 6),
        "estimated_slippage_pct": round(max(slippage_pct, 0.0), 6),
        "adverse_selection_pct": round(max(adverse_pct, 0.0), 6),
        "recent_failure_penalty_pct": round(max(recent_failure_penalty_pct, 0.0), 6),
        "net_edge_pct": round(net_edge_pct, 6),
        "min_confidence": min_confidence,
        "confidence_ok": confidence_ok,
        "sizing_multiplier": round(multiplier, 6),
        "min_multiplier": min_multiplier,
        "max_multiplier": max_multiplier,
        "original_amount_krw": round(max(original_amount_krw, 0.0), 6),
        "adjusted_amount_krw": round(adjusted_amount, 6),
        "applied_amount_krw": round(max(applied_amount, 0.0), 6),
        "max_allowed_amount_krw": max_allowed_amount_krw,
        "min_order_krw": min_order_krw,
        "dust_hold": dust_hold,
        "allowed": allowed,
        "blocker": blocker,
        "sizing_reason": reason,
        "require_positive_net_edge": require_positive,
    }


def _expected_edge_pct(edge: dict) -> float:
    explicit = _float(edge.get("expected_edge_pct"), None)
    if explicit is not None:
        return explicit
    values = [
        (_float(edge.get("adaptive_edge_score")), 0.45),
        (_float(edge.get("avg_post_fill_return_5m")), 0.25),
        (_float(edge.get("avg_post_fill_return_15m")), 0.20),
        (_float(edge.get("avg_realized_return_pct")), 0.10),
    ]
    return sum(value * weight for value, weight in values)


def _first_number(*values: Any) -> float:
    for value in values:
        parsed = _float(value, None)
        if parsed is not None:
            return parsed
    return 0.0


def _float_env(key: str, default: float) -> float:
    return _float(os.getenv(key), default)


def _bool_env(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
