from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

import httpx

from app.database import upsert_market_universe
from app.upbit import UPBIT_BASE_URL, UpbitClientError, fetch_minute_candles, fetch_tickers


EXCHANGE_BASE_URLS = {
    "upbit": UPBIT_BASE_URL,
    "bithumb": "https://api.bithumb.com",
}


async def fetch_krw_markets(*, exchange: str = "upbit") -> list[str]:
    base_url = EXCHANGE_BASE_URLS.get(exchange, UPBIT_BASE_URL)
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{base_url.rstrip('/')}/v1/market/all", params={"isDetails": "false"})
        if response.status_code >= 400:
            raise UpbitClientError(f"Market list fetch failed: {response.status_code} {response.text[:200]}")
        payload = response.json()
    if not isinstance(payload, list):
        raise UpbitClientError("Market list response format is invalid.")
    return sorted(
        str(item.get("market"))
        for item in payload
        if isinstance(item, dict) and str(item.get("market", "")).startswith("KRW-")
    )


def _float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        if math.isfinite(number):
            return number
    except (TypeError, ValueError):
        pass
    return default


def _score_ticker(ticker: dict) -> tuple[float, float, float, float]:
    trade_price_24h = _float(ticker.get("acc_trade_price_24h"))
    change_rate = abs(_float(ticker.get("signed_change_rate") or ticker.get("change_rate")))
    liquidity_score = min(trade_price_24h / 10_000_000_000 * 60, 60)
    volatility_score = max(0.0, 25 - min(change_rate * 100, 25))
    risk_score = max(0.0, 15 - max(change_rate * 100 - 8, 0))
    return liquidity_score + volatility_score + risk_score, liquidity_score, volatility_score, risk_score


async def _candle_diagnostics(market: str, *, base_url: str = UPBIT_BASE_URL) -> dict:
    try:
        candles = await fetch_minute_candles(market=market, unit=1, count=200, base_url=base_url)
    except Exception as exc:
        return {
            "available": False,
            "reason": f"candle fetch failed: {exc.__class__.__name__}",
            "volatility": 0.0,
            "count": 0,
        }
    if len(candles) < 200:
        return {
            "available": False,
            "reason": f"insufficient candles: {len(candles)}/200",
            "volatility": 0.0,
            "count": len(candles),
        }
    prices = [_float(candle.get("trade_price")) for candle in candles if _float(candle.get("trade_price")) > 0]
    if not prices:
        return {"available": False, "reason": "no valid candle prices", "volatility": 0.0, "count": len(candles)}
    avg_price = sum(prices) / len(prices)
    high = max(prices)
    low = min(prices)
    volatility = (high - low) / avg_price if avg_price > 0 else 0.0
    if volatility >= 0.35:
        return {"available": False, "reason": "volatility too high", "volatility": volatility, "count": len(candles)}
    return {"available": True, "reason": "scan passed", "volatility": volatility, "count": len(candles)}


async def scan_market_universe(
    *,
    exchange: str = "upbit",
    quote_currency: str = "KRW",
    top_n: int = 10,
    max_candidates: int = 20,
    min_24h_trade_price_krw: float = 500_000_000,
) -> dict:
    exchange = exchange.lower().strip() or "upbit"
    if quote_currency != "KRW":
        raise ValueError("Only KRW markets are supported for the initial scanner.")
    top_n = max(1, min(int(top_n), 20))
    max_candidates = max(top_n, min(int(max_candidates), 40))
    markets = await fetch_krw_markets(exchange=exchange)
    base_url = EXCHANGE_BASE_URLS.get(exchange, UPBIT_BASE_URL)
    tickers = await fetch_tickers(markets, base_url=base_url)
    ranked_tickers = sorted(tickers, key=lambda item: _float(item.get("acc_trade_price_24h")), reverse=True)

    scanned_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    rows: list[dict] = []
    for ticker in ranked_tickers[:max_candidates]:
        market = str(ticker.get("market", ""))
        if not market.startswith("KRW-"):
            continue
        score, liquidity_score, volatility_score, risk_score = _score_ticker(ticker)
        trade_price_24h = _float(ticker.get("acc_trade_price_24h"))
        candle = await _candle_diagnostics(market, base_url=base_url)
        enabled = trade_price_24h >= min_24h_trade_price_krw and bool(candle["available"])
        reason = candle["reason"] if not candle["available"] else ("low 24h trade price" if not enabled else "scan passed")
        if candle["available"]:
            volatility_score = max(0.0, volatility_score - min(_float(candle["volatility"]) * 100, 20))
            score = liquidity_score + volatility_score + risk_score
        rows.append(
            {
                "exchange": exchange,
                "market": market,
                "symbol": market.split("-")[-1],
                "quote_currency": quote_currency,
                "status": "DISCOVERED" if enabled else "REJECTED",
                "is_enabled": enabled,
                "is_live_allowed": False,
                "is_auto_selectable": enabled,
                "scan_rank": 0,
                "score": round(score, 2),
                "reason": reason,
                "min_24h_trade_price_krw": min_24h_trade_price_krw,
                "last_24h_trade_price_krw": trade_price_24h,
                "last_price": _float(ticker.get("trade_price") or ticker.get("prev_closing_price")),
                "last_change_rate": _float(ticker.get("signed_change_rate") or ticker.get("change_rate")),
                "last_volatility_score": round(volatility_score, 2),
                "last_liquidity_score": round(liquidity_score, 2),
                "last_risk_score": round(risk_score, 2),
                "last_scanned_at": scanned_at,
            }
        )

    accepted = sorted([row for row in rows if row["is_enabled"]], key=lambda row: row["score"], reverse=True)[:top_n]
    accepted_markets = {row["market"] for row in accepted}
    persisted_rows = []
    rank = 1
    for row in rows:
        if row["market"] in accepted_markets:
            row = {**row, "scan_rank": rank, "status": "DISCOVERED", "is_enabled": True, "is_auto_selectable": True}
            rank += 1
        else:
            row = {**row, "scan_rank": 999, "is_auto_selectable": False}
        persisted_rows.append(row)
    changed = upsert_market_universe(persisted_rows)
    return {
        "exchange": exchange,
        "quote_currency": quote_currency,
        "scanned_at": scanned_at,
        "requested_top_n": top_n,
        "market_count": len(markets),
        "persisted_count": changed,
        "accepted": accepted,
        "rejected": [row for row in persisted_rows if row["market"] not in accepted_markets],
    }
