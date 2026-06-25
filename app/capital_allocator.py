from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

from app.aggression_presets import runtime_setting_float, runtime_setting_int
from app.capital_snapshot import build_capital_snapshot
from app.database import (
    acquire_scheduler_task_lock,
    create_capital_allocation_run,
    create_live_strategy_session,
    create_order_reservation,
    enqueue_next_entry,
    finish_capital_allocation_run,
    finish_scheduler_task,
    has_unresolved_live_order,
    has_unresolved_live_order_for_exchange,
    insert_capital_allocation_decision,
    load_active_order_reservations,
    load_candidate_strategy,
    load_global_bot_operation_policy,
    load_live_eligible_candidate_strategies,
    load_live_strategy_session,
    load_next_entry_queue,
    load_open_live_positions,
    load_open_live_positions_for_exchange,
    load_position_slots,
    market_is_live_allowed,
    promote_candidate_strategy,
    reconcile_position_slots,
    record_strategy_switch,
    reserve_position_slot,
    save_active_strategy_selection,
    update_next_entry_status,
    update_order_reservation_status,
    update_live_strategy_session,
)
from app.live_broker import is_emergency_stopped
from app.market_opportunity import build_market_opportunity_rankings, rank_live_candidates
from app.live_state_reconciler import (
    reconcile_expired_order_reservations,
    reconcile_mismatched_position_slot_sessions,
    reconcile_orphan_live_active_candidates,
    reconcile_reserved_entry_blocked_slots,
    reconcile_reserved_slot_session_pointer,
    reconcile_stale_live_strategy_sessions,
)

ALLOCATOR_TASK = "capital_allocator"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _float_env(name: str, default: float, *, minimum: float = 0.0) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, value)


def allocator_config() -> dict:
    return {
        "enabled": _bool_env("AUTO_CAPITAL_ALLOCATOR_ENABLED", True),
        "exchange": os.getenv("AUTO_ALLOWED_EXCHANGE", os.getenv("EXCHANGE", "bithumb")).strip().lower(),
        "max_slots": _int_env("AUTO_MAX_OPEN_POSITION_COUNT", 5, minimum=1, maximum=20),
        "max_new_entries_per_run": max(min(runtime_setting_int("AUTO_MAX_NEW_ENTRIES_PER_TICK", 2), 10), 1),
        "single_position_max_exposure_pct": runtime_setting_float("AUTO_SINGLE_POSITION_MAX_EXPOSURE_PCT", 45.0),
        "cash_reserve_pct": _float_env("AUTO_CASH_RESERVE_PCT", 5.0),
        "min_expected_edge_pct": _float_env("AUTO_MIN_EXPECTED_EDGE_PCT", 0.45),
        "fee_rate": _float_env("AUTO_FEE_RATE", 0.0005),
        "slippage_rate": _float_env("AUTO_SLIPPAGE_RATE", 0.0005),
        "edge_safety_margin_pct": _float_env("AUTO_EDGE_SAFETY_MARGIN_PCT", 0.15),
        "max_order_krw": runtime_setting_float("AUTO_MAX_ORDER_KRW", 30000.0),
        "min_order_krw": _float_env("AUTO_MIN_ORDER_KRW", 5000.0),
        "reservation_ttl_minutes": _int_env("AUTO_ORDER_RESERVATION_TTL_MINUTES", 30, minimum=1, maximum=1440),
        "queue_ttl_minutes": _int_env("AUTO_NEXT_ENTRY_TTL_MINUTES", 360, minimum=5, maximum=10080),
        "lock_ttl_seconds": _int_env("AUTO_CAPITAL_ALLOCATOR_LOCK_TTL_SECONDS", 300, minimum=30, maximum=1800),
        "same_market_replace_min_score_delta": _float_env(
            "AUTO_ALLOCATOR_REPLACE_MIN_SCORE_DELTA",
            _float_env("AUTO_SELECTOR_MIN_SCORE_DELTA", 10.0),
        ),
    }


