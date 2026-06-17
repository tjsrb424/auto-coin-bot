from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from app.backtest import candles_to_frame
from app.database import (
    count_live_strategy_orders_today,
    create_live_position,
    create_live_strategy_session,
    get_live_order_log,
    has_live_strategy_order_for_signal,
    has_open_live_strategy_order,
    insert_candles,
    insert_live_order_log,
    insert_live_signal_log,
    load_candidate_strategy,
    load_candles,
    load_latest_live_strategy_session,
    load_live_order_logs,
    load_open_live_position,
    load_running_live_strategy_sessions,
    update_live_order_log,
    update_live_position,
    update_live_strategy_session,
)
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
)
from app.live_exit import (
    LiveExitConfig,
    create_exit_candidate_for_position,
    live_exit_status,
    manage_exit_order_timeout,
    maybe_create_price_exit_candidate,
)
from app.risk_manager import check_order_risk
from app.strategies import apply_strategy
from app.upbit import fetch_minute_candles

logger = logging.getLogger("uvicorn.error")
_strategy_tick_lock = Lock()


@dataclass(frozen=True)
class LiveStrategyConfig:
    exchange: str
    live_auto_trading_enabled: bool
    auto_strategy_pilot_enabled: bool
    allowed_exchange: str
    allowed_market: str
    allowed_order_type: str
    max_order_krw: float
    max_orders_per_day: int
    max_open_position_count: int
    cooldown_seconds: int
    require_completed_candle: bool
    cancel_unfilled_after_seconds: int
    entry_price_offset_percent: float
    stop_loss_percent: float
    take_profit_percent: float
    max_hold_minutes: int
    exit_enabled: bool
    market_order_enabled: bool

    @classmethod
    def from_env(cls) -> "LiveStrategyConfig":
        live_feature_allowed = os.getenv("APP_ENV", "development").lower() == "production" or os.getenv("ALLOW_DEV_LIVE_TRADING", "false").lower() == "true"
        return cls(
            exchange=os.getenv("EXCHANGE", os.getenv("AUTO_ALLOWED_EXCHANGE", "bithumb")).strip().lower(),
            live_auto_trading_enabled=live_feature_allowed and os.getenv("LIVE_AUTO_TRADING_ENABLED", "false").lower() == "true",
            auto_strategy_pilot_enabled=live_feature_allowed and os.getenv("AUTO_STRATEGY_PILOT_ENABLED", "false").lower() == "true",
            allowed_exchange=os.getenv("AUTO_ALLOWED_EXCHANGE", "bithumb").strip().lower(),
            allowed_market=os.getenv("AUTO_ALLOWED_MARKET", "KRW-BTC"),
            allowed_order_type=os.getenv("AUTO_ALLOWED_ORDER_TYPE", os.getenv("AUTO_ORDER_TYPE", "limit")).strip().lower(),
            max_order_krw=float(os.getenv("AUTO_MAX_ORDER_KRW", "10000")),
            max_orders_per_day=int(os.getenv("AUTO_MAX_ORDERS_PER_DAY", "3")),
            max_open_position_count=int(os.getenv("AUTO_MAX_OPEN_POSITION_COUNT", "1")),
            cooldown_seconds=int(os.getenv("AUTO_COOLDOWN_SECONDS", "1800")),
            require_completed_candle=os.getenv("AUTO_REQUIRE_COMPLETED_CANDLE", "true").lower() == "true",
            cancel_unfilled_after_seconds=int(os.getenv("AUTO_CANCEL_UNFILLED_AFTER_SECONDS", os.getenv("AUTO_CANCEL_AFTER_SECONDS", "60"))),
            entry_price_offset_percent=float(os.getenv("AUTO_ENTRY_PRICE_OFFSET_PERCENT", os.getenv("AUTO_BUY_PRICE_OFFSET_PERCENT", "0.3"))),
            stop_loss_percent=float(os.getenv("AUTO_STOP_LOSS_PERCENT", "0.7")),
            take_profit_percent=float(os.getenv("AUTO_TAKE_PROFIT_PERCENT", "1.0")),
            max_hold_minutes=int(os.getenv("AUTO_MAX_HOLD_MINUTES", "60")),
            exit_enabled=os.getenv("AUTO_EXIT_ENABLED", "false").lower() == "true",
            market_order_enabled=os.getenv("AUTO_MARKET_ORDER_ENABLED", "false").lower() == "true",
        )


