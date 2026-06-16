from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from threading import Lock

from app.backtest import candles_to_frame
from app.database import (
    append_forward_equity_point,
    insert_candles,
    insert_forward_order,
    insert_forward_signal_log,
    insert_forward_tick_log,
    load_candles,
    load_running_forward_sessions,
    mark_forward_session_error,
    update_forward_session_state,
)
from app.strategies import apply_strategy
from app.upbit import fetch_minute_candles

logger = logging.getLogger("uvicorn.error")
KST = timezone(timedelta(hours=9))
_forward_tick_lock = Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace(" ", "T")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    if "+" not in normalized[-6:] and "-" not in normalized[-6:]:
        normalized = f"{normalized}+00:00"
    try:
        return datetime.fromisoformat(normalized).astimezone(timezone.utc)
    except ValueError:
        return None


def _float_setting(settings: dict, key: str, default: float) -> float:
    value = settings.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _kst_date_key(timestamp_utc: str) -> str:
    parsed = _parse_utc(timestamp_utc)
    if parsed is None:
        return datetime.now(KST).date().isoformat()
    return parsed.astimezone(KST).date().isoformat()


def _is_completed_candle(timestamp_utc: str, unit: int, now_utc: datetime | None = None) -> bool:
    candle_time = _parse_utc(timestamp_utc)
    if candle_time is None:
        return False
    now = now_utc or datetime.now(timezone.utc).replace(second=0, microsecond=0)
    return candle_time + timedelta(minutes=unit) <= now


def _completed_candles_after(candles: list[dict], unit: int, last_processed: str | None) -> list[dict]:
    last_processed_dt = _parse_utc(last_processed)
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    completed = []
    for candle in candles:
        candle_dt = _parse_utc(candle["candle_time_utc"])
        if candle_dt is None or not _is_completed_candle(candle["candle_time_utc"], unit, now):
            continue
        if last_processed_dt is not None and candle_dt <= last_processed_dt:
            continue
        completed.append(candle)
    return completed


def latest_completed_candle(candles: list[dict], unit: int) -> dict | None:
    completed = [candle for candle in candles if _is_completed_candle(candle["candle_time_utc"], unit)]
    return completed[-1] if completed else None


def _latest_signal(strategy: str, settings: dict, candles: list[dict], timestamp_utc: str) -> dict:
    context = [candle for candle in candles if (_parse_utc(candle["candle_time_utc"]) or datetime.min.replace(tzinfo=timezone.utc)) <= (_parse_utc(timestamp_utc) or datetime.max.replace(tzinfo=timezone.utc))]
    frame = candles_to_frame(context)
    signal_frame = apply_strategy(strategy, frame, settings)
    last = signal_frame.iloc[-1]
    return {
        "signal": str(last["signal"]),
        "reason": str(last["reason"]),
        "price": float(last["close"]),
    }


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


def _peak_equity(session: dict, current_equity: float) -> float:
    values = [float(point["equity"]) for point in session.get("equity_curve", [])]
    values.append(current_equity)
    return max(values) if values else current_equity


