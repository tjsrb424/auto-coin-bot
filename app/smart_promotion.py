from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from typing import Any

from app.risk_manager import RiskConfig


READY_RECOMMENDATION = "READY_FOR_LIMITED_PILOT_REVIEW"
LIVE_MODE_SHADOW = "shadow"
LIVE_MODE_LIMITED = "limited"


def smart_engine_live_mode() -> str:
    value = os.getenv("SMART_ENGINE_LIVE_MODE", LIVE_MODE_SHADOW).strip().lower()
    return value if value in {LIVE_MODE_SHADOW, LIVE_MODE_LIMITED} else LIVE_MODE_SHADOW


def evaluate_rehearsal_preview(
    *,
    requested_order_krw: float,
    risk_score: float,
    daily_smart_order_count: int = 0,
    now_utc: datetime | None = None,
) -> dict:
    return _evaluate_rehearsal(
        requested_order_krw=requested_order_krw,
        risk_score=risk_score,
        daily_smart_order_count=daily_smart_order_count,
        now_utc=now_utc,
    )


def evaluate_promotion(
    *,
    intent: dict,
    snapshot: dict,
    policy: dict,
    risk_preview: dict | None = None,
    shadow_recommendation: str | None = None,
    available_krw: float | None = None,
    daily_smart_order_count: int = 0,
    risk_score: float | None = None,
    now_utc: datetime | None = None,
) -> dict:
    mode = smart_engine_live_mode()
    risk_config = RiskConfig.from_env()
    max_total = _float(policy.get("max_total_exposure_krw"))
    current_value = _float(snapshot.get("current_bot_position_value_krw"))
    current_qty = _float(snapshot.get("current_bot_position_qty"))
    requested = abs(_float(intent.get("delta_value_krw")))
    requested_qty = abs(_float(intent.get("target_qty")))
    side = str(intent.get("side") or "").upper()
    is_buy = side in {"BID", "BUY"}
    is_sell = side in {"ASK", "SELL"}
    remaining = max(max_total - current_value, 0.0)
    cap_candidates = [max_total * 0.2, _float(risk_config.max_order_krw), remaining]
    if available_krw is not None:
        cap_candidates.append(max(_float(available_krw), 0.0))
    pilot_cap = max(min([value for value in cap_candidates if value >= 0], default=0.0), 0.0) if is_buy else current_value
    blockers: list[str] = []
    if mode != LIVE_MODE_LIMITED:
        blockers.append("SMART_LIVE_MODE_SHADOW")
    if not policy.get("auto_trading_enabled"):
        blockers.append("SMART_POLICY_AUTO_TRADING_DISABLED")
    if not is_buy and not is_sell:
        blockers.append("SMART_LIMITED_SIDE_UNSUPPORTED")
    if shadow_recommendation != READY_RECOMMENDATION:
        blockers.append("SMART_SHADOW_REPORT_NOT_READY")
    if risk_preview is None:
        blockers.append("SMART_RISK_PREVIEW_MISSING")
    elif not risk_preview.get("allowed"):
        blockers.append(str(risk_preview.get("risk_result") or risk_preview.get("block_code") or "SMART_RISK_PREVIEW_BLOCKED"))
    if requested <= 0:
        blockers.append("SMART_ORDER_AMOUNT_ZERO")
    if is_buy and requested > pilot_cap:
        blockers.append("SMART_PILOT_ORDER_CAP_EXCEEDED")
    if is_sell and current_qty <= 0:
        blockers.append("SMART_SELL_POSITION_MISSING")
    if is_sell and requested_qty > current_qty + 1e-12:
        blockers.append("SMART_SELL_QTY_EXCEEDS_POSITION")
    rehearsal = _evaluate_rehearsal(requested_order_krw=requested, risk_score=_float(risk_score, _float(snapshot.get("risk_score"))), daily_smart_order_count=daily_smart_order_count, now_utc=now_utc) if is_buy else _sell_rehearsal_preview(requested, requested_qty, current_qty, now_utc)
    if is_buy:
        blockers.extend(rehearsal["blockers"])
    status = "READY_FOR_LIMITED" if mode == LIVE_MODE_LIMITED and not blockers else ("SHADOW_ONLY" if mode != LIVE_MODE_LIMITED else "BLOCKED")
    return {
        "promotion_status": status,
        "promotion_blockers": list(dict.fromkeys(blockers)),
        "pilot_order_cap_krw": pilot_cap,
        "policy_preview": {
            "max_total_exposure_krw": max_total,
            "current_bot_position_value_krw": current_value,
            "remaining_exposure_krw": remaining,
            "requested_order_krw": requested,
            "requested_qty": requested_qty,
            "current_bot_position_qty": current_qty,
            "available_krw_balance": available_krw,
            "rehearsal": rehearsal,
            "promotion_side": "BUY" if is_buy else "SELL" if is_sell else "UNSUPPORTED",
        },
        "risk_preview": risk_preview or {
            "allowed": False,
            "risk_result": "SMART_RISK_PREVIEW_MISSING",
        },
        "live_mode": mode,
    }


