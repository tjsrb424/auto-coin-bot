from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from app.database import (
    find_adaptive_edge_stat,
    get_connection,
    load_adaptive_edge_stats,
    upsert_adaptive_edge_stat,
)

COMPLETED_OUTCOME_STATUSES = {"PENDING_REALIZED", "POST_FILL_COMPLETE", "REALIZED"}


def adaptive_edge_enabled() -> bool:
    return str(os.getenv("SMART_ADAPTIVE_EDGE_ENABLED", "true")).strip().lower() not in {"0", "false", "no", "off"}


def adaptive_edge_mode() -> str:
    mode = str(os.getenv("SMART_ADAPTIVE_EDGE_MODE", "shadow")).strip().lower()
    return mode if mode in {"shadow", "block", "sizing"} else "shadow"


def refresh_adaptive_edge_stats(*, exchange: str = "bithumb", market: str | None = None) -> dict:
    rows = _load_completed_outcomes(exchange=exchange, market=market)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        if not _has_usable_outcome(row):
            continue
        groups[_group_key(row)].append(row)

    updated = 0
    for key, group_rows in groups.items():
        payload = _build_group_stat(key, group_rows)
        upsert_adaptive_edge_stat(payload)
        updated += 1
    return {"source_outcomes": len(rows), "groups_updated": updated}


def build_adaptive_edge_preview(context: dict) -> dict:
    if not adaptive_edge_enabled():
        return {
            "enabled": False,
            "mode": adaptive_edge_mode(),
            "shadow_only": True,
            "edge_status": "DISABLED",
            "adaptive_edge_score": 0.0,
            "edge_confidence": 0.0,
            "sample_count": 0,
        }
    normalized = _normalize_context(context)
    stat = find_adaptive_edge_stat(normalized)
    match_level = "exact"
    if stat is None:
        candidates = load_adaptive_edge_stats(
            exchange=str(normalized["exchange"]),
            market=str(normalized["market"]),
            strategy_name=str(normalized["strategy_name"]),
            candidate_strategy_id=int(normalized["candidate_strategy_id"]),
            unit=int(normalized["unit"]),
            limit=1,
        )
        stat = candidates[0] if candidates else None
        match_level = "candidate"
    if stat is None:
        candidates = load_adaptive_edge_stats(
            exchange=str(normalized["exchange"]),
            market=str(normalized["market"]),
            strategy_name=str(normalized["strategy_name"]),
            limit=1,
        )
        stat = candidates[0] if candidates else None
        match_level = "strategy"
    if stat is None:
        return {
            "enabled": True,
            "mode": adaptive_edge_mode(),
            "shadow_only": True,
            "edge_status": "NO_SAMPLES",
            "adaptive_edge_score": 0.0,
            "edge_confidence": 0.0,
            "sample_count": 0,
            "match_level": "none",
            "context": normalized,
        }

    confidence = _float(stat.get("confidence_score"))
    edge_score = _float(stat.get("edge_score"))
    sample_count = int(stat.get("sample_count") or 0)
    edge_status = "INSUFFICIENT_SAMPLE"
    if confidence >= 10 and edge_score > 0:
        edge_status = "POSITIVE_EDGE"
    elif confidence >= 10 and edge_score < 0:
        edge_status = "NEGATIVE_EDGE"
    elif confidence >= 10:
        edge_status = "NEUTRAL_EDGE"
    blocker_preview = "SMART_ADAPTIVE_EDGE_NEGATIVE" if confidence >= 60 and edge_score < -0.25 else None
    return {
        "enabled": True,
        "mode": adaptive_edge_mode(),
        "shadow_only": adaptive_edge_mode() == "shadow",
        "edge_status": edge_status,
        "adaptive_edge_score": round(edge_score, 6),
        "edge_confidence": round(confidence, 6),
        "sample_count": sample_count,
        "win_rate": _float(stat.get("win_rate")),
        "profit_factor": _float(stat.get("profit_factor")),
        "avg_post_fill_return_5m": _float(stat.get("avg_post_fill_return_5m")),
        "avg_post_fill_return_15m": _float(stat.get("avg_post_fill_return_15m")),
        "avg_realized_return_pct": _float(stat.get("avg_realized_return_pct")),
        "avg_adverse_selection_pct": _float(stat.get("avg_adverse_selection_pct")),
        "avg_slippage_pct": _float(stat.get("avg_slippage_pct")),
        "max_drawdown_pct": _float(stat.get("max_drawdown_pct")),
        "blocker_preview": blocker_preview,
        "match_level": match_level,
        "stat_id": stat.get("id"),
        "last_updated_at": stat.get("last_updated_at"),
        "context": normalized,
    }


