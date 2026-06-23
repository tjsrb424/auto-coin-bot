from __future__ import annotations

import logging
import os
import socket
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import auth_status, login_admin, logout_admin, require_admin_session
from app.backtest import run_backtest
from app.backtest import compare_strategies
from app.auto_live_pilot import (
    auto_live_pilot_status,
    cancel_auto_live_pilot_open_order,
    run_auto_live_pilot_tick,
    start_auto_live_pilot,
    stop_auto_live_pilot,
)
from app.database import (
    clone_candidate_strategy,
    acquire_runtime_lock,
    load_active_strategy_selection,
    create_forward_session_from_candidate,
    create_live_paper_session,
    database_path,
    delete_candidate_strategy,
    ensure_required_schema,
    get_last_live_order_time,
    get_connection,
    get_db_schema_status,
    get_live_order_log,
    ensure_default_candidate_strategies,
    has_unresolved_live_order,
    has_recent_live_order,
    init_db,
    insert_live_mode_event,
    insert_live_order_log,
    insert_smart_rehearsal_review,
    insert_risk_log,
    insert_candles,
    load_live_eligible_candidate_strategies,
    load_market_universe,
    load_market_universe_item,
    load_market_universe_item_by_id,
    load_candidate_strategy,
    load_bot_operation_policy,
    load_latest_live_paper_session,
    load_decision_snapshot,
    load_decision_snapshots,
    load_execution_quality_logs,
    load_latest_decision_snapshot,
    load_risk_log,
    load_candles,
    load_candles_between,
    load_candidate_strategies,
    load_app_settings,
    load_forward_sessions,
    load_latest_forward_session,
    load_open_live_positions,
    load_runtime_lock,
    load_strategy_kill_switch_events,
    load_trade_history_logs,
    load_latest_paper_session,
    pause_running_forward_sessions_on_startup,
    promote_candidate_strategy,
    reject_candidate_strategy,
    release_runtime_lock,
    save_strategy_validation_run,
    save_backtest,
    save_candidate_strategy,
    save_paper_session,
    save_validation_run,
    set_candidate_strategy_status,
    stop_forward_session,
    stop_latest_live_paper_session,
    stop_latest_paper_session,
    update_live_order_log,
    update_market_universe_item,
    update_app_settings,
    update_bot_operation_policy,
    update_candidate_strategy,
    update_risk_log_resolution,
)
from app.auto_strategy_selector import auto_strategy_selector_status, evaluate_auto_strategy_selector
from app.capital_allocator import capital_allocator_status, run_capital_allocator_once
from app.capital_snapshot import build_capital_snapshot_async
from app.env import load_server_env
from app.forward_paper import latest_completed_candle, process_running_forward_sessions, run_forward_scheduler_tick
from app.strategy_promotion_pipeline import apply_selector_if_allowed, run_strategy_promotion_pipeline
from app.strategy_discovery_scheduler import (
    ALLOWED_STRATEGIES,
    BUY_CANDIDATE_STRATEGIES,
    DEFAULT_DISCOVERY_STRATEGIES,
    discovery_scheduler_config,
    discovery_scheduler_status,
)
from app.autonomous_orchestrator import (
    autonomous_orchestrator_config,
    autonomous_orchestrator_status,
    run_autonomous_orchestrator_background,
    run_autonomous_orchestrator_once,
)
from app.live_broker import (
    LiveBroker,
    LiveBrokerError,
    LiveTradingConfig,
    arm_live_manual_mode,
    current_live_mode,
    evaluate_live_order_risk,
    get_live_broker,
    is_emergency_stopped,
    lock_live_trading,
    masked_exchange_request,
    reset_emergency_stop,
    reset_live_runtime_state,
    trigger_emergency_stop,
)
from app.live_recovery import (
    import_exchange_btc_position,
    is_timeout_exception,
    log_recovery_event,
    recent_recovery_events,
    reconcile_balances,
    reconcile_order_log,
    run_startup_live_recovery_async,
    sync_open_orders,
)
from app.live_exit import (
    approve_exit_candidate,
    cancel_exit_order,
    create_exit_order_preview,
    reject_exit_candidate,
    submit_exit_order,
)
from app.risk_manager import check_order_risk, compute_risk_state, enrich_policy_block_log, get_risk_dashboard
from app.market_scanner import scan_market_universe
from app.execution_quality import summarize_execution_quality
from app.profit_engine import profit_engine_status_payload
from app.shadow_report import build_shadow_report
from app.smart_promotion import smart_engine_live_mode
from app.smart_readiness import build_limited_readiness
from app.live_paper import process_running_live_paper_sessions, run_scheduler_tick
from app.live_strategy_pilot import (
    AUTO_STRATEGY_CONFIRMATION,
    cancel_live_strategy_open_order,
    live_strategy_status,
    run_live_strategy_tick,
    start_live_strategy_pilot,
    stop_live_strategy_pilot,
)
from app.paper_trading import run_paper_trading
from app.strategy_validation import run_strategy_validation
from app.upbit import UpbitClientError, fetch_day_candles, fetch_minute_candles, fetch_tickers

load_server_env()

DEFAULT_MARKET = "KRW-BTC"
DEFAULT_VALIDATION_STRATEGIES = list(DEFAULT_DISCOVERY_STRATEGIES)
RUNTIME_LOCK_ID = "auto-trading"
logger = logging.getLogger("uvicorn.error")
_latest_balance_sync_time_utc: str | None = None


def _configure_runtime_logging() -> None:
    log_dir = os.getenv("LOG_DIR", "").strip()
    if not log_dir:
        return
    path = os.path.join(log_dir, "app.log")
    os.makedirs(log_dir, exist_ok=True)
    root = logging.getLogger()
    if any(isinstance(handler, logging.FileHandler) and getattr(handler, "baseFilename", "") == path for handler in root.handlers):
        return
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)


def _parse_utc(value: str) -> datetime:
    normalized = value.replace(" ", "T")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    if "+" not in normalized[-6:] and "-" not in normalized[-6:]:
        normalized = f"{normalized}+00:00"
    return datetime.fromisoformat(normalized).astimezone(timezone.utc)


def _format_upbit_to(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")


def _format_candle_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "")


def _health_payload(request: Request) -> dict:
    runtime = _runtime_status_payload(request)
    selected_exchange = os.getenv("AUTO_ALLOWED_EXCHANGE", os.getenv("EXCHANGE", "bithumb")).strip().lower()
    if selected_exchange not in {"upbit", "bithumb"}:
        selected_exchange = "bithumb"
    database_status = "UNKNOWN"
    try:
        with get_connection() as conn:
            conn.execute("SELECT 1").fetchone()
        database_status = "OK"
    except Exception as exc:
        database_status = f"ERROR:{exc.__class__.__name__}"
    db_schema = get_db_schema_status()

    risk_state = compute_risk_state(selected_exchange, os.getenv("AUTO_ALLOWED_MARKET", DEFAULT_MARKET))
    scheduler = getattr(request.app.state, "scheduler", None)
    scheduler_running = bool(scheduler and getattr(scheduler, "running", False))
    scheduler_jobs = [job.id for job in scheduler.get_jobs()] if scheduler else []
    return {
        "server_status": "OK",
        "database_status": database_status,
        "database_path": database_path(),
        "schema_status": db_schema.get("schema_status"),
        "missing_tables": db_schema.get("missing_tables", []),
        "db_schema": db_schema,
        "broker_status": _live_status(selected_exchange)["broker_status"],
        "selected_exchange": selected_exchange,
        "scheduler_status": "RUNNING" if scheduler_running else "STOPPED",
        "scheduler_jobs": scheduler_jobs,
        "risk_manager_status": risk_state.get("status", "UNKNOWN"),
        "emergency_stop_status": "ON" if is_emergency_stopped() else "OFF",
        "live_trading_enabled": runtime["live_trading_enabled"],
        "auto_trading_enabled": runtime["live_auto_trading_enabled"],
        "auto_strategy_enabled": runtime["auto_strategy_pilot_enabled"],
        "auto_runtime_status": runtime["runtime_status"],
        "auto_strategy_status": runtime["strategy_status"],
        "live_session_status": runtime["strategy_status"] if runtime["strategy_status"] != "STOPPED" else "PAUSED",
        "latest_order_sync_time": runtime["last_order_time_utc"],
        "latest_balance_sync_time": _latest_balance_sync_time_utc,
    }


def _instance_id() -> str:
    return os.getenv("RUNTIME_INSTANCE_ID", "").strip() or getattr(app.state, "instance_id", "unknown")


def _hostname() -> str:
    return socket.gethostname()


def _server_ip() -> str:
    try:
        return socket.gethostbyname(_hostname())
    except OSError:
        return "unknown"


def _runtime_status_payload(request: Request) -> dict:
    strategy = live_strategy_status()
    auto = auto_live_pilot_status()
    session = strategy.get("session") or auto.get("session") or {}
    raw_status = str(session.get("status") or "")
    if is_emergency_stopped():
        runtime_status = "EMERGENCY_STOPPED"
    elif raw_status in {"READY", "RUNNING"} and bool(session.get("auto_enabled", False)):
        runtime_status = "RUNNING"
    elif raw_status in {"PAUSED", "LIVE_PAUSED"}:
        runtime_status = "PAUSED"
    elif raw_status == "STOPPED":
        runtime_status = "STOPPED"
    else:
        runtime_status = "OFF"
    lock = load_runtime_lock(RUNTIME_LOCK_ID)
    live_config = LiveTradingConfig.for_exchange(strategy.get("exchange") or os.getenv("AUTO_ALLOWED_EXCHANGE", "bithumb"))
    return {
        "app_env": os.getenv("APP_ENV", "development"),
        "exchange": strategy.get("exchange") or auto.get("exchange") or os.getenv("AUTO_ALLOWED_EXCHANGE", "bithumb"),
        "live_trading_enabled": live_config.live_trading_enabled,
        "live_auto_trading_enabled": bool(strategy.get("live_auto_trading_enabled") or auto.get("live_auto_trading_enabled")),
        "auto_strategy_pilot_enabled": bool(strategy.get("auto_strategy_pilot_enabled")),
        "smart_autonomous_trading_enabled": bool(strategy.get("smart_autonomous_trading_enabled")),
        "runtime_status": runtime_status,
        "strategy_status": raw_status or "STOPPED",
        "emergency_stop": is_emergency_stopped(),
        "selected_strategy_id": session.get("candidate_strategy_id"),
        "selected_market": strategy.get("market") or auto.get("market") or DEFAULT_MARKET,
        "last_tick_time_utc": session.get("last_processed_candle_time_utc"),
        "last_order_time_utc": session.get("last_order_time_utc") or get_last_live_order_time(),
        "server_started_at": getattr(request.app.state, "server_started_at", None),
        "instance_id": _instance_id(),
        "hostname": _hostname(),
        "server_ip": _server_ip(),
        "runtime_owner": lock.get("runtime_owner") if lock else None,
        "runtime_lock": lock,
    }


def _try_acquire_runtime_lock(owner: str) -> tuple[bool, dict | None]:
    return acquire_runtime_lock(
        lock_id=RUNTIME_LOCK_ID,
        instance_id=_instance_id(),
        hostname=_hostname(),
        app_env=os.getenv("APP_ENV", "development"),
        runtime_owner=owner,
        ttl_seconds=int(os.getenv("RUNTIME_LOCK_TTL_SECONDS", "3600")),
    )


