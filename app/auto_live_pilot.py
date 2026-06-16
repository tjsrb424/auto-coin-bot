from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

from app.backtest import candles_to_frame
from app.database import (
    count_auto_live_orders_today,
    create_auto_live_pilot_session,
    get_live_order_log,
    has_live_order_for_candle,
    has_open_auto_live_order,
    insert_candles,
    insert_live_order_log,
    load_candidate_strategy,
    load_candles,
    load_latest_auto_live_pilot_session,
    load_running_auto_live_pilot_sessions,
    update_auto_live_pilot_session,
    update_live_order_log,
)
from app.forward_paper import latest_completed_candle
from app.live_broker import BithumbBroker, LiveBrokerError, LiveTradingConfig, evaluate_live_order_risk
from app.live_broker import _available_balance  # internal helper, kept server-side only
from app.live_broker import get_live_broker, masked_exchange_request
from app.live_broker import is_emergency_stopped
from app.strategies import apply_strategy
from app.upbit import fetch_minute_candles

logger = logging.getLogger("uvicorn.error")
_auto_tick_lock = Lock()


@dataclass(frozen=True)
class AutoLivePilotConfig:
    live_auto_trading_enabled: bool
    auto_pilot_enabled: bool
    allowed_exchange: str
    allowed_market: str
    min_order_krw: float
    max_order_krw: float
    max_orders_per_day: int
    order_type: str
    buy_price_offset_percent: float
    cancel_after_seconds: int
    cooldown_seconds: int
    require_completed_candle: bool

    @classmethod
    def from_env(cls) -> "AutoLivePilotConfig":
        return cls(
            live_auto_trading_enabled=os.getenv("LIVE_AUTO_TRADING_ENABLED", "false").lower() == "true",
            auto_pilot_enabled=os.getenv("AUTO_PILOT_ENABLED", "false").lower() == "true",
            allowed_exchange=os.getenv("AUTO_ALLOWED_EXCHANGE", "bithumb").lower(),
            allowed_market=os.getenv("AUTO_ALLOWED_MARKET", "KRW-BTC"),
            min_order_krw=float(os.getenv("AUTO_MIN_ORDER_KRW", "10000")),
            max_order_krw=float(os.getenv("AUTO_MAX_ORDER_KRW", "10000")),
            max_orders_per_day=int(os.getenv("AUTO_MAX_ORDERS_PER_DAY", "1")),
            order_type=os.getenv("AUTO_ORDER_TYPE", "limit").lower(),
            buy_price_offset_percent=float(os.getenv("AUTO_BUY_PRICE_OFFSET_PERCENT", "3")),
            cancel_after_seconds=int(os.getenv("AUTO_CANCEL_AFTER_SECONDS", "60")),
            cooldown_seconds=int(os.getenv("AUTO_COOLDOWN_SECONDS", "1800")),
            require_completed_candle=os.getenv("AUTO_REQUIRE_COMPLETED_CANDLE", "true").lower() == "true",
        )


def auto_live_pilot_status() -> dict:
    session = load_latest_auto_live_pilot_session()
    config = AutoLivePilotConfig.from_env()
    live_config = LiveTradingConfig.for_exchange(config.allowed_exchange)
    return {
        "session": session,
        "exchange": config.allowed_exchange,
        "market": config.allowed_market,
        "live_trading_enabled": live_config.live_trading_enabled,
        "live_auto_trading_enabled": config.live_auto_trading_enabled,
        "auto_pilot_enabled": config.auto_pilot_enabled,
        "emergency_stop": is_emergency_stopped(),
        "api_key_loaded": live_config.api_key_loaded,
        "min_auto_order_krw": config.min_order_krw,
        "max_auto_order_krw": config.max_order_krw,
        "max_orders_per_day": config.max_orders_per_day,
        "auto_cancel_after_seconds": config.cancel_after_seconds,
        "order_type": config.order_type,
    }


