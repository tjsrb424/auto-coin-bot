from __future__ import annotations

import os
from typing import Any


ATTACK_ENTRY_DEFAULT = 65.0
ATTACK_MAX_DEFAULT = 80.0


def calculate_attack_score(
    *,
    market_regime: str,
    internal_signals: dict,
    features: dict,
    external_factors: dict | None,
    risk_score: float,
    current_position_pnl_pct: float,
    current_exposure_pct: float,
) -> dict:
    positives: list[str] = []
    negatives: list[str] = []
    blockers: list[str] = []
    breakdown: dict[str, float] = {}
    regime = str(market_regime or "UNKNOWN").upper()

    if not _env_bool("SMART_AGGRESSIVE_MODE_ENABLED", True):
        blockers.append("SMART_AGGRESSIVE_MODE_DISABLED")
        negatives.append("Aggressive Trend Capture Mode is disabled by environment.")

    _add(breakdown, positives, "market_breakout", 25, regime == "BREAKOUT", "BREAKOUT regime supports aggressive exposure.")
    _add(breakdown, positives, "market_trend_up", 15, regime == "TREND_UP", "TREND_UP regime supports higher exposure.")

    ma5 = _float_or_none(features.get("ma_5"))
    ma20 = _float_or_none(features.get("ma_20"))
    ma60 = _float_or_none(features.get("ma_60"))
    if ma5 is not None and ma20 is not None and ma60 is not None and ma5 > ma20 > ma60:
        breakdown["ma_stack"] = 15.0
        positives.append("MA 5 > MA 20 > MA 60 bullish alignment is active.")

    if _float(features.get("ma_20_slope")) > 0:
        breakdown["ma_20_slope"] = 8.0
        positives.append("MA 20 slope is positive.")
    if _float(features.get("volume_ratio_20")) >= 1.5:
        breakdown["volume_ratio_20"] = 15.0
        positives.append("Volume ratio is elevated versus the 20-candle average.")
    if _float(features.get("recent_return_1h")) > 0.5:
        breakdown["recent_return_1h"] = 10.0
        positives.append("1h return is strong enough for trend capture.")
    if _float(features.get("recent_return_24h")) > 0:
        breakdown["recent_return_24h"] = 8.0
        positives.append("24h return is positive.")

    btc_momentum = _provider_value(external_factors, "btc_usd_momentum")
    if btc_momentum is not None and btc_momentum > 0:
        breakdown["btc_usd_momentum"] = 8.0
        positives.append("BTC/USD momentum is positive.")
    fear_greed = _provider_value(external_factors, "fear_greed_score")
    if fear_greed is not None and fear_greed < 70:
        breakdown["fear_greed_below_greed"] = 5.0
        positives.append("Fear & Greed is below greed level, leaving room for trend capture.")

    if _float(current_position_pnl_pct) > _float(os.getenv("SMART_PYRAMID_MIN_PNL_PCT"), 0.3):
        breakdown["profitable_position"] = 10.0
        positives.append("Current position is profitable, so pyramiding can be considered.")

    if _float(risk_score) >= 60:
        breakdown["risk_score_penalty"] = -25.0
        negatives.append("Risk score is elevated, reducing aggressive score.")
    if _float(risk_score) >= 80:
        blockers.append("SMART_AGGRESSIVE_RISK_BLOCKED")
        negatives.append("Risk score is too high for aggressive exposure.")

    if regime == "OVERHEATED":
        blockers.append("SMART_AGGRESSIVE_OVERHEATED_BLOCKED")
        negatives.append("OVERHEATED market blocks new aggressive buys.")
    elif regime == "TREND_DOWN":
        blockers.append("SMART_AGGRESSIVE_TREND_DOWN_BLOCKED")
        negatives.append("TREND_DOWN market blocks aggressive buys.")
    elif regime == "PANIC":
        blockers.append("SMART_AGGRESSIVE_PANIC_BLOCKED")
        negatives.append("PANIC market blocks aggressive exposure and prefers zero target.")

    score = round(max(min(sum(breakdown.values()), 100.0), 0.0), 4)
    entry = _float(os.getenv("SMART_ATTACK_SCORE_ENTRY"), ATTACK_ENTRY_DEFAULT)
    max_score = _float(os.getenv("SMART_ATTACK_SCORE_MAX"), ATTACK_MAX_DEFAULT)
    if score >= max_score:
        mode = "MAX_AGGRESSIVE"
    elif score >= entry:
        mode = "AGGRESSIVE"
    elif score >= 45:
        mode = "WATCH"
    else:
        mode = "OFF"

    return {
        "attack_score": score,
        "attack_mode": mode,
        "positive_reasons": positives,
        "negative_reasons": negatives,
        "blockers": list(dict.fromkeys(blockers)),
        "score_breakdown": breakdown,
        "current_exposure_pct": _float(current_exposure_pct),
    }