def _try_acquire_runtime_lock_for_start(owner: str, request: Request) -> tuple[bool, dict | None, dict | None]:
    acquired, current_lock = _try_acquire_runtime_lock(owner)
    if acquired:
        return True, current_lock, None

    status_payload = _runtime_status_payload(request)
    if current_lock and status_payload.get("runtime_status") in {"OFF", "STOPPED", "PAUSED"}:
        stale_instance_id = str(current_lock.get("instance_id") or "")
        release_runtime_lock(lock_id=RUNTIME_LOCK_ID, instance_id=stale_instance_id, status="STALE")
        insert_live_mode_event(
            "STALE_RUNTIME_LOCK_RELEASED",
            current_live_mode(),
            "자동매매 시작 전에 멈춤 상태로 남아있던 Runtime 락을 정리했습니다.",
            {
                "lock": current_lock,
                "runtime_status": status_payload.get("runtime_status"),
                "requested_owner": owner,
            },
        )
        acquired, current_lock = _try_acquire_runtime_lock(owner)
        if acquired:
            return True, current_lock, None

    return False, current_lock, status_payload


async def _load_period_candles(market: str, unit: int, start_time_utc: str, end_time_utc: str) -> list[dict]:
    start = _parse_utc(start_time_utc)
    end = _parse_utc(end_time_utc)
    if end <= start:
        raise ValueError("종료 시간은 시작 시간보다 늦어야 합니다.")
    expected_count = ceil((end - start).total_seconds() / (unit * 60)) + 5
    fetch_count = min(max(expected_count, 30), 20000)
    fresh = await fetch_minute_candles(
        market=market,
        unit=unit,
        count=fetch_count,
        to=_format_upbit_to(end),
    )
    insert_candles(fresh)
    candles = load_candles_between(
        market,
        unit,
        _format_candle_time(start),
        _format_candle_time(end),
    )
    if len(candles) < 30:
        raise ValueError("선택한 기간의 백테스트에 필요한 캔들이 30개 미만입니다.")
    return candles


class BacktestRequest(BaseModel):
    market: str = Field(DEFAULT_MARKET, pattern=r"^[A-Z]+-[A-Z0-9]+$")
    unit: int = 1
    count: int = Field(300, ge=30, le=1000)
    strategy: str = "ma_cross"
    settings: dict[str, Any] = Field(default_factory=dict)
    risk: dict[str, Any] = Field(default_factory=dict)


class BacktestCompareRequest(BaseModel):
    market: str = Field(DEFAULT_MARKET, pattern=r"^[A-Z]+-[A-Z0-9]+$")
    unit: int = 1
    start_time_utc: str
    end_time_utc: str
    strategies: list[str] = Field(default_factory=lambda: list(DEFAULT_VALIDATION_STRATEGIES))
    settings_by_strategy: dict[str, dict[str, Any]] = Field(default_factory=dict)
    risk: dict[str, Any] = Field(default_factory=dict)


class PaperTradingRequest(BaseModel):
    market: str = Field(DEFAULT_MARKET, pattern=r"^[A-Z]+-[A-Z0-9]+$")
    unit: int = 1
    count: int = Field(300, ge=30, le=1000)
    strategy: str = "ma_cross"
    settings: dict[str, Any] = Field(default_factory=dict)
    risk: dict[str, Any] = Field(default_factory=dict)


class StrategyValidationRequest(BaseModel):
    market: str = Field(DEFAULT_MARKET, pattern=r"^[A-Z]+-[A-Z0-9]+$")
    strategy: str = "ma_cross"
    timeframes: list[int] = Field(default_factory=lambda: [1, 5, 15, 60])
    periods: list[str] = Field(default_factory=lambda: ["7d", "30d", "90d", "180d"])
    custom_start_time_utc: str | None = None
    custom_end_time_utc: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    risk: dict[str, Any] = Field(default_factory=dict)


class MultiMarketValidationRequest(BaseModel):
    exchange: str = Field("upbit", pattern=r"^(upbit|bithumb)$")
    markets: list[str] = Field(default_factory=list)
    strategies: list[str] = Field(default_factory=lambda: list(DEFAULT_VALIDATION_STRATEGIES))
    timeframes: list[int] = Field(default_factory=lambda: [1, 5, 15, 60])
    periods: list[str] = Field(default_factory=lambda: ["7d", "30d"])
    risk: dict[str, Any] = Field(default_factory=dict)
    max_markets: int = Field(10, ge=1, le=20)
    auto_save_candidates: bool = True
    min_score: float = 70.0
    allow_live_eligible_promotion: bool = False


class MarketScanRequest(BaseModel):
    exchange: str = Field("upbit", pattern=r"^(upbit|bithumb)$")
    top_n: int = Field(10, ge=1, le=20)
    max_candidates: int = Field(20, ge=1, le=40)
    min_24h_trade_price_krw: float = Field(500_000_000, ge=0)


class MarketUniversePatchRequest(BaseModel):
    status: str | None = None
    is_enabled: bool | None = None
    is_live_allowed: bool | None = None
    is_auto_selectable: bool | None = None
    scan_rank: int | None = None
    score: float | None = None
    reason: str | None = None
    min_24h_trade_price_krw: float | None = None


class CandidateStrategyRequest(BaseModel):
    name: str | None = None
    description: str = ""
    strategy: str
    parameters: dict[str, Any]
    unit: int
    market: str = Field(DEFAULT_MARKET, pattern=r"^[A-Z]+-[A-Z0-9]+$")
    backtest_period: str
    score: float
    backtest_total_return: float = 0.0
    backtest_mdd: float = 0.0
    backtest_win_rate: float = 0.0
    backtest_profit_factor: float = 0.0
    backtest_trade_count: int = 0
    backtest_average_trade_pnl: float = 0.0
    warning: str = ""
    status: str = "ACTIVE"


class CandidateAutoSaveRequest(BaseModel):
    candidates: list[CandidateStrategyRequest] = Field(default_factory=list)
    min_score: float = 70.0


class CandidateStrategyUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    strategy: str | None = None
    parameters: dict[str, Any] | None = None
    unit: int | None = None
    market: str | None = None
    backtest_period: str | None = None
    score: float | None = None
    backtest_total_return: float | None = None
    backtest_mdd: float | None = None
    backtest_win_rate: float | None = None
    backtest_profit_factor: float | None = None
    backtest_trade_count: int | None = None
    backtest_average_trade_pnl: float | None = None
    warning: str | None = None
    status: str | None = None


class CandidateStrategyToggleRequest(BaseModel):
    status: str | None = None


class CandidatePromotionRequest(BaseModel):
    status: str | None = None
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class AutoSelectorRequest(BaseModel):
    exchange: str = Field("bithumb", pattern=r"^(upbit|bithumb)$")


class AutonomousOrchestratorRunRequest(BaseModel):
    reason: str = "MANUAL_RUN_NOW"


class AppSettingsRequest(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict)


class BotPolicyPatchRequest(BaseModel):
    auto_trading_enabled: bool | None = None
    max_total_exposure_krw: float | None = Field(None, gt=0)
    daily_loss_limit_pct: float | None = Field(None, gt=0, le=100)


class ForwardPaperStartRequest(BaseModel):
    candidate_strategy_id: int
    initial_balance_krw: float = Field(1_000_000, gt=0)
    risk: dict[str, Any] = Field(default_factory=dict)


class ForwardPaperStopRequest(BaseModel):
    session_id: int | None = None


class LiveArmRequest(BaseModel):
    acknowledged: bool = False
    confirmation: str = ""


class LiveEmergencyResetRequest(BaseModel):
    confirmation: str = ""


class LiveOrderPreviewRequest(BaseModel):
    request_id: str | None = None
    exchange: str | None = Field(None, pattern=r"^(upbit|bithumb)$")
    market: str = Field(DEFAULT_MARKET, pattern=r"^[A-Z]+-[A-Z0-9]+$")
    side: str = Field("BUY", pattern=r"^(BUY|SELL)$")
    order_type: str = Field("LIMIT", pattern=r"^(LIMIT|MARKET)$")
    price: float | None = Field(None, ge=0)
    amount_krw: float | None = Field(None, ge=0)
    volume: float | None = Field(None, ge=0)


class LiveOrderPlaceRequest(BaseModel):
    request_id: str
    final_confirmation: str = ""


class AutoLivePilotStartRequest(BaseModel):
    candidate_strategy_id: int
    order_amount_krw: float = Field(10000, ge=10000)
    confirmation: str = ""
    order_confirmation: str = ""


class LiveStrategyPilotStartRequest(BaseModel):
    candidate_strategy_id: int | None = None
    confirmation: str = ""
    order_confirmation: str = ""


class RuntimeStartRequest(BaseModel):
    candidate_strategy_id: int | None = None
    confirmation: str = ""
    order_confirmation: str = ""


class SmartRehearsalReviewRequest(BaseModel):
    request_id: str
    exchange: str = Field("bithumb", pattern=r"^(upbit|bithumb)$")
    market: str = Field(DEFAULT_MARKET, pattern=r"^[A-Z]+-[A-Z0-9]+$")
    decision: str = Field(..., pattern=r"^(APPROVED|REJECTED)$")
    note: str = Field("", max_length=1000)


class ImportExchangePositionRequest(BaseModel):
    confirmation: str = ""


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class ExitCandidateActionRequest(BaseModel):
    candidate_id: int


class ExitOrderPreviewRequest(BaseModel):
    exit_candidate_id: int
    manual_confirmed: bool = False


class ExitOrderSubmitRequest(BaseModel):
    request_id: str
    final_confirmation: str = ""


class ExitOrderCancelRequest(BaseModel):
    request_id: str


def _fatal_validation_warnings(warnings: list[str]) -> list[str]:
    fatal_keywords = [
        "MDD",
        "loss",
        "loss after fees",
        "insufficient",
        "API",
        "volatility",
        "liquidity",
    ]
    return [warning for warning in warnings if any(keyword.lower() in str(warning).lower() for keyword in fatal_keywords)]


def _validation_row_passes_candidate_gate(row: dict, *, min_score: float = 70.0) -> bool:
    metrics = row.get("metrics") or {}
    warnings = [str(item) for item in row.get("warnings") or []]
    return (
        float(row.get("stability_score") or 0.0) >= min_score
        and float(metrics.get("total_return") or 0.0) > 0
        and float(metrics.get("mdd") or 0.0) <= 0.15
        and not _fatal_validation_warnings(warnings)
    )


def _candidate_from_validation_row(row: dict, *, status: str = "BACKTEST_PASSED") -> dict:
    metrics = row.get("metrics") or {}
    warnings = [str(item) for item in row.get("warnings") or []]
    return {
        "name": f"{row['market']} {row['strategy']} {row['unit']}m {float(row.get('stability_score') or 0):.2f}pt",
        "description": "Auto-saved from multi-market strategy validation.",
        "strategy": row["strategy"],
        "parameters": row.get("parameters") or {},
        "unit": int(row["unit"]),
        "market": row["market"],
        "backtest_period": str(row.get("period_label") or "multi-market"),
        "score": float(row.get("stability_score") or metrics.get("score") or 0.0),
        "backtest_total_return": float(metrics.get("total_return") or 0.0),
        "backtest_mdd": float(metrics.get("mdd") or 0.0),
        "backtest_win_rate": float(metrics.get("win_rate") or 0.0),
        "backtest_profit_factor": float(metrics.get("profit_factor") or 0.0),
        "backtest_trade_count": int(metrics.get("trade_count") or 0),
        "backtest_average_trade_pnl": (
            float(metrics.get("total_return") or 0.0) / int(metrics.get("trade_count") or 1)
        ),
        "warning": ", ".join(warnings),
        "status": status,
    }


