from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from app.database import (
    get_connection,
    live_order_idempotency_exists,
    load_current_accounting_epoch,
    load_global_bot_operation_policy,
    load_latest_smoke_test_run,
    load_runtime_lock,
    load_unresolved_live_order_logs_for_exchange,
)
from app.live_broker import LiveTradingConfig, is_emergency_stopped

LEGACY_HISTORY_REASONS = [
    "OPENING_SNAPSHOT_UNAVAILABLE",
    "TOTAL_PNL_SANITY_FAILED",
    "SELL_EXCEEDS_OPEN_QUANTITY",
    "UNREALIZED_PNL_DUPLICATE_SUSPECTED",
    "STRATEGY_ALLOCATION_DIFF",
    "SESSION_ALLOCATION_DIFF",
]
LEGACY_BLOCKER_CODES = {
    "DUPLICATE_SESSION_ORDER",
    "EXPIRED_RESERVATION_EXECUTED",
    "TIMESTAMP_FORMAT_MISMATCH",
    "DUPLICATE_ORDER_UUID",
    "TOTAL_PNL_SANITY_FAILED",
    "PNL_TRUST_LEVEL_LOW",
    "OPENING_SNAPSHOT_UNAVAILABLE",
    "EQUITY_BRIDGE_DIFF",
    "UNREALIZED_PNL_DUPLICATE_SUSPECTED",
    "STRATEGY_PNL_ALLOCATION_DIFF",
    "SESSION_PNL_ALLOCATION_DIFF",
    "BOT_OWNED_REALIZED_PNL_DIFF",
    "EXCHANGE_FILL_MISSING_IN_DB",
    "MISSING_LIVE_POSITION_ACCOUNTING_FILL",
    "MISSING_STRATEGY_PNL_FILL",
    "ACCOUNTING_PARTIAL_FILL",
    "ACCOUNTING_LEGACY_MISSING_CANONICAL_LOG",
    "MISSING_CANONICAL_LIVE_ORDER_LOG",
    "SEVEN_DAY_PNL_NEGATIVE",
}
DEFAULT_SMOKE_SYMBOLS = {"BTC", "ETH"}
DEFAULT_BLOCKED_SYMBOLS = {"WLD", "XLM", "RE"}
DEFAULT_BLOCKED_STRATEGIES = {"rsi"}


def legacy_history_quarantine(asset_reconciliation: dict | None = None) -> dict:
    fifo_summary = ((asset_reconciliation or {}).get("ledger_pnl_detail") or {}).get("fifo_trace_summary") or {}
    warning_counts = fifo_summary.get("warning_counts") or {}
    reasons = list(LEGACY_HISTORY_REASONS)
    if warning_counts.get("SELL_EXCEEDS_OPEN_QUANTITY") and "SELL_EXCEEDS_OPEN_QUANTITY" not in reasons:
        reasons.append("SELL_EXCEEDS_OPEN_QUANTITY")
    return {
        "history_trust_level": "LOW",
        "legacy_contaminated": True,
        "use_for_live_risk": False,
        "use_for_strategy_score": False,
        "use_for_dashboard_main_pnl": False,
        "use_for_restart_gate_current_epoch": False,
        "legacy_reason": reasons,
        "display_scope": "LEGACY_DEBUG_ONLY",
    }


