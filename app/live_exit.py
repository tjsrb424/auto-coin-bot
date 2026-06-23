from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.database import (
    count_exit_retries,
    create_exit_candidate,
    get_live_order_log,
    has_open_exit_order,
    insert_live_order_log,
    load_active_exit_candidate,
    load_exit_candidate,
    load_latest_exit_candidate,
    load_live_position,
    load_open_live_position,
    load_open_live_positions,
    update_exit_candidate,
    update_live_order_log,
    update_live_position,
    update_live_strategy_session,
)
from app.capital_snapshot import build_capital_snapshot_async, sellable_volume_for_position, snapshot_is_fresh
from app.live_broker import LiveTradingConfig, get_live_broker, masked_exchange_request
from app.live_recovery import is_timeout_exception, log_recovery_event, normalize_exchange_order, reconcile_balances, sync_exit_order_position
from app.risk_manager import check_order_risk

logger = logging.getLogger("uvicorn.error")


@dataclass(frozen=True)
class LiveExitConfig:
    exit_enabled: bool
    exit_order_type: str
    market_order_enabled: bool
    stop_loss_percent: float
    take_profit_percent: float
    max_hold_minutes: int
    exit_price_offset_percent: float
    cancel_exit_order_after_seconds: int
    max_exit_retry_count: int
    require_manual_confirm: bool
    fee_rate: float

    @classmethod
    def from_env(cls) -> "LiveExitConfig":
        return cls(
            exit_enabled=os.getenv("AUTO_EXIT_ENABLED", "false").lower() == "true",
            exit_order_type=os.getenv("AUTO_EXIT_ORDER_TYPE", "limit").strip().lower(),
            market_order_enabled=os.getenv("AUTO_MARKET_ORDER_ENABLED", "false").lower() == "true",
            stop_loss_percent=float(os.getenv("AUTO_STOP_LOSS_PERCENT", "0.7")),
            take_profit_percent=float(os.getenv("AUTO_TAKE_PROFIT_PERCENT", "1.0")),
            max_hold_minutes=int(os.getenv("AUTO_MAX_HOLD_MINUTES", "60")),
            exit_price_offset_percent=float(os.getenv("AUTO_EXIT_PRICE_OFFSET_PERCENT", "0.2")),
            cancel_exit_order_after_seconds=int(os.getenv("AUTO_CANCEL_EXIT_ORDER_AFTER_SECONDS", "60")),
            max_exit_retry_count=int(os.getenv("AUTO_MAX_EXIT_RETRY_COUNT", "1")),
            require_manual_confirm=os.getenv("AUTO_EXIT_REQUIRE_MANUAL_CONFIRM", "true").lower() == "true",
            fee_rate=float(os.getenv("LIVE_FEE_RATE", "0.0005")),
        )


def live_exit_status(session_id: int | None = None, position_id: int | None = None) -> dict:
    candidate = None
    if position_id is not None:
        candidate = load_latest_exit_candidate(position_id=position_id)
    elif session_id is not None:
        candidate = load_latest_exit_candidate(session_id=session_id)
    config = LiveExitConfig.from_env()
    live_config = LiveTradingConfig.for_exchange(str((candidate or {}).get("exchange") or "bithumb"))
    return {
        "exit_candidate": candidate,
        "auto_exit_enabled": config.exit_enabled,
        "exit_order_type": config.exit_order_type,
        "market_order_enabled": config.market_order_enabled,
        "exit_price_offset_percent": config.exit_price_offset_percent,
        "cancel_exit_order_after_seconds": config.cancel_exit_order_after_seconds,
        "max_exit_retry_count": config.max_exit_retry_count,
        "manual_confirm_required": config.require_manual_confirm,
    }


def maybe_create_price_exit_candidate(position: dict, current_price: float, candle_time_utc: str | None = None) -> dict | None:
    reason = None
    if current_price <= float(position.get("stop_loss_price") or 0):
        reason = "STOP_LOSS"
    elif current_price >= float(position.get("take_profit_price") or 0):
        reason = "TAKE_PROFIT"
    elif _holding_minutes(position.get("opened_at")) > LiveExitConfig.from_env().max_hold_minutes:
        reason = "MAX_HOLD_TIME"
    if reason is None:
        update_live_position_metrics(position, current_price)
        return None
    return create_exit_candidate_for_position(position, reason, current_price, candle_time_utc)


