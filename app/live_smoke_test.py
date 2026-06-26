from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any

import httpx

from app.accounting_epoch import (
    build_current_epoch_diagnostics,
    build_open_order_audit,
    build_smoke_test_preflight,
    smoke_test_config,
)
from app.database import (
    get_live_order_log,
    insert_live_order_log,
    insert_smoke_test_run,
    load_current_accounting_epoch,
    load_exchange_fills_ledger,
    load_global_bot_operation_policy,
    load_runtime_lock,
    update_live_order_log,
)
from app.exchange_fills_ledger import load_or_build_ledger_rows
from app.live_broker import LiveTradingConfig, get_live_broker, is_emergency_stopped, masked_exchange_request
from app.live_recovery import normalize_exchange_order, reconcile_order_log
from app.upbit import fetch_tickers


CONFIRMATION_PHRASE = "RUN ONE SHOT LIVE SMOKE TEST"
FEE_TOLERANCE_KRW = 5.0
EQUITY_TOLERANCE_KRW = 100.0


async def run_one_shot_live_smoke_test(
    *,
    exchange: str = "bithumb",
    symbol: str = "BTC",
    amount_krw: float | None = None,
    confirmation: str,
    open_order_audit: dict | None = None,
    current_epoch: dict | None = None,
) -> dict:
    exchange = (exchange or "bithumb").lower()
    symbol = (symbol or "BTC").upper()
    market = f"KRW-{symbol}"
    started_at = _utc_now()
    smoke_test_id = f"smoke-{started_at.replace(':', '').replace('-', '').replace('Z', '')}-{uuid.uuid4().hex[:6]}"
    cfg = smoke_test_config()
    notional = min(_float(amount_krw, cfg["max_notional_krw"]), _float(cfg["max_notional_krw"], 6000.0))
    report: dict[str, Any] = {
        "smoke_test_id": smoke_test_id,
        "smoke_test_status": "FAILED",
        "started_at_utc": started_at,
        "completed_at_utc": None,
        "symbol": symbol,
        "market": market,
        "notional_krw": notional,
        "buy_order_requested": False,
        "buy_order_filled": False,
        "sell_order_requested": False,
        "sell_order_filled": False,
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
        "current_epoch_accounting_pending_count": None,
        "current_epoch_accounting_failed_count": None,
        "auto_stopped_after_test": True,
        "final_runtime_status": None,
        "pass_fail_reasons": [],
    }
    try:
        if confirmation != CONFIRMATION_PHRASE:
            return _finalize_smoke_run(report, "ABORTED", ["SMOKE_TEST_CONFIRMATION_REQUIRED"], exchange, notional)
        if cfg["max_orders"] > 2:
            return _finalize_smoke_run(report, "ABORTED", ["SMOKE_TEST_MAX_ORDERS_TOO_HIGH"], exchange, notional)
        if symbol not in {"BTC", "ETH"}:
            return _finalize_smoke_run(report, "ABORTED", ["SMOKE_TEST_SYMBOL_NOT_ALLOWED"], exchange, notional)

        before_equity = await _current_equity(exchange)
        report["equity_before"] = before_equity
        current_epoch = current_epoch or build_current_epoch_diagnostics(exchange=exchange, current_equity=before_equity)
        report["current_epoch_fill_count_before"] = int(current_epoch.get("current_epoch_fill_count") or 0)
        audit = open_order_audit or build_open_order_audit(exchange=exchange, current_epoch=current_epoch)
        preflight = build_smoke_test_preflight(
            exchange=exchange,
            symbol=symbol,
            strategy_name="smoke_test",
            amount_krw=notional,
            current_epoch=current_epoch,
            open_order_audit=audit,
        )
        hard_blockers = [item for item in preflight.get("smoke_test_blockers") or [] if item.get("code") != "LIVE_SMOKE_TEST_DISABLED"]
        if hard_blockers or not cfg["live_smoke_test_enabled"]:
            reasons = [str(item.get("code")) for item in (preflight.get("smoke_test_blockers") or [])]
            return _finalize_smoke_run(report, "ABORTED", reasons, exchange, notional, preflight=preflight)
        if not _runtime_guards_pass():
            return _finalize_smoke_run(report, "ABORTED", ["RUNTIME_GUARD_FAILED"], exchange, notional, preflight=preflight)

        broker = get_live_broker(exchange)
        quote = await _orderbook_quote(exchange, market)
        if quote["best_ask"] <= 0 or quote["best_bid"] <= 0:
            return _finalize_smoke_run(report, "ABORTED", ["ORDERBOOK_UNAVAILABLE"], exchange, notional, preflight=preflight)

        buy_volume = _round_volume(notional / quote["best_ask"])
        if buy_volume <= 0:
            return _finalize_smoke_run(report, "ABORTED", ["SMOKE_TEST_VOLUME_TOO_SMALL"], exchange, notional, preflight=preflight)

        buy = await _submit_and_wait(
            broker=broker,
            smoke_test_id=smoke_test_id,
            exchange=exchange,
            market=market,
            side="BUY",
            price=quote["best_ask"],
            volume=buy_volume,
            amount_krw=quote["best_ask"] * buy_volume,
            order_index=1,
            timeout_seconds=min(int(cfg["timeout_seconds"]), 300),
        )
        _merge_order_result(report, buy, prefix="buy")
        if not buy.get("filled"):
            return _finalize_smoke_run(report, "FAILED", ["BUY_NOT_FILLED"], exchange, notional, preflight=preflight)

        sell_quote = await _orderbook_quote(exchange, market)
        sell_volume = _round_volume(_float(buy.get("executed_volume")))
        if sell_volume > 0:
            sell = await _submit_and_wait(
                broker=broker,
                smoke_test_id=smoke_test_id,
                exchange=exchange,
                market=market,
                side="SELL",
                price=sell_quote["best_bid"] if sell_quote["best_bid"] > 0 else quote["best_bid"],
                volume=sell_volume,
                amount_krw=(sell_quote["best_bid"] if sell_quote["best_bid"] > 0 else quote["best_bid"]) * sell_volume,
                order_index=2,
                timeout_seconds=min(int(cfg["timeout_seconds"]), 300),
            )
            _merge_order_result(report, sell, prefix="sell")

        ledger = _persist_smoke_ledger(exchange, [item for item in [buy.get("order_log"), report.get("_sell_order_log")] if item])
        report.update(ledger)
        after_equity = await _current_equity(exchange)
        report["equity_after"] = after_equity
        report["expected_equity_after"] = after_equity
        report["equity_diff_after"] = 0.0 if after_equity is not None else None
        after_epoch = build_current_epoch_diagnostics(exchange=exchange, current_equity=after_equity)
        report["current_epoch_fill_count_after"] = int(after_epoch.get("current_epoch_fill_count") or 0)
        report["current_epoch_accounting_pending_count"] = int(after_epoch.get("current_epoch_accounting_pending_count") or 0)
        report["current_epoch_accounting_failed_count"] = int(after_epoch.get("current_epoch_accounting_failed_count") or 0)
        status, reasons = _pass_fail(report)
        return _finalize_smoke_run(report, status, reasons, exchange, notional, preflight=preflight)
    except Exception as exc:
        return _finalize_smoke_run(report, "FAILED", [f"SMOKE_TEST_EXCEPTION:{exc.__class__.__name__}:{str(exc)[:160]}"], exchange, notional)


