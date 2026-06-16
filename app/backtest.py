from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from app.strategies import apply_strategy


def candles_to_frame(candles: list[dict]) -> pd.DataFrame:
    rows = [
        {
            "timestampUtc": c["candle_time_utc"],
            "time": c["candle_time_utc"],
            "open": float(c["opening_price"]),
            "high": float(c["high_price"]),
            "low": float(c["low_price"]),
            "close": float(c["trade_price"]),
            "volume": float(c["candle_acc_trade_volume"]),
        }
        for c in candles
    ]
    return pd.DataFrame(rows).sort_values("timestampUtc").reset_index(drop=True)


def _parse_utc(value: str) -> datetime:
    normalized = value.replace(" ", "T")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    if "+" not in normalized[-6:] and "-" not in normalized[-6:]:
        normalized = f"{normalized}+00:00"
    return datetime.fromisoformat(normalized).astimezone(timezone.utc)


def _holding_minutes(start: str, end: str) -> float:
    return max((_parse_utc(end) - _parse_utc(start)).total_seconds() / 60, 0.0)


def _score(metrics: dict) -> float:
    trade_count = int(metrics.get("trade_count", 0))
    trade_penalty = 0.0
    if trade_count == 0:
        trade_penalty = 25.0
    elif trade_count < 3:
        trade_penalty = 8.0
    return round(
        metrics.get("total_return", 0.0) * 100
        - metrics.get("mdd", 0.0) * 60
        + metrics.get("win_rate", 0.0) * 20
        + min(metrics.get("profit_factor", 0.0), 5.0) * 4
        - trade_penalty,
        4,
    )


