from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.database import count_live_strategy_orders_today, get_connection, load_bot_operation_policy, load_latest_decision_snapshot, load_latest_smart_rehearsal_review
from app.live_broker import is_emergency_stopped
from app.risk_manager import compute_risk_state
from app.shadow_report import build_shadow_report
from app.smart_promotion import READY_RECOMMENDATION, evaluate_rehearsal_preview, smart_engine_live_mode

NON_REVIEWABLE_REHEARSAL_RISK_RESULTS = {
    "BLOCKED_MIN_ORDER_AMOUNT",
}


def build_limited_readiness(
    market: str = "KRW-BTC",
    exchange: str = "bithumb",
    *,
    decision: dict | None = None,
    report: dict | None = None,
    policy: dict | None = None,
    risk_state: dict | None = None,
    daily_smart_order_count: int | None = None,
    emergency_stopped: bool | None = None,
    live_mode: str | None = None,
    now_utc: datetime | None = None,
) -> dict:
    decision = decision if decision is not None else load_latest_decision_snapshot(market)
    report = report if report is not None else build_shadow_report(market=market, limit=100, horizon_candles=3)
    policy = policy if policy is not None else load_bot_operation_policy(market)
    risk_state = risk_state if risk_state is not None else compute_risk_state(exchange, market)
    live_mode = live_mode or smart_engine_live_mode()
    emergency_stopped = is_emergency_stopped() if emergency_stopped is None else bool(emergency_stopped)
    daily_count = count_live_strategy_orders_today(exchange, market) if daily_smart_order_count is None else int(daily_smart_order_count)
    intent = (decision.get("order_intents") or [None])[0] if decision else None
    summary = report.get("summary", {}) if report else {}
    recommendation = summary.get("recommendation")
    requested_order = abs(_float((intent or {}).get("delta_value_krw")))
    risk_score = _float((decision or {}).get("risk_score"))
    latest_rehearsal_order = _latest_rehearsal_order(exchange, market)
    rehearsal = evaluate_rehearsal_preview(requested_order_krw=requested_order, risk_score=risk_score, daily_smart_order_count=daily_count, now_utc=now_utc)
    rehearsal_blockers = list(rehearsal.get("blockers") or [])
    latest_review = latest_rehearsal_order.get("review") if latest_rehearsal_order else None
    if (
        latest_rehearsal_order
        and _reviewable_rehearsal_order(latest_rehearsal_order)
        and latest_rehearsal_order.get("status") in {"FAILED", "BLOCKED"}
        and not (latest_review and latest_review.get("is_active"))
    ):
        rehearsal_blockers.append("SMART_REHEARSAL_REVIEW_REQUIRED")
    checks = [
        _check("latest_decision", "Latest Smart decision", bool(decision), "Latest decision snapshot exists." if decision else "Run an auto-trading tick to create a decision snapshot."),
        _check(
            "latest_order_intent",
            "Smart order intent",
            bool(intent and intent.get("side") in {"BID", "BUY", "ASK", "SELL"} and requested_order > 0),
            f"{intent.get('side')} candidate {requested_order:,.0f} KRW" if intent else "No promotable Smart order intent exists.",
        ),
        _check("policy_auto_trading", "Policy auto trading", bool(policy.get("auto_trading_enabled")), "Policy auto trading is ON." if policy.get("auto_trading_enabled") else "Turn policy auto trading ON in operation settings."),
        _check("emergency_stop", "Emergency Stop", not emergency_stopped, "Emergency Stop is OFF." if not emergency_stopped else "Emergency Stop is ON."),
        _check("risk_state", "Risk state", risk_state.get("status") not in {"BLOCKED", "EMERGENCY_STOPPED"}, f"Current risk state: {risk_state.get('status') or '-'}"),
        _check("shadow_report", "Shadow report", recommendation == READY_RECOMMENDATION, f"Shadow recommendation: {recommendation or '-'}"),
        _check("rehearsal_gate", "Small-order rehearsal gate", bool(rehearsal.get("allowed")), "Rehearsal rules passed." if rehearsal.get("allowed") else ", ".join(rehearsal.get("blockers") or ["No rehearsal blocker detail."])),
        _check("runtime_risk_preview", "Runtime risk preview", True, "check_order_risk() is forced again immediately before submission.", required=False, status="warn"),
    ]
    can_enable = all(item["status"] == "pass" for item in checks if item.get("required", True))
    next_action = _next_action(checks, live_mode, can_enable)
    return {
        "status": "READY_TO_ENABLE_LIMITED" if can_enable else "BLOCKED",
        "can_enable_limited": can_enable,
        "live_mode": live_mode,
        "market": market,
        "exchange": exchange,
        "checked_at": _utc_now(),
        "recommended_next_action": next_action,
        "next_required_operator_action": next_action,
        "checks": checks,
        "rehearsal": rehearsal,
        "can_run_rehearsal": can_enable and live_mode == "limited",
        "rehearsal_blockers": list(dict.fromkeys(rehearsal_blockers)),
        "latest_rehearsal_order": latest_rehearsal_order,
        "external_provider_health": _external_provider_health(decision),
        "daily_smart_order_count": daily_count,
        "shadow_recommendation": recommendation,
        "latest_intent_summary": _intent_summary(intent),
    }


