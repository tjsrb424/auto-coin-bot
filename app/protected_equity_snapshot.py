from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.controlled_auto_live import protected_position_scope_status
from app.database import insert_protected_equity_snapshot, load_protected_equity_snapshot
from app.live_broker import get_live_broker
from app.upbit import fetch_tickers

EQUITY_SNAPSHOT_TTL_SECONDS = int(os.getenv("PROTECTED_EQUITY_SNAPSHOT_TTL_SECONDS", "60"))
EQUITY_REFRESH_TIMEOUT_SECONDS = float(os.getenv("PROTECTED_EQUITY_REFRESH_TIMEOUT_SECONDS", "5"))
EQUITY_BALANCE_TIMEOUT_SECONDS = float(os.getenv("PROTECTED_EQUITY_BALANCE_TIMEOUT_SECONDS", "3"))
EQUITY_TICKER_TIMEOUT_SECONDS = float(os.getenv("PROTECTED_EQUITY_TICKER_TIMEOUT_SECONDS", "3"))

_EQUITY_REFRESH_LOCK = threading.Lock()


class ProtectedEquitySnapshotTimeout(TimeoutError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _plus_seconds(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = str(value).replace(" ", "T")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    if "+" not in normalized[-6:] and "-" not in normalized[-6:]:
        normalized = f"{normalized}+00:00"
    try:
        return datetime.fromisoformat(normalized).astimezone(timezone.utc)
    except ValueError:
        return None


def _duration_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _balance_total(item: dict | None) -> float:
    return _float((item or {}).get("balance")) + _float((item or {}).get("locked"))


def _coin_symbols(balances: dict) -> list[str]:
    symbols = []
    for symbol, item in (balances.get("by_currency") or {}).items():
        symbol = str(symbol or "").upper()
        if not symbol or symbol == "KRW":
            continue
        if _balance_total(item) > 0:
            symbols.append(symbol)
    return sorted(dict.fromkeys(symbols))


async def _ticker_prices(exchange: str, markets: list[str]) -> dict[str, float]:
    if not markets:
        return {}
    broker = get_live_broker(exchange)
    base_url = str(getattr(getattr(broker, "config", None), "base_url", "") or "").rstrip("/")
    prices: dict[str, float] = {}
    try:
        tickers = await fetch_tickers(markets, base_url=base_url)
    except Exception:
        tickers = []
    for item in tickers:
        market = str(item.get("market") or "")
        price = _float(item.get("trade_price") or item.get("close_price"))
        if market and price > 0:
            prices[market] = price
    missing = [market for market in markets if market not in prices]
    for market in missing:
        try:
            tickers = await fetch_tickers([market], base_url=base_url)
        except Exception:
            tickers = []
        for item in tickers:
            price = _float(item.get("trade_price") or item.get("close_price"))
            if price > 0:
                prices[market] = price
                break
        if market not in prices and exchange == "bithumb":
            legacy_price = await _legacy_bithumb_ticker_price(base_url, market)
            if legacy_price > 0:
                prices[market] = legacy_price
    return prices


async def _legacy_bithumb_ticker_price(base_url: str, market: str) -> float:
    symbol = str(market or "").split("-")[-1].upper()
    if not symbol:
        return 0.0
    try:
        async with httpx.AsyncClient(timeout=max(EQUITY_TICKER_TIMEOUT_SECONDS, 0.1)) as client:
            response = await client.get(f"{base_url.rstrip('/')}/public/ticker/{symbol}_KRW")
            response.raise_for_status()
            payload = response.json()
    except Exception:
        return 0.0
    data = payload.get("data") if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        return 0.0
    return _float(data.get("closing_price") or data.get("trade_price"))


async def _refresh_impl(exchange: str) -> dict:
    broker = get_live_broker(exchange)
    balances = await asyncio.wait_for(
        broker.get_balances(),
        timeout=max(EQUITY_BALANCE_TIMEOUT_SECONDS, 0.1),
    )
    symbols = _coin_symbols(balances)
    markets = [f"KRW-{symbol}" for symbol in symbols]
    prices = await asyncio.wait_for(
        _ticker_prices(exchange, markets),
        timeout=max(EQUITY_TICKER_TIMEOUT_SECONDS, 0.1),
    )
    by_currency = balances.get("by_currency") or {}
    cash_krw = _balance_total(by_currency.get("KRW"))
    valuations = []
    missing_prices = []
    coin_valuation_krw = 0.0
    for symbol in symbols:
        market = f"KRW-{symbol}"
        quantity = _balance_total(by_currency.get(symbol))
        price = _float(prices.get(market))
        value = quantity * price if price > 0 else 0.0
        if quantity > 0 and price <= 0:
            missing_prices.append(market)
        coin_valuation_krw += value
        valuations.append(
            {
                "symbol": symbol,
                "market": market,
                "quantity": quantity,
                "price_krw": price,
                "value_krw": value,
            }
        )
    scope = protected_position_scope_status(exchange=exchange)
    refresh_status = "SUCCESS" if not missing_prices else "PARTIAL"
    return {
        "cash_krw": cash_krw,
        "coin_valuation_krw": coin_valuation_krw,
        "total_equity_krw": cash_krw + coin_valuation_krw,
        "positions_count": int(scope.get("total_open_position_count") or 0),
        "legacy_positions_count": int(scope.get("legacy_open_position_count") or 0),
        "protected_positions_count": int(scope.get("protected_open_position_count") or 0),
        "valuation_symbols": valuations,
        "valuation_source": "exchange_ticker",
        "refresh_status": refresh_status,
        "error_message": f"MISSING_TICKER_PRICE:{','.join(missing_prices)}" if missing_prices else "",
        "raw_snapshot": {
            "balance_fetched_at": balances.get("fetched_at"),
            "priced_markets": sorted(prices.keys()),
            "missing_price_markets": missing_prices,
            "protected_position_scope": scope,
        },
    }


def _with_freshness(snapshot: dict | None) -> dict | None:
    if not snapshot:
        return None
    now = datetime.now(timezone.utc)
    created = _parse_utc(str(snapshot.get("created_at_utc") or ""))
    expires = _parse_utc(str(snapshot.get("expires_at_utc") or ""))
    snapshot["is_fresh"] = bool(expires and expires > now and str(snapshot.get("refresh_status") or "").upper() == "SUCCESS")
    snapshot["age_seconds"] = int((now - created).total_seconds()) if created else None
    snapshot["refresh_in_progress"] = _EQUITY_REFRESH_LOCK.locked()
    return snapshot


def load_cached_protected_equity_snapshot(exchange: str = "bithumb") -> dict:
    snapshot = _with_freshness(load_protected_equity_snapshot(exchange=exchange))
    return {
        "ok": True,
        "exchange": exchange,
        "refresh_in_progress": _EQUITY_REFRESH_LOCK.locked(),
        "snapshot": snapshot,
        "equity_snapshot_fresh": bool(snapshot and snapshot.get("is_fresh")),
        "equity_snapshot_status": _equity_status_from_snapshot(snapshot),
    }


def _equity_status_from_snapshot(snapshot: dict | None) -> str:
    if not snapshot:
        return "EQUITY_SNAPSHOT_REFRESH_REQUIRED"
    if not snapshot.get("is_fresh"):
        return "EQUITY_SNAPSHOT_STALE"
    return "EQUITY_SNAPSHOT_READY"


def _failure_snapshot(*, exchange: str, started: float, refresh_status: str, error_message: str) -> dict:
    snapshot = insert_protected_equity_snapshot(
        {
            "equity_snapshot_id": f"equity-{utc_now().replace(':', '').replace('-', '').replace('Z', '')}-{uuid.uuid4().hex[:6]}",
            "exchange_name": exchange,
            "created_at_utc": utc_now(),
            "expires_at_utc": utc_now(),
            "refresh_status": refresh_status,
            "refresh_duration_ms": _duration_ms(started),
            "error_message": error_message,
            "raw_snapshot": {"error_message": error_message},
        }
    )
    return _with_freshness(snapshot) or snapshot


async def refresh_protected_equity_snapshot(*, exchange: str = "bithumb") -> dict:
    if not _EQUITY_REFRESH_LOCK.acquire(blocking=False):
        cached = load_cached_protected_equity_snapshot(exchange)
        return {
            **cached,
            "ok": False,
            "status": "REFRESH_IN_PROGRESS",
            "message": "Protected equity snapshot refresh is already running.",
        }
    started = time.monotonic()
    exchange = (exchange or "bithumb").strip().lower()
    try:
        try:
            details = await asyncio.wait_for(
                _refresh_impl(exchange),
                timeout=max(EQUITY_REFRESH_TIMEOUT_SECONDS, 0.1),
            )
        except asyncio.TimeoutError:
            snapshot = _failure_snapshot(
                exchange=exchange,
                started=started,
                refresh_status="TIMEOUT",
                error_message="Protected equity snapshot refresh timed out.",
            )
            return _response(snapshot, ok=False)
        except Exception as exc:
            snapshot = _failure_snapshot(
                exchange=exchange,
                started=started,
                refresh_status="FAILED",
                error_message=f"{exc.__class__.__name__}:{str(exc)[:220]}",
            )
            return _response(snapshot, ok=False)
        snapshot = insert_protected_equity_snapshot(
            {
                "equity_snapshot_id": f"equity-{utc_now().replace(':', '').replace('-', '').replace('Z', '')}-{uuid.uuid4().hex[:6]}",
                "exchange_name": exchange,
                "created_at_utc": utc_now(),
                "expires_at_utc": _plus_seconds(EQUITY_SNAPSHOT_TTL_SECONDS),
                "refresh_duration_ms": _duration_ms(started),
                **details,
            }
        )
        return _response(snapshot, ok=str(snapshot.get("refresh_status") or "").upper() == "SUCCESS")
    finally:
        _EQUITY_REFRESH_LOCK.release()


def _response(snapshot: dict, *, ok: bool) -> dict:
    fresh = _with_freshness(snapshot) or snapshot
    return {
        "ok": ok,
        "status": fresh.get("refresh_status"),
        "snapshot": fresh,
        "equity_snapshot_id": fresh.get("equity_snapshot_id"),
        "equity_snapshot_fresh": bool(fresh.get("is_fresh")),
        "total_equity_krw": fresh.get("total_equity_krw"),
        "refresh_duration_ms": fresh.get("refresh_duration_ms"),
        "error_message": fresh.get("error_message") or "",
    }
