from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.database import (
    get_connection,
    load_live_position,
    load_trade_outcome_log_by_order_uuid,
    load_trade_outcome_logs,
    load_trade_outcomes_needing_post_fill_updates,
    update_trade_outcome_log,
    upsert_trade_outcome_log,
)

POST_FILL_HORIZONS_MINUTES = (1, 3, 5, 15)


def record_filled_order_outcome(order_log: dict | None, *, position_id: int | None = None) -> dict | None:
    if not order_log:
        return None
    status = str(order_log.get("status") or "").upper()
    if status not in {"FILLED", "PARTIALLY_FILLED"}:
        return None
    order_uuid = str(order_log.get("order_uuid") or "")
    if not order_uuid:
        return None
    filled_volume = _float(order_log.get("executed_volume"))
    filled_amount = _float(order_log.get("filled_amount_krw"))
    filled_price = _filled_price(order_log)
    if filled_volume <= 0 or filled_price <= 0:
        return None

    preview = order_log.get("order_preview_payload") if isinstance(order_log.get("order_preview_payload"), dict) else {}
    policy = preview.get("policy_preview") if isinstance(preview, dict) and isinstance(preview.get("policy_preview"), dict) else {}
    signal = preview.get("signal") if isinstance(preview, dict) and isinstance(preview.get("signal"), dict) else {}
    filled_at = str(order_log.get("updated_at") or order_log.get("created_at") or _utc_now())
    order_price = _float(order_log.get("price"), None)
    slippage_pct = _pct(filled_price - order_price, order_price) if order_price else None
    payload = {
        "order_uuid": order_uuid,
        "request_id": order_log.get("request_id"),
        "live_order_log_id": order_log.get("id"),
        "session_id": order_log.get("session_id"),
        "position_id": position_id or order_log.get("position_id"),
        "exchange": order_log.get("exchange", "bithumb"),
        "market": order_log.get("market", "KRW-BTC"),
        "side": str(order_log.get("side") or "").upper(),
        "order_purpose": order_log.get("order_purpose", "ENTRY"),
        "strategy_name": order_log.get("strategy_name") or preview.get("strategy_name") or "",
        "candidate_strategy_id": order_log.get("candidate_strategy_id"),
        "market_regime": preview.get("market_regime") or policy.get("market_regime") or signal.get("market_regime") or signal.get("regime") or "",
        "action_hint": preview.get("action_hint") or policy.get("action_hint") or signal.get("action_hint") or "",
        "legacy_signal": preview.get("legacy_signal") or signal.get("signal") or "",
        "attack_mode": order_log.get("attack_mode") or preview.get("attack_mode") or policy.get("attack_mode") or "",
        "target_source": order_log.get("target_source") or preview.get("target_source") or policy.get("target_source") or "",
        "entry_or_exit_price": order_price,
        "filled_price": filled_price,
        "filled_volume": filled_volume,
        "filled_amount_krw": filled_amount,
        "fee_krw": _float(order_log.get("paid_fee")),
        "slippage_pct": slippage_pct,
        "spread_pct": _float(preview.get("spread_pct"), None),
        "fill_time_seconds": _seconds_between(order_log.get("created_at"), filled_at),
        "filled_at": filled_at,
        "outcome_status": "PENDING_OUTCOME",
    }
    return upsert_trade_outcome_log(payload)


def refresh_trade_outcome_post_fill_returns(
    *,
    order_uuid: str | None = None,
    now_utc: str | None = None,
    limit: int = 200,
) -> dict:
    outcomes = [load_trade_outcome_log_by_order_uuid(order_uuid)] if order_uuid else load_trade_outcomes_needing_post_fill_updates(limit)
    outcomes = [outcome for outcome in outcomes if outcome]
    updated = 0
    pending = 0
    for outcome in outcomes:
        result = _post_fill_metrics(outcome, now_utc=now_utc)
        if not result:
            pending += 1
            update_trade_outcome_log(str(outcome["order_uuid"]), {"outcome_status": "PENDING_MARKET_DATA"})
            continue
        update_trade_outcome_log(str(outcome["order_uuid"]), result)
        if result.get("outcome_status") == "PENDING_MARKET_DATA":
            pending += 1
        else:
            updated += 1
    _refresh_adaptive_edge_stats(outcomes)
    return {"processed": len(outcomes), "updated": updated, "pending": pending}


