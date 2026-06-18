from __future__ import annotations

from statistics import mean
from typing import Any

from app.database import get_connection, load_decision_snapshots


IGNORED_SHADOW_BLOCKERS = {
    "SMART_SHADOW_MODE",
    "SMART_MIN_REBALANCE_DELTA",
    "SMART_ORDER_DELTA_CAPPED_BY_MAX_TOTAL_EXPOSURE",
}


def build_shadow_report(market: str = "KRW-BTC", limit: int = 100, horizon_candles: int = 3) -> dict:
    decisions = load_decision_snapshots(market=market, limit=limit)
    rows = [_decision_row(decision, horizon_candles) for decision in decisions]
    evaluated = [row for row in rows if row.get("markout_pct") is not None and row.get("direction") in {"LONG", "SHORT"}]
    wins = [row for row in evaluated if row.get("outcome") == "FAVORABLE"]
    hard_blocked = [row for row in rows if row["hard_blockers"]]
    policy_blocked = [row for row in rows if any(str(blocker).startswith("SMART_POLICY_") or str(blocker).startswith("SMART_MAX_TOTAL_EXPOSURE") or str(blocker).startswith("SMART_DAILY_LOSS") for blocker in row["blockers"])]
    actionable = [row for row in rows if row["direction"] in {"LONG", "SHORT"}]
    confidence_values = [float(row["confidence_score"]) for row in rows if row.get("confidence_score") is not None]
    risk_values = [float(row["risk_score"]) for row in rows if row.get("risk_score") is not None]
    markouts = [float(row["markout_pct"]) for row in evaluated if row.get("markout_pct") is not None]
    total = len(rows)
    hard_block_rate = len(hard_blocked) / total if total else 0.0
    policy_block_rate = len(policy_blocked) / total if total else 0.0
    directional_win_rate = len(wins) / len(evaluated) if evaluated else 0.0
    readiness_score = _readiness_score(
        total=total,
        hard_block_rate=hard_block_rate,
        policy_block_rate=policy_block_rate,
        directional_win_rate=directional_win_rate,
        average_risk=mean(risk_values) if risk_values else 100.0,
    )
    rehearsal = _rehearsal_summary(market)
    recommendation = _recommendation(total, readiness_score, directional_win_rate, hard_block_rate, rehearsal)
    return {
        "market": market,
        "limit": limit,
        "horizon_candles": horizon_candles,
        "summary": {
            "decision_count": total,
            "intent_count": sum(1 for row in rows if row.get("intent_id") is not None),
            "actionable_count": len(actionable),
            "evaluated_count": len(evaluated),
            "favorable_count": len(wins),
            "directional_win_rate": directional_win_rate * 100,
            "average_confidence_score": mean(confidence_values) if confidence_values else 0.0,
            "average_risk_score": mean(risk_values) if risk_values else 0.0,
            "average_markout_pct": mean(markouts) if markouts else None,
            "hard_block_count": len(hard_blocked),
            "hard_block_rate": hard_block_rate * 100,
            "policy_block_count": len(policy_blocked),
            "policy_block_rate": policy_block_rate * 100,
            "readiness_score": readiness_score,
            "recommendation": recommendation,
            "rehearsal": rehearsal,
        },
        "action_counts": _count_by(rows, "action_hint"),
        "direction_counts": _count_by(rows, "direction"),
        "market_regime_counts": _count_by(rows, "market_regime"),
        "blocker_counts": _blocker_counts(rows),
        "recent_rows": rows[:20],
    }


def _decision_row(decision: dict, horizon_candles: int) -> dict:
    intent = (decision.get("order_intents") or [None])[0] or {}
    blockers = list(dict.fromkeys([*(decision.get("blockers") or []), *(intent.get("blockers") or [])]))
    hard_blockers = [blocker for blocker in blockers if blocker not in IGNORED_SHADOW_BLOCKERS]
    direction = _direction(decision, intent)
    current_price = _float((decision.get("raw_features") or {}).get("last_price")) or _float(intent.get("limit_price"))
    future = _future_candle(
        market=str(decision.get("market") or "KRW-BTC"),
        timeframe=str(decision.get("timeframe") or ""),
        candle_time_utc=decision.get("candle_time_utc"),
        horizon_candles=horizon_candles,
    )
    future_price = _float(future.get("trade_price")) if future else None
    markout_pct = _markout_pct(current_price, future_price, direction)
    return {
        "decision_id": decision.get("id"),
        "intent_id": intent.get("id"),
        "decided_at": decision.get("decided_at") or decision.get("created_at"),
        "candle_time_utc": decision.get("candle_time_utc"),
        "future_candle_time_utc": future.get("candle_time_utc") if future else None,
        "market": decision.get("market"),
        "timeframe": decision.get("timeframe"),
        "market_regime": decision.get("market_regime"),
        "legacy_signal": decision.get("legacy_signal"),
        "action_hint": decision.get("action_hint"),
        "direction": direction,
        "intent_status": intent.get("status"),
        "intent_side": intent.get("side"),
        "promotion_status": intent.get("promotion_status"),
        "promotion_blockers": intent.get("promotion_blockers") or [],
        "pilot_order_cap_krw": intent.get("pilot_order_cap_krw"),
        "delta_value_krw": intent.get("delta_value_krw"),
        "target_value_krw": intent.get("target_value_krw"),
        "current_exposure_pct": decision.get("current_exposure_pct"),
        "target_exposure_pct": decision.get("target_exposure_pct"),
        "confidence_score": decision.get("confidence_score"),
        "risk_score": decision.get("risk_score"),
        "current_price": current_price,
        "future_price": future_price,
        "markout_pct": markout_pct,
        "outcome": _outcome(markout_pct, direction),
        "blockers": blockers,
        "hard_blockers": hard_blockers,
        "one_line_summary": decision.get("one_line_summary"),
    }