def live_strategy_status() -> dict:
    config = LiveStrategyConfig.from_env()
    live_config = LiveTradingConfig.for_exchange(config.allowed_exchange)
    session = load_latest_live_strategy_session()
    if session and session.get("current_open_order_uuid"):
        session = _sync_live_strategy_order_status(session, config)
    open_position = load_open_live_position(
        int(session["id"]) if session else None,
        config.allowed_exchange,
        config.allowed_market,
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
        "market": config.allowed_market,
        "current_mode": _mode(session),
        "live_trading_enabled": live_config.live_trading_enabled,
        "live_auto_trading_enabled": config.live_auto_trading_enabled,
        "auto_strategy_pilot_enabled": config.auto_strategy_pilot_enabled,
        "emergency_stop": is_emergency_stopped(),
        "api_key_loaded": live_config.api_key_loaded,
        "max_order_krw": config.max_order_krw,
        "max_orders_per_day": config.max_orders_per_day,
        "max_open_position_count": config.max_open_position_count,
        "cancel_unfilled_after_seconds": config.cancel_unfilled_after_seconds,
        "entry_price_offset_percent": config.entry_price_offset_percent,
        "exit_enabled": config.exit_enabled,
        "market_order_enabled": config.market_order_enabled,
        "partial_fill_policy": "PAUSE_AND_CANCEL_REMAINDER",
        "restart_policy": "RUNNING_SESSIONS_START_AS_LIVE_PAUSED",
        "recent_recovery_events": recent_recovery_events(10),
    }


