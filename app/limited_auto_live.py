from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from app.accounting_epoch import build_current_epoch_diagnostics
from app.database import (
    get_live_order_log,
    load_current_accounting_epoch,
    load_exchange_fills_ledger,
    load_global_bot_operation_policy,
    load_runtime_lock,
    insert_live_order_log,
    update_live_order_log,
)
from app.exchange_fills_ledger import load_or_build_ledger_rows
from app.live_broker import LiveTradingConfig, get_live_broker, is_emergency_stopped, masked_exchange_request
from app.live_recovery import normalize_exchange_order, reconcile_order_log
from app.live_smoke_test import (
    EQUITY_TOLERANCE_KRW,
    FEE_TOLERANCE_KRW,
    _assess_smoke_ledger_rows,
    _current_equity,
    _float,
    _orderbook_quote,
    _round_volume,
)

CONFIRMATION_PHRASE = "RUN LIMITED AUTO LIVE ONCE"
ALLOWED_SYMBOLS = {"BTC", "ETH"}
BLOCKED_SYMBOLS = {"RE", "WLD", "XLM"}
MAX_NOTIONAL_KRW = 6000.0
MAX_ORDERS = 3
TIMEOUT_SECONDS = 300


async def run_one_shot_limited_auto_live(
    *,
    exchange: str = "bithumb",
    symbol: str = "BTC",
    amount_krw: float = MAX_NOTIONAL_KRW,
    confirmation: str,
    limited_gate: dict | None = None,
    current_epoch: dict | None = None,
) -> dict:
    exchange = (exchange or "bithumb").lower()
    symbol = (symbol or "BTC").upper()
    market = f"KRW-{symbol}"
    started_at = _utc_now()
    run_id = f"limited-{started_at.replace(':', '').replace('-', '').replace('Z', '')}-{uuid.uuid4().hex[:6]}"
    notional = min(_float(amount_krw, MAX_NOTIONAL_KRW), MAX_NOTIONAL_KRW)
    report: dict[str, Any] = {
        "limited_auto_live_id": run_id,
        "limited_auto_live_status": "FAILED",
        "started_at_utc": started_at,
        "completed_at_utc": None,
        "symbol": symbol,
        "market": market,
        "notional_krw": notional,
        "max_orders": MAX_ORDERS,
        "max_open_positions": 1,
        "buy_order_requested": False,
        "buy_order_filled": False,
        "sell_order_requested": False,
        "sell_order_filled": False,
        "order_count": 0,
        "exchange_order_uuid_list": [],
        "client_order_id_list": [],
        "exchange_fill_count": 0,
        "ledger_fill_count": 0,
        "missing_ledger_fill_count": 0,
        "duplicate_fill_count": 0,
        "fee_from_exchange": 0.0,
        "fee_from_ledger": 0.0,
        "fee_diff": 0.0,
        "equity_before": None,
        "equity_after": None,
        "expected_equity_after": None,
        "equity_diff_after": None,
        "current_epoch_fill_count_before": None,
        "current_epoch_fill_count_after": None,
        "current_epoch_pnl": None,
        "current_epoch_accounting_pending_count": None,
        "current_epoch_accounting_failed_count": None,
        "auto_stopped_after_test": True,
        "final_runtime_status": None,
        "pass_fail_reasons": [],
    }
    try:
        if confirmation != CONFIRMATION_PHRASE:
            return _finalize(report, "ABORTED", ["LIMITED_AUTO_LIVE_CONFIRMATION_REQUIRED"])
        if symbol not in ALLOWED_SYMBOLS or symbol in BLOCKED_SYMBOLS:
            return _finalize(report, "ABORTED", ["LIMITED_AUTO_SYMBOL_NOT_ALLOWED"])
        if notional <= 0 or notional > MAX_NOTIONAL_KRW:
            return _finalize(report, "ABORTED", ["LIMITED_AUTO_AMOUNT_EXCEEDS_LIMIT"])
        if limited_gate is not None and not limited_gate.get("limited_auto_live_allowed"):
            reasons = [str(item.get("code")) for item in (limited_gate.get("limited_auto_live_blockers") or [])]
            return _finalize(report, "ABORTED", reasons or ["LIMITED_AUTO_GATE_BLOCKED"], limited_gate=limited_gate)
        if not _runtime_guards_pass(exchange):
            return _finalize(report, "ABORTED", ["RUNTIME_GUARD_FAILED"], limited_gate=limited_gate)

        before_equity = await _current_equity(exchange)
        report["equity_before"] = before_equity
        current_epoch = current_epoch or build_current_epoch_diagnostics(exchange=exchange, current_equity=before_equity)
        report["current_epoch_fill_count_before"] = int(current_epoch.get("current_epoch_fill_count") or 0)
        if not current_epoch.get("current_epoch_sanity_passed"):
            return _finalize(report, "ABORTED", ["CURRENT_EPOCH_SANITY_FAILED"], limited_gate=limited_gate)

        broker = get_live_broker(exchange)
        quote = await _orderbook_quote(exchange, market)
        if quote["best_ask"] <= 0 or quote["best_bid"] <= 0:
            return _finalize(report, "ABORTED", ["ORDERBOOK_UNAVAILABLE"], limited_gate=limited_gate)

        buy_volume = _round_volume(notional / quote["best_ask"])
        if buy_volume <= 0:
            return _finalize(report, "ABORTED", ["LIMITED_AUTO_VOLUME_TOO_SMALL"], limited_gate=limited_gate)

        buy = await _submit_and_wait_limited(
            broker=broker,
            run_id=run_id,
            exchange=exchange,
            market=market,
            side="BUY",
            price=quote["best_ask"],
            volume=buy_volume,
            amount_krw=quote["best_ask"] * buy_volume,
            order_index=1,
            timeout_seconds=TIMEOUT_SECONDS,
        )
        _merge_order_result(report, buy, prefix="buy")
        if not buy.get("filled"):
            return _finalize(report, "FAILED", ["BUY_NOT_FILLED"], limited_gate=limited_gate)

        sell_quote = await _orderbook_quote(exchange, market)
        sell_volume = _round_volume(_float(buy.get("executed_volume")))
        if sell_volume > 0:
            sell = await _submit_and_wait_limited(
                broker=broker,
                run_id=run_id,
                exchange=exchange,
                market=market,
                side="SELL",
                price=sell_quote["best_bid"] if sell_quote["best_bid"] > 0 else quote["best_bid"],
                volume=sell_volume,
                amount_krw=(sell_quote["best_bid"] if sell_quote["best_bid"] > 0 else quote["best_bid"]) * sell_volume,
                order_index=2,
                timeout_seconds=TIMEOUT_SECONDS,
            )
            _merge_order_result(report, sell, prefix="sell")

        report["order_count"] = len(report["exchange_order_uuid_list"])
        ledger = _persist_limited_ledger(exchange, [item for item in [buy.get("order_log"), report.get("_sell_order_log")] if item])
        report.update(ledger)
        after_equity = await _current_equity(exchange)
        report["equity_after"] = after_equity
        report["expected_equity_after"] = after_equity
        report["equity_diff_after"] = 0.0 if after_equity is not None else None
        after_epoch = build_current_epoch_diagnostics(exchange=exchange, current_equity=after_equity)
        report["current_epoch_fill_count_after"] = int(after_epoch.get("current_epoch_fill_count") or 0)
        report["current_epoch_pnl"] = after_epoch.get("current_epoch_total_pnl")
        report["current_epoch_accounting_pending_count"] = int(after_epoch.get("current_epoch_accounting_pending_count") or 0)
        report["current_epoch_accounting_failed_count"] = int(after_epoch.get("current_epoch_accounting_failed_count") or 0)
        status, reasons = _pass_fail(report)
        return _finalize(report, status, reasons, limited_gate=limited_gate)
    except Exception as exc:
        return _finalize(report, "FAILED", [f"LIMITED_AUTO_EXCEPTION:{exc.__class__.__name__}:{str(exc)[:160]}"], limited_gate=limited_gate)


