from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.accounting_epoch import build_current_epoch_diagnostics, limited_auto_live_gate
from app.backtest import candles_to_frame
from app.database import (
    get_connection,
    get_live_order_log,
    insert_live_order_log,
    load_current_accounting_epoch,
    load_exchange_fills_ledger,
    load_global_bot_operation_policy,
    load_runtime_lock,
    load_unresolved_live_order_logs_for_exchange,
    update_live_order_log,
)
from app.exchange_fills_ledger import load_or_build_ledger_rows
from app.forward_paper import latest_completed_candle
from app.limited_auto_live import (
    ALLOWED_SYMBOLS,
    BLOCKED_SYMBOLS,
    MAX_NOTIONAL_KRW,
    _persist_limited_ledger,
)
from app.live_broker import LiveTradingConfig, get_live_broker, is_emergency_stopped, masked_exchange_request
from app.live_recovery import normalize_exchange_order, reconcile_order_log
from app.live_smoke_test import (
    EQUITY_TOLERANCE_KRW,
    FEE_TOLERANCE_KRW,
    _current_equity,
    _float,
    _orderbook_quote,
    _round_volume,
)
from app.smart_decision import record_shadow_decision
from app.strategies import apply_strategy
from app.upbit import fetch_minute_candles

CONFIRMATION_PHRASE = "RUN CONTROLLED AUTO LIVE ONCE"
CONTROLLED_ALLOWED_STRATEGIES = {"ma_cross", "smart_autonomous"}
CONTROLLED_BLOCKED_STRATEGIES = {"rsi"}
MAX_ORDERS = 5
MAX_OPEN_POSITIONS = 1
DEFAULT_RUNTIME_SECONDS = 600
MAX_RUNTIME_SECONDS = 900
TICK_INTERVAL_SECONDS = 60
MIN_EXPECTED_EDGE_RATE = 0.006


def controlled_auto_live_gate(current_epoch: dict, smoke_preflight: dict, *, exchange: str = "bithumb") -> dict:
    limited_gate = limited_auto_live_gate(current_epoch, smoke_preflight, exchange=exchange)
    limited_run = latest_limited_auto_live_run()
    blockers = list(limited_gate.get("limited_auto_live_blockers") or [])
    if not limited_gate.get("limited_auto_live_allowed"):
        blockers.append({"code": "LIMITED_AUTO_GATE_NOT_READY", "count": 1})
    if not limited_run.get("passed"):
        blockers.append({"code": "LAST_LIMITED_AUTO_RUN_NOT_PASSED", "count": 1})
    if _float(limited_run.get("fee_diff")) != 0.0:
        blockers.append({"code": "LAST_LIMITED_AUTO_FEE_DIFF", "count": 1})
    if _float(limited_run.get("equity_diff_after")) != 0.0:
        blockers.append({"code": "LAST_LIMITED_AUTO_EQUITY_DIFF", "count": 1})
    return {
        "controlled_auto_live_allowed": len(blockers) == 0,
        "full_auto_live_allowed": False,
        "controlled_auto_live_blockers": blockers,
        "last_limited_auto_live_run": limited_run,
        "base_limited_auto_live_gate": limited_gate,
        "controlled_auto_constraints": {
            "allowed_symbols": sorted(ALLOWED_SYMBOLS),
            "blocked_symbols": sorted(BLOCKED_SYMBOLS),
            "allowed_strategies": sorted(CONTROLLED_ALLOWED_STRATEGIES),
            "blocked_strategies": sorted(CONTROLLED_BLOCKED_STRATEGIES),
            "max_notional_krw": MAX_NOTIONAL_KRW,
            "max_orders": MAX_ORDERS,
            "max_open_positions": MAX_OPEN_POSITIONS,
            "runtime_seconds_min": 600,
            "runtime_seconds_max": MAX_RUNTIME_SECONDS,
            "averaging_down_allowed": False,
            "reentry_allowed": False,
            "min_expected_edge_rate": MIN_EXPECTED_EDGE_RATE,
            "stop_on_accounting_error": True,
            "stop_on_missing_ledger_fill": True,
            "stop_on_duplicate_fill": True,
            "stop_on_fee_diff": True,
            "stop_on_equity_diff": True,
            "stop_on_open_order": True,
        },
        "controlled_auto_next_action": "USER_CONFIRM_CONTROLLED_AUTO_LIVE_START" if len(blockers) == 0 else "RESOLVE_CONTROLLED_AUTO_BLOCKERS",
    }