def create_exit_candidate_for_position(position: dict, reason: str, current_price: float, candle_time_utc: str | None = None) -> dict | None:
    if position.get("status") in {"CLOSED", "CLOSING", "ERROR", "MANUAL_REVIEW_REQUIRED"}:
        return None
    active = load_active_exit_candidate(int(position["id"]))
    if active:
        update_live_position_metrics(position, current_price)
        return active

    config = LiveExitConfig.from_env()
    target_price = _round_krw_price(current_price * (1 - config.exit_price_offset_percent / 100))
    volume = float(position.get("entry_volume") or 0.0)
    expected_amount = target_price * volume
    expected_fee = expected_amount * config.fee_rate
    expected_pnl = (target_price - float(position.get("entry_price") or 0.0)) * volume - expected_fee
    candidate_id = create_exit_candidate(
        {
            "position_id": position["id"],
            "session_id": position["session_id"],
            "exchange": position["exchange"],
            "market": position["market"],
            "candidate_strategy_id": position["candidate_strategy_id"],
            "strategy_name": position["strategy_name"],
            "reason": reason,
            "status": "PENDING",
            "entry_price": position["entry_price"],
            "current_price": current_price,
            "target_exit_price": target_price,
            "volume": volume,
            "expected_amount_krw": expected_amount,
            "expected_fee": expected_fee,
            "expected_pnl": expected_pnl,
            "risk_result": "EXIT_CANDIDATE",
            "signal_time_utc": _utc_now(),
            "candle_time_utc": candle_time_utc,
        }
    )
    update_live_position_metrics(position, current_price, status="EXIT_CANDIDATE")
    update_live_strategy_session(
        int(position["session_id"]),
        {"current_position_id": int(position["id"]), "last_risk_result": f"{reason}_EXIT_CANDIDATE"},
    )
    candidate = load_exit_candidate(candidate_id)
    log_recovery_event(
        "EXIT_CANDIDATE_CREATED",
        "WARNING",
        f"Exit candidate created: {reason}.",
        exchange=str(position["exchange"]),
        market=str(position["market"]),
        session_id=int(position["session_id"]),
        payload={"position_id": position["id"], "exit_candidate_id": candidate_id, "reason": reason},
    )
    return candidate


def update_live_position_metrics(position: dict, current_price: float, status: str | None = None) -> None:
    volume = float(position.get("entry_volume") or 0.0)
    entry_price = float(position.get("entry_price") or 0.0)
    unrealized = (current_price - entry_price) * volume
    updates: dict[str, Any] = {"current_price": current_price, "unrealized_pnl": unrealized}
    if status:
        updates["status"] = status
    update_live_position(int(position["id"]), updates)


def approve_exit_candidate(candidate_id: int) -> dict:
    candidate = load_exit_candidate(candidate_id)
    if candidate is None:
        return {"ok": False, "message": "Exit candidate not found."}
    update_exit_candidate(candidate_id, {"status": "APPROVED", "risk_result": "MANUAL_APPROVED"})
    return {"ok": True, "exit_candidate": load_exit_candidate(candidate_id)}


def reject_exit_candidate(candidate_id: int) -> dict:
    candidate = load_exit_candidate(candidate_id)
    if candidate is None:
        return {"ok": False, "message": "Exit candidate not found."}
    update_exit_candidate(candidate_id, {"status": "CANCELED", "risk_result": "REJECTED_BY_USER"})
    position = load_open_live_position(int(candidate["session_id"]), str(candidate["exchange"]), str(candidate["market"]))
    if position and int(position["id"]) == int(candidate["position_id"]):
        update_live_position(int(position["id"]), {"status": "OPEN"})
    return {"ok": True, "exit_candidate": load_exit_candidate(candidate_id)}


def _balance_available_from_snapshot(snapshot: dict, symbol: str) -> float:
    balances = snapshot.get("balances") or {}
    by_currency = balances.get("by_currency") if isinstance(balances, dict) else {}
    if not isinstance(by_currency, dict):
        return 0.0
    item = by_currency.get(symbol)
    if not isinstance(item, dict):
        return 0.0
    return float(item.get("balance") or 0.0)


