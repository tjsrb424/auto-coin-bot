from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.database import (
    get_connection,
    has_open_live_position_for_strategy,
    insert_risk_log,
    load_candidate_strategy,
    load_global_bot_operation_policy,
    load_latest_risk_state,
    load_open_live_positions,
    load_reconcilable_live_order_logs,
    load_risk_logs,
    market_is_live_allowed,
    upsert_risk_state,
)
from app.live_broker import is_emergency_stopped

POLICY_BLOCK_LABELS = {
    "BLOCKED_POLICY_AUTO_TRADING_DISABLED": "운용정책 OFF로 신규매수 차단",
    "BLOCKED_POLICY_MAX_EXPOSURE_INVALID": "최대 투입 금액 설정 오류",
    "BLOCKED_POLICY_MAX_TOTAL_EXPOSURE": "최대 투입 금액 한도 초과",
    "BLOCKED_POLICY_KRW_BALANCE_INSUFFICIENT": "거래소 KRW 잔고 부족",
    "BLOCKED_POLICY_DAILY_LOSS_LIMIT": "일 손실 한도 도달",
}


@dataclass(frozen=True)
class RiskConfig:
    max_daily_loss_percent: float
    max_daily_loss_krw: float
    account_equity_krw: float
    max_orders_per_day: int
    max_entry_orders_per_day: int
    max_exit_orders_per_day: int
    max_consecutive_losses: int
    consecutive_loss_min_krw: float
    min_cooldown_seconds: int
    block_on_balance_mismatch: bool
    block_on_partial_fill: bool
    block_on_open_order: bool
    block_on_open_position: bool
    max_position_ratio_percent: float
    max_order_krw: float
    volatility_window: int
    volatility_block_percent: float
    min_volume_krw: float
    min_current_1m_volume_krw: float
    min_avg_5m_volume_krw: float
    require_completed_candle: bool
    require_order_chance_success: bool

    @classmethod
    def from_env(cls) -> "RiskConfig":
        return cls(
            max_daily_loss_percent=float(os.getenv("RISK_MAX_DAILY_LOSS_PERCENT", "20")),
            max_daily_loss_krw=float(os.getenv("RISK_MAX_DAILY_LOSS_KRW", "10000")),
            account_equity_krw=float(os.getenv("RISK_ACCOUNT_EQUITY_KRW", "300000")),
            max_orders_per_day=int(os.getenv("RISK_MAX_ORDERS_PER_DAY", "3")),
            max_entry_orders_per_day=int(os.getenv("RISK_MAX_ENTRY_ORDERS_PER_DAY", "2")),
            max_exit_orders_per_day=int(os.getenv("RISK_MAX_EXIT_ORDERS_PER_DAY", "3")),
            max_consecutive_losses=int(os.getenv("RISK_MAX_CONSECUTIVE_LOSSES", "4")),
            consecutive_loss_min_krw=float(os.getenv("RISK_CONSECUTIVE_LOSS_MIN_KRW", "500")),
            min_cooldown_seconds=int(os.getenv("RISK_MIN_COOLDOWN_SECONDS", "1800")),
            block_on_balance_mismatch=os.getenv("RISK_BLOCK_ON_BALANCE_MISMATCH", "true").lower() == "true",
            block_on_partial_fill=os.getenv("RISK_BLOCK_ON_PARTIAL_FILL", "true").lower() == "true",
            block_on_open_order=os.getenv("RISK_BLOCK_ON_OPEN_ORDER", "true").lower() == "true",
            block_on_open_position=os.getenv("RISK_BLOCK_ON_OPEN_POSITION", "true").lower() == "true",
            max_position_ratio_percent=float(os.getenv("RISK_MAX_POSITION_RATIO_PERCENT", "20")),
            max_order_krw=float(os.getenv("RISK_MAX_ORDER_KRW", "30000")),
            volatility_window=int(os.getenv("RISK_VOLATILITY_WINDOW", "5")),
            volatility_block_percent=float(os.getenv("RISK_VOLATILITY_BLOCK_PERCENT", "2")),
            min_volume_krw=float(os.getenv("RISK_MIN_VOLUME_KRW", "0")),
            min_current_1m_volume_krw=float(os.getenv("RISK_MIN_CURRENT_1M_VOLUME_KRW", "30000000")),
            min_avg_5m_volume_krw=float(os.getenv("RISK_MIN_AVG_5M_VOLUME_KRW", "50000000")),
            require_completed_candle=os.getenv("RISK_REQUIRE_COMPLETED_CANDLE", "true").lower() == "true",
            require_order_chance_success=os.getenv("RISK_REQUIRE_ORDER_CHANCE_SUCCESS", "true").lower() == "true",
        )


