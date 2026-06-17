from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

UPBIT_BASE_URL = "https://api.upbit.com"
SUPPORTED_UNITS = {1, 3, 5, 10, 15, 30, 60, 240}


class UpbitClientError(RuntimeError):
    pass


async def fetch_minute_candles(
    market: str = "KRW-BTC",
    unit: int = 1,
    count: int = 200,
    to: str | None = None,
) -> list[dict[str, Any]]:
    if unit not in SUPPORTED_UNITS:
        raise UpbitClientError("지원하지 않는 분봉 단위입니다.")
    if count < 1 or count > 20000:
        raise UpbitClientError("캔들 개수는 1~20000 범위여야 합니다.")

    collected: list[dict[str, Any]] = []
    cursor = to
    remaining = count

    async with httpx.AsyncClient(timeout=10.0) as client:
        while remaining > 0:
            batch_count = min(remaining, 200)
            params: dict[str, Any] = {"market": market, "count": batch_count}
            if cursor:
                params["to"] = cursor
            response = await client.get(
                f"{UPBIT_BASE_URL}/v1/candles/minutes/{unit}",
                params=params,
            )
            if response.status_code >= 400:
                raise UpbitClientError(f"업비트 캔들 조회 실패: {response.text}")
            batch = response.json()
            if not batch:
                break
            collected.extend(batch)
            remaining -= len(batch)
            oldest = min(item["candle_date_time_utc"] for item in batch)
            cursor = datetime.fromisoformat(oldest).strftime("%Y-%m-%dT%H:%M:%S")
            if len(batch) < batch_count:
                break

    return sorted(collected, key=lambda item: item["candle_date_time_utc"])


async def fetch_day_candles(
    market: str = "KRW-BTC",
    count: int = 200,
    to: str | None = None,
) -> list[dict[str, Any]]:
    if count < 1 or count > 20000:
        raise UpbitClientError("캔들 개수는 1~20000 범위여야 합니다.")

    collected: list[dict[str, Any]] = []
    cursor = to
    remaining = count

    async with httpx.AsyncClient(timeout=10.0) as client:
        while remaining > 0:
            batch_count = min(remaining, 200)
            params: dict[str, Any] = {"market": market, "count": batch_count}
            if cursor:
                params["to"] = cursor
            response = await client.get(
                f"{UPBIT_BASE_URL}/v1/candles/days",
                params=params,
            )
            if response.status_code >= 400:
                raise UpbitClientError(f"업비트 일봉 조회 실패: {response.text}")
            batch = response.json()
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

    return sorted(collected, key=lambda item: item["candle_date_time_utc"])


async def fetch_tickers(markets: list[str], *, base_url: str = UPBIT_BASE_URL) -> list[dict[str, Any]]:
    unique_markets = [market for market in dict.fromkeys(markets) if market]
    if not unique_markets:
        return []

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            f"{base_url.rstrip('/')}/v1/ticker",
            params={"markets": ",".join(unique_markets)},
        )
        if response.status_code >= 400:
            raise UpbitClientError(f"티커 조회 실패: {response.text}")
        payload = response.json()
        if not isinstance(payload, list):
            raise UpbitClientError("티커 응답 형식이 올바르지 않습니다.")
        return payload