def _aggregate_same_market_exit_candidate(
    candidate: dict,
    position: dict,
    snapshot: dict,
    live_config: LiveTradingConfig,
) -> dict:
    target_price = float(candidate.get("target_exit_price") or 0.0)
    if target_price <= 0:
        return candidate
    exchange = str(candidate.get("exchange") or "bithumb")
    market = str(candidate.get("market") or "")
    positions = [
        item
        for item in (snapshot.get("positions") or load_open_live_positions(exchange, market))
        if str(item.get("exchange") or exchange) == exchange
        and str(item.get("market") or "") == market
        and str(item.get("status") or "") in {"OPEN", "EXIT_CANDIDATE", "EXIT_PENDING", "CLOSING", "MANUAL_REVIEW_REQUIRED"}
    ]
    if len(positions) <= 1:
        return candidate
    if any(has_open_exit_order(int(item["id"])) for item in positions if int(item["id"]) != int(position["id"])):
        return candidate

    current_amount = sellable_volume_for_position(snapshot, position) * target_price
    position_amounts = [float(item.get("entry_volume") or 0.0) * target_price for item in positions]
    has_dust_position = any(0 < amount < live_config.min_order_krw for amount in position_amounts)
    if current_amount >= live_config.min_order_krw and not has_dust_position:
        return candidate

    symbol = market.split("-", 1)[-1]
    total_db_volume = sum(float(item.get("entry_volume") or 0.0) for item in positions)
    aggregate_volume = min(total_db_volume, _balance_available_from_snapshot(snapshot, symbol))
    aggregate_amount = aggregate_volume * target_price
    if aggregate_volume <= 0 or aggregate_amount < live_config.min_order_krw:
        return candidate

    ordered_positions = sorted(positions, key=lambda row: (str(row.get("opened_at") or ""), int(row["id"])))
    return {
        **candidate,
        "volume": aggregate_volume,
        "expected_amount_krw": aggregate_amount,
        "expected_fee": aggregate_amount * LiveExitConfig.from_env().fee_rate,
        "aggregate_exit": True,
        "aggregate_exit_position_ids": [int(item["id"]) for item in ordered_positions],
        "aggregate_exit_reason": "SAME_MARKET_DUST_SWEEP",
    }


async def create_exit_order_preview(candidate_id: int, *, manual_confirmed: bool, is_auto_exit: bool = False) -> dict:
    candidate = load_exit_candidate(candidate_id)
    if candidate is None:
        return {"ok": False, "status": "BLOCKED", "risk_result": "EXIT_CANDIDATE_NOT_FOUND"}
    position = load_live_position(int(candidate["position_id"]))
    if position and str(position.get("status")) not in {"OPEN", "EXIT_CANDIDATE", "EXIT_PENDING"}:
        position = None
    request_id = f"exit-{uuid.uuid4().hex[:24]}"
    order, risk = await _build_exit_order(candidate, position, request_id, manual_confirmed=manual_confirmed, is_auto_exit=is_auto_exit)
    log_payload = _exit_log_payload(candidate, position, order, risk, "PREVIEWED" if risk["allowed"] else "BLOCKED", manual_confirmed, is_auto_exit)
    insert_live_order_log(log_payload)
    update_exit_candidate(candidate_id, {"status": "APPROVED" if risk["allowed"] else "BLOCKED", "risk_result": risk["risk_result"]})
    return {"ok": risk["allowed"], "request_id": request_id, "status": log_payload["status"], "preview": risk, "exit_candidate": load_exit_candidate(candidate_id)}


