from __future__ import annotations

from typing import Any


def evaluate_internal_signals(legacy_signal: dict, features: dict, market_regime: str) -> dict:
    rsi = features.get("rsi_14")
    ma_gap = features.get("ma_5_20_gap_pct")
    ma_slope = features.get("ma_20_slope")
    volume_ratio = features.get("volume_ratio_20")
    ret_1h = features.get("recent_return_1h")
    vol_1h = features.get("volatility_1h")
    signal = str(legacy_signal.get("signal") or "HOLD").upper()
    return {
        "legacy_strategy": _signal(
            "BULLISH" if signal == "BUY" else "BEARISH" if signal == "SELL" else "NEUTRAL",
            28 if signal == "BUY" else -28 if signal == "SELL" else 0,
            70 if signal in {"BUY", "SELL"} else 45,
            legacy_signal.get("reason") or "기존 전략 신호를 내부 참고 신호로 반영합니다.",
            signal,
        ),
        "rsi": _signal(
            "BULLISH" if rsi is not None and rsi < 35 else "BEARISH" if rsi is not None and rsi > 70 else "NEUTRAL",
            _bounded((50 - _float(rsi, 50)) * 2),
            70 if rsi is not None else 0,
            "RSI 과매도는 반등 가능성, 과열은 비중 축소 요인입니다.",
            rsi,
        ),
        "moving_average": _signal(
            "BULLISH" if ma_gap is not None and ma_gap > 0 and _float(ma_slope) >= 0 else "BEARISH" if ma_gap is not None and ma_gap < 0 else "NEUTRAL",
            _bounded(_float(ma_gap) * 8 + _float(ma_slope) * 12),
            70 if ma_gap is not None and ma_slope is not None else 20,
            "단기/중기 이동평균 괴리와 20MA 기울기를 함께 봅니다.",
            {"ma_5_20_gap_pct": ma_gap, "ma_20_slope": ma_slope},
        ),
        "volume": _signal(
            "BULLISH" if volume_ratio is not None and volume_ratio >= 1.35 else "BEARISH" if volume_ratio is not None and volume_ratio < 0.65 else "NEUTRAL",
            _bounded((_float(volume_ratio, 1.0) - 1.0) * 35),
            65 if volume_ratio is not None else 0,
            "최근 거래량이 20봉 평균 대비 얼마나 붙었는지 평가합니다.",
            volume_ratio,
        ),
        "momentum_1h": _signal(
            "BULLISH" if ret_1h is not None and ret_1h > 0.35 else "BEARISH" if ret_1h is not None and ret_1h < -0.35 else "NEUTRAL",
            _bounded(_float(ret_1h) * 15),
            65 if ret_1h is not None else 0,
            "최근 1시간 수익률로 단기 방향성을 평가합니다.",
            ret_1h,
        ),
        "volatility": _signal(
            "BEARISH" if vol_1h is not None and vol_1h >= 2.2 else "NEUTRAL",
            _bounded(-_float(vol_1h) * 10),
            60 if vol_1h is not None else 0,
            "단기 변동성이 높으면 신규 진입 점수를 낮춥니다.",
            vol_1h,
        ),
        "market_regime": _signal(
            market_regime,
            _regime_score(market_regime),
            70 if market_regime != "UNKNOWN" else 0,
            "시장상태 판정 결과를 신호 합산에 반영합니다.",
            market_regime,
        ),
    }


def aggregate_signal_score(signals: dict) -> float:
    weighted = 0.0
    total_weight = 0.0
    for value in signals.values():
        confidence = max(_float(value.get("confidence")), 0.0)
        weighted += _float(value.get("score")) * confidence
        total_weight += confidence
    return round(weighted / total_weight, 2) if total_weight > 0 else 0.0


def _signal(direction: str, score: float, confidence: float, reason: str, raw_value: Any) -> dict:
    return {
        "direction": direction,
        "score": round(_bounded(score), 2),
        "confidence": round(max(min(confidence, 100.0), 0.0), 2),
        "reason": reason,
        "raw_value": raw_value,
    }


def _regime_score(regime: str) -> float:
    return {
        "BREAKOUT": 35,
        "TREND_UP": 24,
        "RANGE": 4,
        "TREND_DOWN": -28,
        "OVERHEATED": -22,
        "PANIC": -55,
        "UNKNOWN": -18,
    }.get(str(regime).upper(), 0)


def _bounded(value: float) -> float:
    return max(min(value, 100.0), -100.0)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
