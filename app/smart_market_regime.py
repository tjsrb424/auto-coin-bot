from __future__ import annotations

from typing import Any


def classify_market_regime(features: dict) -> tuple[str, list[str], list[str]]:
    price = features.get("last_price")
    ma20 = features.get("ma_20")
    slope = features.get("ma_20_slope")
    rsi = features.get("rsi_14")
    ret_1h = features.get("recent_return_1h")
    vol_1h = features.get("volatility_1h")
    volume_ratio = features.get("volume_ratio_20")
    positives: list[str] = []
    negatives: list[str] = []
    if price is None or ma20 is None or slope is None:
        negatives.append("시장상태 판단에 필요한 가격/이평 데이터가 부족합니다.")
        return "UNKNOWN", positives, negatives
    if _float(ret_1h) <= -2 and _float(vol_1h) >= 2:
        negatives.append("단기 급락과 높은 변동성이 동시에 감지되었습니다.")
        return "PANIC", positives, negatives
    if rsi is not None and _float(rsi) >= 72 and _float(ret_1h) > 1:
        negatives.append("RSI 과열과 단기 상승이 겹쳐 추격매수 위험이 큽니다.")
        return "OVERHEATED", positives, negatives
    if _float(price) > _float(ma20) and _float(slope) > 0.03:
        positives.append("가격이 20MA 위에 있고 중기 기울기가 우상향입니다.")
        if volume_ratio is not None and _float(volume_ratio) >= 1.5:
            positives.append("거래량이 평균 대비 강하게 증가해 돌파 가능성을 높입니다.")
            return "BREAKOUT", positives, negatives
        return "TREND_UP", positives, negatives
    if _float(price) < _float(ma20) and _float(slope) < -0.03:
        negatives.append("가격이 20MA 아래에 있고 중기 기울기가 하락 중입니다.")
        return "TREND_DOWN", positives, negatives
    positives.append("뚜렷한 추세보다 박스권/대기 구간에 가깝습니다.")
    return "RANGE", positives, negatives


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