def refresh_realized_outcomes_for_position(position_id: int) -> dict:
    position = load_live_position(position_id)
    if not position or str(position.get("status") or "").upper() != "CLOSED":
        return {"updated": 0, "status": "SKIPPED"}
    realized_pnl = _float(position.get("realized_pnl"))
    basis = _float(position.get("entry_amount_krw"))
    realized_return = _pct(realized_pnl, basis) if basis > 0 else None
    holding_minutes = _seconds_between(position.get("opened_at") or position.get("created_at"), position.get("closed_at") or position.get("updated_at"))
    if holding_minutes is not None:
        holding_minutes = holding_minutes / 60.0

    updated = 0
    for outcome in load_trade_outcome_logs(position_id=position_id, limit=500):
        update_trade_outcome_log(
            str(outcome["order_uuid"]),
            {
                "realized_pnl_krw": realized_pnl,
                "realized_return_pct": realized_return,
                "holding_minutes": holding_minutes,
                "outcome_status": "REALIZED",
            },
        )
        updated += 1
    _refresh_adaptive_edge_stats(load_trade_outcome_logs(position_id=position_id, limit=500))
    return {"updated": updated, "status": "REALIZED" if updated else "NO_OUTCOMES"}


def backfill_trade_outcomes_from_filled_orders(*, limit: int = 1000, now_utc: str | None = None) -> dict:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT live.*
            FROM live_order_logs AS live
            WHERE live.status IN ('FILLED', 'PARTIALLY_FILLED')
              AND live.order_uuid IS NOT NULL
              AND live.order_uuid != ''
              AND live.executed_volume > 0
              AND NOT EXISTS (
                  SELECT 1
                  FROM trade_outcome_logs AS outcome
                  WHERE outcome.order_uuid = live.order_uuid
              )
            ORDER BY live.updated_at ASC, live.id ASC
            LIMIT ?
            """,
            (max(int(limit), 1),),
        ).fetchall()
    created = 0
    touched_positions: set[int] = set()
    touched_markets: set[tuple[str, str]] = set()
    seen_order_uuids: set[str] = set()
    for row in rows:
        order_log = dict(row)
        order_uuid = str(order_log.get("order_uuid") or "")
        if not order_uuid or order_uuid in seen_order_uuids:
            continue
        seen_order_uuids.add(order_uuid)
        outcome = record_filled_order_outcome(order_log, position_id=order_log.get("position_id"))
        if not outcome:
            continue
        created += 1
        if outcome.get("position_id"):
            touched_positions.add(int(outcome["position_id"]))
        touched_markets.add((str(outcome.get("exchange") or "bithumb"), str(outcome.get("market") or "KRW-BTC")))

    post_fill = refresh_trade_outcome_post_fill_returns(now_utc=now_utc, limit=max(limit, 1))
    realized = 0
    for position_id in sorted(touched_positions):
        result = refresh_realized_outcomes_for_position(position_id)
        realized += int(result.get("updated") or 0)
    for exchange, market in touched_markets:
        _refresh_adaptive_edge_stats([{"exchange": exchange, "market": market}])
    return {
        "scanned": len(rows),
        "created": created,
        "post_fill": post_fill,
        "realized_updates": realized,
    }


def _refresh_adaptive_edge_stats(outcomes: list[dict]) -> None:
    markets = {(str(outcome.get("exchange") or "bithumb"), str(outcome.get("market") or "KRW-BTC")) for outcome in outcomes if outcome}
    if not markets:
        return
    try:
        from app.adaptive_edge import refresh_adaptive_edge_stats

        for exchange, market in markets:
            refresh_adaptive_edge_stats(exchange=exchange, market=market)
    except Exception:
        return


def _post_fill_metrics(outcome: dict, *, now_utc: str | None = None) -> dict | None:
    filled_at = _parse_utc(outcome.get("filled_at") or outcome.get("created_at"))
    filled_price = _float(outcome.get("filled_price"))
    if not filled_at or filled_price <= 0:
        return None

    now_dt = _parse_utc(now_utc) if now_utc else datetime.now(timezone.utc)
    side = str(outcome.get("side") or "").upper()
    market = str(outcome.get("market") or "KRW-BTC")
    horizon_prices: dict[int, float | None] = {}
    horizon_updates: dict[str, float | str | None] = {}
    for minutes in POST_FILL_HORIZONS_MINUTES:
        if now_dt < filled_at + timedelta(minutes=minutes):
            horizon_prices[minutes] = None
            continue
        candle = _first_candle_at_or_after(market, 1, filled_at + timedelta(minutes=minutes))
        price = _float((candle or {}).get("trade_price"), None)
        horizon_prices[minutes] = price
        horizon_updates[f"post_fill_return_{minutes}m"] = _pct(price - filled_price, filled_price) if price else None

    known_returns = [value for value in horizon_updates.values() if isinstance(value, (int, float))]
    excursion = _excursion_metrics(market=market, side=side, filled_at=filled_at, filled_price=filled_price, now_dt=now_dt)
    updates: dict[str, float | str | None] = {**horizon_updates, **excursion}
    if len(known_returns) < len(POST_FILL_HORIZONS_MINUTES):
        updates["outcome_status"] = "PENDING_MARKET_DATA"
        return updates
    updates["outcome_status"] = "PENDING_REALIZED" if str(outcome.get("order_purpose") or "").upper() != "EXIT" else "POST_FILL_COMPLETE"
    return updates


def _excursion_metrics(*, market: str, side: str, filled_at: datetime, filled_price: float, now_dt: datetime) -> dict:
    end_at = min(filled_at + timedelta(minutes=15), now_dt)
    candles = _candles_between(market, 1, filled_at, end_at)
    prices = [_float(candle.get("trade_price"), None) for candle in candles]
    prices = [price for price in prices if price and price > 0]
    if not prices:
        return {
            "max_favorable_excursion_pct": None,
            "max_adverse_excursion_pct": None,
            "adverse_selection_pct": None,
        }
    raw_returns = [_pct(price - filled_price, filled_price) for price in prices]
    if side == "SELL":
        favorable = max([-value for value in raw_returns], default=0.0)
        adverse = max(raw_returns, default=0.0)
    else:
        favorable = max(raw_returns, default=0.0)
        adverse = max([-value for value in raw_returns], default=0.0)
    favorable = max(favorable, 0.0)
    adverse = max(adverse, 0.0)
    return {
        "max_favorable_excursion_pct": round(favorable, 6),
        "max_adverse_excursion_pct": round(adverse, 6),
        "adverse_selection_pct": round(adverse, 6),
    }


def _first_candle_at_or_after(market: str, unit: int, target: datetime) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM candles
            WHERE market = ?
              AND unit = ?
              AND candle_time_utc >= ?
            ORDER BY candle_time_utc ASC
            LIMIT 1
            """,
            (market, unit, _format_utc(target)),
        ).fetchone()
    return dict(row) if row else None


def _candles_between(market: str, unit: int, start: datetime, end: datetime) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM candles
            WHERE market = ?
              AND unit = ?
              AND candle_time_utc >= ?
              AND candle_time_utc <= ?
            ORDER BY candle_time_utc ASC
            """,
            (market, unit, _format_utc(start), _format_utc(end)),
        ).fetchall()
    return [dict(row) for row in rows]


def _filled_price(order_log: dict) -> float:
    amount = _float(order_log.get("filled_amount_krw"))
    volume = _float(order_log.get("executed_volume"))
    if amount > 0 and volume > 0:
        return amount / volume
    return _float(order_log.get("price"))


def _seconds_between(start: str | None, end: str | None) -> float | None:
    start_dt = _parse_utc(start)
    end_dt = _parse_utc(end)
    if not start_dt or not end_dt:
        return None
    return max((end_dt - start_dt).total_seconds(), 0.0)


def _pct(delta: float, base: float) -> float:
    return round((delta / base) * 100, 6) if base else 0.0


def _parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_now() -> str:
    return _format_utc(datetime.now(timezone.utc))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