def _sell_rehearsal_preview(requested_order_krw: float, requested_qty: float, current_qty: float, now_utc: datetime | None) -> dict:
    now = now_utc or datetime.now(timezone.utc)
    blockers: list[str] = []
    if requested_order_krw <= 0 or requested_qty <= 0:
        blockers.append("SMART_ORDER_AMOUNT_ZERO")
    if current_qty <= 0:
        blockers.append("SMART_SELL_POSITION_MISSING")
    if requested_qty > current_qty + 1e-12:
        blockers.append("SMART_SELL_QTY_EXCEEDS_POSITION")
    return {
        "allowed": len(blockers) == 0,
        "blockers": blockers,
        "requested_qty": requested_qty,
        "current_bot_position_qty": current_qty,
        "checked_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "rules": {"max_sell_qty": current_qty},
    }


def _evaluate_rehearsal(
    *,
    requested_order_krw: float,
    risk_score: float,
    daily_smart_order_count: int,
    now_utc: datetime | None,
) -> dict:
    rules = {
        "max_daily_orders": _int_env("SMART_REHEARSAL_MAX_DAILY_ORDERS", 1),
        "min_order_krw": _float(os.getenv("SMART_REHEARSAL_MIN_ORDER_KRW"), 10_000.0),
        "max_risk_score": _float(os.getenv("SMART_REHEARSAL_MAX_RISK_SCORE"), 60.0),
        "allowed_start_hour_kst": _int_env("SMART_REHEARSAL_ALLOWED_START_HOUR_KST", 9),
        "allowed_end_hour_kst": _int_env("SMART_REHEARSAL_ALLOWED_END_HOUR_KST", 23),
    }
    now = now_utc or datetime.now(timezone.utc)
    kst_now = now.astimezone(timezone(timedelta(hours=9)))
    blockers: list[str] = []
    if rules["max_daily_orders"] >= 0 and daily_smart_order_count >= rules["max_daily_orders"]:
        blockers.append("SMART_REHEARSAL_DAILY_ORDER_LIMIT")
    if requested_order_krw > 0 and requested_order_krw < rules["min_order_krw"]:
        blockers.append("SMART_REHEARSAL_ORDER_TOO_SMALL")
    if risk_score > rules["max_risk_score"]:
        blockers.append("SMART_REHEARSAL_RISK_SCORE_TOO_HIGH")
    if not _within_hour_window(kst_now.hour, rules["allowed_start_hour_kst"], rules["allowed_end_hour_kst"]):
        blockers.append("SMART_REHEARSAL_TIME_WINDOW_CLOSED")
    return {
        "allowed": len(blockers) == 0,
        "blockers": blockers,
        "daily_smart_order_count": daily_smart_order_count,
        "risk_score": risk_score,
        "checked_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "rules": rules,
    }


def _within_hour_window(hour: int, start: int, end: int) -> bool:
    start = max(min(start, 23), 0)
    end = max(min(end, 23), 0)
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