def latest_limited_auto_live_run() -> dict:
    with get_connection() as conn:
        rows = [
            _decode_json_fields(dict(row))
            for row in conn.execute(
                """
                SELECT *
                FROM live_order_logs
                WHERE order_purpose = 'LIMITED_AUTO_LIVE'
                ORDER BY id DESC
                LIMIT 10
                """
            ).fetchall()
        ]
    if not rows:
        return {"passed": False, "status": "MISSING"}
    run_ids = []
    for row in rows:
        preview = row.get("order_preview_payload") or {}
        run_id = str(preview.get("limited_auto_live_id") or "")
        if run_id and run_id not in run_ids:
            run_ids.append(run_id)
    run_id = run_ids[0] if run_ids else ""
    run_rows = [row for row in rows if str((row.get("order_preview_payload") or {}).get("limited_auto_live_id") or "") == run_id]
    uuids = [str(row.get("order_uuid") or "") for row in run_rows if row.get("order_uuid")]
    ledger_rows = _ledger_rows_for_order_uuids(uuids)
    fee_from_orders = sum(_float(row.get("paid_fee")) for row in run_rows)
    fee_from_ledger = sum(_float(row.get("fee")) for row in ledger_rows)
    missing = sum(1 for row in ledger_rows if str(row.get("match_status") or "") == "MISSING_CANONICAL_LOG")
    duplicate = sum(1 for row in ledger_rows if str(row.get("match_status") or "") == "DUPLICATE_FILL_KEY")
    passed = (
        len(run_rows) >= 2
        and all(str(row.get("status") or "").upper() == "FILLED" for row in run_rows)
        and len(ledger_rows) == len(uuids)
        and missing == 0
        and duplicate == 0
        and abs(fee_from_orders - fee_from_ledger) <= FEE_TOLERANCE_KRW
    )
    return {
        "passed": passed,
        "status": "PASSED" if passed else "FAILED",
        "limited_run_id": run_id,
        "order_count": len(run_rows),
        "exchange_fill_count": len(ledger_rows),
        "ledger_fill_count": len(ledger_rows),
        "missing_ledger_fill_count": missing,
        "duplicate_fill_count": duplicate,
        "fee_from_orders": fee_from_orders,
        "fee_from_ledger": fee_from_ledger,
        "fee_diff": fee_from_orders - fee_from_ledger,
        "equity_diff_after": 0.0 if passed else None,
    }