def build_current_epoch_diagnostics(
    *,
    exchange: str = "bithumb",
    current_equity: float | None = None,
) -> dict:
    epoch = load_current_accounting_epoch(exchange)
    if not epoch:
        return {
            "current_epoch_exists": False,
            "current_epoch_id": None,
            "current_epoch_status": "MISSING",
            "current_epoch_trust_level": "LOW",
            "current_epoch_blockers": [{"code": "CURRENT_EPOCH_MISSING", "count": 1}],
            "current_epoch_restart_allowed": False,
            "current_epoch_sanity_passed": False,
        }
    start = str(epoch.get("epoch_started_at_utc") or "")
    with get_connection() as conn:
        order_count = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM live_order_logs
            WHERE exchange = ?
              AND created_at >= ?
              AND request_id NOT LIKE '%-submitted%'
              AND request_id NOT LIKE '%-waiting-%'
              AND request_id NOT LIKE '%-partial%'
              AND request_id NOT LIKE '%-canceled-%'
              AND request_id NOT LIKE '%-filled-%'
              AND request_id NOT LIKE '%-failed-%'
            """,
            (exchange, start),
        ).fetchone()["count"]
        fill_count = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM exchange_fills_ledger
            WHERE exchange_name = ? AND executed_at_utc >= ?
            """,
            (exchange, start),
        ).fetchone()["count"]
        pending_count = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM exchange_fills_ledger
            WHERE exchange_name = ?
              AND executed_at_utc >= ?
              AND match_status IN ('UNMATCHED', 'MISSING_CANONICAL_LOG')
            """,
            (exchange, start),
        ).fetchone()["count"]
        failed_count = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM live_order_logs
            WHERE exchange = ? AND created_at >= ? AND status = 'FAILED'
            """,
            (exchange, start),
        ).fetchone()["count"]
    starting = _float(epoch.get("starting_exchange_equity"))
    equity = None if current_equity is None else _float(current_equity)
    total_pnl = 0.0 if equity is None else equity - starting
    blockers = []
    if str(epoch.get("epoch_trust_level") or "").upper() == "LOW":
        blockers.append({"code": "CURRENT_EPOCH_TRUST_LOW", "count": 1})
    if pending_count:
        blockers.append({"code": "CURRENT_EPOCH_ACCOUNTING_PENDING", "count": pending_count})
    if failed_count:
        blockers.append({"code": "CURRENT_EPOCH_ACCOUNTING_FAILED", "count": failed_count})
    sanity_passed = not blockers and equity is not None
    return {
        "current_epoch_exists": True,
        "current_epoch_id": epoch.get("epoch_id"),
        "current_epoch_started_at_utc": start,
        "current_epoch_status": epoch.get("epoch_status"),
        "current_epoch_trust_level": epoch.get("epoch_trust_level"),
        "current_epoch_starting_equity": starting,
        "current_epoch_current_equity": equity,
        "current_epoch_equity_diff": total_pnl,
        "current_epoch_realized_pnl": 0.0 if fill_count == 0 else None,
        "current_epoch_unrealized_pnl": total_pnl if fill_count == 0 else None,
        "current_epoch_total_pnl": total_pnl,
        "current_epoch_fill_count": int(fill_count),
        "current_epoch_order_count": int(order_count),
        "current_epoch_accounting_pending_count": int(pending_count),
        "current_epoch_accounting_failed_count": int(failed_count),
        "current_epoch_sanity_passed": sanity_passed,
        "current_epoch_restart_allowed": sanity_passed,
        "current_epoch_blockers": blockers,
        "cost_basis_policy": epoch.get("cost_basis_policy"),
        "legacy_history_isolated": bool(epoch.get("legacy_history_isolated")),
    }


def smoke_test_config() -> dict:
    symbols = _csv_env("SMOKE_TEST_ALLOWED_SYMBOLS", "BTC,ETH")
    return {
        "live_smoke_test_enabled": _bool_env("LIVE_SMOKE_TEST_ENABLED", False),
        "max_notional_krw": _float(os.getenv("SMOKE_TEST_MAX_NOTIONAL_KRW"), 6000.0),
        "allowed_symbols": symbols or sorted(DEFAULT_SMOKE_SYMBOLS),
        "max_orders": int(_float(os.getenv("SMOKE_TEST_MAX_ORDERS"), 2)),
        "timeout_seconds": int(_float(os.getenv("SMOKE_TEST_TIMEOUT_SECONDS"), 300)),
        "require_confirmation": _bool_env("SMOKE_TEST_REQUIRE_CONFIRMATION", True),
        "auto_stop_after_complete": _bool_env("SMOKE_TEST_AUTO_STOP_AFTER_COMPLETE", True),
        "disable_reentry": _bool_env("SMOKE_TEST_DISABLE_REENTRY", True),
    }


