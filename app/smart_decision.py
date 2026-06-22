from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from app.backtest import candles_to_frame
from app.database import (
    insert_decision_snapshot,
    insert_order_intent,
    load_global_bot_operation_policy,
    load_open_live_positions,
    update_live_position,
)
from app.risk_manager import compute_risk_state
from app.shadow_report import build_shadow_report
from app.smart_external_factors import load_external_factors
from app.smart_market_regime import classify_market_regime
from app.smart_promotion import evaluate_promotion, smart_engine_live_mode
from app.smart_signal_engine import evaluate_internal_signals
from app.smart_target_exposure import calculate_target_exposure
from app.smart_attack import apply_aggressive_target_layer, calculate_attack_score


DEFAULT_MARKET = "KRW-BTC"


def record_shadow_decision(*, session: dict, candidate: dict, candles: list[dict], candle: dict, legacy_signal: dict, available_krw_balance: float | None = None) -> dict:
    frame = candles_to_frame(candles)
    features = _build_features(frame)
    market_regime, regime_positives, regime_negatives = classify_market_regime(features)
    current_price = float(candle.get("trade_price") or legacy_signal.get("price") or features.get("last_price") or 0.0)
    policy = load_global_bot_operation_policy()
    max_total_exposure = max(_float(policy.get("max_total_exposure_krw"), 500_000.0), 1.0)
    daily_loss_limit_pct = _float(policy.get("daily_loss_limit_pct"), 3.0)
    daily_loss_limit_krw = max_total_exposure * daily_loss_limit_pct / 100
    position_metrics = _current_bot_position_metrics(session.get("exchange", "bithumb"), session.get("market", DEFAULT_MARKET), current_price, market_regime)
    position_qty = position_metrics["qty"]
    position_value = position_metrics["value"]
    current_position_pnl_pct = position_metrics["pnl_pct"]
    current_exposure = _pct(position_value, max_total_exposure)
    risk_state = compute_risk_state(str(session.get("exchange") or "bithumb"), str(session.get("market") or DEFAULT_MARKET))
    risk_score = _risk_score(features, risk_state)
    internal_signals = evaluate_internal_signals(legacy_signal, features, market_regime)
    external_factors = load_external_factors(str(session.get("market") or DEFAULT_MARKET), local_price_krw=current_price)
    target_result = calculate_target_exposure(
        market_regime=market_regime,
        current_exposure_pct=current_exposure,
        risk_score=risk_score,
        internal_signals=internal_signals,
        risk_state=risk_state,
        policy=policy,
        max_total_exposure_krw=max_total_exposure,
        current_position_value_krw=position_value,
        external_factors=external_factors,
    )
    external_factors["target_adjustment_pct"] = target_result.get("external_factor_adjustment_pct", 0.0)
    attack_result = calculate_attack_score(
        market_regime=market_regime,
        internal_signals=internal_signals,
        features=features,
        external_factors=external_factors,
        risk_score=risk_score,
        current_position_pnl_pct=current_position_pnl_pct,
        current_exposure_pct=current_exposure,
    )
    aggressive_result = apply_aggressive_target_layer(
        market_regime=market_regime,
        conservative_target_exposure_pct=target_result["target_exposure_pct"],
        attack_result=attack_result,
        current_exposure_pct=current_exposure,
        current_position_pnl_pct=current_position_pnl_pct,
        current_price=current_price,
        highest_price_since_entry=position_metrics.get("highest_price_since_entry"),
        risk_blockers=list(target_result["blockers"]),
    )
    target_exposure = aggressive_result["target_exposure_pct"]
    reasons = [*regime_positives, *target_result["positive_reasons"], *aggressive_result["positive_reasons"]]
    negatives = [*regime_negatives, *target_result["negative_reasons"], *aggressive_result["negative_reasons"]]
    blockers = list(aggressive_result["blockers"])
    confidence = _confidence_score(features, market_regime, legacy_signal)
    action_hint = aggressive_result["action_hint"]
    if _shadow_mode_enabled():
        blockers = [*blockers, "SMART_SHADOW_MODE"]
    one_line = _summary(action_hint, market_regime, legacy_signal, blockers)
    snapshot = {
        "decided_at": _utc_now(),
        "exchange": session.get("exchange", "bithumb"),
        "market": session.get("market", DEFAULT_MARKET),
        "timeframe": f"{candidate.get('unit', '')}m",
        "candle_time_utc": candle.get("candle_time_utc"),
        "candle_time_kst": candle.get("candle_time_kst"),
        "selected_strategy_id": session.get("candidate_strategy_id"),
        "selected_strategy_name": candidate.get("name") or candidate.get("strategy"),
        "legacy_signal": str(legacy_signal.get("signal") or "HOLD"),
        "market_regime": market_regime,
        "current_bot_position_qty": position_qty,
        "current_bot_position_value_krw": position_value,
        "current_exposure_pct": current_exposure,
        "target_exposure_pct": target_exposure,
        "action_hint": action_hint,
        "confidence_score": confidence,
        "risk_score": risk_score,
        "one_line_summary": one_line,
        "positive_reasons": reasons,
        "negative_reasons": negatives,
        "blockers": blockers,
        "raw_features": features,
        "external_factors": external_factors,
        "internal_signals": internal_signals,
        "max_total_exposure_krw": max_total_exposure,
        "daily_loss_limit_pct": daily_loss_limit_pct,
        "daily_loss_limit_krw": daily_loss_limit_krw,
        "available_krw_balance": available_krw_balance,
        "exposure_limit_blocked": "SMART_MAX_TOTAL_EXPOSURE_REACHED" in blockers,
        "attack_score": attack_result["attack_score"],
        "attack_mode": attack_result["attack_mode"],
        "attack_score_breakdown": attack_result["score_breakdown"],
        "aggressive_target_exposure_pct": aggressive_result["aggressive_target_exposure_pct"],
        "conservative_target_exposure_pct": aggressive_result["conservative_target_exposure_pct"],
        "final_target_exposure_source": aggressive_result["final_target_exposure_source"],
        "current_position_pnl_pct": current_position_pnl_pct,
        "highest_price_since_entry": aggressive_result.get("highest_price_since_entry"),
        "trailing_stop_price": aggressive_result.get("trailing_stop_price"),
        "partial_take_profit_triggered": aggressive_result["partial_take_profit_triggered"],
        "partial_take_profit_pct": aggressive_result["partial_take_profit_pct"],
        "pyramiding_allowed": aggressive_result["pyramiding_allowed"],
        "aggressive_blockers": aggressive_result.get("aggressive_buy_blockers", attack_result["blockers"]),
        "aggressive_buy_blockers": aggressive_result.get("aggressive_buy_blockers", attack_result["blockers"]),
        "aggressive_warnings": aggressive_result.get("aggressive_warnings", attack_result["blockers"]),
        "core_exposure_pct": aggressive_result.get("core_exposure_pct", 0.0),
        "core_exposure_applied": aggressive_result.get("core_exposure_applied", False),
        "core_exposure_broken_by_panic": aggressive_result.get("core_exposure_broken_by_panic", False),
    }
    snapshot_id = insert_decision_snapshot(snapshot)
    intent = _order_intent(
        snapshot_id=snapshot_id,
        snapshot=snapshot,
        max_total_exposure_krw=max_total_exposure,
        current_value=position_value,
        current_price=current_price,
        blockers=blockers,
    )
    if intent:
        recommendation = None
        try:
            recommendation = build_shadow_report(str(session.get("market") or DEFAULT_MARKET), limit=100).get("summary", {}).get("recommendation")
        except Exception:
            recommendation = None
        promotion = evaluate_promotion(
            intent=intent,
            snapshot=snapshot,
            policy=policy,
            shadow_recommendation=recommendation,
            risk_score=risk_score,
            daily_smart_order_count=0,
        )
        intent.update(promotion)
        intent_id = insert_order_intent(intent)
        intent["id"] = intent_id
    snapshot["id"] = snapshot_id
    snapshot["order_intents"] = [intent] if intent else []
    return snapshot


