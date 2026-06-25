from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

DB_PATH = Path(__file__).resolve().parent.parent / "coin_bot_lab.db"


def _sqlite_busy_timeout_ms() -> int:
    try:
        return max(1000, int(os.getenv("SQLITE_BUSY_TIMEOUT_MS", "10000")))
    except ValueError:
        return 10000


SQLITE_BUSY_TIMEOUT_MS = _sqlite_busy_timeout_ms()
REQUIRED_SCHEMA_TABLES = [
    "market_universe",
    "candidate_strategies",
    "candidate_strategy_promotions",
    "active_strategy_selection",
    "strategy_switch_logs",
    "scheduler_task_state",
    "paper_forward_sessions",
    "paper_forward_equity_points",
    "paper_forward_orders",
    "live_order_logs",
    "live_positions",
    "position_slots",
    "capital_allocation_runs",
    "capital_allocation_decisions",
    "order_reservations",
    "next_entry_queue",
    "execution_quality_logs",
    "trade_outcome_logs",
    "adaptive_edge_stats",
    "aggression_preset_logs",
    "strategy_kill_switch_events",
    "position_fill_events",
]
LIVE_ORDER_EVENT_REQUEST_ID_FILTER = """
              AND request_id NOT LIKE '%-submitted%'
              AND request_id NOT LIKE '%-waiting-%'
              AND request_id NOT LIKE '%-partial%'
              AND request_id NOT LIKE '%-canceled-%'
              AND request_id NOT LIKE '%-filled-%'
              AND request_id NOT LIKE '%-failed-%'
"""
TRADE_HISTORY_STATUSES = (
    "FILLED",
    "PARTIALLY_FILLED",
    "CANCELED",
    "CANCELLED",
    "STALE_CANCELED",
)
DEFAULT_MARKET = "KRW-BTC"
LEGACY_CANDIDATE_STATUSES = {"ACTIVE", "INACTIVE"}
CANDIDATE_STATUSES = {
    "DISCOVERED",
    "BACKTEST_RUNNING",
    "BACKTEST_PASSED",
    "BACKTEST_FAILED",
    "SHADOW_RUNNING",
    "SHADOW_PASSED",
    "LIVE_ELIGIBLE",
    "LIVE_ACTIVE",
    "PAUSED",
    "REJECTED",
    *LEGACY_CANDIDATE_STATUSES,
}
LIVE_CANDIDATE_STATUSES = {"LIVE_ELIGIBLE", "LIVE_ACTIVE"}

DEFAULT_CANDIDATE_STRATEGIES = [
    {
        "name": "필승 v1 - 추세 돌파",
        "description": "15분봉 변동성 돌파를 기준으로 거래량과 추세가 동시에 붙을 때만 진입하는 기본 전략입니다.",
        "strategy": "volatility_breakout",
        "parameters": {"k": 0.45, "exit_window": 12},
        "unit": 15,
        "market": "KRW-BTC",
        "backtest_period": "30d",
        "score": 91.2,
        "backtest_total_return": 0.0,
        "backtest_mdd": 0.0,
        "backtest_win_rate": 0.0,
        "backtest_profit_factor": 0.0,
        "backtest_trade_count": 0,
        "backtest_average_trade_pnl": 0.0,
        "warning": "백테스트 실행 필요",
        "status": "ACTIVE",
    },
    {
        "name": "필승 v2 - 눌림 반등",
        "description": "5분봉 RSI 과매도 회복 구간을 노리는 빠른 반등 전략입니다. 짧은 검증 기간에서 민첩하게 확인합니다.",
        "strategy": "rsi",
        "parameters": {"rsi_period": 14, "buy_threshold": 28, "sell_threshold": 68},
        "unit": 5,
        "market": "KRW-BTC",
        "backtest_period": "30d",
        "score": 89.4,
        "backtest_total_return": 0.0,
        "backtest_mdd": 0.0,
        "backtest_win_rate": 0.0,
        "backtest_profit_factor": 0.0,
        "backtest_trade_count": 0,
        "backtest_average_trade_pnl": 0.0,
        "warning": "백테스트 실행 필요",
        "status": "ACTIVE",
    },
    {
        "name": "필승 v3 - 안정 추세",
        "description": "15분봉 이동평균 교차로 큰 방향성을 확인하는 안정형 전략입니다. 잦은 매매보다 신호 품질을 우선합니다.",
        "strategy": "ma_cross",
        "parameters": {"short_window": 10, "long_window": 30},
        "unit": 15,
        "market": "KRW-BTC",
        "backtest_period": "30d",
        "score": 87.8,
        "backtest_total_return": 0.0,
        "backtest_mdd": 0.0,
        "backtest_win_rate": 0.0,
        "backtest_profit_factor": 0.0,
        "backtest_trade_count": 0,
        "backtest_average_trade_pnl": 0.0,
        "warning": "백테스트 실행 필요",
        "status": "ACTIVE",
    },
]


def _database_path() -> Path:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url.startswith("sqlite:///"):
        raw_path = database_url.removeprefix("sqlite:///")
        if raw_path.startswith("/") or (len(raw_path) > 1 and raw_path[1] == ":"):
            return Path(raw_path)
        return Path(__file__).resolve().parent.parent / raw_path
    return DB_PATH


def database_path() -> str:
    return str(_database_path())


def _connect_database(path: Path | None = None) -> sqlite3.Connection:
    resolved = path or _database_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(resolved, timeout=max(1.0, SQLITE_BUSY_TIMEOUT_MS / 1000))
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.OperationalError:
        # Startup/schema health will report persistent lock failures; individual
        # short-lived connections should still be usable with busy_timeout.
        pass
    return conn


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    conn = _connect_database()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _missing_required_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    existing = {str(row["name"]) for row in rows}
    return [table for table in REQUIRED_SCHEMA_TABLES if table not in existing]


def get_db_schema_status() -> dict:
    try:
        with get_connection() as conn:
            missing = _missing_required_tables(conn)
        return {
            "schema_status": "OK" if not missing else "MISSING_TABLES",
            "database_path": database_path(),
            "required_tables": list(REQUIRED_SCHEMA_TABLES),
            "missing_tables": missing,
        }
    except Exception as exc:
        return {
            "schema_status": "ERROR",
            "database_path": database_path(),
            "required_tables": list(REQUIRED_SCHEMA_TABLES),
            "missing_tables": list(REQUIRED_SCHEMA_TABLES),
            "error": str(exc),
            "error_type": exc.__class__.__name__,
        }


