from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

from app.auto_strategy_selector import evaluate_auto_strategy_selector
from app.database import (
    create_forward_session_from_candidate,
    has_unresolved_live_order,
    has_unresolved_live_order_for_exchange,
    insert_strategy_kill_switch_event,
    load_active_strategy_selection,
    load_candidate_strategies,
    load_candidate_strategies_without_forward_session,
    load_global_bot_operation_policy,
    load_latest_forward_session_for_candidate,
    load_open_live_positions,
    load_open_live_positions_for_exchange,
    load_strategy_switch_logs_with_candidates,
    market_is_live_allowed,
    promote_candidate_strategy,
    record_strategy_switch,
)
from app.live_broker import is_emergency_stopped
from app.risk_manager import compute_risk_state
from app.strategy_kill_switch import evaluate_strategy_kill_switch
from app.upbit import fetch_minute_candles


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _config() -> dict:
    return {
        "enabled": os.getenv("AUTO_PROMOTION_PIPELINE_ENABLED", "true").lower() == "true",
        "exchange": os.getenv("AUTO_ALLOWED_EXCHANGE", "bithumb").strip().lower() or "bithumb",
        "max_enroll_per_tick": _int_env("AUTO_PROMOTION_MAX_ENROLL_PER_TICK", 5),
        "initial_balance_krw": _float_env("AUTO_PROMOTION_FORWARD_BALANCE_KRW", 1_000_000.0),
        "min_forward_trades": _int_env("AUTO_PROMOTION_MIN_FORWARD_TRADES", 30),
        "min_forward_runtime_hours": _float_env("AUTO_PROMOTION_MIN_FORWARD_RUNTIME_HOURS", 168.0),
        "min_forward_return_percent": _float_env("AUTO_PROMOTION_MIN_FORWARD_RETURN_PERCENT", 1.0),
        "max_forward_mdd": _float_env("AUTO_PROMOTION_MAX_FORWARD_MDD", 0.08),
        "min_forward_win_rate": _float_env("AUTO_PROMOTION_MIN_FORWARD_WIN_RATE", 0.42),
        "min_profit_factor": _float_env("AUTO_PROMOTION_MIN_PROFIT_FACTOR", 1.2),
        "min_expectancy_after_fee": _float_env("AUTO_PROMOTION_MIN_EXPECTANCY_AFTER_FEE", 0.0),
        "max_single_trade_profit_share": _float_env("AUTO_PROMOTION_MAX_SINGLE_TRADE_PROFIT_SHARE", 0.5),
        "selector_apply_enabled": os.getenv("AUTO_SELECTOR_APPLY_BEST_ENABLED", "true").lower() == "true",
    }


async def enroll_backtest_passed_candidates(*, limit: int | None = None) -> dict:
    config = _config()
    if not config["enabled"]:
        return {"enrolled": [], "skipped": [{"reason": "PIPELINE_DISABLED"}]}

    max_count = limit if limit is not None else int(config["max_enroll_per_tick"])
    candidates = load_candidate_strategies_without_forward_session(max_count, status="BACKTEST_PASSED")
    enrolled = []
    skipped = []
    for candidate in candidates:
        try:
            candles = await fetch_minute_candles(market=str(candidate["market"]), unit=int(candidate["unit"]), count=2)
            current_price = float(candles[0].get("trade_price") or 0.0) if candles else 0.0
            last_processed = candles[-1].get("candle_time_utc") if len(candles) > 1 else None
            if current_price <= 0:
                skipped.append({"candidate_id": candidate["id"], "reason": "NO_CURRENT_PRICE"})
                continue
            session_id = create_forward_session_from_candidate(
                candidate,
                initial_balance_krw=float(config["initial_balance_krw"]),
                risk={"source": "auto_promotion_pipeline", "created_at": _utc_now()},
                current_price=current_price,
                last_processed_candle_time_utc=last_processed,
            )
            promoted = promote_candidate_strategy(
                int(candidate["id"]),
                "SHADOW_RUNNING",
                reason="Auto-enrolled into Forward Paper shadow validation",
                metadata={"forward_session_id": session_id},
            )
            enrolled.append({"candidate": promoted or candidate, "forward_session_id": session_id})
        except Exception as exc:  # pragma: no cover - scheduler boundary
            skipped.append({"candidate_id": candidate.get("id"), "reason": exc.__class__.__name__, "message": str(exc)})
    return {"enrolled": enrolled, "skipped": skipped}