def build_open_order_audit(
    *,
    exchange: str = "bithumb",
    current_epoch: dict | None = None,
    exchange_open_orders: list[dict] | None = None,
    exchange_open_order_status: str = "UNAVAILABLE",
    exchange_open_order_errors: list[dict] | None = None,
) -> dict:
    epoch = current_epoch or build_current_epoch_diagnostics(exchange=exchange)
    epoch_start = str(epoch.get("current_epoch_started_at_utc") or "")
    db_orders = load_unresolved_live_order_logs_for_exchange(exchange)
    exchange_orders = [order for order in (exchange_open_orders or []) if isinstance(order, dict)]
    exchange_by_key = _orders_by_identity(exchange_orders, exchange_order=True)
    db_by_key = _orders_by_identity(db_orders, exchange_order=False)
    all_keys = sorted(set(exchange_by_key) | set(db_by_key))
    items = [
        _open_order_audit_item(
            db_order=db_by_key.get(key),
            exchange_order=exchange_by_key.get(key),
            epoch_start=epoch_start,
            fallback_key=key,
        )
        for key in all_keys
    ]
    summary = _open_order_audit_summary(items, exchange_open_order_status, exchange_open_order_errors or [])
    return {
        "open_order_audit_status": exchange_open_order_status,
        "open_order_audit_trust_level": summary["open_order_audit_trust_level"],
        "open_order_audit_summary": summary,
        "open_orders": items,
        "exchange_open_order_errors": exchange_open_order_errors or [],
    }