def ensure_required_schema(*, repair: bool = True) -> dict:
    status = get_db_schema_status()
    if repair and status.get("schema_status") == "MISSING_TABLES":
        init_db()
        repaired = get_db_schema_status()
        repaired["repair_attempted"] = True
        repaired["repair_status"] = "REPAIRED" if repaired.get("schema_status") == "OK" else "FAILED"
        repaired["initial_missing_tables"] = status.get("missing_tables", [])
        return repaired
    status["repair_attempted"] = False
    status["repair_status"] = "NOT_NEEDED" if status.get("schema_status") == "OK" else "FAILED"
    return status


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS candles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market TEXT NOT NULL,
                unit INTEGER NOT NULL,
                candle_time_utc TEXT NOT NULL,
                candle_time_kst TEXT NOT NULL,
                opening_price REAL NOT NULL,
                high_price REAL NOT NULL,
                low_price REAL NOT NULL,
                trade_price REAL NOT NULL,
                candle_acc_trade_price REAL NOT NULL,
                candle_acc_trade_volume REAL NOT NULL,
                timestamp INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(market, unit, candle_time_utc)
            );

            CREATE TABLE IF NOT EXISTS backtest_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market TEXT NOT NULL,
                unit INTEGER NOT NULL,
                strategy TEXT NOT NULL,
                settings_json TEXT NOT NULL,
                risk_json TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                backtest_id INTEGER NOT NULL,
                market TEXT NOT NULL,
                unit INTEGER NOT NULL,
                strategy TEXT NOT NULL,
                candle_time_utc TEXT NOT NULL,
                signal TEXT NOT NULL,
                price REAL NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(backtest_id) REFERENCES backtest_results(id)
            );

            CREATE TABLE IF NOT EXISTS virtual_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                backtest_id INTEGER NOT NULL,
                market TEXT NOT NULL,
                side TEXT NOT NULL,
                candle_time_utc TEXT NOT NULL,
                price REAL NOT NULL,
                quantity REAL NOT NULL,
                fee REAL NOT NULL,
                pnl REAL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(backtest_id) REFERENCES backtest_results(id)
            );

            CREATE TABLE IF NOT EXISTS paper_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL,
                market TEXT NOT NULL,
                unit INTEGER NOT NULL,
                strategy TEXT NOT NULL,
                settings_json TEXT NOT NULL,
                risk_json TEXT NOT NULL,
                initial_cash REAL NOT NULL,
                cash_balance REAL NOT NULL,
                btc_balance REAL NOT NULL,
                avg_buy_price REAL NOT NULL,
                current_price REAL NOT NULL,
                equity REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                unrealized_pnl REAL NOT NULL,
                started_at TEXT NOT NULL,
                stopped_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS paper_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                order_time TEXT NOT NULL,
                market TEXT NOT NULL,
                side TEXT NOT NULL,
                strategy TEXT NOT NULL,
                signal_price REAL NOT NULL,
                execution_price REAL NOT NULL,
                quantity REAL NOT NULL,
                fee REAL NOT NULL,
                realized_pnl REAL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(session_id) REFERENCES paper_sessions(id)
            );

            CREATE TABLE IF NOT EXISTS paper_equity_points (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                candle_time_utc TEXT NOT NULL,
                equity REAL NOT NULL,
                cash_balance REAL NOT NULL,
                btc_balance REAL NOT NULL,
                price REAL NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(session_id) REFERENCES paper_sessions(id)
            );

            CREATE TABLE IF NOT EXISTS validation_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market TEXT NOT NULL,
                strategy TEXT NOT NULL,
                request_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS strategy_validation_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                market TEXT NOT NULL,
                unit INTEGER NOT NULL,
                strategy TEXT NOT NULL,
                parameters_json TEXT NOT NULL,
                period_label TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                warnings_json TEXT NOT NULL,
                stability_score REAL NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(run_id) REFERENCES validation_runs(id)
            );

            CREATE TABLE IF NOT EXISTS strategy_validation_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT NOT NULL DEFAULT 'upbit',
                market_count INTEGER NOT NULL DEFAULT 0,
                strategy_count INTEGER NOT NULL DEFAULT 0,
                timeframes_json TEXT NOT NULL DEFAULT '[]',
                periods_json TEXT NOT NULL DEFAULT '[]',
                risk_json TEXT NOT NULL DEFAULT '{}',
                request_json TEXT NOT NULL DEFAULT '{}',
                summary_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'RUNNING',
                started_at TEXT NOT NULL,
                finished_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS candidate_strategies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy TEXT NOT NULL,
                parameters_json TEXT NOT NULL,
                unit INTEGER NOT NULL,
                market TEXT NOT NULL,
                backtest_period TEXT NOT NULL,
                score REAL NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS market_universe (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT NOT NULL DEFAULT 'upbit',
                market TEXT NOT NULL,
                symbol TEXT NOT NULL,
                quote_currency TEXT NOT NULL DEFAULT 'KRW',
                status TEXT NOT NULL DEFAULT 'DISCOVERED',
                is_enabled INTEGER NOT NULL DEFAULT 1,
                is_live_allowed INTEGER NOT NULL DEFAULT 0,
                is_auto_selectable INTEGER NOT NULL DEFAULT 1,
                scan_rank INTEGER NOT NULL DEFAULT 0,
                score REAL NOT NULL DEFAULT 0,
                reason TEXT NOT NULL DEFAULT '',
                min_24h_trade_price_krw REAL NOT NULL DEFAULT 0,
                last_24h_trade_price_krw REAL NOT NULL DEFAULT 0,
                last_price REAL NOT NULL DEFAULT 0,
                last_change_rate REAL NOT NULL DEFAULT 0,
                last_volatility_score REAL NOT NULL DEFAULT 0,
                last_liquidity_score REAL NOT NULL DEFAULT 0,
                last_risk_score REAL NOT NULL DEFAULT 0,
                last_scanned_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(exchange, market)
            );

            CREATE TABLE IF NOT EXISTS candidate_strategy_promotions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_strategy_id INTEGER NOT NULL,
                from_status TEXT NOT NULL,
                to_status TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                score REAL NOT NULL DEFAULT 0,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(candidate_strategy_id) REFERENCES candidate_strategies(id)
            );

            CREATE TABLE IF NOT EXISTS active_strategy_selection (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_strategy_id INTEGER NOT NULL,
                market TEXT NOT NULL,
                strategy TEXT NOT NULL,
                unit INTEGER NOT NULL,
                parameters_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'LIVE_ACTIVE',
                selected_reason TEXT NOT NULL DEFAULT '',
                selected_at TEXT NOT NULL,
                replaced_candidate_strategy_id INTEGER,
                cooldown_until TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(candidate_strategy_id) REFERENCES candidate_strategies(id)
            );

            CREATE TABLE IF NOT EXISTS strategy_switch_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_candidate_strategy_id INTEGER,
                to_candidate_strategy_id INTEGER,
                from_market TEXT,
                to_market TEXT,
                decision TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                blocked_reason TEXT NOT NULL DEFAULT '',
                score_delta REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS scheduler_task_state (
                task_name TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'IDLE',
                lock_owner TEXT NOT NULL DEFAULT '',
                lock_until TEXT,
                last_started_at TEXT,
                last_finished_at TEXT,
                next_run_at TEXT,
                last_error TEXT NOT NULL DEFAULT '',
                last_result_json TEXT NOT NULL DEFAULT '{}',
                run_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS paper_forward_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_strategy_id INTEGER NOT NULL,
                market TEXT NOT NULL,
                unit INTEGER NOT NULL,
                strategy TEXT NOT NULL,
                parameters_json TEXT NOT NULL,
                risk_json TEXT NOT NULL,
                status TEXT NOT NULL,
                initial_balance_krw REAL NOT NULL,
                current_balance_krw REAL NOT NULL,
                current_position_volume REAL NOT NULL,
                average_entry_price REAL NOT NULL,
                current_price REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                unrealized_pnl REAL NOT NULL,
                total_equity REAL NOT NULL,
                total_return_percent REAL NOT NULL,
                max_drawdown REAL NOT NULL,
                trade_count INTEGER NOT NULL,
                win_count INTEGER NOT NULL,
                loss_count INTEGER NOT NULL,
                win_rate REAL NOT NULL,
                profit_factor REAL NOT NULL,
                gross_profit REAL NOT NULL,
                gross_loss REAL NOT NULL,
                last_signal TEXT NOT NULL,
                last_risk_result TEXT NOT NULL,
                last_processed_candle_time_utc TEXT,
                last_tick_time_utc TEXT,
                started_at TEXT NOT NULL,
                stopped_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(candidate_strategy_id) REFERENCES candidate_strategies(id)
            );

            CREATE TABLE IF NOT EXISTS paper_forward_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                candidate_strategy_id INTEGER NOT NULL,
                market TEXT NOT NULL,
                unit INTEGER NOT NULL,
                strategy TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                volume REAL NOT NULL,
                amount_krw REAL NOT NULL,
                fee REAL NOT NULL,
                slippage REAL NOT NULL,
                realized_pnl REAL,
                reason TEXT NOT NULL,
                risk_result TEXT NOT NULL,
                candle_time_utc TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(session_id) REFERENCES paper_forward_sessions(id),
                FOREIGN KEY(candidate_strategy_id) REFERENCES candidate_strategies(id)
            );

            CREATE TABLE IF NOT EXISTS paper_forward_equity_points (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                candle_time_utc TEXT NOT NULL,
                equity REAL NOT NULL,
                cash_balance REAL NOT NULL,
                position_volume REAL NOT NULL,
                price REAL NOT NULL,
                drawdown REAL NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(session_id, candle_time_utc),
                FOREIGN KEY(session_id) REFERENCES paper_forward_sessions(id)
            );

            CREATE TABLE IF NOT EXISTS paper_forward_tick_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tick_time_utc TEXT NOT NULL,
                session_id INTEGER NOT NULL,
                market TEXT NOT NULL,
                unit INTEGER NOT NULL,
                latest_candle_time_utc TEXT,
                last_processed_candle_time_utc TEXT,
                result TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(session_id) REFERENCES paper_forward_sessions(id)
            );

            CREATE TABLE IF NOT EXISTS paper_forward_signal_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_time_utc TEXT NOT NULL,
                session_id INTEGER NOT NULL,
                strategy TEXT NOT NULL,
                signal TEXT NOT NULL,
                confidence REAL NOT NULL,
                reason TEXT NOT NULL,
                risk_result TEXT NOT NULL,
                candle_time_utc TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(session_id, candle_time_utc),
                FOREIGN KEY(session_id) REFERENCES paper_forward_sessions(id)
            );

            CREATE TABLE IF NOT EXISTS live_order_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL UNIQUE,
                session_id INTEGER,
                candidate_strategy_id INTEGER,
                exchange TEXT NOT NULL DEFAULT 'upbit',
                market TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                price REAL,
                volume REAL,
                amount_krw REAL,
                fee_estimate REAL NOT NULL,
                risk_result TEXT NOT NULL,
                order_preview_payload TEXT NOT NULL,
                exchange_request_payload_masked TEXT NOT NULL,
                exchange_response_payload TEXT NOT NULL,
                status TEXT NOT NULL,
                error_message TEXT,
                order_uuid TEXT,
                executed_volume REAL NOT NULL DEFAULT 0,
                remaining_volume REAL NOT NULL DEFAULT 0,
                filled_amount_krw REAL NOT NULL DEFAULT 0,
                paid_fee REAL NOT NULL DEFAULT 0,
                position_id INTEGER,
                exit_candidate_id INTEGER,
                order_purpose TEXT NOT NULL DEFAULT 'ENTRY',
                exit_reason TEXT,
                expected_pnl REAL NOT NULL DEFAULT 0,
                actual_pnl REAL,
                is_auto_exit INTEGER NOT NULL DEFAULT 0,
                manual_confirmed INTEGER NOT NULL DEFAULT 0,
                strategy_name TEXT,
                signal_reason TEXT,
                candle_time_utc TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS live_strategy_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT NOT NULL,
                market TEXT NOT NULL,
                candidate_strategy_id INTEGER NOT NULL,
                strategy_name TEXT NOT NULL,
                strategy_parameters TEXT NOT NULL,
                status TEXT NOT NULL,
                auto_enabled INTEGER NOT NULL,
                initial_balance_krw REAL NOT NULL,
                max_order_krw REAL NOT NULL,
                max_orders_per_day INTEGER NOT NULL,
                orders_created_today INTEGER NOT NULL DEFAULT 0,
                current_open_order_uuid TEXT,
                current_position_id INTEGER,
                last_signal TEXT NOT NULL DEFAULT 'NONE',
                last_signal_time_utc TEXT,
                last_risk_result TEXT,
                last_order_status TEXT,
                last_order_time_utc TEXT,
                last_processed_candle_time_utc TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                stopped_at TEXT,
                FOREIGN KEY(candidate_strategy_id) REFERENCES candidate_strategies(id)
            );

            CREATE TABLE IF NOT EXISTS live_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                exchange TEXT NOT NULL,
                market TEXT NOT NULL,
                candidate_strategy_id INTEGER NOT NULL,
                strategy_name TEXT NOT NULL,
                status TEXT NOT NULL,
                entry_order_uuid TEXT,
                exit_order_uuid TEXT,
                entry_price REAL NOT NULL,
                entry_volume REAL NOT NULL,
                entry_amount_krw REAL NOT NULL,
                current_price REAL NOT NULL,
                unrealized_pnl REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                stop_loss_price REAL NOT NULL,
                take_profit_price REAL NOT NULL,
                highest_price_since_entry REAL,
                trailing_stop_price REAL,
                trailing_stop_pct REAL,
                last_trailing_update_at TEXT,
                opened_at TEXT,
                closed_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(session_id) REFERENCES live_strategy_sessions(id),
                FOREIGN KEY(candidate_strategy_id) REFERENCES candidate_strategies(id)
            );

            CREATE TABLE IF NOT EXISTS position_slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slot_number INTEGER NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'EMPTY',
                exchange TEXT NOT NULL DEFAULT 'bithumb',
                market TEXT,
                candidate_strategy_id INTEGER,
                live_position_id INTEGER,
                live_strategy_session_id INTEGER,
                entry_order_uuid TEXT,
                exit_order_uuid TEXT,
                allocated_krw REAL NOT NULL DEFAULT 0,
                reserved_krw REAL NOT NULL DEFAULT 0,
                current_value_krw REAL NOT NULL DEFAULT 0,
                unrealized_pnl REAL NOT NULL DEFAULT 0,
                realized_pnl REAL NOT NULL DEFAULT 0,
                entry_reason TEXT,
                exit_reason TEXT,
                opened_at TEXT,
                closed_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(candidate_strategy_id) REFERENCES candidate_strategies(id),
                FOREIGN KEY(live_position_id) REFERENCES live_positions(id),
                FOREIGN KEY(live_strategy_session_id) REFERENCES live_strategy_sessions(id)
            );

            CREATE TABLE IF NOT EXISTS capital_allocation_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reason TEXT NOT NULL,
                status TEXT NOT NULL,
                max_total_exposure_krw REAL NOT NULL DEFAULT 0,
                current_exposure_krw REAL NOT NULL DEFAULT 0,
                pending_reserved_krw REAL NOT NULL DEFAULT 0,
                available_krw_balance REAL,
                remaining_exposure_krw REAL NOT NULL DEFAULT 0,
                empty_slot_count INTEGER NOT NULL DEFAULT 0,
                candidate_count INTEGER NOT NULL DEFAULT 0,
                accepted_count INTEGER NOT NULL DEFAULT 0,
                blocked_count INTEGER NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                error TEXT
            );

            CREATE TABLE IF NOT EXISTS capital_allocation_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                candidate_strategy_id INTEGER,
                market TEXT NOT NULL,
                strategy TEXT NOT NULL,
                allocation_score REAL NOT NULL DEFAULT 0,
                desired_order_krw REAL NOT NULL DEFAULT 0,
                approved_order_krw REAL NOT NULL DEFAULT 0,
                blocked_reason TEXT,
                fee_rate REAL NOT NULL DEFAULT 0,
                estimated_fee_krw REAL NOT NULL DEFAULT 0,
                estimated_slippage_krw REAL NOT NULL DEFAULT 0,
                expected_edge_pct REAL NOT NULL DEFAULT 0,
                required_edge_pct REAL NOT NULL DEFAULT 0,
                decision TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(run_id) REFERENCES capital_allocation_runs(id),
                FOREIGN KEY(candidate_strategy_id) REFERENCES candidate_strategies(id)
            );

            CREATE TABLE IF NOT EXISTS order_reservations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL UNIQUE,
                exchange TEXT NOT NULL,
                market TEXT NOT NULL,
                candidate_strategy_id INTEGER NOT NULL,
                slot_id INTEGER,
                amount_krw REAL NOT NULL,
                status TEXT NOT NULL,
                expires_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(candidate_strategy_id) REFERENCES candidate_strategies(id),
                FOREIGN KEY(slot_id) REFERENCES position_slots(id)
            );

            CREATE TABLE IF NOT EXISTS next_entry_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_strategy_id INTEGER NOT NULL,
                market TEXT NOT NULL,
                strategy TEXT NOT NULL,
                unit INTEGER NOT NULL DEFAULT 0,
                score REAL NOT NULL DEFAULT 0,
                allocation_score REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                blocked_reason TEXT,
                queued_at TEXT NOT NULL,
                expires_at TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(candidate_strategy_id, status),
                FOREIGN KEY(candidate_strategy_id) REFERENCES candidate_strategies(id)
            );

            CREATE TABLE IF NOT EXISTS exit_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id INTEGER NOT NULL,
                session_id INTEGER NOT NULL,
                exchange TEXT NOT NULL,
                market TEXT NOT NULL,
                candidate_strategy_id INTEGER NOT NULL,
                strategy_name TEXT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL,
                entry_price REAL NOT NULL,
                current_price REAL NOT NULL,
                target_exit_price REAL NOT NULL,
                volume REAL NOT NULL,
                expected_amount_krw REAL NOT NULL,
                expected_fee REAL NOT NULL,
                expected_pnl REAL NOT NULL,
                risk_result TEXT NOT NULL,
                signal_time_utc TEXT,
                candle_time_utc TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(position_id) REFERENCES live_positions(id),
                FOREIGN KEY(session_id) REFERENCES live_strategy_sessions(id),
                FOREIGN KEY(candidate_strategy_id) REFERENCES candidate_strategies(id)
            );

            CREATE TABLE IF NOT EXISTS live_signal_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                exchange TEXT NOT NULL,
                market TEXT NOT NULL,
                candidate_strategy_id INTEGER NOT NULL,
                strategy_name TEXT NOT NULL,
                signal TEXT NOT NULL,
                confidence REAL NOT NULL,
                reason TEXT NOT NULL,
                candle_time_utc TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(session_id, candle_time_utc, signal),
                FOREIGN KEY(session_id) REFERENCES live_strategy_sessions(id),
                FOREIGN KEY(candidate_strategy_id) REFERENCES candidate_strategies(id)
            );

            CREATE TABLE IF NOT EXISTS auto_live_pilot_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT NOT NULL,
                market TEXT NOT NULL,
                candidate_strategy_id INTEGER,
                strategy_name TEXT NOT NULL,
                status TEXT NOT NULL,
                auto_enabled INTEGER NOT NULL,
                order_amount_krw REAL NOT NULL,
                max_orders_per_day INTEGER NOT NULL,
                orders_created_today INTEGER NOT NULL DEFAULT 0,
                last_signal TEXT NOT NULL DEFAULT 'NONE',
                last_signal_time_utc TEXT,
                last_order_time_utc TEXT,
                last_order_uuid TEXT,
                last_order_status TEXT,
                last_processed_candle_time_utc TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                stopped_at TEXT,
                FOREIGN KEY(candidate_strategy_id) REFERENCES candidate_strategies(id)
            );

            CREATE TABLE IF NOT EXISTS live_mode_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                mode TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS live_recovery_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                exchange TEXT NOT NULL DEFAULT 'bithumb',
                market TEXT NOT NULL DEFAULT 'KRW-BTC',
                session_id INTEGER,
                request_id TEXT,
                order_uuid TEXT,
                message TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS position_fill_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_uuid TEXT NOT NULL,
                position_id INTEGER NOT NULL,
                fill_type TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                order_log_id INTEGER,
                request_id TEXT,
                applied_volume REAL NOT NULL DEFAULT 0,
                applied_amount_krw REAL NOT NULL DEFAULT 0,
                applied_fee REAL NOT NULL DEFAULT 0,
                applied_at TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(order_uuid, fill_type),
                FOREIGN KEY(position_id) REFERENCES live_positions(id),
                FOREIGN KEY(order_log_id) REFERENCES live_order_logs(id)
            );

            CREATE TABLE IF NOT EXISTS runtime_locks (
                lock_id TEXT PRIMARY KEY,
                instance_id TEXT NOT NULL,
                hostname TEXT NOT NULL,
                app_env TEXT NOT NULL,
                runtime_owner TEXT NOT NULL,
                status TEXT NOT NULL,
                acquired_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS risk_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT NOT NULL,
                market TEXT NOT NULL,
                date_kst TEXT NOT NULL,
                status TEXT NOT NULL,
                daily_realized_pnl REAL NOT NULL DEFAULT 0,
                daily_unrealized_pnl REAL NOT NULL DEFAULT 0,
                daily_total_pnl REAL NOT NULL DEFAULT 0,
                daily_loss_percent REAL NOT NULL DEFAULT 0,
                daily_order_count INTEGER NOT NULL DEFAULT 0,
                daily_entry_count INTEGER NOT NULL DEFAULT 0,
                daily_exit_count INTEGER NOT NULL DEFAULT 0,
                consecutive_loss_count INTEGER NOT NULL DEFAULT 0,
                open_order_count INTEGER NOT NULL DEFAULT 0,
                open_position_count INTEGER NOT NULL DEFAULT 0,
                last_order_time_utc TEXT,
                last_loss_time_utc TEXT,
                emergency_stop_enabled INTEGER NOT NULL DEFAULT 0,
                balance_mismatch_detected INTEGER NOT NULL DEFAULT 0,
                partial_fill_detected INTEGER NOT NULL DEFAULT 0,
                volatility_block_enabled INTEGER NOT NULL DEFAULT 0,
                low_volume_block_enabled INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(exchange, market, date_kst)
            );

            CREATE TABLE IF NOT EXISTS risk_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT NOT NULL,
                market TEXT NOT NULL,
                session_id INTEGER,
                position_id INTEGER,
                order_candidate_id TEXT,
                order_log_id INTEGER,
                risk_level TEXT NOT NULL,
                allowed INTEGER NOT NULL,
                block_code TEXT,
                block_reason TEXT,
                checks_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS aggression_preset_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                preset_name TEXT NOT NULL,
                previous_preset TEXT,
                previous_settings_json TEXT NOT NULL DEFAULT '{}',
                applied_settings_json TEXT NOT NULL DEFAULT '{}',
                before_summary_json TEXT NOT NULL DEFAULT '{}',
                after_summary_json TEXT NOT NULL DEFAULT '{}',
                safety_guards_json TEXT NOT NULL DEFAULT '{}',
                requested_by TEXT NOT NULL DEFAULT 'admin',
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS bot_operation_policy (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market TEXT NOT NULL UNIQUE,
                auto_trading_enabled INTEGER NOT NULL DEFAULT 0,
                max_total_exposure_krw REAL NOT NULL DEFAULT 500000,
                daily_loss_limit_pct REAL NOT NULL DEFAULT 3,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS decision_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                decided_at TEXT NOT NULL,
                exchange TEXT NOT NULL,
                market TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                candle_time_utc TEXT,
                candle_time_kst TEXT,
                selected_strategy_id INTEGER,
                selected_strategy_name TEXT,
                selected_strategy_type TEXT,
                legacy_signal TEXT NOT NULL,
                market_regime TEXT NOT NULL,
                current_bot_position_qty REAL NOT NULL DEFAULT 0,
                current_bot_position_value_krw REAL NOT NULL DEFAULT 0,
                current_exposure_pct REAL NOT NULL DEFAULT 0,
                target_exposure_pct REAL NOT NULL DEFAULT 0,
                action_hint TEXT NOT NULL,
                confidence_score REAL NOT NULL DEFAULT 0,
                risk_score REAL NOT NULL DEFAULT 0,
                one_line_summary TEXT NOT NULL,
                positive_reasons_json TEXT NOT NULL,
                negative_reasons_json TEXT NOT NULL,
                blockers_json TEXT NOT NULL,
                raw_features_json TEXT NOT NULL,
                external_factors_json TEXT NOT NULL,
                internal_signals_json TEXT NOT NULL DEFAULT '{}',
                max_total_exposure_krw REAL NOT NULL DEFAULT 0,
                daily_loss_limit_pct REAL NOT NULL DEFAULT 0,
                daily_loss_limit_krw REAL NOT NULL DEFAULT 0,
                available_krw_balance REAL,
                exposure_limit_blocked INTEGER NOT NULL DEFAULT 0,
                attack_score REAL NOT NULL DEFAULT 0,
                attack_mode TEXT NOT NULL DEFAULT 'OFF',
                attack_score_breakdown_json TEXT NOT NULL DEFAULT '{}',
                aggressive_target_exposure_pct REAL NOT NULL DEFAULT 0,
                conservative_target_exposure_pct REAL NOT NULL DEFAULT 0,
                final_target_exposure_source TEXT NOT NULL DEFAULT 'CONSERVATIVE',
                current_position_pnl_pct REAL NOT NULL DEFAULT 0,
                highest_price_since_entry REAL,
                trailing_stop_price REAL,
                partial_take_profit_triggered INTEGER NOT NULL DEFAULT 0,
                pyramiding_allowed INTEGER NOT NULL DEFAULT 0,
                aggressive_blockers_json TEXT NOT NULL DEFAULT '[]',
                aggressive_buy_blockers_json TEXT NOT NULL DEFAULT '[]',
                aggressive_warnings_json TEXT NOT NULL DEFAULT '[]',
                core_exposure_pct REAL NOT NULL DEFAULT 0,
                core_exposure_applied INTEGER NOT NULL DEFAULT 0,
                core_exposure_broken_by_panic INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS order_intents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_snapshot_id INTEGER NOT NULL,
                exchange TEXT NOT NULL,
                market TEXT NOT NULL,
                side TEXT NOT NULL,
                action_hint TEXT NOT NULL,
                current_value_krw REAL NOT NULL DEFAULT 0,
                target_value_krw REAL NOT NULL DEFAULT 0,
                delta_value_krw REAL NOT NULL DEFAULT 0,
                target_qty REAL,
                order_type TEXT NOT NULL,
                limit_price REAL,
                urgency TEXT NOT NULL,
                status TEXT NOT NULL,
                blockers_json TEXT NOT NULL,
                risk_preview_json TEXT NOT NULL DEFAULT '{}',
                policy_preview_json TEXT NOT NULL DEFAULT '{}',
                pilot_order_cap_krw REAL NOT NULL DEFAULT 0,
                promotion_blockers_json TEXT NOT NULL DEFAULT '[]',
                promotion_status TEXT NOT NULL DEFAULT 'SHADOW_ONLY',
                attack_score REAL NOT NULL DEFAULT 0,
                attack_mode TEXT NOT NULL DEFAULT 'OFF',
                target_source TEXT NOT NULL DEFAULT 'CONSERVATIVE',
                pyramiding_allowed INTEGER NOT NULL DEFAULT 0,
                no_averaging_down_blocked INTEGER NOT NULL DEFAULT 0,
                partial_take_profit_pct REAL NOT NULL DEFAULT 0,
                trailing_stop_price REAL,
                position_pnl_pct REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                submitted_at TEXT,
                completed_at TEXT,
                FOREIGN KEY(decision_snapshot_id) REFERENCES decision_snapshots(id)
            );

            CREATE TABLE IF NOT EXISTS smart_rehearsal_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                exchange TEXT NOT NULL,
                market TEXT NOT NULL,
                decision TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                reviewed_by TEXT NOT NULL DEFAULT 'admin',
                reviewed_at TEXT NOT NULL,
                expires_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS execution_quality_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL UNIQUE,
                order_log_id INTEGER,
                signal_time_utc TEXT,
                candle_time_utc TEXT,
                exchange TEXT NOT NULL DEFAULT 'bithumb',
                market TEXT NOT NULL,
                strategy_name TEXT NOT NULL DEFAULT '',
                market_regime TEXT NOT NULL DEFAULT '',
                requested_order_krw REAL,
                available_krw REAL,
                actual_order_krw REAL,
                order_price REAL,
                current_price_at_signal REAL,
                best_bid REAL,
                best_ask REAL,
                spread_pct REAL,
                estimated_slippage_pct REAL,
                submitted_at TEXT,
                filled_at TEXT,
                fill_time_seconds REAL,
                filled_price REAL,
                filled_volume REAL NOT NULL DEFAULT 0,
                unfilled_volume REAL NOT NULL DEFAULT 0,
                cancel_after_seconds INTEGER,
                cancel_reason TEXT NOT NULL DEFAULT '',
                post_fill_return_1m REAL,
                post_fill_return_3m REAL,
                post_fill_return_5m REAL,
                adverse_selection_pct REAL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS trade_outcome_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_uuid TEXT NOT NULL UNIQUE,
                request_id TEXT,
                live_order_log_id INTEGER,
                session_id INTEGER,
                position_id INTEGER,
                exchange TEXT NOT NULL DEFAULT 'bithumb',
                market TEXT NOT NULL,
                side TEXT NOT NULL,
                order_purpose TEXT NOT NULL DEFAULT 'ENTRY',
                strategy_name TEXT NOT NULL DEFAULT '',
                candidate_strategy_id INTEGER,
                market_regime TEXT NOT NULL DEFAULT '',
                action_hint TEXT NOT NULL DEFAULT '',
                legacy_signal TEXT NOT NULL DEFAULT '',
                attack_mode TEXT NOT NULL DEFAULT '',
                target_source TEXT NOT NULL DEFAULT '',
                entry_or_exit_price REAL,
                filled_price REAL,
                filled_volume REAL NOT NULL DEFAULT 0,
                filled_amount_krw REAL NOT NULL DEFAULT 0,
                fee_krw REAL NOT NULL DEFAULT 0,
                slippage_pct REAL,
                spread_pct REAL,
                fill_time_seconds REAL,
                filled_at TEXT,
                post_fill_return_1m REAL,
                post_fill_return_3m REAL,
                post_fill_return_5m REAL,
                post_fill_return_15m REAL,
                max_favorable_excursion_pct REAL,
                max_adverse_excursion_pct REAL,
                adverse_selection_pct REAL,
                realized_pnl_krw REAL,
                realized_return_pct REAL,
                holding_minutes REAL,
                outcome_status TEXT NOT NULL DEFAULT 'PENDING_OUTCOME',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(live_order_log_id) REFERENCES live_order_logs(id),
                FOREIGN KEY(position_id) REFERENCES live_positions(id)
            );

            CREATE TABLE IF NOT EXISTS adaptive_edge_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange TEXT NOT NULL DEFAULT 'bithumb',
                market TEXT NOT NULL,
                strategy_name TEXT NOT NULL DEFAULT '',
                candidate_strategy_id INTEGER NOT NULL DEFAULT 0,
                unit INTEGER NOT NULL DEFAULT 0,
                market_regime TEXT NOT NULL DEFAULT '',
                action_hint TEXT NOT NULL DEFAULT '',
                legacy_signal TEXT NOT NULL DEFAULT '',
                attack_mode TEXT NOT NULL DEFAULT '',
                target_source TEXT NOT NULL DEFAULT '',
                order_purpose TEXT NOT NULL DEFAULT '',
                sample_count INTEGER NOT NULL DEFAULT 0,
                win_count INTEGER NOT NULL DEFAULT 0,
                loss_count INTEGER NOT NULL DEFAULT 0,
                win_rate REAL NOT NULL DEFAULT 0,
                avg_post_fill_return_1m REAL NOT NULL DEFAULT 0,
                avg_post_fill_return_5m REAL NOT NULL DEFAULT 0,
                avg_post_fill_return_15m REAL NOT NULL DEFAULT 0,
                avg_realized_return_pct REAL NOT NULL DEFAULT 0,
                avg_realized_pnl_krw REAL NOT NULL DEFAULT 0,
                profit_factor REAL NOT NULL DEFAULT 0,
                avg_adverse_selection_pct REAL NOT NULL DEFAULT 0,
                avg_slippage_pct REAL NOT NULL DEFAULT 0,
                avg_fill_time_seconds REAL NOT NULL DEFAULT 0,
                max_drawdown_pct REAL NOT NULL DEFAULT 0,
                confidence_score REAL NOT NULL DEFAULT 0,
                edge_score REAL NOT NULL DEFAULT 0,
                last_updated_at TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(
                    exchange, market, strategy_name, candidate_strategy_id, unit,
                    market_regime, action_hint, legacy_signal, attack_mode,
                    target_source, order_purpose
                )
            );

            CREATE TABLE IF NOT EXISTS strategy_kill_switch_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_strategy_id INTEGER,
                exchange TEXT NOT NULL DEFAULT 'bithumb',
                market TEXT NOT NULL,
                strategy_name TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                blockers_json TEXT NOT NULL DEFAULT '[]',
                metrics_json TEXT NOT NULL DEFAULT '{}',
                cooldown_until TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        _ensure_column(conn, "paper_sessions", "mode", "TEXT NOT NULL DEFAULT 'SIMULATION'")
        _ensure_column(conn, "paper_sessions", "last_processed_candle_time_utc", "TEXT")
        _ensure_column(conn, "paper_sessions", "last_signal", "TEXT NOT NULL DEFAULT 'HOLD'")
        _ensure_column(conn, "paper_sessions", "updated_at", "TEXT")
        _ensure_column(conn, "paper_orders", "amount_krw", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "candidate_strategies", "backtest_total_return", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "candidate_strategies", "backtest_mdd", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "candidate_strategies", "backtest_win_rate", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "candidate_strategies", "backtest_profit_factor", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "candidate_strategies", "backtest_trade_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "candidate_strategies", "backtest_average_trade_pnl", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "candidate_strategies", "warning", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "candidate_strategies", "name", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "candidate_strategies", "description", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "candidate_strategies", "status", "TEXT NOT NULL DEFAULT 'ACTIVE'")
        _ensure_column(conn, "candidate_strategies", "updated_at", "TEXT")
        _ensure_column(conn, "strategy_validation_results", "decision", "TEXT NOT NULL DEFAULT 'OBSERVE'")
        _ensure_column(conn, "strategy_validation_results", "total_return", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "strategy_validation_results", "mdd", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "strategy_validation_results", "win_rate", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "strategy_validation_results", "trade_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "strategy_validation_results", "profit_factor", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "strategy_validation_results", "source_run_table", "TEXT NOT NULL DEFAULT 'validation_runs'")
        _ensure_column(conn, "live_order_logs", "exchange", "TEXT NOT NULL DEFAULT 'upbit'")
        _ensure_column(conn, "live_order_logs", "session_id", "INTEGER")
        _ensure_column(conn, "live_order_logs", "candidate_strategy_id", "INTEGER")
        _ensure_column(conn, "live_order_logs", "order_uuid", "TEXT")
        _ensure_column(conn, "live_order_logs", "executed_volume", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "live_order_logs", "remaining_volume", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "live_order_logs", "filled_amount_krw", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "live_order_logs", "paid_fee", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "live_order_logs", "position_id", "INTEGER")
        _ensure_column(conn, "live_order_logs", "exit_candidate_id", "INTEGER")
        _ensure_column(conn, "live_order_logs", "order_purpose", "TEXT NOT NULL DEFAULT 'ENTRY'")
        _ensure_column(conn, "live_order_logs", "exit_reason", "TEXT")
        _ensure_column(conn, "live_order_logs", "expected_pnl", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "live_order_logs", "actual_pnl", "REAL")
        _ensure_column(conn, "live_order_logs", "is_auto_exit", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "live_order_logs", "manual_confirmed", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "live_order_logs", "strategy_name", "TEXT")
        _ensure_column(conn, "live_order_logs", "signal_reason", "TEXT")
        _ensure_column(conn, "live_order_logs", "candle_time_utc", "TEXT")
        _ensure_column(conn, "risk_logs", "read_status", "TEXT NOT NULL DEFAULT 'UNREAD'")
        _ensure_column(conn, "risk_logs", "resolved_at", "TEXT")
        _ensure_column(conn, "risk_logs", "resolution_action", "TEXT")
        _ensure_column(conn, "live_positions", "highest_price_since_entry", "REAL")
        _ensure_column(conn, "live_positions", "trailing_stop_price", "REAL")
        _ensure_column(conn, "live_positions", "trailing_stop_pct", "REAL")
        _ensure_column(conn, "live_positions", "last_trailing_update_at", "TEXT")
        _ensure_column(conn, "live_positions", "scale_in_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "live_positions", "last_scale_in_at", "TEXT")
        _ensure_column(conn, "decision_snapshots", "selected_strategy_type", "TEXT")
        _ensure_column(conn, "decision_snapshots", "internal_signals_json", "TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, "decision_snapshots", "max_total_exposure_krw", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "decision_snapshots", "daily_loss_limit_pct", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "decision_snapshots", "daily_loss_limit_krw", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "decision_snapshots", "available_krw_balance", "REAL")
        _ensure_column(conn, "decision_snapshots", "exposure_limit_blocked", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "decision_snapshots", "attack_score", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "decision_snapshots", "attack_mode", "TEXT NOT NULL DEFAULT 'OFF'")
        _ensure_column(conn, "decision_snapshots", "attack_score_breakdown_json", "TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, "decision_snapshots", "aggressive_target_exposure_pct", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "decision_snapshots", "conservative_target_exposure_pct", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "decision_snapshots", "final_target_exposure_source", "TEXT NOT NULL DEFAULT 'CONSERVATIVE'")
        _ensure_column(conn, "decision_snapshots", "current_position_pnl_pct", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "decision_snapshots", "highest_price_since_entry", "REAL")
        _ensure_column(conn, "decision_snapshots", "trailing_stop_price", "REAL")
        _ensure_column(conn, "decision_snapshots", "partial_take_profit_triggered", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "decision_snapshots", "pyramiding_allowed", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "decision_snapshots", "aggressive_blockers_json", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "decision_snapshots", "aggressive_buy_blockers_json", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "decision_snapshots", "aggressive_warnings_json", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "decision_snapshots", "core_exposure_pct", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "decision_snapshots", "core_exposure_applied", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "decision_snapshots", "core_exposure_broken_by_panic", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "order_intents", "risk_preview_json", "TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, "order_intents", "policy_preview_json", "TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, "order_intents", "pilot_order_cap_krw", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "order_intents", "promotion_blockers_json", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "order_intents", "promotion_status", "TEXT NOT NULL DEFAULT 'SHADOW_ONLY'")
        _ensure_column(conn, "order_intents", "attack_score", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "order_intents", "attack_mode", "TEXT NOT NULL DEFAULT 'OFF'")
        _ensure_column(conn, "order_intents", "target_source", "TEXT NOT NULL DEFAULT 'CONSERVATIVE'")
        _ensure_column(conn, "order_intents", "pyramiding_allowed", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "order_intents", "no_averaging_down_blocked", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "order_intents", "partial_take_profit_pct", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "order_intents", "trailing_stop_price", "REAL")
        _ensure_column(conn, "order_intents", "position_pnl_pct", "REAL NOT NULL DEFAULT 0")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_smart_rehearsal_reviews_latest
            ON smart_rehearsal_reviews(exchange, market, request_id, reviewed_at DESC, id DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_execution_quality_market_strategy
            ON execution_quality_logs(exchange, market, strategy_name, created_at DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trade_outcome_market_strategy
            ON trade_outcome_logs(exchange, market, strategy_name, created_at DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trade_outcome_pending
            ON trade_outcome_logs(outcome_status, filled_at, updated_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trade_outcome_position
            ON trade_outcome_logs(position_id, created_at DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_adaptive_edge_lookup
            ON adaptive_edge_stats(exchange, market, strategy_name, candidate_strategy_id, unit, edge_score DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_adaptive_edge_rank
            ON adaptive_edge_stats(exchange, market, edge_score DESC, confidence_score DESC, last_updated_at DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_aggression_preset_logs_created
            ON aggression_preset_logs(created_at DESC, id DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_strategy_kill_switch_latest
            ON strategy_kill_switch_events(exchange, market, strategy_name, created_at DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_market_universe_selectable
            ON market_universe(exchange, is_enabled, is_auto_selectable, is_live_allowed, score DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_candidate_strategies_status_score
            ON candidate_strategies(status, score DESC, updated_at DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_active_strategy_selection_status
            ON active_strategy_selection(status, selected_at DESC, id DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_candidate_strategies_dedupe
            ON candidate_strategies(market, strategy, unit, backtest_period, status)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rebalance_delta_accumulators (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                candidate_strategy_id INTEGER,
                exchange TEXT NOT NULL DEFAULT 'bithumb',
                market TEXT NOT NULL,
                side TEXT NOT NULL,
                accumulated_delta_krw REAL NOT NULL DEFAULT 0,
                accumulated_qty REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'ACCUMULATING',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                flushed_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_rebalance_delta_accumulators_active
            ON rebalance_delta_accumulators(session_id, market, candidate_strategy_id, side, status)
            """
        )
        conn.execute(
            """
            INSERT INTO bot_operation_policy (
                market, auto_trading_enabled, max_total_exposure_krw, daily_loss_limit_pct
            ) VALUES ('KRW-BTC', 0, 500000, 3)
            ON CONFLICT(market) DO NOTHING
            """
        )
        conn.execute(
            """
            UPDATE candidate_strategies
            SET name = CASE
                    WHEN name IS NULL OR name = '' THEN strategy || ' · ' || unit || 'm · ' || printf('%.2f', score) || 'pt'
                    ELSE name
                END,
                description = COALESCE(description, ''),
                status = COALESCE(NULLIF(status, ''), 'ACTIVE'),
                updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)
            """
        )
        conn.execute(
            """
            UPDATE paper_sessions
            SET updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)
            """
        )
        missing = _missing_required_tables(conn)
        if missing:
            raise RuntimeError(f"DB_SCHEMA_MISSING: {', '.join(missing)}")


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def normalize_candidate_status(status: str | None, *, fallback: str = "ACTIVE") -> str:
    normalized = str(status or fallback).strip().upper()
    return normalized if normalized in CANDIDATE_STATUSES else fallback


def _normalize_market_universe_row(row: dict) -> dict:
    item = dict(row)
    for key in ("is_enabled", "is_live_allowed", "is_auto_selectable"):
        item[key] = bool(item.get(key))
    return item


def upsert_market_universe(items: list[dict]) -> int:
    if not items:
        return 0
    now_utc = _utc_now()
    with get_connection() as conn:
        before = conn.total_changes
        conn.executemany(
            """
            INSERT INTO market_universe (
                exchange, market, symbol, quote_currency, status, is_enabled,
                is_live_allowed, is_auto_selectable, scan_rank, score, reason,
                min_24h_trade_price_krw, last_24h_trade_price_krw, last_price,
                last_change_rate, last_volatility_score, last_liquidity_score,
                last_risk_score, last_scanned_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(exchange, market) DO UPDATE SET
                symbol = excluded.symbol,
                quote_currency = excluded.quote_currency,
                status = excluded.status,
                is_enabled = excluded.is_enabled,
                is_auto_selectable = excluded.is_auto_selectable,
                scan_rank = excluded.scan_rank,
                score = excluded.score,
                reason = excluded.reason,
                min_24h_trade_price_krw = excluded.min_24h_trade_price_krw,
                last_24h_trade_price_krw = excluded.last_24h_trade_price_krw,
                last_price = excluded.last_price,
                last_change_rate = excluded.last_change_rate,
                last_volatility_score = excluded.last_volatility_score,
                last_liquidity_score = excluded.last_liquidity_score,
                last_risk_score = excluded.last_risk_score,
                last_scanned_at = excluded.last_scanned_at,
                updated_at = excluded.updated_at
            """,
            [
                (
                    item.get("exchange", "upbit"),
                    item["market"],
                    item.get("symbol") or str(item["market"]).split("-")[-1],
                    item.get("quote_currency", "KRW"),
                    item.get("status", "DISCOVERED"),
                    1 if item.get("is_enabled", True) else 0,
                    1 if item.get("is_live_allowed", False) else 0,
                    1 if item.get("is_auto_selectable", True) else 0,
                    int(item.get("scan_rank", 0) or 0),
                    float(item.get("score", 0.0) or 0.0),
                    item.get("reason", ""),
                    float(item.get("min_24h_trade_price_krw", 0.0) or 0.0),
                    float(item.get("last_24h_trade_price_krw", 0.0) or 0.0),
                    float(item.get("last_price", 0.0) or 0.0),
                    float(item.get("last_change_rate", 0.0) or 0.0),
                    float(item.get("last_volatility_score", 0.0) or 0.0),
                    float(item.get("last_liquidity_score", 0.0) or 0.0),
                    float(item.get("last_risk_score", 0.0) or 0.0),
                    item.get("last_scanned_at") or now_utc,
                    now_utc,
                )
                for item in items
            ],
        )
        return conn.total_changes - before


def load_market_universe(*, exchange: str | None = None, enabled_only: bool = False, auto_selectable_only: bool = False, live_allowed_only: bool = False, limit: int = 200) -> list[dict]:
    filters: list[str] = []
    params: list[object] = []
    if exchange:
        filters.append("exchange = ?")
        params.append(exchange)
    if enabled_only:
        filters.append("is_enabled = 1")
    if auto_selectable_only:
        filters.append("is_auto_selectable = 1")
    if live_allowed_only:
        filters.append("is_live_allowed = 1")
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM market_universe
            {where}
            ORDER BY scan_rank ASC, score DESC, market ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [_normalize_market_universe_row(dict(row)) for row in rows]


def load_market_universe_item(exchange: str, market: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM market_universe WHERE exchange = ? AND market = ?",
            (exchange, market),
        ).fetchone()
    return _normalize_market_universe_row(dict(row)) if row else None


def load_market_universe_item_by_id(market_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM market_universe WHERE id = ?", (market_id,)).fetchone()
    return _normalize_market_universe_row(dict(row)) if row else None


def update_market_universe_item(market_id: int, updates: dict) -> dict | None:
    allowed = {
        "status",
        "is_enabled",
        "is_live_allowed",
        "is_auto_selectable",
        "scan_rank",
        "score",
        "reason",
        "min_24h_trade_price_krw",
    }
    values = {key: value for key, value in updates.items() if key in allowed}
    if not values:
        return load_market_universe_item_by_id(market_id)
    db_values = {}
    for key, value in values.items():
        db_values[key] = int(bool(value)) if key in {"is_enabled", "is_live_allowed", "is_auto_selectable"} else value
    db_values["updated_at"] = _utc_now()
    columns = ", ".join(f"{key} = ?" for key in db_values)
    params = list(db_values.values()) + [market_id]
    with get_connection() as conn:
        cursor = conn.execute(f"UPDATE market_universe SET {columns} WHERE id = ?", params)
        if cursor.rowcount == 0:
            return None
    return load_market_universe_item_by_id(market_id)


def market_is_live_allowed(exchange: str, market: str) -> bool:
    item = load_market_universe_item(exchange, market)
    if item is None:
        return market == DEFAULT_MARKET
    return bool(item.get("is_enabled") and item.get("is_live_allowed"))


def mark_market_live_allowed(exchange: str, market: str) -> int:
    now_utc = _utc_now()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE market_universe
            SET is_live_allowed = 1,
                updated_at = ?
            WHERE exchange = ?
              AND market = ?
              AND is_enabled = 1
            """,
            (now_utc, exchange, market),
        )
        if cursor.rowcount:
            return int(cursor.rowcount)
        cursor = conn.execute(
            """
            UPDATE market_universe
            SET is_live_allowed = 1,
                updated_at = ?
            WHERE market = ?
              AND is_enabled = 1
            """,
            (now_utc, market),
        )
        return int(cursor.rowcount)


def market_is_auto_selectable(exchange: str, market: str) -> bool:
    item = load_market_universe_item(exchange, market)
    if item is None:
        return market == DEFAULT_MARKET
    return bool(item.get("is_enabled") and item.get("is_auto_selectable"))


def _normalize_scheduler_state(row: dict) -> dict:
    item = dict(row)
    try:
        item["last_result"] = json.loads(item.pop("last_result_json") or "{}")
    except json.JSONDecodeError:
        item["last_result"] = {}
    return item


def _parse_scheduler_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def acquire_scheduler_task_lock(task_name: str, *, owner: str = "scheduler", ttl_seconds: int = 1800) -> tuple[bool, dict | None]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    now_utc = now.isoformat().replace("+00:00", "Z")
    lock_until = (now + timedelta(seconds=max(1, ttl_seconds))).isoformat().replace("+00:00", "Z")
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM scheduler_task_state WHERE task_name = ?", (task_name,)).fetchone()
        current = _normalize_scheduler_state(dict(row)) if row else None
        started_at = _parse_scheduler_time(str(current.get("last_started_at") or "")) if current else None
        ttl_expired = bool(started_at and started_at + timedelta(seconds=max(1, ttl_seconds)) <= now)
        if current and current.get("status") == "RUNNING" and str(current.get("lock_until") or "") > now_utc and not ttl_expired:
            return False, current
        stale_lock_recovered = bool(current and current.get("status") == "RUNNING")
        stale_result = dict(current.get("last_result") or {}) if current else {}
        if stale_lock_recovered:
            stale_result.update(
                {
                    "stale_lock_recovered": True,
                    "previous_lock_until": current.get("lock_until"),
                    "recovered_at": now_utc,
                }
            )
        conn.execute(
            """
            INSERT INTO scheduler_task_state (
                task_name, status, lock_owner, lock_until, last_started_at,
                last_error, last_result_json, updated_at
            ) VALUES (?, 'RUNNING', ?, ?, ?, '', ?, ?)
            ON CONFLICT(task_name) DO UPDATE SET
                status = 'RUNNING',
                lock_owner = excluded.lock_owner,
                lock_until = excluded.lock_until,
                last_started_at = excluded.last_started_at,
                last_error = '',
                last_result_json = CASE
                    WHEN ? THEN excluded.last_result_json
                    ELSE scheduler_task_state.last_result_json
                END,
                updated_at = excluded.updated_at
            """,
            (task_name, owner, lock_until, now_utc, json.dumps(stale_result, ensure_ascii=False), now_utc, 1 if stale_lock_recovered else 0),
        )
        row = conn.execute("SELECT * FROM scheduler_task_state WHERE task_name = ?", (task_name,)).fetchone()
    return True, _normalize_scheduler_state(dict(row)) if row else None


def finish_scheduler_task(
    task_name: str,
    *,
    status: str,
    result: dict | None = None,
    error: str = "",
    next_run_at: str | None = None,
) -> dict:
    now_utc = _utc_now()
    normalized_status = str(status or "IDLE").upper()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO scheduler_task_state (
                task_name, status, lock_owner, lock_until, last_finished_at,
                next_run_at, last_error, last_result_json, run_count, updated_at
            ) VALUES (?, ?, '', NULL, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(task_name) DO UPDATE SET
                status = excluded.status,
                lock_owner = '',
                lock_until = NULL,
                last_finished_at = excluded.last_finished_at,
                next_run_at = excluded.next_run_at,
                last_error = excluded.last_error,
                last_result_json = excluded.last_result_json,
                run_count = scheduler_task_state.run_count + 1,
                updated_at = excluded.updated_at
            """,
            (
                task_name,
                normalized_status,
                now_utc,
                next_run_at,
                error,
                json.dumps(result or {}, ensure_ascii=False),
                now_utc,
            ),
        )
        row = conn.execute("SELECT * FROM scheduler_task_state WHERE task_name = ?", (task_name,)).fetchone()
    return _normalize_scheduler_state(dict(row))


def load_scheduler_task_state(task_name: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM scheduler_task_state WHERE task_name = ?", (task_name,)).fetchone()
    return _normalize_scheduler_state(dict(row)) if row else None


def load_scheduler_task_states(task_names: list[str] | None = None) -> list[dict]:
    filters = ""
    params: list[object] = []
    if task_names:
        filters = f"WHERE task_name IN ({', '.join('?' for _ in task_names)})"
        params.extend(task_names)
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM scheduler_task_state
            {filters}
            ORDER BY task_name ASC
            """,
            params,
        ).fetchall()
    return [_normalize_scheduler_state(dict(row)) for row in rows]


def insert_candles(candles: list[dict]) -> int:
    if not candles:
        return 0
    with get_connection() as conn:
        before = conn.total_changes
        conn.executemany(
            """
            INSERT INTO candles (
                market, unit, candle_time_utc, candle_time_kst, opening_price,
                high_price, low_price, trade_price, candle_acc_trade_price,
                candle_acc_trade_volume, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(market, unit, candle_time_utc) DO UPDATE SET
                candle_time_kst = excluded.candle_time_kst,
                opening_price = excluded.opening_price,
                high_price = excluded.high_price,
                low_price = excluded.low_price,
                trade_price = excluded.trade_price,
                candle_acc_trade_price = excluded.candle_acc_trade_price,
                candle_acc_trade_volume = excluded.candle_acc_trade_volume,
                timestamp = excluded.timestamp
            """,
            [
                (
                    c["market"],
                    c["unit"],
                    c["candle_date_time_utc"],
                    c["candle_date_time_kst"],
                    c["opening_price"],
                    c["high_price"],
                    c["low_price"],
                    c["trade_price"],
                    c["candle_acc_trade_price"],
                    c["candle_acc_trade_volume"],
                    c["timestamp"],
                )
                for c in candles
            ],
        )
        return conn.total_changes - before


def load_candles(market: str, unit: int, limit: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM candles
            WHERE market = ? AND unit = ?
            ORDER BY candle_time_utc DESC
            LIMIT ?
            """,
            (market, unit, limit),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def load_candles_between(market: str, unit: int, start_utc: str, end_utc: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM candles
            WHERE market = ?
              AND unit = ?
              AND candle_time_utc >= ?
              AND candle_time_utc <= ?
            ORDER BY candle_time_utc ASC
            """,
            (market, unit, start_utc, end_utc),
        ).fetchall()
    return [dict(row) for row in rows]


def save_backtest(
    market: str,
    unit: int,
    strategy: str,
    settings: dict,
    risk: dict,
    metrics: dict,
    signals: list[dict],
    orders: list[dict],
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO backtest_results (
                market, unit, strategy, settings_json, risk_json, metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                market,
                unit,
                strategy,
                json.dumps(settings, ensure_ascii=False),
                json.dumps(risk, ensure_ascii=False),
                json.dumps(metrics, ensure_ascii=False),
            ),
        )
        backtest_id = int(cursor.lastrowid)
        conn.executemany(
            """
            INSERT INTO signals (
                backtest_id, market, unit, strategy, candle_time_utc,
                signal, price, reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    backtest_id,
                    market,
                    unit,
                    strategy,
                    s["time"],
                    s["signal"],
                    s["price"],
                    s["reason"],
                )
                for s in signals
            ],
        )
        conn.executemany(
            """
            INSERT INTO virtual_orders (
                backtest_id, market, side, candle_time_utc, price, quantity, fee, pnl
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    backtest_id,
                    market,
                    o["side"],
                    o["time"],
                    o["price"],
                    o["quantity"],
                    o["fee"],
                    o.get("pnl"),
                )
                for o in orders
            ],
        )
    return backtest_id


def save_validation_run(market: str, strategy: str, request: dict, rows: list[dict]) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO validation_runs (market, strategy, request_json)
            VALUES (?, ?, ?)
            """,
            (market, strategy, json.dumps(request, ensure_ascii=False)),
        )
        run_id = int(cursor.lastrowid)
        conn.executemany(
            """
            INSERT INTO strategy_validation_results (
                run_id, market, unit, strategy, parameters_json, period_label,
                metrics_json, warnings_json, stability_score, decision,
                total_return, mdd, win_rate, trade_count, profit_factor
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    row["market"],
                    row["unit"],
                    row["strategy"],
                    json.dumps(row["parameters"], ensure_ascii=False),
                    row["period_label"],
                    json.dumps(row["metrics"], ensure_ascii=False),
                    json.dumps(row["warnings"], ensure_ascii=False),
                    row["stability_score"],
                    row.get("decision", "OBSERVE"),
                    float(row.get("metrics", {}).get("total_return", 0.0) or 0.0),
                    float(row.get("metrics", {}).get("mdd", 0.0) or 0.0),
                    float(row.get("metrics", {}).get("win_rate", 0.0) or 0.0),
                    int(row.get("metrics", {}).get("trade_count", 0) or 0),
                    float(row.get("metrics", {}).get("profit_factor", 0.0) or 0.0),
                )
                for row in rows
            ],
        )
        return run_id


def save_strategy_validation_run(run: dict, rows: list[dict]) -> int:
    now_utc = _utc_now()
    summary = run.get("summary", {})
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO strategy_validation_runs (
                exchange, market_count, strategy_count, timeframes_json,
                periods_json, risk_json, request_json, summary_json,
                status, started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.get("exchange", "upbit"),
                int(run.get("market_count", 0) or 0),
                int(run.get("strategy_count", 0) or 0),
                json.dumps(run.get("timeframes", []), ensure_ascii=False),
                json.dumps(run.get("periods", []), ensure_ascii=False),
                json.dumps(run.get("risk", {}), ensure_ascii=False),
                json.dumps(run.get("request", {}), ensure_ascii=False),
                json.dumps(summary, ensure_ascii=False),
                run.get("status", "COMPLETED"),
                run.get("started_at") or now_utc,
                run.get("finished_at") or now_utc,
            ),
        )
        run_id = int(cursor.lastrowid)
        conn.executemany(
            """
            INSERT INTO strategy_validation_results (
                run_id, market, unit, strategy, parameters_json, period_label,
                metrics_json, warnings_json, stability_score, decision,
                total_return, mdd, win_rate, trade_count, profit_factor,
                source_run_table
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'strategy_validation_runs')
            """,
            [
                (
                    run_id,
                    row["market"],
                    row["unit"],
                    row["strategy"],
                    json.dumps(row["parameters"], ensure_ascii=False),
                    row["period_label"],
                    json.dumps(row["metrics"], ensure_ascii=False),
                    json.dumps(row.get("warnings", []), ensure_ascii=False),
                    float(row.get("stability_score", 0.0) or 0.0),
                    row.get("decision", "OBSERVE"),
                    float(row.get("metrics", {}).get("total_return", 0.0) or 0.0),
                    float(row.get("metrics", {}).get("mdd", 0.0) or 0.0),
                    float(row.get("metrics", {}).get("win_rate", 0.0) or 0.0),
                    int(row.get("metrics", {}).get("trade_count", 0) or 0),
                    float(row.get("metrics", {}).get("profit_factor", 0.0) or 0.0),
                )
                for row in rows
            ],
        )
        return run_id


def save_candidate_strategy(candidate: dict) -> int:
    now_utc = _utc_now()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO candidate_strategies (
                strategy, parameters_json, unit, market, backtest_period, score,
                backtest_total_return, backtest_mdd, backtest_win_rate,
                backtest_profit_factor, backtest_trade_count,
                backtest_average_trade_pnl, warning, name, description, status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate["strategy"],
                json.dumps(candidate["parameters"], ensure_ascii=False),
                candidate["unit"],
                candidate["market"],
                candidate["backtest_period"],
                candidate["score"],
                candidate.get("backtest_total_return", 0.0),
                candidate.get("backtest_mdd", 0.0),
                candidate.get("backtest_win_rate", 0.0),
                candidate.get("backtest_profit_factor", 0.0),
                candidate.get("backtest_trade_count", 0),
                candidate.get("backtest_average_trade_pnl", 0.0),
                candidate.get("warning", ""),
                candidate.get("name") or f"{candidate['strategy']} · {candidate['unit']}m · {float(candidate['score']):.2f}pt",
                candidate.get("description", ""),
                normalize_candidate_status(candidate.get("status"), fallback="ACTIVE"),
                now_utc,
            ),
        )
        return int(cursor.lastrowid)


def load_candidate_strategies(limit: int = 50, *, statuses: list[str] | None = None, market: str | None = None) -> list[dict]:
    filters: list[str] = []
    params: list[object] = []
    if statuses:
        normalized = [normalize_candidate_status(status) for status in statuses]
        placeholders = ", ".join("?" for _ in normalized)
        filters.append(f"status IN ({placeholders})")
        params.extend(normalized)
    if market:
        filters.append("market = ?")
        params.append(market)
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM candidate_strategies
            {where}
            ORDER BY score DESC, updated_at DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    candidates = []
    for row in rows:
        item = dict(row)
        item["parameters"] = json.loads(item.pop("parameters_json"))
        candidates.append(item)
    return candidates


def load_candidate_strategies_without_forward_session(limit: int = 50, *, status: str = "BACKTEST_PASSED") -> list[dict]:
    normalized_status = normalize_candidate_status(status)
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT candidate_strategies.*
            FROM candidate_strategies
            LEFT JOIN paper_forward_sessions
              ON paper_forward_sessions.candidate_strategy_id = candidate_strategies.id
             AND paper_forward_sessions.status IN ('READY', 'RUNNING', 'COMPLETED', 'STOPPED')
            WHERE candidate_strategies.status = ?
              AND paper_forward_sessions.id IS NULL
            ORDER BY candidate_strategies.score DESC, candidate_strategies.updated_at DESC, candidate_strategies.id DESC
            LIMIT ?
            """,
            (normalized_status, limit),
        ).fetchall()
    candidates = []
    for row in rows:
        item = dict(row)
        item["parameters"] = json.loads(item.pop("parameters_json"))
        candidates.append(item)
    return candidates


def count_candidate_strategies_created_since(created_at: str, *, statuses: list[str] | None = None) -> int:
    filters = ["datetime(created_at) >= datetime(?)"]
    params: list[object] = [created_at]
    if statuses:
        normalized = [normalize_candidate_status(status) for status in statuses]
        filters.append(f"status IN ({', '.join('?' for _ in normalized)})")
        params.extend(normalized)
    with get_connection() as conn:
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM candidate_strategies
            WHERE {' AND '.join(filters)}
            """,
            params,
        ).fetchone()
    return int(row["count"] if row else 0)


def find_duplicate_candidate_strategy(candidate: dict, *, statuses: list[str] | None = None) -> dict | None:
    normalized_statuses = [normalize_candidate_status(status) for status in statuses] if statuses else []
    filters = [
        "market = ?",
        "strategy = ?",
        "unit = ?",
        "backtest_period = ?",
    ]
    params: list[object] = [
        candidate.get("market"),
        candidate.get("strategy"),
        int(candidate.get("unit") or 0),
        candidate.get("backtest_period"),
    ]
    if normalized_statuses:
        filters.append(f"status IN ({', '.join('?' for _ in normalized_statuses)})")
        params.extend(normalized_statuses)
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM candidate_strategies
            WHERE {' AND '.join(filters)}
            ORDER BY score DESC, updated_at DESC, id DESC
            LIMIT 100
            """,
            params,
        ).fetchall()
    target_parameters = candidate.get("parameters") or {}
    for row in rows:
        item = dict(row)
        try:
            parameters = json.loads(item.get("parameters_json") or "{}")
        except json.JSONDecodeError:
            parameters = {}
        if parameters == target_parameters:
            item["parameters"] = parameters
            item.pop("parameters_json", None)
            return item
    return None


def load_candidate_strategy(candidate_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM candidate_strategies WHERE id = ?",
            (candidate_id,),
        ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["parameters"] = json.loads(item.pop("parameters_json"))
    return item


def ensure_default_candidate_strategies() -> int:
    now_utc = _utc_now()
    changed = 0
    with get_connection() as conn:
        _ensure_app_settings_table(conn)
        seeded = conn.execute(
            "SELECT value_json FROM app_settings WHERE key = ?",
            ("default_candidate_strategies_seeded",),
        ).fetchone()
        if seeded is not None:
            return 0
        for candidate in DEFAULT_CANDIDATE_STRATEGIES:
            row = conn.execute(
                "SELECT id FROM candidate_strategies WHERE name = ?",
                (candidate["name"],),
            ).fetchone()
            values = (
                candidate["strategy"],
                json.dumps(candidate["parameters"], ensure_ascii=False),
                candidate["unit"],
                candidate["market"],
                candidate["backtest_period"],
                candidate["score"],
                candidate.get("backtest_total_return", 0.0),
                candidate.get("backtest_mdd", 0.0),
                candidate.get("backtest_win_rate", 0.0),
                candidate.get("backtest_profit_factor", 0.0),
                candidate.get("backtest_trade_count", 0),
                candidate.get("backtest_average_trade_pnl", 0.0),
                candidate.get("warning", ""),
                candidate["name"],
                candidate.get("description", ""),
                normalize_candidate_status(candidate.get("status"), fallback="ACTIVE"),
                now_utc,
            )
            if row is None:
                conn.execute(
                    """
                    INSERT INTO candidate_strategies (
                        strategy, parameters_json, unit, market, backtest_period, score,
                        backtest_total_return, backtest_mdd, backtest_win_rate,
                        backtest_profit_factor, backtest_trade_count,
                        backtest_average_trade_pnl, warning, name, description, status, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                changed += 1
                continue
            conn.execute(
                """
                UPDATE candidate_strategies
                SET strategy = ?,
                    parameters_json = ?,
                    unit = ?,
                    market = ?,
                    backtest_period = ?,
                    score = MAX(score, ?),
                    backtest_total_return = CASE WHEN warning = '백테스트 실행 필요' THEN ? ELSE backtest_total_return END,
                    backtest_mdd = CASE WHEN warning = '백테스트 실행 필요' THEN ? ELSE backtest_mdd END,
                    backtest_win_rate = CASE WHEN warning = '백테스트 실행 필요' THEN ? ELSE backtest_win_rate END,
                    backtest_profit_factor = CASE WHEN warning = '백테스트 실행 필요' THEN ? ELSE backtest_profit_factor END,
                    backtest_trade_count = CASE WHEN warning = '백테스트 실행 필요' THEN ? ELSE backtest_trade_count END,
                    backtest_average_trade_pnl = CASE WHEN warning = '백테스트 실행 필요' THEN ? ELSE backtest_average_trade_pnl END,
                    warning = CASE WHEN warning = '' THEN ? ELSE warning END,
                    name = ?,
                    description = ?,
                    status = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                values + (int(row["id"]),),
            )
            changed += 1
        conn.execute(
            """
            INSERT INTO app_settings (key, value_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at
            """,
            ("default_candidate_strategies_seeded", json.dumps(True), now_utc),
        )
    return changed


def update_candidate_strategy(candidate_id: int, updates: dict) -> dict | None:
    current = load_candidate_strategy(candidate_id)
    if current is None:
        return None
    allowed = {
        "name",
        "description",
        "strategy",
        "parameters",
        "unit",
        "market",
        "backtest_period",
        "score",
        "backtest_total_return",
        "backtest_mdd",
        "backtest_win_rate",
        "backtest_profit_factor",
        "backtest_trade_count",
        "backtest_average_trade_pnl",
        "warning",
        "status",
    }
    values = {key: value for key, value in updates.items() if key in allowed}
    if not values:
        return current
    if "status" in values:
        values["status"] = normalize_candidate_status(str(values["status"]), fallback=current.get("status", "ACTIVE"))
    db_values = {}
    for key, value in values.items():
        if key == "parameters":
            db_values["parameters_json"] = json.dumps(value or {}, ensure_ascii=False)
        else:
            db_values[key] = value
    db_values["updated_at"] = _utc_now()
    columns = ", ".join(f"{key} = ?" for key in db_values)
    params = list(db_values.values()) + [candidate_id]
    with get_connection() as conn:
        conn.execute(f"UPDATE candidate_strategies SET {columns} WHERE id = ?", params)
    return load_candidate_strategy(candidate_id)


def clone_candidate_strategy(candidate_id: int) -> dict | None:
    current = load_candidate_strategy(candidate_id)
    if current is None:
        return None
    clone = {
        **current,
        "name": f"{current.get('name') or current['strategy']} 복사본",
        "description": current.get("description", ""),
        "status": "INACTIVE",
    }
    new_id = save_candidate_strategy(clone)
    return load_candidate_strategy(new_id)


def set_candidate_strategy_status(candidate_id: int, status: str) -> dict | None:
    normalized = normalize_candidate_status(status, fallback="INACTIVE")
    return update_candidate_strategy(candidate_id, {"status": normalized})


def record_candidate_promotion(
    candidate_strategy_id: int,
    *,
    from_status: str,
    to_status: str,
    reason: str = "",
    score: float = 0.0,
    metadata: dict | None = None,
) -> dict:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO candidate_strategy_promotions (
                candidate_strategy_id, from_status, to_status, reason, score, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_strategy_id,
                normalize_candidate_status(from_status, fallback=from_status),
                normalize_candidate_status(to_status, fallback=to_status),
                reason,
                float(score or 0.0),
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        row = conn.execute("SELECT * FROM candidate_strategy_promotions WHERE id = ?", (cursor.lastrowid,)).fetchone()
    item = dict(row)
    item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
    return item


def promote_candidate_strategy(candidate_id: int, to_status: str, *, reason: str = "", metadata: dict | None = None) -> dict | None:
    current = load_candidate_strategy(candidate_id)
    if current is None:
        return None
    from_status = str(current.get("status") or "ACTIVE")
    normalized = normalize_candidate_status(to_status, fallback=from_status)
    updated = update_candidate_strategy(candidate_id, {"status": normalized})
    if updated is None:
        return None
    live_allowed_updates = 0
    if normalized in {"LIVE_ELIGIBLE", "LIVE_ACTIVE"}:
        exchange = str((metadata or {}).get("exchange") or os.getenv("AUTO_ALLOWED_EXCHANGE", "bithumb")).strip().lower() or "bithumb"
        live_allowed_updates = mark_market_live_allowed(exchange, str(updated.get("market") or current.get("market") or DEFAULT_MARKET))
        if metadata is None:
            metadata = {}
        metadata = {**metadata, "market_live_allowed_updates": live_allowed_updates}
    record_candidate_promotion(
        candidate_id,
        from_status=from_status,
        to_status=normalized,
        reason=reason,
        score=float(updated.get("score") or 0.0),
        metadata=metadata,
    )
    return updated


def reject_candidate_strategy(candidate_id: int, *, reason: str = "", metadata: dict | None = None) -> dict | None:
    return promote_candidate_strategy(candidate_id, "REJECTED", reason=reason, metadata=metadata)


def load_live_eligible_candidate_strategies(limit: int = 50) -> list[dict]:
    return load_candidate_strategies(limit, statuses=sorted(LIVE_CANDIDATE_STATUSES))


def load_active_strategy_selection() -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM active_strategy_selection
            WHERE status = 'LIVE_ACTIVE'
            ORDER BY selected_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["parameters"] = json.loads(item.pop("parameters_json") or "{}")
    return item


def save_active_strategy_selection(candidate: dict, *, reason: str = "", replaced_candidate_strategy_id: int | None = None, cooldown_until: str | None = None) -> dict:
    now_utc = _utc_now()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE active_strategy_selection
            SET status = 'REPLACED',
                updated_at = ?
            WHERE status = 'LIVE_ACTIVE'
            """,
            (now_utc,),
        )
        cursor = conn.execute(
            """
            INSERT INTO active_strategy_selection (
                candidate_strategy_id, market, strategy, unit, parameters_json,
                status, selected_reason, selected_at, replaced_candidate_strategy_id,
                cooldown_until, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'LIVE_ACTIVE', ?, ?, ?, ?, ?)
            """,
            (
                int(candidate["id"]),
                candidate["market"],
                candidate["strategy"],
                int(candidate["unit"]),
                json.dumps(candidate.get("parameters", {}), ensure_ascii=False),
                reason,
                now_utc,
                replaced_candidate_strategy_id,
                cooldown_until,
                now_utc,
            ),
        )
        row = conn.execute("SELECT * FROM active_strategy_selection WHERE id = ?", (cursor.lastrowid,)).fetchone()
    item = dict(row)
    item["parameters"] = json.loads(item.pop("parameters_json") or "{}")
    return item


def record_strategy_switch(
    *,
    from_candidate_strategy_id: int | None,
    to_candidate_strategy_id: int | None,
    from_market: str | None,
    to_market: str | None,
    decision: str,
    reason: str = "",
    blocked_reason: str = "",
    score_delta: float = 0.0,
) -> dict:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO strategy_switch_logs (
                from_candidate_strategy_id, to_candidate_strategy_id, from_market,
                to_market, decision, reason, blocked_reason, score_delta
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                from_candidate_strategy_id,
                to_candidate_strategy_id,
                from_market,
                to_market,
                decision,
                reason,
                blocked_reason,
                float(score_delta or 0.0),
            ),
        )
        row = conn.execute("SELECT * FROM strategy_switch_logs WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return dict(row)


def load_strategy_switch_logs(limit: int = 20) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM strategy_switch_logs
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def load_strategy_switch_logs_with_candidates(limit: int = 20) -> list[dict]:
    logs = load_strategy_switch_logs(limit)
    candidate_ids = {
        int(value)
        for log in logs
        for value in (log.get("from_candidate_strategy_id"), log.get("to_candidate_strategy_id"))
        if value is not None
    }
    candidates: dict[int, dict] = {}
    for candidate_id in candidate_ids:
        candidate = load_candidate_strategy(candidate_id)
        if candidate:
            candidates[candidate_id] = candidate
    enriched = []
    for log in logs:
        from_candidate_id = log.get("from_candidate_strategy_id")
        to_candidate_id = log.get("to_candidate_strategy_id")
        enriched.append(
            {
                **log,
                "from_candidate": candidates.get(int(from_candidate_id)) if from_candidate_id is not None else None,
                "to_candidate": candidates.get(int(to_candidate_id)) if to_candidate_id is not None else None,
            }
        )
    return enriched


def count_strategy_switches_today() -> int:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS value
            FROM strategy_switch_logs
            WHERE decision = 'APPLIED'
              AND date(created_at) = date('now')
            """
        ).fetchone()
    return int(row["value"] or 0) if row else 0


def delete_candidate_strategy(candidate_id: int) -> bool:
    with get_connection() as conn:
        references = [
            conn.execute("SELECT 1 FROM live_strategy_sessions WHERE candidate_strategy_id = ? LIMIT 1", (candidate_id,)).fetchone(),
            conn.execute("SELECT 1 FROM live_order_logs WHERE candidate_strategy_id = ? LIMIT 1", (candidate_id,)).fetchone(),
            conn.execute("SELECT 1 FROM live_positions WHERE candidate_strategy_id = ? LIMIT 1", (candidate_id,)).fetchone(),
            conn.execute("SELECT 1 FROM paper_forward_sessions WHERE candidate_strategy_id = ? LIMIT 1", (candidate_id,)).fetchone(),
        ]
        if any(references):
            return False
        cursor = conn.execute("DELETE FROM candidate_strategies WHERE id = ?", (candidate_id,))
        return cursor.rowcount > 0


def pause_running_forward_sessions_on_startup() -> int:
    now_utc = _utc_now()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE paper_forward_sessions
            SET status = 'STOPPED',
                stopped_at = ?,
                updated_at = ?,
                last_risk_result = 'STOPPED_ON_SERVER_RESTART'
            WHERE status IN ('READY', 'RUNNING')
            """,
            (now_utc, now_utc),
        )
        return cursor.rowcount


def create_forward_session_from_candidate(
    candidate: dict,
    *,
    initial_balance_krw: float,
    risk: dict,
    current_price: float,
    last_processed_candle_time_utc: str | None,
) -> int:
    now_utc = _utc_now()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO paper_forward_sessions (
                candidate_strategy_id, market, unit, strategy, parameters_json, risk_json,
                status, initial_balance_krw, current_balance_krw, current_position_volume,
                average_entry_price, current_price, realized_pnl, unrealized_pnl,
                total_equity, total_return_percent, max_drawdown, trade_count, win_count,
                loss_count, win_rate, profit_factor, gross_profit, gross_loss,
                last_signal, last_risk_result, last_processed_candle_time_utc,
                last_tick_time_utc, started_at, stopped_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, 'RUNNING', ?, ?, 0, 0, ?, 0, 0,
                ?, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                'HOLD', 'ACTIVE', ?, ?, ?, NULL, ?
            )
            """,
            (
                candidate["id"],
                candidate["market"],
                candidate["unit"],
                candidate["strategy"],
                json.dumps(candidate["parameters"], ensure_ascii=False),
                json.dumps(risk, ensure_ascii=False),
                initial_balance_krw,
                initial_balance_krw,
                current_price,
                initial_balance_krw,
                last_processed_candle_time_utc,
                now_utc,
                now_utc,
                now_utc,
            ),
        )
        session_id = int(cursor.lastrowid)
        conn.execute(
            """
            INSERT OR IGNORE INTO paper_forward_equity_points (
                session_id, candle_time_utc, equity, cash_balance, position_volume, price, drawdown
            ) VALUES (?, ?, ?, ?, 0, ?, 0)
            """,
            (
                session_id,
                last_processed_candle_time_utc or now_utc,
                initial_balance_krw,
                initial_balance_krw,
                current_price,
            ),
        )
        return session_id


def load_running_forward_sessions() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id FROM paper_forward_sessions
            WHERE status IN ('READY', 'RUNNING')
            ORDER BY id ASC
            """
        ).fetchall()
    return [session for row in rows if (session := load_forward_session(int(row["id"]))) is not None]


def load_latest_forward_session() -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id FROM paper_forward_sessions
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        return None
    return load_forward_session(int(row["id"]))


def load_latest_forward_session_for_candidate(candidate_strategy_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id FROM paper_forward_sessions
            WHERE candidate_strategy_id = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (candidate_strategy_id,),
        ).fetchone()
    if row is None:
        return None
    return load_forward_session(int(row["id"]))


def load_forward_sessions(limit: int = 20) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id FROM paper_forward_sessions
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [session for row in rows if (session := load_forward_session(int(row["id"]))) is not None]


def stop_forward_session(session_id: int | None = None) -> dict | None:
    now_utc = _utc_now()
    with get_connection() as conn:
        if session_id is None:
            row = conn.execute(
                """
                SELECT id FROM paper_forward_sessions
                WHERE status = 'RUNNING'
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id FROM paper_forward_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        conn.execute(
            """
            UPDATE paper_forward_sessions
            SET status = 'STOPPED',
                stopped_at = ?,
                updated_at = ?,
                last_risk_result = 'STOPPED_BY_USER'
            WHERE id = ?
            """,
            (now_utc, now_utc, row["id"]),
        )
        return load_forward_session(int(row["id"]), conn)


def append_forward_equity_point(
    session_id: int,
    *,
    candle_time_utc: str,
    equity: float,
    cash_balance: float,
    position_volume: float,
    price: float,
    drawdown: float,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO paper_forward_equity_points (
                session_id, candle_time_utc, equity, cash_balance, position_volume, price, drawdown
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, candle_time_utc, equity, cash_balance, position_volume, price, drawdown),
        )


def insert_forward_order(session_id: int, order: dict) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO paper_forward_orders (
                session_id, candidate_strategy_id, market, unit, strategy, side,
                price, volume, amount_krw, fee, slippage, realized_pnl,
                reason, risk_result, candle_time_utc, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                order["candidate_strategy_id"],
                order["market"],
                order["unit"],
                order["strategy"],
                order["side"],
                order["price"],
                order["volume"],
                order["amount_krw"],
                order["fee"],
                order["slippage"],
                order.get("realized_pnl"),
                order["reason"],
                order["risk_result"],
                order["candle_time_utc"],
                order.get("created_at", _utc_now()),
            ),
        )


def insert_forward_tick_log(
    *,
    session_id: int,
    tick_time_utc: str,
    market: str,
    unit: int,
    latest_candle_time_utc: str | None,
    last_processed_candle_time_utc: str | None,
    result: str,
    message: str,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO paper_forward_tick_logs (
                tick_time_utc, session_id, market, unit, latest_candle_time_utc,
                last_processed_candle_time_utc, result, message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tick_time_utc,
                session_id,
                market,
                unit,
                latest_candle_time_utc,
                last_processed_candle_time_utc,
                result,
                message,
            ),
        )


def insert_forward_signal_log(
    *,
    signal_time_utc: str,
    session_id: int,
    strategy: str,
    signal: str,
    confidence: float,
    reason: str,
    risk_result: str,
    candle_time_utc: str,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO paper_forward_signal_logs (
                signal_time_utc, session_id, strategy, signal, confidence,
                reason, risk_result, candle_time_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_time_utc,
                session_id,
                strategy,
                signal,
                confidence,
                reason,
                risk_result,
                candle_time_utc,
            ),
        )


def update_forward_session_state(session_id: int, state: dict) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE paper_forward_sessions
            SET status = ?,
                current_balance_krw = ?,
                current_position_volume = ?,
                average_entry_price = ?,
                current_price = ?,
                realized_pnl = ?,
                unrealized_pnl = ?,
                total_equity = ?,
                total_return_percent = ?,
                max_drawdown = ?,
                trade_count = ?,
                win_count = ?,
                loss_count = ?,
                win_rate = ?,
                profit_factor = ?,
                gross_profit = ?,
                gross_loss = ?,
                last_signal = ?,
                last_risk_result = ?,
                last_processed_candle_time_utc = ?,
                last_tick_time_utc = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                state.get("status", "RUNNING"),
                state["current_balance_krw"],
                state["current_position_volume"],
                state["average_entry_price"],
                state["current_price"],
                state["realized_pnl"],
                state["unrealized_pnl"],
                state["total_equity"],
                state["total_return_percent"],
                state["max_drawdown"],
                state["trade_count"],
                state["win_count"],
                state["loss_count"],
                state["win_rate"],
                state["profit_factor"],
                state["gross_profit"],
                state["gross_loss"],
                state["last_signal"],
                state["last_risk_result"],
                state["last_processed_candle_time_utc"],
                state["last_tick_time_utc"],
                state.get("updated_at", _utc_now()),
                session_id,
            ),
        )


def mark_forward_session_error(session_id: int, message: str) -> None:
    now_utc = _utc_now()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE paper_forward_sessions
            SET status = 'ERROR',
                last_risk_result = ?,
                last_tick_time_utc = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (message[:120], now_utc, now_utc, session_id),
        )


def insert_live_order_log(log: dict) -> int:
    now_utc = _utc_now()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO live_order_logs (
                request_id, session_id, candidate_strategy_id, exchange, market, side, order_type, price, volume, amount_krw,
                fee_estimate, risk_result, order_preview_payload,
                exchange_request_payload_masked, exchange_response_payload,
                status, error_message, order_uuid, executed_volume, remaining_volume,
                filled_amount_krw, paid_fee, position_id, exit_candidate_id, order_purpose,
                exit_reason, expected_pnl, actual_pnl, is_auto_exit, manual_confirmed,
                strategy_name, signal_reason,
                candle_time_utc, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                log["request_id"],
                log.get("session_id"),
                log.get("candidate_strategy_id"),
                log.get("exchange", "upbit"),
                log["market"],
                log["side"],
                log["order_type"],
                log.get("price"),
                log.get("volume"),
                log.get("amount_krw"),
                log.get("fee_estimate", 0.0),
                log["risk_result"],
                json.dumps(log.get("order_preview_payload", {}), ensure_ascii=False),
                json.dumps(log.get("exchange_request_payload_masked", {}), ensure_ascii=False),
                json.dumps(log.get("exchange_response_payload", {}), ensure_ascii=False),
                log["status"],
                log.get("error_message"),
                log.get("order_uuid"),
                log.get("executed_volume", 0.0),
                log.get("remaining_volume", 0.0),
                log.get("filled_amount_krw", 0.0),
                log.get("paid_fee", 0.0),
                log.get("position_id"),
                log.get("exit_candidate_id"),
                log.get("order_purpose", "ENTRY"),
                log.get("exit_reason"),
                log.get("expected_pnl", 0.0),
                log.get("actual_pnl"),
                1 if log.get("is_auto_exit", False) else 0,
                1 if log.get("manual_confirmed", False) else 0,
                log.get("strategy_name"),
                log.get("signal_reason"),
                log.get("candle_time_utc"),
                now_utc,
                now_utc,
            ),
        )
        return int(cursor.lastrowid)


def update_live_order_log(request_id: str, updates: dict) -> None:
    current = get_live_order_log(request_id)
    if current is None:
        return
    merged = {**current, **updates}
    now_utc = _utc_now()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE live_order_logs
            SET risk_result = ?,
                exchange_request_payload_masked = ?,
                exchange_response_payload = ?,
                status = ?,
                error_message = ?,
                session_id = ?,
                candidate_strategy_id = ?,
                order_uuid = ?,
                executed_volume = ?,
                remaining_volume = ?,
                filled_amount_krw = ?,
                paid_fee = ?,
                position_id = ?,
                exit_candidate_id = ?,
                order_purpose = ?,
                exit_reason = ?,
                expected_pnl = ?,
                actual_pnl = ?,
                is_auto_exit = ?,
                manual_confirmed = ?,
                strategy_name = ?,
                signal_reason = ?,
                candle_time_utc = ?,
                updated_at = ?
            WHERE request_id = ?
            """,
            (
                merged["risk_result"],
                json.dumps(merged.get("exchange_request_payload_masked", {}), ensure_ascii=False),
                json.dumps(merged.get("exchange_response_payload", {}), ensure_ascii=False),
                merged["status"],
                merged.get("error_message"),
                merged.get("session_id"),
                merged.get("candidate_strategy_id"),
                merged.get("order_uuid"),
                merged.get("executed_volume", 0.0),
                merged.get("remaining_volume", 0.0),
                merged.get("filled_amount_krw", 0.0),
                merged.get("paid_fee", 0.0),
                merged.get("position_id"),
                merged.get("exit_candidate_id"),
                merged.get("order_purpose", "ENTRY"),
                merged.get("exit_reason"),
                merged.get("expected_pnl", 0.0),
                merged.get("actual_pnl"),
                1 if merged.get("is_auto_exit", False) else 0,
                1 if merged.get("manual_confirmed", False) else 0,
                merged.get("strategy_name"),
                merged.get("signal_reason"),
                merged.get("candle_time_utc"),
                now_utc,
                request_id,
            ),
        )


def get_live_order_log(request_id: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM live_order_logs WHERE request_id = ?",
            (request_id,),
        ).fetchone()
    if row is None:
        return None
    return _normalize_live_order_log(dict(row))


def insert_smart_rehearsal_review(
    *,
    request_id: str,
    exchange: str = "bithumb",
    market: str = "KRW-BTC",
    decision: str,
    note: str = "",
    reviewed_by: str = "admin",
) -> dict:
    normalized_decision = str(decision or "").strip().upper()
    if normalized_decision not in {"APPROVED", "REJECTED"}:
        raise ValueError("decision must be APPROVED or REJECTED.")
    now_utc = _utc_now()
    expires_at = _smart_rehearsal_review_expiry(now_utc) if normalized_decision == "APPROVED" else None
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO smart_rehearsal_reviews (
                request_id, exchange, market, decision, note, reviewed_by, reviewed_at, expires_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                exchange,
                market,
                normalized_decision,
                str(note or "")[:1000],
                str(reviewed_by or "admin")[:120],
                now_utc,
                expires_at,
                now_utc,
            ),
        )
        row = conn.execute("SELECT * FROM smart_rehearsal_reviews WHERE id = ?", (int(cursor.lastrowid),)).fetchone()
    return _normalize_smart_rehearsal_review(dict(row))


def load_smart_rehearsal_review(
    request_id: str | None,
    exchange: str = "bithumb",
    market: str = "KRW-BTC",
) -> dict | None:
    if not request_id:
        return None
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM smart_rehearsal_reviews
            WHERE request_id = ?
              AND exchange = ?
              AND market = ?
            ORDER BY reviewed_at DESC, id DESC
            LIMIT 1
            """,
            (request_id, exchange, market),
        ).fetchone()
    return _normalize_smart_rehearsal_review(dict(row)) if row else None


def load_latest_smart_rehearsal_review(
    exchange: str = "bithumb",
    market: str = "KRW-BTC",
) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM smart_rehearsal_reviews
            WHERE exchange = ?
              AND market = ?
            ORDER BY reviewed_at DESC, id DESC
            LIMIT 1
            """,
            (exchange, market),
        ).fetchone()
    return _normalize_smart_rehearsal_review(dict(row)) if row else None


def load_live_order_logs(limit: int = 100, include_canonical_with_events: bool = False) -> list[dict]:
    if include_canonical_with_events:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM live_order_logs
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_normalize_live_order_log(dict(row)) for row in rows]

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM live_order_logs AS log
            WHERE NOT (
                log.order_uuid IS NOT NULL
                AND log.request_id NOT LIKE '%-submitted%'
                AND log.request_id NOT LIKE '%-waiting-%'
                AND log.request_id NOT LIKE '%-partial%'
                AND log.request_id NOT LIKE '%-canceled-%'
                AND log.request_id NOT LIKE '%-filled-%'
                AND log.request_id NOT LIKE '%-failed-%'
                AND EXISTS (
                    SELECT 1
                    FROM live_order_logs AS event_log
                    WHERE event_log.order_uuid = log.order_uuid
                      AND (
                          event_log.request_id LIKE '%-submitted%'
                          OR event_log.request_id LIKE '%-waiting-%'
                          OR event_log.request_id LIKE '%-partial%'
                          OR event_log.request_id LIKE '%-canceled-%'
                          OR event_log.request_id LIKE '%-filled-%'
                          OR event_log.request_id LIKE '%-failed-%'
                    )
                )
            )
            AND NOT (
                log.order_uuid IS NOT NULL
                AND (
                    log.request_id LIKE '%-submitted%'
                    OR log.request_id LIKE '%-waiting-%'
                    OR log.request_id LIKE '%-partial%'
                    OR log.request_id LIKE '%-canceled-%'
                    OR log.request_id LIKE '%-filled-%'
                    OR log.request_id LIKE '%-failed-%'
                )
                AND EXISTS (
                    SELECT 1
                    FROM live_order_logs AS newer_event
                    WHERE newer_event.order_uuid = log.order_uuid
                      AND newer_event.status = log.status
                      AND (
                          newer_event.request_id LIKE '%-submitted%'
                          OR newer_event.request_id LIKE '%-waiting-%'
                          OR newer_event.request_id LIKE '%-partial%'
                          OR newer_event.request_id LIKE '%-canceled-%'
                          OR newer_event.request_id LIKE '%-filled-%'
                          OR newer_event.request_id LIKE '%-failed-%'
                      )
                      AND (
                          newer_event.created_at > log.created_at
                          OR (newer_event.created_at = log.created_at AND newer_event.id > log.id)
                      )
                )
            )
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_normalize_live_order_log(dict(row)) for row in rows]


def load_trade_history_logs(limit: int = 100) -> list[dict]:
    status_placeholders = ", ".join("?" for _ in TRADE_HISTORY_STATUSES)
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM live_order_logs AS log
            WHERE log.status IN ({status_placeholders})
              AND UPPER(log.side) IN ('BUY', 'SELL', 'BID', 'ASK')
              AND NOT EXISTS (
                  SELECT 1
                  FROM live_order_logs AS newer_log
                  WHERE newer_log.order_uuid IS NOT NULL
                    AND log.order_uuid IS NOT NULL
                    AND newer_log.order_uuid = log.order_uuid
                    AND newer_log.status = log.status
                    AND (
                        newer_log.updated_at > log.updated_at
                        OR (newer_log.updated_at = log.updated_at AND newer_log.created_at > log.created_at)
                        OR (newer_log.updated_at = log.updated_at AND newer_log.created_at = log.created_at AND newer_log.id > log.id)
                    )
              )
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (*TRADE_HISTORY_STATUSES, limit),
        ).fetchall()
    return [_normalize_live_order_log(dict(row)) for row in rows]


def has_recent_live_order(market: str, side: str, seconds: int = 30) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id FROM live_order_logs
            WHERE market = ?
              AND side = ?
              AND status IN ('SUBMITTED', 'FILLED', 'PARTIALLY_FILLED')
              AND updated_at >= ?
            LIMIT 1
            """,
            (market, side, cutoff),
        ).fetchone()
    return row is not None


def get_last_live_order_time() -> str | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT updated_at FROM live_order_logs
            WHERE status IN ('SUBMITTED', 'FILLED', 'PARTIALLY_FILLED')
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).fetchone()
    return row["updated_at"] if row else None


def insert_live_mode_event(event_type: str, mode: str, message: str) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO live_mode_events (event_type, mode, message)
            VALUES (?, ?, ?)
            """,
            (event_type, mode, message),
        )
        return int(cursor.lastrowid)


def insert_live_recovery_event(event: dict) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO live_recovery_events (
                event_type, severity, exchange, market, session_id,
                request_id, order_uuid, message, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["event_type"],
                event.get("severity", "INFO"),
                event.get("exchange", "bithumb"),
                event.get("market", "KRW-BTC"),
                event.get("session_id"),
                event.get("request_id"),
                event.get("order_uuid"),
                event.get("message", ""),
                json.dumps(event.get("payload", {}), ensure_ascii=False),
                _utc_now(),
            ),
        )
        return int(cursor.lastrowid)


def load_live_recovery_events(limit: int = 100) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM live_recovery_events
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    events = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
        events.append(item)
    return events


def load_runtime_lock(lock_id: str = "auto-trading") -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM runtime_locks WHERE lock_id = ?", (lock_id,)).fetchone()
    return dict(row) if row else None


def acquire_runtime_lock(
    *,
    lock_id: str,
    instance_id: str,
    hostname: str,
    app_env: str,
    runtime_owner: str,
    ttl_seconds: int,
) -> tuple[bool, dict | None]:
    now_utc = _utc_now()
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM runtime_locks WHERE lock_id = ?", (lock_id,)).fetchone()
        current = dict(row) if row else None
        if current and current.get("status") == "RUNNING" and current.get("instance_id") != instance_id and str(current.get("expires_at") or "") > now_utc:
            return False, current
        conn.execute(
            """
            INSERT INTO runtime_locks (
                lock_id, instance_id, hostname, app_env, runtime_owner,
                status, acquired_at, expires_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'RUNNING', ?, ?, ?)
            ON CONFLICT(lock_id) DO UPDATE SET
                instance_id = excluded.instance_id,
                hostname = excluded.hostname,
                app_env = excluded.app_env,
                runtime_owner = excluded.runtime_owner,
                status = 'RUNNING',
                acquired_at = excluded.acquired_at,
                expires_at = excluded.expires_at,
                updated_at = excluded.updated_at
            """,
            (lock_id, instance_id, hostname, app_env, runtime_owner, now_utc, expires_at, now_utc),
        )
    return True, load_runtime_lock(lock_id)


def release_runtime_lock(*, lock_id: str, instance_id: str, status: str = "STOPPED") -> None:
    now_utc = _utc_now()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE runtime_locks
            SET status = ?,
                expires_at = ?,
                updated_at = ?
            WHERE lock_id = ?
              AND instance_id = ?
            """,
            (status, now_utc, now_utc, lock_id, instance_id),
        )


def upsert_risk_state(state: dict) -> int:
    now_utc = _utc_now()
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id
            FROM risk_states
            WHERE exchange = ? AND market = ? AND date_kst = ?
            """,
            (state["exchange"], state["market"], state["date_kst"]),
        ).fetchone()
        values = {
            "status": state["status"],
            "daily_realized_pnl": state.get("daily_realized_pnl", 0.0),
            "daily_unrealized_pnl": state.get("daily_unrealized_pnl", 0.0),
            "daily_total_pnl": state.get("daily_total_pnl", 0.0),
            "daily_loss_percent": state.get("daily_loss_percent", 0.0),
            "daily_order_count": state.get("daily_order_count", 0),
            "daily_entry_count": state.get("daily_entry_count", 0),
            "daily_exit_count": state.get("daily_exit_count", 0),
            "consecutive_loss_count": state.get("consecutive_loss_count", 0),
            "open_order_count": state.get("open_order_count", 0),
            "open_position_count": state.get("open_position_count", 0),
            "last_order_time_utc": state.get("last_order_time_utc"),
            "last_loss_time_utc": state.get("last_loss_time_utc"),
            "emergency_stop_enabled": 1 if state.get("emergency_stop_enabled", False) else 0,
            "balance_mismatch_detected": 1 if state.get("balance_mismatch_detected", False) else 0,
            "partial_fill_detected": 1 if state.get("partial_fill_detected", False) else 0,
            "volatility_block_enabled": 1 if state.get("volatility_block_enabled", False) else 0,
            "low_volume_block_enabled": 1 if state.get("low_volume_block_enabled", False) else 0,
            "updated_at": now_utc,
        }
        if row:
            columns = ", ".join(f"{key} = ?" for key in values)
            conn.execute(
                f"UPDATE risk_states SET {columns} WHERE id = ?",
                [*values.values(), row["id"]],
            )
            return int(row["id"])
        cursor = conn.execute(
            """
            INSERT INTO risk_states (
                exchange, market, date_kst, status, daily_realized_pnl,
                daily_unrealized_pnl, daily_total_pnl, daily_loss_percent,
                daily_order_count, daily_entry_count, daily_exit_count,
                consecutive_loss_count, open_order_count, open_position_count,
                last_order_time_utc, last_loss_time_utc, emergency_stop_enabled,
                balance_mismatch_detected, partial_fill_detected,
                volatility_block_enabled, low_volume_block_enabled,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                state["exchange"],
                state["market"],
                state["date_kst"],
                values["status"],
                values["daily_realized_pnl"],
                values["daily_unrealized_pnl"],
                values["daily_total_pnl"],
                values["daily_loss_percent"],
                values["daily_order_count"],
                values["daily_entry_count"],
                values["daily_exit_count"],
                values["consecutive_loss_count"],
                values["open_order_count"],
                values["open_position_count"],
                values["last_order_time_utc"],
                values["last_loss_time_utc"],
                values["emergency_stop_enabled"],
                values["balance_mismatch_detected"],
                values["partial_fill_detected"],
                values["volatility_block_enabled"],
                values["low_volume_block_enabled"],
                now_utc,
                now_utc,
            ),
        )
        return int(cursor.lastrowid)


def load_latest_risk_state(exchange: str = "bithumb", market: str = "KRW-BTC") -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM risk_states
            WHERE exchange = ? AND market = ?
            ORDER BY date_kst DESC, updated_at DESC, id DESC
            LIMIT 1
            """,
            (exchange, market),
        ).fetchone()
    if row is None:
        return None
    return _normalize_risk_state(dict(row))


def insert_risk_log(log: dict) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO risk_logs (
                exchange, market, session_id, position_id, order_candidate_id,
                order_log_id, risk_level, allowed, block_code, block_reason,
                checks_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                log["exchange"],
                log["market"],
                log.get("session_id"),
                log.get("position_id"),
                log.get("order_candidate_id"),
                log.get("order_log_id"),
                log.get("risk_level", "LOW"),
                1 if log.get("allowed", False) else 0,
                log.get("block_code"),
                log.get("block_reason"),
                json.dumps(log.get("checks", {}), ensure_ascii=False),
                _utc_now(),
            ),
        )
        return int(cursor.lastrowid)


def load_risk_logs(limit: int = 100, exchange: str = "bithumb", market: str = "KRW-BTC") -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM risk_logs
            WHERE exchange = ? AND market = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (exchange, market, limit),
        ).fetchall()
    logs = []
    for row in rows:
        item = dict(row)
        item["allowed"] = bool(item.get("allowed"))
        item["checks"] = json.loads(item.pop("checks_json") or "{}")
        logs.append(item)
    return logs