async def submit_exit_order(request_id: str, *, final_confirmation: str) -> dict:
    if final_confirmation != "SUBMIT LIMIT EXIT ORDER":
        return {"ok": False, "status": "BLOCKED", "risk_result": "BLOCKED_MANUAL_CONFIRM_REQUIRED", "message": "SUBMIT LIMIT EXIT ORDER confirmation is required."}
    preview = get_live_order_log(request_id)
    if preview is None:
        return {"ok": False, "status": "BLOCKED", "risk_result": "PREVIEW_NOT_FOUND"}
    if preview["status"] != "PREVIEWED" or preview["risk_result"] != "ALLOWED":
        return {"ok": False, "status": "BLOCKED", "risk_result": preview["risk_result"]}
    candidate = load_exit_candidate(int(preview["exit_candidate_id"]))
    position = load_live_position(int(preview["position_id"])) if preview.get("position_id") else None
    if position and str(position.get("status")) not in {"OPEN", "EXIT_CANDIDATE", "EXIT_PENDING"}:
        position = None
    order = {
        "request_id": request_id,
        "client_order_id": request_id[:36],
        "exchange": preview["exchange"],
        "market": preview["market"],
        "side": "SELL",
        "ord_type": "limit",
        "order_type": "LIMIT",
        "price": preview["price"],
        "volume": preview["volume"],
        "amount_krw": preview["amount_krw"],
        "order_purpose": "EXIT",
    }
    risk = await evaluate_exit_order(candidate, position, manual_confirmed=True, is_auto_exit=bool(preview.get("is_auto_exit")))
    if not risk["allowed"]:
        update_live_order_log(request_id, {"status": "BLOCKED", "risk_result": risk["risk_result"], "error_message": risk["blocked_reason"]})
        if candidate:
            update_exit_candidate(int(candidate["id"]), {"status": "BLOCKED", "risk_result": risk["risk_result"]})
        return {"ok": False, "status": "BLOCKED", "risk_result": risk["risk_result"], "preview": risk}

    broker = get_live_broker(str(preview["exchange"]))
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
                "error_message": None,
            },
        )
        if candidate:
            update_exit_candidate(int(candidate["id"]), {"status": "SUBMITTED", "risk_result": "ALLOWED"})
        if position:
            update_live_position(int(position["id"]), {"status": "CLOSING", "exit_order_uuid": order_uuid})
            for aggregate_position_id in (preview.get("order_preview_payload") or {}).get("aggregate_exit_position_ids", []):
                if int(aggregate_position_id) != int(position["id"]):
                    update_live_position(int(aggregate_position_id), {"status": "CLOSING", "exit_order_uuid": order_uuid})
            update_live_strategy_session(int(position["session_id"]), {"last_order_status": "SUBMITTED", "last_risk_result": "EXIT_SUBMITTED"})
        if order_uuid:
            await reconcile_exit_order(request_id)
        latest = get_live_order_log(request_id)
        return {"ok": True, "status": latest["status"] if latest else "SUBMITTED", "request_id": request_id, "order_uuid": order_uuid}
    except Exception as exc:
        if is_timeout_exception(exc):
            update_live_order_log(
                request_id,
                {
                    "status": "SUBMITTED",
                    "risk_result": "ORDER_STATUS_UNKNOWN_TIMEOUT",
                    "exchange_request_payload_masked": masked_exchange_request(order),
                    "error_message": "Exit order request timed out; status reconciliation is required before retry.",
                },
            )
            if candidate:
                update_exit_candidate(int(candidate["id"]), {"status": "SUBMITTED", "risk_result": "ORDER_STATUS_UNKNOWN_TIMEOUT"})
            log_recovery_event(
                "EXIT_ORDER_STATUS_UNKNOWN_TIMEOUT",
                "ERROR",
                "Exit order timed out. Re-ordering is blocked until reconciliation.",
                exchange=str(preview["exchange"]),
                market=str(preview["market"]),
                session_id=preview.get("session_id"),
                request_id=request_id,
            )
            return {"ok": False, "status": "SUBMITTED", "risk_result": "ORDER_STATUS_UNKNOWN_TIMEOUT"}
        update_live_order_log(request_id, {"status": "FAILED", "risk_result": "BLOCKED_API_RESPONSE_ERROR", "error_message": str(exc)})
        if position:
            update_live_position(int(position["id"]), {"status": "ERROR"})
        return {"ok": False, "status": "FAILED", "risk_result": "BLOCKED_API_RESPONSE_ERROR", "error_message": str(exc)}


