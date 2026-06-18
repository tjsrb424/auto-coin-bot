from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

DB_PATH = Path(__file__).resolve().parent.parent / "coin_bot_lab.db"
LIVE_ORDER_EVENT_REQUEST_ID_FILTER = """
              AND request_id NOT LIKE '%-submitted%'
              AND request_id NOT LIKE '%-waiting-%'
              AND request_id NOT LIKE '%-partial%'
              AND request_id NOT LIKE '%-canceled-%'
              AND request_id NOT LIKE '%-filled-%'
              AND request_id NOT LIKE '%-failed-%'
"""

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


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    path = _database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


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
                opened_at TEXT,
                closed_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(session_id) REFERENCES live_strategy_sessions(id),
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
        _ensure_column(conn, "decision_snapshots", "internal_signals_json", "TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, "decision_snapshots", "max_total_exposure_krw", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "decision_snapshots", "daily_loss_limit_pct", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "decision_snapshots", "daily_loss_limit_krw", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "decision_snapshots", "available_krw_balance", "REAL")
        _ensure_column(conn, "decision_snapshots", "exposure_limit_blocked", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "order_intents", "risk_preview_json", "TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, "order_intents", "policy_preview_json", "TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, "order_intents", "pilot_order_cap_krw", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "order_intents", "promotion_blockers_json", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "order_intents", "promotion_status", "TEXT NOT NULL DEFAULT 'SHADOW_ONLY'")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_smart_rehearsal_reviews_latest
            ON smart_rehearsal_reviews(exchange, market, request_id, reviewed_at DESC, id DESC)
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


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


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
                metrics_json, warnings_json, stability_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                candidate.get("status", "ACTIVE"),
                now_utc,
            ),
        )
        return int(cursor.lastrowid)


def load_candidate_strategies(limit: int = 50) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM candidate_strategies
            ORDER BY score DESC, updated_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    candidates = []
    for row in rows:
        item = dict(row)
        item["parameters"] = json.loads(item.pop("parameters_json"))
        candidates.append(item)
    return candidates


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
                candidate.get("status", "ACTIVE"),
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
    normalized = "ACTIVE" if status.upper() == "ACTIVE" else "INACTIVE"
    return update_candidate_strategy(candidate_id, {"status": normalized})


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
            """,
            (now_utc, now_utc),
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
                stop_loss_price, take_profit_price, opened_at, closed_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        "current_price",
        "unrealized_pnl",
        "realized_pnl",
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
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
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
    row["exposure_limit_blocked"] = bool(row.get("exposure_limit_blocked"))
    return row


def _normalize_order_intent(row: dict) -> dict:
    row["blockers"] = _json_load(row.pop("blockers_json", "[]"), [])
    row["risk_preview"] = _json_load(row.pop("risk_preview_json", "{}"), {})
    row["policy_preview"] = _json_load(row.pop("policy_preview_json", "{}"), {})
    row["promotion_blockers"] = _json_load(row.pop("promotion_blockers_json", "[]"), [])
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
                selected_strategy_id, selected_strategy_name, legacy_signal, market_regime,
                current_bot_position_qty, current_bot_position_value_krw, current_exposure_pct,
                target_exposure_pct, action_hint, confidence_score, risk_score,
                one_line_summary, positive_reasons_json, negative_reasons_json,
                blockers_json, raw_features_json, external_factors_json, internal_signals_json,
                max_total_exposure_krw, daily_loss_limit_pct, daily_loss_limit_krw,
                available_krw_balance, exposure_limit_blocked, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                promotion_blockers_json, promotion_status, created_at, submitted_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
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
