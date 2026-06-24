from __future__ import annotations

from typing import Any

from app.database import (
    get_connection,
    has_unresolved_live_order,
    has_unresolved_live_order_for_exchange,
    load_adaptive_edge_stats,
    load_live_eligible_candidate_strategies,
    load_market_universe_item,
    load_open_live_positions,
    load_open_live_positions_for_exchange,
    market_is_live_allowed,
)


def build_market_opportunity_rankings(
    *,
    exchange: str = "bithumb",
    candidates: list[dict] | None = None,
    snapshot: dict | None = None,
    limit: int = 10,
) -> dict:
    ranked = rank_live_candidates(exchange=exchange, candidates=candidates, snapshot=snapshot, limit=max(limit, 1))
    top_markets: list[dict] = []
    seen_markets: set[str] = set()
    for item in ranked:
        market = str(item.get("market") or "")
        if market in seen_markets:
            continue
        seen_markets.add(market)
        top_markets.append(
            {
                "market": market,
                "score": item.get("market_opportunity_score", 0.0),
                "candidate_strategy_id": item.get("id"),
                "strategy": item.get("strategy"),
                "eligible_for_allocation": item.get("eligible_for_allocation", False),
                "blockers": item.get("opportunity_blockers", []),
                "recommended_action": item.get("recommended_action", "OBSERVE"),
                "expected_order_amount_krw": item.get("expected_order_amount_krw", 0.0),
                "score_breakdown": item.get("opportunity_score_breakdown", {}),
            }
        )
        if len(top_markets) >= limit:
            break
    return {
        "exchange": exchange,
        "top_markets": top_markets,
        "top_candidates": ranked[:limit],
        "candidate_count": len(ranked),
    }


def rank_live_candidates(
    *,
    exchange: str = "bithumb",
    candidates: list[dict] | None = None,
    snapshot: dict | None = None,
    limit: int = 10,
) -> list[dict]:
    candidates = candidates if candidates is not None else load_live_eligible_candidate_strategies(100)
    open_markets = _open_markets(exchange, snapshot)
    ranked = [enrich_candidate_opportunity(candidate, exchange=exchange, snapshot=snapshot, open_markets=open_markets) for candidate in candidates]
    ranked.sort(
        key=lambda item: (
            1 if item.get("eligible_for_allocation") else 0,
            float(item.get("market_opportunity_score") or 0.0),
            float(item.get("score") or 0.0),
            -int(item.get("id") or 0),
        ),
        reverse=True,
    )
    return ranked[: max(int(limit), 1)] if limit else ranked


def enrich_candidate_opportunity(
    candidate: dict,
    *,
    exchange: str = "bithumb",
    snapshot: dict | None = None,
    open_markets: set[str] | None = None,
) -> dict:
    open_markets = open_markets if open_markets is not None else _open_markets(exchange, snapshot)
    market = str(candidate.get("market") or "")
    market_item = load_market_universe_item(exchange, market) or {}
    adaptive = _best_adaptive_edge(exchange, candidate)
    recent = _recent_performance(exchange, candidate)
    blockers = explain_candidate_blockers(candidate, exchange=exchange, snapshot=snapshot, open_markets=open_markets, market_item=market_item)
    breakdown = _score_breakdown(candidate, market_item, adaptive, recent, blockers)
    score = round(max(0.0, min(100.0, sum(breakdown.values()))), 6)
    eligible = not blockers
    expected_order = _expected_order_amount(score, snapshot)
    if not eligible:
        action = "BLOCKED"
    elif score >= 75:
        action = "ALLOCATE"
    elif score >= 55:
        action = "WATCH"
    else:
        action = "OBSERVE"
    return {
        **candidate,
        "market_opportunity_score": score,
        "opportunity_score_breakdown": breakdown,
        "opportunity_blockers": blockers,
        "eligible_for_allocation": eligible,
        "recommended_action": action,
        "expected_order_amount_krw": expected_order if eligible else 0.0,
        "adaptive_edge_score": _float(adaptive.get("edge_score")),
        "adaptive_edge_confidence": _float(adaptive.get("confidence_score")),
        "execution_quality_score": _execution_quality_score(adaptive),
        "recent_realized_pnl_krw": _float(recent.get("realized_pnl_krw")),
        "recent_realized_return_pct": _float(recent.get("realized_return_pct")),
        "opportunity_context": {
            "market_universe": market_item,
            "adaptive_edge_stat": adaptive,
            "recent_performance": recent,
        },
    }


def explain_candidate_blockers(
    candidate: dict,
    *,
    exchange: str = "bithumb",
    snapshot: dict | None = None,
    open_markets: set[str] | None = None,
    market_item: dict | None = None,
) -> list[str]:
    blockers: list[str] = []
    market = str(candidate.get("market") or "")
    market_item = market_item if market_item is not None else load_market_universe_item(exchange, market) or {}
    open_markets = open_markets if open_markets is not None else _open_markets(exchange, snapshot)
    if market in open_markets or load_open_live_positions(exchange, market):
        blockers.append("BLOCKED_DUPLICATE_MARKET_POSITION")
    if snapshot and snapshot.get("open_order_mismatch_detected"):
        blockers.append("BLOCKED_OPEN_ORDER_MISMATCH")
    if snapshot and snapshot.get("balance_mismatch_detected"):
        blockers.append("BLOCKED_BALANCE_MISMATCH")
    if has_unresolved_live_order(exchange, market) or has_unresolved_live_order_for_exchange(exchange):
        blockers.append("UNRESOLVED_OPEN_ORDER")
    if not market_is_live_allowed(exchange, market):
        blockers.append("MARKET_NOT_LIVE_ALLOWED")
    if market_item and not bool(market_item.get("is_auto_selectable", True)):
        blockers.append("MARKET_NOT_AUTO_SELECTABLE")
    if market_item and _float(market_item.get("last_liquidity_score")) < 10:
        blockers.append("LOW_LIQUIDITY")
    return _dedupe(blockers)