def load_risk_log(log_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM risk_logs WHERE id = ?", (log_id,)).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["allowed"] = bool(item.get("allowed"))
    item["checks"] = json.loads(item.pop("checks_json") or "{}")
    return item


def update_risk_log_resolution(log_id: int, action: str) -> dict | None:
    normalized = action.upper()
    if normalized not in {"READ", "IGNORE", "RETRY"}:
        normalized = "READ"
    read_status = "IGNORED" if normalized == "IGNORE" else "READ"
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM risk_logs WHERE id = ?", (log_id,)).fetchone()
        if row is None:
            return None
        conn.execute(
            """
            UPDATE risk_logs
            SET read_status = ?,
                resolved_at = ?,
                resolution_action = ?
            WHERE id = ?
            """,
            (read_status, _utc_now(), normalized, log_id),
        )
        updated = conn.execute("SELECT * FROM risk_logs WHERE id = ?", (log_id,)).fetchone()
    item = dict(updated)
    item["allowed"] = bool(item.get("allowed"))
    item["checks"] = json.loads(item.pop("checks_json") or "{}")
    return item


def load_app_settings() -> dict:
    with get_connection() as conn:
        _ensure_app_settings_table(conn)
        rows = conn.execute("SELECT key, value_json FROM app_settings").fetchall()
    settings = {}
    for row in rows:
        try:
            settings[row["key"]] = json.loads(row["value_json"])
        except json.JSONDecodeError:
            settings[row["key"]] = None
    return settings


def update_app_settings(settings: dict) -> dict:
    safe_settings = _sanitize_app_settings(settings)
    now_utc = _utc_now()
    with get_connection() as conn:
        _ensure_app_settings_table(conn)
        for key, value in safe_settings.items():
            conn.execute(
                """
                INSERT INTO app_settings (key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (key, json.dumps(value, ensure_ascii=False), now_utc),
            )
    return load_app_settings()


def insert_aggression_preset_log(payload: dict) -> dict:
    now_utc = _utc_now()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO aggression_preset_logs (
                preset_name, previous_preset, previous_settings_json,
                applied_settings_json, before_summary_json, after_summary_json,
                safety_guards_json, requested_by, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(payload.get("preset_name") or ""),
                payload.get("previous_preset"),
                json.dumps(payload.get("previous_settings") or {}, ensure_ascii=False),
                json.dumps(payload.get("applied_settings") or {}, ensure_ascii=False),
                json.dumps(payload.get("before_summary") or {}, ensure_ascii=False),
                json.dumps(payload.get("after_summary") or {}, ensure_ascii=False),
                json.dumps(payload.get("safety_guards") or {}, ensure_ascii=False),
                str(payload.get("requested_by") or "admin"),
                str(payload.get("reason") or ""),
                now_utc,
            ),
        )
        row_id = int(cursor.lastrowid)
    row = load_aggression_preset_log(row_id)
    return row or {"id": row_id, "preset_name": payload.get("preset_name"), "created_at": now_utc}


def load_aggression_preset_log(log_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM aggression_preset_logs WHERE id = ?", (log_id,)).fetchone()
    return _normalize_aggression_preset_log(dict(row)) if row else None


def load_aggression_preset_logs(limit: int = 50) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM aggression_preset_logs
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (max(min(int(limit), 200), 1),),
        ).fetchall()
    return [_normalize_aggression_preset_log(dict(row)) for row in rows]


def _normalize_aggression_preset_log(row: dict) -> dict:
    for source, target in (
        ("previous_settings_json", "previous_settings"),
        ("applied_settings_json", "applied_settings"),
        ("before_summary_json", "before_summary"),
        ("after_summary_json", "after_summary"),
        ("safety_guards_json", "safety_guards"),
    ):
        try:
            row[target] = json.loads(row.pop(source) or "{}")
        except json.JSONDecodeError:
            row[target] = {}
    return row


def _ensure_app_settings_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def _sanitize_app_settings(settings: dict) -> dict:
    blocked_fragments = ("secret", "access_key", "authorization", "jwt", "token", "api_key")
    safe = {}
    for key, value in settings.items():
        lowered = str(key).lower()
        if any(fragment in lowered for fragment in blocked_fragments):
            continue
        safe[key] = value
    return safe


def _normalize_risk_state(row: dict) -> dict:
    for key in (
        "emergency_stop_enabled",
        "balance_mismatch_detected",
        "partial_fill_detected",
        "volatility_block_enabled",
        "low_volume_block_enabled",
    ):
        row[key] = bool(row.get(key))
    return row


def _normalize_live_order_log(row: dict) -> dict:
    row["exchange"] = row.get("exchange") or "upbit"
    row["order_preview_payload"] = json.loads(row.get("order_preview_payload") or "{}")
    row["exchange_request_payload_masked"] = json.loads(row.get("exchange_request_payload_masked") or "{}")
    row["exchange_response_payload"] = json.loads(row.get("exchange_response_payload") or "{}")
    row["is_auto_exit"] = bool(row.get("is_auto_exit"))
    row["manual_confirmed"] = bool(row.get("manual_confirmed"))
    return row


def create_live_strategy_session(session: dict) -> int:
    now_utc = _utc_now()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE live_strategy_sessions
            SET status = 'STOPPED',
                auto_enabled = 0,
                stopped_at = ?,
                updated_at = ?
            WHERE status IN ('READY', 'RUNNING', 'PAUSED')
              AND exchange = ?
              AND market = ?
              AND candidate_strategy_id = ?
            """,
            (
                now_utc,
                now_utc,
                session["exchange"],
                session["market"],
                int(session["candidate_strategy_id"]),
            ),
        )
        cursor = conn.execute(
            """
            INSERT INTO live_strategy_sessions (
                exchange, market, candidate_strategy_id, strategy_name, strategy_parameters,
                status, auto_enabled, initial_balance_krw, max_order_krw,
                max_orders_per_day, orders_created_today, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session["exchange"],
                session["market"],
                session["candidate_strategy_id"],
                session["strategy_name"],
                json.dumps(session.get("strategy_parameters", {}), ensure_ascii=False),
                session.get("status", "READY"),
                1 if session.get("auto_enabled", False) else 0,
                session.get("initial_balance_krw", 0.0),
                session["max_order_krw"],
                session["max_orders_per_day"],
                session.get("orders_created_today", 0),
                now_utc,
                now_utc,
            ),
        )
        return int(cursor.lastrowid)


def load_live_strategy_session(session_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM live_strategy_sessions WHERE id = ?", (session_id,)).fetchone()
    return _normalize_live_strategy_session(dict(row)) if row else None


def load_latest_live_strategy_session() -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM live_strategy_sessions
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
    return _normalize_live_strategy_session(dict(row)) if row else None


def load_running_live_strategy_sessions() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM live_strategy_sessions
            WHERE status IN ('READY', 'RUNNING')
              AND auto_enabled = 1
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()
    return [_normalize_live_strategy_session(dict(row)) for row in rows]


def pause_running_live_strategy_sessions_on_startup() -> int:
    now_utc = _utc_now()
    with get_connection() as conn:
        before = conn.total_changes
        conn.execute(
            """
            UPDATE live_strategy_sessions
            SET status = 'LIVE_PAUSED',
                auto_enabled = 0,
                last_risk_result = 'SERVER_RESTART_LIVE_PAUSED',
                updated_at = ?
            WHERE status IN ('READY', 'RUNNING')
            """,
            (now_utc,),
        )
        return conn.total_changes - before


def update_live_strategy_session(session_id: int, updates: dict) -> None:
    allowed = {
        "candidate_strategy_id",
        "market",
        "strategy_name",
        "strategy_parameters",
        "status",
        "auto_enabled",
        "orders_created_today",
        "current_open_order_uuid",
        "current_position_id",
        "last_signal",
        "last_signal_time_utc",
        "last_risk_result",
        "last_order_status",
        "last_order_time_utc",
        "last_processed_candle_time_utc",
        "stopped_at",
    }
    values = {key: value for key, value in updates.items() if key in allowed}
    if not values:
        return
    if "strategy_parameters" in values:
        values["strategy_parameters"] = json.dumps(values["strategy_parameters"] or {}, ensure_ascii=False)
    values["updated_at"] = _utc_now()
    columns = ", ".join(f"{key} = ?" for key in values)
    params = list(values.values()) + [session_id]
    with get_connection() as conn:
        conn.execute(f"UPDATE live_strategy_sessions SET {columns} WHERE id = ?", params)


def insert_live_signal_log(log: dict) -> int | None:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO live_signal_logs (
                session_id, exchange, market, candidate_strategy_id, strategy_name,
                signal, confidence, reason, candle_time_utc, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                log["session_id"],
                log["exchange"],
                log["market"],
                log["candidate_strategy_id"],
                log["strategy_name"],
                log["signal"],
                log.get("confidence", 1.0),
                log.get("reason", ""),
                log["candle_time_utc"],
                _utc_now(),
            ),
        )
        return int(cursor.lastrowid) if cursor.lastrowid else None


def load_live_signal_logs(session_id: int | None = None, limit: int = 100) -> list[dict]:
    params: list[object] = []
    where = ""
    if session_id is not None:
        where = "WHERE session_id = ?"
        params.append(session_id)
    params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM live_signal_logs
            {where}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def create_live_position(position: dict) -> int:
    now_utc = _utc_now()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO live_positions (
                session_id, exchange, market, candidate_strategy_id, strategy_name,
                status, entry_order_uuid, exit_order_uuid, entry_price, entry_volume,
                entry_amount_krw, current_price, unrealized_pnl, realized_pnl,
                stop_loss_price, take_profit_price, highest_price_since_entry,
                trailing_stop_price, trailing_stop_pct, last_trailing_update_at,
                scale_in_count, last_scale_in_at, opened_at, closed_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position["session_id"],
                position["exchange"],
                position["market"],
                position["candidate_strategy_id"],
                position["strategy_name"],
                position.get("status", "OPEN"),
                position.get("entry_order_uuid"),
                position.get("exit_order_uuid"),
                position.get("entry_price", 0.0),
                position.get("entry_volume", 0.0),
                position.get("entry_amount_krw", 0.0),
                position.get("current_price", position.get("entry_price", 0.0)),
                position.get("unrealized_pnl", 0.0),
                position.get("realized_pnl", 0.0),
                position.get("stop_loss_price", 0.0),
                position.get("take_profit_price", 0.0),
                position.get("highest_price_since_entry"),
                position.get("trailing_stop_price"),
                position.get("trailing_stop_pct"),
                position.get("last_trailing_update_at"),
                position.get("scale_in_count", 0),
                position.get("last_scale_in_at"),
                position.get("opened_at", now_utc),
                position.get("closed_at"),
                now_utc,
                now_utc,
            ),
        )
        return int(cursor.lastrowid)


def load_open_live_position(session_id: int | None = None, exchange: str = "bithumb", market: str = "KRW-BTC") -> dict | None:
    params: list[object] = [exchange, market]
    session_filter = ""
    if session_id is not None:
        session_filter = "AND session_id = ?"
        params.append(session_id)
    with get_connection() as conn:
        row = conn.execute(
            f"""
            SELECT * FROM live_positions
            WHERE exchange = ?
              AND market = ?
              AND status IN ('OPEN', 'EXIT_CANDIDATE', 'EXIT_PENDING', 'CLOSING', 'MANUAL_REVIEW_REQUIRED')
              {session_filter}
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
    return dict(row) if row else None


def load_live_position_by_entry_order_uuid(exchange: str, market: str, entry_order_uuid: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM live_positions
            WHERE exchange = ?
              AND market = ?
              AND entry_order_uuid = ?
              AND status IN ('OPEN', 'EXIT_CANDIDATE', 'EXIT_PENDING', 'CLOSING', 'MANUAL_REVIEW_REQUIRED')
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (exchange, market, entry_order_uuid),
        ).fetchone()
    return dict(row) if row else None


def load_live_position(position_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM live_positions WHERE id = ?", (position_id,)).fetchone()
    return dict(row) if row else None


def load_open_live_positions(exchange: str = "bithumb", market: str = "KRW-BTC") -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM live_positions
            WHERE exchange = ?
              AND market = ?
              AND status IN ('OPEN', 'EXIT_CANDIDATE', 'EXIT_PENDING', 'CLOSING', 'MANUAL_REVIEW_REQUIRED')
            ORDER BY created_at DESC, id DESC
            """,
            (exchange, market),
        ).fetchall()
    return [dict(row) for row in rows]


def load_open_live_positions_for_exchange(exchange: str = "bithumb") -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM live_positions
            WHERE exchange = ?
              AND status IN ('OPEN', 'EXIT_CANDIDATE', 'EXIT_PENDING', 'CLOSING', 'MANUAL_REVIEW_REQUIRED')
            ORDER BY created_at DESC, id DESC
            """,
            (exchange,),
        ).fetchall()
    return [dict(row) for row in rows]


def ensure_position_slots(max_slots: int = 5, exchange: str = "bithumb") -> list[dict]:
    max_slots = max(1, int(max_slots))
    now_utc = _utc_now()
    with get_connection() as conn:
        for slot_number in range(1, max_slots + 1):
            conn.execute(
                """
                INSERT OR IGNORE INTO position_slots (
                    slot_number, status, exchange, created_at, updated_at
                ) VALUES (?, 'EMPTY', ?, ?, ?)
                """,
                (slot_number, exchange, now_utc, now_utc),
            )
        rows = conn.execute(
            """
            SELECT *
            FROM position_slots
            WHERE slot_number <= ?
            ORDER BY slot_number ASC
            """,
            (max_slots,),
        ).fetchall()
    return [dict(row) for row in rows]


def reconcile_position_slots(max_slots: int = 5, exchange: str = "bithumb") -> list[dict]:
    ensure_position_slots(max_slots, exchange)
    now_utc = _utc_now()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE order_reservations
            SET status = 'EXPIRED',
                updated_at = ?
            WHERE exchange = ?
              AND status IN ('RESERVED', 'ORDER_SUBMITTED')
              AND expires_at IS NOT NULL
              AND expires_at <= ?
            """,
            (now_utc, exchange, now_utc),
        )
        conn.execute(
            """
            UPDATE position_slots
            SET status = 'EMPTY',
                market = NULL,
                candidate_strategy_id = NULL,
                live_position_id = NULL,
                live_strategy_session_id = NULL,
                entry_order_uuid = NULL,
                exit_order_uuid = NULL,
                allocated_krw = 0,
                reserved_krw = 0,
                current_value_krw = 0,
                unrealized_pnl = 0,
                realized_pnl = 0,
                entry_reason = NULL,
                exit_reason = NULL,
                opened_at = NULL,
                closed_at = ?,
                updated_at = ?
            WHERE exchange = ?
              AND status = 'RESERVED'
              AND NOT EXISTS (
                  SELECT 1
                  FROM order_reservations r
                  WHERE r.slot_id = position_slots.id
                    AND r.status IN ('RESERVED', 'ORDER_SUBMITTED')
                    AND (r.expires_at IS NULL OR r.expires_at > ?)
              )
            """,
            (now_utc, now_utc, exchange, now_utc),
        )
        positions = conn.execute(
            """
            SELECT *
            FROM live_positions
            WHERE exchange = ?
              AND status IN ('OPEN', 'EXIT_CANDIDATE', 'EXIT_PENDING', 'CLOSING', 'MANUAL_REVIEW_REQUIRED')
            ORDER BY opened_at ASC, id ASC
            """,
            (exchange,),
        ).fetchall()
        active_position_ids = {int(row["id"]) for row in positions}
        conn.execute(
            """
            UPDATE position_slots
            SET status = 'EMPTY',
                market = NULL,
                candidate_strategy_id = NULL,
                live_position_id = NULL,
                live_strategy_session_id = NULL,
                entry_order_uuid = NULL,
                exit_order_uuid = NULL,
                allocated_krw = 0,
                reserved_krw = 0,
                current_value_krw = 0,
                unrealized_pnl = 0,
                realized_pnl = 0,
                entry_reason = NULL,
                exit_reason = NULL,
                opened_at = NULL,
                closed_at = ?,
                updated_at = ?
            WHERE status NOT IN ('RESERVED', 'ENTERING')
              AND (live_position_id IS NOT NULL OR status != 'EMPTY')
              AND (live_position_id IS NULL OR live_position_id NOT IN (
                  SELECT id FROM live_positions
                  WHERE exchange = ?
                    AND status IN ('OPEN', 'EXIT_CANDIDATE', 'EXIT_PENDING', 'CLOSING', 'MANUAL_REVIEW_REQUIRED')
              ))
            """,
            (now_utc, now_utc, exchange),
        )
        for position_row in positions:
            position = dict(position_row)
            position_id = int(position["id"])
            current_value = float(position["current_price"] or 0.0) * float(position["entry_volume"] or 0.0)
            existing = conn.execute(
                "SELECT * FROM position_slots WHERE live_position_id = ? LIMIT 1",
                (position_id,),
            ).fetchone()
            if existing is None:
                existing = conn.execute(
                    """
                    SELECT *
                    FROM position_slots
                    WHERE slot_number <= ?
                      AND status = 'EMPTY'
                    ORDER BY slot_number ASC
                    LIMIT 1
                    """,
                    (max_slots,),
                ).fetchone()
            if existing is None:
                continue
            conn.execute(
                """
                UPDATE position_slots
                SET status = ?,
                    exchange = ?,
                    market = ?,
                    candidate_strategy_id = ?,
                    live_position_id = ?,
                    live_strategy_session_id = ?,
                    entry_order_uuid = ?,
                    exit_order_uuid = ?,
                    allocated_krw = ?,
                    reserved_krw = 0,
                    current_value_krw = ?,
                    unrealized_pnl = ?,
                    realized_pnl = ?,
                    opened_at = ?,
                    closed_at = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    position["status"],
                    position["exchange"],
                    position["market"],
                    int(position["candidate_strategy_id"]),
                    position_id,
                    int(position["session_id"]),
                    position.get("entry_order_uuid"),
                    position.get("exit_order_uuid"),
                    float(position["entry_amount_krw"] or current_value),
                    current_value,
                    float(position["unrealized_pnl"] or 0.0),
                    float(position["realized_pnl"] or 0.0),
                    position.get("opened_at"),
                    now_utc,
                    int(existing["id"]),
                ),
            )
        rows = conn.execute(
            """
            SELECT *
            FROM position_slots
            WHERE slot_number <= ?
            ORDER BY slot_number ASC
            """,
            (max_slots,),
        ).fetchall()
    return [dict(row) for row in rows]