def start_auto_live_pilot(*, candidate_strategy_id: int, order_amount_krw: float, confirmation: str, order_confirmation: str) -> dict:
    if confirmation != "AUTO PILOT ENABLE":
        return {"ok": False, "message": "AUTO PILOT ENABLE confirmation is required.", **auto_live_pilot_status()}
    if order_confirmation != "PLACE AUTO LIVE ORDER":
        return {"ok": False, "message": "PLACE AUTO LIVE ORDER confirmation is required.", **auto_live_pilot_status()}
    config = AutoLivePilotConfig.from_env()
    candidate = load_candidate_strategy(candidate_strategy_id)
    if candidate is None:
        return {"ok": False, "message": "Candidate strategy not found.", **auto_live_pilot_status()}
    live_config = LiveTradingConfig.for_exchange(config.allowed_exchange)
    min_order_krw = max(config.min_order_krw, live_config.min_order_krw)
    max_order_krw = max(config.max_order_krw, min_order_krw)
    amount = min(max(float(order_amount_krw), min_order_krw), max_order_krw)
    session_id = create_auto_live_pilot_session(
        {
            "exchange": config.allowed_exchange,
            "market": config.allowed_market,
            "candidate_strategy_id": candidate["id"],
            "strategy_name": candidate["strategy"],
            "status": "READY",
            "auto_enabled": True,
            "order_amount_krw": amount,
            "max_orders_per_day": config.max_orders_per_day,
        }
    )
    run_auto_live_pilot_tick()
    return {"ok": True, "session_id": session_id, **auto_live_pilot_status()}


def stop_auto_live_pilot() -> dict:
    session = load_latest_auto_live_pilot_session()
    if session:
        now_utc = _utc_now()
        update_auto_live_pilot_session(int(session["id"]), {"status": "STOPPED", "auto_enabled": False, "stopped_at": now_utc})
    return {"ok": True, **auto_live_pilot_status()}


def cancel_auto_live_pilot_open_order() -> dict:
    session = load_latest_auto_live_pilot_session()
    if not session or not session.get("last_order_uuid"):
        return {"ok": False, "message": "No Auto Pilot open order.", **auto_live_pilot_status()}
    try:
        response = asyncio.run(BithumbBroker().cancel_order(str(session["last_order_uuid"])))
        _update_order_by_uuid(str(session["last_order_uuid"]), "CANCELED", response)
        update_auto_live_pilot_session(
            int(session["id"]),
            {"status": "STOPPED", "last_order_status": "CANCELED", "stopped_at": _utc_now()},
        )
        return {"ok": True, "message": "Auto Pilot order canceled.", **auto_live_pilot_status()}
    except Exception as exc:
        update_auto_live_pilot_session(int(session["id"]), {"status": "ERROR", "last_order_status": "FAILED", "stopped_at": _utc_now()})
        return {"ok": False, "message": str(exc), **auto_live_pilot_status()}


def run_auto_live_pilot_tick() -> None:
    if not _auto_tick_lock.acquire(blocking=False):
        return
    try:
        asyncio.run(process_auto_live_pilot_sessions())
    finally:
        _auto_tick_lock.release()


async def process_auto_live_pilot_sessions() -> None:
    for session in load_running_auto_live_pilot_sessions():
        try:
            await _process_session(session)
        except Exception as exc:
            logger.exception("[auto-live] session=%s failed", session.get("id"))
            update_auto_live_pilot_session(
                int(session["id"]),
                {"status": "ERROR", "last_order_status": "ERROR", "stopped_at": _utc_now()},
            )
            _insert_blocked_log(session, "BLOCKED_API_RESPONSE_ERROR", str(exc), None, None)


async def _process_session(session: dict) -> None:
    config = AutoLivePilotConfig.from_env()
    live_config = LiveTradingConfig.for_exchange(config.allowed_exchange)

    if session.get("last_order_uuid"):
        await _manage_open_order(session, config)
        return

    blocked = await _precheck_block_reason(session, config, live_config)
    if blocked:
        _insert_blocked_log(session, blocked, blocked, None, None)
        update_auto_live_pilot_session(int(session["id"]), {"status": "RUNNING", "last_order_status": "BLOCKED"})
        return

    candidate = load_candidate_strategy(int(session["candidate_strategy_id"]))
    if candidate is None:
        _insert_blocked_log(session, "BLOCKED_DUPLICATE_SIGNAL", "Candidate strategy not found.", None, None)
        update_auto_live_pilot_session(int(session["id"]), {"status": "ERROR", "stopped_at": _utc_now()})
        return

    fresh = await fetch_minute_candles(market=config.allowed_market, unit=int(candidate["unit"]), count=300)
    insert_candles(fresh)
    candles = load_candles(config.allowed_market, int(candidate["unit"]), 300)
    latest = latest_completed_candle(candles, int(candidate["unit"])) if config.require_completed_candle else (candles[-1] if candles else None)
    if latest is None:
        return
    candle_time = latest["candle_time_utc"]
    signal = _latest_signal(candidate, candles, candle_time)
    update_auto_live_pilot_session(
        int(session["id"]),
        {"status": "RUNNING", "last_signal": signal["signal"], "last_signal_time_utc": candle_time, "last_processed_candle_time_utc": candle_time},
    )
    if signal["signal"] != "BUY":
        return
    if has_live_order_for_candle(config.allowed_exchange, config.allowed_market, candle_time):
        _insert_blocked_log(session, "BLOCKED_DUPLICATE_CANDLE", "Duplicate candle.", candle_time, signal)
        update_auto_live_pilot_session(int(session["id"]), {"status": "RUNNING", "last_order_status": "BLOCKED"})
        return

    await _submit_pilot_order(session, candidate, latest, signal, config, live_config)