def kst_date(value: datetime | None = None) -> str:
    kst = timezone(timedelta(hours=9))
    return (value or datetime.now(timezone.utc)).astimezone(kst).date().isoformat()


def get_risk_dashboard(exchange: str = "bithumb", market: str = "KRW-BTC") -> dict:
    state = compute_risk_state(exchange, market)
    logs = [enrich_policy_block_log(log) for log in load_risk_logs(50, exchange, market)]
    policy_block_logs = [log for log in logs if log.get("policy_block_detail")]
    return {
        "risk_state": state,
        "risk_logs": logs,
        "latest_policy_block": policy_block_logs[0] if policy_block_logs else None,
        "policy_block_logs": policy_block_logs,
        "config": RiskConfig.from_env().__dict__,
    }


def is_policy_block_code(value: str | None) -> bool:
    return str(value or "").upper() in POLICY_BLOCK_LABELS


def enrich_policy_block_log(log: dict) -> dict:
    detail = policy_block_detail_from_log(log)
    if detail is None:
        return log
    return {
        **log,
        "policy_block_detail": detail,
        "policy_block_summary": detail["summary"],
    }


def policy_block_detail_from_log(log: dict) -> dict | None:
    code = str(log.get("block_code") or "").upper()
    if not is_policy_block_code(code):
        return None
    checks = log.get("checks") or {}
    policy_check = checks.get("operation_policy_check") or {}
    detail = policy_check.get("detail") if isinstance(policy_check, dict) else {}
    detail = detail if isinstance(detail, dict) else {}
    max_total = _float(detail.get("max_total_exposure_krw"))
    current_value = _float(detail.get("current_bot_position_value_krw"))
    requested = _float(detail.get("requested_order_krw"))
    projected = _float(detail.get("projected_bot_position_value_krw")) or (current_value + requested)
    remaining = _float(detail.get("remaining_exposure_krw"))
    available_krw = detail.get("available_krw_balance")
    daily_loss = _float(detail.get("daily_loss_krw"))
    daily_loss_limit = _float(detail.get("daily_loss_limit_krw"))
    exceeded_by = max(projected - max_total, 0.0) if max_total > 0 else 0.0
    krw_shortfall = _float(detail.get("krw_shortfall_krw"))
    return {
        "code": code,
        "summary": POLICY_BLOCK_LABELS.get(code, code),
        "reason": log.get("block_reason") or code,
        "auto_trading_enabled": bool(detail.get("auto_trading_enabled")),
        "requested_order_krw": requested,
        "max_total_exposure_krw": max_total,
        "current_bot_position_value_krw": current_value,
        "projected_bot_position_value_krw": projected,
        "remaining_exposure_krw": remaining,
        "available_krw_balance": available_krw,
        "daily_loss_krw": daily_loss,
        "daily_loss_limit_pct": _float(detail.get("daily_loss_limit_pct")),
        "daily_loss_limit_krw": daily_loss_limit,
        "daily_loss_usage_pct": (daily_loss / daily_loss_limit * 100) if daily_loss_limit > 0 else 0.0,
        "exceeded_by_krw": exceeded_by,
        "krw_shortfall_krw": krw_shortfall,
        "next_action": _policy_block_next_action(code),
    }


def _policy_block_next_action(code: str) -> str:
    if code == "BLOCKED_POLICY_AUTO_TRADING_DISABLED":
        return "운용설정에서 자동매매를 ON으로 바꿔야 신규매수가 허용됩니다."
    if code == "BLOCKED_POLICY_MAX_TOTAL_EXPOSURE":
        return "포지션을 줄이거나 최대 투입 금액을 상향해야 추가매수가 허용됩니다."
    if code == "BLOCKED_POLICY_KRW_BALANCE_INSUFFICIENT":
        return "거래소 KRW 주문 가능 잔고가 주문 후보 금액보다 커야 합니다."
    if code == "BLOCKED_POLICY_DAILY_LOSS_LIMIT":
        return "오늘 신규매수는 중단하고 다음 KST 일자 리셋 후 재평가합니다."
    if code == "BLOCKED_POLICY_MAX_EXPOSURE_INVALID":
        return "운용설정의 최대 투입 금액을 0보다 크게 저장해야 합니다."
    return "운용정책 설정과 현재 포지션 상태를 확인하세요."