async def reconcile_exit_order(request_id: str) -> dict | None:
    log = get_live_order_log(request_id)
    if log is None or log.get("order_purpose") != "EXIT" or not log.get("order_uuid"):
        return None
    broker = get_live_broker(str(log["exchange"]))
    raw = await broker.get_order(str(log["order_uuid"]))
    status = normalize_exchange_order(raw)
    position = load_live_position(int(log["position_id"])) if log.get("position_id") else None
    entry_basis = (float(position.get("entry_price") or 0.0) * status.executed_volume) if position else 0.0
    actual_pnl = status.filled_amount_krw - entry_basis - status.paid_fee
    update_live_order_log(
        request_id,
        {
            "status": status.status,
            "exchange_response_payload": raw,
            "executed_volume": status.executed_volume,
            "remaining_volume": status.remaining_volume,
            "filled_amount_krw": status.filled_amount_krw,
            "paid_fee": status.paid_fee,
            "actual_pnl": actual_pnl if status.status == "FILLED" else log.get("actual_pnl"),
        },
    )
    candidate_id = log.get("exit_candidate_id")
    if candidate_id:
        update_exit_candidate(int(candidate_id), {"status": _candidate_status_for_order(status.status), "risk_result": status.status})
    sync_exit_order_position({**log, "actual_pnl": actual_pnl}, status)
    return {"status": status.status, "raw": raw}


async def cancel_exit_order(request_id: str) -> dict:
    log = get_live_order_log(request_id)
    if log is None or log.get("order_purpose") != "EXIT" or not log.get("order_uuid"):
        return {"ok": False, "message": "No exit order to cancel."}
    try:
        response = await get_live_broker(str(log["exchange"])).cancel_order(str(log["order_uuid"]))
        update_live_order_log(request_id, {"status": "CANCELED", "exchange_response_payload": response})
        if log.get("exit_candidate_id"):
            update_exit_candidate(int(log["exit_candidate_id"]), {"status": "CANCELED", "risk_result": "CANCELED"})
        if log.get("position_id"):
            update_live_position(int(log["position_id"]), {"status": "MANUAL_REVIEW_REQUIRED"})
        return {"ok": True, "status": "CANCELED"}
    except Exception as exc:
        log_recovery_event(
            "EXIT_ORDER_CANCEL_FAILED",
            "ERROR",
            "Exit order cancel failed.",
            exchange=str(log["exchange"]),
            market=str(log["market"]),
            session_id=log.get("session_id"),
            request_id=request_id,
            order_uuid=str(log["order_uuid"]),
            payload={"error": str(exc)},
        )
        return {"ok": False, "status": "FAILED", "message": str(exc)}


async def manage_exit_order_timeout(position: dict, config: LiveExitConfig | None = None) -> None:
    config = config or LiveExitConfig.from_env()
    candidate = load_active_exit_candidate(int(position["id"]))
    if not candidate:
        return
    # The active order is found through LiveOrderLog; keeping this conservative
    # avoids canceling unrelated entry orders.
    from app.database import load_live_order_logs

    for log in load_live_order_logs(300, include_canonical_with_events=True):
        if log.get("order_purpose") == "EXIT" and log.get("position_id") == position["id"] and log.get("status") in {"SUBMITTED", "WAITING"}:
            await reconcile_exit_order(str(log["request_id"]))
            refreshed = get_live_order_log(str(log["request_id"]))
            if refreshed and refreshed.get("status") == "WAITING" and _seconds_since(str(refreshed.get("updated_at") or _utc_now())) >= config.cancel_exit_order_after_seconds:
                await cancel_exit_order(str(refreshed["request_id"]))
            return