async def run_controlled_auto_live(
    *,
    exchange: str = "bithumb",
    symbols: list[str] | None = None,
    amount_krw: float = MAX_NOTIONAL_KRW,
    runtime_seconds: int = DEFAULT_RUNTIME_SECONDS,
    confirmation: str,
    controlled_gate: dict | None = None,
    current_epoch: dict | None = None,
) -> dict:
    exchange = (exchange or "bithumb").lower()
    symbols = [str(symbol).upper() for symbol in (symbols or ["BTC", "ETH"])]
    symbols = [symbol for symbol in symbols if symbol in ALLOWED_SYMBOLS and symbol not in BLOCKED_SYMBOLS]
    runtime_seconds = min(max(int(runtime_seconds), 600), MAX_RUNTIME_SECONDS)
    notional = min(_float(amount_krw, MAX_NOTIONAL_KRW), MAX_NOTIONAL_KRW)
    started_at = _utc_now()
    run_id = f"controlled-{started_at.replace(':', '').replace('-', '').replace('Z', '')}-{uuid.uuid4().hex[:6]}"
    report: dict[str, Any] = {
        "controlled_run_id": run_id,
        "controlled_auto_live_status": "FAILED",
        "started_at_utc": started_at,
        "completed_at_utc": None,
        "runtime_limit_seconds": runtime_seconds,
        "runtime_seconds": 0,
        "symbols": symbols,
        "strategies": sorted(CONTROLLED_ALLOWED_STRATEGIES),
        "used_symbols": [],
        "used_strategies": [],
        "order_count": 0,
        "buy_filled_count": 0,
        "sell_filled_count": 0,
        "exchange_order_uuid_list": [],
        "client_order_id_list": [],
        "gross_pnl": 0.0,
        "net_pnl_after_fee": 0.0,
        "total_fee": 0.0,
        "spread_slippage_estimate": 0.0,
        "exchange_fill_count": 0,
        "ledger_fill_count": 0,
        "missing_ledger_fill_count": 0,
        "duplicate_fill_count": 0,
        "fee_diff": 0.0,
        "equity_before": None,
        "equity_after": None,
        "equity_diff_after": None,
        "current_epoch_pnl_before": None,
        "current_epoch_pnl_after": None,
        "run_pnl": 0.0,
        "current_epoch_accounting_pending_count": None,
        "current_epoch_accounting_failed_count": None,
        "final_runtime_status": None,
        "pass_fail_reasons": [],
        "tick_reports": [],
    }
    try:
        if confirmation != CONFIRMATION_PHRASE:
            return _finalize(report, "ABORTED", ["CONTROLLED_AUTO_LIVE_CONFIRMATION_REQUIRED"], controlled_gate=controlled_gate)
        if not symbols:
            return _finalize(report, "ABORTED", ["CONTROLLED_AUTO_SYMBOLS_NOT_ALLOWED"], controlled_gate=controlled_gate)
        if controlled_gate is not None and not controlled_gate.get("controlled_auto_live_allowed"):
            reasons = [str(item.get("code")) for item in (controlled_gate.get("controlled_auto_live_blockers") or [])]
            return _finalize(report, "ABORTED", reasons or ["CONTROLLED_AUTO_GATE_BLOCKED"], controlled_gate=controlled_gate)
        if not _runtime_guards_pass(exchange):
            return _finalize(report, "ABORTED", ["RUNTIME_GUARD_FAILED"], controlled_gate=controlled_gate)
        if notional <= 0 or notional > MAX_NOTIONAL_KRW:
            return _finalize(report, "ABORTED", ["CONTROLLED_AUTO_AMOUNT_EXCEEDS_LIMIT"], controlled_gate=controlled_gate)

        before_equity = await _current_equity(exchange)
        report["equity_before"] = before_equity
        current_epoch = current_epoch or build_current_epoch_diagnostics(exchange=exchange, current_equity=before_equity)
        if not current_epoch.get("current_epoch_sanity_passed"):
            return _finalize(report, "ABORTED", ["CURRENT_EPOCH_SANITY_FAILED"], controlled_gate=controlled_gate)
        report["current_epoch_pnl_before"] = current_epoch.get("current_epoch_total_pnl")

        broker = get_live_broker(exchange)
        held_position: dict[str, Any] | None = None
        ordered_logs: list[dict] = []
        deadline = asyncio.get_running_loop().time() + runtime_seconds
        while asyncio.get_running_loop().time() < deadline:
            open_blocker = await _open_order_blocker(exchange, symbols)
            if open_blocker:
                return await _finalize_after_orders(report, exchange, ordered_logs, "STOPPED", [open_blocker], before_equity, controlled_gate)
            if int(report["order_count"]) >= MAX_ORDERS:
                break

            if held_position is None:
                decision = await _select_entry_decision(exchange, symbols, notional)
                report["tick_reports"].append(decision)
                if decision.get("signal") == "BUY" and decision.get("edge_allowed"):
                    buy = await _submit_and_wait_controlled(
                        broker=broker,
                        run_id=run_id,
                        exchange=exchange,
                        market=decision["market"],
                        side="BUY",
                        price=_float(decision["entry_price"]),
                        volume=_round_volume(notional / _float(decision["entry_price"])),
                        amount_krw=notional,
                        order_index=int(report["order_count"]) + 1,
                        strategy_name=str(decision["strategy"]),
                        signal_reason=str(decision.get("reason") or "CONTROLLED_ENTRY"),
                    )
                    _merge_order_result(report, buy, prefix="buy")
                    if buy.get("order_log"):
                        ordered_logs.append(buy["order_log"])
                    if not buy.get("filled"):
                        return await _finalize_after_orders(report, exchange, ordered_logs, "STOPPED", ["BUY_NOT_FILLED"], before_equity, controlled_gate)
                    held_position = {
                        "market": decision["market"],
                        "symbol": decision["symbol"],
                        "strategy": decision["strategy"],
                        "quantity": _float(buy.get("executed_volume")),
                        "buy_value": _float(buy.get("filled_amount_krw")),
                        "buy_fee": _float(buy.get("paid_fee")),
                        "buy_price": _float(decision["entry_price"]),
                    }
                else:
                    await asyncio.sleep(min(TICK_INTERVAL_SECONDS, max(deadline - asyncio.get_running_loop().time(), 0)))
                    continue
            else:
                exit_decision = await _exit_decision(exchange, held_position)
                report["tick_reports"].append(exit_decision)
                if exit_decision.get("signal") == "SELL" or deadline - asyncio.get_running_loop().time() <= TICK_INTERVAL_SECONDS:
                    sell_price = _float(exit_decision.get("exit_price"))
                    sell = await _submit_and_wait_controlled(
                        broker=broker,
                        run_id=run_id,
                        exchange=exchange,
                        market=held_position["market"],
                        side="SELL",
                        price=sell_price,
                        volume=_round_volume(_float(held_position.get("quantity"))),
                        amount_krw=sell_price * _round_volume(_float(held_position.get("quantity"))),
                        order_index=int(report["order_count"]) + 1,
                        strategy_name=str(held_position["strategy"]),
                        signal_reason=str(exit_decision.get("reason") or "CONTROLLED_EXIT"),
                    )
                    _merge_order_result(report, sell, prefix="sell")
                    if sell.get("order_log"):
                        ordered_logs.append(sell["order_log"])
                    if not sell.get("filled"):
                        return await _finalize_after_orders(report, exchange, ordered_logs, "STOPPED", ["SELL_NOT_FILLED"], before_equity, controlled_gate)
                    held_position["sell_value"] = _float(sell.get("filled_amount_krw"))
                    held_position["sell_fee"] = _float(sell.get("paid_fee"))
                    held_position["sell_price"] = sell_price
                    _apply_realized_position_pnl(report, held_position)
                    held_position = None
                    break
            await asyncio.sleep(min(TICK_INTERVAL_SECONDS, max(deadline - asyncio.get_running_loop().time(), 0)))
        if held_position is not None and int(report["order_count"]) < MAX_ORDERS:
            quote = await _orderbook_quote(exchange, held_position["market"])
            sell = await _submit_and_wait_controlled(
                broker=broker,
                run_id=run_id,
                exchange=exchange,
                market=held_position["market"],
                side="SELL",
                price=_float(quote.get("best_bid")),
                volume=_round_volume(_float(held_position.get("quantity"))),
                amount_krw=_float(quote.get("best_bid")) * _round_volume(_float(held_position.get("quantity"))),
                order_index=int(report["order_count"]) + 1,
                strategy_name=str(held_position["strategy"]),
                signal_reason="CONTROLLED_RUNTIME_END_FLATTEN",
            )
            _merge_order_result(report, sell, prefix="sell")
            if sell.get("order_log"):
                ordered_logs.append(sell["order_log"])
            if sell.get("filled"):
                held_position["sell_value"] = _float(sell.get("filled_amount_krw"))
                held_position["sell_fee"] = _float(sell.get("paid_fee"))
                held_position["sell_price"] = _float(quote.get("best_bid"))
                _apply_realized_position_pnl(report, held_position)
            else:
                return await _finalize_after_orders(report, exchange, ordered_logs, "STOPPED", ["RUNTIME_END_SELL_NOT_FILLED"], before_equity, controlled_gate)
        return await _finalize_after_orders(report, exchange, ordered_logs, "PASSED", [], before_equity, controlled_gate)
    except Exception as exc:
        return _finalize(report, "FAILED", [f"CONTROLLED_AUTO_EXCEPTION:{exc.__class__.__name__}:{str(exc)[:160]}"], controlled_gate=controlled_gate)


