from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from threading import Lock

import pandas as pd

from app.backtest import candles_to_frame
from app.database import (
    append_live_equity_point,
    insert_candles,
    insert_live_paper_order,
    load_candles,
    load_running_live_paper_sessions,
    mark_live_paper_session_error,
    update_live_paper_session_state,
)
from app.strategies import apply_strategy
from app.upbit import fetch_minute_candles

logger = logging.getLogger("uvicorn.error")
KST = timezone(timedelta(hours=9))
_live_tick_lock = Lock()


def _float_setting(settings: dict, key: str, default: float) -> float:
    value = settings.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _kst_date_key(timestamp_utc: str) -> str:
    normalized = timestamp_utc.removesuffix("Z")
    utc_dt = datetime.fromisoformat(normalized).replace(tzinfo=timezone.utc)
    return utc_dt.astimezone(KST).date().isoformat()


def _consecutive_losses(orders: list[dict]) -> int:
    count = 0
    for order in reversed(orders):
        if order["side"] != "SELL":
            continue
        pnl = order.get("realized_pnl")
        if pnl is not None and pnl < 0:
            count += 1
            continue
        break
    return count


def _day_start_equity(session: dict, date_key: str, fallback: float) -> float:
    for point in session.get("equity_curve", []):
        if _kst_date_key(point["time"]) == date_key:
            return float(point["equity"])
    return fallback


def _latest_signal(strategy: str, settings: dict, candles: list[dict], timestamp_utc: str) -> dict:
    context = [candle for candle in candles if candle["candle_time_utc"] <= timestamp_utc]
    frame = candles_to_frame(context)
    signal_frame = apply_strategy(strategy, frame, settings)
    last = signal_frame.iloc[-1]
    return {
        "signal": str(last["signal"]),
        "reason": str(last["reason"]),
        "price": float(last["close"]),
    }


class PaperBroker:
    def __init__(self, session: dict):
        self.session = session
        self.session_id = int(session["id"])
        self.market = session["market"]
        self.strategy = session["strategy"]
        self.risk = session.get("risk", {})
        self.cash = float(session["balance"]["cash_krw"])
        self.position_volume = float(session["position"]["btc_quantity"])
        self.average_entry_price = float(session["position"]["avg_buy_price"])
        self.realized_pnl = float(session["balance"]["realized_pnl"])

    def apply_candle(self, candle: dict, signal: str, reason: str) -> str:
        price = float(candle["trade_price"])
        high = float(candle["high_price"])
        low = float(candle["low_price"])
        timestamp_utc = candle["candle_time_utc"]
        last_signal = signal

        if signal == "BUY":
            self._try_buy(timestamp_utc, price, high, low, reason)
        elif signal == "SELL":
            self._try_sell(timestamp_utc, price, reason)

        self._persist_state(timestamp_utc, price, last_signal)
        return last_signal

    def _try_buy(self, timestamp_utc: str, price: float, high: float, low: float, reason: str) -> None:
        max_order_amount = _float_setting(self.risk, "max_order_amount", 100_000)
        daily_max_loss_rate = _float_setting(self.risk, "daily_max_loss_rate", 0.03)
        max_position_ratio = _float_setting(self.risk, "max_position_ratio", 0.5)
        consecutive_loss_limit = int(_float_setting(self.risk, "consecutive_loss_limit", 3))
        volatility_block_rate = _float_setting(self.risk, "volatility_block_rate", 0.03)
        fee_rate = _float_setting(self.risk, "fee_rate", 0.0005)
        slippage_rate = _float_setting(self.risk, "slippage_rate", 0.0005)

        equity_before = self.cash + self.position_volume * price
        date_key = _kst_date_key(timestamp_utc)
        day_start_equity = _day_start_equity(self.session, date_key, equity_before)
        day_loss_rate = (
            (equity_before - day_start_equity) / day_start_equity
            if day_start_equity > 0
            else 0.0
        )
        position_value = self.position_volume * price
        max_position_value = equity_before * max(max_position_ratio, 0)
        available_room = max_position_value - position_value
        order_amount = min(max_order_amount, self.cash, available_room)
        range_rate = (high - low) / price if price > 0 else 0.0

        block_reason = ""
        if day_loss_rate <= -abs(daily_max_loss_rate):
            block_reason = "daily_loss_limit"
        elif _consecutive_losses(self.session.get("orders", [])) >= max(consecutive_loss_limit, 0):
            block_reason = "consecutive_loss_limit"
        elif range_rate >= volatility_block_rate:
            block_reason = "volatility_block"
        elif order_amount <= 0:
            block_reason = "position_or_cash_limit"

        if block_reason:
            logger.info(
                "[paper-live] session=%s BUY blocked candle=%s reason=%s",
                self.session_id,
                timestamp_utc,
                block_reason,
            )
            return

        execution_price = price * (1 + max(slippage_rate, 0))
        fee = order_amount * fee_rate
        spend = max(order_amount - fee, 0)
        volume = spend / execution_price if execution_price > 0 else 0.0
        new_volume = self.position_volume + volume
        if new_volume > 0:
            self.average_entry_price = (
                (self.position_volume * self.average_entry_price) + (volume * execution_price)
            ) / new_volume
        self.position_volume = new_volume
        self.cash -= order_amount
        insert_live_paper_order(
            self.session_id,
            order_time=timestamp_utc,
            market=self.market,
            side="BUY",
            strategy=self.strategy,
            signal_price=price,
            execution_price=execution_price,
            quantity=volume,
            amount_krw=order_amount,
            fee=fee,
            realized_pnl=None,
            reason=reason,
        )
        logger.info(
            "[paper-live] session=%s BUY candle=%s price=%.0f volume=%.8f",
            self.session_id,
            timestamp_utc,
            execution_price,
            volume,
        )

    def _try_sell(self, timestamp_utc: str, price: float, reason: str) -> None:
        if self.position_volume <= 0:
            return

        fee_rate = _float_setting(self.risk, "fee_rate", 0.0005)
        slippage_rate = _float_setting(self.risk, "slippage_rate", 0.0005)
        execution_price = price * (1 - max(slippage_rate, 0))
        gross = self.position_volume * execution_price
        fee = gross * fee_rate
        net = gross - fee
        pnl = net - self.position_volume * self.average_entry_price
        sold_volume = self.position_volume
        self.cash += net
        self.realized_pnl += pnl
        self.position_volume = 0.0
        self.average_entry_price = 0.0
        insert_live_paper_order(
            self.session_id,
            order_time=timestamp_utc,
            market=self.market,
            side="SELL",
            strategy=self.strategy,
            signal_price=price,
            execution_price=execution_price,
            quantity=sold_volume,
            amount_krw=gross,
            fee=fee,
            realized_pnl=pnl,
            reason=reason,
        )
        logger.info(
            "[paper-live] session=%s SELL candle=%s price=%.0f volume=%.8f pnl=%.0f",
            self.session_id,
            timestamp_utc,
            execution_price,
            sold_volume,
            pnl,
        )

    def _persist_state(self, timestamp_utc: str, price: float, last_signal: str) -> None:
        unrealized_pnl = (
            (price - self.average_entry_price) * self.position_volume
            if self.position_volume > 0
            else 0.0
        )
        equity = self.cash + self.position_volume * price
        append_live_equity_point(
            self.session_id,
            timestamp_utc,
            equity,
            self.cash,
            self.position_volume,
            price,
        )
        update_live_paper_session_state(
            self.session_id,
            cash_balance=self.cash,
            btc_balance=self.position_volume,
            avg_buy_price=self.average_entry_price,
            current_price=price,
            equity=equity,
            realized_pnl=self.realized_pnl,
            unrealized_pnl=unrealized_pnl,
            last_processed_candle_time_utc=timestamp_utc,
            last_signal=last_signal,
        )