def compute_risk_state(
    exchange: str = "bithumb",
    market: str = "KRW-BTC",
    *,
    balance_mismatch: bool | None = None,
    account_equity_krw: float | None = None,
) -> dict:
    config = RiskConfig.from_env()
    date = kst_date()
    start_utc, end_utc = _kst_day_bounds_utc(date)
    with get_connection() as conn:
        order_row = conn.execute(
            """
            SELECT
                COUNT(*) AS daily_order_count,
                SUM(CASE WHEN COALESCE(order_purpose, 'ENTRY') = 'ENTRY' THEN 1 ELSE 0 END) AS daily_entry_count,
                SUM(CASE WHEN COALESCE(order_purpose, 'ENTRY') = 'EXIT' THEN 1 ELSE 0 END) AS daily_exit_count,
                MAX(updated_at) AS last_order_time_utc
            FROM live_order_logs
            WHERE exchange = ?
              AND market = ?
              AND status IN ('SUBMITTED', 'WAITING', 'PARTIALLY_FILLED', 'FILLED', 'CANCELED', 'FAILED')
              AND request_id NOT LIKE '%-submitted%'
              AND request_id NOT LIKE '%-waiting-%'
              AND request_id NOT LIKE '%-partial%'
              AND request_id NOT LIKE '%-canceled-%'
              AND request_id NOT LIKE '%-filled-%'
              AND request_id NOT LIKE '%-failed-%'
              AND created_at >= ?
              AND created_at < ?
            """,
            (exchange, market, start_utc, end_utc),
        ).fetchone()
        pnl_row = conn.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN status = 'CLOSED' THEN realized_pnl ELSE 0 END), 0) AS realized_pnl,
                COALESCE(SUM(CASE WHEN status IN ('OPEN', 'EXIT_CANDIDATE', 'EXIT_PENDING', 'CLOSING', 'MANUAL_REVIEW_REQUIRED') THEN unrealized_pnl ELSE 0 END), 0) AS unrealized_pnl,
                MAX(CASE WHEN realized_pnl < 0 THEN closed_at ELSE NULL END) AS last_loss_time_utc
            FROM live_positions
            WHERE exchange = ?
              AND market = ?
              AND (created_at < ?)
            """,
            (exchange, market, end_utc),
        ).fetchone()
        partial = conn.execute(
            """
            SELECT id
            FROM live_order_logs
            WHERE exchange = ?
              AND market = ?
              AND status = 'PARTIALLY_FILLED'
              AND request_id NOT LIKE '%-partial%'
            LIMIT 1
            """,
            (exchange, market),
        ).fetchone()
    daily_order_count = int(order_row["daily_order_count"] or 0)
    daily_entry_count = int(order_row["daily_entry_count"] or 0)
    daily_exit_count = int(order_row["daily_exit_count"] or 0)
    realized = float(pnl_row["realized_pnl"] or 0.0)
    unrealized = float(pnl_row["unrealized_pnl"] or 0.0)
    total_pnl = realized + unrealized
    daily_loss_krw = abs(min(total_pnl, 0.0))
    daily_loss_basis_krw = _daily_loss_basis_krw(account_equity_krw, config)
    daily_loss_percent = daily_loss_krw / daily_loss_basis_krw * 100
    open_order_count = len(load_reconcilable_live_order_logs(exchange, market))
    open_position_count = len(load_open_live_positions(exchange, market))
    partial_fill = partial is not None
    if balance_mismatch is None:
        latest = load_latest_risk_state(exchange, market)
        balance_mismatch = bool(latest and latest.get("balance_mismatch_detected"))
    status = "OK"
    if is_emergency_stopped():
        status = "EMERGENCY_STOPPED"
    elif balance_mismatch or partial_fill:
        status = "MANUAL_REVIEW_REQUIRED"
    elif (
        (config.max_orders_per_day > 0 and daily_order_count >= config.max_orders_per_day)
        or (config.max_entry_orders_per_day > 0 and daily_entry_count >= config.max_entry_orders_per_day)
        or (config.max_exit_orders_per_day > 0 and daily_exit_count >= config.max_exit_orders_per_day)
        or daily_loss_krw >= config.max_daily_loss_krw
        or daily_loss_percent >= config.max_daily_loss_percent
    ):
        status = "BLOCKED"
    elif open_order_count or open_position_count:
        status = "WARNING"
    state = {
        "exchange": exchange,
        "market": market,
        "date_kst": date,
        "status": status,
        "daily_realized_pnl": realized,
        "daily_unrealized_pnl": unrealized,
        "daily_total_pnl": total_pnl,
        "daily_loss_percent": daily_loss_percent,
        "daily_loss_basis_krw": daily_loss_basis_krw,
        "daily_order_count": daily_order_count,
        "daily_entry_count": daily_entry_count,
        "daily_exit_count": daily_exit_count,
        "consecutive_loss_count": consecutive_loss_count(exchange, market, config.consecutive_loss_min_krw),
        "open_order_count": open_order_count,
        "open_position_count": open_position_count,
        "last_order_time_utc": order_row["last_order_time_utc"] if order_row else None,
        "last_loss_time_utc": pnl_row["last_loss_time_utc"] if pnl_row else None,
        "emergency_stop_enabled": is_emergency_stopped(),
        "balance_mismatch_detected": bool(balance_mismatch),
        "partial_fill_detected": partial_fill,
        "volatility_block_enabled": config.volatility_block_percent > 0,
        "low_volume_block_enabled": (
            config.min_volume_krw > 0
            or config.min_current_1m_volume_krw > 0
            or config.min_avg_5m_volume_krw > 0
        ),
    }
    upsert_risk_state(state)
    return state


def check_order_risk(
    *,
    order: dict,
    purpose: str,
    base_result: dict | None = None,
    mode: str = "LIVE_MANUAL_ONLY",
    session_id: int | None = None,
    position_id: int | None = None,
    candidate_strategy_id: int | None = None,
    candle_time_utc: str | None = None,
    signal: str | None = None,
    market_snapshot: dict | None = None,
    balances: dict | None = None,
    account_equity_krw: float | None = None,
    balance_mismatch: bool | None = None,
    manual_confirmed: bool = False,
    is_auto: bool = False,
) -> dict:
    exchange = str(order.get("exchange", "bithumb")).lower()
    market = str(order.get("market", "KRW-BTC"))
    side = str(order.get("side", "")).upper()
    config = RiskConfig.from_env()
    effective_account_equity_krw = (
        account_equity_krw
        if account_equity_krw is not None
        else estimate_account_equity_krw(balances, market_snapshot, market)
    )
    state = compute_risk_state(
        exchange,
        market,
        balance_mismatch=balance_mismatch,
        account_equity_krw=effective_account_equity_krw,
    )
    result = _standard_result(base_result, config)
    checks: dict[str, dict] = {}

    def block(code: str, reason: str | None = None, check_name: str = "guardrail", detail: Any = None) -> None:
        if result["allowed"]:
            result["allowed"] = False
            result["risk_level"] = "BLOCKED"
            result["block_code"] = code
            result["block_reason"] = reason or code
        check = {"allowed": False, "code": code, "reason": reason or code}
        if detail is not None:
            check["detail"] = detail
        checks[check_name] = check

    def ok(check_name: str, detail: Any = True) -> None:
        checks.setdefault(check_name, {"allowed": True, "detail": detail})

    if not result["allowed"]:
        checks["base_check"] = {"allowed": False, "code": result["block_code"], "reason": result["block_reason"]}
    else:
        ok("base_check")

    if is_emergency_stopped():
        block("BLOCKED_EMERGENCY_STOP", check_name="mode_check")
    elif mode not in {"LIVE_MANUAL_ONLY", "AUTO_STRATEGY_RUNNING"} and not is_auto:
        block("BLOCKED_INVALID_MODE", check_name="mode_check")
    else:
        ok("mode_check", mode)

    candidate = load_candidate_strategy(int(candidate_strategy_id)) if candidate_strategy_id is not None else None
    candidate_status = str((candidate or {}).get("status") or "")
    market_live_candidate_allowed = (
        market == "KRW-BTC"
        or (
            bool(candidate)
            and candidate_status in {"LIVE_ELIGIBLE", "LIVE_ACTIVE"}
            and str(candidate.get("market")) == market
            and market_is_live_allowed(exchange, market)
        )
    )

    if exchange != "bithumb":
        block("BLOCKED_EXCHANGE_NOT_ALLOWED", check_name="exchange_check")
    elif not market_live_candidate_allowed:
        block("BLOCKED_MARKET_NOT_ALLOWED", check_name="exchange_check", detail={
            "market": market,
            "candidate_strategy_id": candidate_strategy_id,
            "candidate_status": candidate_status or None,
            "requires_status": ["LIVE_ELIGIBLE", "LIVE_ACTIVE"],
            "live_allowed_market": market_is_live_allowed(exchange, market),
        })
    else:
        ok("exchange_check")

    order_type = str(order.get("order_type", order.get("ord_type", "LIMIT"))).upper()
    if order_type != "LIMIT" and str(order.get("ord_type", "")).lower() != "limit":
        block("BLOCKED_ORDER_TYPE_NOT_ALLOWED", check_name="order_type_check")
    elif order_type == "MARKET":
        block("BLOCKED_MARKET_ORDER_DISABLED", check_name="order_type_check")
    else:
        ok("order_type_check")

    amount = _float(order.get("amount_krw")) or (_float(order.get("price")) * _float(order.get("volume")))
    if amount > config.max_order_krw:
        block("BLOCKED_MAX_ORDER_AMOUNT", check_name="amount_check")
    else:
        ok("amount_check", amount)

    policy = load_global_bot_operation_policy()
    policy_limit = _float(policy.get("max_total_exposure_krw"))
    policy_daily_loss_limit_krw = policy_limit * _float(policy.get("daily_loss_limit_pct")) / 100
    current_bot_position_value = _current_bot_position_value_krw(exchange, market, market_snapshot)
    available_krw = _available_balance_total(balances, "KRW")
    policy_max_allowed = max(policy_limit - current_bot_position_value, 0.0) if policy_limit > 0 else 0.0
    policy_check_detail = {
        "auto_trading_enabled": bool(policy.get("auto_trading_enabled")),
        "max_total_exposure_krw": policy_limit,
        "daily_loss_limit_pct": _float(policy.get("daily_loss_limit_pct")),
        "daily_loss_limit_krw": policy_daily_loss_limit_krw,
        "daily_loss_krw": abs(min(state["daily_total_pnl"], 0.0)),
        "current_bot_position_value_krw": current_bot_position_value,
        "requested_order_krw": amount,
        "projected_bot_position_value_krw": current_bot_position_value + amount,
        "remaining_exposure_krw": policy_max_allowed,
        "available_krw_balance": available_krw if balances else None,
        "max_allowed_entry_krw": min(policy_max_allowed, available_krw) if balances else policy_max_allowed,
        "exposure_overage_krw": max((current_bot_position_value + amount) - policy_limit, 0.0) if policy_limit > 0 else 0.0,
        "krw_shortfall_krw": max(amount - available_krw, 0.0) if balances is not None else 0.0,
    }
    if purpose == "ENTRY" and side in {"BUY", "BID"} and is_auto:
        if not policy.get("auto_trading_enabled"):
            block("BLOCKED_POLICY_AUTO_TRADING_DISABLED", check_name="operation_policy_check", detail=policy_check_detail)
        elif policy_limit <= 0:
            block("BLOCKED_POLICY_MAX_EXPOSURE_INVALID", check_name="operation_policy_check", detail=policy_check_detail)
        elif current_bot_position_value >= policy_limit:
            block("BLOCKED_POLICY_MAX_TOTAL_EXPOSURE", check_name="operation_policy_check", detail=policy_check_detail)
        elif current_bot_position_value + amount > policy_limit:
            block("BLOCKED_POLICY_MAX_TOTAL_EXPOSURE", check_name="operation_policy_check", detail=policy_check_detail)
        elif balances is not None and available_krw < amount:
            block("BLOCKED_POLICY_KRW_BALANCE_INSUFFICIENT", check_name="operation_policy_check", detail=policy_check_detail)
        elif policy_daily_loss_limit_krw > 0 and abs(min(state["daily_total_pnl"], 0.0)) >= policy_daily_loss_limit_krw:
            block("BLOCKED_POLICY_DAILY_LOSS_LIMIT", check_name="operation_policy_check", detail=policy_check_detail)
        else:
            ok("operation_policy_check", policy_check_detail)
    else:
        ok("operation_policy_check", {**policy_check_detail, "scope": "not_auto_entry"})

    same_strategy_position_open = (
        purpose == "ENTRY"
        and candidate_strategy_id is not None
        and has_open_live_position_for_strategy(exchange, market, int(candidate_strategy_id))
    )
    if purpose == "ENTRY" and config.block_on_open_position and same_strategy_position_open:
        block("BLOCKED_OPEN_POSITION_EXISTS", check_name="position_check")
    elif purpose == "ENTRY":
        ok("position_check")

    if config.block_on_open_order and state["open_order_count"] > 0:
        block("BLOCKED_OPEN_ORDER_EXISTS", check_name="open_order_check")
    else:
        ok("open_order_check")

    if config.block_on_partial_fill and state["partial_fill_detected"]:
        block("BLOCKED_PARTIAL_FILL_UNSUPPORTED", check_name="partial_fill_check")
    else:
        ok("partial_fill_check")

    if config.block_on_balance_mismatch and state["balance_mismatch_detected"]:
        block("BLOCKED_BALANCE_MISMATCH", check_name="balance_reconciliation_check")
    else:
        ok("balance_reconciliation_check")

    if config.max_orders_per_day > 0 and state["daily_order_count"] >= config.max_orders_per_day:
        block("BLOCKED_MAX_ORDERS_PER_DAY", check_name="daily_limit_check")
    elif purpose == "ENTRY" and config.max_entry_orders_per_day > 0 and state["daily_entry_count"] >= config.max_entry_orders_per_day:
        block("BLOCKED_MAX_ENTRY_ORDERS_PER_DAY", check_name="daily_limit_check")
    elif purpose == "EXIT" and config.max_exit_orders_per_day > 0 and state["daily_exit_count"] >= config.max_exit_orders_per_day:
        block("BLOCKED_MAX_EXIT_ORDERS_PER_DAY", check_name="daily_limit_check")
    elif purpose == "ENTRY" and (
        abs(min(state["daily_total_pnl"], 0.0)) >= config.max_daily_loss_krw
        or state["daily_loss_percent"] >= config.max_daily_loss_percent
    ):
        block("BLOCKED_DAILY_LOSS_LIMIT", check_name="daily_limit_check")
    else:
        ok("daily_limit_check")

    if purpose == "ENTRY" and state["consecutive_loss_count"] >= config.max_consecutive_losses:
        block("BLOCKED_CONSECUTIVE_LOSS_LIMIT", check_name="loss_streak_check")
    else:
        ok("loss_streak_check")

    cooldown_remaining = cooldown_remaining_seconds(state.get("last_order_time_utc"), config.min_cooldown_seconds)
    if cooldown_remaining > 0:
        block("BLOCKED_COOLDOWN", f"{cooldown_remaining}s cooldown remaining.", "cooldown_check")
    else:
        ok("cooldown_check")
    result["cooldown_remaining_seconds"] = cooldown_remaining

    duplicate = duplicate_order_exists(
        exchange=exchange,
        market=market,
        side=side,
        request_id=str(order.get("request_id") or ""),
        session_id=session_id,
        candidate_strategy_id=candidate_strategy_id,
        candle_time_utc=candle_time_utc,
        signal=signal,
    )
    if duplicate:
        block(duplicate, check_name="duplicate_check")
    else:
        ok("duplicate_check")

    if purpose == "ENTRY":
        market_block = market_condition_block(market_snapshot, config)
        if market_block:
            block(market_block, check_name="market_condition_check")
        else:
            ok("market_condition_check")

    if result["allowed"] and result["risk_level"] == "LOW" and state["status"] in {"WARNING", "MANUAL_REVIEW_REQUIRED"}:
        result["risk_level"] = "MEDIUM"
    result["checks"] = checks
    result["risk_result"] = "ALLOWED" if result["allowed"] else result["block_code"]
    result["blocked_reason"] = "" if result["allowed"] else result["block_reason"]
    result["max_allowed_order_krw"] = min(result.get("max_allowed_order_krw", config.max_order_krw), config.max_order_krw)
    if purpose == "ENTRY" and side in {"BUY", "BID"}:
        result["max_allowed_order_krw"] = min(result["max_allowed_order_krw"], policy_check_detail["max_allowed_entry_krw"])
    result["operation_policy"] = policy_check_detail
    result["checked_at"] = _utc_now()
    insert_risk_log(
        {
            "exchange": exchange,
            "market": market,
            "session_id": session_id,
            "position_id": position_id,
            "order_candidate_id": order.get("request_id"),
            "risk_level": result["risk_level"],
            "allowed": result["allowed"],
            "block_code": result["block_code"],
            "block_reason": result["block_reason"],
            "checks": checks,
        }
    )
    if not result["allowed"]:
        compute_risk_state(
            exchange,
            market,
            balance_mismatch=state["balance_mismatch_detected"],
            account_equity_krw=effective_account_equity_krw,
        )
    return result


def estimate_account_equity_krw(
    balances: dict | None,
    market_snapshot: dict | None = None,
    market: str = "KRW-BTC",
) -> float:
    if not balances:
        return 0.0
    total = _balance_total(balances, "KRW")
    base_currency = market.split("-")[-1] if "-" in market else "BTC"
    price = _float((market_snapshot or {}).get("price")) or _float((market_snapshot or {}).get("trade_price"))
    if price > 0:
        total += _balance_total(balances, base_currency) * price
    return total


def standardize_risk_result(result: dict) -> dict:
    return _standard_result(result, RiskConfig.from_env())


def cooldown_remaining_seconds(last_order_time_utc: str | None, cooldown_seconds: int) -> int:
    if not last_order_time_utc:
        return 0
    parsed = _parse_utc(last_order_time_utc)
    if parsed is None:
        return 0
    elapsed = (datetime.now(timezone.utc) - parsed).total_seconds()
    return max(int(cooldown_seconds - elapsed), 0)


def consecutive_loss_count(exchange: str, market: str, min_loss_krw: float | None = None) -> int:
    threshold = abs(float(min_loss_krw if min_loss_krw is not None else RiskConfig.from_env().consecutive_loss_min_krw))
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT realized_pnl
            FROM live_positions
            WHERE exchange = ?
              AND market = ?
              AND status = 'CLOSED'
            ORDER BY closed_at DESC, updated_at DESC, id DESC
            LIMIT 20
            """,
            (exchange, market),
        ).fetchall()
    count = 0
    for row in rows:
        realized_pnl = float(row["realized_pnl"] or 0.0)
        if (realized_pnl < 0 and threshold <= 0) or (threshold > 0 and realized_pnl <= -threshold):
            count += 1
        else:
            break
    return count