async def _select_entry_decision(exchange: str, symbols: list[str], notional: float) -> dict:
    decisions = []
    for symbol in symbols:
        market = f"KRW-{symbol}"
        quote = await _orderbook_quote(exchange, market)
        candles = await _load_strategy_candles(exchange, market, 1)
        ma_decision = _ma_cross_decision(symbol, market, candles, quote, notional)
        decisions.append(ma_decision)
        smart_decision = await _smart_autonomous_decision(symbol, market, candles, quote, notional)
        decisions.append(smart_decision)
    decisions.sort(key=lambda item: _float(item.get("expected_edge_rate")), reverse=True)
    return decisions[0] if decisions else {"signal": "HOLD", "reason": "NO_DECISION"}


async def _exit_decision(exchange: str, position: dict) -> dict:
    market = str(position["market"])
    quote = await _orderbook_quote(exchange, market)
    candles = await _load_strategy_candles(exchange, market, 1)
    latest = _latest_ma_signal(candles)
    price = _float(quote.get("best_bid"))
    buy_price = _float(position.get("buy_price"))
    gross_rate = (price - buy_price) / buy_price if buy_price > 0 else 0.0
    if latest.get("signal") == "SELL":
        signal = "SELL"
        reason = "MA_CROSS_EXIT_SIGNAL"
    elif gross_rate >= MIN_EXPECTED_EDGE_RATE:
        signal = "SELL"
        reason = "CONTROLLED_EDGE_CAPTURE"
    else:
        signal = "HOLD"
        reason = "HOLD_UNTIL_SIGNAL_OR_RUNTIME_END"
    return {
        "symbol": str(position.get("symbol")),
        "market": market,
        "strategy": str(position.get("strategy")),
        "signal": signal,
        "reason": reason,
        "exit_price": price,
        "unrealized_gross_rate": gross_rate,
    }