def load_position_slots(max_slots: int = 5, exchange: str = "bithumb") -> list[dict]:
    return reconcile_position_slots(max_slots=max_slots, exchange=exchange)


def reserve_position_slot(
    *,
    slot_id: int,
    exchange: str,
    market: str,
    candidate_strategy_id: int,
    live_strategy_session_id: int | None,
    amount_krw: float,
    reason: str,
) -> dict:
    now_utc = _utc_now()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE position_slots
            SET status = 'RESERVED',
                exchange = ?,
                market = ?,
                candidate_strategy_id = ?,
                live_strategy_session_id = ?,
                allocated_krw = ?,
                reserved_krw = ?,
                entry_reason = ?,
                opened_at = ?,
                closed_at = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (
                exchange,
                market,
                candidate_strategy_id,
                live_strategy_session_id,
                amount_krw,
                amount_krw,
                reason,
                now_utc,
                now_utc,
                slot_id,
            ),
        )
        row = conn.execute("SELECT * FROM position_slots WHERE id = ?", (slot_id,)).fetchone()
    return dict(row) if row else {}


def create_order_reservation(reservation: dict) -> int:
    now_utc = _utc_now()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO order_reservations (
                request_id, exchange, market, candidate_strategy_id, slot_id,
                amount_krw, status, expires_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reservation["request_id"],
                reservation["exchange"],
                reservation["market"],
                int(reservation["candidate_strategy_id"]),
                reservation.get("slot_id"),
                float(reservation["amount_krw"]),
                reservation.get("status", "RESERVED"),
                reservation.get("expires_at"),
                now_utc,
                now_utc,
            ),
        )
        return int(cursor.lastrowid)