def run_backtest(
    candles: list[dict],
    strategy: str,
    settings: dict,
    risk: dict,
    *,
    market: str = "KRW-BTC",
) -> dict:
    if len(candles) < 30:
        raise ValueError("백테스트에는 최소 30개 이상의 캔들이 필요합니다.")

    initial_cash = float(risk.get("initial_cash", risk.get("initial_balance_krw", 10_000_000)))
    position_size = min(max(float(risk.get("position_size", 1.0)), 0.0), 1.0)
    fee_rate = max(float(risk.get("fee_rate", 0.0005)), 0.0)
    slippage_rate = max(float(risk.get("slippage_rate", 0.0)), 0.0)

    df = apply_strategy(strategy, candles_to_frame(candles), settings)

    cash = initial_cash
    quantity = 0.0
    entry_price = 0.0
    entry_time: str | None = None
    entry_cost = 0.0
    realized_pnl_total = 0.0
    signals: list[dict] = []
    orders: list[dict] = []
    closed_returns: list[float] = []
    closed_pnls: list[float] = []
    holding_minutes: list[float] = []
    equity_curve: list[dict] = []
    peak = initial_cash

    rows = list(df.itertuples(index=False))
    for index, row in enumerate(rows):
        mark_price = float(row.close)
        signal = str(row.signal)
        reason = str(row.reason)

        if signal in {"BUY", "SELL"}:
            signals.append(
                {
                    "time": row.time,
                    "signal": signal,
                    "price": mark_price,
                    "reason": reason,
                }
            )

        # Signals are calculated from the completed candle. Execution happens at the next candle open.
        next_row = rows[index + 1] if index + 1 < len(rows) else None
        if next_row is not None and signal == "BUY" and quantity == 0 and cash > 0:
            execution_price = float(next_row.open) * (1 + slippage_rate)
            budget = cash * position_size
            fee = budget * fee_rate
            spend = max(budget - fee, 0.0)
            buy_quantity = spend / execution_price if execution_price > 0 else 0.0
            if budget <= cash and buy_quantity > 0:
                cash = max(cash - budget, 0.0)
                quantity = buy_quantity
                entry_price = execution_price
                entry_time = str(next_row.time)
                entry_cost = budget
                orders.append(
                    {
                        "time": next_row.time,
                        "market": market,
                        "strategy": strategy,
                        "side": "BUY",
                        "price": execution_price,
                        "quantity": quantity,
                        "volume": quantity,
                        "amount_krw": budget,
                        "fee": fee,
                        "pnl": None,
                        "realized_pnl": None,
                        "reason": reason,
                        "signal_time": row.time,
                    }
                )
        elif next_row is not None and signal == "SELL" and quantity > 0:
            execution_price = float(next_row.open) * (1 - slippage_rate)
            sell_quantity = quantity
            gross = sell_quantity * execution_price
            fee = gross * fee_rate
            net = max(gross - fee, 0.0)
            pnl = net - entry_cost
            cash += net
            realized_pnl_total += pnl
            closed_pnls.append(pnl)
            if entry_cost > 0:
                closed_returns.append(pnl / entry_cost)
            if entry_time is not None:
                holding_minutes.append(_holding_minutes(entry_time, str(next_row.time)))
            orders.append(
                {
                    "time": next_row.time,
                    "market": market,
                    "strategy": strategy,
                    "side": "SELL",
                    "price": execution_price,
                    "quantity": sell_quantity,
                    "volume": sell_quantity,
                    "amount_krw": gross,
                    "fee": fee,
                    "pnl": pnl,
                    "realized_pnl": pnl,
                    "reason": reason,
                    "signal_time": row.time,
                }
            )
            quantity = 0.0
            entry_price = 0.0
            entry_time = None
            entry_cost = 0.0

        equity = cash + quantity * mark_price
        peak = max(peak, equity)
        drawdown = (equity - peak) / peak if peak else 0.0
        equity_curve.append(
            {
                "time": row.time,
                "equity": equity,
                "cash_krw": cash,
                "btc_quantity": quantity,
                "price": mark_price,
                "drawdown": drawdown,
            }
        )

    final_equity = equity_curve[-1]["equity"]
    drawdowns = [point["drawdown"] for point in equity_curve]
    wins = [pnl for pnl in closed_pnls if pnl > 0]
    losses = [pnl for pnl in closed_pnls if pnl <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    avg_profit = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (math.inf if gross_profit > 0 else 0.0)
    profit_loss_ratio = abs(avg_profit / avg_loss) if avg_loss else (math.inf if avg_profit > 0 else 0.0)

    metrics = {
        "total_return": (final_equity - initial_cash) / initial_cash if initial_cash > 0 else 0.0,
        "mdd": float(abs(min(drawdowns))) if drawdowns else 0.0,
        "win_rate": len(wins) / len(closed_pnls) if closed_pnls else 0.0,
        "trade_count": len(orders),
        "average_profit": avg_profit,
        "average_loss": avg_loss,
        "profit_factor": profit_factor,
        "profit_loss_ratio": profit_loss_ratio,
        "average_holding_time_minutes": float(np.mean(holding_minutes)) if holding_minutes else 0.0,
        "last_signal": signals[-1]["signal"] if signals else "HOLD",
        "final_equity": final_equity,
        "realized_pnl": realized_pnl_total,
        "score": 0.0,
    }
    metrics["score"] = _score(metrics)

    return {
        "strategy": strategy,
        "settings": settings,
        "risk": {
            "initial_cash": initial_cash,
            "position_size": position_size,
            "fee_rate": fee_rate,
            "slippage_rate": slippage_rate,
        },
        "metrics": metrics,
        "signals": signals,
        "orders": orders,
        "equity_curve": equity_curve,
        "candles": df.replace({np.nan: None}).to_dict(orient="records"),
    }


def compare_strategies(
    candles: list[dict],
    strategies: list[str],
    settings_by_strategy: dict[str, dict],
    risk: dict,
    *,
    market: str = "KRW-BTC",
) -> dict:
    results = [
        run_backtest(
            candles,
            strategy,
            settings_by_strategy.get(strategy, {}),
            risk,
            market=market,
        )
        for strategy in strategies
    ]
    comparison = [
        {
            "strategy": result["strategy"],
            "total_return": result["metrics"]["total_return"],
            "mdd": result["metrics"]["mdd"],
            "win_rate": result["metrics"]["win_rate"],
            "trade_count": result["metrics"]["trade_count"],
            "profit_factor": result["metrics"]["profit_factor"],
            "final_equity": result["metrics"]["final_equity"],
            "score": result["metrics"]["score"],
        }
        for result in results
    ]
    comparison.sort(key=lambda item: item["score"], reverse=True)
    return {"results": results, "comparison": comparison}
