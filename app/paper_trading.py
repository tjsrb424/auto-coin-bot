from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from app.backtest import candles_to_frame
from app.strategies import apply_strategy

KST = timezone(timedelta(hours=9))


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


def run_paper_trading(
    candles: list[dict],
    market: str,
    unit: int,
    strategy: str,
    settings: dict,
    risk: dict,
) -> dict:
    if len(candles) < 30:
        raise ValueError("페이퍼 트레이딩에는 최소 30개 이상의 캔들이 필요합니다.")

    initial_cash = _float_setting(risk, "initial_cash", 1_000_000)
    max_order_amount = _float_setting(risk, "max_order_amount", 100_000)
    daily_max_loss_rate = _float_setting(risk, "daily_max_loss_rate", 0.03)
    max_position_ratio = _float_setting(risk, "max_position_ratio", 0.5)
    consecutive_loss_limit = int(_float_setting(risk, "consecutive_loss_limit", 3))
    volatility_block_rate = _float_setting(risk, "volatility_block_rate", 0.03)
    fee_rate = _float_setting(risk, "fee_rate", 0.0005)
    slippage_rate = _float_setting(risk, "slippage_rate", 0.0005)

    df = candles_to_frame(candles)
    df = apply_strategy(strategy, df, settings)

    cash = initial_cash
    btc_quantity = 0.0
    avg_buy_price = 0.0
    realized_pnl = 0.0
    consecutive_losses = 0
    day_start_equity: dict[str, float] = {}
    orders: list[dict] = []
    blocked_signals: list[dict] = []
    signals: list[dict] = []
    equity_curve: list[dict] = []

    for row in df.itertuples(index=False):
        price = float(row.close)
        high = float(row.high)
        low = float(row.low)
        signal = str(row.signal)
        reason = str(row.reason)
        date_key = _kst_date_key(str(row.timestampUtc))
        equity_before = cash + btc_quantity * price
        day_start_equity.setdefault(date_key, equity_before)
        day_loss_rate = (
            (equity_before - day_start_equity[date_key]) / day_start_equity[date_key]
            if day_start_equity[date_key] > 0
            else 0.0
        )
        range_rate = (high - low) / price if price > 0 else 0.0

        if signal in {"BUY", "SELL"}:
            signals.append(
                {
                    "time": row.time,
                    "signal": signal,
                    "price": price,
                    "reason": reason,
                }
            )

        if signal == "BUY":
            block_reason = ""
            position_value = btc_quantity * price
            max_position_value = equity_before * max(max_position_ratio, 0)
            available_room = max_position_value - position_value
            order_amount = min(max_order_amount, cash, available_room)
            if day_loss_rate <= -abs(daily_max_loss_rate):
                block_reason = "daily_loss_limit"
            elif consecutive_losses >= max(consecutive_loss_limit, 0):
                block_reason = "consecutive_loss_limit"
            elif range_rate >= volatility_block_rate:
                block_reason = "volatility_block"
            elif order_amount <= 0:
                block_reason = "position_or_cash_limit"

            if block_reason:
                blocked_signals.append(
                    {
                        "time": row.time,
                        "signal": signal,
                        "price": price,
                        "reason": block_reason,
                    }
                )
            else:
                execution_price = price * (1 + max(slippage_rate, 0))
                fee = order_amount * fee_rate
                spend = max(order_amount - fee, 0)
                bought_quantity = spend / execution_price if execution_price > 0 else 0.0
                new_quantity = btc_quantity + bought_quantity
                if new_quantity > 0:
                    avg_buy_price = (
                        (btc_quantity * avg_buy_price) + (bought_quantity * execution_price)
                    ) / new_quantity
                btc_quantity = new_quantity
                cash -= order_amount
                orders.append(
                    {
                        "time": row.time,
                        "market": market,
                        "side": "BUY",
                        "strategy": strategy,
                        "signal_price": price,
                        "execution_price": execution_price,
                        "quantity": bought_quantity,
                        "fee": fee,
                        "realized_pnl": None,
                        "reason": reason,
                    }
                )
        elif signal == "SELL" and btc_quantity > 0:
            execution_price = price * (1 - max(slippage_rate, 0))
            gross = btc_quantity * execution_price
            fee = gross * fee_rate
            net = gross - fee
            pnl = net - btc_quantity * avg_buy_price
            cash += net
            realized_pnl += pnl
            consecutive_losses = consecutive_losses + 1 if pnl < 0 else 0
            orders.append(
                {
                    "time": row.time,
                    "market": market,
                    "side": "SELL",
                    "strategy": strategy,
                    "signal_price": price,
                    "execution_price": execution_price,
                    "quantity": btc_quantity,
                    "fee": fee,
                    "realized_pnl": pnl,
                    "reason": reason,
                }
            )
            btc_quantity = 0.0
            avg_buy_price = 0.0

        unrealized_pnl = (price - avg_buy_price) * btc_quantity if btc_quantity > 0 else 0.0
        equity = cash + btc_quantity * price
        equity_curve.append(
            {
                "time": row.time,
                "equity": equity,
                "cash_krw": cash,
                "btc_quantity": btc_quantity,
                "price": price,
                "unrealized_pnl": unrealized_pnl,
            }
        )

    last_price = float(df.iloc[-1]["close"])
    unrealized_pnl = (last_price - avg_buy_price) * btc_quantity if btc_quantity > 0 else 0.0
    equity = cash + btc_quantity * last_price
    equity_values = np.array([point["equity"] for point in equity_curve])
    peak = np.maximum.accumulate(equity_values) if len(equity_values) else np.array([])
    mdd = float(abs(np.min((equity_values - peak) / peak))) if len(peak) else 0.0

    return {
        "status": "STOPPED",
        "mode": "SIMULATION",
        "market": market,
        "unit": unit,
        "strategy": strategy,
        "settings": settings,
        "risk": {
            "initial_cash": initial_cash,
            "max_order_amount": max_order_amount,
            "daily_max_loss_rate": daily_max_loss_rate,
            "max_position_ratio": max_position_ratio,
            "consecutive_loss_limit": consecutive_loss_limit,
            "volatility_block_rate": volatility_block_rate,
            "fee_rate": fee_rate,
            "slippage_rate": slippage_rate,
        },
        "started_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "stopped_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "last_processed_candle_time_utc": str(df.iloc[-1]["timestampUtc"]),
        "last_signal": signals[-1]["signal"] if signals else "HOLD",
        "balance": {
            "initial_cash": initial_cash,
            "cash_krw": cash,
            "current_price": last_price,
            "equity": equity,
            "realized_pnl": realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "total_pnl": equity - initial_cash,
            "total_return": (equity - initial_cash) / initial_cash if initial_cash > 0 else 0.0,
            "mdd": mdd,
        },
        "position": {
            "btc_quantity": btc_quantity,
            "avg_buy_price": avg_buy_price,
            "market_value": btc_quantity * last_price,
            "position_ratio": (btc_quantity * last_price) / equity if equity > 0 else 0.0,
        },
        "signals": signals,
        "blocked_signals": blocked_signals,
        "orders": orders,
        "equity_curve": equity_curve,
        "candles": df.replace({np.nan: None}).to_dict(orient="records"),
    }