def load_active_order_reservations(exchange: str = "bithumb") -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM order_reservations
            WHERE exchange = ?
              AND status IN ('RESERVED', 'ORDER_SUBMITTED')
              AND (expires_at IS NULL OR expires_at > ?)
            ORDER BY created_at ASC, id ASC
            """,
            (exchange, _utc_now()),
        ).fetchall()
    return [dict(row) for row in rows]


def update_order_reservation_status(
    *,
    candidate_strategy_id: int | None = None,
    market: str | None = None,
    status: str,
    previous_statuses: list[str] | None = None,
) -> int:
    previous_statuses = previous_statuses or ["RESERVED", "ORDER_SUBMITTED"]
    filters = [f"status IN ({', '.join('?' for _ in previous_statuses)})"]
    where_params: list[object] = [*previous_statuses]
    if candidate_strategy_id is not None:
        filters.append("candidate_strategy_id = ?")
        where_params.append(candidate_strategy_id)
    if market is not None:
        filters.append("market = ?")
        where_params.append(market)
    with get_connection() as conn:
        before = conn.total_changes
        conn.execute(
            f"""
            UPDATE order_reservations
            SET status = ?,
                updated_at = ?
            WHERE {' AND '.join(filters)}
            """,
            [status, _utc_now(), *where_params],
        )
        return conn.total_changes - before


def create_capital_allocation_run(payload: dict) -> int:
    now_utc = _utc_now()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO capital_allocation_runs (
                reason, status, max_total_exposure_krw, current_exposure_krw,
                pending_reserved_krw, available_krw_balance, remaining_exposure_krw,
                empty_slot_count, candidate_count, accepted_count, blocked_count,
                started_at, finished_at, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.get("reason", "SCHEDULED"),
                payload.get("status", "RUNNING"),
                float(payload.get("max_total_exposure_krw") or 0.0),
                float(payload.get("current_exposure_krw") or 0.0),
                float(payload.get("pending_reserved_krw") or 0.0),
                payload.get("available_krw_balance"),
                float(payload.get("remaining_exposure_krw") or 0.0),
                int(payload.get("empty_slot_count") or 0),
                int(payload.get("candidate_count") or 0),
                int(payload.get("accepted_count") or 0),
                int(payload.get("blocked_count") or 0),
                payload.get("started_at", now_utc),
                payload.get("finished_at"),
                payload.get("error"),
            ),
        )
        return int(cursor.lastrowid)


def finish_capital_allocation_run(run_id: int, updates: dict) -> dict | None:
    allowed = {
        "status",
        "max_total_exposure_krw",
        "current_exposure_krw",
        "pending_reserved_krw",
        "available_krw_balance",
        "remaining_exposure_krw",
        "empty_slot_count",
        "candidate_count",
        "accepted_count",
        "blocked_count",
        "finished_at",
        "error",
    }
    values = {key: updates[key] for key in allowed if key in updates}
    values.setdefault("finished_at", _utc_now())
    assignments = ", ".join(f"{key} = ?" for key in values)
    with get_connection() as conn:
        conn.execute(
            f"UPDATE capital_allocation_runs SET {assignments} WHERE id = ?",
            [*values.values(), run_id],
        )
        row = conn.execute("SELECT * FROM capital_allocation_runs WHERE id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def insert_capital_allocation_decision(decision: dict) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO capital_allocation_decisions (
                run_id, candidate_strategy_id, market, strategy, allocation_score,
                desired_order_krw, approved_order_krw, blocked_reason, fee_rate,
                estimated_fee_krw, estimated_slippage_krw, expected_edge_pct,
                required_edge_pct, decision
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(decision["run_id"]),
                decision.get("candidate_strategy_id"),
                decision["market"],
                decision["strategy"],
                float(decision.get("allocation_score") or 0.0),
                float(decision.get("desired_order_krw") or 0.0),
                float(decision.get("approved_order_krw") or 0.0),
                decision.get("blocked_reason"),
                float(decision.get("fee_rate") or 0.0),
                float(decision.get("estimated_fee_krw") or 0.0),
                float(decision.get("estimated_slippage_krw") or 0.0),
                float(decision.get("expected_edge_pct") or 0.0),
                float(decision.get("required_edge_pct") or 0.0),
                decision.get("decision", "BLOCKED"),
            ),
        )
        return int(cursor.lastrowid)


def enqueue_next_entry(candidate: dict, *, allocation_score: float, blocked_reason: str, ttl_minutes: int = 360) -> int | None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    expires_at = (now + timedelta(minutes=ttl_minutes)).isoformat().replace("+00:00", "Z")
    now_utc = now.isoformat().replace("+00:00", "Z")
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO next_entry_queue (
                candidate_strategy_id, market, strategy, unit, score,
                allocation_score, status, blocked_reason, queued_at, expires_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'QUEUED', ?, ?, ?, ?)
            ON CONFLICT(candidate_strategy_id, status) DO UPDATE SET
                market = excluded.market,
                strategy = excluded.strategy,
                unit = excluded.unit,
                score = excluded.score,
                allocation_score = excluded.allocation_score,
                blocked_reason = excluded.blocked_reason,
                expires_at = excluded.expires_at,
                updated_at = excluded.updated_at
            """,
            (
                int(candidate["id"]),
                candidate["market"],
                candidate["strategy"],
                int(candidate.get("unit") or 0),
                float(candidate.get("score") or 0.0),
                allocation_score,
                blocked_reason,
                now_utc,
                expires_at,
                now_utc,
            ),
        )
        row = conn.execute(
            """
            SELECT id FROM next_entry_queue
            WHERE candidate_strategy_id = ?
              AND status = 'QUEUED'
            LIMIT 1
            """,
            (int(candidate["id"]),),
        ).fetchone()
        return int(row["id"]) if row else None