def apply_aggressive_target_layer(
    *,
    market_regime: str,
    conservative_target_exposure_pct: float,
    attack_result: dict,
    current_exposure_pct: float,
    current_position_pnl_pct: float,
    current_price: float,
    highest_price_since_entry: float | None,
    risk_blockers: list[str] | None = None,
) -> dict:
    regime = str(market_regime or "UNKNOWN").upper()
    current = _float(current_exposure_pct)
    conservative = _float(conservative_target_exposure_pct)
    pnl = _float(current_position_pnl_pct)
    blockers = list(dict.fromkeys([*(risk_blockers or []), *((attack_result or {}).get("blockers") or [])]))
    positives = list((attack_result or {}).get("positive_reasons") or [])
    negatives = list((attack_result or {}).get("negative_reasons") or [])
    attack_mode = str((attack_result or {}).get("attack_mode") or "OFF")
    attack_score = _float((attack_result or {}).get("attack_score"))

    aggressive_target = _aggressive_target(regime, attack_mode)
    final_target = conservative
    source = "CONSERVATIVE"
    if _aggressive_allowed(regime, attack_mode, blockers):
        final_target = max(conservative, aggressive_target)
        source = "AGGRESSIVE" if final_target > conservative else "CONSERVATIVE"

    no_averaging_down = False
    if _env_bool("SMART_NO_AVERAGING_DOWN", True) and current > 0 and pnl < 0 and final_target > current:
        no_averaging_down = True
        blockers.append("SMART_AGGRESSIVE_NO_AVERAGING_DOWN")
        negatives.append("Current position is losing, so aggressive add-buy is blocked.")
        final_target = current
        source = "RISK_REDUCED"

    if blockers and final_target > current:
        final_target = current
        source = "RISK_REDUCED"
    if regime == "PANIC":
        final_target = 0.0
        source = "RISK_REDUCED"
    elif regime == "TREND_DOWN":
        final_target = min(final_target, 10.0)
        source = "RISK_REDUCED" if final_target < conservative else source
    elif regime == "OVERHEATED" and final_target > current:
        final_target = current
        source = "RISK_REDUCED"

    pyramid_min = _float(os.getenv("SMART_PYRAMID_MIN_PNL_PCT"), 0.3)
    pyramiding_allowed = current > 0 and pnl >= pyramid_min and attack_score >= _float(os.getenv("SMART_ATTACK_SCORE_ENTRY"), ATTACK_ENTRY_DEFAULT) and not no_averaging_down and regime in {"BREAKOUT", "TREND_UP", "RANGE"}
    if pyramiding_allowed:
        positives.append("Position is profitable, so pyramiding add-buy is allowed within hard caps.")

    highest = max(_float(highest_price_since_entry), _float(current_price))
    trailing_pct = _trailing_stop_pct(regime)
    trailing_stop_price = highest * (1 - trailing_pct / 100) if highest > 0 else 0.0
    target_source = source
    partial_take_profit_triggered = False
    partial_take_profit_pct = 0.0

    if current > 0 and regime == "PANIC":
        final_target = 0.0
        target_source = "TRAILING_EXIT"
        negatives.append("PANIC market triggers exit preference over trailing hold.")
    elif current > 0 and trailing_stop_price > 0 and _float(current_price) <= trailing_stop_price:
        final_target = min(final_target, max(current * 0.5, 0.0))
        target_source = "TRAILING_EXIT"
        negatives.append("Current price touched the trailing stop candidate.")
    else:
        tp1 = _float(os.getenv("SMART_PARTIAL_TAKE_PROFIT_1_PCT"), 0.8)
        tp2 = _float(os.getenv("SMART_PARTIAL_TAKE_PROFIT_2_PCT"), 1.5)
        strong_breakout_hold = regime == "BREAKOUT" and attack_score >= _float(os.getenv("SMART_ATTACK_SCORE_MAX"), ATTACK_MAX_DEFAULT)
        if current > 0 and pnl >= tp2:
            partial_take_profit_triggered = True
            partial_take_profit_pct = 25.0
            final_target = min(final_target, current * 0.75)
            target_source = "PARTIAL_TAKE_PROFIT"
            negatives.append("Profit exceeds the second partial-take threshold, so partial profit is preferred.")
        elif current > 0 and pnl >= tp1 and (regime == "OVERHEATED" or attack_mode in {"OFF", "WATCH"} or not strong_breakout_hold):
            partial_take_profit_triggered = True
            partial_take_profit_pct = 20.0
            final_target = min(final_target, current * 0.8)
            target_source = "PARTIAL_TAKE_PROFIT"
            negatives.append("Profit exceeds the first partial-take threshold, so some exposure may be secured.")
        elif current > 0 and pnl >= 2.5:
            target_source = "TRAILING_EXIT"
            positives.append("Profit exceeds 2.5%, so remaining exposure is managed by trailing stop.")

    final_target = round(max(min(final_target, 100.0), 0.0), 4)
    action_hint = _action_hint(current, final_target, target_source)
    return {
        "target_exposure_pct": final_target,
        "aggressive_target_exposure_pct": round(max(min(aggressive_target, 100.0), 0.0), 4),
        "conservative_target_exposure_pct": round(max(min(conservative, 100.0), 0.0), 4),
        "final_target_exposure_source": target_source,
        "action_hint": action_hint,
        "positive_reasons": positives,
        "negative_reasons": negatives,
        "blockers": list(dict.fromkeys(blockers)),
        "highest_price_since_entry": round(highest, 8) if highest else None,
        "trailing_stop_price": round(trailing_stop_price, 8) if trailing_stop_price else None,
        "trailing_stop_pct": trailing_pct,
        "partial_take_profit_triggered": partial_take_profit_triggered,
        "partial_take_profit_pct": partial_take_profit_pct,
        "pyramiding_allowed": pyramiding_allowed,
        "no_averaging_down_blocked": no_averaging_down,
    }