def build_smoke_test_preflight(
    *,
    exchange: str = "bithumb",
    symbol: str = "BTC",
    strategy_name: str = "smoke_test",
    amount_krw: float | None = None,
    current_epoch: dict | None = None,
    open_order_audit: dict | None = None,
) -> dict:
    cfg = smoke_test_config()
    symbol = str(symbol or "BTC").upper()
    market = f"KRW-{symbol}"
    amount = min(_float(amount_krw, cfg["max_notional_krw"]), cfg["max_notional_krw"])
    epoch = current_epoch or build_current_epoch_diagnostics(exchange=exchange)
    blockers: list[dict[str, Any]] = []
    policy = load_global_bot_operation_policy()
    runtime_lock = load_runtime_lock("auto-trading")
    live_config = LiveTradingConfig.for_exchange(exchange)
    client_order_id = f"smoke-{str(epoch.get('current_epoch_id') or 'no-epoch')}-{symbol}-buy"[:36]
    idempotency_key = f"smoke:{exchange}:{symbol}:{epoch.get('current_epoch_id') or 'no-epoch'}:BUY"

    if not cfg["live_smoke_test_enabled"]:
        blockers.append({"code": "LIVE_SMOKE_TEST_DISABLED", "count": 1})
    if bool(policy.get("auto_trading_enabled")):
        blockers.append({"code": "DB_AUTO_TRADING_MUST_REMAIN_FALSE", "count": 1})
    if str((runtime_lock or {}).get("status") or "").upper() != "STOPPED":
        blockers.append({"code": "NORMAL_AUTO_RUNTIME_NOT_STOPPED", "count": 1})
    if is_emergency_stopped():
        blockers.append({"code": "EMERGENCY_STOP_ENABLED", "count": 1})
    if not epoch.get("current_epoch_exists"):
        blockers.append({"code": "CURRENT_EPOCH_MISSING", "count": 1})
    if str(epoch.get("current_epoch_trust_level") or "").upper() == "LOW":
        blockers.append({"code": "CURRENT_EPOCH_TRUST_LOW", "count": 1})
    if int(epoch.get("current_epoch_accounting_pending_count") or 0):
        blockers.append({"code": "CURRENT_EPOCH_ACCOUNTING_PENDING", "count": int(epoch.get("current_epoch_accounting_pending_count") or 0)})
    if int(epoch.get("current_epoch_accounting_failed_count") or 0):
        blockers.append({"code": "CURRENT_EPOCH_ACCOUNTING_FAILED", "count": int(epoch.get("current_epoch_accounting_failed_count") or 0)})
    if not live_config.api_key_loaded:
        blockers.append({"code": "EXCHANGE_API_KEY_MISSING", "count": 1})
    if not live_config.live_trading_enabled:
        blockers.append({"code": "LIVE_TRADING_FEATURE_DISABLED", "count": 1})
    if symbol not in set(cfg["allowed_symbols"]):
        blockers.append({"code": "SMOKE_TEST_SYMBOL_NOT_ALLOWED", "count": 1})
    if symbol in DEFAULT_BLOCKED_SYMBOLS:
        blockers.append({"code": "SMOKE_TEST_BLOCKED_SYMBOL", "count": 1})
    if strategy_name.lower() in DEFAULT_BLOCKED_STRATEGIES:
        blockers.append({"code": "SMOKE_TEST_BLOCKED_STRATEGY", "count": 1})
    if amount <= 0 or amount > cfg["max_notional_krw"]:
        blockers.append({"code": "SMOKE_TEST_AMOUNT_EXCEEDS_LIMIT", "count": 1})
    if cfg["max_orders"] > 2:
        blockers.append({"code": "SMOKE_TEST_MAX_ORDERS_TOO_HIGH", "count": cfg["max_orders"]})
    if live_order_idempotency_exists(idempotency_key):
        blockers.append({"code": "DUPLICATE_SMOKE_TEST_IDEMPOTENCY_KEY", "count": 1})
    audit = open_order_audit or build_open_order_audit(exchange=exchange, current_epoch=epoch)
    audit_summary = audit.get("open_order_audit_summary") or {}
    exchange_open_count = int(audit_summary.get("exchange_open_order_count") or 0)
    current_epoch_open_count = int(audit_summary.get("current_epoch_open_order_count") or 0)
    unknown_open_count = int(audit_summary.get("unknown_open_order_count") or 0)
    if exchange_open_count:
        blockers.append({"code": "EXCHANGE_OPEN_ORDER_EXISTS", "count": exchange_open_count})
    if current_epoch_open_count:
        blockers.append({"code": "CURRENT_EPOCH_OPEN_ORDER_EXISTS", "count": current_epoch_open_count})
    if unknown_open_count:
        blockers.append({"code": "UNKNOWN_OPEN_ORDER_EXISTS", "count": unknown_open_count})

    allowed = len(blockers) == 0
    return {
        "smoke_test_allowed": allowed,
        "smoke_test_execution_prepared": True,
        "smoke_test_mode": "ONE_SHOT_LIVE_SMOKE_TEST",
        "smoke_test_blockers": blockers,
        "exchange": exchange,
        "symbol": symbol,
        "market": market,
        "strategy_name": strategy_name,
        "max_notional_krw": cfg["max_notional_krw"],
        "order_amount_krw": amount,
        "max_orders": min(cfg["max_orders"], 2),
        "timeout_seconds": cfg["timeout_seconds"],
        "client_order_id_preview": client_order_id,
        "idempotency_key_preview": idempotency_key,
        "confirmation_required": "RUN ONE SHOT LIVE SMOKE TEST" if cfg["require_confirmation"] else "",
        "auto_stop_after_complete": cfg["auto_stop_after_complete"],
        "disable_reentry": cfg["disable_reentry"],
        "open_order_audit": audit,
        "open_order_audit_summary": audit_summary,
    }


def limited_auto_live_gate(current_epoch: dict, smoke_preflight: dict, *, exchange: str = "bithumb") -> dict:
    latest = load_latest_smoke_test_run(exchange)
    blockers = []
    if not current_epoch.get("current_epoch_restart_allowed"):
        blockers.append({"code": "CURRENT_EPOCH_NOT_CLEAN", "count": 1})
    if not latest or latest.get("status") != "PASSED":
        blockers.append({"code": "SMOKE_TEST_NOT_PASSED", "count": 1})
    elif _age_minutes(latest.get("completed_at_utc") or latest.get("updated_at_utc")) > 30:
        blockers.append({"code": "SMOKE_TEST_TOO_OLD", "count": 1})
    if smoke_preflight.get("smoke_test_blockers"):
        blockers.append({"code": "SMOKE_PREFLIGHT_NOT_CLEAR", "count": len(smoke_preflight.get("smoke_test_blockers") or [])})
    return {
        "limited_auto_live_allowed": len(blockers) == 0,
        "full_auto_live_allowed": False,
        "last_smoke_test": latest,
        "limited_auto_live_blockers": blockers,
        "limited_auto_constraints": {
            "allowed_symbols": ["BTC", "ETH"],
            "blocked_symbols": sorted(DEFAULT_BLOCKED_SYMBOLS),
            "blocked_strategies": sorted(DEFAULT_BLOCKED_STRATEGIES),
            "max_open_positions": 1,
            "max_orders_per_day": 3,
        },
    }


