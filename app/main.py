from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from math import ceil
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

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
    create_forward_session_from_candidate,
    create_live_paper_session,
    get_last_live_order_time,
    get_live_order_log,
    has_recent_live_order,
    init_db,
    insert_live_mode_event,
    insert_live_order_log,
    insert_candles,
    load_candidate_strategy,
    load_latest_live_paper_session,
    load_candles,
    load_candles_between,
    load_candidate_strategies,
    load_forward_sessions,
    load_latest_forward_session,
    load_live_order_logs,
    load_latest_paper_session,
    pause_running_forward_sessions_on_startup,
    save_backtest,
    save_candidate_strategy,
    save_paper_session,
    save_validation_run,
    stop_forward_session,
    stop_latest_live_paper_session,
    stop_latest_paper_session,
    update_live_order_log,
)
from app.env import load_server_env
from app.forward_paper import latest_completed_candle, process_running_forward_sessions, run_forward_scheduler_tick
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
from app.live_paper import process_running_live_paper_sessions, run_scheduler_tick
from app.live_strategy_pilot import (
    cancel_live_strategy_open_order,
    live_strategy_status,
    run_live_strategy_tick,
    start_live_strategy_pilot,
    stop_live_strategy_pilot,
)
from app.paper_trading import run_paper_trading
from app.strategy_validation import run_strategy_validation
from app.upbit import UpbitClientError, fetch_minute_candles

load_server_env()

DEFAULT_MARKET = "KRW-BTC"
logger = logging.getLogger("uvicorn.error")


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


async def _load_period_candles(market: str, unit: int, start_time_utc: str, end_time_utc: str) -> list[dict]:
    start = _parse_utc(start_time_utc)
    end = _parse_utc(end_time_utc)
    if end <= start:
        raise ValueError("醫낅즺 ?쒓컙? ?쒖옉 ?쒓컙蹂대떎 ?ㅼ뿬???⑸땲??")
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
        raise ValueError("?좏깮??湲곌컙??諛깊뀒?ㅽ듃???꾩슂??罹붾뱾??30媛?誘몃쭔?낅땲??")
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
    strategies: list[str] = Field(default_factory=lambda: ["ma_cross", "rsi", "volatility_breakout"])
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


class CandidateStrategyRequest(BaseModel):
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
    candidate_strategy_id: int
    confirmation: str = ""
    order_confirmation: str = ""


@asynccontextmanager
async def lifespan(_: FastAPI):
    load_server_env()
    init_db()
    reset_live_runtime_state()
    insert_live_mode_event("SERVER_START", current_live_mode(), "?쒕쾭 ?쒖옉 ???ㅺ굅??紐⑤뱶???먮룞 ?좉툑 ?곹깭濡?珥덇린?붾릺?덉뒿?덈떎.")
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
    scheduler.start()
    logger.info("[paper-live] scheduler started interval_seconds=60")
    logger.info("[paper-forward] scheduler started interval_seconds=60")
    logger.info("[auto-live] pilot scheduler started interval_seconds=10")
    logger.info("[live-strategy] pilot scheduler started interval_seconds=10")
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Coin Bot Lab API", version="0.0.1", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/candles")
async def get_candles(
    market: str = Query(DEFAULT_MARKET),
    unit: int = Query(1),
    count: int = Query(200, ge=1, le=1000),
) -> dict:
    try:
        fresh = await fetch_minute_candles(market=market, unit=unit, count=count)
        inserted = insert_candles(fresh)
        candles = load_candles(market, unit, count)
        return {"market": market, "unit": unit, "inserted": inserted, "candles": candles}
    except UpbitClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/backtests")
async def create_backtest(payload: BacktestRequest) -> dict:
    if payload.market != DEFAULT_MARKET:
        raise HTTPException(status_code=400, detail="Sprint 0 湲곕낯 ???留덉폆? KRW-BTC留?吏?먰빀?덈떎.")
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
    if payload.market != DEFAULT_MARKET:
        raise HTTPException(status_code=400, detail="Sprint 3 諛깊뀒?ㅽ듃 鍮꾧탳??KRW-BTC留?吏?먰빀?덈떎.")
    allowed = {"ma_cross", "rsi", "volatility_breakout"}
    strategies = [strategy for strategy in payload.strategies if strategy in allowed]
    if not strategies:
        raise HTTPException(status_code=400, detail="鍮꾧탳???꾨왂???놁뒿?덈떎.")
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
    if payload.market != DEFAULT_MARKET:
        raise HTTPException(status_code=400, detail="Sprint 1 湲곕낯 ???留덉폆? KRW-BTC留?吏?먰빀?덈떎.")
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
            return {"status": "STOPPED", "message": "以묒????섏씠???몃젅?대뵫 ?몄뀡???놁뒿?덈떎."}
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
    if payload.market != DEFAULT_MARKET:
        raise HTTPException(status_code=400, detail="Sprint 4 ?꾨왂 寃利앹? KRW-BTC留?吏?먰빀?덈떎.")
    if payload.strategy not in {"ma_cross", "rsi", "volatility_breakout"}:
        raise HTTPException(status_code=400, detail="吏?먰븯吏 ?딅뒗 ?꾨왂?낅땲??")
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