def _future_candle(*, market: str, timeframe: str, candle_time_utc: str | None, horizon_candles: int) -> dict | None:
    if not candle_time_utc:
        return None
    unit = _timeframe_unit(timeframe)
    if unit <= 0:
        return None
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT candle_time_utc, trade_price
            FROM candles
            WHERE market = ?
              AND unit = ?
              AND candle_time_utc > ?
            ORDER BY candle_time_utc ASC
            LIMIT 1 OFFSET ?
            """,
            (market, unit, candle_time_utc, max(horizon_candles - 1, 0)),
        ).fetchone()
    return dict(row) if row else None


def _timeframe_unit(value: str) -> int:
    raw = str(value or "").lower().replace("m", "").strip()
    try:
        return int(raw)
    except ValueError:
        return 0


def _direction(decision: dict, intent: dict) -> str:
    side = str(intent.get("side") or "").upper()
    action = str(decision.get("action_hint") or "").upper()
    if side in {"BID", "BUY"} or action == "BUY_MORE":
        return "LONG"
    if side in {"ASK", "SELL"} or action in {"REDUCE", "EXIT"}:
        return "SHORT"
    return "HOLD"


def _markout_pct(current_price: float, future_price: float | None, direction: str) -> float | None:
    if current_price <= 0 or future_price is None or future_price <= 0 or direction not in {"LONG", "SHORT"}:
        return None
    raw = (future_price - current_price) / current_price * 100
    return raw if direction == "LONG" else -raw


def _outcome(markout_pct: float | None, direction: str) -> str:
    if direction == "HOLD":
        return "NOT_ACTIONABLE"
    if markout_pct is None:
        return "PENDING"
    if markout_pct > 0:
        return "FAVORABLE"
    if markout_pct < 0:
        return "UNFAVORABLE"
    return "FLAT"


def _readiness_score(*, total: int, hard_block_rate: float, policy_block_rate: float, directional_win_rate: float, average_risk: float) -> float:
    if total == 0:
        return 0.0
    score = 55.0
    score += min(total, 100) * 0.12
    score += (directional_win_rate - 0.5) * 40
    score -= hard_block_rate * 45
    score -= policy_block_rate * 20
    score -= max(average_risk - 50, 0) * 0.25
    return round(max(min(score, 100.0), 0.0), 2)


def _recommendation(total: int, readiness_score: float, directional_win_rate: float, hard_block_rate: float, rehearsal: dict | None = None) -> str:
    latest = (rehearsal or {}).get("latest_order") or {}
    if latest.get("status") in {"BLOCKED", "FAILED"} or latest.get("risk_result") not in {None, "ALLOWED"}:
        return "REHEARSAL_REVIEW_REQUIRED"
    if latest.get("status") in {"SUBMITTED", "WAITING", "PARTIALLY_FILLED"}:
        return "REHEARSAL_REVIEW_REQUIRED"
    if total < 20:
        return "MORE_SHADOW_DATA_REQUIRED"
    if hard_block_rate >= 0.25:
        return "FIX_BLOCKERS_BEFORE_PROMOTION"
    if readiness_score >= 70 and directional_win_rate >= 0.55:
        return "READY_FOR_LIMITED_PILOT_REVIEW"
    return "CONTINUE_SHADOW_MODE"


def _rehearsal_summary(market: str) -> dict:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT request_id, status, risk_result, side, price, volume, amount_krw,
                   filled_amount_krw, paid_fee, error_message, created_at, updated_at
            FROM live_order_logs
            WHERE market = ?
              AND request_id LIKE 'smart-rehearsal-%'
            ORDER BY created_at DESC, id DESC
            LIMIT 20
            """,
            (market,),
        ).fetchall()
    orders = [dict(row) for row in rows]
    latest = orders[0] if orders else None
    submitted = [row for row in orders if row.get("status") in {"SUBMITTED", "WAITING", "PARTIALLY_FILLED", "FILLED", "CANCELED"}]
    blocked = [row for row in orders if row.get("status") in {"BLOCKED", "FAILED"}]
    return {
        "order_count": len(orders),
        "submitted_count": len(submitted),
        "blocked_count": len(blocked),
        "latest_order": latest,
        "requires_review": bool(latest and (latest.get("status") in {"BLOCKED", "FAILED", "SUBMITTED", "WAITING", "PARTIALLY_FILLED"} or latest.get("risk_result") not in {None, "ALLOWED"})),
        "recent_orders": orders[:5],
    }


def _count_by(rows: list[dict], key: str) -> dict:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "-")
        counts[value] = counts.get(value, 0) + 1
    return counts


def _blocker_counts(rows: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for row in rows:
        for blocker in row.get("blockers") or []:
            counts[str(blocker)] = counts.get(str(blocker), 0) + 1
    return counts


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