def attach_adaptive_edge_preview(
    *,
    intent: dict,
    snapshot: dict,
    candidate: dict | None = None,
    order_purpose: str = "ENTRY",
) -> dict:
    context = {
        "exchange": snapshot.get("exchange", "bithumb"),
        "market": snapshot.get("market", "KRW-BTC"),
        "strategy_name": snapshot.get("selected_strategy_type") or snapshot.get("selected_strategy_name") or (candidate or {}).get("strategy") or "",
        "candidate_strategy_id": snapshot.get("selected_strategy_id") or (candidate or {}).get("id") or 0,
        "unit": (candidate or {}).get("unit") or _unit_from_timeframe(snapshot.get("timeframe")),
        "market_regime": snapshot.get("market_regime", ""),
        "action_hint": intent.get("action_hint") or snapshot.get("action_hint") or "",
        "legacy_signal": snapshot.get("legacy_signal", ""),
        "attack_mode": intent.get("attack_mode") or snapshot.get("attack_mode") or "",
        "target_source": intent.get("target_source") or snapshot.get("final_target_exposure_source") or "",
        "order_purpose": order_purpose,
    }
    preview = build_adaptive_edge_preview(context)
    intent["policy_preview"] = {
        **(intent.get("policy_preview") or {}),
        "adaptive_edge": preview,
        "adaptive_edge_score": preview.get("adaptive_edge_score", 0.0),
        "edge_confidence": preview.get("edge_confidence", 0.0),
    }
    return intent