async def _load_strategy_candles(exchange: str, market: str, unit: int) -> list[dict]:
    broker = get_live_broker(exchange)
    base_url = getattr(getattr(broker, "config", None), "base_url", "")
    try:
        candles = await fetch_minute_candles(market=market, unit=unit, count=120, base_url=base_url)
    except Exception:
        candles = await fetch_minute_candles(market=market, unit=unit, count=120)
    return [_normalize_candle(candle) for candle in candles]


def _ma_cross_decision(symbol: str, market: str, candles: list[dict], quote: dict, notional: float) -> dict:
    latest = _latest_ma_signal(candles)
    frame = candles_to_frame(candles)
    close = frame["close"].astype(float) if not frame.empty else None
    ma5 = close.rolling(5).mean().iloc[-1] if close is not None and len(close) >= 20 else None
    ma20 = close.rolling(20).mean().iloc[-1] if close is not None and len(close) >= 20 else None
    last = float(close.iloc[-1]) if close is not None and len(close) else _float(quote.get("best_ask"))
    ma_gap_rate = ((float(ma5) - float(ma20)) / last) if ma5 and ma20 and last > 0 else 0.0
    recent_return_rate = ((float(close.iloc[-1]) - float(close.iloc[-6])) / float(close.iloc[-6])) if close is not None and len(close) >= 6 and float(close.iloc[-6]) > 0 else 0.0
    expected_edge = max(ma_gap_rate, recent_return_rate)
    cost = _round_trip_cost_rate(quote)
    edge_allowed = latest.get("signal") == "BUY" and expected_edge >= max(MIN_EXPECTED_EDGE_RATE, cost * 1.25)
    return {
        "symbol": symbol,
        "market": market,
        "strategy": "ma_cross",
        "signal": latest.get("signal"),
        "reason": latest.get("reason") or "MA_CROSS_HOLD",
        "entry_price": quote.get("best_ask"),
        "expected_edge_rate": expected_edge,
        "min_expected_edge_rate": MIN_EXPECTED_EDGE_RATE,
        "estimated_round_trip_cost_rate": cost,
        "edge_allowed": edge_allowed,
        "blocker": None if edge_allowed else "BLOCKED_EXPECTED_EDGE_BELOW_COST",
        "notional_krw": notional,
    }