def load_next_entry_queue(limit: int = 20, statuses: list[str] | None = None) -> list[dict]:
    statuses = statuses or ["QUEUED", "BLOCKED"]
    placeholders = ",".join("?" for _ in statuses)
    params: list[object] = [*statuses, _utc_now(), limit]
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM next_entry_queue
            WHERE status IN ({placeholders})
              AND (expires_at IS NULL OR expires_at > ?)
            ORDER BY allocation_score DESC, queued_at ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def update_next_entry_status(candidate_strategy_id: int, status: str, blocked_reason: str | None = None) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE next_entry_queue
            SET status = ?,
                blocked_reason = COALESCE(?, blocked_reason),
                updated_at = ?
            WHERE candidate_strategy_id = ?
              AND status = 'QUEUED'
            """,
            (status, blocked_reason, _utc_now(), candidate_strategy_id),
        )


def load_open_live_position_for_strategy(exchange: str, market: str, candidate_strategy_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM live_positions
            WHERE exchange = ?
              AND market = ?
              AND candidate_strategy_id = ?
              AND status IN ('OPEN', 'EXIT_CANDIDATE', 'EXIT_PENDING', 'CLOSING', 'MANUAL_REVIEW_REQUIRED')
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (exchange, market, candidate_strategy_id),
        ).fetchone()
    return dict(row) if row else None


def has_open_live_position_for_strategy(exchange: str, market: str, candidate_strategy_id: int) -> bool:
    return load_open_live_position_for_strategy(exchange, market, candidate_strategy_id) is not None


def update_live_position(position_id: int, updates: dict) -> None:
    allowed = {
        "status",
        "exit_order_uuid",
        "entry_price",
        "current_price",
        "entry_volume",
        "entry_amount_krw",
        "unrealized_pnl",
        "realized_pnl",
        "highest_price_since_entry",
        "trailing_stop_price",
        "trailing_stop_pct",
        "last_trailing_update_at",
        "scale_in_count",
        "last_scale_in_at",
        "closed_at",
    }
    values = {key: value for key, value in updates.items() if key in allowed}
    if not values:
        return
    values["updated_at"] = _utc_now()
    columns = ", ".join(f"{key} = ?" for key in values)
    params = list(values.values()) + [position_id]
    with get_connection() as conn:
        conn.execute(f"UPDATE live_positions SET {columns} WHERE id = ?", params)


def load_position_fill_event(order_uuid: str, fill_type: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM position_fill_events
            WHERE order_uuid = ?
              AND fill_type = ?
            LIMIT 1
            """,
            (order_uuid, fill_type),
        ).fetchone()
    return dict(row) if row else None


def insert_position_fill_event(event: dict) -> bool:
    now_utc = _utc_now()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO position_fill_events (
                order_uuid, position_id, fill_type, source, order_log_id, request_id,
                applied_volume, applied_amount_krw, applied_fee, applied_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["order_uuid"],
                int(event["position_id"]),
                event["fill_type"],
                event.get("source", ""),
                event.get("order_log_id"),
                event.get("request_id"),
                event.get("applied_volume", 0.0),
                event.get("applied_amount_krw", 0.0),
                event.get("applied_fee", 0.0),
                event.get("applied_at", now_utc),
                now_utc,
            ),
        )
        return cursor.rowcount > 0


def upsert_rebalance_delta_accumulator(
    *,
    session_id: int,
    candidate_strategy_id: int | None,
    exchange: str,
    market: str,
    side: str,
    delta_krw: float,
    qty: float = 0.0,
    metadata: dict | None = None,
    max_accumulated_krw: float | None = None,
) -> dict:
    now_utc = _utc_now()
    normalized_side = side.upper()
    metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
    delta_abs = abs(delta_krw)
    qty_abs = abs(qty)
    max_accumulated = None if max_accumulated_krw is None else max(float(max_accumulated_krw), 0.0)
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM rebalance_delta_accumulators
            WHERE session_id = ?
              AND COALESCE(candidate_strategy_id, -1) = COALESCE(?, -1)
              AND exchange = ?
              AND market = ?
              AND side = ?
              AND status = 'ACCUMULATING'
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (session_id, candidate_strategy_id, exchange, market, normalized_side),
        ).fetchone()
        if row:
            current_delta = float(row["accumulated_delta_krw"] or 0.0)
            new_delta = current_delta + delta_abs
            capped = False
            if max_accumulated is not None and new_delta > max_accumulated:
                new_delta = max_accumulated
                capped = True
            effective_delta = max(new_delta - current_delta, 0.0)
            effective_qty = qty_abs * (effective_delta / delta_abs) if delta_abs > 0 else 0.0
            conn.execute(
                """
                UPDATE rebalance_delta_accumulators
                SET accumulated_delta_krw = ?,
                    accumulated_qty = accumulated_qty + ?,
                    metadata_json = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (new_delta, effective_qty, metadata_json, now_utc, row["id"]),
            )
            updated = conn.execute("SELECT * FROM rebalance_delta_accumulators WHERE id = ?", (row["id"],)).fetchone()
            result = dict(updated)
            result["_capped"] = capped
            result["_effective_delta_krw"] = effective_delta
            return result
        initial_delta = delta_abs
        capped = False
        if max_accumulated is not None and initial_delta > max_accumulated:
            initial_delta = max_accumulated
            capped = True
        effective_qty = qty_abs * (initial_delta / delta_abs) if delta_abs > 0 else 0.0
        cursor = conn.execute(
            """
            INSERT INTO rebalance_delta_accumulators (
                session_id, candidate_strategy_id, exchange, market, side,
                accumulated_delta_krw, accumulated_qty, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, candidate_strategy_id, exchange, market, normalized_side, initial_delta, effective_qty, metadata_json, now_utc, now_utc),
        )
        created = conn.execute("SELECT * FROM rebalance_delta_accumulators WHERE id = ?", (cursor.lastrowid,)).fetchone()
        result = dict(created)
        result["_capped"] = capped
        result["_effective_delta_krw"] = initial_delta
        return result


def mark_rebalance_delta_accumulator(accumulator_id: int, status: str, metadata: dict | None = None) -> None:
    now_utc = _utc_now()
    values: dict[str, object] = {"status": status, "updated_at": now_utc}
    if status in {"PROMOTED", "DISCARDED", "STALE", "DISCARDED_DUST"}:
        values["flushed_at"] = now_utc
    if metadata is not None:
        values["metadata_json"] = json.dumps(metadata, ensure_ascii=False)
    columns = ", ".join(f"{key} = ?" for key in values)
    params = [*values.values(), accumulator_id]
    with get_connection() as conn:
        conn.execute(f"UPDATE rebalance_delta_accumulators SET {columns} WHERE id = ?", params)


def mark_rebalance_delta_accumulators(
    *,
    session_id: int,
    candidate_strategy_id: int | None,
    exchange: str,
    market: str,
    side: str,
    status: str,
    metadata: dict | None = None,
    previous_status: str = "ACCUMULATING",
) -> int:
    now_utc = _utc_now()
    normalized_side = side.upper()
    values: dict[str, object] = {"status": status, "updated_at": now_utc}
    if status in {"PROMOTED", "DISCARDED", "STALE", "DISCARDED_DUST"}:
        values["flushed_at"] = now_utc
    if metadata is not None:
        values["metadata_json"] = json.dumps(metadata, ensure_ascii=False)
    columns = ", ".join(f"{key} = ?" for key in values)
    params = [
        *values.values(),
        session_id,
        candidate_strategy_id,
        exchange,
        market,
        normalized_side,
        previous_status,
    ]
    with get_connection() as conn:
        before = conn.total_changes
        conn.execute(
            f"""
            UPDATE rebalance_delta_accumulators
            SET {columns}
            WHERE session_id = ?
              AND COALESCE(candidate_strategy_id, -1) = COALESCE(?, -1)
              AND exchange = ?
              AND market = ?
              AND side = ?
              AND status = ?
            """,
            params,
        )
        return conn.total_changes - before


def create_exit_candidate(candidate: dict) -> int:
    now_utc = _utc_now()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO exit_candidates (
                position_id, session_id, exchange, market, candidate_strategy_id,
                strategy_name, reason, status, entry_price, current_price,
                target_exit_price, volume, expected_amount_krw, expected_fee,
                expected_pnl, risk_result, signal_time_utc, candle_time_utc,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate["position_id"],
                candidate["session_id"],
                candidate["exchange"],
                candidate["market"],
                candidate["candidate_strategy_id"],
                candidate["strategy_name"],
                candidate["reason"],
                candidate.get("status", "PENDING"),
                candidate["entry_price"],
                candidate["current_price"],
                candidate["target_exit_price"],
                candidate["volume"],
                candidate["expected_amount_krw"],
                candidate["expected_fee"],
                candidate["expected_pnl"],
                candidate.get("risk_result", "EXIT_CANDIDATE"),
                candidate.get("signal_time_utc"),
                candidate.get("candle_time_utc"),
                now_utc,
                now_utc,
            ),
        )
        return int(cursor.lastrowid)


def load_exit_candidate(candidate_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM exit_candidates WHERE id = ?", (candidate_id,)).fetchone()
    return dict(row) if row else None


def load_latest_exit_candidate(position_id: int | None = None, session_id: int | None = None) -> dict | None:
    clauses = []
    params: list[object] = []
    if position_id is not None:
        clauses.append("position_id = ?")
        params.append(position_id)
    if session_id is not None:
        clauses.append("session_id = ?")
        params.append(session_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_connection() as conn:
        row = conn.execute(
            f"""
            SELECT *
            FROM exit_candidates
            {where}
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
    return dict(row) if row else None


def load_active_exit_candidate(position_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM exit_candidates
            WHERE position_id = ?
              AND status IN ('PENDING', 'APPROVED', 'SUBMITTED')
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (position_id,),
        ).fetchone()
    return dict(row) if row else None


def update_exit_candidate(candidate_id: int, updates: dict) -> None:
    allowed = {
        "status",
        "current_price",
        "target_exit_price",
        "expected_amount_krw",
        "expected_fee",
        "expected_pnl",
        "risk_result",
    }
    values = {key: value for key, value in updates.items() if key in allowed}
    if not values:
        return
    values["updated_at"] = _utc_now()
    columns = ", ".join(f"{key} = ?" for key in values)
    params = list(values.values()) + [candidate_id]
    with get_connection() as conn:
        conn.execute(f"UPDATE exit_candidates SET {columns} WHERE id = ?", params)


def has_open_exit_order(position_id: int) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id
            FROM live_order_logs
            WHERE position_id = ?
              AND order_purpose = 'EXIT'
              AND status IN ('SUBMITTED', 'WAITING', 'PARTIALLY_FILLED')
              AND request_id NOT LIKE '%-submitted%'
              AND request_id NOT LIKE '%-waiting-%'
              AND request_id NOT LIKE '%-partial%'
              AND request_id NOT LIKE '%-canceled-%'
              AND request_id NOT LIKE '%-filled-%'
              AND request_id NOT LIKE '%-failed-%'
            LIMIT 1
            """,
            (position_id,),
        ).fetchone()
    return row is not None


def count_exit_retries(exit_candidate_id: int) -> int:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM live_order_logs
            WHERE exit_candidate_id = ?
              AND order_purpose = 'EXIT'
              AND status IN ('SUBMITTED', 'WAITING', 'PARTIALLY_FILLED', 'FILLED', 'CANCELED', 'FAILED')
              AND request_id NOT LIKE '%-submitted%'
              AND request_id NOT LIKE '%-waiting-%'
              AND request_id NOT LIKE '%-partial%'
              AND request_id NOT LIKE '%-canceled-%'
              AND request_id NOT LIKE '%-filled-%'
              AND request_id NOT LIKE '%-failed-%'
            """,
            (exit_candidate_id,),
        ).fetchone()
    return int(row["count"] if row else 0)


def count_live_strategy_orders_today(exchange: str, market: str) -> int:
    kst = timezone(timedelta(hours=9))
    now_kst = datetime.now(kst)
    start_utc = now_kst.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    end_utc = (now_kst.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM live_order_logs
            WHERE exchange = ?
              AND market = ?
              AND session_id IS NOT NULL
              AND candidate_strategy_id IS NOT NULL
              AND status IN ('SUBMITTED', 'WAITING', 'PARTIALLY_FILLED', 'FILLED', 'CANCELED')
              AND request_id NOT LIKE '%-waiting-%'
              AND request_id NOT LIKE '%-canceled-%'
              AND request_id NOT LIKE '%-filled-%'
              AND request_id NOT LIKE '%-partial-%'
              AND request_id NOT LIKE '%-failed-%'
              AND created_at >= ?
              AND created_at < ?
            """,
            (exchange, market, start_utc, end_utc),
        ).fetchone()
    return int(row["count"] if row else 0)


def has_live_strategy_order_for_signal(session_id: int, candidate_strategy_id: int, market: str, candle_time_utc: str, signal: str, side: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id FROM live_order_logs
            WHERE session_id = ?
              AND candidate_strategy_id = ?
              AND market = ?
              AND candle_time_utc = ?
              AND side = ?
              AND strategy_name IS NOT NULL
              AND status IN ('BLOCKED', 'PREVIEWED', 'SUBMITTED', 'WAITING', 'PARTIALLY_FILLED', 'FILLED', 'CANCELED', 'FAILED', 'ERROR')
            LIMIT 1
            """,
            (session_id, candidate_strategy_id, market, candle_time_utc, side),
        ).fetchone()
    return row is not None


def has_open_live_strategy_order(exchange: str, market: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            f"""
            SELECT id FROM live_order_logs
            WHERE exchange = ?
              AND market = ?
              AND session_id IS NOT NULL
              AND status IN ('SUBMITTED', 'WAITING', 'PARTIALLY_FILLED')
{LIVE_ORDER_EVENT_REQUEST_ID_FILTER}
            LIMIT 1
            """,
            (exchange, market),
        ).fetchone()
    return row is not None


def has_unresolved_live_order(exchange: str, market: str) -> bool:
    return bool(load_reconcilable_live_order_logs(exchange, market))


def has_unresolved_live_order_for_exchange(exchange: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            f"""
            SELECT 1
            FROM live_order_logs
            WHERE exchange = ?
              AND status IN ('SUBMITTED', 'WAITING', 'PARTIALLY_FILLED')
{LIVE_ORDER_EVENT_REQUEST_ID_FILTER}
            LIMIT 1
            """,
            (exchange,),
        ).fetchone()
    return row is not None


def has_unresolved_entry_live_order_for_exchange(exchange: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            f"""
            SELECT 1
            FROM live_order_logs
            WHERE exchange = ?
              AND order_purpose = 'ENTRY'
              AND status IN ('SUBMITTED', 'WAITING', 'PARTIALLY_FILLED')
{LIVE_ORDER_EVENT_REQUEST_ID_FILTER}
            LIMIT 1
            """,
            (exchange,),
        ).fetchone()
    return row is not None


def load_unresolved_live_order_logs_for_exchange(exchange: str = "bithumb") -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM live_order_logs
            WHERE exchange = ?
              AND status IN ('SUBMITTED', 'WAITING', 'PARTIALLY_FILLED')
{LIVE_ORDER_EVENT_REQUEST_ID_FILTER}
            ORDER BY updated_at ASC, id ASC
            """,
            (exchange,),
        ).fetchall()
    return [_normalize_live_order_log(dict(row)) for row in rows]


def load_reconcilable_live_order_logs(exchange: str = "bithumb", market: str = "KRW-BTC") -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM live_order_logs
            WHERE exchange = ?
              AND market = ?
              AND status IN ('SUBMITTED', 'WAITING', 'PARTIALLY_FILLED')
{LIVE_ORDER_EVENT_REQUEST_ID_FILTER}
            ORDER BY updated_at ASC, id ASC
            """,
            (exchange, market),
        ).fetchall()
    return [_normalize_live_order_log(dict(row)) for row in rows]


def get_live_order_log_by_uuid(order_uuid: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            f"""
            SELECT *
            FROM live_order_logs
            WHERE order_uuid = ?
{LIVE_ORDER_EVENT_REQUEST_ID_FILTER}
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (order_uuid,),
        ).fetchone()
    return _normalize_live_order_log(dict(row)) if row else None


def load_live_order_logs_by_uuid(order_uuid: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM live_order_logs
            WHERE order_uuid = ?
            ORDER BY
                CASE
                    WHEN request_id NOT LIKE '%-submitted%'
                     AND request_id NOT LIKE '%-waiting-%'
                     AND request_id NOT LIKE '%-partial%'
                     AND request_id NOT LIKE '%-canceled-%'
                     AND request_id NOT LIKE '%-filled-%'
                     AND request_id NOT LIKE '%-failed-%'
                    THEN 0 ELSE 1
                END,
                CASE WHEN position_id IS NOT NULL THEN 0 ELSE 1 END,
                updated_at ASC,
                id ASC
            """,
            (order_uuid,),
        ).fetchall()
    return [_normalize_live_order_log(dict(row)) for row in rows]


def load_filled_entry_order_logs_without_position(exchange: str = "bithumb", market: str = "KRW-BTC") -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM live_order_logs
            WHERE exchange = ?
              AND market = ?
              AND side = 'BUY'
              AND order_purpose = 'ENTRY'
              AND status = 'FILLED'
              AND position_id IS NULL
              AND session_id IS NOT NULL
              AND candidate_strategy_id IS NOT NULL
              AND order_uuid IS NOT NULL
              AND executed_volume > 0
{LIVE_ORDER_EVENT_REQUEST_ID_FILTER}
            ORDER BY updated_at ASC, id ASC
            """,
            (exchange, market),
        ).fetchall()
    return [_normalize_live_order_log(dict(row)) for row in rows]


def _normalize_live_strategy_session(row: dict) -> dict:
    row["auto_enabled"] = bool(row.get("auto_enabled"))
    row["strategy_parameters"] = json.loads(row.get("strategy_parameters") or "{}")
    return row


def create_auto_live_pilot_session(session: dict) -> int:
    now_utc = _utc_now()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE auto_live_pilot_sessions
            SET status = 'STOPPED', stopped_at = ?, updated_at = ?
            WHERE status IN ('READY', 'RUNNING')
            """,
            (now_utc, now_utc),
        )
        cursor = conn.execute(
            """
            INSERT INTO auto_live_pilot_sessions (
                exchange, market, candidate_strategy_id, strategy_name, status,
                auto_enabled, order_amount_krw, max_orders_per_day,
                orders_created_today, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session["exchange"],
                session["market"],
                session.get("candidate_strategy_id"),
                session["strategy_name"],
                session.get("status", "READY"),
                1 if session.get("auto_enabled", False) else 0,
                session["order_amount_krw"],
                session["max_orders_per_day"],
                session.get("orders_created_today", 0),
                now_utc,
                now_utc,
            ),
        )
        return int(cursor.lastrowid)


def load_latest_auto_live_pilot_session() -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM auto_live_pilot_sessions
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
    return _normalize_auto_live_pilot_session(dict(row)) if row else None


def load_running_auto_live_pilot_sessions() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM auto_live_pilot_sessions
            WHERE status IN ('READY', 'RUNNING')
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()
    return [_normalize_auto_live_pilot_session(dict(row)) for row in rows]


def pause_running_auto_live_pilot_sessions_on_startup() -> int:
    now_utc = _utc_now()
    with get_connection() as conn:
        before = conn.total_changes
        conn.execute(
            """
            UPDATE auto_live_pilot_sessions
            SET status = 'LIVE_PAUSED',
                auto_enabled = 0,
                last_order_status = COALESCE(last_order_status, 'SERVER_RESTART_LIVE_PAUSED'),
                updated_at = ?
            WHERE status IN ('READY', 'RUNNING')
            """,
            (now_utc,),
        )
        return conn.total_changes - before


def update_auto_live_pilot_session(session_id: int, updates: dict) -> None:
    allowed = {
        "status",
        "auto_enabled",
        "orders_created_today",
        "last_signal",
        "last_signal_time_utc",
        "last_order_time_utc",
        "last_order_uuid",
        "last_order_status",
        "last_processed_candle_time_utc",
        "stopped_at",
    }
    values = {key: value for key, value in updates.items() if key in allowed}
    if not values:
        return
    values["updated_at"] = _utc_now()
    columns = ", ".join(f"{key} = ?" for key in values)
    params = list(values.values()) + [session_id]
    with get_connection() as conn:
        conn.execute(f"UPDATE auto_live_pilot_sessions SET {columns} WHERE id = ?", params)


def count_auto_live_orders_today(exchange: str, market: str) -> int:
    kst = timezone(timedelta(hours=9))
    now_kst = datetime.now(kst)
    start_utc = now_kst.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    end_utc = (now_kst.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM live_order_logs
            WHERE exchange = ?
              AND market = ?
              AND strategy_name IS NOT NULL
              AND status IN ('SUBMITTED', 'WAITING', 'CANCELED', 'FILLED')
              AND request_id NOT LIKE '%-submitted%'
              AND request_id NOT LIKE '%-waiting-%'
              AND request_id NOT LIKE '%-canceled-%'
              AND request_id NOT LIKE '%-filled-%'
              AND request_id NOT LIKE '%-failed-%'
              AND created_at >= ?
              AND created_at < ?
            """,
            (exchange, market, start_utc, end_utc),
        ).fetchone()
    return int(row["count"] if row else 0)


def has_live_order_for_candle(exchange: str, market: str, candle_time_utc: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id FROM live_order_logs
            WHERE exchange = ?
              AND market = ?
              AND candle_time_utc = ?
              AND strategy_name IS NOT NULL
              AND status IN ('SUBMITTED', 'WAITING', 'PARTIALLY_FILLED', 'CANCELED', 'FILLED', 'FAILED')
              AND request_id NOT LIKE '%-submitted%'
              AND request_id NOT LIKE '%-waiting-%'
              AND request_id NOT LIKE '%-canceled-%'
              AND request_id NOT LIKE '%-filled-%'
              AND request_id NOT LIKE '%-failed-%'
            LIMIT 1
            """,
            (exchange, market, candle_time_utc),
        ).fetchone()
    return row is not None


