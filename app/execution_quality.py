from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def build_execution_quality_payload(
    *,
    order_log: dict,
    market_regime: str | None = None,
    sizing: dict | None = None,
    orderbook_top: dict | None = None,
    current_price_at_signal: float | None = None,
) -> dict:
    submitted_at = order_log.get("created_at") or order_log.get("updated_at")
    filled_at = order_log.get("updated_at") if str(order_log.get("status")) == "FILLED" else None
    filled_price = _filled_price(order_log)
    order_price = _float(order_log.get("price"))
    spread_pct = _float((orderbook_top or {}).get("spread_pct"), None)
    slippage_pct = _pct(filled_price - order_price, order_price) if filled_price and order_price else None
    fill_time = _seconds_between(submitted_at, filled_at)
    return {
        "request_id": order_log.get("request_id"),
        "order_log_id": order_log.get("id"),
        "signal_time_utc": order_log.get("created_at"),
        "candle_time_utc": order_log.get("candle_time_utc"),
        "exchange": order_log.get("exchange", "bithumb"),
        "market": order_log.get("market", "KRW-BTC"),
        "strategy_name": order_log.get("strategy_name") or (sizing or {}).get("strategy_name") or "",
        "market_regime": market_regime or (sizing or {}).get("market_regime") or "",
        "requested_order_krw": (sizing or {}).get("requested_order_krw") or (sizing or {}).get("amount_requested_krw") or order_log.get("amount_krw"),
        "available_krw": (sizing or {}).get("available_krw") or (sizing or {}).get("available_krw_balance"),
        "actual_order_krw": (sizing or {}).get("actual_order_krw") or (sizing or {}).get("capped_order_amount_krw") or order_log.get("amount_krw"),
        "order_price": order_price,
        "current_price_at_signal": current_price_at_signal,
        "best_bid": (orderbook_top or {}).get("best_bid"),
        "best_ask": (orderbook_top or {}).get("best_ask"),
        "spread_pct": spread_pct,
        "estimated_slippage_pct": slippage_pct,
        "submitted_at": submitted_at,
        "filled_at": filled_at,
        "fill_time_seconds": fill_time,
        "filled_price": filled_price,
        "filled_volume": _float(order_log.get("executed_volume")),
        "unfilled_volume": _float(order_log.get("remaining_volume")),
        "cancel_after_seconds": (sizing or {}).get("cancel_after_seconds"),
        "cancel_reason": order_log.get("risk_result") if str(order_log.get("status")) == "CANCELED" else "",
        "post_fill_return_1m": None,
        "post_fill_return_3m": None,
        "post_fill_return_5m": None,
        "adverse_selection_pct": None,
    }


def summarize_execution_quality(rows: list[dict]) -> dict:
    total = len(rows)
    if total <= 0:
        return {
            "order_count": 0,
            "fill_rate": 0.0,
            "cancel_rate": 0.0,
            "average_spread_pct": 0.0,
            "average_slippage_pct": 0.0,
            "average_adverse_selection_pct": 0.0,
            "average_fill_time_seconds": 0.0,
            "execution_score": 0.0,
        }
    filled = [row for row in rows if _float(row.get("filled_volume")) > 0 or row.get("filled_at")]
    canceled = [row for row in rows if row.get("cancel_reason")]
    avg_spread = _avg(row.get("spread_pct") for row in rows)
    avg_slippage = _avg(row.get("estimated_slippage_pct") for row in rows)
    avg_adverse = _avg(row.get("adverse_selection_pct") for row in rows)
    avg_fill_time = _avg(row.get("fill_time_seconds") for row in rows)
    score = max(0.0, 100.0 - avg_spread * 10 - max(avg_slippage, 0.0) * 20 - max(avg_adverse, 0.0) * 15 - (len(canceled) / total) * 30)
    return {
        "order_count": total,
        "fill_rate": len(filled) / total,
        "cancel_rate": len(canceled) / total,
        "average_spread_pct": avg_spread,
        "average_slippage_pct": avg_slippage,
        "average_adverse_selection_pct": avg_adverse,
        "average_fill_time_seconds": avg_fill_time,
        "execution_score": round(score, 4),
    }


def _filled_price(order_log: dict) -> float | None:
    amount = _float(order_log.get("filled_amount_krw"))
    volume = _float(order_log.get("executed_volume"))
    if amount > 0 and volume > 0:
        return amount / volume
    return _float(order_log.get("price"), None)


def _avg(values) -> float:
    nums = [_float(value, None) for value in values]
    nums = [value for value in nums if value is not None]
    return round(sum(nums) / len(nums), 6) if nums else 0.0


def _pct(delta: float, base: float) -> float:
    return delta / base * 100 if base else 0.0


def _seconds_between(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    parsed_start = _parse_utc(start)
    parsed_end = _parse_utc(end)
    if not parsed_start or not parsed_end:
        return None
    return max((parsed_end - parsed_start).total_seconds(), 0.0)


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