def _position_value(position: dict) -> float:
    current_value = float(position.get("current_value_krw") or 0.0)
    if current_value > 0:
        return current_value
    return float(position.get("current_price") or 0.0) * float(position.get("entry_volume") or 0.0)


def _expected_edge_pct(candidate: dict) -> float:
    total_return = float(candidate.get("backtest_total_return") or 0.0)
    if total_return:
        return total_return * 100
    return max(float(candidate.get("score") or 0.0) / 100, 0.0)


def required_edge_pct(config: dict) -> float:
    round_trip_fee = float(config["fee_rate"]) * 2 * 100
    slippage = float(config["slippage_rate"]) * 2 * 100
    return max(
        float(config["min_expected_edge_pct"]),
        round_trip_fee + slippage + float(config["edge_safety_margin_pct"]),
    )


def allocation_score(candidate: dict, config: dict | None = None) -> float:
    config = config or allocator_config()
    score = float(candidate.get("score") or 0.0)
    expected_edge = _expected_edge_pct(candidate)
    mdd = float(candidate.get("backtest_mdd") or 0.0) * 100
    trade_count = min(float(candidate.get("backtest_trade_count") or 0.0), 50.0)
    edge_bonus = max(expected_edge - required_edge_pct(config), 0.0) * 1.5
    mdd_penalty = max(mdd - 10.0, 0.0) * 1.2
    trade_bonus = trade_count * 0.2
    opportunity = candidate.get("market_opportunity_score")
    opportunity_adjustment = 0.0
    if opportunity is not None:
        opportunity_adjustment = (float(opportunity or 0.0) - 50.0) * 0.5
    return max(0.0, score + edge_bonus + trade_bonus + opportunity_adjustment - mdd_penalty)


def capital_allocator_status(exchange: str | None = None) -> dict:
    config = allocator_config()
    exchange = exchange or str(config["exchange"])
    snapshot = build_capital_snapshot(exchange)
    slots = snapshot.get("slots") or load_position_slots(int(config["max_slots"]), exchange)
    reservations = snapshot.get("reservations") or load_active_order_reservations(exchange)
    candidates = load_live_eligible_candidate_strategies(100)
    opportunity_rankings = build_market_opportunity_rankings(exchange=exchange, candidates=candidates, snapshot=snapshot, limit=10)
    return {
        "enabled": bool(config["enabled"]),
        "exchange": exchange,
        "policy": load_global_bot_operation_policy(),
        "max_slots": int(config["max_slots"]),
        "open_slot_count": len([slot for slot in slots if str(slot.get("status")) != "EMPTY"]),
        "empty_slot_count": len([slot for slot in slots if str(slot.get("status")) == "EMPTY"]),
        "max_total_exposure_krw": snapshot.get("max_total_exposure_krw", 0.0),
        "current_open_position_value_krw": snapshot.get("db_open_position_value_krw", 0.0),
        "db_open_position_value_krw": snapshot.get("db_open_position_value_krw", 0.0),
        "exchange_position_value_krw": snapshot.get("exchange_position_value_krw", 0.0),
        "pending_buy_reserved_krw": snapshot.get("pending_buy_reserved_krw", 0.0),
        "pending_exchange_buy_order_krw": snapshot.get("pending_exchange_buy_order_krw", 0.0),
        "available_krw_balance": snapshot.get("available_krw_balance"),
        "available_budget_krw": snapshot.get("available_budget_krw", 0.0),
        "remaining_exposure_krw": snapshot.get("remaining_exposure_krw", 0.0),
        "cash_reserve_krw": snapshot.get("cash_reserve_krw", 0.0),
        "balance_mismatch_detected": bool(snapshot.get("balance_mismatch_detected")),
        "open_order_mismatch_detected": bool(snapshot.get("open_order_mismatch_detected")),
        "snapshot_created_at": snapshot.get("created_at"),
        "snapshot_error": snapshot.get("snapshot_error", ""),
        "snapshot_warnings": snapshot.get("warnings", []),
        "snapshot_blockers": snapshot.get("blockers", []),
        "slots": slots,
        "reservations": reservations,
        "next_entry_queue": load_next_entry_queue(20, ["QUEUED", "BLOCKED"]),
        "required_edge_pct": required_edge_pct(config),
        "market_opportunity_rankings": opportunity_rankings,
    }