def has_open_auto_live_order(exchange: str, market: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id FROM live_order_logs
            WHERE exchange = ?
              AND market = ?
              AND strategy_name IS NOT NULL
              AND status IN ('SUBMITTED', 'WAITING', 'PARTIALLY_FILLED')
              AND request_id NOT LIKE '%-submitted%'
              AND request_id NOT LIKE '%-waiting-%'
              AND request_id NOT LIKE '%-partial%'
              AND request_id NOT LIKE '%-canceled-%'
              AND request_id NOT LIKE '%-filled-%'
              AND request_id NOT LIKE '%-failed-%'
            LIMIT 1
            """,
            (exchange, market),
        ).fetchone()
    return row is not None


def _normalize_auto_live_pilot_session(row: dict) -> dict:
    row["auto_enabled"] = bool(row.get("auto_enabled"))
    return row


def load_forward_session(session_id: int, conn: sqlite3.Connection | None = None) -> dict | None:
    owns_connection = conn is None
    if conn is None:
        conn = _connect_database()
    try:
        session = conn.execute(
            "SELECT * FROM paper_forward_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if session is None:
            return None
        candidate = conn.execute(
            "SELECT * FROM candidate_strategies WHERE id = ?",
            (session["candidate_strategy_id"],),
        ).fetchone()
        orders = conn.execute(
            """
            SELECT * FROM paper_forward_orders
            WHERE session_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (session_id,),
        ).fetchall()
        equity_rows = conn.execute(
            """
            SELECT * FROM paper_forward_equity_points
            WHERE session_id = ?
            ORDER BY candle_time_utc ASC, id ASC
            """,
            (session_id,),
        ).fetchall()
        tick_rows = conn.execute(
            """
            SELECT * FROM paper_forward_tick_logs
            WHERE session_id = ?
            ORDER BY tick_time_utc DESC, id DESC
            LIMIT 50
            """,
            (session_id,),
        ).fetchall()
        signal_rows = conn.execute(
            """
            SELECT * FROM paper_forward_signal_logs
            WHERE session_id = ?
            ORDER BY signal_time_utc DESC, id DESC
            LIMIT 100
            """,
            (session_id,),
        ).fetchall()

        normalized_orders = []
        for row in orders:
            order = dict(row)
            order["time"] = order["created_at"]
            order["quantity"] = order["volume"]
            order["execution_price"] = order["price"]
            order["signal_price"] = order["price"]
            order["risk_check_result"] = order["risk_result"]
            order["order_source"] = "PaperBroker"
            order["candle_timestamp"] = order["candle_time_utc"]
            order["blocked"] = order["risk_result"] != "PASS"
            order["blocked_reason"] = None if order["risk_result"] == "PASS" else order["risk_result"]
            order["signal_reason"] = order["reason"]
            normalized_orders.append(order)

        normalized_equity = []
        for row in equity_rows:
            point = dict(row)
            point["time"] = point.pop("candle_time_utc")
            point["cash_krw"] = point.pop("cash_balance")
            point["btc_quantity"] = point.pop("position_volume")
            normalized_equity.append(point)

        candidate_item = dict(candidate) if candidate is not None else None
        if candidate_item is not None:
            candidate_item["parameters"] = json.loads(candidate_item.pop("parameters_json"))

        next_check_time_utc = (
            _format_utc(_parse_utc(session["last_tick_time_utc"] or session["updated_at"]) + timedelta(seconds=60))
            if session["status"] == "RUNNING" and _parse_utc(session["last_tick_time_utc"] or session["updated_at"])
            else None
        )
        average_trade_pnl = (
            session["realized_pnl"] / max(session["win_count"] + session["loss_count"], 1)
            if session["win_count"] + session["loss_count"] > 0
            else 0.0
        )
        return {
            "id": session["id"],
            "mode": "FORWARD_PAPER",
            "status": session["status"],
            "risk_status": session["last_risk_result"],
            "scheduler_interval_seconds": 60,
            "candidate_strategy_id": session["candidate_strategy_id"],
            "candidate": candidate_item,
            "market": session["market"],
            "unit": session["unit"],
            "timeframe": session["unit"],
            "strategy": session["strategy"],
            "strategy_name": session["strategy"],
            "settings": json.loads(session["parameters_json"]),
            "risk": json.loads(session["risk_json"]),
            "started_at": session["started_at"],
            "stopped_at": session["stopped_at"],
            "created_at": session["created_at"],
            "updated_at": session["updated_at"],
            "last_processed_candle_time_utc": session["last_processed_candle_time_utc"],
            "last_tick_time_utc": session["last_tick_time_utc"],
            "next_check_time_utc": next_check_time_utc,
            "last_signal": session["last_signal"],
            "balance": {
                "initial_cash": session["initial_balance_krw"],
                "initial_balance_krw": session["initial_balance_krw"],
                "cash_krw": session["current_balance_krw"],
                "current_balance_krw": session["current_balance_krw"],
                "current_price": session["current_price"],
                "equity": session["total_equity"],
                "total_equity": session["total_equity"],
                "realized_pnl": session["realized_pnl"],
                "unrealized_pnl": session["unrealized_pnl"],
                "total_pnl": session["total_equity"] - session["initial_balance_krw"],
                "total_return": session["total_return_percent"] / 100,
                "total_return_percent": session["total_return_percent"],
                "mdd": session["max_drawdown"],
            },
            "position": {
                "btc_quantity": session["current_position_volume"],
                "current_position_volume": session["current_position_volume"],
                "avg_buy_price": session["average_entry_price"],
                "average_entry_price": session["average_entry_price"],
                "market_value": session["current_position_volume"] * session["current_price"],
                "position_ratio": (
                    (session["current_position_volume"] * session["current_price"]) / session["total_equity"]
                    if session["total_equity"]
                    else 0.0
                ),
            },
            "metrics": {
                "total_return": session["total_return_percent"] / 100,
                "mdd": session["max_drawdown"],
                "win_rate": session["win_rate"],
                "trade_count": session["trade_count"],
                "profit_factor": session["profit_factor"],
                "realized_pnl": session["realized_pnl"],
                "average_trade_pnl": average_trade_pnl,
            },
            "orders": normalized_orders,
            "equity_curve": normalized_equity,
            "tick_logs": [dict(row) for row in tick_rows],
            "signal_logs": [dict(row) for row in signal_rows],
        }
    finally:
        if owns_connection:
            conn.close()


def _json_load(value: str | None, fallback):
    try:
        return json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _normalize_decision_snapshot(row: dict) -> dict:
    row["positive_reasons"] = _json_load(row.pop("positive_reasons_json", "[]"), [])
    row["negative_reasons"] = _json_load(row.pop("negative_reasons_json", "[]"), [])
    row["blockers"] = _json_load(row.pop("blockers_json", "[]"), [])
    row["raw_features"] = _json_load(row.pop("raw_features_json", "{}"), {})
    row["external_factors"] = _json_load(row.pop("external_factors_json", "{}"), {})
    row["internal_signals"] = _json_load(row.pop("internal_signals_json", "{}"), {})
    row["attack_score_breakdown"] = _json_load(row.pop("attack_score_breakdown_json", "{}"), {})
    row["aggressive_blockers"] = _json_load(row.pop("aggressive_blockers_json", "[]"), [])
    row["aggressive_buy_blockers"] = _json_load(row.pop("aggressive_buy_blockers_json", "[]"), row["aggressive_blockers"])
    if not row["aggressive_buy_blockers"] and row["aggressive_blockers"]:
        row["aggressive_buy_blockers"] = row["aggressive_blockers"]
    row["aggressive_warnings"] = _json_load(row.pop("aggressive_warnings_json", "[]"), [])
    row["exposure_limit_blocked"] = bool(row.get("exposure_limit_blocked"))
    row["partial_take_profit_triggered"] = bool(row.get("partial_take_profit_triggered"))
    row["pyramiding_allowed"] = bool(row.get("pyramiding_allowed"))
    row["core_exposure_applied"] = bool(row.get("core_exposure_applied"))
    row["core_exposure_broken_by_panic"] = bool(row.get("core_exposure_broken_by_panic"))
    return row


def _normalize_order_intent(row: dict) -> dict:
    row["blockers"] = _json_load(row.pop("blockers_json", "[]"), [])
    row["risk_preview"] = _json_load(row.pop("risk_preview_json", "{}"), {})
    row["policy_preview"] = _json_load(row.pop("policy_preview_json", "{}"), {})
    row["promotion_blockers"] = _json_load(row.pop("promotion_blockers_json", "[]"), [])
    row["pyramiding_allowed"] = bool(row.get("pyramiding_allowed"))
    row["no_averaging_down_blocked"] = bool(row.get("no_averaging_down_blocked"))
    return row


def _normalize_smart_rehearsal_review(row: dict) -> dict:
    row["is_active"] = _smart_rehearsal_review_active(row)
    return row


def _smart_rehearsal_review_active(review: dict | None, now_utc: datetime | None = None) -> bool:
    if not review or review.get("decision") != "APPROVED":
        return False
    expires_at = _parse_utc(review.get("expires_at"))
    if expires_at is None:
        return False
    now = now_utc or datetime.now(timezone.utc)
    return now < expires_at


def _smart_rehearsal_review_expiry(reviewed_at_utc: str) -> str:
    reviewed_at = _parse_utc(reviewed_at_utc) or datetime.now(timezone.utc)
    return _format_utc(reviewed_at + timedelta(days=7))


def load_bot_operation_policy(market: str = "KRW-BTC") -> dict:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM bot_operation_policy WHERE market = ?", (market,)).fetchone()
        if row is None:
            now_utc = _utc_now()
            conn.execute(
                """
                INSERT INTO bot_operation_policy (
                    market, auto_trading_enabled, max_total_exposure_krw,
                    daily_loss_limit_pct, created_at, updated_at
                ) VALUES (?, 0, 500000, 3, ?, ?)
                """,
                (market, now_utc, now_utc),
            )
            row = conn.execute("SELECT * FROM bot_operation_policy WHERE market = ?", (market,)).fetchone()
        policy = dict(row)
        policy["auto_trading_enabled"] = bool(policy.get("auto_trading_enabled"))
        policy["daily_loss_limit_krw"] = (
            float(policy.get("max_total_exposure_krw") or 0.0)
            * float(policy.get("daily_loss_limit_pct") or 0.0)
            / 100
        )
        return policy


def load_global_bot_operation_policy() -> dict:
    return load_bot_operation_policy("KRW-BTC")


def update_bot_operation_policy(market: str = "KRW-BTC", updates: dict | None = None) -> dict:
    updates = updates or {}
    allowed = {"auto_trading_enabled", "max_total_exposure_krw", "daily_loss_limit_pct"}
    values = {key: updates[key] for key in allowed if key in updates}
    if "max_total_exposure_krw" in values and float(values["max_total_exposure_krw"]) <= 0:
        raise ValueError("max_total_exposure_krw must be greater than 0.")
    if "daily_loss_limit_pct" in values:
        pct = float(values["daily_loss_limit_pct"])
        if pct <= 0 or pct > 100:
            raise ValueError("daily_loss_limit_pct must be greater than 0 and less than or equal to 100.")
        values["daily_loss_limit_pct"] = pct
    if "auto_trading_enabled" in values:
        values["auto_trading_enabled"] = 1 if bool(values["auto_trading_enabled"]) else 0
    load_bot_operation_policy(market)
    if values:
        assignments = ", ".join(f"{key} = ?" for key in values)
        params = [*values.values(), _utc_now(), market]
        with get_connection() as conn:
            conn.execute(
                f"""
                UPDATE bot_operation_policy
                SET {assignments}, updated_at = ?
                WHERE market = ?
                """,
                params,
            )
    return load_bot_operation_policy(market)


def insert_decision_snapshot(snapshot: dict) -> int:
    now_utc = _utc_now()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO decision_snapshots (
                decided_at, exchange, market, timeframe, candle_time_utc, candle_time_kst,
                selected_strategy_id, selected_strategy_name, selected_strategy_type, legacy_signal, market_regime,
                current_bot_position_qty, current_bot_position_value_krw, current_exposure_pct,
                target_exposure_pct, action_hint, confidence_score, risk_score,
                one_line_summary, positive_reasons_json, negative_reasons_json,
                blockers_json, raw_features_json, external_factors_json, internal_signals_json,
                max_total_exposure_krw, daily_loss_limit_pct, daily_loss_limit_krw,
                available_krw_balance, exposure_limit_blocked, attack_score, attack_mode,
                attack_score_breakdown_json, aggressive_target_exposure_pct,
                conservative_target_exposure_pct, final_target_exposure_source,
                current_position_pnl_pct, highest_price_since_entry, trailing_stop_price,
                partial_take_profit_triggered, pyramiding_allowed, aggressive_blockers_json,
                aggressive_buy_blockers_json, aggressive_warnings_json, core_exposure_pct,
                core_exposure_applied, core_exposure_broken_by_panic,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.get("decided_at", now_utc),
                snapshot.get("exchange", "bithumb"),
                snapshot.get("market", "KRW-BTC"),
                snapshot.get("timeframe", "5m"),
                snapshot.get("candle_time_utc"),
                snapshot.get("candle_time_kst"),
                snapshot.get("selected_strategy_id"),
                snapshot.get("selected_strategy_name"),
                snapshot.get("selected_strategy_type"),
                snapshot.get("legacy_signal", "HOLD"),
                snapshot.get("market_regime", "UNKNOWN"),
                snapshot.get("current_bot_position_qty", 0.0),
                snapshot.get("current_bot_position_value_krw", 0.0),
                snapshot.get("current_exposure_pct", 0.0),
                snapshot.get("target_exposure_pct", 0.0),
                snapshot.get("action_hint", "WAIT"),
                snapshot.get("confidence_score", 0.0),
                snapshot.get("risk_score", 0.0),
                snapshot.get("one_line_summary", ""),
                json.dumps(snapshot.get("positive_reasons", []), ensure_ascii=False),
                json.dumps(snapshot.get("negative_reasons", []), ensure_ascii=False),
                json.dumps(snapshot.get("blockers", []), ensure_ascii=False),
                json.dumps(snapshot.get("raw_features", {}), ensure_ascii=False),
                json.dumps(snapshot.get("external_factors", {}), ensure_ascii=False),
                json.dumps(snapshot.get("internal_signals", {}), ensure_ascii=False),
                snapshot.get("max_total_exposure_krw", 0.0),
                snapshot.get("daily_loss_limit_pct", 0.0),
                snapshot.get("daily_loss_limit_krw", 0.0),
                snapshot.get("available_krw_balance"),
                1 if snapshot.get("exposure_limit_blocked") else 0,
                snapshot.get("attack_score", 0.0),
                snapshot.get("attack_mode", "OFF"),
                json.dumps(snapshot.get("attack_score_breakdown", {}), ensure_ascii=False),
                snapshot.get("aggressive_target_exposure_pct", 0.0),
                snapshot.get("conservative_target_exposure_pct", 0.0),
                snapshot.get("final_target_exposure_source", "CONSERVATIVE"),
                snapshot.get("current_position_pnl_pct", 0.0),
                snapshot.get("highest_price_since_entry"),
                snapshot.get("trailing_stop_price"),
                1 if snapshot.get("partial_take_profit_triggered") else 0,
                1 if snapshot.get("pyramiding_allowed") else 0,
                json.dumps(snapshot.get("aggressive_blockers", []), ensure_ascii=False),
                json.dumps(snapshot.get("aggressive_buy_blockers", snapshot.get("aggressive_blockers", [])), ensure_ascii=False),
                json.dumps(snapshot.get("aggressive_warnings", []), ensure_ascii=False),
                snapshot.get("core_exposure_pct", 0.0),
                1 if snapshot.get("core_exposure_applied") else 0,
                1 if snapshot.get("core_exposure_broken_by_panic") else 0,
                now_utc,
            ),
        )
        return int(cursor.lastrowid)


def insert_order_intent(intent: dict) -> int:
    now_utc = _utc_now()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO order_intents (
                decision_snapshot_id, exchange, market, side, action_hint,
                current_value_krw, target_value_krw, delta_value_krw, target_qty,
                order_type, limit_price, urgency, status, blockers_json,
                risk_preview_json, policy_preview_json, pilot_order_cap_krw,
                promotion_blockers_json, promotion_status, attack_score, attack_mode,
                target_source, pyramiding_allowed, no_averaging_down_blocked,
                partial_take_profit_pct, trailing_stop_price, position_pnl_pct,
                created_at, submitted_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                intent["decision_snapshot_id"],
                intent.get("exchange", "bithumb"),
                intent.get("market", "KRW-BTC"),
                intent.get("side", "NONE"),
                intent.get("action_hint", "WAIT"),
                intent.get("current_value_krw", 0.0),
                intent.get("target_value_krw", 0.0),
                intent.get("delta_value_krw", 0.0),
                intent.get("target_qty"),
                intent.get("order_type", "LIMIT"),
                intent.get("limit_price"),
                intent.get("urgency", "NORMAL"),
                intent.get("status", "CREATED"),
                json.dumps(intent.get("blockers", []), ensure_ascii=False),
                json.dumps(intent.get("risk_preview", {}), ensure_ascii=False),
                json.dumps(intent.get("policy_preview", {}), ensure_ascii=False),
                intent.get("pilot_order_cap_krw", 0.0),
                json.dumps(intent.get("promotion_blockers", []), ensure_ascii=False),
                intent.get("promotion_status", "SHADOW_ONLY"),
                intent.get("attack_score", 0.0),
                intent.get("attack_mode", "OFF"),
                intent.get("target_source", "CONSERVATIVE"),
                1 if intent.get("pyramiding_allowed") else 0,
                1 if intent.get("no_averaging_down_blocked") else 0,
                intent.get("partial_take_profit_pct", 0.0),
                intent.get("trailing_stop_price"),
                intent.get("position_pnl_pct", 0.0),
                now_utc,
                intent.get("submitted_at"),
                intent.get("completed_at"),
            ),
        )
        return int(cursor.lastrowid)


def update_order_intent(intent_id: int, updates: dict) -> dict | None:
    allowed = {
        "status",
        "risk_preview_json",
        "policy_preview_json",
        "pilot_order_cap_krw",
        "promotion_blockers_json",
        "promotion_status",
        "submitted_at",
        "completed_at",
    }
    values = {key: updates[key] for key in allowed if key in updates}
    if "risk_preview" in updates:
        values["risk_preview_json"] = json.dumps(updates["risk_preview"], ensure_ascii=False)
    if "policy_preview" in updates:
        values["policy_preview_json"] = json.dumps(updates["policy_preview"], ensure_ascii=False)
    if "promotion_blockers" in updates:
        values["promotion_blockers_json"] = json.dumps(updates["promotion_blockers"], ensure_ascii=False)
    if not values:
        return None
    assignments = ", ".join(f"{key} = ?" for key in values)
    with get_connection() as conn:
        conn.execute(f"UPDATE order_intents SET {assignments} WHERE id = ?", [*values.values(), intent_id])
        row = conn.execute("SELECT * FROM order_intents WHERE id = ?", (intent_id,)).fetchone()
    return _normalize_order_intent(dict(row)) if row else None


def upsert_execution_quality_log(payload: dict) -> dict | None:
    request_id = str(payload.get("request_id") or "")
    if not request_id:
        return None
    now_utc = _utc_now()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO execution_quality_logs (
                request_id, order_log_id, signal_time_utc, candle_time_utc, exchange, market,
                strategy_name, market_regime, requested_order_krw, available_krw,
                actual_order_krw, order_price, current_price_at_signal, best_bid, best_ask,
                spread_pct, estimated_slippage_pct, submitted_at, filled_at,
                fill_time_seconds, filled_price, filled_volume, unfilled_volume,
                cancel_after_seconds, cancel_reason, post_fill_return_1m,
                post_fill_return_3m, post_fill_return_5m, adverse_selection_pct,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(request_id) DO UPDATE SET
                order_log_id = excluded.order_log_id,
                signal_time_utc = excluded.signal_time_utc,
                candle_time_utc = excluded.candle_time_utc,
                exchange = excluded.exchange,
                market = excluded.market,
                strategy_name = excluded.strategy_name,
                market_regime = excluded.market_regime,
                requested_order_krw = excluded.requested_order_krw,
                available_krw = excluded.available_krw,
                actual_order_krw = excluded.actual_order_krw,
                order_price = excluded.order_price,
                current_price_at_signal = excluded.current_price_at_signal,
                best_bid = excluded.best_bid,
                best_ask = excluded.best_ask,
                spread_pct = excluded.spread_pct,
                estimated_slippage_pct = excluded.estimated_slippage_pct,
                submitted_at = excluded.submitted_at,
                filled_at = excluded.filled_at,
                fill_time_seconds = excluded.fill_time_seconds,
                filled_price = excluded.filled_price,
                filled_volume = excluded.filled_volume,
                unfilled_volume = excluded.unfilled_volume,
                cancel_after_seconds = excluded.cancel_after_seconds,
                cancel_reason = excluded.cancel_reason,
                post_fill_return_1m = excluded.post_fill_return_1m,
                post_fill_return_3m = excluded.post_fill_return_3m,
                post_fill_return_5m = excluded.post_fill_return_5m,
                adverse_selection_pct = excluded.adverse_selection_pct,
                updated_at = excluded.updated_at
            """,
            (
                request_id,
                payload.get("order_log_id"),
                payload.get("signal_time_utc"),
                payload.get("candle_time_utc"),
                payload.get("exchange", "bithumb"),
                payload.get("market", "KRW-BTC"),
                payload.get("strategy_name", ""),
                payload.get("market_regime", ""),
                payload.get("requested_order_krw"),
                payload.get("available_krw"),
                payload.get("actual_order_krw"),
                payload.get("order_price"),
                payload.get("current_price_at_signal"),
                payload.get("best_bid"),
                payload.get("best_ask"),
                payload.get("spread_pct"),
                payload.get("estimated_slippage_pct"),
                payload.get("submitted_at"),
                payload.get("filled_at"),
                payload.get("fill_time_seconds"),
                payload.get("filled_price"),
                payload.get("filled_volume", 0.0),
                payload.get("unfilled_volume", 0.0),
                payload.get("cancel_after_seconds"),
                payload.get("cancel_reason", ""),
                payload.get("post_fill_return_1m"),
                payload.get("post_fill_return_3m"),
                payload.get("post_fill_return_5m"),
                payload.get("adverse_selection_pct"),
                now_utc,
                now_utc,
            ),
        )
        row = conn.execute("SELECT * FROM execution_quality_logs WHERE request_id = ?", (request_id,)).fetchone()
    return dict(row) if row else None


def load_execution_quality_logs(
    *,
    exchange: str = "bithumb",
    market: str | None = None,
    strategy_name: str | None = None,
    limit: int = 100,
) -> list[dict]:
    clauses = ["exchange = ?"]
    params: list[object] = [exchange]
    if market:
        clauses.append("market = ?")
        params.append(market)
    if strategy_name:
        clauses.append("strategy_name = ?")
        params.append(strategy_name)
    params.append(max(int(limit), 1))
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM execution_quality_logs
            WHERE {" AND ".join(clauses)}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def upsert_trade_outcome_log(payload: dict) -> dict | None:
    order_uuid = str(payload.get("order_uuid") or "")
    if not order_uuid:
        return None
    now_utc = _utc_now()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO trade_outcome_logs (
                order_uuid, request_id, live_order_log_id, session_id, position_id,
                exchange, market, side, order_purpose, strategy_name,
                candidate_strategy_id, market_regime, action_hint, legacy_signal,
                attack_mode, target_source, entry_or_exit_price, filled_price,
                filled_volume, filled_amount_krw, fee_krw, slippage_pct,
                spread_pct, fill_time_seconds, filled_at, post_fill_return_1m,
                post_fill_return_3m, post_fill_return_5m, post_fill_return_15m,
                max_favorable_excursion_pct, max_adverse_excursion_pct,
                adverse_selection_pct, realized_pnl_krw, realized_return_pct,
                holding_minutes, outcome_status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(order_uuid) DO UPDATE SET
                request_id = COALESCE(excluded.request_id, trade_outcome_logs.request_id),
                live_order_log_id = COALESCE(excluded.live_order_log_id, trade_outcome_logs.live_order_log_id),
                session_id = COALESCE(excluded.session_id, trade_outcome_logs.session_id),
                position_id = COALESCE(excluded.position_id, trade_outcome_logs.position_id),
                exchange = excluded.exchange,
                market = excluded.market,
                side = excluded.side,
                order_purpose = excluded.order_purpose,
                strategy_name = excluded.strategy_name,
                candidate_strategy_id = COALESCE(excluded.candidate_strategy_id, trade_outcome_logs.candidate_strategy_id),
                market_regime = excluded.market_regime,
                action_hint = excluded.action_hint,
                legacy_signal = excluded.legacy_signal,
                attack_mode = excluded.attack_mode,
                target_source = excluded.target_source,
                entry_or_exit_price = COALESCE(excluded.entry_or_exit_price, trade_outcome_logs.entry_or_exit_price),
                filled_price = COALESCE(excluded.filled_price, trade_outcome_logs.filled_price),
                filled_volume = CASE WHEN excluded.filled_volume > 0 THEN excluded.filled_volume ELSE trade_outcome_logs.filled_volume END,
                filled_amount_krw = CASE WHEN excluded.filled_amount_krw > 0 THEN excluded.filled_amount_krw ELSE trade_outcome_logs.filled_amount_krw END,
                fee_krw = CASE WHEN excluded.fee_krw > 0 THEN excluded.fee_krw ELSE trade_outcome_logs.fee_krw END,
                slippage_pct = COALESCE(excluded.slippage_pct, trade_outcome_logs.slippage_pct),
                spread_pct = COALESCE(excluded.spread_pct, trade_outcome_logs.spread_pct),
                fill_time_seconds = COALESCE(excluded.fill_time_seconds, trade_outcome_logs.fill_time_seconds),
                filled_at = COALESCE(excluded.filled_at, trade_outcome_logs.filled_at),
                outcome_status = CASE
                    WHEN trade_outcome_logs.outcome_status IN ('REALIZED', 'POST_FILL_COMPLETE')
                    THEN trade_outcome_logs.outcome_status
                    ELSE excluded.outcome_status
                END,
                updated_at = excluded.updated_at
            """,
            (
                order_uuid,
                payload.get("request_id"),
                payload.get("live_order_log_id"),
                payload.get("session_id"),
                payload.get("position_id"),
                payload.get("exchange", "bithumb"),
                payload.get("market", "KRW-BTC"),
                str(payload.get("side") or "").upper(),
                payload.get("order_purpose", "ENTRY"),
                payload.get("strategy_name", ""),
                payload.get("candidate_strategy_id"),
                payload.get("market_regime", ""),
                payload.get("action_hint", ""),
                payload.get("legacy_signal", ""),
                payload.get("attack_mode", ""),
                payload.get("target_source", ""),
                payload.get("entry_or_exit_price"),
                payload.get("filled_price"),
                payload.get("filled_volume", 0.0),
                payload.get("filled_amount_krw", 0.0),
                payload.get("fee_krw", 0.0),
                payload.get("slippage_pct"),
                payload.get("spread_pct"),
                payload.get("fill_time_seconds"),
                payload.get("filled_at"),
                payload.get("post_fill_return_1m"),
                payload.get("post_fill_return_3m"),
                payload.get("post_fill_return_5m"),
                payload.get("post_fill_return_15m"),
                payload.get("max_favorable_excursion_pct"),
                payload.get("max_adverse_excursion_pct"),
                payload.get("adverse_selection_pct"),
                payload.get("realized_pnl_krw"),
                payload.get("realized_return_pct"),
                payload.get("holding_minutes"),
                payload.get("outcome_status", "PENDING_OUTCOME"),
                now_utc,
                now_utc,
            ),
        )
        row = conn.execute("SELECT * FROM trade_outcome_logs WHERE order_uuid = ?", (order_uuid,)).fetchone()
    return dict(row) if row else None


