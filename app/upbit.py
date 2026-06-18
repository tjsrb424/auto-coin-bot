from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Any

import httpx

UPBIT_BASE_URL = "https://api.upbit.com"
SUPPORTED_UNITS = {1, 3, 5, 10, 15, 30, 60, 240}
UPBIT_PUBLIC_BATCH_DELAY_SECONDS = float(os.getenv("UPBIT_PUBLIC_BATCH_DELAY_SECONDS", "0.12"))
UPBIT_PUBLIC_MAX_RETRIES = int(os.getenv("UPBIT_PUBLIC_MAX_RETRIES", "5"))


class UpbitClientError(RuntimeError):
    pass


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return max(float(retry_after), UPBIT_PUBLIC_BATCH_DELAY_SECONDS)
        except ValueError:
            pass
    return min(0.5 * (2**attempt), 4.0)


async def _get_public_json(client: httpx.AsyncClient, url: str, params: dict[str, Any], label: str) -> Any:
    for attempt in range(UPBIT_PUBLIC_MAX_RETRIES + 1):
        response = await client.get(url, params=params)
        if response.status_code == 429 and attempt < UPBIT_PUBLIC_MAX_RETRIES:
            await asyncio.sleep(_retry_delay(response, attempt))
            continue
        if response.status_code >= 400:
            raise UpbitClientError(f"{label} failed: {response.text}")
        return response.json()
    raise UpbitClientError(f"{label} failed: too many requests")


async def _pace_next_public_batch(remaining: int) -> None:
    if remaining > 0 and UPBIT_PUBLIC_BATCH_DELAY_SECONDS > 0:
        await asyncio.sleep(UPBIT_PUBLIC_BATCH_DELAY_SECONDS)


async def fetch_minute_candles(
    market: str = "KRW-BTC",
    unit: int = 1,
    count: int = 200,
    to: str | None = None,
) -> list[dict[str, Any]]:
    if unit not in SUPPORTED_UNITS:
        raise UpbitClientError("Unsupported minute candle unit.")
    if count < 1 or count > 20000:
        raise UpbitClientError("Candle count must be between 1 and 20000.")

    collected: list[dict[str, Any]] = []
    cursor = to
    remaining = count

    async with httpx.AsyncClient(timeout=10.0) as client:
        while remaining > 0:
            batch_count = min(remaining, 200)
            params: dict[str, Any] = {"market": market, "count": batch_count}
            if cursor:
                params["to"] = cursor
            batch = await _get_public_json(
                client,
                f"{UPBIT_BASE_URL}/v1/candles/minutes/{unit}",
                params,
                "Upbit minute candle fetch",
            )
            if not batch:
                break
            collected.extend(batch)
            remaining -= len(batch)
            oldest = min(item["candle_date_time_utc"] for item in batch)
            cursor = datetime.fromisoformat(oldest).strftime("%Y-%m-%dT%H:%M:%S")
            if len(batch) < batch_count:
                break
            await _pace_next_public_batch(remaining)

    return sorted(collected, key=lambda item: item["candle_date_time_utc"])


async def fetch_day_candles(
    market: str = "KRW-BTC",
    count: int = 200,
    to: str | None = None,
) -> list[dict[str, Any]]:
    if count < 1 or count > 20000:
        raise UpbitClientError("Candle count must be between 1 and 20000.")

    collected: list[dict[str, Any]] = []
    cursor = to
    remaining = count

    async with httpx.AsyncClient(timeout=10.0) as client:
        while remaining > 0:
            batch_count = min(remaining, 200)
            params: dict[str, Any] = {"market": market, "count": batch_count}
            if cursor:
                params["to"] = cursor
            batch = await _get_public_json(
                client,
                f"{UPBIT_BASE_URL}/v1/candles/days",
                params,
                "Upbit day candle fetch",
            )
            if not batch:
                break
            for item in batch:
                item["unit"] = 1440
            collected.extend(batch)
            remaining -= len(batch)
            oldest = min(item["candle_date_time_utc"] for item in batch)
            cursor = datetime.fromisoformat(oldest).strftime("%Y-%m-%dT%H:%M:%S")
            if len(batch) < batch_count:
                break
            await _pace_next_public_batch(remaining)

    return sorted(collected, key=lambda item: item["candle_date_time_utc"])


async def fetch_tickers(markets: list[str], *, base_url: str = UPBIT_BASE_URL) -> list[dict[str, Any]]:
    unique_markets = [market for market in dict.fromkeys(markets) if market]
    if not unique_markets:
        return []

    async with httpx.AsyncClient(timeout=10.0) as client:
        payload = await _get_public_json(
            client,
            f"{base_url.rstrip('/')}/v1/ticker",
            {"markets": ",".join(unique_markets)},
            "Upbit ticker fetch",
        )
        if not isinstance(payload, list):
            raise UpbitClientError("Upbit ticker response format is invalid.")
        return payload