@app.post("/api/candidate-strategies")
def create_candidate_strategy(payload: CandidateStrategyRequest) -> dict:
    if payload.market != DEFAULT_MARKET:
        raise HTTPException(status_code=400, detail="?꾨낫 ?꾨왂? KRW-BTC留?吏?먰빀?덈떎.")
    candidate = payload.model_dump()
    candidate_id = save_candidate_strategy(candidate)
    return {"id": candidate_id, **candidate}


@app.get("/api/candidate-strategies")
def list_candidate_strategies() -> dict:
    return {"candidates": load_candidate_strategies()}


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
        "api_key_policy": "API Key???쒕쾭 ?섍꼍蹂?섏뿉?쒕쭔 ?쎌쑝硫? 異쒓툑 沅뚰븳 ?녿뒗 ?ㅻ쭔 ?ъ슜?섏꽭??",
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


async def _safe_live_balances() -> tuple[dict, str, str | None]:
    config = LiveTradingConfig.from_env()
    if not config.live_trading_enabled:
        return {"by_currency": {}, "krw": {"balance": 0, "locked": 0}, "btc": {"balance": 0, "locked": 0}, "eth": {"balance": 0, "locked": 0}}, "DISABLED", "LIVE_TRADING_ENABLED=false ?낅땲??"
    if not config.api_key_loaded:
        return {"by_currency": {}, "krw": {"balance": 0, "locked": 0}, "btc": {"balance": 0, "locked": 0}, "eth": {"balance": 0, "locked": 0}}, "API_KEY_MISSING", "UPBIT_ACCESS_KEY/UPBIT_SECRET_KEY媛 ?꾩슂?⑸땲??"
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
        return empty, "API_KEY_MISSING", f"{prefix}_ACCESS_KEY/{prefix}_SECRET_KEY媛 ?꾩슂?⑸땲??"
    try:
        balances = await get_live_broker(config.exchange).get_balances()
        return balances, "SUCCESS", None
    except LiveBrokerError as exc:
        return empty, "FAILED", str(exc)


async def _safe_order_chance(market: str, exchange: str | None = None) -> tuple[dict, str, str | None]:
    config = LiveTradingConfig.for_exchange(exchange) if exchange else LiveTradingConfig.from_env()
    if not config.api_key_loaded:
        prefix = "BITHUMB" if config.exchange == "bithumb" else "UPBIT"
        return {}, "API_KEY_MISSING", f"{prefix}_ACCESS_KEY/{prefix}_SECRET_KEY媛 ?꾩슂?⑸땲??"
    try:
        chance = await get_live_broker(config.exchange).get_order_chance(market)
        return chance, "SUCCESS", None
    except LiveBrokerError as exc:
        return {}, "FAILED", str(exc)


@app.get("/api/live-trading/status")
@app.get("/api/live/status")
def live_trading_status(exchange: str | None = Query(None, pattern=r"^(upbit|bithumb)$")) -> dict:
    return _live_status(exchange)


@app.get("/api/live-trading/balances")
@app.get("/api/live/balances")
async def live_trading_balances(exchange: str | None = Query(None, pattern=r"^(upbit|bithumb)$")) -> dict:
    selected_config = LiveTradingConfig.for_exchange(exchange) if exchange else LiveTradingConfig.from_env()
    balances, status, error = await _safe_live_balances_for_exchange(selected_config.exchange)
    estimated_total = float(balances.get("krw", {}).get("balance", 0)) + float(balances.get("krw", {}).get("locked", 0))
    btc_snapshot = await _market_snapshot("KRW-BTC")
    eth_snapshot = await _market_snapshot("KRW-ETH")
    if btc_snapshot:
        estimated_total += (float(balances.get("btc", {}).get("balance", 0)) + float(balances.get("btc", {}).get("locked", 0))) * btc_snapshot["price"]
    if eth_snapshot:
        estimated_total += (float(balances.get("eth", {}).get("balance", 0)) + float(balances.get("eth", {}).get("locked", 0))) * eth_snapshot["price"]
    return {
        **_live_status(selected_config.exchange),
        "balance_fetch_status": status,
        "error_message": error,
        "balances": balances,
        "estimated_total_equity_krw": estimated_total,
        "prices": {"KRW-BTC": btc_snapshot, "KRW-ETH": eth_snapshot},
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
    insert_live_mode_event("LOCK", mode, "?ъ슜?먭? ?ㅺ굅??紐⑤뱶瑜??좉툑 泥섎━?덉뒿?덈떎.")
    return {**_live_status(), "message": "?ㅺ굅??紐⑤뱶媛 ?좉툑 ?곹깭?낅땲??"}


@app.post("/api/live-trading/emergency-stop")
def emergency_stop_live_trading() -> dict:
    mode = trigger_emergency_stop()
    insert_live_mode_event("EMERGENCY_STOP", mode, "Emergency Stop???쒖꽦?붾릺??紐⑤뱺 ?ㅺ굅??二쇰Ц ?꾨낫瑜?李⑤떒?⑸땲??")
    return {**_live_status(), "message": "Emergency Stop ?쒖꽦?? ?먮룞 泥?궛? ?ㅽ뻾?섏? ?딆뒿?덈떎."}


@app.post("/api/live-trading/reset-emergency")
def reset_live_emergency(payload: LiveEmergencyResetRequest) -> dict:
    ok, mode, message = reset_emergency_stop(payload.confirmation)
    insert_live_mode_event("RESET_EMERGENCY" if ok else "RESET_EMERGENCY_BLOCKED", mode, message)
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
        }
    )
    return {"request_id": request_id, "preview": preview, "status": "PREVIEWED" if preview["allowed"] else "BLOCKED", **_live_status(exchange)}


