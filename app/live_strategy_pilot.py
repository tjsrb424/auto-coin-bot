from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any

import httpx

from app.backtest import candles_to_frame
from app.database import (
    count_live_strategy_orders_today,
    create_live_position,
    create_live_strategy_session,
    get_live_order_log,
    get_live_order_log_by_uuid,
    has_live_strategy_order_for_signal,
    has_open_live_position_for_strategy,
    has_open_live_strategy_order,
    insert_candles,
    insert_live_order_log,
    insert_live_signal_log,
    load_active_strategy_selection,
    load_candidate_strategy,
    load_bot_operation_policy,
    load_candles,
    load_latest_live_strategy_session,
    load_live_order_logs,
    load_open_live_position,
    load_open_live_position_for_strategy,
    load_open_live_positions_for_exchange,
    load_live_position_by_entry_order_uuid,
    load_running_live_strategy_sessions,
    market_is_live_allowed,
    update_order_intent,
    update_live_order_log,
    update_live_position,
    update_live_strategy_session,
)
from app.auto_strategy_selector import evaluate_auto_strategy_selector
from app.forward_paper import latest_completed_candle
from app.live_broker import (
    BithumbBroker,
    LiveTradingConfig,
    _available_balance,
    current_live_mode,
    evaluate_live_order_risk,
    get_live_broker,
    is_emergency_stopped,
    masked_exchange_request,
)
from app.live_recovery import (
    auto_order_recovery_block_reason,
    is_timeout_exception,
    normalize_exchange_order,
    reconcile_order_log,
    log_recovery_event,
    recent_recovery_events,
    sync_open_orders,
)
from app.live_exit import (
    LiveExitConfig,
    create_exit_candidate_for_position,
    create_exit_order_preview,
    live_exit_status,
    manage_exit_order_timeout,
    maybe_create_price_exit_candidate,
    submit_exit_order,
)
from app.market_liquidity import one_minute_liquidity_snapshot
from app.risk_manager import check_order_risk
from app.smart_decision import record_shadow_decision
from app.shadow_report import build_shadow_report
from app.smart_promotion import evaluate_promotion, is_smart_live_mode, smart_engine_live_mode
from app.strategies import apply_strategy
from app.upbit import fetch_minute_candles

logger = logging.getLogger("uvicorn.error")
_strategy_tick_lock = Lock()
AUTO_STRATEGY_CONFIRMATION = "돈은 속도가 아니라 규율로 지킨다"
AUTO_STRATEGY_ORDER_CONFIRMATION = "PLACE AUTO LIVE ORDER"
SMART_AUTONOMOUS_STRATEGY_NAME = "smart_autonomous"
SMART_AUTONOMOUS_CANDIDATE_ID = 0


@dataclass(frozen=True)
class LiveStrategyConfig:
    exchange: str
    live_auto_trading_enabled: bool
    auto_strategy_pilot_enabled: bool
    smart_autonomous_trading_enabled: bool
    allowed_exchange: str
    allowed_market: str
    allowed_order_type: str
    max_order_krw: float
    max_orders_per_day: int
    max_open_position_count: int
    cooldown_seconds: int
    core_order_cooldown_seconds: int
    require_completed_candle: bool
    cancel_unfilled_after_seconds: int
    entry_price_offset_percent: float
    core_entry_price_offset_percent: float
    core_marketable_limit_enabled: bool
    core_marketable_limit_max_slippage_pct: float
    core_marketable_limit_price_buffer_pct: float
    stop_loss_percent: float
    take_profit_percent: float
    max_hold_minutes: int
    exit_enabled: bool
    market_order_enabled: bool

    @classmethod
    def from_env(cls) -> "LiveStrategyConfig":
        live_feature_allowed = os.getenv("APP_ENV", "development").lower() == "production" or os.getenv("ALLOW_DEV_LIVE_TRADING", "false").lower() == "true"
        entry_price_offset_percent = float(os.getenv("AUTO_ENTRY_PRICE_OFFSET_PERCENT", os.getenv("AUTO_BUY_PRICE_OFFSET_PERCENT", "0.3")))
        return cls(
            exchange=os.getenv("EXCHANGE", os.getenv("AUTO_ALLOWED_EXCHANGE", "bithumb")).strip().lower(),
            live_auto_trading_enabled=live_feature_allowed and os.getenv("LIVE_AUTO_TRADING_ENABLED", "false").lower() == "true",
            auto_strategy_pilot_enabled=live_feature_allowed and os.getenv("AUTO_STRATEGY_PILOT_ENABLED", "false").lower() == "true",
            smart_autonomous_trading_enabled=live_feature_allowed and os.getenv("SMART_AUTONOMOUS_TRADING_ENABLED", os.getenv("AUTO_STRATEGY_PILOT_ENABLED", "false")).lower() == "true",
            allowed_exchange=os.getenv("AUTO_ALLOWED_EXCHANGE", "bithumb").strip().lower(),
            allowed_market=os.getenv("AUTO_ALLOWED_MARKET", "KRW-BTC"),
            allowed_order_type=os.getenv("AUTO_ALLOWED_ORDER_TYPE", os.getenv("AUTO_ORDER_TYPE", "limit")).strip().lower(),
            max_order_krw=float(os.getenv("AUTO_MAX_ORDER_KRW", "30000")),
            max_orders_per_day=int(os.getenv("AUTO_MAX_ORDERS_PER_DAY", "3")),
            max_open_position_count=int(os.getenv("AUTO_MAX_OPEN_POSITION_COUNT", "1")),
            cooldown_seconds=int(os.getenv("AUTO_COOLDOWN_SECONDS", "1800")),
            core_order_cooldown_seconds=int(os.getenv("SMART_CORE_ORDER_COOLDOWN_SECONDS", os.getenv("AUTO_COOLDOWN_SECONDS", "1800"))),
            require_completed_candle=os.getenv("AUTO_REQUIRE_COMPLETED_CANDLE", "true").lower() == "true",
            cancel_unfilled_after_seconds=int(os.getenv("AUTO_CANCEL_UNFILLED_AFTER_SECONDS", os.getenv("AUTO_CANCEL_AFTER_SECONDS", "60"))),
            entry_price_offset_percent=entry_price_offset_percent,
            core_entry_price_offset_percent=float(os.getenv("SMART_CORE_ENTRY_PRICE_OFFSET_PERCENT", str(entry_price_offset_percent))),
            core_marketable_limit_enabled=os.getenv("SMART_CORE_MARKETABLE_LIMIT_ENABLED", "false").lower() == "true",
            core_marketable_limit_max_slippage_pct=float(os.getenv("SMART_CORE_MARKETABLE_LIMIT_MAX_SLIPPAGE_PCT", "0.15")),
            core_marketable_limit_price_buffer_pct=float(os.getenv("SMART_CORE_MARKETABLE_LIMIT_PRICE_BUFFER_PCT", "0.02")),
            stop_loss_percent=float(os.getenv("AUTO_STOP_LOSS_PERCENT", "0.7")),
            take_profit_percent=float(os.getenv("AUTO_TAKE_PROFIT_PERCENT", "1.0")),
            max_hold_minutes=int(os.getenv("AUTO_MAX_HOLD_MINUTES", "60")),
            exit_enabled=os.getenv("AUTO_EXIT_ENABLED", "false").lower() == "true",
            market_order_enabled=os.getenv("AUTO_MARKET_ORDER_ENABLED", "false").lower() == "true",
        )


def _smart_autonomous_candidate(config: LiveStrategyConfig) -> dict:
    unit = int(os.getenv("SMART_AUTONOMOUS_CANDLE_UNIT", os.getenv("AUTO_CANDLE_UNIT", "5")))
    return {
        "id": SMART_AUTONOMOUS_CANDIDATE_ID,
        "name": "Smart Autonomous Engine",
        "strategy": SMART_AUTONOMOUS_STRATEGY_NAME,
        "parameters": {"mode": smart_engine_live_mode(), "unit": unit},
        "unit": unit,
        "market": config.allowed_market,
    }


def _is_smart_autonomous_session(session: dict) -> bool:
    return (
        int(session.get("candidate_strategy_id") or 0) == SMART_AUTONOMOUS_CANDIDATE_ID
        or str(session.get("strategy_name") or "").lower() == SMART_AUTONOMOUS_STRATEGY_NAME
    )


def _is_smart_order_mode(session: dict) -> bool:
    return is_smart_live_mode(smart_engine_live_mode())


def _session_candidate(session: dict, config: LiveStrategyConfig) -> dict | None:
    if _is_smart_autonomous_session(session):
        return _smart_autonomous_candidate(config)
    candidate_id = session.get("candidate_strategy_id")
    return load_candidate_strategy(int(candidate_id)) if candidate_id is not None else None


def _session_market(session: dict, config: LiveStrategyConfig) -> str:
    return str(session.get("market") or config.allowed_market or "KRW-BTC")


def _active_selector_candidate() -> dict | None:
    active = load_active_strategy_selection()
    if not active:
        return None
    return load_candidate_strategy(int(active["candidate_strategy_id"]))


def _sync_session_to_active_selector(session: dict, config: LiveStrategyConfig) -> dict:
    candidate = _active_selector_candidate()
    if not candidate:
        return session
    if int(candidate.get("id") or 0) == int(session.get("candidate_strategy_id") or 0):
        return session
    candidate_market = str(candidate.get("market") or "")
    candidate_status = str(candidate.get("status") or "")
    if candidate_status not in {"LIVE_ELIGIBLE", "LIVE_ACTIVE"}:
        return session
    if not market_is_live_allowed(config.allowed_exchange, candidate_market):
        return session
    if load_open_live_positions_for_exchange(config.allowed_exchange):
        return session
    update_live_strategy_session(
        int(session["id"]),
        {
            "candidate_strategy_id": int(candidate["id"]),
            "market": candidate_market,
            "strategy_name": candidate["strategy"],
            "strategy_parameters": candidate.get("parameters", {}),
            "last_risk_result": "ACTIVE_SELECTOR_SYNCED",
            "last_order_status": "WAITING_NEXT_ENTRY",
            "last_signal": "NONE",
            "last_processed_candle_time_utc": None,
        },
    )
    return {
        **session,
        "candidate_strategy_id": int(candidate["id"]),
        "market": candidate_market,
        "strategy_name": candidate["strategy"],
        "strategy_parameters": candidate.get("parameters", {}),
        "last_risk_result": "ACTIVE_SELECTOR_SYNCED",
        "last_order_status": "WAITING_NEXT_ENTRY",
        "last_signal": "NONE",
        "last_processed_candle_time_utc": None,
    }