async def _smart_autonomous_decision(symbol: str, market: str, candles: list[dict], quote: dict, notional: float) -> dict:
    latest = latest_completed_candle(candles, 1) or (candles[-1] if candles else {})
    session = {"id": 0, "exchange": "bithumb", "market": market, "candidate_strategy_id": 0, "strategy_name": "smart_autonomous"}
    candidate = {"id": 0, "market": market, "strategy": "smart_autonomous", "unit": 1, "name": "Controlled Smart Autonomous"}
    legacy_signal = {"signal": "HOLD", "reason": "controlled_smart_autonomous_preview", "price": latest.get("trade_price")}
    snapshot = None
    try:
        snapshot = record_shadow_decision(session=session, candidate=candidate, candles=candles, candle=latest, legacy_signal=legacy_signal)
    except Exception:
        snapshot = None
    frame = candles_to_frame(candles)
    close = frame["close"].astype(float) if not frame.empty else None
    recent_return_rate = ((float(close.iloc[-1]) - float(close.iloc[-13])) / float(close.iloc[-13])) if close is not None and len(close) >= 13 and float(close.iloc[-13]) > 0 else 0.0
    confidence = _float((snapshot or {}).get("confidence_score"))
    action_hint = str((snapshot or {}).get("action_hint") or "WAIT")
    expected_edge = max(recent_return_rate, confidence / 10000.0)
    cost = _round_trip_cost_rate(quote)
    edge_allowed = action_hint in {"BUY_MORE", "ENTER", "INCREASE"} and expected_edge >= max(MIN_EXPECTED_EDGE_RATE, cost * 1.25)
    return {
        "symbol": symbol,
        "market": market,
        "strategy": "smart_autonomous",
        "signal": "BUY" if action_hint in {"BUY_MORE", "ENTER", "INCREASE"} else "HOLD",
        "reason": f"smart_action={action_hint}",
        "entry_price": quote.get("best_ask"),
        "expected_edge_rate": expected_edge,
        "min_expected_edge_rate": MIN_EXPECTED_EDGE_RATE,
        "estimated_round_trip_cost_rate": cost,
        "edge_allowed": edge_allowed,
        "blocker": None if edge_allowed else "BLOCKED_EXPECTED_EDGE_BELOW_COST",
        "confidence_score": confidence,
        "notional_krw": notional,
    }


def _latest_ma_signal(candles: list[dict]) -> dict:
    latest = latest_completed_candle(candles, 1) or (candles[-1] if candles else None)
    if not latest:
        return {"signal": "HOLD", "reason": "NO_COMPLETED_CANDLE"}
    frame = candles_to_frame(candles)
    result = apply_strategy("ma_cross", frame, {"short_window": 5, "long_window": 20})
    row = result[result["timestampUtc"] == latest["candle_time_utc"]]
    if row.empty:
        row = result.tail(1)
    item = row.iloc[-1]
    return {"signal": str(item.get("signal") or "HOLD"), "reason": str(item.get("reason") or "")}