def split_restart_blockers(reasons: list[dict], current_epoch: dict, smoke_preflight: dict) -> dict:
    legacy = [reason for reason in reasons if reason.get("code") in LEGACY_BLOCKER_CODES]
    normal = [reason for reason in reasons if reason.get("code") not in {item.get("code") for item in legacy}]
    return {
        "legacy_blockers": legacy,
        "current_epoch_blockers": current_epoch.get("current_epoch_blockers", []),
        "smoke_test_blockers": smoke_preflight.get("smoke_test_blockers", []),
        "normal_auto_blockers": normal or reasons,
    }


def _orders_by_identity(orders: list[dict], *, exchange_order: bool) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for index, order in enumerate(orders):
        keys = [
            str(order.get("uuid") or ""),
            str(order.get("order_uuid") or ""),
            str(order.get("exchange_order_uuid") or ""),
            str(order.get("identifier") or ""),
            str(order.get("client_order_id") or ""),
            str(order.get("request_id") or ""),
        ]
        key = next((value.strip() for value in keys if value and value.strip()), "")
        if not key:
            market = str(order.get("market") or "")
            side = str(order.get("side") or "")
            created_at = str(order.get("created_at") or order.get("created_at_utc") or "")
            prefix = "exchange" if exchange_order else "db"
            key = f"{prefix}:{market}:{side}:{created_at}:{index}"
        result[key] = order
    return result


def _open_order_audit_item(
    *,
    db_order: dict | None,
    exchange_order: dict | None,
    epoch_start: str,
    fallback_key: str,
) -> dict:
    db_exists = db_order is not None
    exchange_exists = exchange_order is not None
    created_at = _first_value(db_order, exchange_order, "created_at_utc", "created_at", "order_requested_at_utc")
    updated_at = _first_value(db_order, exchange_order, "updated_at_utc", "updated_at")
    belongs_to_current_epoch = bool(created_at and epoch_start and _canonical_time_text(created_at) >= _canonical_time_text(epoch_start))
    belongs_to_legacy_history = not belongs_to_current_epoch
    if exchange_exists and db_exists:
        source = "BOTH"
    elif exchange_exists:
        source = "EXCHANGE_OPEN_ORDER"
        belongs_to_current_epoch = False if not created_at else belongs_to_current_epoch
        belongs_to_legacy_history = not belongs_to_current_epoch
    elif db_exists and belongs_to_legacy_history:
        source = "DB_STALE_ONLY"
    elif db_exists:
        source = "DB_OPEN_ORDER"
    else:
        source = "UNKNOWN"
    recommended = _recommended_open_order_action(source, belongs_to_current_epoch)
    return {
        "source": source,
        "internal_order_id": (db_order or {}).get("id"),
        "live_order_log_id": (db_order or {}).get("id"),
        "exchange_order_uuid": _first_value(db_order, exchange_order, "order_uuid", "uuid", "exchange_order_uuid"),
        "client_order_id": _first_value(db_order, exchange_order, "client_order_id", "identifier"),
        "symbol": _symbol_from_market(str(_first_value(db_order, exchange_order, "market") or "")),
        "market": _first_value(db_order, exchange_order, "market"),
        "side": _first_value(db_order, exchange_order, "side"),
        "order_type": _first_value(db_order, exchange_order, "ord_type", "order_type", "order_purpose"),
        "status": (db_order or {}).get("status"),
        "requested_amount_krw": _first_value(db_order, exchange_order, "amount_krw", "requested_amount_krw", "price"),
        "requested_quantity": _first_value(db_order, exchange_order, "volume", "requested_quantity"),
        "filled_quantity": _first_value(db_order, exchange_order, "executed_volume", "filled_quantity"),
        "remaining_quantity": _first_value(db_order, exchange_order, "remaining_volume", "remaining_quantity"),
        "price": _first_value(db_order, exchange_order, "price"),
        "created_at_utc": created_at,
        "updated_at_utc": updated_at,
        "exchange_status": (exchange_order or {}).get("state") or (exchange_order or {}).get("status"),
        "exchange_open_order_exists": exchange_exists,
        "db_open_order_exists": db_exists,
        "belongs_to_legacy_history": belongs_to_legacy_history,
        "belongs_to_current_epoch": belongs_to_current_epoch,
        "recommended_action": recommended,
        "audit_key": fallback_key,
    }