def start_live_strategy_pilot(*, candidate_strategy_id: int, confirmation: str, order_confirmation: str) -> dict:
    if confirmation != "AUTO STRATEGY ENABLE":
        return {"ok": False, "message": "AUTO STRATEGY ENABLE confirmation is required.", **live_strategy_status()}
    if order_confirmation != "PLACE AUTO LIVE ORDER":
        return {"ok": False, "message": "PLACE AUTO LIVE ORDER confirmation is required.", **live_strategy_status()}
    config = LiveStrategyConfig.from_env()
    candidate = load_candidate_strategy(candidate_strategy_id)
    if candidate is None:
        return {"ok": False, "message": "Candidate strategy not found.", **live_strategy_status()}
    if candidate["market"] != config.allowed_market:
        return {"ok": False, "message": "Only KRW-BTC candidate strategies are allowed.", **live_strategy_status()}
    if config.allowed_exchange != "bithumb":
        return {"ok": False, "message": "AUTO_ALLOWED_EXCHANGE=bithumb 설정이 필요합니다.", **live_strategy_status()}
    session_id = create_live_strategy_session(
        {
            "exchange": config.allowed_exchange,
            "market": config.allowed_market,
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
    for session in load_running_live_strategy_sessions():
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

    if is_emergency_stopped():
        await _handle_emergency(session)
        return

    if session.get("current_open_order_uuid"):
        await _manage_open_order(session, config)
        return

    position = load_open_live_position(int(session["id"]), config.allowed_exchange, config.allowed_market)
    if position:
        await _process_open_position(session, position, config)
        return

    blocked = await _precheck_block_reason(session, config, live_config)
    if blocked:
        _insert_blocked_log(session, blocked, blocked, None, None)
        updates = {"last_risk_result": blocked, "last_order_status": "BLOCKED"}
        if _should_stop_on_block(blocked):
            updates.update({"status": "STOPPED", "auto_enabled": False, "stopped_at": _utc_now()})
        else:
            updates["status"] = "RUNNING"
        update_live_strategy_session(int(session["id"]), updates)
        return

    candidate = load_candidate_strategy(int(session["candidate_strategy_id"]))
    if candidate is None:
        _insert_blocked_log(session, "BLOCKED_DUPLICATE_SIGNAL", "Candidate strategy not found.", None, None)
        update_live_strategy_session(int(session["id"]), {"status": "ERROR", "last_risk_result": "BLOCKED_DUPLICATE_SIGNAL"})
        return

    fresh = await fetch_minute_candles(market=config.allowed_market, unit=int(candidate["unit"]), count=300)
    insert_candles(fresh)
    candles = load_candles(config.allowed_market, int(candidate["unit"]), 300)
    latest = latest_completed_candle(candles, int(candidate["unit"])) if config.require_completed_candle else (candles[-1] if candles else None)
    if latest is None:
        return

    candle_time = latest["candle_time_utc"]
    signal = _latest_signal(candidate, candles, candle_time)
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

    if signal["signal"] == "SELL":
        position = load_open_live_position(int(session["id"]), config.allowed_exchange, config.allowed_market)
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


async def _process_open_position(session: dict, position: dict, config: LiveStrategyConfig) -> None:
    await manage_exit_order_timeout(position, LiveExitConfig.from_env())
    candidate = load_candidate_strategy(int(session["candidate_strategy_id"]))
    if candidate is None:
        update_live_strategy_session(int(session["id"]), {"last_risk_result": "BLOCKED_DUPLICATE_SIGNAL"})
        return

    fresh = await fetch_minute_candles(market=config.allowed_market, unit=int(candidate["unit"]), count=300)
    insert_candles(fresh)
    candles = load_candles(config.allowed_market, int(candidate["unit"]), 300)
    latest = latest_completed_candle(candles, int(candidate["unit"])) if config.require_completed_candle else (candles[-1] if candles else None)
    if latest is None:
        return
    candle_time = latest["candle_time_utc"]
    current_price = float(latest["trade_price"])
    exit_candidate = maybe_create_price_exit_candidate(position, current_price, candle_time)
    signal = _latest_signal(candidate, candles, candle_time)
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
    if signal["signal"] == "SELL" and exit_candidate is None:
        exit_candidate = create_exit_candidate_for_position(position, "STRATEGY_SELL", current_price, candle_time)
    update_live_strategy_session(
        int(session["id"]),
        {
            "status": "PAUSED" if exit_candidate else "RUNNING",
            "last_signal": signal["signal"],
            "last_signal_time_utc": candle_time,
            "last_processed_candle_time_utc": candle_time,
            "last_risk_result": str(exit_candidate["reason"]) + "_EXIT_CANDIDATE" if exit_candidate else "POSITION_OPEN",
        },
    )


async def _precheck_block_reason(session: dict, config: LiveStrategyConfig, live_config: LiveTradingConfig) -> str | None:
    if is_emergency_stopped():
        return "BLOCKED_EMERGENCY_STOP"
    if not live_config.live_trading_enabled:
        return "BLOCKED_LIVE_DISABLED"
    if not config.live_auto_trading_enabled:
        return "BLOCKED_AUTO_DISABLED"
    if not config.auto_strategy_pilot_enabled:
        return "BLOCKED_AUTO_STRATEGY_DISABLED"
    if config.allowed_exchange != "bithumb" or session["exchange"] != "bithumb":
        return "BLOCKED_EXCHANGE_NOT_ALLOWED"
    if config.allowed_market != "KRW-BTC" or session["market"] != "KRW-BTC":
        return "BLOCKED_MARKET_NOT_ALLOWED"
    if config.allowed_order_type != "limit":
        return "BLOCKED_ORDER_TYPE_NOT_ALLOWED"
    if config.market_order_enabled:
        return "BLOCKED_MARKET_ORDER_DISABLED"
    if not live_config.api_key_loaded:
        return "BLOCKED_ORDER_CHANCE_FAILED"
    if count_live_strategy_orders_today("bithumb", "KRW-BTC") >= config.max_orders_per_day:
        return "BLOCKED_MAX_ORDERS_PER_DAY"
    if has_open_live_strategy_order("bithumb", "KRW-BTC"):
        return "BLOCKED_OPEN_ORDER_EXISTS"
    if load_open_live_position(None, "bithumb", "KRW-BTC"):
        return "BLOCKED_OPEN_POSITION_EXISTS"
    recovery_block = await auto_order_recovery_block_reason("bithumb", "KRW-BTC")
    if recovery_block:
        return recovery_block
    last_order_time = session.get("last_order_time_utc")
    if last_order_time and _seconds_since(str(last_order_time)) < config.cooldown_seconds:
        return "BLOCKED_COOLDOWN"
    return None


async def _submit_entry_order(session: dict, candidate: dict, candle: dict, signal: dict, config: LiveStrategyConfig, live_config: LiveTradingConfig) -> None:
    broker = get_live_broker("bithumb")
    try:
        balances = await broker.get_balances()
        chance = await broker.get_order_chance("KRW-BTC")
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
        "market": "KRW-BTC",
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
        mode="LIVE_MANUAL_ONLY",
        balances=balances,
        request_exists=False,
        recent_duplicate=False,
        market_snapshot={"price": current_price, "range_rate": range_rate, "volume": float(candle["candle_acc_trade_volume"])},
    )
    if amount > config.max_order_krw:
        risk["allowed"] = False
        risk["risk_result"] = "BLOCKED_MAX_ORDER_AMOUNT"
        risk["blocked_reason"] = "BLOCKED_MAX_ORDER_AMOUNT"
    risk = check_order_risk(
        order=order,
        purpose="ENTRY",
        base_result=risk,
        mode="AUTO_STRATEGY_RUNNING",
        session_id=int(session["id"]),
        candidate_strategy_id=int(session["candidate_strategy_id"]),
        candle_time_utc=candle["candle_time_utc"],
        signal=(signal or {}).get("signal"),
        market_snapshot={
            "price": current_price,
            "range_rate": range_rate,
            "volume": float(candle["candle_acc_trade_volume"]),
            "trade_price_volume": float(candle.get("candle_acc_trade_price") or 0.0),
            "complete": True,
        },
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
            _update_order_by_uuid(order_uuid, "CANCELED", cancel_response)
            update_live_strategy_session(
                int(session["id"]),
                {
                    "status": "STOPPED",
                    "auto_enabled": False,
                    "current_open_order_uuid": None,
                    "last_order_status": "CANCELED",
                    "last_risk_result": "AUTO_CANCELED_UNFILLED",
                    "stopped_at": _utc_now(),
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
    entry_price = _float(order_status.get("price"))
    entry_volume = _float(order_status.get("executed_volume")) or _float(order_status.get("volume"))
    entry_amount = entry_price * entry_volume
    stop_loss_price = entry_price * (1 - config.stop_loss_percent / 100) if entry_price > 0 else 0.0
    take_profit_price = entry_price * (1 + config.take_profit_percent / 100) if entry_price > 0 else 0.0
    return create_live_position(
        {
            "session_id": session["id"],
            "exchange": session["exchange"],
            "market": session["market"],
            "candidate_strategy_id": session["candidate_strategy_id"],
            "strategy_name": session["strategy_name"],
            "status": "OPEN",
            "entry_order_uuid": order_status.get("uuid") or session.get("current_open_order_uuid"),
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
        "side": "BUY",
        "order_type": "LIMIT",
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
    }


def _update_order_by_uuid(order_uuid: str, status: str, response: dict) -> None:
    logs = load_live_order_logs(300, include_canonical_with_events=True)
    request_id = None
    for item in logs:
        if item.get("order_uuid") == order_uuid and str(item.get("request_id", "")).startswith("strategy-") and not _is_strategy_order_event_request(str(item.get("request_id", ""))):
            request_id = item["request_id"]
            break
    if request_id is None:
        return
    reconciled = normalize_exchange_order(response)
    update_live_order_log(
        request_id,
        {
            "status": status,
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