class ForwardPaperBroker:
    def __init__(self, session: dict):
        self.session = session
        self.session_id = int(session["id"])
        self.candidate_strategy_id = int(session["candidate_strategy_id"])
        self.market = session["market"]
        self.unit = int(session["unit"])
        self.strategy = session["strategy"]
        self.risk = session.get("risk", {})
        self.cash = float(session["balance"]["cash_krw"])
        self.position_volume = float(session["position"]["btc_quantity"])
        self.average_entry_price = float(session["position"]["avg_buy_price"])
        self.realized_pnl = float(session["balance"]["realized_pnl"])
        metrics = session.get("metrics", {})
        self.trade_count = int(metrics.get("trade_count", 0))
        self.win_count = 0
        self.loss_count = 0
        self.gross_profit = 0.0
        self.gross_loss = 0.0
        for order in session.get("orders", []):
            if order["side"] != "SELL" or order.get("realized_pnl") is None:
                continue
            pnl = float(order["realized_pnl"])
            if pnl > 0:
                self.win_count += 1
                self.gross_profit += pnl
            else:
                self.loss_count += 1
                self.gross_loss += abs(pnl)

    def apply_candle(self, candle: dict, signal: str, reason: str, tick_time_utc: str) -> str:
        price = float(candle["trade_price"])
        high = float(candle["high_price"])
        low = float(candle["low_price"])
        volume = float(candle["candle_acc_trade_volume"])
        timestamp_utc = candle["candle_time_utc"]
        risk_result = "PASS"

        if signal == "BUY":
            risk_result = self._try_buy(timestamp_utc, price, high, low, volume, reason)
        elif signal == "SELL":
            risk_result = self._try_sell(timestamp_utc, price, reason)
        else:
            risk_result = "HOLD"

        insert_forward_signal_log(
            signal_time_utc=tick_time_utc,
            session_id=self.session_id,
            strategy=self.strategy,
            signal=signal,
            confidence=1.0,
            reason=reason or "hold",
            risk_result=risk_result,
            candle_time_utc=timestamp_utc,
        )
        self._persist_state(timestamp_utc, price, signal, risk_result, tick_time_utc)
        return risk_result

    def _try_buy(self, timestamp_utc: str, price: float, high: float, low: float, volume: float, reason: str) -> str:
        max_order_amount = _float_setting(self.risk, "max_order_amount", 100_000)
        daily_max_loss_rate = _float_setting(self.risk, "daily_max_loss_rate", 0.03)
        max_position_ratio = _float_setting(self.risk, "max_position_ratio", 0.5)
        consecutive_loss_limit = int(_float_setting(self.risk, "consecutive_loss_limit", 3))
        volatility_block_rate = _float_setting(self.risk, "volatility_block_rate", 0.03)
        min_volume = _float_setting(self.risk, "min_volume", 0.0)
        fee_rate = max(_float_setting(self.risk, "fee_rate", 0.0005), 0.0)
        slippage_rate = max(_float_setting(self.risk, "slippage_rate", 0.0005), 0.0)

        equity_before = self.cash + self.position_volume * price
        date_key = _kst_date_key(timestamp_utc)
        day_start_equity = _day_start_equity(self.session, date_key, equity_before)
        day_loss_rate = (equity_before - day_start_equity) / day_start_equity if day_start_equity > 0 else 0.0
        position_value = self.position_volume * price
        max_position_value = equity_before * max(max_position_ratio, 0.0)
        available_room = max_position_value - position_value
        range_rate = (high - low) / price if price > 0 else 0.0

        if self.cash <= 0:
            return "BLOCKED_BY_INSUFFICIENT_BALANCE"
        if max_order_amount <= 0:
            return "BLOCKED_BY_MAX_ORDER_AMOUNT"
        if available_room <= 0:
            return "BLOCKED_BY_MAX_POSITION_RATIO"
        if day_loss_rate <= -abs(daily_max_loss_rate):
            return "BLOCKED_BY_DAILY_LOSS_LIMIT"
        if _consecutive_losses(self.session.get("orders", [])) >= max(consecutive_loss_limit, 0):
            return "BLOCKED_BY_CONSECUTIVE_LOSS_LIMIT"
        if range_rate >= volatility_block_rate:
            return "BLOCKED_BY_VOLATILITY_FILTER"
        if min_volume > 0 and volume < min_volume:
            return "BLOCKED_BY_LOW_VOLUME"

        order_amount = min(max_order_amount, self.cash, available_room)
        if order_amount <= 0:
            return "BLOCKED_BY_INSUFFICIENT_BALANCE"

        execution_price = price * (1 + slippage_rate)
        fee = order_amount * fee_rate
        spend = max(order_amount - fee, 0.0)
        bought_volume = spend / execution_price if execution_price > 0 else 0.0
        if bought_volume <= 0:
            return "BLOCKED_BY_INSUFFICIENT_BALANCE"

        new_volume = self.position_volume + bought_volume
        self.average_entry_price = (
            ((self.position_volume * self.average_entry_price) + (bought_volume * execution_price)) / new_volume
            if new_volume > 0
            else 0.0
        )
        self.position_volume = new_volume
        self.cash = max(self.cash - order_amount, 0.0)
        self.trade_count += 1
        insert_forward_order(
            self.session_id,
            {
                "candidate_strategy_id": self.candidate_strategy_id,
                "market": self.market,
                "unit": self.unit,
                "strategy": self.strategy,
                "side": "BUY",
                "price": execution_price,
                "volume": bought_volume,
                "amount_krw": order_amount,
                "fee": fee,
                "slippage": slippage_rate,
                "realized_pnl": None,
                "reason": reason,
                "risk_result": "PASS",
                "candle_time_utc": timestamp_utc,
                "created_at": _utc_now(),
            },
        )
        logger.info("[paper-forward] session=%s BUY candle=%s price=%.0f volume=%.8f", self.session_id, timestamp_utc, execution_price, bought_volume)
        return "PASS"

    def _try_sell(self, timestamp_utc: str, price: float, reason: str) -> str:
        if self.position_volume <= 0:
            return "BLOCKED_BY_INSUFFICIENT_POSITION"

        fee_rate = max(_float_setting(self.risk, "fee_rate", 0.0005), 0.0)
        slippage_rate = max(_float_setting(self.risk, "slippage_rate", 0.0005), 0.0)
        execution_price = price * (1 - slippage_rate)
        sold_volume = self.position_volume
        gross = sold_volume * execution_price
        fee = gross * fee_rate
        net = max(gross - fee, 0.0)
        pnl = net - sold_volume * self.average_entry_price
        self.cash += net
        self.realized_pnl += pnl
        if pnl > 0:
            self.win_count += 1
            self.gross_profit += pnl
        else:
            self.loss_count += 1
            self.gross_loss += abs(pnl)
        self.position_volume = 0.0
        self.average_entry_price = 0.0
        self.trade_count += 1
        insert_forward_order(
            self.session_id,
            {
                "candidate_strategy_id": self.candidate_strategy_id,
                "market": self.market,
                "unit": self.unit,
                "strategy": self.strategy,
                "side": "SELL",
                "price": execution_price,
                "volume": sold_volume,
                "amount_krw": gross,
                "fee": fee,
                "slippage": slippage_rate,
                "realized_pnl": pnl,
                "reason": reason,
                "risk_result": "PASS",
                "candle_time_utc": timestamp_utc,
                "created_at": _utc_now(),
            },
        )
        logger.info("[paper-forward] session=%s SELL candle=%s price=%.0f volume=%.8f pnl=%.0f", self.session_id, timestamp_utc, execution_price, sold_volume, pnl)
        return "PASS"

    def _persist_state(self, timestamp_utc: str, price: float, last_signal: str, risk_result: str, tick_time_utc: str) -> None:
        unrealized_pnl = (price - self.average_entry_price) * self.position_volume if self.position_volume > 0 else 0.0
        equity = self.cash + self.position_volume * price
        initial_balance = float(self.session["balance"]["initial_cash"])
        peak = _peak_equity(self.session, equity)
        drawdown = (equity - peak) / peak if peak > 0 else 0.0
        max_drawdown = max(float(self.session["metrics"].get("mdd", 0.0)), abs(drawdown))
        closed_count = self.win_count + self.loss_count
        win_rate = self.win_count / closed_count if closed_count else 0.0
        profit_factor = self.gross_profit / self.gross_loss if self.gross_loss > 0 else (999.0 if self.gross_profit > 0 else 0.0)

        append_forward_equity_point(
            self.session_id,
            candle_time_utc=timestamp_utc,
            equity=equity,
            cash_balance=self.cash,
            position_volume=self.position_volume,
            price=price,
            drawdown=drawdown,
        )
        update_forward_session_state(
            self.session_id,
            {
                "status": "RUNNING",
                "current_balance_krw": self.cash,
                "current_position_volume": self.position_volume,
                "average_entry_price": self.average_entry_price,
                "current_price": price,
                "realized_pnl": self.realized_pnl,
                "unrealized_pnl": unrealized_pnl,
                "total_equity": equity,
                "total_return_percent": ((equity - initial_balance) / initial_balance * 100) if initial_balance > 0 else 0.0,
                "max_drawdown": max_drawdown,
                "trade_count": self.trade_count,
                "win_count": self.win_count,
                "loss_count": self.loss_count,
                "win_rate": win_rate,
                "profit_factor": profit_factor,
                "gross_profit": self.gross_profit,
                "gross_loss": self.gross_loss,
                "last_signal": last_signal,
                "last_risk_result": risk_result,
                "last_processed_candle_time_utc": timestamp_utc,
                "last_tick_time_utc": tick_time_utc,
                "updated_at": tick_time_utc,
            },
        )