async def evaluate_exit_order(candidate: dict | None, position: dict | None, *, manual_confirmed: bool, is_auto_exit: bool) -> dict:
    config = LiveExitConfig.from_env()
    live_config = LiveTradingConfig.for_exchange(str((candidate or {}).get("exchange") or "bithumb"))
    risk_result = "ALLOWED"
    reason = ""
    balance_mismatch = None
    balances = None
    if candidate is None:
        risk_result = "EXIT_CANDIDATE_NOT_FOUND"
    elif position is None:
        risk_result = "BLOCKED_POSITION_NOT_OPEN"
    elif str(candidate.get("exchange")) != "bithumb":
        risk_result = "BLOCKED_EXCHANGE_NOT_ALLOWED"
    elif str(candidate.get("market")) != str(position.get("market")):
        risk_result = "BLOCKED_SELL_POSITION_NOT_FOUND"
    elif config.exit_order_type != "limit":
        risk_result = "BLOCKED_MARKET_ORDER_DISABLED"
    elif config.market_order_enabled:
        risk_result = "BLOCKED_MARKET_ORDER_DISABLED"
    elif is_auto_exit and _is_emergency_stopped():
        risk_result = "BLOCKED_EMERGENCY_STOP"
    elif is_auto_exit and not config.exit_enabled:
        risk_result = "BLOCKED_EXIT_DISABLED"
    elif str(position.get("status")) not in {"OPEN", "EXIT_CANDIDATE", "EXIT_PENDING"}:
        risk_result = "BLOCKED_POSITION_NOT_OPEN"
    elif has_open_exit_order(int(position["id"])):
        risk_result = "BLOCKED_OPEN_EXIT_ORDER_EXISTS"
    elif count_exit_retries(int(candidate["id"])) >= config.max_exit_retry_count:
        risk_result = "BLOCKED_MAX_EXIT_RETRY"
    elif (not is_auto_exit) and config.require_manual_confirm and (not manual_confirmed or candidate.get("status") != "APPROVED"):
        risk_result = "BLOCKED_MANUAL_CONFIRM_REQUIRED"
    else:
        balance_status = await reconcile_balances(str(candidate["exchange"]), str(candidate["market"]))
        balance_mismatch = bool(balance_status.get("blocking"))
        if balance_status.get("blocking"):
            risk_result = "BLOCKED_BALANCE_MISMATCH"
        else:
            try:
                broker = get_live_broker(str(candidate["exchange"]))
                balances = await broker.get_balances()
                await broker.get_order_chance(str(candidate["market"]))
                snapshot = await build_capital_snapshot_async(str(candidate["exchange"]))
                if not snapshot_is_fresh(snapshot):
                    risk_result = "BLOCKED_SNAPSHOT_STALE"
                else:
                    candidate = _aggregate_same_market_exit_candidate(candidate, position, snapshot, live_config)
                    sellable_volume = sellable_volume_for_position(snapshot, position)
                    target_price = float(candidate.get("target_exit_price") or 0.0)
                    if candidate.get("aggregate_exit"):
                        sellable_volume = float(candidate.get("volume") or sellable_volume)
                    if sellable_volume <= 0:
                        risk_result = "BLOCKED_SELL_BALANCE_ZERO"
                    elif sellable_volume + 1e-12 < float(candidate["volume"]):
                        adjusted_amount = sellable_volume * target_price
                        if adjusted_amount < live_config.min_order_krw:
                            risk_result = "BLOCKED_SELL_VOLUME_BELOW_MIN"
                        else:
                            candidate = {
                                **candidate,
                                "volume": sellable_volume,
                                "expected_amount_krw": adjusted_amount,
                            }
                    elif float(candidate.get("expected_amount_krw") or 0.0) < live_config.min_order_krw:
                        risk_result = "BLOCKED_SELL_VOLUME_BELOW_MIN"
            except Exception as exc:
                risk_result = "BLOCKED_ORDER_CHANCE_FAILED"
                reason = str(exc)
    allowed = risk_result == "ALLOWED"
    if not allowed and not reason:
        reason = risk_result
    amount = float(candidate.get("expected_amount_krw") or 0.0) if candidate else 0.0
    result = {
        "allowed": allowed,
        "risk_result": risk_result,
        "blocked_reason": reason,
        "order_purpose": "EXIT",
        "side": "SELL",
        "order_type": "LIMIT",
        "price": float(candidate.get("target_exit_price") or 0.0) if candidate else 0.0,
        "volume": float(candidate.get("volume") or 0.0) if candidate else 0.0,
        "amount_krw": amount,
        "fee_estimate": float(candidate.get("expected_fee") or 0.0) if candidate else 0.0,
        "expected_pnl": float(candidate.get("expected_pnl") or 0.0) if candidate else 0.0,
        "manual_confirmed": manual_confirmed,
        "is_auto_exit": is_auto_exit,
        "aggregate_exit": bool(candidate.get("aggregate_exit")) if candidate else False,
        "aggregate_exit_position_ids": candidate.get("aggregate_exit_position_ids", []) if candidate else [],
        "aggregate_exit_reason": candidate.get("aggregate_exit_reason") if candidate else None,
    }
    if candidate:
        order = {
            "request_id": f"exit-candidate-{candidate['id']}",
            "exchange": candidate["exchange"],
            "market": candidate["market"],
            "side": "SELL",
            "order_type": "LIMIT",
            "price": candidate["target_exit_price"],
            "volume": candidate["volume"],
            "amount_krw": candidate["expected_amount_krw"],
        }
        result = check_order_risk(
            order=order,
            purpose="EXIT",
            base_result=result,
            mode="AUTO_STRATEGY_RUNNING" if is_auto_exit else "LIVE_MANUAL_ONLY",
            session_id=int(candidate["session_id"]),
            position_id=int(candidate["position_id"]),
            candidate_strategy_id=int(candidate["candidate_strategy_id"]),
            candle_time_utc=candidate.get("candle_time_utc"),
            signal=candidate.get("reason"),
            market_snapshot={"price": float(candidate.get("target_exit_price") or 0.0)},
            balances=balances,
            balance_mismatch=balance_mismatch,
            manual_confirmed=manual_confirmed,
            is_auto=is_auto_exit,
        )
    return result