def _build_features(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {}
    close = frame["close"].astype(float)
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    volume = frame["volume"].astype(float)
    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    returns = close.pct_change()
    last = float(close.iloc[-1])
    ma20_now = _last(ma20)
    ma20_prev = _last(ma20.shift(5))
    volume_avg20 = _last(volume.rolling(20).mean())
    volume_ratio = (_last(volume) / volume_avg20) if volume_avg20 and volume_avg20 > 0 else None
    one_hour_window = min(12, len(close) - 1)
    return {
        "last_price": last,
        "rsi_14": _last(rsi),
        "ma_5": _last(ma5),
        "ma_20": ma20_now,
        "ma_60": _last(ma60),
        "ma_5_20_gap_pct": _pct((_last(ma5) or 0) - (ma20_now or 0), ma20_now or 0),
        "ma_20_slope": ((ma20_now - ma20_prev) / ma20_prev * 100) if ma20_now and ma20_prev else None,
        "volume_ratio_20": volume_ratio,
        "volatility_1h": float(returns.tail(12).std() * math.sqrt(12) * 100) if len(returns.dropna()) >= 12 else None,
        "volatility_24h": float(returns.tail(288).std() * math.sqrt(288) * 100) if len(returns.dropna()) >= 30 else None,
        "recent_return_5m": _pct(last - float(close.iloc[-2]), float(close.iloc[-2])) if len(close) >= 2 else None,
        "recent_return_1h": _pct(last - float(close.iloc[-1 - one_hour_window]), float(close.iloc[-1 - one_hour_window])) if one_hour_window > 0 else None,
        "recent_return_24h": _pct(last - float(close.iloc[0]), float(close.iloc[0])) if len(close) < 288 else _pct(last - float(close.iloc[-288]), float(close.iloc[-288])),
        "volume_latest": _last(volume),
        "high_low_range_pct": _pct(float(high.iloc[-1]) - float(low.iloc[-1]), last),
        "spread_pct": None,
        "orderbook_imbalance": None,
        "liquidity_score": None,
        "market_depth_krw": None,
        "slippage_estimate_pct": None,
    }


def _classify_market_regime(features: dict) -> str:
    price = features.get("last_price")
    ma20 = features.get("ma_20")
    slope = features.get("ma_20_slope")
    rsi = features.get("rsi_14")
    ret_1h = features.get("recent_return_1h")
    vol_1h = features.get("volatility_1h")
    volume_ratio = features.get("volume_ratio_20")
    if price is None or ma20 is None or slope is None:
        return "UNKNOWN"
    if ret_1h is not None and ret_1h <= -2 and vol_1h is not None and vol_1h >= 2:
        return "PANIC"
    if rsi is not None and rsi >= 72 and ret_1h is not None and ret_1h > 1:
        return "OVERHEATED"
    if price > ma20 and slope > 0.03:
        if volume_ratio is not None and volume_ratio >= 1.5:
            return "BREAKOUT"
        return "TREND_UP"
    if price < ma20 and slope < -0.03:
        return "TREND_DOWN"
    return "RANGE"


def _target_exposure(
    *,
    legacy_signal: str,
    market_regime: str,
    current_exposure_pct: float,
    risk_score: float,
    features: dict,
    risk_state: dict,
    policy: dict,
    max_total_exposure_krw: float,
    current_position_value_krw: float,
) -> tuple[float, list[str], list[str], list[str]]:
    positives: list[str] = []
    negatives: list[str] = []
    blockers: list[str] = []
    target = current_exposure_pct
    if not policy.get("auto_trading_enabled"):
        blockers.append("SMART_POLICY_AUTO_TRADING_DISABLED")
        negatives.append("운용정책에서 자동매매가 OFF 상태입니다.")
    if current_position_value_krw >= max_total_exposure_krw:
        blockers.append("SMART_MAX_TOTAL_EXPOSURE_REACHED")
        negatives.append("현재 봇 포지션 평가금액이 최대 투입 금액 이상입니다.")
    if risk_state.get("status") in {"EMERGENCY_STOPPED", "BLOCKED"}:
        blockers.append(str(risk_state.get("status")))
    if risk_score >= 80:
        blockers.append("SMART_RISK_SCORE_HIGH")
    if market_regime == "PANIC":
        target = 0.0
        negatives.append("PANIC 시장상태로 신규매수보다 노출 축소가 우선입니다.")
    elif legacy_signal == "SELL":
        target = 0.0
        negatives.append("기존 전략 SELL 신호가 발생했습니다.")
    elif legacy_signal == "BUY":
        positives.append("기존 전략 BUY 신호가 발생했습니다.")
        if market_regime in {"TREND_UP", "BREAKOUT"} and risk_score < 60:
            target = 50.0 if market_regime == "BREAKOUT" else 40.0
            positives.append(f"{market_regime} 상태가 BUY 신호를 지지합니다.")
        elif market_regime == "RANGE" and risk_score < 60:
            target = 30.0
            positives.append("횡보장에서 제한 비중 진입 후보입니다.")
        elif market_regime == "TREND_DOWN":
            target = 15.0
            negatives.append("하락 추세라 목표 비중을 낮게 제한합니다.")
        else:
            target = 20.0
    elif legacy_signal == "HOLD":
        positives.append("기존 전략은 관망 신호입니다.")
    if market_regime == "UNKNOWN":
        blockers.append("SMART_MARKET_REGIME_UNKNOWN")
        negatives.append("시장상태 판단에 필요한 데이터가 부족합니다.")
    daily_loss_limit_krw = max_total_exposure_krw * _float(policy.get("daily_loss_limit_pct"), 3.0) / 100
    daily_loss_krw = abs(min(_float(risk_state.get("daily_total_pnl"), 0.0), 0.0))
    if daily_loss_limit_krw > 0 and daily_loss_krw >= daily_loss_limit_krw and target > current_exposure_pct:
        blockers.append("SMART_DAILY_LOSS_LIMIT_REACHED")
        negatives.append("일 손실 제한에 도달해 신규매수와 추가매수를 차단합니다.")
    max_exposure = 100.0
    target = min(max(target, 0.0), max_exposure)
    return round(target, 4), positives, negatives, blockers


def _order_intent(*, snapshot_id: int, snapshot: dict, max_total_exposure_krw: float, current_value: float, current_price: float, blockers: list[str]) -> dict | None:
    target_value = max_total_exposure_krw * float(snapshot["target_exposure_pct"]) / 100
    delta = target_value - current_value
    min_delta_krw = _float(os.getenv("SMART_MIN_REBALANCE_DELTA_KRW"), 10_000.0)
    min_delta_pct = _float(os.getenv("SMART_MIN_REBALANCE_DELTA_PCT"), 5.0)
    delta_pct = abs(float(snapshot["target_exposure_pct"]) - float(snapshot["current_exposure_pct"]))
    action = str(snapshot["action_hint"])
    side = "BID" if delta > 0 else ("ASK" if delta < 0 else "NONE")
    aggressive_buy_blockers = set(snapshot.get("aggressive_buy_blockers") or snapshot.get("aggressive_blockers") or [])
    intent_blockers = [blocker for blocker in blockers if side == "BID" or blocker not in aggressive_buy_blockers]
    if abs(delta) < min_delta_krw or delta_pct < min_delta_pct:
        intent_blockers.append("SMART_MIN_REBALANCE_DELTA")
        action = "HOLD_POSITION" if current_value > 0 else "WAIT"
    if side == "BID" and current_value >= max_total_exposure_krw:
        intent_blockers.append("SMART_MAX_TOTAL_EXPOSURE_REACHED")
    if side == "BID" and current_value + abs(delta) > max_total_exposure_krw:
        capped_delta = max(0.0, max_total_exposure_krw - current_value)
        if capped_delta <= 0:
            intent_blockers.append("SMART_MAX_TOTAL_EXPOSURE_REACHED")
            delta = 0.0
            side = "NONE"
        else:
            intent_blockers.append("SMART_ORDER_DELTA_CAPPED_BY_MAX_TOTAL_EXPOSURE")
            delta = capped_delta
    if side == "NONE" and not intent_blockers:
        return None
    return {
        "decision_snapshot_id": snapshot_id,
        "exchange": snapshot["exchange"],
        "market": snapshot["market"],
        "side": side,
        "action_hint": action,
        "current_value_krw": current_value,
        "target_value_krw": target_value,
        "delta_value_krw": delta,
        "target_qty": abs(delta) / current_price if current_price > 0 and side != "NONE" else None,
        "order_type": "LIMIT",
        "limit_price": current_price if current_price > 0 else None,
        "urgency": "NORMAL",
        "status": "BLOCKED" if intent_blockers else "CREATED",
        "blockers": intent_blockers,
        "attack_score": snapshot.get("attack_score", 0.0),
        "attack_mode": snapshot.get("attack_mode", "OFF"),
        "target_source": snapshot.get("final_target_exposure_source", "CONSERVATIVE"),
        "pyramiding_allowed": bool(snapshot.get("pyramiding_allowed")),
        "no_averaging_down_blocked": "SMART_AGGRESSIVE_NO_AVERAGING_DOWN" in intent_blockers,
        "partial_take_profit_pct": snapshot.get("partial_take_profit_pct", 0.0),
        "trailing_stop_price": snapshot.get("trailing_stop_price"),
        "position_pnl_pct": snapshot.get("current_position_pnl_pct", 0.0),
        "policy_preview": {
            "attack_score": snapshot.get("attack_score", 0.0),
            "attack_mode": snapshot.get("attack_mode", "OFF"),
            "target_source": snapshot.get("final_target_exposure_source", "CONSERVATIVE"),
            "pyramiding_allowed": bool(snapshot.get("pyramiding_allowed")),
            "no_averaging_down_blocked": "SMART_AGGRESSIVE_NO_AVERAGING_DOWN" in intent_blockers,
            "partial_take_profit_pct": snapshot.get("partial_take_profit_pct", 0.0),
            "trailing_stop_price": snapshot.get("trailing_stop_price"),
            "position_pnl_pct": snapshot.get("current_position_pnl_pct", 0.0),
            "core_exposure_pct": snapshot.get("core_exposure_pct", 0.0),
            "core_exposure_applied": bool(snapshot.get("core_exposure_applied")),
            "core_exposure_broken_by_panic": bool(snapshot.get("core_exposure_broken_by_panic")),
            "aggressive_buy_blockers": list(snapshot.get("aggressive_buy_blockers") or []),
            "aggressive_warnings": list(snapshot.get("aggressive_warnings") or []),
        },
    }


def _action_hint(current: float, target: float) -> str:
    delta_pct = target - current
    min_delta_pct = _float(os.getenv("SMART_MIN_REBALANCE_DELTA_PCT"), 5.0)
    if abs(delta_pct) < min_delta_pct:
        return "HOLD_POSITION" if current > 0 else "WAIT"
    if target <= 0 and current > 0:
        return "EXIT"
    if delta_pct > 0:
        return "BUY_MORE"
    return "REDUCE"


def _confidence_score(features: dict, market_regime: str, legacy_signal: dict) -> float:
    score = 45.0
    if market_regime != "UNKNOWN":
        score += 15
    if legacy_signal.get("signal") in {"BUY", "SELL"}:
        score += 15
    if features.get("volume_ratio_20") is not None:
        score += min(float(features["volume_ratio_20"]) * 5, 15)
    return round(min(score, 100.0), 2)


def _internal_signals(legacy_signal: dict, features: dict, market_regime: str) -> dict:
    rsi = features.get("rsi_14")
    ma_gap = features.get("ma_5_20_gap_pct")
    volume_ratio = features.get("volume_ratio_20")
    ret_1h = features.get("recent_return_1h")
    signals = {
        "legacy_strategy": {
            "signal": str(legacy_signal.get("signal") or "HOLD"),
            "reason": legacy_signal.get("reason"),
            "score": 0,
        },
        "rsi": {
            "direction": "BULLISH" if rsi is not None and rsi < 35 else ("BEARISH" if rsi is not None and rsi > 70 else "NEUTRAL"),
            "score": _bounded_score(50 - _float(rsi, 50), scale=2),
            "value": rsi,
        },
        "moving_average": {
            "direction": "BULLISH" if ma_gap is not None and ma_gap > 0 else ("BEARISH" if ma_gap is not None and ma_gap < 0 else "NEUTRAL"),
            "score": _bounded_score(_float(ma_gap), scale=8),
            "value": ma_gap,
        },
        "volume": {
            "direction": "BULLISH" if volume_ratio is not None and volume_ratio >= 1.2 else "NEUTRAL",
            "score": _bounded_score((_float(volume_ratio, 1.0) - 1.0) * 25, scale=1),
            "value": volume_ratio,
        },
        "momentum_1h": {
            "direction": "BULLISH" if ret_1h is not None and ret_1h > 0 else ("BEARISH" if ret_1h is not None and ret_1h < 0 else "NEUTRAL"),
            "score": _bounded_score(_float(ret_1h), scale=15),
            "value": ret_1h,
        },
        "market_regime": {
            "direction": market_regime,
            "score": 0,
        },
    }
    if signals["legacy_strategy"]["signal"] == "BUY":
        signals["legacy_strategy"]["score"] = 25
    elif signals["legacy_strategy"]["signal"] == "SELL":
        signals["legacy_strategy"]["score"] = -25
    return signals


def _bounded_score(value: float, *, scale: float) -> float:
    return round(max(min(value * scale, 100.0), -100.0), 2)


def _risk_score(features: dict, risk_state: dict) -> float:
    score = 20.0
    if risk_state.get("status") == "WARNING":
        score += 20
    if risk_state.get("emergency_stop_enabled"):
        score += 60
    if risk_state.get("open_order_count", 0) > 0:
        score += 20
    if risk_state.get("daily_loss_percent", 0) > 1:
        score += min(float(risk_state.get("daily_loss_percent", 0)) * 2, 30)
    if features.get("volatility_1h") is not None:
        score += min(float(features["volatility_1h"]) * 4, 25)
    return round(min(score, 100.0), 2)


def _summary(action_hint: str, market_regime: str, legacy_signal: dict, blockers: list[str]) -> str:
    signal = str(legacy_signal.get("signal") or "HOLD")
    if "SMART_SHADOW_MODE" in blockers:
        return f"Shadow Mode에서 {market_regime} 시장과 {signal} 신호를 분석했으며 실제 주문은 차단됩니다."
    if action_hint == "BUY_MORE":
        return f"{market_regime} 시장에서 {signal} 신호가 발생해 목표 비중 확대 후보입니다."
    if action_hint in {"REDUCE", "EXIT"}:
        return f"{market_regime} 시장에서 포지션 축소 또는 청산 후보입니다."
    return f"{market_regime} 시장에서 {signal} 신호가 유지되어 현재는 관망 판단입니다."


def _current_bot_position_metrics(exchange: str, market: str, price: float, market_regime: str) -> dict:
    qty = 0.0
    value = 0.0
    cost = 0.0
    highest_prices: list[float] = []
    trailing_prices: list[float] = []
    trailing_pct = _trailing_stop_pct(market_regime)
    now = _utc_now()
    for position in load_open_live_positions(exchange, market):
        volume = _float(position.get("entry_volume"), 0.0)
        current_price = price if price > 0 else _float(position.get("current_price"), 0.0)
        entry_price = _float(position.get("entry_price"), 0.0)
        entry_amount = _float(position.get("entry_amount_krw"), entry_price * volume)
        highest = max(
            _float(position.get("highest_price_since_entry"), 0.0),
            entry_price,
            current_price,
            price,
        )
        trailing_stop = highest * (1 - trailing_pct / 100) if highest > 0 else 0.0
        unrealized = (current_price - entry_price) * volume if entry_price > 0 else 0.0
        qty += volume
        value += volume * current_price
        cost += entry_amount
        if highest:
            highest_prices.append(highest)
        if trailing_stop:
            trailing_prices.append(trailing_stop)
        if position.get("id"):
            update_live_position(
                int(position["id"]),
                {
                    "current_price": current_price,
                    "unrealized_pnl": unrealized,
                    "highest_price_since_entry": highest,
                    "trailing_stop_price": trailing_stop,
                    "trailing_stop_pct": trailing_pct,
                    "last_trailing_update_at": now,
                },
            )
    return {
        "qty": qty,
        "value": value,
        "pnl_pct": _pct(value - cost, cost) if cost > 0 else 0.0,
        "highest_price_since_entry": max(highest_prices) if highest_prices else None,
        "trailing_stop_price": max(trailing_prices) if trailing_prices else None,
    }


def _trailing_stop_pct(market_regime: str) -> float:
    default = {
        "BREAKOUT": 0.9,
        "TREND_UP": 0.7,
        "RANGE": 0.5,
        "OVERHEATED": 0.4,
    }.get(str(market_regime or "").upper(), 0.7)
    return _float(os.getenv("SMART_TRAILING_STOP_PCT"), default)


def _shadow_mode_enabled() -> bool:
    mode = smart_engine_live_mode()
    if mode != "shadow":
        return False
    return os.getenv("SMART_ENGINE_SHADOW_MODE", "true").lower() != "false"


def _last(series) -> float | None:
    if len(series) == 0:
        return None
    value = series.iloc[-1]
    if pd.isna(value):
        return None
    return float(value)


def _pct(numerator: float, denominator: float) -> float:
    return float(numerator) / float(denominator) * 100 if denominator else 0.0


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