def _neutral_legacy_signal() -> dict:
    return {
        "signal": "HOLD",
        "reason": "Smart Autonomous Engine uses internal indicators as reference signals.",
    }


def live_strategy_status() -> dict:
    config = LiveStrategyConfig.from_env()
    live_config = LiveTradingConfig.for_exchange(config.allowed_exchange)
    session = load_latest_live_strategy_session()
    if session and session.get("current_open_order_uuid"):
        session = _sync_live_strategy_order_status(session, config)
    session_market = _session_market(session or {}, config)
    open_position = load_open_live_position(
        int(session["id"]) if session else None,
        config.allowed_exchange,
        session_market,
    )
    exit_state = live_exit_status(
        int(session["id"]) if session else None,
        int(open_position["id"]) if open_position else None,
    )
    return {
        "session": session,
        "position": open_position,
        **exit_state,
        "exchange": config.allowed_exchange,
        "market": session_market,
        "current_mode": _mode(session),
        "live_trading_enabled": live_config.live_trading_enabled,
        "live_auto_trading_enabled": config.live_auto_trading_enabled,
        "auto_strategy_pilot_enabled": config.auto_strategy_pilot_enabled,
        "smart_autonomous_trading_enabled": config.smart_autonomous_trading_enabled,
        "emergency_stop": is_emergency_stopped(),
        "api_key_loaded": live_config.api_key_loaded,
        "max_order_krw": config.max_order_krw,
        "max_orders_per_day": config.max_orders_per_day,
        "max_open_position_count": config.max_open_position_count,
        "core_order_cooldown_seconds": config.core_order_cooldown_seconds,
        "cancel_unfilled_after_seconds": config.cancel_unfilled_after_seconds,
        "entry_price_offset_percent": config.entry_price_offset_percent,
        "exit_enabled": config.exit_enabled,
        "market_order_enabled": config.market_order_enabled,
        "partial_fill_policy": "PAUSE_AND_CANCEL_REMAINDER",
        "restart_policy": "RUNNING_SESSIONS_START_AS_LIVE_PAUSED",
        "recent_recovery_events": recent_recovery_events(10),
    }


def start_live_strategy_pilot(*, candidate_strategy_id: int | None = None, confirmation: str, order_confirmation: str) -> dict:
    if confirmation != AUTO_STRATEGY_CONFIRMATION:
        return {"ok": False, "message": f"{AUTO_STRATEGY_CONFIRMATION} confirmation is required.", **live_strategy_status()}
    if order_confirmation != AUTO_STRATEGY_ORDER_CONFIRMATION:
        return {"ok": False, "message": f"{AUTO_STRATEGY_ORDER_CONFIRMATION} confirmation is required.", **live_strategy_status()}
    config = LiveStrategyConfig.from_env()
    active_candidate = _active_selector_candidate() if candidate_strategy_id is None else None
    smart_autonomous = candidate_strategy_id is None and active_candidate is None
    candidate = active_candidate or (_smart_autonomous_candidate(config) if smart_autonomous else load_candidate_strategy(int(candidate_strategy_id)))
    if candidate is None:
        return {"ok": False, "message": "Candidate strategy not found.", **live_strategy_status()}
    candidate_market = str(candidate["market"])
    candidate_status = str(candidate.get("status") or "")
    if candidate_market != config.allowed_market and not (
        candidate_status in {"LIVE_ELIGIBLE", "LIVE_ACTIVE"} and market_is_live_allowed(config.allowed_exchange, candidate_market)
    ):
        return {"ok": False, "message": "Candidate market is not live-allowed.", **live_strategy_status()}
    if config.allowed_exchange != "bithumb":
        return {"ok": False, "message": "AUTO_ALLOWED_EXCHANGE=bithumb 설정이 필요합니다.", **live_strategy_status()}
    policy = load_bot_operation_policy(candidate_market)
    if not policy.get("auto_trading_enabled"):
        return {"ok": False, "message": "bot_operation_policy.auto_trading_enabled is OFF.", **live_strategy_status()}
    session_id = create_live_strategy_session(
        {
            "exchange": config.allowed_exchange,
            "market": candidate_market,
            "candidate_strategy_id": candidate["id"],
            "strategy_name": candidate["strategy"],
            "strategy_parameters": candidate.get("parameters", {}),
            "status": "READY",
            "auto_enabled": True,
            "initial_balance_krw": 0.0,
            "max_order_krw": config.max_order_krw,
            "max_orders_per_day": config.max_orders_per_day,
        }
    )
    run_live_strategy_tick()
    return {"ok": True, "session_id": session_id, **live_strategy_status()}


def stop_live_strategy_pilot() -> dict:
    session = load_latest_live_strategy_session()
    if session:
        update_live_strategy_session(
            int(session["id"]),
            {"status": "STOPPED", "auto_enabled": False, "stopped_at": _utc_now()},
        )
    return {"ok": True, **live_strategy_status()}


def cancel_live_strategy_open_order() -> dict:
    session = load_latest_live_strategy_session()
    if not session or not session.get("current_open_order_uuid"):
        return {"ok": False, "message": "No live strategy open order.", **live_strategy_status()}
    order_uuid = str(session["current_open_order_uuid"])
    try:
        response = asyncio.run(BithumbBroker().cancel_order(order_uuid))
        _update_order_by_uuid(order_uuid, "CANCELED", response)
        update_live_strategy_session(
            int(session["id"]),
            {
                "status": "STOPPED",
                "auto_enabled": False,
                "current_open_order_uuid": None,
                "last_order_status": "CANCELED",
                "stopped_at": _utc_now(),
            },
        )
        return {"ok": True, "message": "Live strategy order canceled.", **live_strategy_status()}
    except Exception as exc:
        update_live_strategy_session(int(session["id"]), {"status": "ERROR", "last_order_status": "FAILED"})
        return {"ok": False, "message": str(exc), **live_strategy_status()}


def run_live_strategy_tick() -> None:
    if not _strategy_tick_lock.acquire(blocking=False):
        return
    try:
        asyncio.run(process_live_strategy_sessions())
    finally:
        _strategy_tick_lock.release()


async def process_live_strategy_sessions() -> None:
    config = LiveStrategyConfig.from_env()
    sessions = load_running_live_strategy_sessions()
    try:
        for market in sorted({_session_market(session, config) for session in sessions} or {"KRW-BTC"}):
            await sync_open_orders("bithumb", market)
    except Exception as exc:
        logger.warning("[live-strategy] pending order reconciliation failed error=%s", exc)
    for session in sessions:
        try:
            await _process_session(session)
        except Exception as exc:
            logger.exception("[live-strategy] session=%s failed", session.get("id"))
            update_live_strategy_session(
                int(session["id"]),
                {"status": "ERROR", "last_risk_result": "BLOCKED_API_RESPONSE_ERROR", "last_order_status": "ERROR"},
            )
            _insert_blocked_log(session, "BLOCKED_API_RESPONSE_ERROR", str(exc), None, None)


async def _process_session(session: dict) -> None:
    config = LiveStrategyConfig.from_env()
    live_config = LiveTradingConfig.for_exchange(config.allowed_exchange)
    session_market = _session_market(session, config)

    if is_emergency_stopped():
        await _handle_emergency(session)
        return

    if session.get("current_open_order_uuid"):
        await _manage_open_order(session, config)
        return

    position = load_open_live_position(int(session["id"]), config.allowed_exchange, session_market)
    if position:
        await _process_open_position(session, position, config, live_config)
        return

    session = _sync_session_to_active_selector(session, config)
    session_market = _session_market(session, config)

    if _is_smart_autonomous_session(session):
        smart_position = load_open_live_position(None, config.allowed_exchange, session_market)
        if smart_position:
            update_live_strategy_session(
                int(session["id"]),
                {
                    "status": "RUNNING",
                    "current_position_id": int(smart_position["id"]),
                    "last_risk_result": "POSITION_ADOPTED_BY_SMART_ENGINE",
                    "last_order_status": "POSITION_OPEN",
                },
            )
            await _process_open_position(session, smart_position, config, live_config)
            return

    strategy_position = load_open_live_position_for_strategy(
        config.allowed_exchange,
        session_market,
        int(session["candidate_strategy_id"]),
    )
    if strategy_position:
        update_live_strategy_session(
            int(session["id"]),
            {
                "status": "RUNNING",
                "current_position_id": int(strategy_position["id"]),
                "last_risk_result": "POSITION_ADOPTED_FROM_STRATEGY",
                "last_order_status": "POSITION_OPEN",
            },
        )
        await _process_open_position(session, strategy_position, config, live_config)
        return

    blocked = await _precheck_block_reason(session, config, live_config, check_cooldown=not _is_smart_order_mode(session))
    if blocked:
        _insert_blocked_log(session, blocked, blocked, None, None)
        updates = {"last_risk_result": blocked, "last_order_status": "BLOCKED"}
        if _should_stop_on_block(blocked):
            updates.update({"status": "STOPPED", "auto_enabled": False, "stopped_at": _utc_now()})
        else:
            updates["status"] = "RUNNING"
        update_live_strategy_session(int(session["id"]), updates)
        return

    candidate = _session_candidate(session, config)
    if candidate is None:
        _insert_blocked_log(session, "BLOCKED_DUPLICATE_SIGNAL", "Candidate strategy not found.", None, None)
        update_live_strategy_session(int(session["id"]), {"status": "ERROR", "last_risk_result": "BLOCKED_DUPLICATE_SIGNAL"})
        return

    fresh = await fetch_minute_candles(market=session_market, unit=int(candidate["unit"]), count=300)
    insert_candles(fresh)
    candles = load_candles(session_market, int(candidate["unit"]), 300)
    latest = latest_completed_candle(candles, int(candidate["unit"])) if config.require_completed_candle else (candles[-1] if candles else None)
    if latest is None:
        return

    candle_time = latest["candle_time_utc"]
    signal = _neutral_legacy_signal() if _is_smart_autonomous_session(session) else _latest_signal(candidate, candles, candle_time)
    insert_live_signal_log(
        {
            "session_id": session["id"],
            "exchange": session["exchange"],
            "market": session["market"],
            "candidate_strategy_id": session["candidate_strategy_id"],
            "strategy_name": session["strategy_name"],
            "signal": signal["signal"],
            "confidence": 1.0,
            "reason": signal["reason"],
            "candle_time_utc": candle_time,
        }
    )
    update_live_strategy_session(
        int(session["id"]),
        {
            "status": "RUNNING",
            "last_signal": signal["signal"],
            "last_signal_time_utc": candle_time,
            "last_processed_candle_time_utc": candle_time,
        },
    )
    smart_snapshot = await _record_smart_decision(session=session, candidate=candidate, candles=candles, candle=latest, signal=signal)
    if _is_smart_order_mode(session):
        await _submit_smart_intent_order(session, latest, signal, config, live_config, smart_snapshot)
        return

    if _is_smart_autonomous_session(session):
        return

    if signal["signal"] == "SELL":
        position = load_open_live_position(int(session["id"]), config.allowed_exchange, session_market)
        if position and not config.exit_enabled:
            update_live_position(int(position["id"]), {"status": "EXIT_CANDIDATE", "current_price": float(latest["trade_price"])})
            update_live_strategy_session(int(session["id"]), {"current_position_id": int(position["id"]), "last_risk_result": "EXIT_CANDIDATE_ONLY"})
        return
    if signal["signal"] != "BUY":
        return
    if has_live_strategy_order_for_signal(int(session["id"]), int(session["candidate_strategy_id"]), session["market"], candle_time, signal["signal"], "BUY"):
        _insert_blocked_log(session, "BLOCKED_DUPLICATE_CANDLE", "Duplicate candle/signal.", candle_time, signal)
        update_live_strategy_session(int(session["id"]), {"last_risk_result": "BLOCKED_DUPLICATE_CANDLE", "last_order_status": "BLOCKED"})
        return

    await _submit_entry_order(session, candidate, latest, signal, config, live_config)