def _check(check_id: str, label: str, passed: bool, detail: str, *, required: bool = True, status: str | None = None) -> dict:
    return {"id": check_id, "label": label, "status": status or ("pass" if passed else "block"), "required": required, "detail": detail}


def _next_action(checks: list[dict], live_mode: str, can_enable: bool) -> str:
    if can_enable and live_mode == "limited":
        return "limited mode is ON. Review the first rehearsal order in live order logs and Shadow report."
    if can_enable:
        return "Before enabling limited mode, verify exchange balance and open orders one last time."
    for item in checks:
        if item.get("required", True) and item.get("status") == "block":
            return str(item.get("detail") or "Resolve blocked checks before enabling limited mode.")
    return "Resolve blocked checks before enabling limited mode."


def _intent_summary(intent: dict | None) -> dict | None:
    if not intent:
        return None
    return {
        "id": intent.get("id"),
        "side": intent.get("side"),
        "status": intent.get("status"),
        "promotion_status": intent.get("promotion_status"),
        "delta_value_krw": intent.get("delta_value_krw"),
        "pilot_order_cap_krw": intent.get("pilot_order_cap_krw"),
        "promotion_blockers": intent.get("promotion_blockers", []),
    }


def _external_provider_health(decision: dict | None) -> dict:
    providers = (((decision or {}).get("external_factors") or {}).get("providers") or {})
    return {
        key: {
            "stale": bool(value.get("stale")) if isinstance(value, dict) else True,
            "severity": value.get("severity") if isinstance(value, dict) else None,
            "source": value.get("source") if isinstance(value, dict) else None,
            "reason": value.get("reason") if isinstance(value, dict) else "provider missing",
        }
        for key, value in providers.items()
    }


def _latest_rehearsal_order(exchange: str, market: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT request_id, exchange, status, risk_result, side, price, volume, amount_krw,
                   order_uuid, error_message, created_at, updated_at
            FROM live_order_logs
            WHERE exchange = ?
              AND market = ?
              AND request_id LIKE 'smart-rehearsal-%'
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (exchange, market),
        ).fetchone()
    if row is None:
        return None
    order = dict(row)
    review = load_latest_smart_rehearsal_review(exchange=exchange, market=market)
    order["review"] = review
    order["review_status"] = review.get("decision") if review else None
    order["review_active"] = bool(review and review.get("is_active"))
    order["review_expires_at"] = review.get("expires_at") if review else None
    order["reviewable"] = _reviewable_rehearsal_order(order)
    return order


def _reviewable_rehearsal_order(order: dict | None) -> bool:
    if not order:
        return False
    if str(order.get("risk_result") or "") in NON_REVIEWABLE_REHEARSAL_RISK_RESULTS:
        return False
    amount = _float(order.get("amount_krw"))
    if 0 < amount < 5_000:
        return False
    return True


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