def duplicate_order_exists(
    *,
    exchange: str,
    market: str,
    side: str,
    request_id: str,
    session_id: int | None,
    candidate_strategy_id: int | None,
    candle_time_utc: str | None,
    signal: str | None,
) -> str | None:
    with get_connection() as conn:
        if request_id:
            row = conn.execute("SELECT status FROM live_order_logs WHERE request_id = ? LIMIT 1", (request_id,)).fetchone()
            if row and row["status"] != "PREVIEWED":
                return "BLOCKED_DUPLICATE_REQUEST"
        if candle_time_utc:
            row = conn.execute(
                """
                SELECT id
                FROM live_order_logs
                WHERE exchange = ?
                  AND market = ?
                  AND side = ?
                  AND request_id != ?
                  AND candle_time_utc = ?
                  AND (? IS NULL OR session_id = ?)
                  AND (? IS NULL OR candidate_strategy_id = ?)
                  AND status IN ('PREVIEWED', 'SUBMITTED', 'WAITING', 'PARTIALLY_FILLED', 'FILLED', 'CANCELED', 'FAILED')
                LIMIT 1
                """,
                (exchange, market, side, request_id, candle_time_utc, session_id, session_id, candidate_strategy_id, candidate_strategy_id),
            ).fetchone()
            if row:
                return "BLOCKED_DUPLICATE_CANDLE"
    return None