async def _process_open_position(session: dict, position: dict, config: LiveStrategyConfig, live_config: LiveTradingConfig) -> None:
    await manage_exit_order_timeout(position, LiveExitConfig.from_env())
    candidate = _session_candidate(session, config)
    if candidate is None:
        update_live_strategy_session(int(session["id"]), {"last_risk_result": "BLOCKED_DUPLICATE_SIGNAL"})
        return

    session_market = _session_market(session, config)
    fresh = await fetch_minute_candles(market=session_market, unit=int(candidate["unit"]), count=300)
    insert_candles(fresh)
    candles = load_candles(session_market, int(candidate["unit"]), 300)
    latest = latest_completed_candle(candles, int(candidate["unit"])) if config.require_completed_candle else (candles[-1] if candles else None)
    if latest is None:
        return
    candle_time = latest["candle_time_utc"]
    current_price = float(latest["trade_price"])
    smart_runtime = _is_smart_autonomous_session(session) or _is_smart_order_mode(session)
    exit_candidate = None if smart_runtime else maybe_create_price_exit_candidate(position, current_price, candle_time)
    signal = _neutral_legacy_signal() if _is_smart_autonomous_session(session) else _latest_signal(candidate, candles, candle_time)
    insert_live_signal_log(
        {
            "session_id": session["id"],
            "exchange": session["exchange"],
            "market": session["market"],
            "candidate_strategy_id": session["candidate_strategy_id"],
            "strategy_name": session["strategy_name"],
            "signal": signal["signal"],
            "confidence": 1.0,
            "reason": signal["reason"],
            "candle_time_utc": candle_time,
        }
    )
    if not smart_runtime and signal["signal"] == "SELL" and exit_candidate is None:
        exit_candidate = create_exit_candidate_for_position(position, "STRATEGY_SELL", current_price, candle_time)
    smart_snapshot = await _record_smart_decision(session=session, candidate=candidate, candles=candles, candle=latest, signal=signal)
    if _is_smart_order_mode(session):
        handled = await _submit_smart_intent_order(session, latest, signal, config, live_config, smart_snapshot)
        if handled:
            return
    auto_exit_result = None
    if exit_candidate and config.exit_enabled:
        auto_exit_result = await _submit_auto_exit_candidate(session, exit_candidate)
    update_live_strategy_session(
        int(session["id"]),
        {
            "status": "RUNNING" if auto_exit_result and auto_exit_result.get("ok") else ("PAUSED" if exit_candidate else "RUNNING"),
            "last_signal": signal["signal"],
            "last_signal_time_utc": candle_time,
            "last_processed_candle_time_utc": candle_time,
            "last_risk_result": (
                "AUTO_EXIT_SUBMITTED"
                if auto_exit_result and auto_exit_result.get("ok")
                else (str(exit_candidate["reason"]) + "_EXIT_CANDIDATE" if exit_candidate else "POSITION_OPEN")
            ),
        },
    )


async def _submit_auto_exit_candidate(session: dict, exit_candidate: dict) -> dict:
    try:
        if str(exit_candidate.get("status") or "").upper() == "SUBMITTED":
            return {"ok": True, "status": "SUBMITTED", "risk_result": "AUTO_EXIT_ALREADY_SUBMITTED"}
        preview = await create_exit_order_preview(int(exit_candidate["id"]), manual_confirmed=True, is_auto_exit=True)
        if not preview.get("ok"):
            preview_risk = preview.get("preview") if isinstance(preview.get("preview"), dict) else {}
            update_live_strategy_session(
                int(session["id"]),
                {
                    "status": "PAUSED",
                    "last_risk_result": str(preview_risk.get("risk_result") or "AUTO_EXIT_BLOCKED"),
                    "last_order_status": "BLOCKED",
                },
            )
            return preview
        request_id = str(preview["request_id"])
        result = await submit_exit_order(request_id, final_confirmation="SUBMIT LIMIT EXIT ORDER")
        update_live_strategy_session(
            int(session["id"]),
            {
                "last_risk_result": str(result.get("risk_result") or "AUTO_EXIT_SUBMITTED"),
                "last_order_status": str(result.get("status") or "SUBMITTED"),
            },
        )
        return result
    except Exception as exc:
        logger.exception("[live-strategy] auto exit submit failed session=%s candidate=%s", session.get("id"), exit_candidate.get("id"))
        update_live_strategy_session(
            int(session["id"]),
            {"status": "PAUSED", "last_risk_result": "AUTO_EXIT_FAILED", "last_order_status": "FAILED"},
        )
        log_recovery_event(
            "AUTO_EXIT_FAILED",
            "ERROR",
            "Auto exit order submission failed.",
            exchange=str(exit_candidate.get("exchange") or "bithumb"),
            market=str(exit_candidate.get("market") or "KRW-BTC"),
            session_id=int(session["id"]),
            payload={"exit_candidate_id": exit_candidate.get("id"), "error": str(exc)},
        )
        return {"ok": False, "status": "FAILED", "risk_result": "AUTO_EXIT_FAILED", "message": str(exc)}


async def _record_smart_decision(*, session: dict, candidate: dict, candles: list[dict], candle: dict, signal: dict) -> dict | None:
    available_krw_balance = None
    try:
        balances = await get_live_broker("bithumb").get_balances()
        available_krw_balance = _available_balance(balances, "KRW")
    except Exception:
        available_krw_balance = None
    try:
        return record_shadow_decision(session=session, candidate=candidate, candles=candles, candle=candle, legacy_signal=signal, available_krw_balance=available_krw_balance)
    except Exception as exc:
        logger.warning("[smart-decision] shadow decision record failed session_id=%s error=%s", session.get("id"), exc)
        return None


async def _precheck_block_reason(session: dict, config: LiveStrategyConfig, live_config: LiveTradingConfig, *, check_cooldown: bool = True) -> str | None:
    session_market = _session_market(session, config)
    if is_emergency_stopped():
        return "BLOCKED_EMERGENCY_STOP"
    if not live_config.live_trading_enabled:
        return "BLOCKED_LIVE_DISABLED"
    if not config.live_auto_trading_enabled:
        return "BLOCKED_AUTO_DISABLED"
    if not (config.auto_strategy_pilot_enabled or config.smart_autonomous_trading_enabled):
        return "BLOCKED_AUTO_STRATEGY_DISABLED"
    if config.allowed_exchange != "bithumb" or session["exchange"] != "bithumb":
        return "BLOCKED_EXCHANGE_NOT_ALLOWED"
    if session_market != "KRW-BTC" and not market_is_live_allowed("bithumb", session_market):
        return "BLOCKED_MARKET_NOT_ALLOWED"
    if not load_bot_operation_policy(session_market).get("auto_trading_enabled"):
        return "SMART_POLICY_AUTO_TRADING_DISABLED"
    if config.allowed_order_type != "limit":
        return "BLOCKED_ORDER_TYPE_NOT_ALLOWED"
    if config.market_order_enabled:
        return "BLOCKED_MARKET_ORDER_DISABLED"
    if not live_config.api_key_loaded:
        return "BLOCKED_ORDER_CHANCE_FAILED"
    if config.max_orders_per_day > 0 and count_live_strategy_orders_today("bithumb", session_market) >= config.max_orders_per_day:
        return "BLOCKED_MAX_ORDERS_PER_DAY"
    if has_open_live_strategy_order("bithumb", session_market):
        return "BLOCKED_OPEN_ORDER_EXISTS"
    if not _is_smart_autonomous_session(session) and has_open_live_position_for_strategy("bithumb", session_market, int(session["candidate_strategy_id"])):
        return "BLOCKED_OPEN_POSITION_EXISTS"
    recovery_block = await auto_order_recovery_block_reason("bithumb", session_market)
    if recovery_block:
        return recovery_block
    last_order_time = session.get("last_order_time_utc")
    if check_cooldown and last_order_time and _seconds_since(str(last_order_time)) < config.cooldown_seconds:
        return "BLOCKED_COOLDOWN"
    return None


def _smart_bid_cap_preview(
    *,
    original_delta_value_krw: float,
    amount_requested_krw: float,
    capped_order_amount_krw: float,
    hard_cap_krw: float,
    mode_cap_krw: float,
    max_order_krw: float,
    max_live_order_krw: float,
    remaining_exposure_krw: float,
    available_krw_balance: float,
) -> dict:
    return {
        "original_delta_value_krw": original_delta_value_krw,
        "amount_requested_krw": amount_requested_krw,
        "capped_order_amount_krw": max(capped_order_amount_krw, 0.0),
        "hard_cap_krw": max(hard_cap_krw, 0.0),
        "mode_cap_krw": max(mode_cap_krw, 0.0),
        "max_order_krw": max_order_krw,
        "max_live_order_krw": max_live_order_krw,
        "remaining_exposure_krw": max(remaining_exposure_krw, 0.0),
        "available_krw_balance": max(available_krw_balance, 0.0),
        "cap_applied": capped_order_amount_krw < amount_requested_krw,
    }


