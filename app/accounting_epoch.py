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


def build_smoke_test_preflight(
    *,
    exchange: str = "bithumb",
    symbol: str = "BTC",
    strategy_name: str = "smoke_test",
    amount_krw: float | None = None,
    current_epoch: dict | None = None,
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
    with get_connection() as conn:
        active_orders = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM live_order_logs
            WHERE exchange = ? AND status IN ('SUBMITTED', 'WAITING', 'PARTIALLY_FILLED')
            """,
            (exchange,),
        ).fetchone()["count"]
    if active_orders:
        blockers.append({"code": "OPEN_LIVE_ORDER_EXISTS", "count": int(active_orders)})

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