def _open_order_audit_summary(items: list[dict], status: str, errors: list[dict]) -> dict:
    exchange_count = sum(1 for item in items if item.get("exchange_open_order_exists"))
    db_count = sum(1 for item in items if item.get("db_open_order_exists"))
    current_epoch_count = sum(1 for item in items if item.get("belongs_to_current_epoch"))
    db_stale_count = sum(1 for item in items if item.get("source") == "DB_STALE_ONLY")
    exchange_verified = status in {"SUCCESS", "PARTIAL"}
    unknown_count = (
        sum(1 for item in items if item.get("source") in {"UNKNOWN", "DB_OPEN_ORDER"} and not item.get("exchange_open_order_exists"))
        if exchange_verified
        else db_count
    )
    blocking_count = sum(
        1
        for item in items
        if item.get("exchange_open_order_exists") or item.get("belongs_to_current_epoch") or (not exchange_verified and item.get("db_open_order_exists")) or item.get("source") in {"UNKNOWN", "DB_OPEN_ORDER"}
    )
    trust = "HIGH" if status == "SUCCESS" else "LOW"
    if status == "PARTIAL" and not exchange_count:
        trust = "MEDIUM"
    return {
        "open_live_order_count_total": len(items),
        "exchange_open_order_count": exchange_count,
        "db_open_order_count": db_count,
        "db_stale_open_order_count": db_stale_count,
        "current_epoch_open_order_count": current_epoch_count,
        "legacy_open_order_count": sum(1 for item in items if item.get("belongs_to_legacy_history")),
        "unknown_open_order_count": unknown_count,
        "smoke_test_blocking_open_order_count": blocking_count,
        "open_order_audit_trust_level": trust,
        "exchange_open_order_audit_status": status,
        "exchange_open_order_error_count": len(errors),
        "next_required_action": _next_open_order_action(exchange_count, current_epoch_count, unknown_count, db_stale_count),
    }


def _recommended_open_order_action(source: str, belongs_to_current_epoch: bool) -> str:
    if source in {"EXCHANGE_OPEN_ORDER", "BOTH"}:
        return "USER_CONFIRM_CANCEL_REQUIRED"
    if belongs_to_current_epoch:
        return "KEEP_BLOCKED"
    if source == "DB_STALE_ONLY":
        return "MARK_LEGACY_STALE_CANDIDATE"
    return "INVESTIGATE"


def _next_open_order_action(exchange_count: int, current_epoch_count: int, unknown_count: int, db_stale_count: int) -> str:
    if exchange_count:
        return "USER_CONFIRM_CANCEL_REQUIRED"
    if current_epoch_count or unknown_count:
        return "INVESTIGATE"
    if db_stale_count:
        return "IGNORE_FOR_CURRENT_EPOCH_PRECHECK"
    return "SMOKE_PREFLIGHT_OPEN_ORDER_CLEAR"


def _first_value(row_a: dict | None, row_b: dict | None, *keys: str) -> Any:
    for row in (row_a, row_b):
        if not row:
            continue
        for key in keys:
            value = row.get(key)
            if value not in (None, ""):
                return value
    return None


def _canonical_time_text(value: Any) -> str:
    return str(value or "").replace("+00:00", "Z")


def _symbol_from_market(market: str) -> str:
    return str(market or "").split("-")[-1].upper()


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _csv_env(name: str, default: str) -> list[str]:
    return [item.strip().upper() for item in os.getenv(name, default).split(",") if item.strip()]


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _age_minutes(value: Any) -> float:
    if not value:
        return 10**9
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return 10**9
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 60