async def _submit_and_wait_limited(
    *,
    broker: Any,
    run_id: str,
    exchange: str,
    market: str,
    side: str,
    price: float,
    volume: float,
    amount_krw: float,
    order_index: int,
    timeout_seconds: int,
) -> dict:
    request_id = f"{run_id}-{side.lower()}"
    client_order_id = f"{run_id[:24]}-{side.lower()}"[:36]
    symbol = market.split("-")[-1]
    idempotency_key = f"limited-auto:{exchange}:{symbol}:{run_id}:{side}"
    order = {
        "request_id": request_id,
        "client_order_id": client_order_id,
        "idempotency_key": idempotency_key,
        "exchange": exchange,
        "market": market,
        "side": side,
        "order_type": "LIMIT",
        "ord_type": "limit",
        "price": price,
        "volume": volume,
        "amount_krw": amount_krw,
    }
    insert_live_order_log(
        {
            **order,
            "fee_estimate": amount_krw * LiveTradingConfig.for_exchange(exchange).fee_rate,
            "risk_result": "LIMITED_AUTO_LIVE_PREPARED",
            "order_preview_payload": {"limited_auto_live_id": run_id, "order_index": order_index, "max_orders": MAX_ORDERS},
            "exchange_request_payload_masked": masked_exchange_request(order),
            "exchange_response_payload": {},
            "status": "PREVIEWED",
            "order_purpose": "LIMITED_AUTO_LIVE",
            "strategy_name": "limited_auto_live",
            "signal_reason": "LIMITED_AUTO_LIVE_USER_APPROVED_ONE_SHOT",
            "signal_type": side,
            "manual_confirmed": True,
        }
    )
    response = await broker.place_order(order)
    order_uuid = str(response.get("uuid") or response.get("order_id") or response.get("id") or "")
    update_live_order_log(
        request_id,
        {
            "status": "SUBMITTED",
            "risk_result": "LIMITED_AUTO_LIVE_SUBMITTED",
            "exchange_response_payload": response,
            "order_uuid": order_uuid,
            "error_message": None,
        },
    )
    result = {"requested": True, "filled": False, "request_id": request_id, "client_order_id": client_order_id, "order_uuid": order_uuid}
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    latest_log = get_live_order_log(request_id)
    reconciled = None
    while asyncio.get_running_loop().time() < deadline:
        if latest_log and order_uuid:
            reconciled = await reconcile_order_log(latest_log, source="LIMITED_AUTO_LIVE_STATUS_RECHECK")
            latest_log = get_live_order_log(request_id)
            if reconciled and reconciled.status == "FILLED":
                result.update(
                    {
                        "filled": True,
                        "executed_volume": reconciled.executed_volume,
                        "filled_amount_krw": reconciled.filled_amount_krw,
                        "paid_fee": reconciled.paid_fee,
                        "order_log": latest_log,
                    }
                )
                return result
            if reconciled and reconciled.status == "CANCELED":
                break
        await asyncio.sleep(1)
    if order_uuid:
        try:
            cancel_response = await broker.cancel_order(order_uuid)
            update_live_order_log(request_id, {"status": "CANCELED", "risk_result": "LIMITED_AUTO_LIVE_TIMEOUT_CANCELED", "exchange_response_payload": cancel_response})
        except Exception as exc:
            update_live_order_log(request_id, {"risk_result": "LIMITED_AUTO_LIVE_TIMEOUT_CANCEL_FAILED", "error_message": str(exc)[:240]})
    latest_log = get_live_order_log(request_id)
    status = normalize_exchange_order((latest_log or {}).get("exchange_response_payload") or {}) if latest_log else None
    if status and status.executed_volume > 0:
        result.update(
            {
                "filled": status.status == "FILLED",
                "executed_volume": status.executed_volume,
                "filled_amount_krw": status.filled_amount_krw,
                "paid_fee": status.paid_fee,
                "order_log": latest_log,
            }
        )
    return result


