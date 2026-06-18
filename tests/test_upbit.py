from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx

from app import upbit


def response(status_code: int, payload: object) -> httpx.Response:
    return httpx.Response(status_code, json=payload, request=httpx.Request("GET", "https://api.upbit.com/test"))


class FakeAsyncClient:
    def __init__(self, responses: list[httpx.Response], **_: object) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    async def __aenter__(self) -> "FakeAsyncClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def get(self, url: str, params: dict) -> httpx.Response:
        self.calls.append({"url": url, "params": params})
        return self.responses.pop(0)


class UpbitClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_minute_candles_retries_rate_limit(self) -> None:
        candle = {
            "market": "KRW-BTC",
            "candle_date_time_utc": "2026-06-18T03:00:00",
            "opening_price": 1,
            "high_price": 1,
            "low_price": 1,
            "trade_price": 1,
            "candle_acc_trade_volume": 1,
            "candle_acc_trade_price": 1,
        }
        fake = FakeAsyncClient([response(429, {"name": "too_many_requests"}), response(200, [candle])])

        with (
            patch.object(upbit.httpx, "AsyncClient", return_value=fake),
            patch.object(upbit.asyncio, "sleep", new_callable=AsyncMock) as sleep,
            patch.object(upbit, "UPBIT_PUBLIC_BATCH_DELAY_SECONDS", 0),
        ):
            candles = await upbit.fetch_minute_candles(count=1)

        self.assertEqual(candles, [candle])
        self.assertEqual(len(fake.calls), 2)
        sleep.assert_awaited_once()

    async def test_fetch_minute_candles_paces_multi_batch_requests(self) -> None:
        base = datetime(2026, 6, 18, 3, 0, 0)
        first_batch = [
            {"candle_date_time_utc": (base - timedelta(minutes=minute)).isoformat()}
            for minute in range(200)
        ]
        second_batch = [{"candle_date_time_utc": "2026-06-17T23:00:00"}]
        fake = FakeAsyncClient([response(200, first_batch), response(200, second_batch)])

        with (
            patch.object(upbit.httpx, "AsyncClient", return_value=fake),
            patch.object(upbit.asyncio, "sleep", new_callable=AsyncMock) as sleep,
            patch.object(upbit, "UPBIT_PUBLIC_BATCH_DELAY_SECONDS", 0.12),
        ):
            candles = await upbit.fetch_minute_candles(count=201)

        self.assertEqual(len(candles), 201)
        self.assertEqual(len(fake.calls), 2)
        sleep.assert_awaited_once_with(0.12)


if __name__ == "__main__":
    unittest.main()
