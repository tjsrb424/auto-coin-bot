from __future__ import annotations

import itertools
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from app.backtest import run_backtest

PERIOD_DAYS = {
    "7d": 7,
    "30d": 30,
    "90d": 90,
    "180d": 180,
}


def parameter_grid(strategy: str, base_settings: dict | None = None) -> list[dict]:
    base = dict(base_settings or {})
    if strategy == "ma_cross":
        values = [
            {"short_window": short, "long_window": long}
            for short, long in itertools.product([5, 10, 20], [20, 30, 60])
            if short < long
        ]
    elif strategy == "rsi":
        values = [
            {"rsi_period": 14, "buy_threshold": buy, "sell_threshold": sell}
            for buy, sell in itertools.product([25, 30, 35], [65, 70, 75])
        ]
    elif strategy == "volatility_breakout":
        values = [{"k": k, "exit_rule": int(base.get("exit_rule", base.get("exit_window", 10)))} for k in [0.3, 0.5, 0.7]]
    else:
        values = [base]
    return [{**base, **value} for value in values]


def build_periods(periods: list[str], custom_start: str | None, custom_end: str | None) -> list[dict]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    built = []
    for period in periods:
        if period == "custom":
            if custom_start and custom_end:
                built.append(
                    {
                        "label": "custom",
                        "start_time_utc": custom_start,
                        "end_time_utc": custom_end,
                        "days": None,
                    }
                )
            continue
        days = PERIOD_DAYS.get(period)
        if days is None:
            continue
        start = now - timedelta(days=days)
        built.append(
            {
                "label": period,
                "start_time_utc": start.isoformat().replace("+00:00", "Z"),
                "end_time_utc": now.isoformat().replace("+00:00", "Z"),
                "days": days,
            }
        )
    return built


def _warning_labels(metrics: dict, no_fee_metrics: dict, period: dict) -> list[str]:
    warnings = []
    if int(metrics.get("trade_count", 0)) < 3:
        warnings.append("거래 횟수 부족")
    if int(metrics.get("trade_count", 0)) > 120:
        warnings.append("거래 횟수 과다")
    if float(metrics.get("mdd", 0.0)) >= 0.2:
        warnings.append("MDD 큼")
    if period.get("days") and period["days"] >= 90 and float(metrics.get("total_return", 0.0)) < 0:
        warnings.append("90일 이상 기간 손실")
    if float(no_fee_metrics.get("total_return", 0.0)) > 0 and float(metrics.get("total_return", 0.0)) <= 0:
        warnings.append("수수료 포함 시 손실")
    return warnings


def _base_stability_score(metrics: dict) -> float:
    trade_count = int(metrics.get("trade_count", 0))
    score = 50.0
    score += float(metrics.get("total_return", 0.0)) * 260
    score -= float(metrics.get("mdd", 0.0)) * 120
    score += min(float(metrics.get("profit_factor", 0.0)), 4.0) * 7
    score += float(metrics.get("win_rate", 0.0)) * 12
    if trade_count < 3:
        score -= 18
    if trade_count > 120:
        score -= min((trade_count - 120) * 0.12, 18)
    return score


def _clamp_score(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 2)


def _apply_cross_row_warnings(rows: list[dict]) -> None:
    by_combo: dict[tuple, list[dict]] = defaultdict(list)
    by_period: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        parameter_key = tuple(sorted(row["parameters"].items()))
        by_combo[(row["strategy"], row["unit"], parameter_key)].append(row)
        by_period[(row["strategy"], row["unit"], row["period_label"])].append(row)

    for grouped in by_combo.values():
        returns = [float(row["metrics"]["total_return"]) for row in grouped]
        positive_count = sum(1 for value in returns if value > 0)
        if max(returns, default=0.0) > 0.03 and positive_count <= 1 and len(grouped) > 1:
            for row in grouped:
                row["warnings"].append("특정 기간 의존")
                row["stability_score"] -= 10
        if len(returns) > 1 and statistics.pstdev(returns) > 0.08:
            for row in grouped:
                row["warnings"].append("기간별 성과 변동 큼")
                row["stability_score"] -= 8

    for grouped in by_period.values():
        returns = [float(row["metrics"]["total_return"]) for row in grouped]
        if len(returns) > 2 and max(returns) - min(returns) > 0.08:
            for row in grouped:
                row["warnings"].append("파라미터 민감")
                row["stability_score"] -= 8

    for row in rows:
        row["warnings"] = list(dict.fromkeys(row["warnings"]))
        row["stability_score"] = _clamp_score(row["stability_score"])


async def run_strategy_validation(
    *,
    market: str,
    strategy: str,
    timeframes: list[int],
    periods: list[str],
    custom_start_time_utc: str | None,
    custom_end_time_utc: str | None,
    base_settings: dict,
    risk: dict,
    load_period_candles: Callable[[str, int, str, str], Awaitable[list[dict]]],
) -> dict:
    period_specs = build_periods(periods, custom_start_time_utc, custom_end_time_utc)
    parameter_sets = parameter_grid(strategy, base_settings)
    rows: list[dict] = []

    for unit in timeframes:
        for period in period_specs:
            try:
                candles = await load_period_candles(
                    market,
                    unit,
                    period["start_time_utc"],
                    period["end_time_utc"],
                )
            except ValueError:
                continue
            for parameters in parameter_sets:
                result = run_backtest(candles, strategy, parameters, risk, market=market)
                no_fee_risk = {**risk, "fee_rate": 0.0, "slippage_rate": 0.0}
                no_fee_result = run_backtest(candles, strategy, parameters, no_fee_risk, market=market)
                metrics = result["metrics"]
                warnings = _warning_labels(metrics, no_fee_result["metrics"], period)
                rows.append(
                    {
                        "market": market,
                        "unit": unit,
                        "timeframe": f"{unit}m" if unit < 60 else "1h",
                        "strategy": strategy,
                        "parameters": parameters,
                        "period_label": period["label"],
                        "start_time_utc": period["start_time_utc"],
                        "end_time_utc": period["end_time_utc"],
                        "metrics": metrics,
                        "stability_score": _base_stability_score(metrics),
                        "warnings": warnings,
                    }
                )

    _apply_cross_row_warnings(rows)
    ranking = sorted(rows, key=lambda row: row["stability_score"], reverse=True)
    return {
        "strategy": strategy,
        "rows": ranking,
        "periods": period_specs,
        "parameter_count": len(parameter_sets),
    }