def market_condition_block(market_snapshot: dict | None, config: RiskConfig) -> str | None:
    if not market_snapshot:
        return None
    range_rate = _float(market_snapshot.get("range_rate"))
    if range_rate * 100 >= config.volatility_block_percent:
        return "BLOCKED_VOLATILITY_FILTER"
    if market_snapshot.get("liquidity_check_required") or "current_1m_trade_price_volume" in market_snapshot or "recent_5m_avg_trade_price_volume" in market_snapshot:
        current_1m = _float(market_snapshot.get("current_1m_trade_price_volume"))
        avg_5m = _float(market_snapshot.get("recent_5m_avg_trade_price_volume"))
        avg_count = int(_float(market_snapshot.get("recent_5m_volume_count")))
        if config.min_current_1m_volume_krw > 0 and current_1m <= 0:
            return "BLOCKED_VOLUME_DATA_UNAVAILABLE"
        if config.min_avg_5m_volume_krw > 0 and (avg_5m <= 0 or avg_count < 5):
            return "BLOCKED_VOLUME_DATA_UNAVAILABLE"
        if config.min_current_1m_volume_krw > 0 and current_1m < config.min_current_1m_volume_krw:
            return "BLOCKED_LOW_1M_VOLUME"
        if config.min_avg_5m_volume_krw > 0 and avg_5m < config.min_avg_5m_volume_krw:
            return "BLOCKED_LOW_5M_AVG_VOLUME"
        if config.require_completed_candle and market_snapshot.get("complete") is False:
            return "BLOCKED_INCOMPLETE_CANDLE"
        return None
    volume_krw = _float(market_snapshot.get("trade_price_volume")) or _float(market_snapshot.get("volume_krw")) or 0.0
    if volume_krw <= 0:
        volume_krw = _float(market_snapshot.get("price")) * _float(market_snapshot.get("volume"))
    if volume_krw < config.min_volume_krw:
        return "BLOCKED_LOW_VOLUME"
    if config.require_completed_candle and market_snapshot.get("complete") is False:
        return "BLOCKED_INCOMPLETE_CANDLE"
    return None