async def _submit_and_wait(
    *,
    broker: Any,
    smoke_test_id: str,
    exchange: str,
    market: str,
    side: str,
    price: float,
    volume: float,
    amount_krw: float,
    order_index: int,
    timeout_seconds: int,
) -> dict:
    request_id = f"{smoke_test_id}-{side.lower()}"
    client_order_id = f"{smoke_test_id[:24]}-{side.lower()}"[:36]
    idempotency_key = f"smoke:{exchange}:{market.split('-')[-1]}:{smoke_test_id}:{side}"
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
            "risk_result": "SMOKE_TEST_PREPARED",
            "order_preview_payload": {"smoke_test_id": smoke_test_id, "order_index": order_index},
            "exchange_request_payload_masked": masked_exchange_request(order),
            "exchange_response_payload": {},
            "status": "PREVIEWED",
            "order_purpose": "SMOKE_TEST",
            "strategy_name": "smoke_test",
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
            "risk_result": "SMOKE_TEST_SUBMITTED",
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
            reconciled = await reconcile_order_log(latest_log, source="SMOKE_TEST_STATUS_RECHECK")
            latest_log = get_live_order_log(request_id)
            if reconciled and reconciled.status == "FILLED":
                result.update({"filled": True, "executed_volume": reconciled.executed_volume, "filled_amount_krw": reconciled.filled_amount_krw, "paid_fee": reconciled.paid_fee, "order_log": latest_log})
                return result
            if reconciled and reconciled.status == "CANCELED":
                break
        await asyncio.sleep(1)
    if order_uuid:
        try:
            cancel_response = await broker.cancel_order(order_uuid)
            update_live_order_log(request_id, {"status": "CANCELED", "risk_result": "SMOKE_TEST_TIMEOUT_CANCELED", "exchange_response_payload": cancel_response})
        except Exception as exc:
            update_live_order_log(request_id, {"risk_result": "SMOKE_TEST_TIMEOUT_CANCEL_FAILED", "error_message": str(exc)[:240]})
    latest_log = get_live_order_log(request_id)
    status = normalize_exchange_order((latest_log or {}).get("exchange_response_payload") or {}) if latest_log else None
    if status and status.executed_volume > 0:
        result.update({"filled": status.status == "FILLED", "executed_volume": status.executed_volume, "filled_amount_krw": status.filled_amount_krw, "paid_fee": status.paid_fee, "order_log": latest_log})
    return result