def update_trade_outcome_log(order_uuid: str, updates: dict) -> dict | None:
    allowed = {
        "request_id",
        "live_order_log_id",
        "session_id",
        "position_id",
        "exchange",
        "market",
        "side",
        "order_purpose",
        "strategy_name",
        "candidate_strategy_id",
        "market_regime",
        "action_hint",
        "legacy_signal",
        "attack_mode",
        "target_source",
        "entry_or_exit_price",
        "filled_price",
        "filled_volume",
        "filled_amount_krw",
        "fee_krw",
        "slippage_pct",
        "spread_pct",
        "fill_time_seconds",
        "filled_at",
        "post_fill_return_1m",
        "post_fill_return_3m",
        "post_fill_return_5m",
        "post_fill_return_15m",
        "max_favorable_excursion_pct",
        "max_adverse_excursion_pct",
        "adverse_selection_pct",
        "realized_pnl_krw",
        "realized_return_pct",
        "holding_minutes",
        "outcome_status",
    }
    values = {key: value for key, value in updates.items() if key in allowed}
    if not values:
        return load_trade_outcome_log_by_order_uuid(order_uuid)
    values["updated_at"] = _utc_now()
    assignments = ", ".join(f"{key} = ?" for key in values)
    with get_connection() as conn:
        conn.execute(f"UPDATE trade_outcome_logs SET {assignments} WHERE order_uuid = ?", [*values.values(), order_uuid])
        row = conn.execute("SELECT * FROM trade_outcome_logs WHERE order_uuid = ?", (order_uuid,)).fetchone()
    return dict(row) if row else None


def load_trade_outcome_log_by_order_uuid(order_uuid: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM trade_outcome_logs WHERE order_uuid = ?", (order_uuid,)).fetchone()
    return dict(row) if row else None


def load_trade_outcome_logs(
    *,
    exchange: str | None = None,
    market: str | None = None,
    position_id: int | None = None,
    outcome_status: str | None = None,
    limit: int = 100,
) -> list[dict]:
    clauses: list[str] = []
    params: list[object] = []
    if exchange:
        clauses.append("exchange = ?")
        params.append(exchange)
    if market:
        clauses.append("market = ?")
        params.append(market)
    if position_id is not None:
        clauses.append("position_id = ?")
        params.append(position_id)
    if outcome_status:
        clauses.append("outcome_status = ?")
        params.append(outcome_status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(max(int(limit), 1))
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM trade_outcome_logs
            {where}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def load_trade_outcomes_needing_post_fill_updates(limit: int = 200) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM trade_outcome_logs
            WHERE filled_price IS NOT NULL
              AND filled_price > 0
              AND (
                    post_fill_return_1m IS NULL
                 OR post_fill_return_3m IS NULL
                 OR post_fill_return_5m IS NULL
                 OR post_fill_return_15m IS NULL
              )
              AND outcome_status IN ('PENDING_OUTCOME', 'PENDING_MARKET_DATA', 'PENDING_REALIZED')
            ORDER BY filled_at ASC, id ASC
            LIMIT ?
            """,
            (max(int(limit), 1),),
        ).fetchall()
    return [dict(row) for row in rows]


def upsert_adaptive_edge_stat(payload: dict) -> dict | None:
    now_utc = _utc_now()
    key = {
        "exchange": payload.get("exchange", "bithumb"),
        "market": payload.get("market", "KRW-BTC"),
        "strategy_name": payload.get("strategy_name", ""),
        "candidate_strategy_id": int(payload.get("candidate_strategy_id") or 0),
        "unit": int(payload.get("unit") or 0),
        "market_regime": payload.get("market_regime", ""),
        "action_hint": payload.get("action_hint", ""),
        "legacy_signal": payload.get("legacy_signal", ""),
        "attack_mode": payload.get("attack_mode", ""),
        "target_source": payload.get("target_source", ""),
        "order_purpose": payload.get("order_purpose", ""),
    }
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO adaptive_edge_stats (
                exchange, market, strategy_name, candidate_strategy_id, unit,
                market_regime, action_hint, legacy_signal, attack_mode,
                target_source, order_purpose, sample_count, win_count, loss_count,
                win_rate, avg_post_fill_return_1m, avg_post_fill_return_5m,
                avg_post_fill_return_15m, avg_realized_return_pct,
                avg_realized_pnl_krw, profit_factor, avg_adverse_selection_pct,
                avg_slippage_pct, avg_fill_time_seconds, max_drawdown_pct,
                confidence_score, edge_score, last_updated_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(
                exchange, market, strategy_name, candidate_strategy_id, unit,
                market_regime, action_hint, legacy_signal, attack_mode,
                target_source, order_purpose
            ) DO UPDATE SET
                sample_count = excluded.sample_count,
                win_count = excluded.win_count,
                loss_count = excluded.loss_count,
                win_rate = excluded.win_rate,
                avg_post_fill_return_1m = excluded.avg_post_fill_return_1m,
                avg_post_fill_return_5m = excluded.avg_post_fill_return_5m,
                avg_post_fill_return_15m = excluded.avg_post_fill_return_15m,
                avg_realized_return_pct = excluded.avg_realized_return_pct,
                avg_realized_pnl_krw = excluded.avg_realized_pnl_krw,
                profit_factor = excluded.profit_factor,
                avg_adverse_selection_pct = excluded.avg_adverse_selection_pct,
                avg_slippage_pct = excluded.avg_slippage_pct,
                avg_fill_time_seconds = excluded.avg_fill_time_seconds,
                max_drawdown_pct = excluded.max_drawdown_pct,
                confidence_score = excluded.confidence_score,
                edge_score = excluded.edge_score,
                last_updated_at = excluded.last_updated_at,
                updated_at = excluded.updated_at
            """,
            (
                key["exchange"],
                key["market"],
                key["strategy_name"],
                key["candidate_strategy_id"],
                key["unit"],
                key["market_regime"],
                key["action_hint"],
                key["legacy_signal"],
                key["attack_mode"],
                key["target_source"],
                key["order_purpose"],
                int(payload.get("sample_count") or 0),
                int(payload.get("win_count") or 0),
                int(payload.get("loss_count") or 0),
                payload.get("win_rate", 0.0),
                payload.get("avg_post_fill_return_1m", 0.0),
                payload.get("avg_post_fill_return_5m", 0.0),
                payload.get("avg_post_fill_return_15m", 0.0),
                payload.get("avg_realized_return_pct", 0.0),
                payload.get("avg_realized_pnl_krw", 0.0),
                payload.get("profit_factor", 0.0),
                payload.get("avg_adverse_selection_pct", 0.0),
                payload.get("avg_slippage_pct", 0.0),
                payload.get("avg_fill_time_seconds", 0.0),
                payload.get("max_drawdown_pct", 0.0),
                payload.get("confidence_score", 0.0),
                payload.get("edge_score", 0.0),
                payload.get("last_updated_at", now_utc),
                now_utc,
                now_utc,
            ),
        )
        row = conn.execute(
            """
            SELECT *
            FROM adaptive_edge_stats
            WHERE exchange = ?
              AND market = ?
              AND strategy_name = ?
              AND candidate_strategy_id = ?
              AND unit = ?
              AND market_regime = ?
              AND action_hint = ?
              AND legacy_signal = ?
              AND attack_mode = ?
              AND target_source = ?
              AND order_purpose = ?
            """,
            (
                key["exchange"],
                key["market"],
                key["strategy_name"],
                key["candidate_strategy_id"],
                key["unit"],
                key["market_regime"],
                key["action_hint"],
                key["legacy_signal"],
                key["attack_mode"],
                key["target_source"],
                key["order_purpose"],
            ),
        ).fetchone()
    return dict(row) if row else None


def load_adaptive_edge_stats(
    *,
    exchange: str = "bithumb",
    market: str | None = None,
    strategy_name: str | None = None,
    candidate_strategy_id: int | None = None,
    unit: int | None = None,
    limit: int = 100,
) -> list[dict]:
    clauses = ["exchange = ?"]
    params: list[object] = [exchange]
    if market:
        clauses.append("market = ?")
        params.append(market)
    if strategy_name:
        clauses.append("strategy_name = ?")
        params.append(strategy_name)
    if candidate_strategy_id is not None:
        clauses.append("candidate_strategy_id = ?")
        params.append(int(candidate_strategy_id))
    if unit is not None:
        clauses.append("unit = ?")
        params.append(int(unit))
    params.append(max(int(limit), 1))
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM adaptive_edge_stats
            WHERE {" AND ".join(clauses)}
            ORDER BY edge_score DESC, confidence_score DESC, sample_count DESC, last_updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def find_adaptive_edge_stat(context: dict) -> dict | None:
    params = (
        context.get("exchange", "bithumb"),
        context.get("market", "KRW-BTC"),
        context.get("strategy_name", ""),
        int(context.get("candidate_strategy_id") or 0),
        int(context.get("unit") or 0),
        context.get("market_regime", ""),
        context.get("action_hint", ""),
        context.get("legacy_signal", ""),
        context.get("attack_mode", ""),
        context.get("target_source", ""),
        context.get("order_purpose", ""),
    )
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM adaptive_edge_stats
            WHERE exchange = ?
              AND market = ?
              AND strategy_name = ?
              AND candidate_strategy_id = ?
              AND unit = ?
              AND market_regime = ?
              AND action_hint = ?
              AND legacy_signal = ?
              AND attack_mode = ?
              AND target_source = ?
              AND order_purpose = ?
            LIMIT 1
            """,
            params,
        ).fetchone()
    return dict(row) if row else None


def insert_strategy_kill_switch_event(event: dict) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO strategy_kill_switch_events (
                candidate_strategy_id, exchange, market, strategy_name, action,
                reason, blockers_json, metrics_json, cooldown_until, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.get("candidate_strategy_id"),
                event.get("exchange", "bithumb"),
                event.get("market", "KRW-BTC"),
                event.get("strategy_name", ""),
                event.get("action", "NONE"),
                event.get("reason", ""),
                json.dumps(event.get("blockers", []), ensure_ascii=False),
                json.dumps(event.get("metrics", {}), ensure_ascii=False),
                event.get("cooldown_until"),
                event.get("created_at", _utc_now()),
            ),
        )
        return int(cursor.lastrowid)


def load_strategy_kill_switch_events(
    *,
    exchange: str = "bithumb",
    market: str | None = None,
    strategy_name: str | None = None,
    limit: int = 20,
) -> list[dict]:
    clauses = ["exchange = ?"]
    params: list[object] = [exchange]
    if market:
        clauses.append("market = ?")
        params.append(market)
    if strategy_name:
        clauses.append("strategy_name = ?")
        params.append(strategy_name)
    params.append(max(int(limit), 1))
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM strategy_kill_switch_events
            WHERE {" AND ".join(clauses)}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["blockers"] = _json_load(item.pop("blockers_json", "[]"), [])
        item["metrics"] = _json_load(item.pop("metrics_json", "{}"), {})
        result.append(item)
    return result


def load_latest_decision_snapshot(market: str | None = None) -> dict | None:
    params: list[object] = []
    market_filter = ""
    if market:
        market_filter = "WHERE market = ?"
        params.append(market)
    with get_connection() as conn:
        row = conn.execute(
            f"""
            SELECT *
            FROM decision_snapshots
            {market_filter}
            ORDER BY decided_at DESC, id DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
        if row is None:
            return None
        snapshot = _normalize_decision_snapshot(dict(row))
        intents = conn.execute(
            """
            SELECT *
            FROM order_intents
            WHERE decision_snapshot_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (snapshot["id"],),
        ).fetchall()
        snapshot["order_intents"] = [_normalize_order_intent(dict(item)) for item in intents]
        return snapshot


def load_decision_snapshots(*, market: str | None = None, limit: int = 50, offset: int = 0, from_time: str | None = None, to_time: str | None = None) -> list[dict]:
    filters: list[str] = []
    params: list[object] = []
    if market:
        filters.append("market = ?")
        params.append(market)
    if from_time:
        filters.append("decided_at >= ?")
        params.append(from_time)
    if to_time:
        filters.append("decided_at <= ?")
        params.append(to_time)
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    params.extend([limit, offset])
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM decision_snapshots
            {where}
            ORDER BY decided_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
        snapshots = [_normalize_decision_snapshot(dict(row)) for row in rows]
        if not snapshots:
            return []
        ids = [snapshot["id"] for snapshot in snapshots]
        placeholders = ",".join("?" for _ in ids)
        intent_rows = conn.execute(
            f"""
            SELECT *
            FROM order_intents
            WHERE decision_snapshot_id IN ({placeholders})
            ORDER BY created_at DESC, id DESC
            """,
            ids,
        ).fetchall()
    intents_by_snapshot: dict[int, list[dict]] = {int(snapshot["id"]): [] for snapshot in snapshots}
    for item in intent_rows:
        intent = _normalize_order_intent(dict(item))
        intents_by_snapshot.setdefault(int(intent["decision_snapshot_id"]), []).append(intent)
    for snapshot in snapshots:
        snapshot["order_intents"] = intents_by_snapshot.get(int(snapshot["id"]), [])
    return snapshots


def load_decision_snapshot(decision_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM decision_snapshots WHERE id = ?", (decision_id,)).fetchone()
        if row is None:
            return None
        snapshot = _normalize_decision_snapshot(dict(row))
        intents = conn.execute(
            """
            SELECT *
            FROM order_intents
            WHERE decision_snapshot_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (decision_id,),
        ).fetchall()
    snapshot["order_intents"] = [_normalize_order_intent(dict(item)) for item in intents]
    return snapshot


def save_paper_session(result: dict) -> int:
    balance = result["balance"]
    position = result["position"]
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO paper_sessions (
                status, mode, market, unit, strategy, settings_json, risk_json,
                initial_cash, cash_balance, btc_balance, avg_buy_price,
                current_price, equity, realized_pnl, unrealized_pnl,
                started_at, stopped_at, last_processed_candle_time_utc, last_signal, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                result["status"],
                result.get("mode", "SIMULATION"),
                result["market"],
                result["unit"],
                result["strategy"],
                json.dumps(result["settings"], ensure_ascii=False),
                json.dumps(result["risk"], ensure_ascii=False),
                balance["initial_cash"],
                balance["cash_krw"],
                position["btc_quantity"],
                position["avg_buy_price"],
                balance["current_price"],
                balance["equity"],
                balance["realized_pnl"],
                balance["unrealized_pnl"],
                result["started_at"],
                result.get("stopped_at"),
                result.get("last_processed_candle_time_utc"),
                result.get("last_signal", "HOLD"),
            ),
        )
        session_id = int(cursor.lastrowid)
        conn.executemany(
            """
            INSERT INTO paper_orders (
                session_id, order_time, market, side, strategy, signal_price,
                execution_price, quantity, amount_krw, fee, realized_pnl, reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    session_id,
                    order["time"],
                    order["market"],
                    order["side"],
                    order["strategy"],
                    order["signal_price"],
                    order["execution_price"],
                    order["quantity"],
                    order.get("amount_krw", order["execution_price"] * order["quantity"]),
                    order["fee"],
                    order.get("realized_pnl"),
                    order["reason"],
                )
                for order in result["orders"]
            ],
        )
        conn.executemany(
            """
            INSERT INTO paper_equity_points (
                session_id, candle_time_utc, equity, cash_balance, btc_balance, price
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    session_id,
                    point["time"],
                    point["equity"],
                    point["cash_krw"],
                    point["btc_quantity"],
                    point["price"],
                )
                for point in result["equity_curve"]
            ],
        )
    return session_id


def create_live_paper_session(
    market: str,
    unit: int,
    strategy: str,
    settings: dict,
    risk: dict,
    current_price: float,
    last_processed_candle_time_utc: str | None,
) -> int:
    initial_cash = float(risk.get("initial_cash", 1_000_000))
    now_utc = _utc_now()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO paper_sessions (
                status, mode, market, unit, strategy, settings_json, risk_json,
                initial_cash, cash_balance, btc_balance, avg_buy_price,
                current_price, equity, realized_pnl, unrealized_pnl,
                started_at, stopped_at, last_processed_candle_time_utc, last_signal, updated_at
            ) VALUES (
                'RUNNING', 'LIVE', ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, 0, 0, ?, NULL, ?, 'HOLD', ?
            )
            """,
            (
                market,
                unit,
                strategy,
                json.dumps(settings, ensure_ascii=False),
                json.dumps(risk, ensure_ascii=False),
                initial_cash,
                initial_cash,
                current_price,
                initial_cash,
                now_utc,
                last_processed_candle_time_utc,
                now_utc,
            ),
        )
        session_id = int(cursor.lastrowid)
        conn.execute(
            """
            INSERT INTO paper_equity_points (
                session_id, candle_time_utc, equity, cash_balance, btc_balance, price
            ) VALUES (?, ?, ?, ?, 0, ?)
            """,
            (
                session_id,
                last_processed_candle_time_utc or now_utc,
                initial_cash,
                initial_cash,
                current_price,
            ),
        )
        return session_id


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace(" ", "T")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    if "+" not in normalized[-6:] and "-" not in normalized[-6:]:
        normalized = f"{normalized}+00:00"
    try:
        return datetime.fromisoformat(normalized).astimezone(timezone.utc)
    except ValueError:
        return None


def _format_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_running_live_paper_sessions() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id FROM paper_sessions
            WHERE mode = 'LIVE' AND status = 'RUNNING'
            ORDER BY id ASC
            """
        ).fetchall()
    sessions = [load_paper_session(int(row["id"])) for row in rows]
    return [session for session in sessions if session is not None]


def stop_latest_live_paper_session() -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id FROM paper_sessions
            WHERE mode = 'LIVE' AND status = 'RUNNING'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        now_utc = _utc_now()
        conn.execute(
            """
            UPDATE paper_sessions
            SET status = 'STOPPED', stopped_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (now_utc, now_utc, row["id"]),
        )
        return load_paper_session(int(row["id"]), conn)


def load_latest_live_paper_session() -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id FROM paper_sessions
            WHERE mode = 'LIVE'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return load_paper_session(int(row["id"]), conn)


def append_live_equity_point(
    session_id: int,
    candle_time_utc: str,
    equity: float,
    cash_balance: float,
    btc_balance: float,
    price: float,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO paper_equity_points (
                session_id, candle_time_utc, equity, cash_balance, btc_balance, price
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, candle_time_utc, equity, cash_balance, btc_balance, price),
        )


def update_live_paper_session_state(
    session_id: int,
    *,
    cash_balance: float,
    btc_balance: float,
    avg_buy_price: float,
    current_price: float,
    equity: float,
    realized_pnl: float,
    unrealized_pnl: float,
    last_processed_candle_time_utc: str,
    last_signal: str,
    status: str = "RUNNING",
) -> None:
    now_utc = _utc_now()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE paper_sessions
            SET status = ?,
                cash_balance = ?,
                btc_balance = ?,
                avg_buy_price = ?,
                current_price = ?,
                equity = ?,
                realized_pnl = ?,
                unrealized_pnl = ?,
                last_processed_candle_time_utc = ?,
                last_signal = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                cash_balance,
                btc_balance,
                avg_buy_price,
                current_price,
                equity,
                realized_pnl,
                unrealized_pnl,
                last_processed_candle_time_utc,
                last_signal,
                now_utc,
                session_id,
            ),
        )


def mark_live_paper_session_error(session_id: int, message: str) -> None:
    now_utc = _utc_now()
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE paper_sessions
            SET status = 'ERROR', last_signal = ?, updated_at = ?
            WHERE id = ?
            """,
            (message[:120], now_utc, session_id),
        )


def insert_live_paper_order(
    session_id: int,
    *,
    order_time: str,
    market: str,
    side: str,
    strategy: str,
    signal_price: float,
    execution_price: float,
    quantity: float,
    amount_krw: float,
    fee: float,
    realized_pnl: float | None,
    reason: str,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO paper_orders (
                session_id, order_time, market, side, strategy, signal_price,
                execution_price, quantity, amount_krw, fee, realized_pnl, reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                order_time,
                market,
                side,
                strategy,
                signal_price,
                execution_price,
                quantity,
                amount_krw,
                fee,
                realized_pnl,
                reason,
            ),
        )


def stop_latest_paper_session() -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id FROM paper_sessions
            WHERE status = 'RUNNING'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            """
            UPDATE paper_sessions
            SET status = 'STOPPED', stopped_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (row["id"],),
        )
        return load_paper_session(int(row["id"]), conn)


def load_latest_paper_session() -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id FROM paper_sessions
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return load_paper_session(int(row["id"]), conn)


def load_paper_session(session_id: int, conn: sqlite3.Connection | None = None) -> dict | None:
    owns_connection = conn is None
    if conn is None:
        conn = _connect_database()
    try:
        session = conn.execute(
            "SELECT * FROM paper_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if session is None:
            return None
        orders = conn.execute(
            """
            SELECT * FROM paper_orders
            WHERE session_id = ?
            ORDER BY order_time ASC, id ASC
            """,
            (session_id,),
        ).fetchall()
        equity = conn.execute(
            """
            SELECT * FROM paper_equity_points
            WHERE session_id = ?
            ORDER BY candle_time_utc ASC, id ASC
            """,
            (session_id,),
        ).fetchall()
        normalized_orders = []
        for row in orders:
            order = dict(row)
            order["time"] = order.pop("order_time")
            order["price"] = order.get("execution_price", 0)
            order["volume"] = order.get("quantity", 0)
            if not order.get("amount_krw"):
                order["amount_krw"] = order["price"] * order["volume"]
            order["signal_reason"] = order.get("reason", "")
            order["risk_check_result"] = "PASS"
            order["order_source"] = "PaperBroker"
            order["candle_timestamp"] = order["time"]
            order["blocked"] = False
            order["blocked_reason"] = None
            normalized_orders.append(order)
        normalized_equity = []
        for row in equity:
            point = dict(row)
            point["time"] = point.pop("candle_time_utc")
            point["cash_krw"] = point.pop("cash_balance")
            point["btc_quantity"] = point.pop("btc_balance")
            normalized_equity.append(point)
        equity_values = [point["equity"] for point in normalized_equity]
        peak = None
        mdd = 0.0
        for value in equity_values:
            peak = value if peak is None else max(peak, value)
            if peak:
                mdd = max(mdd, abs((value - peak) / peak))
        total_pnl = session["equity"] - session["initial_cash"]
        total_return = total_pnl / session["initial_cash"] if session["initial_cash"] > 0 else 0.0
        mode = session["mode"] if "mode" in session.keys() else "SIMULATION"
        updated_at = session["updated_at"] if "updated_at" in session.keys() else None
        next_check_time_utc = (
            _format_utc(_parse_utc(updated_at) + timedelta(seconds=60))
            if mode == "LIVE" and session["status"] == "RUNNING" and _parse_utc(updated_at)
            else None
        )
        return {
            "id": session["id"],
            "status": session["status"],
            "mode": mode,
            "risk_status": "ACTIVE" if session["status"] == "RUNNING" else "INACTIVE",
            "scheduler_interval_seconds": 60 if mode == "LIVE" else None,
            "market": session["market"],
            "unit": session["unit"],
            "timeframe": session["unit"],
            "strategy": session["strategy"],
            "settings": json.loads(session["settings_json"]),
            "risk": json.loads(session["risk_json"]),
            "started_at": session["started_at"],
            "stopped_at": session["stopped_at"],
            "last_processed_candle_time_utc": (
                session["last_processed_candle_time_utc"]
                if "last_processed_candle_time_utc" in session.keys()
                else None
            ),
            "last_signal": session["last_signal"] if "last_signal" in session.keys() else "HOLD",
            "updated_at": updated_at,
            "next_check_time_utc": next_check_time_utc,
            "balance": {
                "initial_cash": session["initial_cash"],
                "initial_balance_krw": session["initial_cash"],
                "cash_krw": session["cash_balance"],
                "current_balance_krw": session["cash_balance"],
                "current_price": session["current_price"],
                "equity": session["equity"],
                "realized_pnl": session["realized_pnl"],
                "unrealized_pnl": session["unrealized_pnl"],
                "total_pnl": total_pnl,
                "total_return": total_return,
                "mdd": mdd,
            },
            "position": {
                "btc_quantity": session["btc_balance"],
                "current_position_volume": session["btc_balance"],
                "avg_buy_price": session["avg_buy_price"],
                "average_entry_price": session["avg_buy_price"],
                "market_value": session["btc_balance"] * session["current_price"],
                "position_ratio": (
                    (session["btc_balance"] * session["current_price"]) / session["equity"]
                    if session["equity"]
                    else 0.0
                ),
            },
            "orders": normalized_orders,
            "equity_curve": normalized_equity,
        }
    finally:
        if owns_connection:
            conn.close()