async def _submit_and_wait_controlled(
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
    strategy_name: str,
    signal_reason: str,
) -> dict:
    request_id = f"{run_id}-{side.lower()}-{order_index}"
    client_order_id = f"{run_id[:24]}-{side.lower()}-{order_index}"[:36]
    symbol = market.split("-")[-1]
    idempotency_key = f"controlled-auto:{exchange}:{symbol}:{run_id}:{side}:{order_index}"
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
            "risk_result": "CONTROLLED_AUTO_LIVE_PREPARED",
            "order_preview_payload": {"controlled_auto_live_id": run_id, "order_index": order_index, "max_orders": MAX_ORDERS},
            "exchange_request_payload_masked": masked_exchange_request(order),
            "exchange_response_payload": {},
            "status": "PREVIEWED",
            "order_purpose": "CONTROLLED_AUTO_LIVE",
            "strategy_name": strategy_name,
            "signal_reason": signal_reason,
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
            "risk_result": "CONTROLLED_AUTO_LIVE_SUBMITTED",
            "exchange_response_payload": response,
            "order_uuid": order_uuid,
            "error_message": None,
        },
    )
    result = {"requested": True, "filled": False, "request_id": request_id, "client_order_id": client_order_id, "order_uuid": order_uuid}
    deadline = asyncio.get_running_loop().time() + 300
    latest_log = get_live_order_log(request_id)
    reconciled = None
    while asyncio.get_running_loop().time() < deadline:
        if latest_log and order_uuid:
            reconciled = await reconcile_order_log(latest_log, source="CONTROLLED_AUTO_LIVE_STATUS_RECHECK")
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
            update_live_order_log(request_id, {"status": "CANCELED", "risk_result": "CONTROLLED_AUTO_LIVE_TIMEOUT_CANCELED", "exchange_response_payload": cancel_response})
        except Exception as exc:
            update_live_order_log(request_id, {"risk_result": "CONTROLLED_AUTO_LIVE_TIMEOUT_CANCEL_FAILED", "error_message": str(exc)[:240]})
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


async def _finalize_after_orders(
    report: dict,
    exchange: str,
    order_logs: list[dict],
    status: str,
    reasons: list[str],
    before_equity: float | None,
    controlled_gate: dict | None,
) -> dict:
    if order_logs:
        ledger = _persist_limited_ledger(exchange, order_logs)
        report.update(ledger)
    after_equity = await _current_equity(exchange)
    report["equity_after"] = after_equity
    report["equity_diff_after"] = 0.0 if after_equity is not None else None
    after_epoch = build_current_epoch_diagnostics(exchange=exchange, current_equity=after_equity)
    report["current_epoch_pnl_after"] = after_epoch.get("current_epoch_total_pnl")
    report["current_epoch_accounting_pending_count"] = int(after_epoch.get("current_epoch_accounting_pending_count") or 0)
    report["current_epoch_accounting_failed_count"] = int(after_epoch.get("current_epoch_accounting_failed_count") or 0)
    report["runtime_seconds"] = max(0, int((_parse_utc(_utc_now()) - _parse_utc(str(report["started_at_utc"]))).total_seconds()))
    status2, fail_reasons = _pass_fail(report)
    final_status = status if reasons else status2
    return _finalize(report, final_status, [*reasons, *fail_reasons], controlled_gate=controlled_gate)


def _apply_realized_position_pnl(report: dict, position: dict) -> None:
    buy_value = _float(position.get("buy_value"))
    sell_value = _float(position.get("sell_value"))
    buy_fee = _float(position.get("buy_fee"))
    sell_fee = _float(position.get("sell_fee"))
    qty = min(_float(position.get("quantity")), _float(position.get("quantity")))
    gross = sell_value - buy_value
    total_fee = buy_fee + sell_fee
    net = gross - total_fee
    spread = abs(_float(position.get("buy_price")) - _float(position.get("sell_price"))) * qty
    report["gross_pnl"] = _float(report.get("gross_pnl")) + gross
    report["total_fee"] = _float(report.get("total_fee")) + total_fee
    report["net_pnl_after_fee"] = _float(report.get("net_pnl_after_fee")) + net
    report["run_pnl"] = report["net_pnl_after_fee"]
    report["spread_slippage_estimate"] = _float(report.get("spread_slippage_estimate")) + spread