def _candidate_block_reason(candidate: dict, exchange: str, open_markets: set[str], config: dict) -> str:
    market = str(candidate.get("market") or "")
    if market in open_markets:
        return "BLOCKED_DUPLICATE_MARKET_POSITION"
    if load_open_live_positions(exchange, market):
        return "BLOCKED_DUPLICATE_MARKET_POSITION"
    if has_unresolved_live_order(exchange, market) or has_unresolved_live_order_for_exchange(exchange):
        return "UNRESOLVED_OPEN_ORDER"
    if not market_is_live_allowed(exchange, market):
        return "MARKET_NOT_LIVE_ALLOWED"
    if _expected_edge_pct(candidate) < required_edge_pct(config):
        return "BLOCKED_EXPECTED_EDGE_BELOW_COST"
    return ""


def _same_market_reserved_slot(candidate: dict, slots: list[dict], config: dict) -> tuple[dict | None, str]:
    market = str(candidate.get("market") or "")
    candidate_id = int(candidate.get("id") or 0)
    candidate_score = float(candidate.get("score") or 0.0)
    min_delta = float(config.get("same_market_replace_min_score_delta") or 0.0)
    for slot in slots:
        if str(slot.get("market") or "") != market:
            continue
        if str(slot.get("status") or "").upper() != "RESERVED" or slot.get("live_position_id"):
            return None, "BLOCKED_DUPLICATE_MARKET_POSITION"
        existing_candidate_id = int(slot.get("candidate_strategy_id") or 0)
        if existing_candidate_id == candidate_id:
            return None, "SAME_MARKET_CANDIDATE_ALREADY_RESERVED"
        existing = load_candidate_strategy(existing_candidate_id) if existing_candidate_id else None
        existing_score = float((existing or {}).get("score") or 0.0)
        if existing and candidate_score - existing_score < min_delta:
            return None, "SAME_MARKET_REPLACE_SCORE_DELTA_TOO_SMALL"
        return slot, ""
    return None, "NO_SAME_MARKET_RESERVED_SLOT"


def _replace_reserved_slot_candidate(
    *,
    slot: dict,
    candidate: dict,
    approved_order: float,
    reason: str,
    config: dict,
) -> dict:
    now_utc = _utc_now()
    old_candidate_id = int(slot.get("candidate_strategy_id") or 0)
    old_candidate = load_candidate_strategy(old_candidate_id) if old_candidate_id else None
    old_session_id = int(slot.get("live_strategy_session_id") or 0)
    market = str(candidate["market"])
    if old_candidate_id:
        update_order_reservation_status(
            candidate_strategy_id=old_candidate_id,
            market=market,
            status="REPLACED",
            previous_statuses=["RESERVED"],
        )
        promote_candidate_strategy(
            old_candidate_id,
            "LIVE_ELIGIBLE",
            reason="Replaced by stronger same-market candidate before entry",
        )
    if old_session_id:
        update_live_strategy_session(
            old_session_id,
            {
                "status": "STOPPED",
                "auto_enabled": False,
                "last_risk_result": "REPLACED_BEFORE_ENTRY",
                "last_order_status": "REPLACED",
                "stopped_at": now_utc,
            },
        )
    session_id = _create_allocator_session(candidate=candidate, approved_order=approved_order, exchange=str(config["exchange"]))
    reservation_id = create_order_reservation(
        {
            "request_id": f"allocator-replace-{uuid.uuid4().hex[:16]}",
            "exchange": str(config["exchange"]),
            "market": market,
            "candidate_strategy_id": int(candidate["id"]),
            "slot_id": int(slot["id"]),
            "amount_krw": approved_order,
            "status": "RESERVED",
            "expires_at": (
                datetime.now(timezone.utc).replace(microsecond=0)
                + timedelta(minutes=int(config["reservation_ttl_minutes"]))
            ).isoformat().replace("+00:00", "Z"),
        }
    )
    updated_slot = reserve_position_slot(
        slot_id=int(slot["id"]),
        exchange=str(config["exchange"]),
        market=market,
        candidate_strategy_id=int(candidate["id"]),
        live_strategy_session_id=session_id,
        amount_krw=approved_order,
        reason=reason,
    )
    promote_candidate_strategy(int(candidate["id"]), "LIVE_ACTIVE", reason="Replaced same-market reserved slot")
    save_active_strategy_selection(candidate, reason="Replaced same-market reserved slot", replaced_candidate_strategy_id=old_candidate_id or None)
    record_strategy_switch(
        from_candidate_strategy_id=old_candidate_id or None,
        to_candidate_strategy_id=int(candidate["id"]),
        from_market=market,
        to_market=market,
        decision="APPLIED",
        reason="Replaced same-market reserved slot before entry",
        score_delta=float(candidate.get("score") or 0.0) - float((old_candidate or {}).get("score") or 0.0),
    )
    return {
        "slot": updated_slot,
        "session_id": session_id,
        "reservation_id": reservation_id,
        "replaced_candidate_strategy_id": old_candidate_id or None,
    }