def _annotate_validation_decisions(rows: list[dict], *, min_score: float) -> list[dict]:
    annotated = []
    for row in rows:
        decision = "AUTO_SAVE" if _validation_row_passes_candidate_gate(row, min_score=min_score) else "REJECT"
        annotated.append({**row, "decision": decision})
    return annotated


@asynccontextmanager
async def lifespan(_: FastAPI):
    load_server_env()
    _configure_runtime_logging()
    _.state.server_started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    _.state.instance_id = os.getenv("RUNTIME_INSTANCE_ID", f"{socket.gethostname()}-{uuid.uuid4().hex[:12]}")
    init_db()
    schema_status = ensure_required_schema(repair=True)
    _.state.db_schema_status = schema_status
    logger.info(
        "[db-schema] path=%s status=%s missing_tables=%s repair_status=%s",
        schema_status.get("database_path"),
        schema_status.get("schema_status"),
        schema_status.get("missing_tables", []),
        schema_status.get("repair_status"),
    )
    if schema_status.get("schema_status") != "OK":
        raise RuntimeError(f"DB_SCHEMA_MISSING: {', '.join(schema_status.get('missing_tables', []))}")
    ensure_default_candidate_strategies()
    reset_live_runtime_state()
    insert_live_mode_event("SERVER_START", current_live_mode(), "서버 시작 시 실거래 모드는 자동 잠금 상태로 초기화되었습니다.")
    recovery_result = await run_startup_live_recovery_async()
    logger.info("[live-recovery] startup recovery result=%s", recovery_result)
    stopped_forward_sessions = pause_running_forward_sessions_on_startup()
    if stopped_forward_sessions:
        logger.info(
            "[paper-forward] stopped %s RUNNING sessions on server startup; auto-resume is disabled",
            stopped_forward_sessions,
        )
    scheduler = BackgroundScheduler(timezone="Asia/Seoul")
    scheduler.add_job(
        run_scheduler_tick,
        "interval",
        seconds=60,
        id="paper_live_tick",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        run_forward_scheduler_tick,
        "interval",
        seconds=60,
        id="paper_forward_tick",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        run_auto_live_pilot_tick,
        "interval",
        seconds=10,
        id="auto_live_pilot_tick",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.add_job(
        run_live_strategy_tick,
        "interval",
        seconds=10,
        id="live_strategy_pilot_tick",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    discovery_config = discovery_scheduler_config()
    orchestrator_config = autonomous_orchestrator_config()
    scheduler.add_job(
        run_autonomous_orchestrator_background,
        "interval",
        minutes=int(orchestrator_config["interval_minutes"]),
        args=["SCHEDULED"],
        id="autonomous_orchestrator_tick",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.start()
    if orchestrator_config["bootstrap_enabled"]:
        scheduler.add_job(
            run_autonomous_orchestrator_background,
            "date",
            run_date=datetime.now(timezone.utc) + timedelta(seconds=90),
            args=["SERVER_STARTUP"],
            id="autonomous_orchestrator_bootstrap",
            max_instances=1,
            replace_existing=True,
        )
    _.state.scheduler = scheduler
    _.state.scheduler_started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    logger.info("[paper-live] scheduler started interval_seconds=60")
    logger.info("[paper-forward] scheduler started interval_seconds=60")
    logger.info("[auto-live] pilot scheduler started interval_seconds=10")
    logger.info("[live-strategy] pilot scheduler started interval_seconds=10")
    logger.info("[autonomous-orchestrator] scheduler started interval_minutes=%s", orchestrator_config["interval_minutes"])
    logger.info("[autonomous-orchestrator] bootstrap_enabled=%s discovery_exchange=%s", orchestrator_config["bootstrap_enabled"], discovery_config["exchange"])
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Coin Bot Lab API", version="0.0.1", lifespan=lifespan)
_cors_origins = [origin.strip() for origin in os.getenv("FRONTEND_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def require_admin_for_api(request: Request, call_next):
    if request.method == "OPTIONS" or not request.url.path.startswith("/api/") or request.url.path.startswith("/api/auth/"):
        return await call_next(request)
    try:
        require_admin_session(request)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)


@app.get("/api/auth/status")
def auth_status_endpoint(request: Request) -> dict:
    return auth_status(request)


@app.post("/api/auth/login")
def login_endpoint(payload: AdminLoginRequest, response: Response) -> dict:
    return login_admin(payload.username, payload.password, response)


@app.post("/api/auth/logout")
def logout_endpoint(response: Response) -> dict:
    return logout_admin(response)


@app.get("/api/runtime/status")
def get_runtime_status(request: Request) -> dict:
    return _runtime_status_payload(request)


@app.post("/api/runtime/start")
def start_runtime_endpoint(payload: RuntimeStartRequest, request: Request, background_tasks: BackgroundTasks) -> dict:
    if payload.confirmation != AUTO_STRATEGY_CONFIRMATION:
        raise HTTPException(status_code=400, detail=f"{AUTO_STRATEGY_CONFIRMATION} confirmation is required.")
    acquired, current_lock, status_payload = _try_acquire_runtime_lock_for_start("admin-ui", request)
    if not acquired:
        return {
            "ok": False,
            "message": "다른 서버 인스턴스가 이미 자동매매 Runtime을 실행 중입니다.",
            "runtime_lock": current_lock,
            **(status_payload or _runtime_status_payload(request)),
        }
    result = start_live_strategy_pilot(
        candidate_strategy_id=payload.candidate_strategy_id,
        confirmation=payload.confirmation,
        order_confirmation=payload.order_confirmation,
    )
    if result.get("ok") is False:
        release_runtime_lock(lock_id=RUNTIME_LOCK_ID, instance_id=_instance_id(), status="STOPPED")
    elif autonomous_orchestrator_config()["on_start_enabled"]:
        background_tasks.add_task(run_autonomous_orchestrator_background, "RUNTIME_STARTED")
    return {**result, **_runtime_status_payload(request)}


@app.post("/api/runtime/stop")
def stop_runtime_endpoint(request: Request) -> dict:
    strategy = stop_live_strategy_pilot()
    auto = stop_auto_live_pilot()
    release_runtime_lock(lock_id=RUNTIME_LOCK_ID, instance_id=_instance_id(), status="STOPPED")
    insert_live_mode_event("AUTO_TRADING_STOPPED_BY_USER", current_live_mode(), "사용자가 UI에서 자동매매 Runtime을 중지했습니다.")
    return {"ok": True, "strategy": strategy, "auto": auto, **_runtime_status_payload(request)}


@app.get("/health")
def health(request: Request) -> dict:
    return _health_payload(request)


@app.get("/health/db-schema")
def health_db_schema() -> dict:
    return ensure_required_schema(repair=True)


@app.get("/health/live")
def health_live(request: Request) -> dict:
    payload = _health_payload(request)
    return {
        "server_status": payload["server_status"],
        "database_status": payload["database_status"],
        "scheduler_status": payload["scheduler_status"],
        "auto_runtime_status": payload["auto_runtime_status"],
    }


@app.get("/health/broker")
def health_broker(request: Request) -> dict:
    payload = _health_payload(request)
    return {
        "broker_status": payload["broker_status"],
        "selected_exchange": payload["selected_exchange"],
        "live_trading_enabled": payload["live_trading_enabled"],
        "auto_trading_enabled": payload["auto_trading_enabled"],
        "latest_order_sync_time": payload["latest_order_sync_time"],
        "latest_balance_sync_time": payload["latest_balance_sync_time"],
    }


@app.get("/health/risk")
def health_risk(request: Request) -> dict:
    payload = _health_payload(request)
    return {
        "risk_manager_status": payload["risk_manager_status"],
        "emergency_stop_status": payload["emergency_stop_status"],
        "selected_exchange": payload["selected_exchange"],
    }


@app.get("/health/scheduler")
def health_scheduler(request: Request) -> dict:
    payload = _health_payload(request)
    return {
        "scheduler_status": payload["scheduler_status"],
        "started_at": getattr(request.app.state, "scheduler_started_at", None),
        "jobs": payload.get("scheduler_jobs", []),
    }


@app.get("/api/candles")
async def get_candles(
    market: str = Query(DEFAULT_MARKET),
    unit: int = Query(1),
    count: int = Query(200, ge=1, le=1000),
) -> dict:
    try:
        fresh = await fetch_day_candles(market=market, count=count) if unit == 1440 else await fetch_minute_candles(market=market, unit=unit, count=count)
        inserted = insert_candles(fresh)
        candles = load_candles(market, unit, count)
        return {"market": market, "unit": unit, "inserted": inserted, "candles": candles}
    except UpbitClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/markets/universe")
def get_market_universe(
    exchange: str | None = Query(None, pattern=r"^(upbit|bithumb)$"),
    enabled_only: bool = Query(False),
    auto_selectable_only: bool = Query(False),
    live_allowed_only: bool = Query(False),
    limit: int = Query(200, ge=1, le=500),
) -> dict:
    return {
        "markets": load_market_universe(
            exchange=exchange,
            enabled_only=enabled_only,
            auto_selectable_only=auto_selectable_only,
            live_allowed_only=live_allowed_only,
            limit=limit,
        )
    }


@app.post("/api/markets/scan")
async def scan_markets(payload: MarketScanRequest) -> dict:
    try:
        return await scan_market_universe(
            exchange=payload.exchange,
            top_n=payload.top_n,
            max_candidates=payload.max_candidates,
            min_24h_trade_price_krw=payload.min_24h_trade_price_krw,
        )
    except (UpbitClientError, ValueError) as exc:
        raise HTTPException(status_code=502 if isinstance(exc, UpbitClientError) else 400, detail=str(exc)) from exc


@app.patch("/api/markets/universe/{market_id}")
def patch_market_universe(market_id: int, payload: MarketUniversePatchRequest) -> dict:
    item = update_market_universe_item(market_id, {key: value for key, value in payload.model_dump(exclude_unset=True).items() if value is not None})
    if item is None:
        raise HTTPException(status_code=404, detail="Market universe item not found.")
    return {"market": item}


@app.post("/api/backtests")
async def create_backtest(payload: BacktestRequest) -> dict:
    try:
        fresh = await fetch_minute_candles(
            market=payload.market,
            unit=payload.unit,
            count=payload.count,
        )
        insert_candles(fresh)
        candles = load_candles(payload.market, payload.unit, payload.count)
        result = run_backtest(candles, payload.strategy, payload.settings, payload.risk, market=payload.market)
        backtest_id = save_backtest(
            payload.market,
            payload.unit,
            payload.strategy,
            payload.settings,
            payload.risk,
            result["metrics"],
            result["signals"],
            result["orders"],
        )
        return {
            "id": backtest_id,
            "market": payload.market,
            "unit": payload.unit,
            "strategy": payload.strategy,
            **result,
        }
    except UpbitClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/backtests/compare")
async def compare_backtests(payload: BacktestCompareRequest) -> dict:
    strategies = [strategy for strategy in payload.strategies if strategy in ALLOWED_STRATEGIES]
    if not strategies:
        raise HTTPException(status_code=400, detail="비교할 전략이 없습니다.")
    try:
        candles = await _load_period_candles(
            payload.market,
            payload.unit,
            payload.start_time_utc,
            payload.end_time_utc,
        )
        result = compare_strategies(
            candles,
            strategies,
            payload.settings_by_strategy,
            payload.risk,
            market=payload.market,
        )
        return {
            "market": payload.market,
            "unit": payload.unit,
            "start_time_utc": payload.start_time_utc,
            "end_time_utc": payload.end_time_utc,
            "candle_count": len(candles),
            **result,
        }
    except UpbitClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/paper-trading/simulate")
async def simulate_paper_trading(payload: PaperTradingRequest) -> dict:
    try:
        fresh = await fetch_minute_candles(
            market=payload.market,
            unit=payload.unit,
            count=payload.count,
        )
        insert_candles(fresh)
        candles = load_candles(payload.market, payload.unit, payload.count)
        result = run_paper_trading(
            candles,
            payload.market,
            payload.unit,
            payload.strategy,
            payload.settings,
            payload.risk,
        )
        session_id = save_paper_session(result)
        return {"id": session_id, **result}
    except UpbitClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/paper-trading/start")
async def start_paper_trading(payload: PaperTradingRequest) -> dict:
    return await simulate_paper_trading(payload)


@app.post("/api/paper-trading/stop")
def stop_paper_trading() -> dict:
    session = stop_latest_paper_session()
    if session is None:
        latest = load_latest_paper_session()
        if latest is None:
            return {"status": "STOPPED", "message": "중지할 페이퍼 트레이딩 세션이 없습니다."}
        return {**latest, "status": "STOPPED"}
    return session


@app.get("/api/paper-trading/latest")
def latest_paper_trading() -> dict:
    session = load_latest_paper_session()
    if session is None:
        return {"status": "EMPTY"}
    return session


@app.post("/api/strategy-validation/run")
async def run_validation(payload: StrategyValidationRequest) -> dict:
    if payload.strategy not in ALLOWED_STRATEGIES:
        raise HTTPException(status_code=400, detail="지원하지 않는 전략입니다.")
    try:
        result = await run_strategy_validation(
            market=payload.market,
            strategy=payload.strategy,
            timeframes=payload.timeframes,
            periods=payload.periods,
            custom_start_time_utc=payload.custom_start_time_utc,
            custom_end_time_utc=payload.custom_end_time_utc,
            base_settings=payload.settings,
            risk=payload.risk,
            load_period_candles=_load_period_candles,
        )
        run_id = save_validation_run(
            payload.market,
            payload.strategy,
            payload.model_dump(),
            result["rows"],
        )
        return {"run_id": run_id, **result}
    except UpbitClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/strategy-validation/multi-market")
async def run_multi_market_validation(payload: MultiMarketValidationRequest) -> dict:
    strategies = [strategy for strategy in payload.strategies if strategy in BUY_CANDIDATE_STRATEGIES]
    if not strategies:
        raise HTTPException(status_code=400, detail="No supported strategies were requested.")
    markets = [market for market in dict.fromkeys(payload.markets) if market.startswith("KRW-")]
    if not markets:
        universe = load_market_universe(exchange=payload.exchange, enabled_only=True, auto_selectable_only=True, limit=payload.max_markets)
        markets = [str(item["market"]) for item in universe]
    if not markets:
        markets = [DEFAULT_MARKET]
    markets = markets[: payload.max_markets]

    started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    all_rows: list[dict] = []
    errors: list[dict] = []
    saved_candidates: list[dict] = []
    for market in markets:
        for strategy in strategies:
            try:
                result = await run_strategy_validation(
                    market=market,
                    strategy=strategy,
                    timeframes=payload.timeframes,
                    periods=payload.periods,
                    custom_start_time_utc=None,
                    custom_end_time_utc=None,
                    base_settings={},
                    risk=payload.risk,
                    load_period_candles=_load_period_candles,
                )
                all_rows.extend(_annotate_validation_decisions(result["rows"], min_score=payload.min_score))
            except (UpbitClientError, ValueError) as exc:
                errors.append({"market": market, "strategy": strategy, "error": str(exc)})

    ranking = sorted(all_rows, key=lambda row: float(row.get("stability_score") or 0.0), reverse=True)
    if payload.auto_save_candidates:
        for row in ranking:
            if row.get("decision") != "AUTO_SAVE":
                continue
            candidate_id = save_candidate_strategy(_candidate_from_validation_row(row, status="BACKTEST_PASSED"))
            saved = load_candidate_strategy(candidate_id)
            if saved:
                saved_candidates.append(saved)
            if len(saved_candidates) >= payload.max_markets:
                break
    finished_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    summary = {
        "market_count": len(markets),
        "strategy_count": len(strategies),
        "row_count": len(ranking),
        "saved_candidate_count": len(saved_candidates),
        "error_count": len(errors),
    }
    run_id = save_strategy_validation_run(
        {
            "exchange": payload.exchange,
            "market_count": len(markets),
            "strategy_count": len(strategies),
            "timeframes": payload.timeframes,
            "periods": payload.periods,
            "risk": payload.risk,
            "request": payload.model_dump(),
            "summary": summary,
            "status": "COMPLETED_WITH_ERRORS" if errors else "COMPLETED",
            "started_at": started_at,
            "finished_at": finished_at,
        },
        ranking,
    )
    return {
        "run_id": run_id,
        "exchange": payload.exchange,
        "markets": markets,
        "strategies": strategies,
        "summary": summary,
        "rows": ranking,
        "saved_candidates": saved_candidates,
        "errors": errors,
    }


@app.post("/api/candidate-strategies")
def create_candidate_strategy(payload: CandidateStrategyRequest) -> dict:
    candidate = payload.model_dump()
    candidate_id = save_candidate_strategy(candidate)
    return {"id": candidate_id, **candidate}


@app.get("/api/candidate-strategies")
def list_candidate_strategies() -> dict:
    return {"candidates": load_candidate_strategies()}


@app.get("/api/candidate-strategies/live-eligible")
def list_live_eligible_candidate_strategies() -> dict:
    return {"candidates": load_live_eligible_candidate_strategies()}


@app.post("/api/candidate-strategies/auto-save")
def auto_save_candidate_strategies(payload: CandidateAutoSaveRequest) -> dict:
    saved = []
    rejected = []
    for request in payload.candidates:
        candidate = request.model_dump()
        gate_row = {
            "market": candidate["market"],
            "strategy": candidate["strategy"],
            "unit": candidate["unit"],
            "parameters": candidate["parameters"],
            "period_label": candidate["backtest_period"],
            "stability_score": candidate["score"],
            "warnings": [candidate.get("warning", "")] if candidate.get("warning") else [],
            "metrics": {
                "total_return": candidate["backtest_total_return"],
                "mdd": candidate["backtest_mdd"],
                "win_rate": candidate["backtest_win_rate"],
                "profit_factor": candidate["backtest_profit_factor"],
                "trade_count": candidate["backtest_trade_count"],
            },
        }
        if not _validation_row_passes_candidate_gate(gate_row, min_score=payload.min_score):
            rejected.append({"candidate": candidate, "reason": "AUTO_SAVE_GATE_FAILED"})
            continue
        candidate["status"] = "BACKTEST_PASSED"
        candidate_id = save_candidate_strategy(candidate)
        saved_candidate = load_candidate_strategy(candidate_id)
        if saved_candidate:
            saved.append(saved_candidate)
    return {"saved": saved, "rejected": rejected}


@app.patch("/api/candidate-strategies/{candidate_id}")
def patch_candidate_strategy(candidate_id: int, payload: CandidateStrategyUpdateRequest) -> dict:
    updates = {key: value for key, value in payload.model_dump().items() if value is not None}
    candidate = update_candidate_strategy(candidate_id, updates)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate strategy not found.")
    return {"candidate": candidate}


@app.post("/api/candidate-strategies/{candidate_id}/clone")
def clone_candidate_strategy_endpoint(candidate_id: int) -> dict:
    candidate = clone_candidate_strategy(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate strategy not found.")
    return {"candidate": candidate}


@app.post("/api/candidate-strategies/{candidate_id}/toggle")
def toggle_candidate_strategy_endpoint(candidate_id: int, payload: CandidateStrategyToggleRequest) -> dict:
    current = load_candidate_strategy(candidate_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Candidate strategy not found.")
    next_status = payload.status or ("INACTIVE" if current.get("status") == "ACTIVE" else "ACTIVE")
    candidate = set_candidate_strategy_status(candidate_id, next_status)
    return {"candidate": candidate}


def _next_promotion_status(current_status: str, requested_status: str | None) -> str:
    if requested_status:
        return requested_status
    flow = {
        "DISCOVERED": "BACKTEST_RUNNING",
        "BACKTEST_RUNNING": "BACKTEST_PASSED",
        "BACKTEST_PASSED": "SHADOW_RUNNING",
        "SHADOW_RUNNING": "SHADOW_PASSED",
        "SHADOW_PASSED": "LIVE_ELIGIBLE",
        "LIVE_ELIGIBLE": "LIVE_ACTIVE",
        "LIVE_ACTIVE": "LIVE_ACTIVE",
        "ACTIVE": "BACKTEST_PASSED",
        "INACTIVE": "PAUSED",
    }
    return flow.get(current_status, "PAUSED")


@app.post("/api/candidate-strategies/{candidate_id}/promote")
def promote_candidate_strategy_endpoint(candidate_id: int, payload: CandidatePromotionRequest) -> dict:
    current = load_candidate_strategy(candidate_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Candidate strategy not found.")
    current_status = str(current.get("status") or "ACTIVE")
    to_status = _next_promotion_status(current_status, payload.status)
    if to_status == "LIVE_ELIGIBLE" and current_status != "SHADOW_PASSED":
        raise HTTPException(status_code=409, detail="Only SHADOW_PASSED candidates can be promoted to LIVE_ELIGIBLE.")
    if to_status == "LIVE_ACTIVE" and current_status not in {"LIVE_ELIGIBLE", "LIVE_ACTIVE"}:
        raise HTTPException(status_code=409, detail="Only LIVE_ELIGIBLE candidates can become LIVE_ACTIVE.")
    candidate = promote_candidate_strategy(candidate_id, to_status, reason=payload.reason, metadata=payload.metadata)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate strategy not found.")
    return {"candidate": candidate}


@app.post("/api/candidate-strategies/{candidate_id}/reject")
def reject_candidate_strategy_endpoint(candidate_id: int, payload: CandidatePromotionRequest) -> dict:
    candidate = reject_candidate_strategy(candidate_id, reason=payload.reason, metadata=payload.metadata)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate strategy not found.")
    return {"candidate": candidate}


@app.delete("/api/candidate-strategies/{candidate_id}")
def delete_candidate_strategy_endpoint(candidate_id: int) -> dict:
    current = load_candidate_strategy(candidate_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Candidate strategy not found.")
    deleted = delete_candidate_strategy(candidate_id)
    if not deleted:
        raise HTTPException(status_code=409, detail="실행 이력이나 주문/포지션과 연결된 전략은 삭제할 수 없습니다. 비활성화를 사용하세요.")
    return {"ok": True, "deleted_id": candidate_id}


@app.get("/api/auto-strategy-selector/status")
def get_auto_strategy_selector_status(exchange: str = Query("bithumb", pattern=r"^(upbit|bithumb)$")) -> dict:
    return auto_strategy_selector_status(exchange=exchange)


@app.post("/api/auto-strategy-selector/evaluate")
def evaluate_auto_strategy_selector_endpoint(payload: AutoSelectorRequest) -> dict:
    return evaluate_auto_strategy_selector(exchange=payload.exchange, apply=False)


@app.post("/api/auto-strategy-selector/apply-best")
def apply_best_auto_strategy_endpoint(payload: AutoSelectorRequest) -> dict:
    return apply_selector_if_allowed(exchange=payload.exchange)


@app.post("/api/strategy-promotion/run")
def run_strategy_promotion_endpoint(payload: AutoSelectorRequest) -> dict:
    return run_strategy_promotion_pipeline(exchange=payload.exchange)


@app.get("/api/capital-snapshot")
async def get_capital_snapshot(exchange: str = Query("bithumb", pattern=r"^(upbit|bithumb)$")) -> dict:
    snapshot = await build_capital_snapshot_async(exchange)
    return {
        "snapshot": snapshot,
        "warnings": snapshot.get("warnings", []),
        "blockers": snapshot.get("blockers", []),
    }


@app.post("/api/capital-snapshot/reconcile")
async def reconcile_capital_snapshot(exchange: str = Query("bithumb", pattern=r"^(upbit|bithumb)$")) -> dict:
    snapshot = await build_capital_snapshot_async(exchange)
    return {
        "ok": not snapshot.get("snapshot_error"),
        "snapshot": snapshot,
        "warnings": snapshot.get("warnings", []),
        "blockers": snapshot.get("blockers", []),
    }


@app.get("/api/capital-allocator/status")
def get_capital_allocator_status(exchange: str = Query("bithumb", pattern=r"^(upbit|bithumb)$")) -> dict:
    return capital_allocator_status(exchange=exchange)


@app.post("/api/capital-allocator/run-now")
def run_capital_allocator_endpoint(payload: AutoSelectorRequest | None = None) -> dict:
    exchange = payload.exchange if payload else "bithumb"
    return run_capital_allocator_once("MANUAL_RUN_NOW", exchange=exchange)


@app.get("/api/position-slots")
def get_position_slots(exchange: str = Query("bithumb", pattern=r"^(upbit|bithumb)$")) -> dict:
    status = capital_allocator_status(exchange=exchange)
    return {
        "exchange": status["exchange"],
        "max_slots": status["max_slots"],
        "open_slot_count": status["open_slot_count"],
        "empty_slot_count": status["empty_slot_count"],
        "slots": status["slots"],
    }


@app.get("/api/next-entry-queue")
def get_next_entry_queue(exchange: str = Query("bithumb", pattern=r"^(upbit|bithumb)$")) -> dict:
    status = capital_allocator_status(exchange=exchange)
    return {
        "exchange": status["exchange"],
        "queue": status["next_entry_queue"],
    }


@app.get("/api/strategy-discovery-scheduler/status")
def get_strategy_discovery_scheduler_status() -> dict:
    return discovery_scheduler_status()


@app.get("/api/autonomous-orchestrator/status")
def get_autonomous_orchestrator_status() -> dict:
    return autonomous_orchestrator_status()


@app.post("/api/autonomous-orchestrator/run-now")
def run_autonomous_orchestrator_endpoint(payload: AutonomousOrchestratorRunRequest | None = None) -> dict:
    reason = payload.reason if payload else "MANUAL_RUN_NOW"
    return run_autonomous_orchestrator_once(reason=reason)


def _live_status(exchange: str | None = None) -> dict:
    config = LiveTradingConfig.for_exchange(exchange) if exchange else LiveTradingConfig.from_env()
    mode = current_live_mode()
    broker_status = "READY" if config.api_key_loaded else "API_KEY_MISSING"
    if config.api_key_loaded and not config.live_trading_enabled:
        broker_status = "READY_READ_ONLY"
    if is_emergency_stopped():
        broker_status = "EMERGENCY_STOPPED"
    return {
        "mode": mode,
        "exchange": config.exchange,
        "live_trading_enabled": config.live_trading_enabled,
        "broker_status": broker_status,
        "api_key_loaded": config.api_key_loaded,
        "access_key_loaded": config.access_key_loaded,
        "secret_key_loaded": config.secret_key_loaded,
        "balance_fetch_status": "NOT_REQUESTED",
        "order_chance_status": "NOT_REQUESTED",
        "risk_manager_status": "ACTIVE" if mode == "LIVE_MANUAL_ONLY" else "LOCKED",
        "emergency_stop": is_emergency_stopped(),
        "max_live_order_krw": config.max_live_order_krw,
        "daily_loss_limit_percent": config.max_daily_live_loss_percent,
        "min_order_krw": config.min_order_krw,
        "last_live_order_time": get_last_live_order_time(),
        "api_key_policy": "API Key는 서버 환경변수에서만 읽으며, 출금 권한이 없는 키만 사용하세요.",
    }


async def _market_snapshot(market: str) -> dict | None:
    try:
        fresh = await fetch_minute_candles(market=market, unit=1, count=1)
        if not fresh:
            return None
        candle = fresh[-1]
        price = float(candle["trade_price"])
        return {
            "price": price,
            "range_rate": ((float(candle["high_price"]) - float(candle["low_price"])) / price) if price > 0 else 0.0,
            "volume": float(candle["candle_acc_trade_volume"]),
            "candle_time_utc": candle["candle_date_time_utc"],
        }
    except Exception:
        return None


async def _market_snapshots(markets: list[str], exchange: str = "upbit") -> dict[str, dict]:
    unique_markets = [market for market in dict.fromkeys(markets) if market]
    if not unique_markets:
        return {}

    base_url = "https://api.bithumb.com" if exchange == "bithumb" else "https://api.upbit.com"
    snapshots: dict[str, dict] = {}
    try:
        tickers = await fetch_tickers(unique_markets, base_url=base_url)
        for ticker in tickers:
            market = str(ticker.get("market", ""))
            price = float(ticker.get("trade_price") or ticker.get("prev_closing_price") or 0)
            if not market or price <= 0:
                continue
            snapshots[market] = {
                "price": price,
                "signed_change_rate": float(ticker.get("signed_change_rate") or 0),
                "change_rate": float(ticker.get("change_rate") or 0),
                "acc_trade_price_24h": float(ticker.get("acc_trade_price_24h") or 0),
                "candle_time_utc": ticker.get("trade_timestamp"),
            }
    except Exception:
        snapshots = {}

    missing_markets = [market for market in unique_markets if market not in snapshots]
    for market in missing_markets:
        snapshot = await _market_snapshot(market)
        if snapshot:
            snapshots[market] = snapshot
    return snapshots


async def _safe_live_balances() -> tuple[dict, str, str | None]:
    config = LiveTradingConfig.from_env()
    if not config.live_trading_enabled:
        return {"by_currency": {}, "krw": {"balance": 0, "locked": 0}, "btc": {"balance": 0, "locked": 0}, "eth": {"balance": 0, "locked": 0}}, "DISABLED", "LIVE_TRADING_ENABLED=false 입니다."
    if not config.api_key_loaded:
        return {"by_currency": {}, "krw": {"balance": 0, "locked": 0}, "btc": {"balance": 0, "locked": 0}, "eth": {"balance": 0, "locked": 0}}, "API_KEY_MISSING", "UPBIT_ACCESS_KEY/UPBIT_SECRET_KEY가 필요합니다."
    try:
        balances = await LiveBroker().get_balance()
        return balances, "SUCCESS", None
    except LiveBrokerError as exc:
        return {"by_currency": {}, "krw": {"balance": 0, "locked": 0}, "btc": {"balance": 0, "locked": 0}, "eth": {"balance": 0, "locked": 0}}, "FAILED", str(exc)


async def _safe_live_balances_for_exchange(exchange: str | None = None) -> tuple[dict, str, str | None]:
    empty = {"by_currency": {}, "krw": {"balance": 0, "locked": 0}, "btc": {"balance": 0, "locked": 0}, "eth": {"balance": 0, "locked": 0}}
    config = LiveTradingConfig.for_exchange(exchange) if exchange else LiveTradingConfig.from_env()
    if not config.api_key_loaded:
        prefix = "BITHUMB" if config.exchange == "bithumb" else "UPBIT"
        return empty, "API_KEY_MISSING", f"{prefix}_ACCESS_KEY/{prefix}_SECRET_KEY가 필요합니다."
    try:
        balances = await get_live_broker(config.exchange).get_balances()
        return balances, "SUCCESS", None
    except LiveBrokerError as exc:
        return empty, _broker_error_code(str(exc)), str(exc)


async def _safe_order_chance(market: str, exchange: str | None = None) -> tuple[dict, str, str | None]:
    config = LiveTradingConfig.for_exchange(exchange) if exchange else LiveTradingConfig.from_env()
    if not config.api_key_loaded:
        prefix = "BITHUMB" if config.exchange == "bithumb" else "UPBIT"
        return {}, "API_KEY_MISSING", f"{prefix}_ACCESS_KEY/{prefix}_SECRET_KEY가 필요합니다."
    try:
        chance = await get_live_broker(config.exchange).get_order_chance(market)
        return chance, "SUCCESS", None
    except LiveBrokerError as exc:
        return {}, _broker_error_code(str(exc)), str(exc)


def _broker_error_code(message: str) -> str:
    lowered = message.lower()
    if any(pattern in lowered for pattern in ["ip", "authorization ip", "no_authorization_ip", "not allowed", "허용", "인증 ip"]):
        return "API_IP_NOT_ALLOWED"
    if any(pattern in lowered for pattern in ["jwt", "authorization", "unauthorized", "invalid api", "invalid_access_key", "authentication"]):
        return "BROKER_AUTH_ERROR"
    return "FAILED"


@app.get("/api/live-trading/status")
@app.get("/api/live/status")
def live_trading_status(exchange: str | None = Query(None, pattern=r"^(upbit|bithumb)$")) -> dict:
    return _live_status(exchange)


@app.get("/api/live-trading/balances")
@app.get("/api/live/balances")
async def live_trading_balances(exchange: str | None = Query(None, pattern=r"^(upbit|bithumb)$")) -> dict:
    global _latest_balance_sync_time_utc
    selected_config = LiveTradingConfig.for_exchange(exchange) if exchange else LiveTradingConfig.from_env()
    balances, status, error = await _safe_live_balances_for_exchange(selected_config.exchange)
    _latest_balance_sync_time_utc = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    estimated_total = float(balances.get("krw", {}).get("balance", 0)) + float(balances.get("krw", {}).get("locked", 0))
    currencies = [
        str(currency).upper()
        for currency, entry in (balances.get("by_currency") or {}).items()
        if str(currency).upper() != "KRW"
        and (float(entry.get("balance", 0)) + float(entry.get("locked", 0))) > 0
    ]
    markets = [f"KRW-{currency}" for currency in currencies]
    prices = await _market_snapshots(markets, selected_config.exchange)
    for currency in currencies:
        entry = balances.get("by_currency", {}).get(currency, {})
        snapshot = prices.get(f"KRW-{currency}")
        if snapshot:
            estimated_total += (float(entry.get("balance", 0)) + float(entry.get("locked", 0))) * float(snapshot["price"])
    return {
        **_live_status(selected_config.exchange),
        "balance_fetch_status": status,
        "error_message": error,
        "balances": balances,
        "estimated_total_equity_krw": estimated_total,
        "prices": prices,
    }


@app.get("/api/live-trading/order-chance")
async def live_trading_order_chance(market: str = Query(DEFAULT_MARKET), exchange: str | None = Query(None, pattern=r"^(upbit|bithumb)$")) -> dict:
    chance, status, error = await _safe_order_chance(market, exchange)
    config = LiveTradingConfig.for_exchange(exchange) if exchange else LiveTradingConfig.from_env()
    return {
        **_live_status(config.exchange),
        "exchange": config.exchange,
        "market": market,
        "order_chance_status": status,
        "order_chance_error": error,
        "order_chance": chance,
    }


@app.post("/api/live-trading/arm")
def arm_live_trading(payload: LiveArmRequest) -> dict:
    ok, mode, message = arm_live_manual_mode(payload.confirmation, payload.acknowledged)
    insert_live_mode_event("ARM" if ok else "ARM_BLOCKED", mode, message)
    return {**_live_status(), "ok": ok, "message": message}


@app.post("/api/live-trading/lock")
def lock_live_trading_endpoint() -> dict:
    mode = lock_live_trading()
    insert_live_mode_event("LOCK", mode, "사용자가 실거래 모드를 잠금 처리했습니다.")
    return {**_live_status(), "message": "실거래 모드가 잠금 상태입니다."}


@app.post("/api/live-trading/emergency-stop")
def emergency_stop_live_trading() -> dict:
    mode = trigger_emergency_stop()
    insert_live_mode_event("EMERGENCY_STOP", mode, "Emergency Stop이 활성화되어 모든 실거래 주문 후보를 차단합니다.")
    compute_risk_state("bithumb", DEFAULT_MARKET)
    insert_risk_log(
        {
            "exchange": "bithumb",
            "market": DEFAULT_MARKET,
            "risk_level": "BLOCKED",
            "allowed": False,
            "block_code": "BLOCKED_EMERGENCY_STOP",
            "block_reason": "Emergency Stop enabled.",
            "checks": {"mode_check": {"allowed": False, "code": "BLOCKED_EMERGENCY_STOP"}},
        }
    )
    return {**_live_status(), "message": "Emergency Stop 활성화 중에는 자동 청산을 실행하지 않습니다."}


@app.post("/api/live-trading/reset-emergency")
def reset_live_emergency(payload: LiveEmergencyResetRequest) -> dict:
    ok, mode, message = reset_emergency_stop(payload.confirmation)
    insert_live_mode_event("RESET_EMERGENCY" if ok else "RESET_EMERGENCY_BLOCKED", mode, message)
    compute_risk_state("bithumb", DEFAULT_MARKET)
    return {**_live_status(), "ok": ok, "message": message}


@app.post("/api/live-orders/preview")
@app.post("/api/live/order-preview")
async def preview_live_order(payload: LiveOrderPreviewRequest) -> dict:
    active_config = LiveTradingConfig.from_env()
    exchange = payload.exchange or active_config.exchange
    config = LiveTradingConfig.for_exchange(exchange)
    request_id = payload.request_id or f"live-{uuid.uuid4()}"
    if get_live_order_log(request_id) is not None:
        risk = {
            "allowed": False,
            "risk_result": "BLOCKED_DUPLICATE_ORDER",
            "blocked_reason": "BLOCKED_DUPLICATE_ORDER",
            "fee_estimate": 0.0,
            "request_id": request_id,
        }
        return {"request_id": request_id, "preview": risk, "status": "BLOCKED", **_live_status(exchange)}

    order = {
        "request_id": request_id,
        "exchange": exchange,
        "market": payload.market,
        "side": payload.side,
        "order_type": payload.order_type,
        "price": payload.price,
        "amount_krw": payload.amount_krw,
        "volume": payload.volume,
    }
    balances, balance_status, balance_error = await _safe_live_balances_for_exchange(exchange)
    order_chance, order_chance_status, order_chance_error = await _safe_order_chance(payload.market, exchange)
    snapshot = await _market_snapshot(payload.market)
    preview = evaluate_live_order_risk(
        order=order,
        config=config,
        mode=current_live_mode(),
        balances=balances,
        request_exists=False,
        recent_duplicate=has_recent_live_order(payload.market, payload.side, config.duplicate_window_seconds),
        market_snapshot=snapshot,
    )
    order.update(
        {
            "price": preview["price"],
            "amount_krw": preview["amount_krw"],
            "volume": preview["volume"],
        }
    )
    if balance_status != "SUCCESS" and preview["risk_result"] == "ALLOWED":
        preview["allowed"] = False
        preview["risk_result"] = "BLOCKED_API_RESPONSE_ERROR"
        preview["blocked_reason"] = balance_error or balance_status
    if order_chance_status != "SUCCESS" and preview["risk_result"] == "ALLOWED":
        preview["allowed"] = False
        preview["risk_result"] = "BLOCKED_API_RESPONSE_ERROR"
        preview["blocked_reason"] = order_chance_error or order_chance_status
    preview = check_order_risk(
        order=order,
        purpose="ENTRY" if payload.side == "BUY" else "EXIT",
        base_result=preview,
        mode=current_live_mode(),
        market_snapshot=snapshot,
        balances=balances,
        manual_confirmed=False,
        is_auto=False,
    )
    preview["request_id"] = request_id
    preview["balance_fetch_status"] = balance_status
    preview["balance_error"] = balance_error
    preview["exchange"] = exchange
    preview["order_chance_status"] = order_chance_status
    preview["order_chance_error"] = order_chance_error
    preview["order_chance"] = order_chance
    preview["market_snapshot"] = snapshot
    insert_live_order_log(
        {
            "request_id": request_id,
            "exchange": exchange,
            "market": payload.market,
            "side": payload.side,
            "order_type": payload.order_type,
            "price": preview["price"],
            "volume": preview["volume"],
            "amount_krw": preview["amount_krw"],
            "fee_estimate": preview["fee_estimate"],
            "risk_result": preview["risk_result"],
            "order_preview_payload": preview,
            "exchange_request_payload_masked": {},
            "exchange_response_payload": {},
            "status": "PREVIEWED" if preview["allowed"] else "BLOCKED",
            "error_message": preview.get("blocked_reason") or balance_error,
            "order_purpose": "ENTRY" if payload.side == "BUY" else "EXIT",
            "manual_confirmed": False,
        }
    )
    return {"request_id": request_id, "preview": preview, "status": "PREVIEWED" if preview["allowed"] else "BLOCKED", **_live_status(exchange)}


@app.post("/api/live-orders/place")
async def place_live_order(payload: LiveOrderPlaceRequest) -> dict:
    if payload.final_confirmation != "PLACE LIVE ORDER":
        raise HTTPException(status_code=400, detail="최종 확인 문구 PLACE LIVE ORDER가 필요합니다.")
    preview_log = get_live_order_log(payload.request_id)
    if preview_log is None:
        raise HTTPException(status_code=404, detail="먼저 주문 미리보기를 실행해야 합니다.")
    if preview_log["status"] != "PREVIEWED" or preview_log["risk_result"] != "ALLOWED":
        return {"request_id": payload.request_id, "status": "BLOCKED", "risk_result": preview_log["risk_result"], "message": "Risk Manager가 주문을 차단했습니다."}
    if current_live_mode() != "LIVE_MANUAL_ONLY":
        live_config = LiveTradingConfig.from_env()
        blocked_result = "BLOCKED_LIVE_DISABLED" if not live_config.live_trading_enabled else "BLOCKED_LIVE_LOCKED"
        update_live_order_log(payload.request_id, {"status": "BLOCKED", "risk_result": blocked_result, "error_message": "LIVE_MANUAL_ONLY mode is required."})
        return {"request_id": payload.request_id, "status": "BLOCKED", "risk_result": blocked_result, **_live_status(preview_log.get("exchange"))}
    order_payload = {
        "request_id": payload.request_id,
        "exchange": preview_log.get("exchange", LiveTradingConfig.from_env().exchange),
        "market": preview_log["market"],
        "side": preview_log["side"],
        "order_type": preview_log["order_type"],
        "price": preview_log["price"],
        "amount_krw": preview_log["amount_krw"],
        "volume": preview_log["volume"],
    }
    config = LiveTradingConfig.for_exchange(str(order_payload["exchange"]))
    if has_unresolved_live_order(str(order_payload["exchange"]), preview_log["market"]):
        update_live_order_log(
            payload.request_id,
            {
                "status": "BLOCKED",
                "risk_result": "BLOCKED_UNRESOLVED_LIVE_ORDER",
                "error_message": "Existing live order must be reconciled before placing another order.",
            },
        )
        return {"request_id": payload.request_id, "status": "BLOCKED", "risk_result": "BLOCKED_UNRESOLVED_LIVE_ORDER", **_live_status(str(order_payload["exchange"]))}
    balances, balance_status, balance_error = await _safe_live_balances_for_exchange(str(order_payload["exchange"]))
    snapshot = await _market_snapshot(preview_log["market"])
    final_risk = evaluate_live_order_risk(
        order=order_payload,
        config=config,
        mode=current_live_mode(),
        balances=balances,
        request_exists=False,
        recent_duplicate=has_recent_live_order(preview_log["market"], preview_log["side"], config.duplicate_window_seconds),
        market_snapshot=snapshot,
    )
    if balance_status != "SUCCESS" and final_risk["risk_result"] == "ALLOWED":
        final_risk["allowed"] = False
        final_risk["risk_result"] = "BLOCKED_API_RESPONSE_ERROR"
        final_risk["blocked_reason"] = balance_error or balance_status
    final_risk = check_order_risk(
        order=order_payload,
        purpose="ENTRY" if str(order_payload["side"]).upper() == "BUY" else "EXIT",
        base_result=final_risk,
        mode=current_live_mode(),
        market_snapshot=snapshot,
        balances=balances,
        manual_confirmed=True,
        is_auto=False,
    )
    if not final_risk["allowed"]:
        update_live_order_log(
            payload.request_id,
            {
                "status": "BLOCKED",
                "risk_result": final_risk["risk_result"],
                "exchange_response_payload": {},
                "error_message": final_risk.get("blocked_reason"),
            },
        )
        return {"request_id": payload.request_id, "status": "BLOCKED", "risk_result": final_risk["risk_result"], "preview": final_risk, **_live_status(str(order_payload["exchange"]))}
    broker = get_live_broker(str(order_payload["exchange"]))
    try:
        masked_request = masked_exchange_request(order_payload)
        exchange_response = await broker.place_order(order_payload)
        order_uuid = str(exchange_response.get("uuid") or exchange_response.get("order_id") or exchange_response.get("id") or "")
        update_live_order_log(
            payload.request_id,
            {
                "status": "SUBMITTED",
                "risk_result": "ALLOWED",
                "exchange_request_payload_masked": masked_request,
                "exchange_response_payload": exchange_response,
                "order_uuid": order_uuid or preview_log.get("order_uuid"),
                "error_message": None,
            },
        )
        reconciled = None
        latest_log = get_live_order_log(payload.request_id)
        if latest_log is not None and order_uuid:
            reconciled = await reconcile_order_log(latest_log, source="MANUAL_POST_SUBMIT_STATUS_RECHECK")
        final_log = get_live_order_log(payload.request_id)
        return {
            "request_id": payload.request_id,
            "status": final_log["status"] if final_log else "SUBMITTED",
            "exchange_response": exchange_response,
            "reconciled_status": reconciled.status if reconciled else None,
            **_live_status(str(order_payload["exchange"])),
        }
    except Exception as exc:
        if is_timeout_exception(exc):
            update_live_order_log(
                payload.request_id,
                {
                    "status": "SUBMITTED",
                    "risk_result": "ORDER_STATUS_UNKNOWN_TIMEOUT",
                    "exchange_request_payload_masked": masked_exchange_request(order_payload),
                    "exchange_response_payload": {},
                    "error_message": "Exchange request timed out; order status must be reconciled before any retry.",
                },
            )
            log_recovery_event(
                "ORDER_STATUS_UNKNOWN_TIMEOUT",
                "ERROR",
                "Manual live order timed out. Re-ordering is blocked until reconciliation.",
                exchange=str(order_payload["exchange"]),
                market=preview_log["market"],
                request_id=payload.request_id,
            )
            return {
                "request_id": payload.request_id,
                "status": "SUBMITTED",
                "risk_result": "ORDER_STATUS_UNKNOWN_TIMEOUT",
                "error_message": "Exchange request timed out; status reconciliation is required.",
                **_live_status(str(order_payload["exchange"])),
            }
        update_live_order_log(
            payload.request_id,
            {
                "status": "FAILED",
                "risk_result": "BLOCKED_API_RESPONSE_ERROR",
                "exchange_request_payload_masked": masked_exchange_request(order_payload),
                "exchange_response_payload": {},
                "error_message": str(exc),
            },
        )
        return {"request_id": payload.request_id, "status": "FAILED", "risk_result": "BLOCKED_API_RESPONSE_ERROR", "error_message": str(exc), **_live_status(str(order_payload["exchange"]))}


@app.get("/api/live-orders")
def list_live_orders() -> dict:
    return {"orders": load_trade_history_logs(), "recovery_events": recent_recovery_events(), **_live_status()}


@app.get("/api/live-recovery/status")
async def live_recovery_status(exchange: str = Query("bithumb", pattern=r"^(bithumb)$")) -> dict:
    balance_status = await reconcile_balances(exchange, DEFAULT_MARKET)
    return {
        "exchange": exchange,
        "market": DEFAULT_MARKET,
        "balance_reconciliation": balance_status,
        "recent_events": recent_recovery_events(),
    }


@app.post("/api/live-recovery/sync-open-orders")
async def sync_live_open_orders(exchange: str = Query("bithumb", pattern=r"^(bithumb)$")) -> dict:
    return {"sync": await sync_open_orders(exchange, DEFAULT_MARKET), "recent_events": recent_recovery_events()}


@app.post("/api/live-recovery/import-exchange-position")
async def import_exchange_position_endpoint(payload: ImportExchangePositionRequest, exchange: str = Query("bithumb", pattern=r"^(bithumb)$")) -> dict:
    result = await import_exchange_btc_position(exchange, DEFAULT_MARKET, confirmation=payload.confirmation)
    return {**result, "recent_events": recent_recovery_events()}


@app.get("/api/risk/status")
def risk_status(exchange: str = Query("bithumb", pattern=r"^(bithumb)$")) -> dict:
    return get_risk_dashboard(exchange, DEFAULT_MARKET)


@app.get("/api/risk/policy-blocks/latest")
def latest_policy_block(exchange: str = Query("bithumb", pattern=r"^(bithumb)$")) -> dict:
    dashboard = get_risk_dashboard(exchange, DEFAULT_MARKET)
    return {
        "policy_block": dashboard.get("latest_policy_block"),
        "policy_block_logs": dashboard.get("policy_block_logs", [])[:10],
    }


@app.get("/api/risk/logs/{log_id}")
def risk_log_detail(log_id: int) -> dict:
    log = load_risk_log(log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="Risk log not found.")
    return {"log": enrich_policy_block_log(log)}


async def _bot_policy_payload(market: str = DEFAULT_MARKET, exchange: str = "bithumb") -> dict:
    policy = load_bot_operation_policy(market)
    latest_price = 0.0
    try:
        snapshot = await _market_snapshot(market)
        latest_price = float(snapshot.get("price") or 0.0) if snapshot else 0.0
    except Exception:
        latest_price = 0.0
    current_position_value = 0.0
    for position in load_open_live_positions(exchange, market):
        volume = float(position.get("entry_volume") or 0.0)
        price = float(position.get("current_price") or latest_price or position.get("entry_price") or 0.0)
        current_position_value += volume * price

    balances, balance_status, balance_error = await _safe_live_balances_for_exchange(exchange)
    available_krw = None
    if balance_status == "SUCCESS":
        krw = (balances.get("by_currency") or {}).get("KRW") or balances.get("krw") or {}
        available_krw = float(krw.get("balance") or 0.0)

    max_total = float(policy.get("max_total_exposure_krw") or 0.0)
    daily_loss_pct = float(policy.get("daily_loss_limit_pct") or 0.0)
    return {
        **policy,
        "daily_loss_limit_krw": max_total * daily_loss_pct / 100,
        "current_bot_position_value_krw": current_position_value,
        "available_krw_balance": available_krw,
        "balance_fetch_status": balance_status,
        "balance_error": balance_error,
        "exposure_usage_pct": (current_position_value / max_total * 100) if max_total > 0 else 0.0,
    }


@app.get("/api/bot/policy")
async def get_bot_policy(market: str = Query(DEFAULT_MARKET), exchange: str = Query("bithumb", pattern=r"^(bithumb)$")) -> dict:
    return {"policy": await _bot_policy_payload(market, exchange)}


@app.patch("/api/bot/policy")
async def patch_bot_policy(payload: BotPolicyPatchRequest, market: str = Query(DEFAULT_MARKET), exchange: str = Query("bithumb", pattern=r"^(bithumb)$")) -> dict:
    updates = payload.model_dump(exclude_unset=True)
    try:
        update_bot_operation_policy(market, updates)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"policy": await _bot_policy_payload(market, exchange)}


@app.get("/api/analysis/latest")
def latest_analysis(market: str | None = Query(None)) -> dict:
    snapshot = load_latest_decision_snapshot(market)
    return {"decision": snapshot}


@app.get("/api/analysis/history")
def analysis_history(
    market: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    from_time: str | None = Query(None, alias="from"),
    to_time: str | None = Query(None, alias="to"),
) -> dict:
    snapshots = load_decision_snapshots(
        market=market,
        limit=limit,
        offset=offset,
        from_time=from_time,
        to_time=to_time,
    )
    return {"decisions": snapshots, "limit": limit, "offset": offset}


@app.get("/api/analysis/decision/{decision_id}")
def analysis_decision(decision_id: int) -> dict:
    snapshot = load_decision_snapshot(decision_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Decision snapshot not found.")
    return {"decision": snapshot}


@app.get("/api/analysis/shadow-report")
def analysis_shadow_report(
    market: str = Query(DEFAULT_MARKET),
    limit: int = Query(100, ge=1, le=500),
    horizon_candles: int = Query(3, ge=1, le=24),
) -> dict:
    return {"report": build_shadow_report(market=market, limit=limit, horizon_candles=horizon_candles)}


@app.get("/api/smart-engine/status")
def smart_engine_status(market: str = Query(DEFAULT_MARKET)) -> dict:
    decision = load_latest_decision_snapshot(market)
    report = build_shadow_report(market=market, limit=100, horizon_candles=3)
    latest_intent = (decision.get("order_intents") or [None])[0] if decision else None
    limited_readiness = build_limited_readiness(market=market, decision=decision, report=report)
    latest_rehearsal = (limited_readiness or {}).get("latest_rehearsal_order")
    rehearsal_review = (latest_rehearsal or {}).get("review") or (report.get("summary", {}).get("rehearsal", {}) or {}).get("latest_review")
    return {
        "live_mode": smart_engine_live_mode(),
        "decision": decision,
        "latest_intent": latest_intent,
        "readiness": report.get("summary", {}),
        "limited_readiness": limited_readiness,
        "latest_rehearsal_order": latest_rehearsal,
        "rehearsal_review": rehearsal_review,
        "rehearsal_review_status": (rehearsal_review or {}).get("decision"),
        "rehearsal_review_active": bool(rehearsal_review and rehearsal_review.get("is_active")),
        "rehearsal_review_expires_at": (rehearsal_review or {}).get("expires_at"),
        "remaining_rehearsal_blockers": (limited_readiness or {}).get("rehearsal_blockers", []),
        "promotion_status": (latest_intent or {}).get("promotion_status"),
        "promotion_blockers": (latest_intent or {}).get("promotion_blockers", []),
    }


@app.get("/api/profit-engine/status")
def profit_engine_status(
    exchange: str = Query("bithumb", pattern=r"^(bithumb)$"),
    market: str = Query(DEFAULT_MARKET),
) -> dict:
    decision = load_latest_decision_snapshot(market)
    latest_intent = (decision.get("order_intents") or [None])[0] if decision else None
    policy_preview = (latest_intent or {}).get("policy_preview") or {}
    quality_logs = load_execution_quality_logs(exchange=exchange, market=market, limit=50)
    kill_switch_events = load_strategy_kill_switch_events(exchange=exchange, market=market, limit=10)
    return {
        "config": profit_engine_status_payload(),
        "decision": decision,
        "latest_intent": latest_intent,
        "latest_order_sizing": {
            "requested_order_krw": policy_preview.get("requested_order_krw") or policy_preview.get("amount_requested_krw"),
            "available_krw": policy_preview.get("available_krw") or policy_preview.get("available_krw_balance"),
            "actual_order_krw": policy_preview.get("actual_order_krw") or policy_preview.get("capped_order_amount_krw"),
            "fee_buffer_rate": policy_preview.get("fee_buffer_rate"),
            "sizing_mode": policy_preview.get("sizing_mode"),
            "sizing_reason": policy_preview.get("sizing_reason"),
            "block_code": policy_preview.get("block_code"),
        },
        "entry_gate": {
            "market_regime": policy_preview.get("market_regime") or (decision or {}).get("market_regime"),
            "strategy_name": policy_preview.get("strategy_name") or (decision or {}).get("selected_strategy_name"),
            "entry_allowed": policy_preview.get("entry_allowed"),
            "entry_block_reason": policy_preview.get("entry_block_reason"),
            "block_code": policy_preview.get("block_code"),
        },
        "execution_quality": {
            "summary": summarize_execution_quality(quality_logs),
            "latest_logs": quality_logs[:10],
        },
        "kill_switch": {
            "status": "PAUSED" if any(str(item.get("action")) == "PAUSED" for item in kill_switch_events) else "OK",
            "latest_events": kill_switch_events,
        },
    }


@app.post("/api/smart-engine/rehearsal-review")
def smart_engine_rehearsal_review(payload: SmartRehearsalReviewRequest) -> dict:
    request_id = payload.request_id.strip()
    if not request_id.startswith("smart-rehearsal-"):
        raise HTTPException(status_code=400, detail="Smart rehearsal request_id만 검토할 수 있습니다.")
    log = get_live_order_log(request_id)
    if log is None:
        raise HTTPException(status_code=404, detail="리허설 주문 로그를 찾을 수 없습니다.")
    exchange = str(log.get("exchange") or payload.exchange or "bithumb")
    market = str(log.get("market") or payload.market or DEFAULT_MARKET)
    if exchange != payload.exchange or market != payload.market:
        raise HTTPException(status_code=409, detail="요청한 exchange/market이 리허설 주문 로그와 일치하지 않습니다.")
    review = insert_smart_rehearsal_review(
        request_id=request_id,
        exchange=exchange,
        market=market,
        decision=payload.decision,
        note=payload.note,
        reviewed_by="admin-ui",
    )
    return {"ok": True, "review": review, **smart_engine_status(market=market)}


@app.post("/api/alerts/{alert_id}/read")
def mark_alert_read(alert_id: int) -> dict:
    alert = update_risk_log_resolution(alert_id, "READ")
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found.")
    return {"alert": alert, "dashboard": get_risk_dashboard(alert["exchange"], alert["market"])}


@app.post("/api/alerts/{alert_id}/ignore")
def ignore_alert(alert_id: int) -> dict:
    alert = update_risk_log_resolution(alert_id, "IGNORE")
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found.")
    return {"alert": alert, "dashboard": get_risk_dashboard(alert["exchange"], alert["market"])}


@app.post("/api/alerts/{alert_id}/retry")
async def retry_alert(alert_id: int) -> dict:
    alert = update_risk_log_resolution(alert_id, "RETRY")
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found.")
    compute_risk_state(alert["exchange"], alert["market"])
    recovery = await sync_open_orders(alert["exchange"], alert["market"])
    return {
        "alert": alert,
        "recovery": recovery,
        "dashboard": get_risk_dashboard(alert["exchange"], alert["market"]),
    }


@app.get("/api/settings")
def get_app_settings_endpoint() -> dict:
    return {"settings": load_app_settings(), **_live_status()}


@app.patch("/api/settings")
def update_app_settings_endpoint(payload: AppSettingsRequest) -> dict:
    return {"settings": update_app_settings(payload.settings), **_live_status()}


@app.get("/api/auto-live-pilot/status")
def get_auto_live_pilot_status() -> dict:
    return auto_live_pilot_status()


@app.post("/api/auto-live-pilot/start")
def start_auto_live_pilot_endpoint(payload: AutoLivePilotStartRequest, request: Request) -> dict:
    acquired, current_lock, status_payload = _try_acquire_runtime_lock_for_start("auto-live-pilot-api", request)
    if not acquired:
        return {"ok": False, "message": "다른 서버 인스턴스가 이미 자동매매 Runtime을 실행 중입니다.", "runtime_lock": current_lock, **(status_payload or auto_live_pilot_status())}
    result = start_auto_live_pilot(
        candidate_strategy_id=payload.candidate_strategy_id,
        order_amount_krw=payload.order_amount_krw,
        confirmation=payload.confirmation,
        order_confirmation=payload.order_confirmation,
    )
    if result.get("ok") is False:
        release_runtime_lock(lock_id=RUNTIME_LOCK_ID, instance_id=_instance_id(), status="STOPPED")
    return result


@app.post("/api/auto-live-pilot/stop")
def stop_auto_live_pilot_endpoint() -> dict:
    result = stop_auto_live_pilot()
    release_runtime_lock(lock_id=RUNTIME_LOCK_ID, instance_id=_instance_id(), status="STOPPED")
    return result


@app.post("/api/auto-live-pilot/cancel-open-order")
def cancel_auto_live_pilot_open_order_endpoint() -> dict:
    return cancel_auto_live_pilot_open_order()


@app.get("/api/live-strategy-pilot/status")
def get_live_strategy_pilot_status() -> dict:
    return live_strategy_status()


@app.post("/api/live-strategy-pilot/start")
def start_live_strategy_pilot_endpoint(payload: LiveStrategyPilotStartRequest, request: Request) -> dict:
    acquired, current_lock, status_payload = _try_acquire_runtime_lock_for_start("live-strategy-pilot-api", request)
    if not acquired:
        return {"ok": False, "message": "다른 서버 인스턴스가 이미 자동매매 Runtime을 실행 중입니다.", "runtime_lock": current_lock, **(status_payload or live_strategy_status())}
    result = start_live_strategy_pilot(
        candidate_strategy_id=payload.candidate_strategy_id,
        confirmation=payload.confirmation,
        order_confirmation=payload.order_confirmation,
    )
    if result.get("ok") is False:
        release_runtime_lock(lock_id=RUNTIME_LOCK_ID, instance_id=_instance_id(), status="STOPPED")
    return result


@app.post("/api/live-strategy-pilot/stop")
def stop_live_strategy_pilot_endpoint() -> dict:
    result = stop_live_strategy_pilot()
    release_runtime_lock(lock_id=RUNTIME_LOCK_ID, instance_id=_instance_id(), status="STOPPED")
    return result


@app.post("/api/live-strategy-pilot/cancel-open-order")
def cancel_live_strategy_open_order_endpoint() -> dict:
    return cancel_live_strategy_open_order()


@app.post("/api/live-exit-candidates/approve")
def approve_live_exit_candidate_endpoint(payload: ExitCandidateActionRequest) -> dict:
    return approve_exit_candidate(payload.candidate_id)


@app.post("/api/live-exit-candidates/reject")
def reject_live_exit_candidate_endpoint(payload: ExitCandidateActionRequest) -> dict:
    return reject_exit_candidate(payload.candidate_id)


@app.post("/api/live-exit-orders/preview")
async def preview_live_exit_order_endpoint(payload: ExitOrderPreviewRequest) -> dict:
    return await create_exit_order_preview(payload.exit_candidate_id, manual_confirmed=payload.manual_confirmed, is_auto_exit=False)


@app.post("/api/live-exit-orders/submit")
async def submit_live_exit_order_endpoint(payload: ExitOrderSubmitRequest) -> dict:
    return await submit_exit_order(payload.request_id, final_confirmation=payload.final_confirmation)


@app.post("/api/live-exit-orders/cancel")
async def cancel_live_exit_order_endpoint(payload: ExitOrderCancelRequest) -> dict:
    return await cancel_exit_order(payload.request_id)


@app.post("/api/forward-paper/start")
async def start_forward_paper(payload: ForwardPaperStartRequest) -> dict:
    candidate = load_candidate_strategy(payload.candidate_strategy_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="후보 전략을 찾을 수 없습니다.")
    if candidate["market"] != DEFAULT_MARKET:
        raise HTTPException(status_code=400, detail="Forward Paper는 KRW-BTC 후보 전략만 지원합니다.")
    try:
        fresh = await fetch_minute_candles(
            market=candidate["market"],
            unit=int(candidate["unit"]),
            count=300,
        )
        insert_candles(fresh)
        candles = load_candles(candidate["market"], int(candidate["unit"]), 300)
        latest_candle = latest_completed_candle(candles, int(candidate["unit"]))
        if latest_candle is None:
            raise HTTPException(status_code=502, detail="Forward Paper를 시작할 완성 캔들이 없습니다.")
        risk = {
            "initial_cash": payload.initial_balance_krw,
            "max_order_amount": 100_000,
            "daily_max_loss_rate": 0.03,
            "max_position_ratio": 0.5,
            "consecutive_loss_limit": 3,
            "volatility_block_rate": 0.03,
            "min_volume": 0.0,
            "fee_rate": 0.0005,
            "slippage_rate": 0.0005,
            **payload.risk,
        }
        session_id = create_forward_session_from_candidate(
            candidate,
            initial_balance_krw=payload.initial_balance_krw,
            risk=risk,
            current_price=float(latest_candle["trade_price"]),
            last_processed_candle_time_utc=latest_candle["candle_time_utc"],
        )
        session = load_latest_forward_session()
        logger.info(
            "[paper-forward] session=%s started candidate=%s market=%s unit=%s last_processed=%s",
            session_id,
            candidate["id"],
            candidate["market"],
            candidate["unit"],
            latest_candle["candle_time_utc"],
        )
        return session or {"id": session_id, "status": "RUNNING"}
    except UpbitClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/forward-paper/stop")
def stop_forward_paper(payload: ForwardPaperStopRequest | None = None) -> dict:
    session = stop_forward_session(payload.session_id if payload else None)
    if session is None:
        latest = load_latest_forward_session()
        if latest is None:
            return {"status": "STOPPED", "mode": "FORWARD_PAPER", "message": "중지할 Forward Paper 세션이 없습니다."}
        return {**latest, "status": "STOPPED"}
    logger.info("[paper-forward] session=%s stopped", session["id"])
    return session


@app.get("/api/forward-paper/latest")
def latest_forward_paper() -> dict:
    session = load_latest_forward_session()
    if session is None:
        return {"status": "EMPTY", "mode": "FORWARD_PAPER"}
    return session


@app.get("/api/forward-paper/sessions")
def list_forward_paper_sessions() -> dict:
    return {"sessions": load_forward_sessions()}


@app.post("/api/forward-paper/tick")
async def tick_forward_paper() -> dict:
    return await process_running_forward_sessions()


@app.post("/api/paper-trading/live/start")
async def start_live_paper_trading(payload: PaperTradingRequest) -> dict:
    if payload.market != DEFAULT_MARKET:
        raise HTTPException(status_code=400, detail="실시간 페이퍼 트레이딩은 KRW-BTC만 지원합니다.")
    try:
        fresh = await fetch_minute_candles(
            market=payload.market,
            unit=payload.unit,
            count=max(payload.count, 30),
        )
        insert_candles(fresh)
        candles = load_candles(payload.market, payload.unit, max(payload.count, 30))
        latest_candle = candles[-1] if candles else None
        if latest_candle is None:
            raise HTTPException(status_code=502, detail="초기화할 최신 캔들이 없습니다.")
        session_id = create_live_paper_session(
            payload.market,
            payload.unit,
            payload.strategy,
            payload.settings,
            payload.risk,
            float(latest_candle["trade_price"]),
            latest_candle["candle_time_utc"],
        )
        session = load_latest_live_paper_session()
        logger.info(
            "[paper-live] session=%s started market=%s unit=%s last_processed=%s",
            session_id,
            payload.market,
            payload.unit,
            latest_candle["candle_time_utc"],
        )
        return session or {"id": session_id, "status": "RUNNING"}
    except UpbitClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/paper-trading/live/stop")
def stop_live_paper_trading() -> dict:
    session = stop_latest_live_paper_session()
    if session is None:
        latest = load_latest_live_paper_session()
        if latest is None:
            return {"status": "STOPPED", "message": "중지할 실시간 페이퍼 세션이 없습니다."}
        return {**latest, "status": "STOPPED"}
    logger.info("[paper-live] session=%s stopped", session["id"])
    return session


@app.get("/api/paper-trading/live/latest")
def latest_live_paper_trading() -> dict:
    session = load_latest_live_paper_session()
    if session is None:
        return {"status": "EMPTY", "mode": "LIVE"}
    return session


@app.post("/api/paper-trading/live/tick")
async def tick_live_paper_trading() -> dict:
    return await process_running_live_paper_sessions()