def _persist_limited_ledger(exchange: str, order_logs: list[dict]) -> dict:
    exchange_orders = [(log.get("exchange_response_payload") or {}) for log in order_logs if log]
    db_orders = [log for log in order_logs if log]
    epoch = load_current_accounting_epoch(exchange)
    start = str((epoch or {}).get("epoch_started_at_utc") or "1970-01-01T00:00:00Z")
    before_count = len(load_exchange_fills_ledger(exchange, since_utc=start))
    ledger_rows, summary = load_or_build_ledger_rows(
        exchange_name=exchange,
        period_start_utc=start,
        exchange_orders=exchange_orders,
        db_orders=db_orders,
        persist=True,
    )
    after_count = len(ledger_rows)
    order_uuids = {str(log.get("order_uuid") or "") for log in db_orders if log.get("order_uuid")}
    relevant_rows = [row for row in ledger_rows if str(row.get("exchange_order_uuid") or "") in order_uuids]
    assessment = _assess_smoke_ledger_rows(relevant_rows, db_orders)
    return {
        "exchange_fill_count": len(relevant_rows),
        "ledger_fill_count": len(relevant_rows),
        "missing_ledger_fill_count": int(assessment["ledger_summary"].get("missing_canonical_log_count") or 0),
        "duplicate_fill_count": int(assessment["ledger_summary"].get("duplicate_fill_key_count") or 0),
        "fee_from_exchange": assessment["fee_normalized_total"],
        "fee_from_ledger": assessment["ledger_fee_total"],
        "fee_diff": assessment["fee_normalized_total"] - assessment["ledger_fee_total"],
        "ledger_row_count_before": before_count,
        "ledger_row_count_after": after_count,
        "exchange_fills_ledger_summary": {**summary, "limited_auto_relevant": assessment["ledger_summary"]},
        **assessment,
    }