async def _precheck_block_reason(session: dict, config: AutoLivePilotConfig, live_config: LiveTradingConfig) -> str | None:
    if is_emergency_stopped():
        return "BLOCKED_EMERGENCY_STOP"
    if not live_config.live_trading_enabled:
        return "BLOCKED_LIVE_DISABLED"
    if not config.live_auto_trading_enabled or not config.auto_pilot_enabled:
        return "BLOCKED_AUTO_DISABLED"
    if config.allowed_exchange != "bithumb" or session["exchange"] != "bithumb":
        return "BLOCKED_EXCHANGE_NOT_ALLOWED"
    if config.allowed_market != "KRW-BTC" or session["market"] != "KRW-BTC":
        return "BLOCKED_MARKET_NOT_ALLOWED"
    if config.order_type != "limit":
        return "BLOCKED_ORDER_TYPE_NOT_ALLOWED"
    if not live_config.api_key_loaded:
        return "BLOCKED_ORDER_CHANCE_FAILED"
    if count_auto_live_orders_today("bithumb", "KRW-BTC") >= config.max_orders_per_day:
        return "BLOCKED_DAILY_ORDER_COUNT"
    if has_open_auto_live_order("bithumb", "KRW-BTC"):
        return "BLOCKED_OPEN_ORDER_EXISTS"
    last_order_time = session.get("last_order_time_utc")
    if last_order_time and _seconds_since(last_order_time) < config.cooldown_seconds:
        return "BLOCKED_COOLDOWN"
    return None


async def _submit_pilot_order(session: dict, candidate: dict, candle: dict, signal: dict, config: AutoLivePilotConfig, live_config: LiveTradingConfig) -> None:
    broker = get_live_broker("bithumb")
    balances = await broker.get_balances()
    chance = await broker.get_order_chance("KRW-BTC")
    if not chance:
        _insert_blocked_log(session, "BLOCKED_ORDER_CHANCE_FAILED", "Order chance failed.", candle["candle_time_utc"], signal)
        update_auto_live_pilot_session(int(session["id"]), {"status": "STOPPED", "last_order_status": "BLOCKED", "stopped_at": _utc_now()})
        return

    current_price = float(candle["trade_price"])
    range_rate = ((float(candle["high_price"]) - float(candle["low_price"])) / current_price) if current_price > 0 else 0.0
    price = _round_price(current_price * (1 - config.buy_price_offset_percent / 100))
    min_order_krw = max(config.min_order_krw, live_config.min_order_krw)
    max_order_krw = max(config.max_order_krw, min_order_krw)
    amount = min(max(float(session["order_amount_krw"]), min_order_krw), max_order_krw)
    volume = amount / price if price > 0 else 0.0
    request_id = f"auto-{uuid.uuid4().hex[:24]}"
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
    if not risk["allowed"]:
        _insert_blocked_log(session, risk["risk_result"], risk.get("blocked_reason"), candle["candle_time_utc"], signal, order, risk)
        update_auto_live_pilot_session(int(session["id"]), {"status": "STOPPED", "last_order_status": "BLOCKED", "stopped_at": _utc_now()})
        return
    if _available_balance(balances, "KRW") < amount + risk["fee_estimate"]:
        _insert_blocked_log(session, "BLOCKED_INSUFFICIENT_BALANCE", "Insufficient KRW balance.", candle["candle_time_utc"], signal, order, risk)
        update_auto_live_pilot_session(int(session["id"]), {"status": "STOPPED", "last_order_status": "BLOCKED", "stopped_at": _utc_now()})
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
        update_auto_live_pilot_session(
            int(session["id"]),
            {
                "status": "RUNNING",
                "orders_created_today": int(session.get("orders_created_today") or 0) + 1,
                "last_order_time_utc": _utc_now(),
                "last_order_uuid": order_uuid,
                "last_order_status": "SUBMITTED",
            },
        )
    except Exception as exc:
        update_live_order_log(request_id, {"status": "FAILED", "risk_result": "BLOCKED_API_RESPONSE_ERROR", "error_message": str(exc)})
        update_auto_live_pilot_session(int(session["id"]), {"status": "ERROR", "last_order_status": "FAILED", "stopped_at": _utc_now()})