def _create_allocator_session(*, candidate: dict, approved_order: float, exchange: str) -> int:
    session_id = create_live_strategy_session(
        {
            "exchange": exchange,
            "market": candidate["market"],
            "candidate_strategy_id": int(candidate["id"]),
            "strategy_name": candidate["strategy"],
            "strategy_parameters": candidate.get("parameters", {}),
            "status": "READY",
            "auto_enabled": True,
            "initial_balance_krw": 0.0,
            "max_order_krw": approved_order,
            "max_orders_per_day": _int_env("AUTO_MAX_ORDERS_PER_DAY", 3, minimum=1),
        }
    )
    session = load_live_strategy_session(session_id)
    if not session:
        raise RuntimeError(f"ALLOCATOR_SESSION_CREATE_MISSING:{session_id}")
    if str(session.get("market") or "") != str(candidate["market"]) or int(session.get("candidate_strategy_id") or 0) != int(
        candidate["id"]
    ):
        raise RuntimeError(
            "ALLOCATOR_SESSION_CANDIDATE_MISMATCH:"
            f"session_id={session_id}:session_market={session.get('market')}:"
            f"session_candidate={session.get('candidate_strategy_id')}:"
            f"candidate_market={candidate['market']}:candidate_id={candidate['id']}"
        )
    return session_id