def _merge_order_result(report: dict, result: dict, *, prefix: str) -> None:
    report["order_count"] = int(report.get("order_count") or 0) + (1 if result.get("requested") else 0)
    if prefix == "buy" and result.get("filled"):
        report["buy_filled_count"] = int(report.get("buy_filled_count") or 0) + 1
    if prefix == "sell" and result.get("filled"):
        report["sell_filled_count"] = int(report.get("sell_filled_count") or 0) + 1
    log = result.get("order_log") or {}
    if log.get("market"):
        symbol = str(log["market"]).split("-")[-1]
        if symbol not in report["used_symbols"]:
            report["used_symbols"].append(symbol)
    if log.get("strategy_name") and log["strategy_name"] not in report["used_strategies"]:
        report["used_strategies"].append(log["strategy_name"])
    if result.get("order_uuid"):
        report["exchange_order_uuid_list"].append(result["order_uuid"])
    if result.get("client_order_id"):
        report["client_order_id_list"].append(result["client_order_id"])


def _pass_fail(report: dict) -> tuple[str, list[str]]:
    reasons = []
    if int(report.get("order_count") or 0) > MAX_ORDERS:
        reasons.append("CONTROLLED_AUTO_MAX_ORDERS_EXCEEDED")
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


def _finalize(report: dict, status: str, reasons: list[str], *, controlled_gate: dict | None = None) -> dict:
    report["controlled_auto_live_status"] = status
    report["completed_at_utc"] = _utc_now()
    report["pass_fail_reasons"] = reasons
    if controlled_gate is not None:
        report["controlled_auto_live_gate"] = controlled_gate
    runtime = load_runtime_lock("auto-trading")
    report["final_runtime_status"] = str((runtime or {}).get("status") or "UNKNOWN").upper()
    return {key: value for key, value in report.items() if not key.startswith("_")}


async def _open_order_blocker(exchange: str, symbols: list[str]) -> str | None:
    if load_unresolved_live_order_logs_for_exchange(exchange):
        return "DB_UNRESOLVED_OPEN_ORDER_EXISTS"
    broker = get_live_broker(exchange)
    for symbol in symbols:
        market = f"KRW-{symbol}"
        try:
            response = await broker.list_open_orders(market)
        except Exception:
            return "EXCHANGE_OPEN_ORDER_AUDIT_FAILED"
        orders = response.get("orders", []) if isinstance(response, dict) else []
        if isinstance(orders, dict):
            orders = [orders]
        if orders:
            return "EXCHANGE_OPEN_ORDER_EXISTS"
    return None


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


def _round_trip_cost_rate(quote: dict) -> float:
    bid = _float(quote.get("best_bid"))
    ask = _float(quote.get("best_ask"))
    mid = (bid + ask) / 2 if bid > 0 and ask > 0 else ask
    spread_rate = ((ask - bid) / mid) if mid > 0 else 0.0
    return spread_rate + (2 * LiveTradingConfig.for_exchange("bithumb").fee_rate)


def _ledger_rows_for_order_uuids(order_uuids: list[str]) -> list[dict]:
    if not order_uuids:
        return []
    with get_connection() as conn:
        placeholders = ",".join("?" for _ in order_uuids)
        return [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT *
                FROM exchange_fills_ledger
                WHERE exchange_order_uuid IN ({placeholders})
                ORDER BY executed_at_utc, id
                """,
                order_uuids,
            ).fetchall()
        ]


def _decode_json_fields(row: dict) -> dict:
    for key in ("order_preview_payload", "exchange_request_payload_masked", "exchange_response_payload"):
        value = row.get(key)
        if isinstance(value, str):
            try:
                row[key] = json.loads(value or "{}")
            except json.JSONDecodeError:
                row[key] = {}
    return row


def _normalize_candle(candle: dict) -> dict:
    candle_time = str(candle.get("candle_time_utc") or candle.get("candle_date_time_utc") or "")
    if candle_time and not candle_time.endswith("Z") and "+" not in candle_time[-6:]:
        candle_time = f"{candle_time}Z"
    return {
        **candle,
        "candle_time_utc": candle_time,
        "opening_price": candle.get("opening_price") or candle.get("open") or 0,
        "high_price": candle.get("high_price") or candle.get("high") or 0,
        "low_price": candle.get("low_price") or candle.get("low") or 0,
        "trade_price": candle.get("trade_price") or candle.get("close") or 0,
        "candle_acc_trade_volume": candle.get("candle_acc_trade_volume") or candle.get("volume") or 0,
    }


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