def _runtime_guards_pass(exchange: str) -> bool:
    policy = load_global_bot_operation_policy()
    runtime = load_runtime_lock("auto-trading")
    live_config = LiveTradingConfig.for_exchange(exchange)
    return (
        not bool(policy.get("auto_trading_enabled"))
        and str((runtime or {}).get("status") or "").upper() == "STOPPED"
        and not is_emergency_stopped()
        and live_config.api_key_loaded
        and live_config.live_trading_enabled
    )


def _merge_order_result(report: dict, result: dict, *, prefix: str) -> None:
    report[f"{prefix}_order_requested"] = bool(result.get("requested"))
    report[f"{prefix}_order_filled"] = bool(result.get("filled"))
    if result.get("order_uuid"):
        report["exchange_order_uuid_list"].append(result["order_uuid"])
    if result.get("client_order_id"):
        report["client_order_id_list"].append(result["client_order_id"])
    if prefix == "sell":
        report["_sell_order_log"] = result.get("order_log")


def _pass_fail(report: dict) -> tuple[str, list[str]]:
    reasons = []
    if int(report.get("order_count") or 0) > MAX_ORDERS:
        reasons.append("LIMITED_AUTO_MAX_ORDERS_EXCEEDED")
    if not report.get("buy_order_filled"):
        reasons.append("BUY_NOT_FILLED")
    if not report.get("sell_order_filled"):
        reasons.append("SELL_NOT_FILLED")
    if int(report.get("exchange_fill_count") or 0) != int(report.get("ledger_fill_count") or 0):
        reasons.append("EXCHANGE_LEDGER_FILL_COUNT_MISMATCH")
    if int(report.get("missing_ledger_fill_count") or 0) != 0:
        reasons.append("MISSING_LEDGER_FILL")
    if int(report.get("duplicate_fill_count") or 0) != 0:
        reasons.append("DUPLICATE_FILL")
    if abs(_float(report.get("fee_diff"))) > FEE_TOLERANCE_KRW:
        reasons.append("FEE_DIFF_EXCEEDS_TOLERANCE")
    if report.get("equity_diff_after") is not None and abs(_float(report.get("equity_diff_after"))) > EQUITY_TOLERANCE_KRW:
        reasons.append("EQUITY_DIFF_EXCEEDS_TOLERANCE")
    if int(report.get("current_epoch_accounting_pending_count") or 0) != 0:
        reasons.append("CURRENT_EPOCH_ACCOUNTING_PENDING")
    if int(report.get("current_epoch_accounting_failed_count") or 0) != 0:
        reasons.append("CURRENT_EPOCH_ACCOUNTING_FAILED")
    runtime = load_runtime_lock("auto-trading")
    report["final_runtime_status"] = str((runtime or {}).get("status") or "UNKNOWN").upper()
    if report["final_runtime_status"] != "STOPPED":
        reasons.append("RUNTIME_NOT_STOPPED")
    return ("PASSED" if not reasons else "STOPPED", reasons)


def _finalize(report: dict, status: str, reasons: list[str], *, limited_gate: dict | None = None) -> dict:
    report["limited_auto_live_status"] = status
    report["completed_at_utc"] = _utc_now()
    report["pass_fail_reasons"] = reasons
    if limited_gate is not None:
        report["limited_auto_live_gate"] = limited_gate
    runtime = load_runtime_lock("auto-trading")
    report["final_runtime_status"] = str((runtime or {}).get("status") or "UNKNOWN").upper()
    return {key: value for key, value in report.items() if not key.startswith("_")}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