def run_capital_allocator_once(reason: str = "SCHEDULED", *, exchange: str | None = None) -> dict:
    config = allocator_config()
    exchange = exchange or str(config["exchange"])
    acquired, current = acquire_scheduler_task_lock(
        ALLOCATOR_TASK,
        owner=reason,
        ttl_seconds=int(config["lock_ttl_seconds"]),
    )
    if not acquired:
        return {"task_name": ALLOCATOR_TASK, "status": "SKIPPED_LOCKED", "current": current}

    run_id = create_capital_allocation_run({"reason": reason, "status": "RUNNING", "started_at": _utc_now()})
    accepted: list[dict] = []
    blocked: list[dict] = []
    try:
        if not config["enabled"]:
            run = finish_capital_allocation_run(run_id, {"status": "SKIPPED", "error": "CAPITAL_ALLOCATOR_DISABLED"})
            finish_scheduler_task(
                ALLOCATOR_TASK,
                status="SKIPPED",
                result={"reason": reason, "skip_reason": "CAPITAL_ALLOCATOR_DISABLED", "run_id": run_id},
            )
            return {"ok": True, "run": run, "accepted": accepted, "blocked": blocked}
        if is_emergency_stopped():
            run = finish_capital_allocation_run(run_id, {"status": "SKIPPED", "error": "EMERGENCY_STOP_ACTIVE"})
            finish_scheduler_task(
                ALLOCATOR_TASK,
                status="SKIPPED",
                result={"reason": reason, "skip_reason": "EMERGENCY_STOP_ACTIVE", "run_id": run_id},
            )
            return {"ok": True, "run": run, "accepted": accepted, "blocked": blocked}

        policy = load_global_bot_operation_policy()
        stale_reconcile = reconcile_stale_live_strategy_sessions(dry_run=False)
        orphan_reconcile = reconcile_orphan_live_active_candidates(dry_run=False)
        mismatched_reconcile = reconcile_mismatched_position_slot_sessions(dry_run=False)
        expired_reconcile = reconcile_expired_order_reservations(dry_run=False)
        reserved_pointer_reconcile = reconcile_reserved_slot_session_pointer(dry_run=False)
        reserved_blocked_reconcile = reconcile_reserved_entry_blocked_slots(dry_run=False)
        reconcile_position_slots(int(config["max_slots"]), exchange)
        snapshot = build_capital_snapshot(exchange)
        slots = snapshot.get("slots") or reconcile_position_slots(int(config["max_slots"]), exchange)
        open_positions = snapshot.get("positions") or load_open_live_positions_for_exchange(exchange)
        open_markets = {str(item.get("market")) for item in open_positions}
        empty_slots = [slot for slot in slots if str(slot.get("status")) == "EMPTY"]
        candidates = [
            item
            for item in load_live_eligible_candidate_strategies(100)
            if str(item.get("status") or "").upper() == "LIVE_ELIGIBLE"
        ]
        candidates = rank_live_candidates(exchange=exchange, candidates=candidates, snapshot=snapshot, limit=100)
        candidates = sorted(candidates, key=lambda item: allocation_score(item, config), reverse=True)

        max_total = float(snapshot.get("max_total_exposure_krw") or policy.get("max_total_exposure_krw") or 0.0)
        current_exposure = float(snapshot.get("db_open_position_value_krw") or 0.0)
        pending_reserved = float(snapshot.get("pending_buy_reserved_krw") or 0.0)
        remaining = float(snapshot.get("remaining_exposure_krw") or 0.0)
        available_budget = float(snapshot.get("available_budget_krw") or 0.0)
        max_single = max_total * float(config["single_position_max_exposure_pct"]) / 100
        accepted_count = 0

        for candidate in candidates:
            if accepted_count >= int(config["max_new_entries_per_run"]):
                break
            if not policy.get("auto_trading_enabled"):
                queue_id = enqueue_next_entry(
                    candidate,
                    allocation_score=allocation_score(candidate, config),
                    blocked_reason="POLICY_AUTO_TRADING_DISABLED",
                    ttl_minutes=int(config["queue_ttl_minutes"]),
                )
                blocked.append({"candidate": candidate, "blocked_reason": "POLICY_AUTO_TRADING_DISABLED", "queue_id": queue_id})
                continue

            replacement_slot, same_market_block = _same_market_reserved_slot(candidate, slots, config)
            is_replacement = replacement_slot is not None
            if same_market_block != "NO_SAME_MARKET_RESERVED_SLOT" and not is_replacement:
                block_reason = same_market_block
            elif not is_replacement and not empty_slots:
                block_reason = "NO_EMPTY_SLOT"
            else:
                block_reason = _candidate_block_reason(candidate, exchange, open_markets, config)
            if not block_reason and snapshot.get("snapshot_error"):
                block_reason = "BLOCKED_SNAPSHOT_FAILED"
            if not block_reason and snapshot.get("balance_mismatch_detected"):
                block_reason = "BLOCKED_BALANCE_MISMATCH"
            if not block_reason and snapshot.get("open_order_mismatch_detected"):
                block_reason = "BLOCKED_OPEN_ORDER_MISMATCH"
            score = allocation_score(candidate, config)
            opportunity_breakdown = candidate.get("opportunity_score_breakdown") or {}
            opportunity_blockers = candidate.get("opportunity_blockers") or []
            desired_order = min(max_single, max(float(config["min_order_krw"]), max_single * min(score, 100.0) / 100))
            released_reserved = float((replacement_slot or {}).get("reserved_krw") or 0.0)
            if is_replacement:
                cash_after_reserve = max(float(snapshot.get("available_krw_balance") or 0.0) - float(snapshot.get("cash_reserve_krw") or 0.0), 0.0)
                budget_for_candidate = min(max(remaining + released_reserved, 0.0), cash_after_reserve)
            else:
                budget_for_candidate = available_budget
            approved_order = min(budget_for_candidate, desired_order, max_single, float(config["max_order_krw"]))
            if approved_order < float(config["min_order_krw"]) and not block_reason:
                if snapshot.get("available_krw_balance") is None:
                    block_reason = "BLOCKED_EXCHANGE_BALANCE_UNAVAILABLE"
                elif float(snapshot.get("available_krw_balance") or 0.0) < float(config["min_order_krw"]):
                    block_reason = "BLOCKED_INSUFFICIENT_KRW_BALANCE"
                elif (remaining + released_reserved if is_replacement else remaining) < float(config["min_order_krw"]):
                    block_reason = "BLOCKED_REMAINING_EXPOSURE_TOO_SMALL"
                else:
                    block_reason = "BLOCKED_CAPITAL_TOO_SMALL"

            insert_capital_allocation_decision(
                {
                    "run_id": run_id,
                    "candidate_strategy_id": int(candidate["id"]),
                    "market": candidate["market"],
                    "strategy": candidate["strategy"],
                    "allocation_score": score,
                    "desired_order_krw": desired_order,
                    "approved_order_krw": 0.0 if block_reason else approved_order,
                    "blocked_reason": block_reason,
                    "fee_rate": float(config["fee_rate"]),
                    "estimated_fee_krw": approved_order * float(config["fee_rate"]) * 2,
                    "estimated_slippage_krw": approved_order * float(config["slippage_rate"]) * 2,
                    "expected_edge_pct": _expected_edge_pct(candidate),
                    "required_edge_pct": required_edge_pct(config),
                    "decision": "BLOCKED" if block_reason else "ACCEPTED",
                }
            )

            if block_reason:
                queue_id = enqueue_next_entry(
                    candidate,
                    allocation_score=score,
                    blocked_reason=block_reason,
                    ttl_minutes=int(config["queue_ttl_minutes"]),
                )
                blocked.append(
                    {
                        "candidate": candidate,
                        "blocked_reason": block_reason,
                        "queue_id": queue_id,
                        "market_opportunity_score": candidate.get("market_opportunity_score", 0.0),
                        "opportunity_score_breakdown": opportunity_breakdown,
                        "opportunity_blockers": opportunity_blockers,
                    }
                )
                continue

            if is_replacement:
                replacement = _replace_reserved_slot_candidate(
                    slot=replacement_slot,
                    candidate=candidate,
                    approved_order=approved_order,
                    reason=reason,
                    config={**config, "exchange": exchange},
                )
                accepted_count += 1
                accepted.append(
                    {
                        "candidate": candidate,
                        "slot_id": int(replacement["slot"]["id"]),
                        "slot_number": int(replacement["slot"]["slot_number"]),
                        "session_id": replacement["session_id"],
                        "reservation_id": replacement["reservation_id"],
                        "approved_order_krw": approved_order,
                        "replaced_candidate_strategy_id": replacement.get("replaced_candidate_strategy_id"),
                        "market_opportunity_score": candidate.get("market_opportunity_score", 0.0),
                        "opportunity_score_breakdown": opportunity_breakdown,
                    }
                )
                update_next_entry_status(int(candidate["id"]), "PROMOTED_TO_SLOT")
                open_markets.add(str(candidate["market"]))
                available_budget = max(available_budget + released_reserved - approved_order, 0.0)
                slots = [replacement["slot"] if int(slot.get("id") or 0) == int(replacement["slot"]["id"]) else slot for slot in slots]
                continue

            slot = empty_slots.pop(0)
            session_id = _create_allocator_session(candidate=candidate, approved_order=approved_order, exchange=exchange)
            reservation_id = create_order_reservation(
                {
                    "request_id": f"allocator-{uuid.uuid4().hex[:24]}",
                    "exchange": exchange,
                    "market": candidate["market"],
                    "candidate_strategy_id": int(candidate["id"]),
                    "slot_id": int(slot["id"]),
                    "amount_krw": approved_order,
                    "status": "RESERVED",
                    "expires_at": (
                        datetime.now(timezone.utc).replace(microsecond=0)
                        + timedelta(minutes=int(config["reservation_ttl_minutes"]))
                    ).isoformat().replace("+00:00", "Z"),
                }
            )
            reserve_position_slot(
                slot_id=int(slot["id"]),
                exchange=exchange,
                market=candidate["market"],
                candidate_strategy_id=int(candidate["id"]),
                live_strategy_session_id=session_id,
                amount_krw=approved_order,
                reason=reason,
            )
            promote_candidate_strategy(int(candidate["id"]), "LIVE_ACTIVE", reason="Assigned to capital allocator slot")
            save_active_strategy_selection(candidate, reason="Latest allocator slot assignment")
            record_strategy_switch(
                from_candidate_strategy_id=None,
                to_candidate_strategy_id=int(candidate["id"]),
                from_market=None,
                to_market=str(candidate["market"]),
                decision="APPLIED",
                reason=f"Assigned to capital allocator slot {slot['slot_number']}",
                score_delta=float(candidate.get("score") or 0.0),
            )
            update_next_entry_status(int(candidate["id"]), "PROMOTED_TO_SLOT")
            accepted_count += 1
            accepted.append(
                {
                    "candidate": candidate,
                    "slot_id": int(slot["id"]),
                    "slot_number": int(slot["slot_number"]),
                    "session_id": session_id,
                    "reservation_id": reservation_id,
                    "approved_order_krw": approved_order,
                    "market_opportunity_score": candidate.get("market_opportunity_score", 0.0),
                    "opportunity_score_breakdown": opportunity_breakdown,
                }
            )
            open_markets.add(str(candidate["market"]))
            available_budget = max(available_budget - approved_order, 0.0)

        status = "COMPLETED" if not blocked else ("COMPLETED_WITH_BLOCKS" if accepted else "BLOCKED")
        run = finish_capital_allocation_run(
            run_id,
            {
                "status": status,
                "max_total_exposure_krw": max_total,
                "current_exposure_krw": current_exposure,
                "pending_reserved_krw": pending_reserved,
                "available_krw_balance": snapshot.get("available_krw_balance"),
                "remaining_exposure_krw": remaining,
                "empty_slot_count": len(empty_slots),
                "candidate_count": len(candidates),
                "accepted_count": len(accepted),
                "blocked_count": len(blocked),
            },
        )
        finish_scheduler_task(
            ALLOCATOR_TASK,
            status=status,
            result={
                "reason": reason,
                "run_id": run_id,
                "candidate_count": len(candidates),
                "accepted_count": len(accepted),
                "blocked_count": len(blocked),
                "empty_slot_count": len(empty_slots),
                "snapshot_error": snapshot.get("snapshot_error", ""),
                "stale_session_reconcile": stale_reconcile,
                "orphan_live_active_reconcile": orphan_reconcile,
                "mismatched_position_slot_reconcile": mismatched_reconcile,
                "expired_order_reservation_reconcile": expired_reconcile,
                "reserved_slot_session_pointer_reconcile": reserved_pointer_reconcile,
                "reserved_entry_blocked_slot_reconcile": reserved_blocked_reconcile,
            },
        )
        return {"ok": True, "run": run, "accepted": accepted, "blocked": blocked, "status": capital_allocator_status(exchange)}
    except Exception as exc:
        run = finish_capital_allocation_run(run_id, {"status": "FAILED", "error": str(exc)})
        finish_scheduler_task(
            ALLOCATOR_TASK,
            status="FAILED",
            result={"reason": reason, "run_id": run_id, "error_type": exc.__class__.__name__},
            error=str(exc),
        )
        return {"ok": False, "run": run, "accepted": accepted, "blocked": blocked, "error": str(exc)}