def _load_completed_outcomes(*, exchange: str, market: str | None) -> list[dict]:
    clauses = ["outcome.exchange = ?", f"outcome.outcome_status IN ({','.join('?' for _ in COMPLETED_OUTCOME_STATUSES)})"]
    params: list[object] = [exchange, *sorted(COMPLETED_OUTCOME_STATUSES)]
    if market:
        clauses.append("outcome.market = ?")
        params.append(market)
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT
                outcome.*,
                COALESCE(candidate.unit, 0) AS strategy_unit
            FROM trade_outcome_logs AS outcome
            LEFT JOIN candidate_strategies AS candidate
              ON candidate.id = outcome.candidate_strategy_id
            WHERE {" AND ".join(clauses)}
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def _build_group_stat(key: tuple, rows: list[dict]) -> dict:
    sample_count = len(rows)
    returns = [_outcome_return(row) for row in rows]
    wins = [value for value in returns if value is not None and value > 0]
    losses = [value for value in returns if value is not None and value < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else gross_profit if gross_profit > 0 else 0.0
    win_count = len(wins)
    loss_count = len(losses)
    confidence = _confidence_score(sample_count)
    max_drawdown = min([value for value in returns if value is not None] or [0.0])
    stat = {
        "exchange": key[0],
        "market": key[1],
        "strategy_name": key[2],
        "candidate_strategy_id": key[3],
        "unit": key[4],
        "market_regime": key[5],
        "action_hint": key[6],
        "legacy_signal": key[7],
        "attack_mode": key[8],
        "target_source": key[9],
        "order_purpose": key[10],
        "sample_count": sample_count,
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate": win_count / sample_count if sample_count else 0.0,
        "avg_post_fill_return_1m": _avg(row.get("post_fill_return_1m") for row in rows),
        "avg_post_fill_return_5m": _avg(row.get("post_fill_return_5m") for row in rows),
        "avg_post_fill_return_15m": _avg(row.get("post_fill_return_15m") for row in rows),
        "avg_realized_return_pct": _avg(row.get("realized_return_pct") for row in rows),
        "avg_realized_pnl_krw": _avg(row.get("realized_pnl_krw") for row in rows),
        "profit_factor": round(profit_factor, 6),
        "avg_adverse_selection_pct": _avg(row.get("adverse_selection_pct") for row in rows),
        "avg_slippage_pct": _avg(row.get("slippage_pct") for row in rows),
        "avg_fill_time_seconds": _avg(row.get("fill_time_seconds") for row in rows),
        "max_drawdown_pct": round(max_drawdown, 6),
        "confidence_score": confidence,
        "last_updated_at": _utc_now(),
    }
    stat["edge_score"] = _edge_score(stat)
    return stat


def _edge_score(stat: dict) -> float:
    win_rate_component = (_float(stat.get("win_rate")) - 0.5) * 2.0
    profit_factor_component = min(max(_float(stat.get("profit_factor")) - 1.0, -1.0), 2.0) * 0.5
    raw = (
        _float(stat.get("avg_post_fill_return_5m")) * 0.35
        + _float(stat.get("avg_post_fill_return_15m")) * 0.25
        + _float(stat.get("avg_realized_return_pct")) * 0.30
        + win_rate_component
        + profit_factor_component
        - max(_float(stat.get("avg_adverse_selection_pct")), 0.0) * 0.30
        - max(_float(stat.get("avg_slippage_pct")), 0.0) * 0.20
        + min(_float(stat.get("max_drawdown_pct")), 0.0) * 0.20
    )
    confidence_multiplier = _float(stat.get("confidence_score")) / 100.0
    return round(raw * confidence_multiplier, 6)


def _group_key(row: dict) -> tuple:
    return (
        str(row.get("exchange") or "bithumb"),
        str(row.get("market") or "KRW-BTC"),
        str(row.get("strategy_name") or ""),
        int(row.get("candidate_strategy_id") or 0),
        int(row.get("strategy_unit") or 0),
        str(row.get("market_regime") or ""),
        str(row.get("action_hint") or ""),
        str(row.get("legacy_signal") or ""),
        str(row.get("attack_mode") or ""),
        str(row.get("target_source") or ""),
        str(row.get("order_purpose") or ""),
    )


def _normalize_context(context: dict) -> dict:
    return {
        "exchange": str(context.get("exchange") or "bithumb"),
        "market": str(context.get("market") or "KRW-BTC"),
        "strategy_name": str(context.get("strategy_name") or ""),
        "candidate_strategy_id": int(context.get("candidate_strategy_id") or 0),
        "unit": int(context.get("unit") or 0),
        "market_regime": str(context.get("market_regime") or ""),
        "action_hint": str(context.get("action_hint") or ""),
        "legacy_signal": str(context.get("legacy_signal") or ""),
        "attack_mode": str(context.get("attack_mode") or ""),
        "target_source": str(context.get("target_source") or ""),
        "order_purpose": str(context.get("order_purpose") or ""),
    }


def _outcome_return(row: dict) -> float | None:
    for key in ("realized_return_pct", "post_fill_return_15m", "post_fill_return_5m", "post_fill_return_1m"):
        value = _float(row.get(key), None)
        if value is not None:
            return value
    return None


def _has_usable_outcome(row: dict) -> bool:
    return _outcome_return(row) is not None


def _confidence_score(sample_count: int) -> float:
    if sample_count <= 0:
        return 0.0
    if sample_count <= 5:
        return min(sample_count * 2.0, 10.0)
    if sample_count <= 20:
        return 10.0 + (sample_count - 5) / 15.0 * 20.0
    if sample_count <= 50:
        return 30.0 + (sample_count - 20) / 30.0 * 35.0
    return min(65.0 + (sample_count - 50) / 50.0 * 35.0, 100.0)


def _unit_from_timeframe(value: Any) -> int:
    text = str(value or "").strip().lower().removesuffix("m")
    try:
        return int(text)
    except ValueError:
        return 0


def _avg(values) -> float:
    nums = [_float(value, None) for value in values]
    nums = [value for value in nums if value is not None]
    return round(sum(nums) / len(nums), 6) if nums else 0.0


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
