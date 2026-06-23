from __future__ import annotations

import pandas as pd

from app.profit_strategies import panic_blocker, range_reversion, trend_pullback, volume_breakout


def moving_average_cross(
    df: pd.DataFrame,
    short_window: int = 5,
    long_window: int = 20,
) -> pd.DataFrame:
    out = df.copy()
    out["short_ma"] = out["close"].rolling(short_window).mean()
    out["long_ma"] = out["close"].rolling(long_window).mean()
    prev_short = out["short_ma"].shift(1)
    prev_long = out["long_ma"].shift(1)
    out["signal"] = "HOLD"
    out["reason"] = ""
    buy = (prev_short <= prev_long) & (out["short_ma"] > out["long_ma"])
    sell = (prev_short >= prev_long) & (out["short_ma"] < out["long_ma"])
    out.loc[buy, ["signal", "reason"]] = ["BUY", "short_ma_cross_up"]
    out.loc[sell, ["signal", "reason"]] = ["SELL", "short_ma_cross_down"]
    return out


def rsi_strategy(
    df: pd.DataFrame,
    period: int = 14,
    oversold: float = 30,
    overbought: float = 70,
) -> pd.DataFrame:
    out = df.copy()
    delta = out["close"].diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    out["rsi"] = 100 - (100 / (1 + rs))
    prev_rsi = out["rsi"].shift(1)
    out["signal"] = "HOLD"
    out["reason"] = ""
    buy = (prev_rsi >= oversold) & (out["rsi"] < oversold)
    sell = (prev_rsi <= overbought) & (out["rsi"] > overbought)
    out.loc[buy, ["signal", "reason"]] = ["BUY", "rsi_oversold_cross"]
    out.loc[sell, ["signal", "reason"]] = ["SELL", "rsi_overbought_cross"]
    return out


def volatility_breakout(
    df: pd.DataFrame,
    k: float = 0.5,
    exit_window: int = 10,
) -> pd.DataFrame:
    out = df.copy()
    prev_range = (out["high"].shift(1) - out["low"].shift(1)).clip(lower=0)
    out["target"] = out["open"] + prev_range * k
    out["exit_ma"] = out["close"].rolling(exit_window).mean()
    out["signal"] = "HOLD"
    out["reason"] = ""
    buy = out["high"] >= out["target"]
    sell = out["close"] < out["exit_ma"]
    out.loc[buy, ["signal", "reason"]] = ["BUY", "breakout_target_hit"]
    out.loc[sell, ["signal", "reason"]] = ["SELL", "close_below_exit_ma"]
    return out


def apply_strategy(strategy: str, df: pd.DataFrame, settings: dict) -> pd.DataFrame:
    if strategy == "ma_cross":
        return moving_average_cross(
            df,
            int(settings.get("short_window", 5)),
            int(settings.get("long_window", 20)),
        )
    if strategy == "rsi":
        return rsi_strategy(
            df,
            int(settings.get("rsi_period", settings.get("period", 14))),
            float(settings.get("buy_threshold", settings.get("oversold", 30))),
            float(settings.get("sell_threshold", settings.get("overbought", 70))),
        )
    if strategy == "volatility_breakout":
        return volatility_breakout(
            df,
            float(settings.get("k", 0.5)),
            int(settings.get("exit_rule", settings.get("exit_window", 10))),
        )
    if strategy == "trend_pullback":
        return trend_pullback(
            df,
            float(settings.get("rsi_low", 35)),
            float(settings.get("rsi_high", 45)),
        )
    if strategy == "volume_breakout":
        return volume_breakout(
            df,
            float(settings.get("volume_multiplier", 1.8)),
            float(settings.get("max_recent_return_pct", 5.0)),
        )
    if strategy == "range_reversion":
        return range_reversion(
            df,
            float(settings.get("rsi_threshold", 30)),
        )
    if strategy == "panic_blocker":
        return panic_blocker(df)
    raise ValueError("지원하지 않는 전략입니다.")