def _daily_loss_basis_krw(account_equity_krw: float | None, config: RiskConfig) -> float:
    equity = _float(account_equity_krw)
    if equity <= 0:
        equity = config.account_equity_krw
    return max(equity, 1.0)


def _balance_total(balances: dict | None, currency: str) -> float:
    if not balances:
        return 0.0
    target = currency.upper()
    for item in balances.get("balances", []) or []:
        if str(item.get("currency", "")).upper() == target:
            return _float(item.get("balance")) + _float(item.get("locked"))
    by_currency = balances.get("by_currency") or {}
    item = by_currency.get(target) or by_currency.get(target.lower())
    if isinstance(item, dict):
        return _float(item.get("balance")) + _float(item.get("locked"))
    return 0.0


def _available_balance_total(balances: dict | None, currency: str) -> float:
    if not balances:
        return 0.0
    target = currency.upper()
    for item in balances.get("balances", []) or []:
        if str(item.get("currency", "")).upper() == target:
            return _float(item.get("balance"))
    by_currency = balances.get("by_currency") or {}
    item = by_currency.get(target) or by_currency.get(target.lower())
    if isinstance(item, dict):
        return _float(item.get("balance"))
    direct = balances.get(target.lower()) or balances.get(target)
    if isinstance(direct, dict):
        return _float(direct.get("balance"))
    return 0.0