def _session_passes(session: dict, config: dict) -> tuple[bool, list[str]]:
    blockers: list[str] = []
    trade_count = int(session.get("metrics", {}).get("trade_count") or session.get("trade_count") or 0)
    total_return_percent = float(session.get("balance", {}).get("total_return_percent") or session.get("total_return_percent") or 0.0)
    mdd = float(session.get("metrics", {}).get("mdd") or session.get("max_drawdown") or 0.0)
    win_rate = float(session.get("metrics", {}).get("win_rate") or session.get("win_rate") or 0.0)
    profit_factor = float(session.get("metrics", {}).get("profit_factor") or session.get("profit_factor") or 0.0)
    expectancy = _expectancy_after_fee(session)
    single_trade_profit_share = _single_trade_profit_share(session)
    runtime_hours = _forward_runtime_hours(session)
    if trade_count < int(config["min_forward_trades"]):
        blockers.append("FORWARD_TRADE_COUNT_TOO_LOW")
    if runtime_hours < float(config["min_forward_runtime_hours"]):
        blockers.append("FORWARD_RUNTIME_TOO_SHORT")
    if total_return_percent < float(config["min_forward_return_percent"]):
        blockers.append("FORWARD_RETURN_TOO_LOW")
    if mdd > float(config["max_forward_mdd"]):
        blockers.append("FORWARD_MDD_TOO_HIGH")
    if win_rate < float(config["min_forward_win_rate"]):
        blockers.append("FORWARD_WIN_RATE_TOO_LOW")
    if profit_factor < float(config["min_profit_factor"]):
        blockers.append("FORWARD_PROFIT_FACTOR_TOO_LOW")
    if expectancy <= float(config["min_expectancy_after_fee"]):
        blockers.append("FORWARD_EXPECTANCY_TOO_LOW")
    if single_trade_profit_share > float(config["max_single_trade_profit_share"]):
        blockers.append("FORWARD_SINGLE_TRADE_PROFIT_SHARE_TOO_HIGH")
    return not blockers, blockers


def _forward_runtime_hours(session: dict) -> float:
    start = _parse_utc(session.get("started_at") or session.get("created_at"))
    end = _parse_utc(session.get("last_tick_time_utc") or session.get("updated_at") or session.get("stopped_at")) or datetime.now(timezone.utc)
    if start is None:
        return 0.0
    return max((end - start).total_seconds() / 3600, 0.0)


def _expectancy_after_fee(session: dict) -> float:
    pnl_values = [
        float(order.get("realized_pnl") or 0.0)
        for order in session.get("orders", [])
        if str(order.get("side") or "").upper() == "SELL" and order.get("realized_pnl") is not None
    ]
    if pnl_values:
        return sum(pnl_values) / len(pnl_values)
    return float(session.get("metrics", {}).get("average_trade_pnl") or 0.0)


def _single_trade_profit_share(session: dict) -> float:
    profits = [
        max(float(order.get("realized_pnl") or 0.0), 0.0)
        for order in session.get("orders", [])
        if str(order.get("side") or "").upper() == "SELL"
    ]
    total_profit = sum(profits)
    if total_profit <= 0:
        return 0.0
    return max(profits) / total_profit


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def promote_shadow_candidates() -> dict:
    config = _config()
    if not config["enabled"]:
        return {"promoted": [], "blocked": [{"reason": "PIPELINE_DISABLED"}]}

    promoted = []
    blocked = []
    for candidate in load_candidate_strategies(100, statuses=["SHADOW_RUNNING", "SHADOW_PASSED"]):
        session = load_latest_forward_session_for_candidate(int(candidate["id"]))
        if not session:
            blocked.append({"candidate_id": candidate["id"], "reason": "NO_FORWARD_SESSION"})
            continue
        kill_switch = evaluate_strategy_kill_switch(orders=session.get("orders") or [])
        if kill_switch["action"] == "PAUSE_STRATEGY":
            insert_strategy_kill_switch_event(
                {
                    "candidate_strategy_id": candidate["id"],
                    "exchange": config["exchange"],
                    "market": candidate.get("market") or session.get("market"),
                    "strategy_name": candidate.get("strategy") or session.get("strategy_name"),
                    "action": "PAUSED",
                    "reason": ",".join(kill_switch.get("blockers") or []),
                    "blockers": kill_switch.get("blockers") or [],
                    "metrics": kill_switch,
                }
            )
            promote_candidate_strategy(
                int(candidate["id"]),
                "PAUSED",
                reason="Strategy kill switch paused candidate",
                metadata={"forward_session_id": session["id"], "kill_switch": kill_switch},
            )
            blocked.append({"candidate_id": candidate["id"], "forward_session_id": session["id"], "reasons": kill_switch["blockers"], "kill_switch": kill_switch})
            continue
        passes, blockers = _session_passes(session, config)
        if not passes:
            blocked.append({"candidate_id": candidate["id"], "forward_session_id": session["id"], "reasons": blockers})
            continue
        current_status = str(candidate.get("status") or "")
        if current_status == "SHADOW_RUNNING":
            candidate = promote_candidate_strategy(
                int(candidate["id"]),
                "SHADOW_PASSED",
                reason="Forward Paper shadow gates passed",
                metadata={"forward_session_id": session["id"], "metrics": session.get("metrics", {})},
            ) or candidate
            promoted.append({"candidate": candidate, "to_status": "SHADOW_PASSED", "forward_session_id": session["id"]})
        if str(candidate.get("status") or "") == "SHADOW_PASSED":
            candidate = promote_candidate_strategy(
                int(candidate["id"]),
                "LIVE_ELIGIBLE",
                reason="Forward Paper shadow gates passed; live selector eligible",
                metadata={"forward_session_id": session["id"], "metrics": session.get("metrics", {})},
            ) or candidate
            promoted.append({"candidate": candidate, "to_status": "LIVE_ELIGIBLE", "forward_session_id": session["id"]})
    return {"promoted": promoted, "blocked": blocked}