async def process_forward_session(session: dict) -> int:
    tick_time_utc = _utc_now()
    market = session["market"]
    unit = int(session["unit"])
    fresh = await fetch_minute_candles(market=market, unit=unit, count=200)
    insert_candles(fresh)
    candles = load_candles(market, unit, 300)
    latest_completed = latest_completed_candle(candles, unit)
    latest_completed_time = latest_completed["candle_time_utc"] if latest_completed else None
    last_processed = session.get("last_processed_candle_time_utc")
    new_candles = _completed_candles_after(candles, unit, last_processed)

    if not new_candles:
        insert_forward_tick_log(
            session_id=int(session["id"]),
            tick_time_utc=tick_time_utc,
            market=market,
            unit=unit,
            latest_candle_time_utc=latest_completed_time,
            last_processed_candle_time_utc=last_processed,
            result="NO_NEW_CANDLE",
            message="완성된 새 캔들이 없습니다.",
        )
        update_forward_session_state(
            int(session["id"]),
            {
                "status": "RUNNING",
                "current_balance_krw": session["balance"]["cash_krw"],
                "current_position_volume": session["position"]["btc_quantity"],
                "average_entry_price": session["position"]["avg_buy_price"],
                "current_price": session["balance"]["current_price"],
                "realized_pnl": session["balance"]["realized_pnl"],
                "unrealized_pnl": session["balance"]["unrealized_pnl"],
                "total_equity": session["balance"]["equity"],
                "total_return_percent": session["balance"].get("total_return_percent", session["balance"].get("total_return", 0.0) * 100),
                "max_drawdown": session["metrics"].get("mdd", 0.0),
                "trade_count": session["metrics"].get("trade_count", 0),
                "win_count": sum(1 for order in session.get("orders", []) if order["side"] == "SELL" and (order.get("realized_pnl") or 0) > 0),
                "loss_count": sum(1 for order in session.get("orders", []) if order["side"] == "SELL" and (order.get("realized_pnl") or 0) <= 0),
                "win_rate": session["metrics"].get("win_rate", 0.0),
                "profit_factor": session["metrics"].get("profit_factor", 0.0),
                "gross_profit": sum(max(float(order.get("realized_pnl") or 0), 0.0) for order in session.get("orders", []) if order["side"] == "SELL"),
                "gross_loss": abs(sum(min(float(order.get("realized_pnl") or 0), 0.0) for order in session.get("orders", []) if order["side"] == "SELL")),
                "last_signal": session.get("last_signal", "HOLD"),
                "last_risk_result": "NO_NEW_CANDLE",
                "last_processed_candle_time_utc": last_processed,
                "last_tick_time_utc": tick_time_utc,
                "updated_at": tick_time_utc,
            },
        )
        logger.info("[paper-forward] session=%s NO_NEW_CANDLE last_processed=%s", session["id"], last_processed)
        return 0

    broker = ForwardPaperBroker(session)
    last_result = "PROCESSED"
    for candle in new_candles:
        signal_info = _latest_signal(
            session["strategy"],
            session.get("settings", {}),
            candles,
            candle["candle_time_utc"],
        )
        risk_result = broker.apply_candle(
            candle,
            signal_info["signal"],
            signal_info["reason"],
            tick_time_utc,
        )
        if signal_info["signal"] in {"BUY", "SELL"} and risk_result == "PASS":
            last_result = "ORDER_CREATED"
        elif risk_result.startswith("BLOCKED_BY_"):
            last_result = "BLOCKED_BY_RISK"

    insert_forward_tick_log(
        session_id=int(session["id"]),
        tick_time_utc=tick_time_utc,
        market=market,
        unit=unit,
        latest_candle_time_utc=new_candles[-1]["candle_time_utc"],
        last_processed_candle_time_utc=new_candles[-1]["candle_time_utc"],
        result=last_result,
        message=f"{len(new_candles)}개 완성 캔들을 처리했습니다.",
    )
    return len(new_candles)


