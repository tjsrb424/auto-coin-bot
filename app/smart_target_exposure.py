from __future__ import annotations

from typing import Any

from app.smart_signal_engine import aggregate_signal_score


def calculate_target_exposure(
    *,
    current_exposure_pct: float,
    risk_score: float,
    market_regime: str,
    internal_signals: dict,
    risk_state: dict,
    policy: dict,
    max_total_exposure_krw: float,
    current_position_value_krw: float,
    external_factors: dict | None = None,
) -> dict:
    positives: list[str] = []
    negatives: list[str] = []
    blockers: list[str] = []
    aggregate = aggregate_signal_score(internal_signals)
    target = _base_exposure(market_regime) + aggregate * 0.35
    target -= max(_float(risk_score) - 45, 0.0) * 0.45
    external_adjustment, external_positives, external_negatives, external_blockers = _external_factor_adjustment(external_factors)
    target += external_adjustment
    positives.extend(external_positives)
    negatives.extend(external_negatives)
    blockers.extend(external_blockers)
    if aggregate > 15:
        positives.append(f"Internal signal aggregate score {aggregate:.1f} supports a higher target exposure.")
    if aggregate < -15:
        negatives.append(f"Internal signal aggregate score {aggregate:.1f} lowers target exposure.")
    if not policy.get("auto_trading_enabled"):
        blockers.append("SMART_POLICY_AUTO_TRADING_DISABLED")
        negatives.append("Operation policy auto trading is OFF.")
    if current_position_value_krw >= max_total_exposure_krw:
        blockers.append("SMART_MAX_TOTAL_EXPOSURE_REACHED")
        negatives.append("Current bot position value is at or above max total exposure.")
    if risk_state.get("status") in {"EMERGENCY_STOPPED", "BLOCKED"}:
        blockers.append(str(risk_state.get("status")))
        negatives.append(f"Risk state is {risk_state.get('status')}.")
    if risk_score >= 80:
        blockers.append("SMART_RISK_SCORE_HIGH")
        negatives.append("Risk score is high, so new entry is restricted.")
    if market_regime == "UNKNOWN":
        blockers.append("SMART_MARKET_REGIME_UNKNOWN")
        negatives.append("Market regime is UNKNOWN due to insufficient data.")
    daily_loss_limit_krw = max_total_exposure_krw * _float(policy.get("daily_loss_limit_pct"), 3.0) / 100
    daily_loss_krw = abs(min(_float(risk_state.get("daily_total_pnl")), 0.0))
    if daily_loss_limit_krw > 0 and daily_loss_krw >= daily_loss_limit_krw:
        blockers.append("SMART_DAILY_LOSS_LIMIT_REACHED")
        negatives.append("Daily loss limit was reached, so additional buy exposure is blocked.")
    if blockers:
        target = min(target, current_exposure_pct)
    if market_regime in {"PANIC"}:
        target = 0.0
    target = round(max(min(target, 100.0), 0.0), 4)
    if target > current_exposure_pct:
        positives.append("Target exposure is above current exposure, so a buy candidate may be created.")
    elif target < current_exposure_pct:
        negatives.append("Target exposure is below current exposure, so reduce/exit may be considered.")
    else:
        positives.append("Target exposure is close to current exposure, so holding is preferred.")
    return {
        "target_exposure_pct": target,
        "aggregate_signal_score": aggregate,
        "external_factor_adjustment_pct": external_adjustment,
        "positive_reasons": positives,
        "negative_reasons": negatives,
        "blockers": list(dict.fromkeys(blockers)),
    }


def _base_exposure(regime: str) -> float:
    return {
        "BREAKOUT": 45.0,
        "TREND_UP": 35.0,
        "RANGE": 18.0,
        "TREND_DOWN": 8.0,
        "OVERHEATED": 5.0,
        "PANIC": 0.0,
        "UNKNOWN": 0.0,
    }.get(str(regime).upper(), 10.0)


def _external_factor_adjustment(external_factors: dict | None) -> tuple[float, list[str], list[str], list[str]]:
    positives: list[str] = []
    negatives: list[str] = []
    blockers: list[str] = []
    providers = (external_factors or {}).get("providers") or {}
    adjustment = 0.0
    if "SMART_EXCHANGE_NOTICE_RISK_BLOCK" in (external_factors or {}).get("hard_blockers", []):
        adjustment -= 15.0
        blockers.append("SMART_EXCHANGE_NOTICE_RISK_BLOCK")
        negatives.append("Exchange notice hard block detected; new buy target is capped at current exposure or lower.")
    news_sentiment = _provider_value(providers, "news_sentiment_score")
    if news_sentiment is not None:
        if news_sentiment <= -60:
            adjustment -= 6.0
            negatives.append(f"News sentiment {news_sentiment:.0f} is strongly negative, lowering target exposure by 6.00pt.")
        elif news_sentiment <= -30:
            adjustment -= 3.0
            negatives.append(f"News sentiment {news_sentiment:.0f} is negative, lowering target exposure by 3.00pt.")
        elif news_sentiment >= 50:
            adjustment += 1.0
            positives.append(f"News sentiment {news_sentiment:.0f} is positive, adding only a conservative 1.00pt.")
    btc_momentum = _provider_value(providers, "btc_usd_momentum")
    if btc_momentum is not None:
        momentum_adjustment = max(min(btc_momentum * 0.6, 3.0), -5.0)
        adjustment += momentum_adjustment
        if momentum_adjustment > 0.5:
            positives.append(f"BTC/USD 24h momentum {btc_momentum:.2f}% adds {momentum_adjustment:.2f}pt to target exposure.")
        elif momentum_adjustment < -0.5:
            negatives.append(f"BTC/USD 24h momentum {btc_momentum:.2f}% adds {momentum_adjustment:.2f}pt to target exposure.")
    kimchi_premium = _provider_value(providers, "kimchi_premium")
    if kimchi_premium is not None:
        if kimchi_premium >= 5:
            adjustment -= 4.0
            negatives.append(f"Kimchi premium {kimchi_premium:.2f}% is high, lowering target exposure by 4.00pt.")
        elif kimchi_premium >= 3:
            adjustment -= 2.0
            negatives.append(f"Kimchi premium {kimchi_premium:.2f}% is elevated, lowering target exposure by 2.00pt.")
        elif kimchi_premium <= -2:
            adjustment += 1.0
            positives.append(f"Kimchi premium {kimchi_premium:.2f}% is low, adding only a conservative 1.00pt.")
    fear_greed = _provider_value(providers, "fear_greed_score")
    if fear_greed is not None:
        if fear_greed >= 80:
            adjustment -= 6.0
            negatives.append(f"Fear and Greed Index {fear_greed:.0f} is extreme greed, lowering target exposure by 6.00pt.")
        elif fear_greed >= 70:
            adjustment -= 3.0
            negatives.append(f"Fear and Greed Index {fear_greed:.0f} is greed, lowering target exposure by 3.00pt.")
        elif fear_greed <= 20:
            adjustment -= 1.0
            negatives.append(f"Fear and Greed Index {fear_greed:.0f} is extreme fear; no aggressive buy boost is applied.")
    adjustment = round(max(min(adjustment, 5.0), -15.0), 4)
    if external_factors and not positives and not negatives:
        negatives.append("External factors are stale or neutral and did not change target exposure.")
    return adjustment, positives, negatives, blockers


def _provider_value(providers: dict, key: str) -> float | None:
    item = providers.get(key)
    if not isinstance(item, dict) or item.get("stale"):
        return None
    return _optional_float(item.get("value"))


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
