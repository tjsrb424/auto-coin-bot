from __future__ import annotations

from app.database import insert_candles, load_candles
from app.forward_paper import latest_completed_candle
from app.upbit import fetch_minute_candles


async def one_minute_liquidity_snapshot(market: str, *, require_completed: bool = True) -> dict:
    try:
        fresh = await fetch_minute_candles(market=market, unit=1, count=10)
        insert_candles(fresh)
        candles = load_candles(market, 1, 10)
        latest = latest_completed_candle(candles, 1) if require_completed else (candles[-1] if candles else None)
        if latest is None:
            return {"liquidity_check_required": True}
        latest_time = str(latest["candle_time_utc"])
        recent = [candle for candle in candles if str(candle["candle_time_utc"]) <= latest_time][-5:]
        volumes = [float(candle.get("candle_acc_trade_price") or 0.0) for candle in recent]
        avg_5m = sum(volumes) / len(volumes) if volumes else 0.0
        return {
            "liquidity_check_required": True,
            "liquidity_candle_unit": 1,
            "liquidity_candle_time_utc": latest_time,
            "current_1m_trade_price_volume": float(latest.get("candle_acc_trade_price") or 0.0),
            "recent_5m_avg_trade_price_volume": avg_5m,
            "recent_5m_volume_count": len(volumes),
        }
    except Exception:
        return {"liquidity_check_required": True}