async def process_running_forward_sessions() -> dict:
    if not _forward_tick_lock.acquire(blocking=False):
        logger.info("[paper-forward] tick skipped because previous tick is still running")
        return {"processed_sessions": 0, "processed_candles": 0, "skipped": True}
    try:
        sessions = load_running_forward_sessions()
        logger.info("[paper-forward] tick started running_sessions=%s", len(sessions))
        processed_sessions = 0
        processed_candles = 0
        for session in sessions:
            try:
                processed_candles += await process_forward_session(session)
                processed_sessions += 1
            except Exception as exc:  # pragma: no cover - scheduler boundary
                logger.exception("[paper-forward] session=%s ERROR %s", session.get("id"), exc)
                mark_forward_session_error(int(session["id"]), str(exc))
                insert_forward_tick_log(
                    session_id=int(session["id"]),
                    tick_time_utc=_utc_now(),
                    market=session.get("market", ""),
                    unit=int(session.get("unit", 0)),
                    latest_candle_time_utc=None,
                    last_processed_candle_time_utc=session.get("last_processed_candle_time_utc"),
                    result="ERROR",
                    message=str(exc),
                )
        logger.info("[paper-forward] tick finished processed_sessions=%s processed_candles=%s", processed_sessions, processed_candles)
        return {"processed_sessions": processed_sessions, "processed_candles": processed_candles, "skipped": False}
    finally:
        _forward_tick_lock.release()


def run_forward_scheduler_tick() -> None:
    asyncio.run(process_running_forward_sessions())