def _score_breakdown(candidate: dict, market_item: dict, adaptive: dict, recent: dict, blockers: list[str]) -> dict[str, float]:
    candidate_score = _float(candidate.get("score"))
    adaptive_edge = _float(adaptive.get("edge_score"))
    adaptive_confidence = _float(adaptive.get("confidence_score"))
    adaptive_weight = min(adaptive_confidence / 100.0, 1.0)
    liquidity = _float(market_item.get("last_liquidity_score"))
    volatility = _float(market_item.get("last_volatility_score"))
    market_risk = _float(market_item.get("last_risk_score"))
    execution_score = _execution_quality_score(adaptive)
    realized_return = _float(recent.get("realized_return_pct"))
    realized_pnl = _float(recent.get("realized_pnl_krw"))
    recent_loss_penalty = abs(min(realized_return, 0.0)) * 2.0 + (5.0 if realized_pnl < 0 else 0.0)
    duplicate_penalty = 25.0 if "BLOCKED_DUPLICATE_MARKET_POSITION" in blockers else 0.0
    unresolved_penalty = 20.0 if "UNRESOLVED_OPEN_ORDER" in blockers or "BLOCKED_OPEN_ORDER_MISMATCH" in blockers else 0.0
    liquidity_penalty = 15.0 if "LOW_LIQUIDITY" in blockers else 0.0
    return {
        "candidate_score_component": candidate_score * 0.42,
        "adaptive_edge_component": max(min(adaptive_edge * 10.0, 25.0), -25.0) * adaptive_weight,
        "recent_performance_component": max(min(realized_return * 4.0, 15.0), -15.0),
        "liquidity_component": liquidity * 0.12,
        "volatility_quality_component": max(0.0, 100.0 - abs(volatility - 45.0)) * 0.08 if volatility else 0.0,
        "execution_quality_component": execution_score * 0.08,
        "market_risk_penalty": -market_risk * 0.08,
        "spread_slippage_penalty": -max(_float(adaptive.get("avg_slippage_pct")), 0.0) * 2.0,
        "adverse_selection_penalty": -max(_float(adaptive.get("avg_adverse_selection_pct")), 0.0) * 2.0,
        "recent_loss_penalty": -recent_loss_penalty,
        "duplicate_exposure_penalty": -duplicate_penalty,
        "unresolved_order_penalty": -unresolved_penalty,
        "liquidity_penalty": -liquidity_penalty,
    }


def _best_adaptive_edge(exchange: str, candidate: dict) -> dict:
    rows = load_adaptive_edge_stats(
        exchange=exchange,
        market=str(candidate.get("market") or ""),
        strategy_name=str(candidate.get("strategy") or ""),
        candidate_strategy_id=int(candidate.get("id") or 0),
        unit=int(candidate.get("unit") or 0),
        limit=1,
    )
    if rows:
        return rows[0]
    rows = load_adaptive_edge_stats(
        exchange=exchange,
        market=str(candidate.get("market") or ""),
        strategy_name=str(candidate.get("strategy") or ""),
        limit=1,
    )
    return rows[0] if rows else {}


def _recent_performance(exchange: str, candidate: dict) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
                COALESCE(SUM(realized_pnl_krw), 0) AS realized_pnl_krw,
                COALESCE(AVG(realized_return_pct), 0) AS realized_return_pct,
                COUNT(*) AS sample_count
            FROM trade_outcome_logs
            WHERE exchange = ?
              AND market = ?
              AND strategy_name = ?
              AND candidate_strategy_id = ?
              AND outcome_status = 'REALIZED'
            """,
            (
                exchange,
                str(candidate.get("market") or ""),
                str(candidate.get("strategy") or ""),
                int(candidate.get("id") or 0),
            ),
        ).fetchone()
    return dict(row) if row else {"realized_pnl_krw": 0.0, "realized_return_pct": 0.0, "sample_count": 0}


def _execution_quality_score(adaptive: dict) -> float:
    slippage = max(_float(adaptive.get("avg_slippage_pct")), 0.0)
    adverse = max(_float(adaptive.get("avg_adverse_selection_pct")), 0.0)
    fill_time = max(_float(adaptive.get("avg_fill_time_seconds")), 0.0)
    return round(max(0.0, 100.0 - slippage * 12.0 - adverse * 10.0 - min(fill_time / 10.0, 20.0)), 6)


def _expected_order_amount(score: float, snapshot: dict | None) -> float:
    if not snapshot:
        return 0.0
    available = _float(snapshot.get("available_budget_krw"))
    remaining = _float(snapshot.get("remaining_exposure_krw"))
    if available <= 0 or remaining <= 0:
        return 0.0
    return round(min(available, remaining) * min(max(score, 0.0), 100.0) / 100.0, 6)


def _open_markets(exchange: str, snapshot: dict | None) -> set[str]:
    positions = (snapshot or {}).get("positions")
    if positions is None:
        positions = load_open_live_positions_for_exchange(exchange)
    return {str(position.get("market") or "") for position in positions if position.get("market")}


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