async def _build_exit_order(candidate: dict, position: dict | None, request_id: str, *, manual_confirmed: bool, is_auto_exit: bool) -> tuple[dict, dict]:
    risk = await evaluate_exit_order(candidate, position, manual_confirmed=manual_confirmed, is_auto_exit=is_auto_exit)
    order = {
        "request_id": request_id,
        "client_order_id": request_id[:36],
        "exchange": candidate["exchange"],
        "market": candidate["market"],
        "side": "SELL",
        "ord_type": "limit",
        "order_type": "LIMIT",
        "price": risk["price"],
        "volume": risk["volume"],
        "amount_krw": risk["amount_krw"],
        "order_purpose": "EXIT",
    }
    return order, risk


def _exit_log_payload(candidate: dict, position: dict | None, order: dict, risk: dict, status: str, manual_confirmed: bool, is_auto_exit: bool) -> dict:
    return {
        "request_id": order["request_id"],
        "session_id": candidate["session_id"],
        "candidate_strategy_id": candidate["candidate_strategy_id"],
        "exchange": candidate["exchange"],
        "market": candidate["market"],
        "side": "SELL",
        "order_type": "LIMIT",
        "price": order["price"],
        "volume": order["volume"],
        "amount_krw": order["amount_krw"],
        "fee_estimate": risk["fee_estimate"],
        "risk_result": risk["risk_result"],
        "order_preview_payload": risk,
        "exchange_request_payload_masked": {},
        "exchange_response_payload": {},
        "status": status,
        "error_message": None if risk["allowed"] else risk["blocked_reason"],
        "position_id": candidate["position_id"],
        "exit_candidate_id": candidate["id"],
        "order_purpose": "EXIT",
        "exit_reason": candidate["reason"],
        "expected_pnl": risk["expected_pnl"],
        "actual_pnl": None,
        "is_auto_exit": is_auto_exit,
        "manual_confirmed": manual_confirmed,
        "strategy_name": candidate["strategy_name"],
        "signal_reason": candidate["reason"],
        "candle_time_utc": candidate.get("candle_time_utc"),
    }


def _candidate_status_for_order(order_status: str) -> str:
    if order_status == "FILLED":
        return "COMPLETED"
    if order_status == "CANCELED":
        return "CANCELED"
    if order_status == "PARTIALLY_FILLED":
        return "SUBMITTED"
    return order_status


def _holding_minutes(opened_at: str | None) -> float:
    if not opened_at:
        return 0.0
    return _seconds_since(opened_at) / 60


def _seconds_since(timestamp_utc: str) -> float:
    normalized = timestamp_utc.replace("Z", "+00:00")
    return (datetime.now(timezone.utc) - datetime.fromisoformat(normalized)).total_seconds()


def _round_krw_price(price: float) -> float:
    if price >= 1_000_000:
        return float(int(price // 1000) * 1000)
    if price >= 100_000:
        return float(int(price // 100) * 100)
    if price >= 10_000:
        return float(int(price // 10) * 10)
    return float(int(price))


def _is_emergency_stopped() -> bool:
    from app.live_broker import is_emergency_stopped

    return is_emergency_stopped()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