def _smart_bid_cap_blocker(
    *,
    amount_requested: float,
    available_krw: float,
    remaining_exposure: float,
    hard_cap: float,
    min_order_krw: float,
) -> str:
    if amount_requested <= 0:
        return "SMART_ORDER_AMOUNT_ZERO"
    if amount_requested < min_order_krw:
        return "SMART_ORDER_AMOUNT_BELOW_MIN"
    if available_krw <= 0 or available_krw < min_order_krw:
        return "SMART_INSUFFICIENT_KRW_BALANCE"
    if remaining_exposure <= 0:
        return "SMART_MAX_TOTAL_EXPOSURE_REACHED"
    if remaining_exposure < min_order_krw:
        return "SMART_REMAINING_EXPOSURE_BELOW_MIN"
    if hard_cap <= 0:
        return "SMART_ORDER_CAP_ZERO"
    if hard_cap < min_order_krw:
        return "SMART_CAPPED_ORDER_BELOW_MIN"
    return "SMART_CAPPED_ORDER_BELOW_MIN"


def _mark_smart_dust_intent(
    session: dict,
    intent_id: Any,
    blocker: str,
    policy_preview: dict,
) -> None:
    if intent_id:
        update_order_intent(
            int(intent_id),
            {
                "status": "BLOCKED",
                "promotion_status": "DUST_HOLD",
                "promotion_blockers": [blocker],
                "policy_preview": policy_preview,
            },
        )
    update_live_strategy_session(int(session["id"]), {"last_risk_result": blocker, "last_order_status": "BLOCKED"})


def _smart_core_accumulation_bid(intent: dict, smart_snapshot: dict) -> bool:
    policy_preview = intent.get("policy_preview") or {}
    target_source = str(
        smart_snapshot.get("final_target_exposure_source")
        or intent.get("target_source")
        or policy_preview.get("target_source")
        or ""
    ).upper()
    if target_source == "CORE":
        return True
    if bool(smart_snapshot.get("core_exposure_applied")) or bool(policy_preview.get("core_exposure_applied")):
        return True
    current_exposure_pct = _float(smart_snapshot.get("current_exposure_pct"))
    core_exposure_pct = _float(smart_snapshot.get("core_exposure_pct", policy_preview.get("core_exposure_pct")))
    return core_exposure_pct > 0 and current_exposure_pct < core_exposure_pct


def _smart_cooldown_preview(session: dict, config: LiveStrategyConfig, *, core_accumulation: bool) -> dict | None:
    last_order_time = session.get("last_order_time_utc")
    if not last_order_time:
        return None
    seconds_since = _seconds_since(str(last_order_time))
    cooldown_seconds = config.core_order_cooldown_seconds if core_accumulation else config.cooldown_seconds
    remaining = max(cooldown_seconds - seconds_since, 0.0)
    if remaining <= 0:
        return None
    return {
        "cooldown_seconds_applied": cooldown_seconds,
        "cooldown_type": "CORE_ACCUMULATION" if core_accumulation else "DEFAULT",
        "last_order_time_utc": last_order_time,
        "seconds_since_last_order": seconds_since,
        "remaining_cooldown_seconds": remaining,
    }


def _block_smart_intent_cooldown(
    session: dict,
    intent: dict,
    blocker: str,
    cooldown_preview: dict,
) -> None:
    intent_id = intent.get("id")
    policy_preview = {**(intent.get("policy_preview") or {}), **cooldown_preview}
    if intent_id:
        update_order_intent(
            int(intent_id),
            {
                "status": "BLOCKED",
                "promotion_status": "BLOCKED",
                "promotion_blockers": [blocker],
                "policy_preview": policy_preview,
            },
        )
    update_live_strategy_session(int(session["id"]), {"last_risk_result": blocker, "last_order_status": "BLOCKED", "status": "RUNNING"})


async def _bithumb_orderbook_top(market: str) -> dict:
    empty = {"best_bid": None, "best_ask": None, "spread_krw": None, "spread_pct": None}
    base_url = os.getenv("BITHUMB_BASE_URL", "https://api.bithumb.com").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{base_url}/v1/orderbook", params={"markets": market})
        if response.status_code >= 400:
            return empty
        payload = response.json()
        first = payload[0] if isinstance(payload, list) and payload else payload if isinstance(payload, dict) else {}
        units = first.get("orderbook_units") if isinstance(first, dict) else None
        top = units[0] if isinstance(units, list) and units else {}
        best_bid = _float(top.get("bid_price")) if isinstance(top, dict) else 0.0
        best_ask = _float(top.get("ask_price")) if isinstance(top, dict) else 0.0
        if best_bid <= 0 or best_ask <= 0:
            return empty
        spread = best_ask - best_bid
        mid = (best_ask + best_bid) / 2
        return {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread_krw": spread,
            "spread_pct": (spread / mid * 100) if mid > 0 else None,
        }
    except Exception:
        return empty


def _smart_bid_price_preview(
    *,
    current_price: float,
    order_price: float,
    entry_offset_percent: float,
    config: LiveStrategyConfig,
    core_accumulation: bool,
    orderbook_top: dict,
) -> dict:
    price_gap_krw = current_price - order_price
    return {
        "price_policy": "CORE_ACCUMULATION_LIMIT" if core_accumulation else "DEFAULT_PASSIVE_LIMIT",
        "current_price": current_price,
        "order_price": order_price,
        "entry_offset_percent": entry_offset_percent,
        "core_entry_offset_percent": config.core_entry_price_offset_percent,
        "auto_entry_offset_percent": config.entry_price_offset_percent,
        "price_buffer_pct": config.core_marketable_limit_price_buffer_pct,
        "max_slippage_pct": config.core_marketable_limit_max_slippage_pct,
        "price_gap_pct": (price_gap_krw / current_price * 100) if current_price > 0 else None,
        "price_gap_krw": price_gap_krw,
        "best_bid": orderbook_top.get("best_bid"),
        "best_ask": orderbook_top.get("best_ask"),
        "spread_krw": orderbook_top.get("spread_krw"),
        "spread_pct": orderbook_top.get("spread_pct"),
        "marketable_limit_enabled": False,
        "marketable_limit_fallback_reason": None,
    }