def apply_selector_if_allowed(*, exchange: str | None = None) -> dict:
    config = _config()
    exchange = exchange or str(config["exchange"])
    if not config["selector_apply_enabled"]:
        return {"decision": "BLOCKED", "blockers": ["SELECTOR_APPLY_DISABLED"]}

    status = evaluate_auto_strategy_selector(exchange=exchange, apply=False)
    best = status.get("best_candidate")
    active = load_active_strategy_selection()
    market = str((best or active or {}).get("market") or "KRW-BTC")
    policy = load_global_bot_operation_policy()
    blockers: list[str] = []
    if not policy.get("auto_trading_enabled"):
        blockers.append("POLICY_AUTO_TRADING_DISABLED")
    if is_emergency_stopped():
        blockers.append("EMERGENCY_STOP_ACTIVE")
    if best:
        best_market = str(best.get("market") or market)
        if not market_is_live_allowed(exchange, best_market):
            blockers.append("MARKET_NOT_LIVE_ALLOWED")
        risk_state = compute_risk_state(exchange, best_market)
        if risk_state.get("status") in {"BLOCKED", "EMERGENCY_STOPPED"}:
            blockers.append("RISK_STATE_BLOCKED")
        if has_unresolved_live_order(exchange, best_market) or has_unresolved_live_order_for_exchange(exchange):
            blockers.append("UNRESOLVED_OPEN_ORDER")
        open_positions = load_open_live_positions_for_exchange(exchange)
        max_open_positions = int(os.getenv("AUTO_SELECTOR_MAX_OPEN_POSITIONS", os.getenv("AUTO_MAX_OPEN_POSITION_COUNT", "5")))
        if len(open_positions) >= max_open_positions:
            blockers.append("OPEN_POSITION_LIMIT")
        elif load_open_live_positions(exchange, best_market):
            blockers.append("DUPLICATE_MARKET_POSITION")
    else:
        risk_state = {}
    blockers.extend(status.get("blockers") or [])
    blockers = list(dict.fromkeys(blockers))
    if blockers:
        record_strategy_switch(
            from_candidate_strategy_id=int(active["candidate_strategy_id"]) if active else None,
            to_candidate_strategy_id=int(best["id"]) if best else None,
            from_market=str(active.get("market")) if active else None,
            to_market=str(best.get("market")) if best else None,
            decision="BLOCKED",
            blocked_reason=", ".join(blockers),
            score_delta=float(status.get("score_delta") or 0.0),
        )
        return {**status, "decision": "BLOCKED", "can_apply": False, "blockers": blockers, "risk_state": risk_state}
    return evaluate_auto_strategy_selector(exchange=exchange, apply=True)


async def run_strategy_promotion_pipeline_async(*, exchange: str | None = None) -> dict:
    enrolled = await enroll_backtest_passed_candidates()
    promoted = promote_shadow_candidates()
    from app.capital_allocator import run_capital_allocator_once

    allocator = await asyncio.to_thread(run_capital_allocator_once, "PROMOTION_SELECTOR", exchange=exchange or _config()["exchange"])
    selector = apply_selector_if_allowed(exchange=exchange)
    return {
        "ok": True,
        "exchange": exchange or _config()["exchange"],
        "enrolled": enrolled,
        "promoted": promoted,
        "selector": selector,
        "allocator": allocator,
        "switch_logs": load_strategy_switch_logs_with_candidates(10),
    }


def run_strategy_promotion_pipeline(*, exchange: str | None = None) -> dict:
    return asyncio.run(run_strategy_promotion_pipeline_async(exchange=exchange))


def run_strategy_promotion_scheduler_tick() -> None:
    run_strategy_promotion_pipeline()
