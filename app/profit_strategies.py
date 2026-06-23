from __future__ import annotations

import pandas as pd


def trend_pullback(df: pd.DataFrame, rsi_low: float = 35, rsi_high: float = 45) -> pd.DataFrame:
    out = _with_indicators(df)
    out["signal"] = "HOLD"
    out["reason"] = ""
    rsi_rebound = (out["rsi_14"].shift(1) < rsi_low) & out["rsi_14"].between(rsi_low, rsi_high)
    trend_ok = (out["ma20"] > out["ma60"]) & (out["ma20_slope"] > 0)
    pullback_ok = (out["close"] > out["ma60"]) & (out["close"] <= out["ma20"] * 1.01)
    volume_ok = out["volume"] >= out["volume_ma20"]
    buy = trend_ok & pullback_ok & rsi_rebound & volume_ok
    sell = (out["close"] < out["ma20"]) & (out["rsi_14"] > 65)
    out.loc[buy, ["signal", "reason"]] = ["BUY", "trend_pullback_rebound"]
    out.loc[sell, ["signal", "reason"]] = ["SELL", "trend_pullback_exit"]
    return out


def volume_breakout(df: pd.DataFrame, volume_multiplier: float = 1.8, max_recent_return_pct: float = 5.0) -> pd.DataFrame:
    out = _with_indicators(df)
    out["signal"] = "HOLD"
    out["reason"] = ""
    high20 = out["high"].shift(1).rolling(20).max()
    recent_return = (out["close"] / out["close"].shift(3) - 1) * 100
    upper_wick = (out["high"] - out[["open", "close"]].max(axis=1)) / out["close"].replace(0, pd.NA) * 100
    breakout = out["close"] > high20
    volume_ok = out["volume"] >= out["volume_ma20"] * volume_multiplier
    trend_ok = out["ma20_slope"] >= 0
    not_overheated = recent_return < max_recent_return_pct
    wick_ok = upper_wick < 1.5
    buy = breakout & volume_ok & trend_ok & not_overheated & wick_ok
    sell = out["close"] < out["ma20"]
    out.loc[buy, ["signal", "reason"]] = ["BUY", "volume_breakout_confirmed"]
    out.loc[sell, ["signal", "reason"]] = ["SELL", "volume_breakout_exit"]
    return out


def range_reversion(df: pd.DataFrame, rsi_threshold: float = 30) -> pd.DataFrame:
    out = _with_indicators(df)
    out["signal"] = "HOLD"
    out["reason"] = ""
    std20 = out["close"].rolling(20).std()
    lower_band = out["ma20"] - std20 * 2
    slope_flat = out["ma20_slope"].abs() < 0.03
    reentry = (out["close"].shift(1) < lower_band.shift(1)) & (out["close"] >= lower_band)
    rsi_ok = (out["rsi_14"].shift(1) <= rsi_threshold) & (out["rsi_14"] > out["rsi_14"].shift(1))
    volume_ok = out["volume"] >= out["volume_ma20"] * 0.5
    buy = slope_flat & reentry & rsi_ok & volume_ok
    sell = (out["close"] >= out["ma20"]) | (out["rsi_14"] >= 55)
    out.loc[buy, ["signal", "reason"]] = ["BUY", "range_reversion_reentry"]
    out.loc[sell, ["signal", "reason"]] = ["SELL", "range_reversion_exit"]
    return out


def panic_blocker(df: pd.DataFrame) -> pd.DataFrame:
    out = _with_indicators(df)
    out["signal"] = "HOLD"
    out["reason"] = ""
    recent_return = (out["close"] / out["close"].shift(12) - 1) * 100
    volatility = (out["high"].rolling(12).max() - out["low"].rolling(12).min()) / out["close"].replace(0, pd.NA) * 100
    panic = (recent_return <= -2) & (volatility >= 2)
    out.loc[panic, ["signal", "reason"]] = ["SELL", "panic_blocker_risk_off"]
    return out


def _with_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ma20"] = out["close"].rolling(20).mean()
    out["ma60"] = out["close"].rolling(60).mean()
    out["ma20_slope"] = out["ma20"].pct_change(3) * 100
    delta = out["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    out["rsi_14"] = 100 - (100 / (1 + (gain / loss.replace(0, pd.NA))))
    out["volume_ma20"] = out["volume"].rolling(20).mean()
    return out