@app.post("/api/live-orders/place")
async def place_live_order(payload: LiveOrderPlaceRequest) -> dict:
    if payload.final_confirmation != "PLACE LIVE ORDER":
        raise HTTPException(status_code=400, detail="理쒖쥌 ?뺤씤 臾멸뎄 PLACE LIVE ORDER媛 ?꾩슂?⑸땲??")
    preview_log = get_live_order_log(payload.request_id)
    if preview_log is None:
        raise HTTPException(status_code=404, detail="癒쇱? 二쇰Ц 誘몃━蹂닿린瑜??ㅽ뻾?댁빞 ?⑸땲??")
    if preview_log["status"] != "PREVIEWED" or preview_log["risk_result"] != "ALLOWED":
        return {"request_id": payload.request_id, "status": "BLOCKED", "risk_result": preview_log["risk_result"], "message": "Risk Manager媛 二쇰Ц??李⑤떒?덉뒿?덈떎."}
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
        update_live_order_log(
            payload.request_id,
            {
                "status": "SUBMITTED",
                "risk_result": "ALLOWED",
                "exchange_request_payload_masked": masked_request,
                "exchange_response_payload": exchange_response,
                "error_message": None,
            },
        )
        return {"request_id": payload.request_id, "status": "SUBMITTED", "exchange_response": exchange_response, **_live_status(str(order_payload["exchange"]))}
    except Exception as exc:
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
    return {"orders": load_live_order_logs(), **_live_status()}


@app.get("/api/auto-live-pilot/status")
def get_auto_live_pilot_status() -> dict:
    return auto_live_pilot_status()


@app.post("/api/auto-live-pilot/start")
def start_auto_live_pilot_endpoint(payload: AutoLivePilotStartRequest) -> dict:
    return start_auto_live_pilot(
        candidate_strategy_id=payload.candidate_strategy_id,
        order_amount_krw=payload.order_amount_krw,
        confirmation=payload.confirmation,
        order_confirmation=payload.order_confirmation,
    )


@app.post("/api/auto-live-pilot/stop")
def stop_auto_live_pilot_endpoint() -> dict:
    return stop_auto_live_pilot()


@app.post("/api/auto-live-pilot/cancel-open-order")
def cancel_auto_live_pilot_open_order_endpoint() -> dict:
    return cancel_auto_live_pilot_open_order()


@app.get("/api/live-strategy-pilot/status")
def get_live_strategy_pilot_status() -> dict:
    return live_strategy_status()


@app.post("/api/live-strategy-pilot/start")
def start_live_strategy_pilot_endpoint(payload: LiveStrategyPilotStartRequest) -> dict:
    return start_live_strategy_pilot(
        candidate_strategy_id=payload.candidate_strategy_id,
        confirmation=payload.confirmation,
        order_confirmation=payload.order_confirmation,
    )


@app.post("/api/live-strategy-pilot/stop")
def stop_live_strategy_pilot_endpoint() -> dict:
    return stop_live_strategy_pilot()


@app.post("/api/live-strategy-pilot/cancel-open-order")
def cancel_live_strategy_open_order_endpoint() -> dict:
    return cancel_live_strategy_open_order()


@app.post("/api/forward-paper/start")
async def start_forward_paper(payload: ForwardPaperStartRequest) -> dict:
    candidate = load_candidate_strategy(payload.candidate_strategy_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="?꾨낫 ?꾨왂??李얠쓣 ???놁뒿?덈떎.")
    if candidate["market"] != DEFAULT_MARKET:
        raise HTTPException(status_code=400, detail="Forward Paper??KRW-BTC ?꾨낫 ?꾨왂留?吏?먰빀?덈떎.")
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
            raise HTTPException(status_code=502, detail="Forward Paper瑜??쒖옉???꾩꽦 罹붾뱾???놁뒿?덈떎.")
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
            return {"status": "STOPPED", "mode": "FORWARD_PAPER", "message": "以묒???Forward Paper ?몄뀡???놁뒿?덈떎."}
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
        raise HTTPException(status_code=400, detail="Sprint 2.5 ?ㅼ떆媛??섏씠?쇰뒗 KRW-BTC留?吏?먰빀?덈떎.")
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
            raise HTTPException(status_code=502, detail="珥덇린?뷀븷 理쒖떊 罹붾뱾???놁뒿?덈떎.")
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
            return {"status": "STOPPED", "message": "以묒????ㅼ떆媛??섏씠???몄뀡???놁뒿?덈떎."}
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
