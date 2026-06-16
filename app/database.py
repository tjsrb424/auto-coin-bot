from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

DB_PATH = Path(__file__).resolve().parent.parent / "coin_bot_lab.db"


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
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
                strategy_name TEXT,
                signal_reason TEXT,
                candle_time_utc TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
        _ensure_column(conn, "live_order_logs", "exchange", "TEXT NOT NULL DEFAULT 'upbit'")
        _ensure_column(conn, "live_order_logs", "order_uuid", "TEXT")
        _ensure_column(conn, "live_order_logs", "strategy_name", "TEXT")
        _ensure_column(conn, "live_order_logs", "signal_reason", "TEXT")
        _ensure_column(conn, "live_order_logs", "candle_time_utc", "TEXT")
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
            INSERT OR IGNORE INTO candles (
                market, unit, candle_time_utc, candle_time_kst, opening_price,
                high_price, low_price, trade_price, candle_acc_trade_price,
                candle_acc_trade_volume, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO candidate_strategies (
                strategy, parameters_json, unit, market, backtest_period, score,
                backtest_total_return, backtest_mdd, backtest_win_rate,
                backtest_profit_factor, backtest_trade_count,
                backtest_average_trade_pnl, warning
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
        return int(cursor.lastrowid)


def load_candidate_strategies(limit: int = 50) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM candidate_strategies
            ORDER BY created_at DESC, id DESC
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
            WHERE status = 'RUNNING'
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
            WHERE status = 'RUNNING'
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
                request_id, exchange, market, side, order_type, price, volume, amount_krw,
                fee_estimate, risk_result, order_preview_payload,
                exchange_request_payload_masked, exchange_response_payload,
                status, error_message, order_uuid, strategy_name, signal_reason,
                candle_time_utc, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                log["request_id"],
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
                order_uuid = ?,
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
                merged.get("order_uuid"),
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


def load_live_order_logs(limit: int = 100) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM live_order_logs
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


def _normalize_live_order_log(row: dict) -> dict:
    row["exchange"] = row.get("exchange") or "upbit"
    row["order_preview_payload"] = json.loads(row.get("order_preview_payload") or "{}")
    row["exchange_request_payload_masked"] = json.loads(row.get("exchange_request_payload_masked") or "{}")
    row["exchange_response_payload"] = json.loads(row.get("exchange_response_payload") or "{}")
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
              AND request_id NOT LIKE '%-submitted'
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
              AND status IN ('SUBMITTED', 'WAITING', 'CANCELED', 'FILLED')
              AND request_id NOT LIKE '%-submitted'
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
              AND status IN ('SUBMITTED', 'WAITING')
              AND request_id NOT LIKE '%-submitted'
              AND request_id NOT LIKE '%-waiting-%'
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