def _current_bot_position_value_krw(exchange: str, market: str, market_snapshot: dict | None) -> float:
    fallback_price = _float((market_snapshot or {}).get("price")) or _float((market_snapshot or {}).get("trade_price"))
    total = 0.0
    for position in load_open_live_positions(exchange, market):
        qty = _float(position.get("entry_volume"))
        price = _float(position.get("current_price")) or fallback_price or _float(position.get("entry_price"))
        total += qty * price
    return total


def _standard_result(result: dict | None, config: RiskConfig) -> dict:
    source = dict(result or {})
    allowed = bool(source.get("allowed", True))
    block_code = source.get("block_code") or (None if allowed else source.get("risk_result") or source.get("blocked_reason") or "BLOCKED")
    block_reason = source.get("block_reason") or source.get("blocked_reason") or block_code
    return {
        **source,
        "allowed": allowed,
        "risk_level": source.get("risk_level") or ("LOW" if allowed else "BLOCKED"),
        "block_code": block_code,
        "block_reason": "" if allowed else block_reason,
        "warnings": source.get("warnings", []),
        "max_allowed_order_krw": source.get("max_allowed_order_krw", config.max_order_krw),
        "checked_at": source.get("checked_at", _utc_now()),
        "checks": source.get("checks", {}),
    }


def _kst_day_bounds_utc(date_kst: str) -> tuple[str, str]:
    kst = timezone(timedelta(hours=9))
    start = datetime.fromisoformat(date_kst).replace(tzinfo=kst)
    end = start + timedelta(days=1)
    return (
        start.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        end.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    )


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).astimezone(timezone.utc)
    except ValueError:
        return None


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