def _round_krw_price_up(price: float) -> float:
    if price <= 0:
        return 0.0
    if price >= 1_000_000:
        unit = 1000
    elif price >= 100_000:
        unit = 100
    elif price >= 10_000:
        unit = 10
    else:
        unit = 1
    return float(((int(price) + unit - 1) // unit) * unit)


def _smart_bid_price_policy(
    *,
    current_price: float,
    config: LiveStrategyConfig,
    core_accumulation: bool,
    orderbook_top: dict,
) -> tuple[float, dict]:
    fallback_offset = config.core_entry_price_offset_percent if core_accumulation else config.entry_price_offset_percent
    fallback_price = _round_krw_price(current_price * (1 - fallback_offset / 100))
    fallback_preview = _smart_bid_price_preview(
        current_price=current_price,
        order_price=fallback_price,
        entry_offset_percent=fallback_offset,
        config=config,
        core_accumulation=core_accumulation,
        orderbook_top=orderbook_top,
    )
    if not core_accumulation:
        return fallback_price, fallback_preview
    if not config.core_marketable_limit_enabled:
        return fallback_price, {**fallback_preview, "marketable_limit_fallback_reason": "MARKETABLE_LIMIT_DISABLED"}
    best_ask = _float(orderbook_top.get("best_ask"))
    if best_ask <= 0:
        reason = "ORDERBOOK_UNAVAILABLE" if orderbook_top.get("best_ask") is None else "BEST_ASK_INVALID"
        return fallback_price, {
            **fallback_preview,
            "price_policy": "CORE_MARKETABLE_LIMIT_FALLBACK_OFFSET",
            "marketable_limit_enabled": True,
            "marketable_limit_fallback_reason": reason,
        }
    marketable_price = max(best_ask, _round_krw_price_up(best_ask * (1 + config.core_marketable_limit_price_buffer_pct / 100)))
    max_price = current_price * (1 + config.core_marketable_limit_max_slippage_pct / 100)
    if current_price > 0 and marketable_price > max_price:
        return fallback_price, {
            **fallback_preview,
            "price_policy": "CORE_MARKETABLE_LIMIT_FALLBACK_OFFSET",
            "marketable_limit_enabled": True,
            "marketable_limit_fallback_reason": "BEST_ASK_TOO_FAR_FROM_CURRENT",
        }
    marketable_preview = _smart_bid_price_preview(
        current_price=current_price,
        order_price=marketable_price,
        entry_offset_percent=0.0,
        config=config,
        core_accumulation=core_accumulation,
        orderbook_top=orderbook_top,
    )
    return marketable_price, {
        **marketable_preview,
        "price_policy": "CORE_MARKETABLE_LIMIT",
        "marketable_limit_enabled": True,
        "marketable_limit_fallback_reason": None,
    }


def _smart_submitted_request_payload(order: dict, price_preview: dict, config: LiveStrategyConfig) -> dict:
    return {
        **masked_exchange_request(order),
        "submitted_price": order.get("price"),
        "submitted_current_price": price_preview.get("current_price"),
        "submitted_best_bid": price_preview.get("best_bid"),
        "submitted_best_ask": price_preview.get("best_ask"),
        "entry_offset_percent": price_preview.get("entry_offset_percent"),
        "price_policy": price_preview.get("price_policy"),
        "price_buffer_pct": price_preview.get("price_buffer_pct"),
        "max_slippage_pct": price_preview.get("max_slippage_pct"),
        "marketable_limit_fallback_reason": price_preview.get("marketable_limit_fallback_reason"),
        "cancel_unfilled_after_seconds": config.cancel_unfilled_after_seconds,
    }


def _block_smart_intent_order(
    session: dict,
    intent_id: Any,
    blocker: str,
    candle_time_utc: str | None,
    signal: dict | None,
    cap_preview: dict,
) -> None:
    if intent_id:
        update_order_intent(
            int(intent_id),
            {
                "status": "BLOCKED",
                "promotion_status": "BLOCKED",
                "promotion_blockers": [blocker],
                "policy_preview": cap_preview,
            },
        )
    _insert_blocked_log(
        session,
        blocker,
        blocker,
        candle_time_utc,
        signal,
        preview={"risk_result": blocker, "fee_estimate": 0.0, "policy_preview": cap_preview},
    )
    update_live_strategy_session(int(session["id"]), {"last_risk_result": blocker, "last_order_status": "BLOCKED"})


async def _submit_smart_intent_order(
    session: dict,
    candle: dict,
    signal: dict,
    config: LiveStrategyConfig,
    live_config: LiveTradingConfig,
    smart_snapshot: dict | None,
) -> bool:
    if not smart_snapshot:
        return False
    intent = (smart_snapshot.get("order_intents") or [None])[0]
    if not intent:
        return False
    intent_id = intent.get("id")
    side = str(intent.get("side") or "").upper()
    current_price = float(candle["trade_price"])
    market = str(session.get("market") or "KRW-BTC")
    amount_requested = abs(_float(intent.get("delta_value_krw")))
    if side in {"BID", "BUY"} and 0 < amount_requested < live_config.min_order_krw:
        _mark_smart_dust_intent(
            session,
            intent_id,
            "SMART_ORDER_AMOUNT_BELOW_MIN",
            {
                "amount_requested_krw": amount_requested,
                "min_order_krw": live_config.min_order_krw,
                "dust_side": "BUY",
            },
        )
        return True
    if side in {"ASK", "SELL"}:
        sell_requested = amount_requested
        if sell_requested <= 0 and current_price > 0:
            sell_requested = abs(_float(intent.get("target_qty"))) * current_price
        if 0 < sell_requested < live_config.min_order_krw:
            _mark_smart_dust_intent(
                session,
                intent_id,
                "SMART_SELL_AMOUNT_BELOW_MIN",
                {
                    "amount_requested_krw": sell_requested,
                    "min_order_krw": live_config.min_order_krw,
                    "dust_side": "SELL",
                },
            )
            return True
    core_accumulation_bid = side in {"BID", "BUY"} and _smart_core_accumulation_bid(intent, smart_snapshot)
    cooldown_preview = _smart_cooldown_preview(session, config, core_accumulation=core_accumulation_bid)
    if cooldown_preview:
        blocker = "SMART_CORE_COOLDOWN" if core_accumulation_bid else "BLOCKED_COOLDOWN"
        _block_smart_intent_cooldown(session, intent, blocker, cooldown_preview)
        return True
    if any(
        has_live_strategy_order_for_signal(int(session["id"]), int(session["candidate_strategy_id"]), str(session.get("market") or "KRW-BTC"), candle["candle_time_utc"], signal.get("signal", "HOLD"), order_side)
        for order_side in ("BUY", "SELL")
    ):
        update_live_strategy_session(int(session["id"]), {"last_risk_result": "SMART_DUPLICATE_CANDLE", "last_order_status": "BLOCKED"})
        return True
    if side in {"ASK", "SELL"}:
        return await _submit_smart_intent_sell_order(session, candle, signal, live_config, smart_snapshot, intent)
    if side not in {"BID", "BUY"}:
        if intent_id:
            update_order_intent(
                int(intent_id),
                {
                    "promotion_status": "BLOCKED",
                    "promotion_blockers": ["SMART_LIMITED_SIDE_UNSUPPORTED"],
                    "status": "BLOCKED",
                },
            )
        return False
    broker = get_live_broker("bithumb")
    try:
        balances = await broker.get_balances()
        chance = await broker.get_order_chance(market)
    except Exception as exc:
        _insert_blocked_log(session, "SMART_ORDER_CHANCE_FAILED", str(exc), candle["candle_time_utc"], signal)
        if intent_id:
            update_order_intent(int(intent_id), {"promotion_status": "BLOCKED", "promotion_blockers": ["SMART_ORDER_CHANCE_FAILED"]})
        return True
    if not chance:
        _insert_blocked_log(session, "SMART_ORDER_CHANCE_FAILED", "Order chance failed.", candle["candle_time_utc"], signal)
        if intent_id:
            update_order_intent(int(intent_id), {"promotion_status": "BLOCKED", "promotion_blockers": ["SMART_ORDER_CHANCE_FAILED"]})
        return True
    max_total = _float(smart_snapshot.get("max_total_exposure_krw"))
    current_value = _float(smart_snapshot.get("current_bot_position_value_krw"))
    available_krw = _available_balance(balances, "KRW")
    mode = smart_engine_live_mode()
    mode_cap = max_total * 0.2 if mode == "limited" else max_total
    remaining_exposure = max(max_total - current_value, 0.0)
    hard_cap = min(mode_cap, config.max_order_krw, live_config.max_live_order_krw, remaining_exposure, available_krw)
    amount = min(amount_requested, hard_cap)
    cap_preview = _smart_bid_cap_preview(
        original_delta_value_krw=_float(intent.get("delta_value_krw")),
        amount_requested_krw=amount_requested,
        capped_order_amount_krw=amount,
        hard_cap_krw=hard_cap,
        mode_cap_krw=mode_cap,
        max_order_krw=config.max_order_krw,
        max_live_order_krw=live_config.max_live_order_krw,
        remaining_exposure_krw=remaining_exposure,
        available_krw_balance=available_krw,
    )
    if amount <= 0 or amount < live_config.min_order_krw:
        blocker = _smart_bid_cap_blocker(
            amount_requested=amount_requested,
            available_krw=available_krw,
            remaining_exposure=remaining_exposure,
            hard_cap=hard_cap,
            min_order_krw=live_config.min_order_krw,
        )
        if blocker in {"SMART_ORDER_AMOUNT_BELOW_MIN", "SMART_CAPPED_ORDER_BELOW_MIN"}:
            _mark_smart_dust_intent(session, intent_id, blocker, cap_preview)
            return True
        _block_smart_intent_order(session, intent_id, blocker, candle["candle_time_utc"], signal, cap_preview)
        return True
    core_accumulation = core_accumulation_bid
    try:
        orderbook_top = await _bithumb_orderbook_top(str(session.get("market") or "KRW-BTC"))
    except Exception:
        orderbook_top = {"best_bid": None, "best_ask": None, "spread_krw": None, "spread_pct": None}
    price, price_preview = _smart_bid_price_policy(
        current_price=current_price,
        config=config,
        core_accumulation=core_accumulation,
        orderbook_top=orderbook_top,
    )
    cap_preview = {**cap_preview, **price_preview}
    request_id = f"smart-rehearsal-{uuid.uuid4().hex[:18]}"
    order = {
        "request_id": request_id,
        "client_order_id": request_id[:36],
        "exchange": "bithumb",
        "market": market,
        "side": "BUY",
        "ord_type": "limit",
        "order_type": "LIMIT",
        "price": price,
        "amount_krw": amount,
        "volume": amount / price if price > 0 else 0.0,
    }
    risk_preview = evaluate_live_order_risk(
        order=order,
        config=live_config,
        mode="AUTO_STRATEGY_RUNNING",
        balances=balances,
        request_exists=False,
        recent_duplicate=False,
        market_snapshot={"price": current_price},
        is_auto=True,
    )
    risk_preview = check_order_risk(
        order=order,
        purpose="ENTRY",
        base_result=risk_preview,
        mode="AUTO_STRATEGY_RUNNING",
        session_id=int(session["id"]),
        candidate_strategy_id=None if _is_smart_autonomous_session(session) else int(session["candidate_strategy_id"]),
        candle_time_utc=candle["candle_time_utc"],
        signal=(signal or {}).get("signal"),
        market_snapshot={"price": current_price, "complete": True},
        balances=balances,
        is_auto=True,
    )
    risk_preview["policy_preview"] = {**(risk_preview.get("policy_preview") or {}), **cap_preview}
    try:
        recommendation = build_shadow_report(str(session.get("market") or "KRW-BTC"), limit=100).get("summary", {}).get("recommendation")
    except Exception:
        recommendation = None
    promotion = evaluate_promotion(
        intent={**intent, "delta_value_krw": amount},
        snapshot=smart_snapshot,
        policy=load_bot_operation_policy(str(session.get("market") or "KRW-BTC")),
        risk_preview=risk_preview,
        shadow_recommendation=recommendation,
        available_krw=available_krw,
        daily_smart_order_count=count_live_strategy_orders_today(str(session.get("exchange") or "bithumb"), str(session.get("market") or "KRW-BTC")),
        risk_score=_float(smart_snapshot.get("risk_score"), 0.0),
    )
    promotion["policy_preview"] = {**(intent.get("policy_preview") or {}), **promotion.get("policy_preview", {}), **cap_preview}
    if intent_id:
        update_order_intent(
            int(intent_id),
            {**promotion, "status": "READY_FOR_LIVE" if promotion["promotion_status"] in {"READY_FOR_LIMITED", "READY_FOR_LIVE"} else "BLOCKED"},
        )
    if promotion["promotion_status"] not in {"READY_FOR_LIMITED", "READY_FOR_LIVE"}:
        _insert_blocked_log(session, promotion["promotion_blockers"][0] if promotion["promotion_blockers"] else "SMART_PROMOTION_BLOCKED", "Smart Engine limited order blocked.", candle["candle_time_utc"], signal, order, risk_preview)
        update_live_strategy_session(int(session["id"]), {"last_risk_result": "SMART_PROMOTION_BLOCKED", "last_order_status": "BLOCKED"})
        return True
    insert_live_order_log(_log_payload(session, "PREVIEWED", "ALLOWED", candle["candle_time_utc"], signal, order, risk_preview))
    try:
        response = await broker.place_order(order)
        order_uuid = str(response.get("uuid") or response.get("order_id") or response.get("id") or "")
        update_live_order_log(
            request_id,
            {
                "status": "SUBMITTED",
                "risk_result": "ALLOWED",
                "order_uuid": order_uuid,
                "exchange_request_payload_masked": _smart_submitted_request_payload(order, price_preview, config),
                "exchange_response_payload": response,
            },
        )
        if intent_id:
            update_order_intent(int(intent_id), {"promotion_status": "SUBMITTED", "status": "SUBMITTED", "submitted_at": _utc_now()})
        update_live_strategy_session(
            int(session["id"]),
            {
                "status": "RUNNING",
                "orders_created_today": int(session.get("orders_created_today") or 0) + 1,
                "current_open_order_uuid": order_uuid,
                "last_order_time_utc": _utc_now(),
                "last_order_status": "SUBMITTED",
                "last_risk_result": "ALLOWED",
            },
        )
        return True
    except Exception as exc:
        update_live_order_log(request_id, {"status": "FAILED", "risk_result": "SMART_SUBMIT_FAILED", "error_message": str(exc)})
        if intent_id:
            update_order_intent(int(intent_id), {"promotion_status": "BLOCKED", "promotion_blockers": ["SMART_SUBMIT_FAILED"]})
        update_live_strategy_session(int(session["id"]), {"last_risk_result": "SMART_SUBMIT_FAILED", "last_order_status": "FAILED"})
        return True


async def _submit_smart_intent_sell_order(
    session: dict,
    candle: dict,
    signal: dict,
    live_config: LiveTradingConfig,
    smart_snapshot: dict,
    intent: dict,
) -> bool:
    intent_id = intent.get("id")
    exchange = str(session.get("exchange") or "bithumb")
    market = str(session.get("market") or "KRW-BTC")
    position = load_open_live_position(int(session["id"]), exchange, market) or load_open_live_position(None, exchange, market)
    if not position:
        if intent_id:
            update_order_intent(int(intent_id), {"promotion_status": "BLOCKED", "promotion_blockers": ["SMART_SELL_POSITION_MISSING"], "status": "BLOCKED"})
        _insert_blocked_log(session, "SMART_SELL_POSITION_MISSING", "Smart Engine limited sell blocked: no open bot position.", candle.get("candle_time_utc"), signal)
        return True
    broker = get_live_broker("bithumb")
    try:
        balances = await broker.get_balances()
        chance = await broker.get_order_chance(market)
    except Exception as exc:
        _insert_blocked_log(session, "SMART_ORDER_CHANCE_FAILED", str(exc), candle["candle_time_utc"], signal)
        if intent_id:
            update_order_intent(int(intent_id), {"promotion_status": "BLOCKED", "promotion_blockers": ["SMART_ORDER_CHANCE_FAILED"]})
        return True
    if not chance:
        _insert_blocked_log(session, "SMART_ORDER_CHANCE_FAILED", "Order chance failed.", candle["candle_time_utc"], signal)
        if intent_id:
            update_order_intent(int(intent_id), {"promotion_status": "BLOCKED", "promotion_blockers": ["SMART_ORDER_CHANCE_FAILED"]})
        return True
    current_price = float(candle["trade_price"])
    current_qty = max(_float(position.get("entry_volume")), 0.0)
    requested_qty = abs(_float(intent.get("target_qty"))) or (abs(_float(intent.get("delta_value_krw"))) / current_price if current_price > 0 else 0.0)
    volume = max(min(requested_qty, current_qty), 0.0)
    price = _round_krw_price(current_price)
    amount = volume * price
    request_id = f"smart-rehearsal-{uuid.uuid4().hex[:18]}"
    order = {
        "request_id": request_id,
        "client_order_id": request_id[:36],
        "exchange": "bithumb",
        "market": market,
        "side": "SELL",
        "ord_type": "limit",
        "order_type": "LIMIT",
        "price": price,
        "amount_krw": amount,
        "volume": volume,
        "order_purpose": "EXIT",
    }
    risk_preview = evaluate_live_order_risk(order=order, config=live_config, mode="AUTO_STRATEGY_RUNNING", balances=balances, request_exists=False, recent_duplicate=False, market_snapshot={"price": current_price}, is_auto=True)
    risk_preview = check_order_risk(
        order=order,
        purpose="EXIT",
        base_result=risk_preview,
        mode="AUTO_STRATEGY_RUNNING",
        session_id=int(session["id"]),
        position_id=int(position["id"]),
        candidate_strategy_id=None if _is_smart_autonomous_session(session) else int(session["candidate_strategy_id"]),
        candle_time_utc=candle["candle_time_utc"],
        signal=(signal or {}).get("signal"),
        market_snapshot={"price": current_price, "complete": True},
        balances=balances,
        is_auto=True,
    )
    try:
        recommendation = build_shadow_report(str(session.get("market") or "KRW-BTC"), limit=100).get("summary", {}).get("recommendation")
    except Exception:
        recommendation = None
    promotion = evaluate_promotion(
        intent={**intent, "delta_value_krw": amount, "target_qty": volume},
        snapshot=smart_snapshot,
        policy=load_bot_operation_policy(str(session.get("market") or "KRW-BTC")),
        risk_preview=risk_preview,
        shadow_recommendation=recommendation,
        available_krw=None,
        daily_smart_order_count=count_live_strategy_orders_today(str(session.get("exchange") or "bithumb"), str(session.get("market") or "KRW-BTC")),
        risk_score=_float(smart_snapshot.get("risk_score"), 0.0),
    )
    promotion["policy_preview"] = {**(intent.get("policy_preview") or {}), **promotion.get("policy_preview", {})}
    if intent_id:
        update_order_intent(int(intent_id), {**promotion, "status": "READY_FOR_LIVE" if promotion["promotion_status"] in {"READY_FOR_LIMITED", "READY_FOR_LIVE"} else "BLOCKED"})
    if promotion["promotion_status"] not in {"READY_FOR_LIMITED", "READY_FOR_LIVE"}:
        _insert_blocked_log(session, promotion["promotion_blockers"][0] if promotion["promotion_blockers"] else "SMART_PROMOTION_BLOCKED", "Smart Engine limited sell blocked.", candle["candle_time_utc"], signal, order, risk_preview)
        update_live_strategy_session(int(session["id"]), {"last_risk_result": "SMART_PROMOTION_BLOCKED", "last_order_status": "BLOCKED"})
        return True
    insert_live_order_log({**_log_payload(session, "PREVIEWED", "ALLOWED", candle["candle_time_utc"], signal, order, risk_preview), "position_id": position.get("id"), "order_purpose": "EXIT", "is_auto_exit": True})
    try:
        response = await broker.place_order(order)
        order_uuid = str(response.get("uuid") or response.get("order_id") or response.get("id") or "")
        update_live_order_log(request_id, {"status": "SUBMITTED", "risk_result": "ALLOWED", "order_uuid": order_uuid, "exchange_request_payload_masked": masked_exchange_request(order), "exchange_response_payload": response})
        if intent_id:
            update_order_intent(int(intent_id), {"promotion_status": "SUBMITTED", "status": "SUBMITTED", "submitted_at": _utc_now()})
        update_live_position(int(position["id"]), {"status": "CLOSING", "exit_order_uuid": order_uuid})
        update_live_strategy_session(int(session["id"]), {"last_order_status": "SUBMITTED", "last_risk_result": "SMART_SELL_SUBMITTED", "last_order_time_utc": _utc_now()})
        return True
    except Exception as exc:
        update_live_order_log(request_id, {"status": "FAILED", "risk_result": "SMART_SUBMIT_FAILED", "error_message": str(exc)})
        if intent_id:
            update_order_intent(int(intent_id), {"promotion_status": "BLOCKED", "promotion_blockers": ["SMART_SUBMIT_FAILED"]})
        update_live_strategy_session(int(session["id"]), {"last_risk_result": "SMART_SUBMIT_FAILED", "last_order_status": "FAILED"})
        return True


async def _submit_entry_order(session: dict, candidate: dict, candle: dict, signal: dict, config: LiveStrategyConfig, live_config: LiveTradingConfig) -> None:
    broker = get_live_broker("bithumb")
    market = str(session.get("market") or "KRW-BTC")
    try:
        balances = await broker.get_balances()
        chance = await broker.get_order_chance(market)
    except Exception as exc:
        _insert_blocked_log(session, "BLOCKED_ORDER_CHANCE_FAILED", str(exc), candle["candle_time_utc"], signal)
        update_live_strategy_session(int(session["id"]), {"last_risk_result": "BLOCKED_ORDER_CHANCE_FAILED", "last_order_status": "BLOCKED"})
        return
    if not chance:
        _insert_blocked_log(session, "BLOCKED_ORDER_CHANCE_FAILED", "Order chance failed.", candle["candle_time_utc"], signal)
        update_live_strategy_session(int(session["id"]), {"last_risk_result": "BLOCKED_ORDER_CHANCE_FAILED", "last_order_status": "BLOCKED"})
        return

    current_price = float(candle["trade_price"])
    range_rate = ((float(candle["high_price"]) - float(candle["low_price"])) / current_price) if current_price > 0 else 0.0
    price = _round_krw_price(current_price * (1 - config.entry_price_offset_percent / 100))
    amount = min(config.max_order_krw, live_config.max_live_order_krw)
    volume = amount / price if price > 0 else 0.0
    request_id = f"strategy-{uuid.uuid4().hex[:24]}"
    order = {
        "request_id": request_id,
        "client_order_id": request_id[:36],
        "exchange": "bithumb",
        "market": market,
        "side": "BUY",
        "ord_type": "limit",
        "order_type": "LIMIT",
        "price": price,
        "amount_krw": amount,
        "volume": volume,
    }
    risk = evaluate_live_order_risk(
        order=order,
        config=live_config,
        mode="AUTO_STRATEGY_RUNNING",
        balances=balances,
        request_exists=False,
        recent_duplicate=False,
        market_snapshot={"price": current_price, "range_rate": range_rate, "volume": float(candle["candle_acc_trade_volume"])},
        is_auto=True,
    )
    if amount > config.max_order_krw:
        risk["allowed"] = False
        risk["risk_result"] = "BLOCKED_MAX_ORDER_AMOUNT"
        risk["blocked_reason"] = "BLOCKED_MAX_ORDER_AMOUNT"
    liquidity_snapshot = await one_minute_liquidity_snapshot(market, require_completed=config.require_completed_candle)
    market_snapshot = {
        "price": current_price,
        "range_rate": range_rate,
        "volume": float(candle["candle_acc_trade_volume"]),
        "trade_price_volume": float(candle.get("candle_acc_trade_price") or 0.0),
        "complete": True,
        **liquidity_snapshot,
    }
    risk = check_order_risk(
        order=order,
        purpose="ENTRY",
        base_result=risk,
        mode="AUTO_STRATEGY_RUNNING",
        session_id=int(session["id"]),
        candidate_strategy_id=int(session["candidate_strategy_id"]),
        candle_time_utc=candle["candle_time_utc"],
        signal=(signal or {}).get("signal"),
        market_snapshot=market_snapshot,
        balances=balances,
        is_auto=True,
    )
    if not risk["allowed"]:
        _insert_blocked_log(session, str(risk["risk_result"]), risk.get("blocked_reason"), candle["candle_time_utc"], signal, order, risk)
        update_live_strategy_session(int(session["id"]), {"last_risk_result": str(risk["risk_result"]), "last_order_status": "BLOCKED"})
        return
    if _available_balance(balances, "KRW") < amount + risk["fee_estimate"]:
        _insert_blocked_log(session, "BLOCKED_INSUFFICIENT_BALANCE", "Insufficient KRW balance.", candle["candle_time_utc"], signal, order, risk)
        update_live_strategy_session(int(session["id"]), {"last_risk_result": "BLOCKED_INSUFFICIENT_BALANCE", "last_order_status": "BLOCKED"})
        return

    insert_live_order_log(_log_payload(session, "PREVIEWED", "ALLOWED", candle["candle_time_utc"], signal, order, risk))
    try:
        response = await broker.place_order(order)
        order_uuid = str(response.get("uuid") or response.get("order_id") or response.get("id") or "")
        update_live_order_log(
            request_id,
            {
                "status": "SUBMITTED",
                "risk_result": "ALLOWED",
                "order_uuid": order_uuid,
                "exchange_request_payload_masked": masked_exchange_request(order),
                "exchange_response_payload": response,
            },
        )
        _insert_order_status_event(request_id, order_uuid, "SUBMITTED", response)
        update_live_strategy_session(
            int(session["id"]),
            {
                "status": "RUNNING",
                "orders_created_today": int(session.get("orders_created_today") or 0) + 1,
                "current_open_order_uuid": order_uuid,
                "last_order_time_utc": _utc_now(),
                "last_order_status": "SUBMITTED",
                "last_risk_result": "ALLOWED",
            },
        )
        latest_log = get_live_order_log(request_id)
        if latest_log is not None and order_uuid:
            reconciled = await reconcile_order_log(latest_log, source="POST_SUBMIT_STATUS_RECHECK")
            if reconciled:
                updates: dict[str, Any] = {"last_order_status": reconciled.status}
                if reconciled.status == "FILLED":
                    position_id = _create_position_from_order(session, reconciled.raw, config)
                    updates.update(
                        {
                            "status": "RUNNING",
                            "auto_enabled": True,
                            "current_open_order_uuid": None,
                            "current_position_id": position_id,
                            "last_risk_result": "POSITION_OPEN_SYNCED",
                        }
                    )
                elif reconciled.status == "PARTIALLY_FILLED":
                    updates.update({"status": "PAUSED", "auto_enabled": False, "last_risk_result": "BLOCKED_PARTIAL_FILL_REQUIRES_RECOVERY"})
                update_live_strategy_session(int(session["id"]), updates)
    except Exception as exc:
        if is_timeout_exception(exc):
            update_live_order_log(
                request_id,
                {
                    "status": "SUBMITTED",
                    "risk_result": "ORDER_STATUS_UNKNOWN_TIMEOUT",
                    "exchange_request_payload_masked": masked_exchange_request(order),
                    "error_message": "Exchange request timed out; order status must be reconciled before any retry.",
                },
            )
            update_live_strategy_session(
                int(session["id"]),
                {
                    "status": "PAUSED",
                    "auto_enabled": False,
                    "last_order_status": "ORDER_STATUS_UNKNOWN_TIMEOUT",
                    "last_risk_result": "ORDER_STATUS_UNKNOWN_TIMEOUT",
                },
            )
            log_recovery_event(
                "ORDER_STATUS_UNKNOWN_TIMEOUT",
                "ERROR",
                "Live strategy order timed out. Re-ordering is blocked until reconciliation.",
                session_id=int(session["id"]),
                request_id=request_id,
                payload={"market": "KRW-BTC", "candle_time_utc": candle["candle_time_utc"]},
            )
            return
        update_live_order_log(request_id, {"status": "FAILED", "risk_result": "BLOCKED_API_RESPONSE_ERROR", "error_message": str(exc)})
        update_live_strategy_session(int(session["id"]), {"status": "ERROR", "last_order_status": "FAILED", "last_risk_result": "BLOCKED_API_RESPONSE_ERROR"})


def _sync_live_strategy_order_status(session: dict, config: LiveStrategyConfig) -> dict:
    try:
        return asyncio.run(_sync_live_strategy_order_status_async(session, config))
    except RuntimeError:
        return session
    except Exception as exc:
        logger.warning("[live-strategy] order sync failed session_id=%s error=%s", session.get("id"), exc)
        return session


async def _sync_live_strategy_order_status_async(session: dict, config: LiveStrategyConfig) -> dict:
    order_uuid = str(session.get("current_open_order_uuid") or "")
    if not order_uuid:
        return session
    try:
        status = await BithumbBroker().get_order(order_uuid)
    except Exception as exc:
        logger.warning("[live-strategy] order status fetch failed order_uuid=%s error=%s", order_uuid, exc)
        return session

    state = str(status.get("state") or status.get("status") or "").lower()
    reconciled = normalize_exchange_order(status)
    executed_volume = reconciled.executed_volume
    remaining_volume = reconciled.remaining_volume
    session_id = int(session["id"])

    if reconciled.status == "PARTIALLY_FILLED":
        _update_order_by_uuid(order_uuid, "PARTIALLY_FILLED", status)
        update_live_strategy_session(
            session_id,
            {"status": "PAUSED", "last_order_status": "PARTIALLY_FILLED", "last_risk_result": "BLOCKED_PARTIAL_FILL_UNSUPPORTED"},
        )
        return load_latest_live_strategy_session() or session

    if state in {"done", "filled"} or (executed_volume > 0 and remaining_volume <= 0):
        _update_order_by_uuid(order_uuid, "FILLED", status)
        open_position = load_open_live_position(session_id, session.get("exchange", config.allowed_exchange), session.get("market", config.allowed_market))
        position_id = int(open_position["id"]) if open_position else _create_position_from_order(session, status, config)
        update_live_strategy_session(
            session_id,
            {
                "status": "RUNNING",
                "auto_enabled": True,
                "current_open_order_uuid": None,
                "current_position_id": position_id,
                "last_order_status": "FILLED",
                "last_risk_result": "POSITION_OPEN_SYNCED",
            },
        )
        return load_latest_live_strategy_session() or session

    if state in {"cancel", "canceled", "cancelled"}:
        _update_order_by_uuid(order_uuid, "CANCELED", status)
        update_live_strategy_session(
            session_id,
            {
                "auto_enabled": False,
                "current_open_order_uuid": None,
                "last_order_status": "CANCELED",
                "last_risk_result": "ORDER_CANCELED_SYNCED",
            },
        )
        return load_latest_live_strategy_session() or session

    if state in {"wait", "waiting"}:
        _update_order_by_uuid(order_uuid, "WAITING", status)
        update_live_strategy_session(session_id, {"last_order_status": "WAITING", "last_risk_result": "WAITING_SYNCED"})
        return load_latest_live_strategy_session() or session

    return session


async def _manage_open_order(session: dict, config: LiveStrategyConfig) -> None:
    order_uuid = str(session["current_open_order_uuid"])
    broker = BithumbBroker()
    try:
        status = await broker.get_order(order_uuid)
        state = str(status.get("state") or status.get("status") or "").lower()
        reconciled = normalize_exchange_order(status)
        executed_volume = reconciled.executed_volume
        remaining_volume = reconciled.remaining_volume
        if reconciled.status == "PARTIALLY_FILLED":
            _update_order_by_uuid(order_uuid, "PARTIALLY_FILLED", status)
            try:
                cancel_response = await broker.cancel_order(order_uuid)
                _update_order_by_uuid(order_uuid, "PARTIALLY_FILLED", {**status, "cancel_remaining_response": cancel_response})
            except Exception as cancel_exc:
                log_recovery_event(
                    "PARTIAL_FILL_CANCEL_FAILED",
                    "ERROR",
                    "Live strategy partial fill residual cancel failed.",
                    session_id=int(session["id"]),
                    order_uuid=order_uuid,
                    payload={"error": str(cancel_exc)},
                )
            update_live_strategy_session(
                int(session["id"]),
                {"status": "PAUSED", "auto_enabled": False, "last_order_status": "PARTIALLY_FILLED", "last_risk_result": "BLOCKED_PARTIAL_FILL_REQUIRES_RECOVERY"},
            )
            return
        if reconciled.status == "FILLED" or state in {"done", "filled"} or (executed_volume > 0 and remaining_volume <= 0):
            _update_order_by_uuid(order_uuid, "FILLED", status)
            position_id = _create_position_from_order(session, status, config)
            update_live_strategy_session(
                int(session["id"]),
                {
                    "status": "RUNNING",
                    "current_open_order_uuid": None,
                    "current_position_id": position_id,
                    "last_order_status": "FILLED",
                    "last_risk_result": "POSITION_OPEN",
                },
            )
            return
        if _seconds_since(str(session.get("last_order_time_utc") or _utc_now())) >= config.cancel_unfilled_after_seconds:
            try:
                cancel_response = await broker.cancel_order(order_uuid)
            except Exception as cancel_exc:
                log_recovery_event(
                    "ORDER_CANCEL_FAILED",
                    "ERROR",
                    "Live strategy unfilled order cancel failed.",
                    session_id=int(session["id"]),
                    order_uuid=order_uuid,
                    payload={"error": str(cancel_exc)},
                )
                update_live_strategy_session(int(session["id"]), {"status": "PAUSED", "auto_enabled": False, "last_order_status": "CANCEL_FAILED", "last_risk_result": "ORDER_CANCEL_FAILED"})
                return
            _update_order_by_uuid(order_uuid, "CANCELED", {**cancel_response, "cancel_reason": "AUTO_CANCEL_UNFILLED_TIMEOUT"})
            update_live_strategy_session(
                int(session["id"]),
                {
                    "status": "RUNNING",
                    "auto_enabled": True,
                    "current_open_order_uuid": None,
                    "last_order_status": "CANCELED",
                    "last_risk_result": "AUTO_CANCELED_UNFILLED",
                },
            )
            return
        _update_order_by_uuid(order_uuid, "WAITING", status)
        update_live_strategy_session(int(session["id"]), {"status": "RUNNING", "last_order_status": "WAITING", "last_risk_result": "WAITING"})
    except Exception as exc:
        log_recovery_event(
            "API_ERROR",
            "ERROR",
            "Live strategy order status reconciliation failed.",
            session_id=int(session["id"]),
            order_uuid=order_uuid,
            payload={"error": str(exc)},
        )
        update_live_strategy_session(int(session["id"]), {"status": "PAUSED", "auto_enabled": False, "last_order_status": "RECONCILIATION_FAILED", "last_risk_result": "BLOCKED_API_RESPONSE_ERROR"})


async def _handle_emergency(session: dict) -> None:
    order_uuid = session.get("current_open_order_uuid")
    if order_uuid:
        try:
            response = await BithumbBroker().cancel_order(str(order_uuid))
            _update_order_by_uuid(str(order_uuid), "CANCELED", response)
        except Exception as exc:
            _update_order_by_uuid(str(order_uuid), "FAILED", {"error": str(exc)})
    position = load_open_live_position(int(session["id"]), session.get("exchange", "bithumb"), session.get("market", "KRW-BTC"))
    if position:
        update_live_position(int(position["id"]), {"status": "MANUAL_REVIEW_REQUIRED"})
    update_live_strategy_session(
        int(session["id"]),
        {
            "status": "EMERGENCY_STOPPED",
            "auto_enabled": False,
            "current_open_order_uuid": None,
            "last_risk_result": "BLOCKED_EMERGENCY_STOP",
            "last_order_status": "BLOCKED",
            "stopped_at": _utc_now(),
        },
    )


def _create_position_from_order(session: dict, order_status: dict, config: LiveStrategyConfig) -> int:
    entry_order_uuid = str(order_status.get("uuid") or order_status.get("order_id") or order_status.get("id") or session.get("current_open_order_uuid") or "")
    if entry_order_uuid:
        existing = load_live_position_by_entry_order_uuid(session["exchange"], session["market"], entry_order_uuid)
        if existing:
            _attach_position_to_order_log(entry_order_uuid, int(existing["id"]))
            return int(existing["id"])
    entry_price = _float(order_status.get("price"))
    entry_volume = _float(order_status.get("executed_volume")) or _float(order_status.get("volume"))
    entry_amount = entry_price * entry_volume
    stop_loss_price = entry_price * (1 - config.stop_loss_percent / 100) if entry_price > 0 else 0.0
    take_profit_price = entry_price * (1 + config.take_profit_percent / 100) if entry_price > 0 else 0.0
    position_id = create_live_position(
        {
            "session_id": session["id"],
            "exchange": session["exchange"],
            "market": session["market"],
            "candidate_strategy_id": session["candidate_strategy_id"],
            "strategy_name": session["strategy_name"],
            "status": "OPEN",
            "entry_order_uuid": entry_order_uuid,
            "entry_price": entry_price,
            "entry_volume": entry_volume,
            "entry_amount_krw": entry_amount,
            "current_price": entry_price,
            "unrealized_pnl": 0.0,
            "realized_pnl": 0.0,
            "stop_loss_price": stop_loss_price,
            "take_profit_price": take_profit_price,
            "opened_at": _utc_now(),
        }
    )
    if entry_order_uuid:
        _attach_position_to_order_log(entry_order_uuid, position_id)
    return position_id


def _attach_position_to_order_log(order_uuid: str, position_id: int) -> None:
    log = get_live_order_log_by_uuid(order_uuid)
    if not log:
        return
    if str(log.get("order_purpose") or "ENTRY").upper() != "ENTRY":
        return
    if log.get("position_id"):
        return
    update_live_order_log(str(log["request_id"]), {"position_id": position_id})


def _insert_blocked_log(session: dict, risk_result: str, message: str | None, candle_time_utc: str | None, signal: dict | None, order: dict | None = None, preview: dict | None = None) -> None:
    payload = order or {
        "request_id": f"strategy-blocked-{uuid.uuid4().hex[:18]}",
        "exchange": session.get("exchange", "bithumb"),
        "market": session.get("market", "KRW-BTC"),
        "side": "BUY",
        "order_type": "LIMIT",
        "price": 0.0,
        "volume": 0.0,
        "amount_krw": session.get("max_order_krw", 0.0),
    }
    insert_live_order_log(_log_payload(session, "BLOCKED", risk_result, candle_time_utc, signal, payload, preview or {"risk_result": risk_result, "fee_estimate": 0.0}, message))


def _log_payload(session: dict, status: str, risk_result: str, candle_time_utc: str | None, signal: dict | None, order: dict, preview: dict | None, error_message: str | None = None) -> dict:
    return {
        "request_id": order["request_id"],
        "session_id": session.get("id"),
        "candidate_strategy_id": session.get("candidate_strategy_id"),
        "exchange": order.get("exchange", session.get("exchange", "bithumb")),
        "market": order.get("market", session.get("market", "KRW-BTC")),
        "side": order.get("side", "BUY"),
        "order_type": order.get("order_type", "LIMIT"),
        "price": order.get("price"),
        "volume": order.get("volume"),
        "amount_krw": order.get("amount_krw"),
        "fee_estimate": (preview or {}).get("fee_estimate", 0.0),
        "risk_result": risk_result,
        "order_preview_payload": preview or {},
        "exchange_request_payload_masked": {},
        "exchange_response_payload": {},
        "status": status,
        "error_message": error_message or (None if risk_result == "ALLOWED" else risk_result),
        "strategy_name": session.get("strategy_name"),
        "signal_reason": (signal or {}).get("reason"),
        "candle_time_utc": candle_time_utc,
        "order_purpose": order.get("order_purpose", "ENTRY"),
    }


def _update_order_by_uuid(order_uuid: str, status: str, response: dict) -> None:
    logs = load_live_order_logs(300, include_canonical_with_events=True)
    request_id = None
    order_log = None
    for item in logs:
        request_id_value = str(item.get("request_id", ""))
        if item.get("order_uuid") == order_uuid and not _is_strategy_order_event_request(request_id_value):
            request_id = item["request_id"]
            order_log = item
            break
    if request_id is None:
        return
    reconciled = normalize_exchange_order(response)
    exchange_request_payload_masked = dict((order_log or {}).get("exchange_request_payload_masked") or {})
    if status == "CANCELED":
        if response.get("cancel_reason"):
            cancel_reason = str(response["cancel_reason"])
        elif reconciled.executed_volume > 0 and reconciled.remaining_volume > 0:
            cancel_reason = "PARTIAL_FILL_REMAINDER_CANCELED"
        elif str(response.get("state") or "").lower() in {"cancel", "canceled", "cancelled"}:
            cancel_reason = "AUTO_CANCEL_UNFILLED_TIMEOUT"
        else:
            cancel_reason = "EXCHANGE_CANCELED"
        exchange_request_payload_masked.update(
            {
                "executed_volume": reconciled.executed_volume,
                "remaining_volume": reconciled.remaining_volume,
                "cancel_reason": cancel_reason,
            }
        )
    update_live_order_log(
        request_id,
        {
            "status": status,
            "exchange_request_payload_masked": exchange_request_payload_masked,
            "exchange_response_payload": response,
            "order_uuid": order_uuid,
            "executed_volume": reconciled.executed_volume,
            "remaining_volume": reconciled.remaining_volume,
            "filled_amount_krw": reconciled.filled_amount_krw,
            "paid_fee": reconciled.paid_fee,
        },
    )
    if status in {"WAITING", "PARTIALLY_FILLED", "CANCELED", "FILLED", "FAILED"}:
        _insert_order_status_event(request_id, order_uuid, status, response)


def _insert_order_status_event(request_id: str, order_uuid: str, status: str, response: dict) -> None:
    current = get_live_order_log(request_id)
    if current is None or _has_recent_status_event(order_uuid, status):
        return
    event_payload = {
        **current,
        "request_id": f"{request_id}-{status.lower()}-{uuid.uuid4().hex[:8]}",
        "status": status,
        "exchange_response_payload": response,
        "order_uuid": order_uuid,
        "error_message": current.get("error_message") if status == "FAILED" else None,
    }
    insert_live_order_log(event_payload)


def _is_strategy_order_event_request(request_id: str) -> bool:
    return (
        "-submitted-" in request_id
        or "-waiting-" in request_id
        or "-partial" in request_id
        or "-canceled-" in request_id
        or "-filled-" in request_id
        or "-failed-" in request_id
    )


def _has_recent_status_event(order_uuid: str, status: str) -> bool:
    for item in load_live_order_logs(50, include_canonical_with_events=True):
        if item.get("order_uuid") == order_uuid and item.get("status") == status and _is_strategy_order_event_request(str(item.get("request_id", ""))):
            return True
    return False


def _should_stop_on_block(risk_result: str) -> bool:
    return risk_result in {
        "BLOCKED_LIVE_DISABLED",
        "BLOCKED_AUTO_DISABLED",
        "BLOCKED_AUTO_STRATEGY_DISABLED",
        "BLOCKED_EXCHANGE_NOT_ALLOWED",
        "BLOCKED_MARKET_NOT_ALLOWED",
        "BLOCKED_ORDER_TYPE_NOT_ALLOWED",
        "BLOCKED_MARKET_ORDER_DISABLED",
        "BLOCKED_ORDER_CHANCE_FAILED",
    }


def _latest_signal(candidate: dict, candles: list[dict], candle_time_utc: str) -> dict:
    context = [candle for candle in candles if candle["candle_time_utc"] <= candle_time_utc]
    frame = candles_to_frame(context)
    signal_frame = apply_strategy(candidate["strategy"], frame, candidate.get("parameters", {}))
    last = signal_frame.iloc[-1]
    return {"signal": str(last["signal"]), "reason": str(last["reason"]), "price": float(last["close"])}


def _round_krw_price(price: float) -> float:
    if price >= 1_000_000:
        return float(int(price // 1000) * 1000)
    if price >= 100_000:
        return float(int(price // 100) * 100)
    if price >= 10_000:
        return float(int(price // 10) * 10)
    return float(int(price))


def _mode(session: dict | None) -> str:
    if is_emergency_stopped():
        return "EMERGENCY_STOPPED"
    if session and session.get("status") == "RUNNING":
        return "AUTO_STRATEGY_RUNNING"
    if session and session.get("status") in {"READY", "PAUSED"}:
        return f"AUTO_STRATEGY_{session['status']}"
    return current_live_mode()


def _seconds_since(timestamp_utc: str) -> float:
    normalized = timestamp_utc.replace("Z", "+00:00")
    return (datetime.now(timezone.utc) - datetime.fromisoformat(normalized)).total_seconds()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