async def _manage_open_order(session: dict, config: AutoLivePilotConfig) -> None:
    order_uuid = str(session["last_order_uuid"])
    broker = BithumbBroker()
    try:
        status = await broker.get_order(order_uuid)
        state = str(status.get("state") or status.get("status") or "").lower()
        if state in {"done", "filled"}:
            _update_order_by_uuid(order_uuid, "FILLED", status)
            update_auto_live_pilot_session(int(session["id"]), {"status": "STOPPED", "last_order_status": "FILLED", "stopped_at": _utc_now()})
            return
        if _seconds_since(str(session.get("last_order_time_utc") or _utc_now())) >= config.cancel_after_seconds:
            cancel_response = await broker.cancel_order(order_uuid)
            _update_order_by_uuid(order_uuid, "CANCELED", cancel_response)
            update_auto_live_pilot_session(int(session["id"]), {"status": "STOPPED", "last_order_status": "CANCELED", "stopped_at": _utc_now()})
            return
        _update_order_by_uuid(order_uuid, "WAITING", status)
        update_auto_live_pilot_session(int(session["id"]), {"status": "RUNNING", "last_order_status": "WAITING"})
    except Exception as exc:
        _update_order_by_uuid(order_uuid, "FAILED", {"error": str(exc)})
        update_auto_live_pilot_session(int(session["id"]), {"status": "ERROR", "last_order_status": "FAILED", "stopped_at": _utc_now()})


def _insert_blocked_log(session: dict, risk_result: str, message: str | None, candle_time_utc: str | None, signal: dict | None, order: dict | None = None, preview: dict | None = None) -> None:
    payload = order or {
        "request_id": f"auto-blocked-{uuid.uuid4().hex[:20]}",
        "exchange": session.get("exchange", "bithumb"),
        "market": session.get("market", "KRW-BTC"),
        "side": "BUY",
        "order_type": "LIMIT",
        "price": 0.0,
        "volume": 0.0,
        "amount_krw": session.get("order_amount_krw", 0.0),
    }
    insert_live_order_log(_log_payload(session, "BLOCKED", risk_result, candle_time_utc, signal, payload, preview or {"risk_result": risk_result, "fee_estimate": 0.0}, message))


def _log_payload(session: dict, status: str, risk_result: str, candle_time_utc: str | None, signal: dict | None, order: dict, preview: dict | None, error_message: str | None = None) -> dict:
    return {
        "request_id": order["request_id"],
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
    latest = load_latest_auto_live_pilot_session()
    request_id = None
    if latest and latest.get("last_order_uuid") == order_uuid:
        from app.database import load_live_order_logs

        logs = load_live_order_logs(200)
        for item in logs:
            if item.get("order_uuid") == order_uuid and not _is_auto_order_event_request(str(item.get("request_id", ""))):
                request_id = item["request_id"]
                break
        if request_id is None:
            for item in logs:
                if item.get("order_uuid") == order_uuid:
                    request_id = item["request_id"]
                    break
    if request_id:
        update_live_order_log(request_id, {"status": status, "exchange_response_payload": response, "order_uuid": order_uuid})
        current = get_live_order_log(request_id)
        if current is not None and status in {"WAITING", "CANCELED", "FILLED", "FAILED"}:
            event_payload = {
                **current,
                "request_id": f"{request_id}-{status.lower()}-{uuid.uuid4().hex[:8]}",
                "status": status,
                "exchange_response_payload": response,
                "order_uuid": order_uuid,
                "error_message": current.get("error_message") if status == "FAILED" else None,
            }
            insert_live_order_log(event_payload)


def _is_auto_order_event_request(request_id: str) -> bool:
    return (
        request_id.endswith("-submitted")
        or "-waiting-" in request_id
        or "-canceled-" in request_id
        or "-filled-" in request_id
        or "-failed-" in request_id
    )


def _latest_signal(candidate: dict, candles: list[dict], candle_time_utc: str) -> dict:
    context = [candle for candle in candles if candle["candle_time_utc"] <= candle_time_utc]
    frame = candles_to_frame(context)
    signal_frame = apply_strategy(candidate["strategy"], frame, candidate.get("parameters", {}))
    last = signal_frame.iloc[-1]
    return {"signal": str(last["signal"]), "reason": str(last["reason"]), "price": float(last["close"])}


def _round_price(price: float) -> float:
    return float(int(price // 1000) * 1000)


def _seconds_since(timestamp_utc: str) -> float:
    normalized = timestamp_utc.replace("Z", "+00:00")
    return (datetime.now(timezone.utc) - datetime.fromisoformat(normalized)).total_seconds()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