async def process_running_live_paper_sessions() -> dict:
    if not _live_tick_lock.acquire(blocking=False):
        logger.info("[paper-live] tick skipped because previous tick is still running")
        return {"processed_sessions": 0, "skipped": True}
    try:
        sessions = load_running_live_paper_sessions()
        logger.info("[paper-live] tick started running_sessions=%s", len(sessions))
        processed_sessions = 0
        processed_candles = 0
        created_orders_before = 0
        for session in sessions:
            try:
                before_order_count = len(session.get("orders", []))
                candle_count = await process_live_paper_session(session)
                refreshed = len(load_running_live_paper_sessions())
                _ = refreshed
                processed_sessions += 1
                processed_candles += candle_count
                created_orders_before += before_order_count
            except Exception as exc:  # pragma: no cover - defensive scheduler boundary
                logger.exception("[paper-live] session=%s ERROR %s", session.get("id"), exc)
                mark_live_paper_session_error(int(session["id"]), str(exc))
        logger.info(
            "[paper-live] tick finished processed_sessions=%s processed_candles=%s",
            processed_sessions,
            processed_candles,
        )
        return {
            "processed_sessions": processed_sessions,
            "processed_candles": processed_candles,
            "order_count_before": created_orders_before,
            "skipped": False,
        }
    finally:
        _live_tick_lock.release()


async def process_live_paper_session(session: dict) -> int:
    market = session["market"]
    unit = int(session["unit"])
    fresh = await fetch_minute_candles(market=market, unit=unit, count=200)
    insert_candles(fresh)
    candles = load_candles(market, unit, 300)
    last_processed = session.get("last_processed_candle_time_utc")
    new_candles = [
        candle for candle in candles if last_processed is None or candle["candle_time_utc"] > last_processed
    ]
    if not new_candles:
        logger.info(
            "[paper-live] session=%s no new candle after=%s",
            session["id"],
            last_processed,
        )
        return 0

    broker = PaperBroker(session)
    for candle in new_candles:
        signal_info = _latest_signal(
            session["strategy"],
            session.get("settings", {}),
            candles,
            candle["candle_time_utc"],
        )
        broker.apply_candle(
            candle,
            signal_info["signal"],
            signal_info["reason"],
        )
    return len(new_candles)


def run_scheduler_tick() -> None:
    asyncio.run(process_running_live_paper_sessions())