def _aggressive_target(regime: str, mode: str) -> float:
    if regime == "BREAKOUT":
        return _float(os.getenv("SMART_AGGRESSIVE_MAX_EXPOSURE_BREAKOUT"), 75.0) if mode == "MAX_AGGRESSIVE" else 60.0 if mode == "AGGRESSIVE" else 0.0
    if regime == "TREND_UP":
        return _float(os.getenv("SMART_AGGRESSIVE_MAX_EXPOSURE_TREND_UP"), 65.0) if mode == "MAX_AGGRESSIVE" else 50.0 if mode == "AGGRESSIVE" else 0.0
    if regime == "RANGE":
        return _float(os.getenv("SMART_AGGRESSIVE_MAX_EXPOSURE_RANGE"), 30.0) if mode in {"AGGRESSIVE", "MAX_AGGRESSIVE"} else 0.0
    if regime == "TREND_DOWN":
        return 10.0
    if regime == "PANIC":
        return 0.0
    return 0.0


def _aggressive_allowed(regime: str, mode: str, blockers: list[str]) -> bool:
    if blockers:
        return False
    return regime in {"BREAKOUT", "TREND_UP", "RANGE"} and mode in {"AGGRESSIVE", "MAX_AGGRESSIVE"}


def _action_hint(current: float, target: float, source: str) -> str:
    if source == "PARTIAL_TAKE_PROFIT":
        return "TAKE_PROFIT_PARTIAL"
    if source == "TRAILING_EXIT":
        return "EXIT" if target <= 0 else "REDUCE"
    delta = target - current
    min_delta = _float(os.getenv("SMART_MIN_REBALANCE_DELTA_PCT"), 5.0)
    if abs(delta) < min_delta:
        return "HOLD_POSITION" if current > 0 else "WAIT"
    if target <= 0 and current > 0:
        return "EXIT"
    return "BUY_MORE" if delta > 0 else "REDUCE"


def _trailing_stop_pct(regime: str) -> float:
    default = {
        "BREAKOUT": 0.9,
        "TREND_UP": 0.7,
        "RANGE": 0.5,
        "OVERHEATED": 0.4,
    }.get(regime, _float(os.getenv("SMART_TRAILING_STOP_PCT"), 0.7))
    return _float(os.getenv("SMART_TRAILING_STOP_PCT"), default)


def _add(breakdown: dict[str, float], positives: list[str], key: str, value: float, condition: bool, reason: str) -> None:
    if condition:
        breakdown[key] = float(value)
        positives.append(reason)


def _provider_value(external_factors: dict | None, key: str) -> float | None:
    item = ((external_factors or {}).get("providers") or {}).get(key)
    if not isinstance(item, dict) or item.get("stale"):
        return None
    return _float_or_none(item.get("value"))


def _env_bool(key: str, default: bool) -> bool:
    value = os.getenv(key)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