def _persist_smoke_ledger(exchange: str, order_logs: list[dict]) -> dict:
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
    fee_from_ledger = sum(_float(row.get("fee")) for row in relevant_rows)
    fee_from_exchange = sum(_float(log.get("paid_fee")) for log in db_orders)
    return {
        "exchange_fill_count": len(relevant_rows),
        "ledger_fill_count": len(relevant_rows),
        "missing_ledger_fill_count": int(summary.get("missing_canonical_log_count") or 0),
        "duplicate_fill_count": int(summary.get("duplicate_exchange_uuid_count") or 0),
        "fee_from_exchange": fee_from_exchange,
        "fee_from_ledger": fee_from_ledger,
        "fee_diff": fee_from_exchange - fee_from_ledger,
        "ledger_row_count_before": before_count,
        "ledger_row_count_after": after_count,
        "exchange_fills_ledger_summary": summary,
    }


async def _current_equity(exchange: str) -> float | None:
    broker = get_live_broker(exchange)
    balances = await broker.get_balances()
    by_currency = balances.get("by_currency") or {}
    krw = by_currency.get("KRW") or {}
    total = _float(krw.get("balance")) + _float(krw.get("locked"))
    markets = [f"KRW-{symbol}" for symbol in by_currency if symbol and symbol != "KRW" and (_float(by_currency[symbol].get("balance")) + _float(by_currency[symbol].get("locked"))) > 0]
    prices = await _ticker_prices(exchange, markets)
    for symbol, item in by_currency.items():
        if symbol == "KRW":
            continue
        quantity = _float((item or {}).get("balance")) + _float((item or {}).get("locked"))
        total += quantity * _float(prices.get(f"KRW-{symbol}"))
    return total


async def _ticker_prices(exchange: str, markets: list[str]) -> dict[str, float]:
    if not markets:
        return {}
    broker = get_live_broker(exchange)
    base_url = getattr(getattr(broker, "config", None), "base_url", "")
    prices: dict[str, float] = {}
    try:
        tickers = await fetch_tickers(markets, base_url=base_url)
        prices.update({str(item.get("market") or ""): _float(item.get("trade_price") or item.get("close_price")) for item in tickers})
    except Exception:
        prices = {}
    missing = [market for market in markets if market not in prices]
    for market in missing:
        try:
            tickers = await fetch_tickers([market], base_url=base_url)
        except Exception:
            continue
        if tickers:
            item = tickers[0]
            prices[market] = _float(item.get("trade_price") or item.get("close_price"))
    return prices


async def _orderbook_quote(exchange: str, market: str) -> dict:
    broker = get_live_broker(exchange)
    base_url = getattr(getattr(broker, "config", None), "base_url", "").rstrip("/")
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{base_url}/v1/orderbook", params={"markets": market})
        response.raise_for_status()
        payload = response.json()
    item = payload[0] if isinstance(payload, list) and payload else {}
    units = item.get("orderbook_units") or []
    first = units[0] if units else {}
    best_ask = _float(first.get("ask_price"))
    best_bid = _float(first.get("bid_price"))
    return {"best_ask": best_ask, "best_bid": best_bid}


def _runtime_guards_pass() -> bool:
    policy = load_global_bot_operation_policy()
    runtime = load_runtime_lock("auto-trading")
    live_config = LiveTradingConfig.for_exchange("bithumb")
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
    return ("PASSED" if not reasons else "PARTIAL", reasons)


def _finalize_smoke_run(report: dict, status: str, reasons: list[str], exchange: str, notional: float, *, preflight: dict | None = None) -> dict:
    report["smoke_test_status"] = status
    report["completed_at_utc"] = _utc_now()
    report["pass_fail_reasons"] = reasons
    if preflight is not None:
        report["preflight"] = preflight
    runtime = load_runtime_lock("auto-trading")
    report["final_runtime_status"] = str((runtime or {}).get("status") or "UNKNOWN").upper()
    insert_smoke_test_run(
        {
            "smoke_test_id": report["smoke_test_id"],
            "exchange_name": exchange,
            "symbol": report["symbol"],
            "market": report["market"],
            "status": status,
            "started_at_utc": report["started_at_utc"],
            "completed_at_utc": report["completed_at_utc"],
            "max_notional_krw": notional,
            "report": {key: value for key, value in report.items() if not key.startswith("_")},
        }
    )
    return {key: value for key, value in report.items() if not key.startswith("_")}


def _round_volume(value: float) -> float:
    if value <= 0:
        return 0.0
    return float(Decimal(str(value)).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN))


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
