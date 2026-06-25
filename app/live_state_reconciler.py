from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.database import (
    enqueue_next_entry,
    get_connection,
    promote_candidate_strategy,
    update_live_strategy_session,
)

ACTIVE_SLOT_STATUSES = {"OPEN", "RESERVED", "ENTERING", "CLOSING", "EXIT_PENDING", "EXIT_CANDIDATE", "MANUAL_REVIEW_REQUIRED"}
ACTIVE_RESERVATION_STATUSES = {"RESERVED", "ORDER_SUBMITTED"}
ACTIVE_POSITION_STATUSES = {"OPEN", "CLOSING", "EXIT_PENDING", "EXIT_CANDIDATE", "MANUAL_REVIEW_REQUIRED"}
ACTIVE_SESSION_STATUSES = {"READY", "RUNNING"}
STALE_POINTER_SESSION_STATUSES = {"READY", "RUNNING", "LIVE_PAUSED"}
TERMINAL_POSITION_STATUSES = {"CLOSED", "DUPLICATE_RECONCILED", "REJECTED"}
ORPHAN_REASON = "ORPHAN_LIVE_ACTIVE_RECONCILED"
STALE_POINTER_REASON = "STALE_SESSION_POSITION_POINTER_RECONCILED"
MISMATCHED_SLOT_REASON = "MISMATCHED_SLOT_SESSION_RECONCILED"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def _row(sql: str, params: tuple[Any, ...] = ()) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def _status_set(values: set[str]) -> str:
    return ", ".join("?" for _ in values)


def _latest_active_selection_status(candidate_id: int) -> str | None:
    row = _row(
        """
        SELECT status
        FROM active_strategy_selection
        WHERE candidate_strategy_id = ?
        ORDER BY selected_at DESC, id DESC
        LIMIT 1
        """,
        (candidate_id,),
    )
    return str(row["status"]) if row else None


def _orphan_candidate_state(candidate: dict) -> dict:
    candidate_id = int(candidate["id"])
    market = str(candidate.get("market") or "")
    active_selection_status = _latest_active_selection_status(candidate_id)
    slot_count = _row(
        f"""
        SELECT COUNT(*) AS count
        FROM position_slots
        WHERE candidate_strategy_id = ?
          AND status IN ({_status_set(ACTIVE_SLOT_STATUSES)})
        """,
        (candidate_id, *ACTIVE_SLOT_STATUSES),
    )
    reservation_count = _row(
        f"""
        SELECT COUNT(*) AS count
        FROM order_reservations
        WHERE candidate_strategy_id = ?
          AND status IN ({_status_set(ACTIVE_RESERVATION_STATUSES)})
          AND (expires_at IS NULL OR expires_at > ?)
        """,
        (candidate_id, *ACTIVE_RESERVATION_STATUSES, _utc_now()),
    )
    open_position_count = _row(
        f"""
        SELECT COUNT(*) AS count
        FROM live_positions
        WHERE candidate_strategy_id = ?
          AND status IN ({_status_set(ACTIVE_POSITION_STATUSES)})
        """,
        (candidate_id, *ACTIVE_POSITION_STATUSES),
    )
    running_session_count = _row(
        f"""
        SELECT COUNT(*) AS count
        FROM live_strategy_sessions
        WHERE candidate_strategy_id = ?
          AND status IN ({_status_set(ACTIVE_SESSION_STATUSES)})
        """,
        (candidate_id, *ACTIVE_SESSION_STATUSES),
    )
    active_selection_count = _row(
        """
        SELECT COUNT(*) AS count
        FROM active_strategy_selection
        WHERE candidate_strategy_id = ?
          AND status = 'LIVE_ACTIVE'
        """,
        (candidate_id,),
    )
    had_slot = int((slot_count or {}).get("count") or 0) > 0
    had_reservation = int((reservation_count or {}).get("count") or 0) > 0
    had_open_position = int((open_position_count or {}).get("count") or 0) > 0
    had_running_session = int((running_session_count or {}).get("count") or 0) > 0
    had_active_selection = int((active_selection_count or {}).get("count") or 0) > 0
    is_orphan = not any((had_slot, had_reservation, had_open_position, had_running_session, had_active_selection))
    return {
        "candidate_strategy_id": candidate_id,
        "market": market,
        "previous_status": str(candidate.get("status") or ""),
        "new_status": "LIVE_ELIGIBLE" if is_orphan else str(candidate.get("status") or ""),
        "reason": ORPHAN_REASON if is_orphan else "LIVE_ACTIVE_HAS_EXECUTION_LINK",
        "had_slot": had_slot,
        "had_reservation": had_reservation,
        "had_open_position": had_open_position,
        "had_running_session": had_running_session,
        "had_active_selection": had_active_selection,
        "active_selection_status": active_selection_status,
        "orphan": is_orphan,
    }


def find_orphan_live_active_candidates() -> dict:
    candidates = _rows(
        """
        SELECT *
        FROM candidate_strategies
        WHERE status = 'LIVE_ACTIVE'
        ORDER BY updated_at DESC, id DESC
        """
    )
    items = [_orphan_candidate_state(candidate) for candidate in candidates]
    orphans = [item for item in items if item["orphan"]]
    return {
        "checked_count": len(items),
        "orphan_count": len(orphans),
        "items": orphans,
    }


def reconcile_orphan_live_active_candidates(*, dry_run: bool = True, enqueue: bool = True) -> dict:
    candidates = _rows(
        """
        SELECT *
        FROM candidate_strategies
        WHERE status = 'LIVE_ACTIVE'
        ORDER BY updated_at DESC, id DESC
        """
    )
    items: list[dict] = []
    demoted_count = 0
    queued_count = 0
    for candidate in candidates:
        state = _orphan_candidate_state(candidate)
        if state["orphan"]:
            demoted_count += 1
            if not dry_run:
                promote_candidate_strategy(
                    int(candidate["id"]),
                    "LIVE_ELIGIBLE",
                    reason=ORPHAN_REASON,
                    metadata={
                        "previous_status": "LIVE_ACTIVE",
                        "had_slot": state["had_slot"],
                        "had_reservation": state["had_reservation"],
                        "had_open_position": state["had_open_position"],
                        "had_running_session": state["had_running_session"],
                        "had_active_selection": state["had_active_selection"],
                        "active_selection_status": state["active_selection_status"],
                    },
                )
                if enqueue:
                    enqueue_next_entry(
                        {**candidate, "status": "LIVE_ELIGIBLE"},
                        allocation_score=float(candidate.get("score") or 0.0),
                        blocked_reason=ORPHAN_REASON,
                    )
                    queued_count += 1
        items.append(state)
    return {
        "checked_count": len(candidates),
        "orphan_count": demoted_count,
        "demoted_count": 0 if dry_run else demoted_count,
        "reassigned_count": 0,
        "queued_count": 0 if dry_run else queued_count,
        "dry_run": dry_run,
        "items": [item for item in items if item["orphan"]],
    }


def _replacement_open_position(session: dict) -> dict | None:
    row = _row(
        f"""
        SELECT *
        FROM live_positions
        WHERE session_id = ?
          AND exchange = ?
          AND market = ?
          AND status IN ({_status_set(ACTIVE_POSITION_STATUSES)})
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (int(session["id"]), str(session.get("exchange") or "bithumb"), str(session.get("market") or ""), *ACTIVE_POSITION_STATUSES),
    )
    if row:
        return row
    return _row(
        f"""
        SELECT *
        FROM live_positions
        WHERE exchange = ?
          AND market = ?
          AND candidate_strategy_id = ?
          AND status IN ({_status_set(ACTIVE_POSITION_STATUSES)})
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (
            str(session.get("exchange") or "bithumb"),
            str(session.get("market") or ""),
            int(session.get("candidate_strategy_id") or 0),
            *ACTIVE_POSITION_STATUSES,
        ),
    )


def find_stale_live_strategy_sessions() -> dict:
    sessions = _rows(
        f"""
        SELECT *
        FROM live_strategy_sessions
        WHERE status IN ({_status_set(STALE_POINTER_SESSION_STATUSES)})
          AND current_position_id IS NOT NULL
        ORDER BY updated_at DESC, id DESC
        """,
        tuple(STALE_POINTER_SESSION_STATUSES),
    )
    items: list[dict] = []
    for session in sessions:
        position = _row("SELECT * FROM live_positions WHERE id = ?", (int(session["current_position_id"]),))
        old_status = str((position or {}).get("status") or "MISSING")
        if position and old_status not in TERMINAL_POSITION_STATUSES and old_status in ACTIVE_POSITION_STATUSES:
            continue
        replacement = _replacement_open_position(session)
        current_open_order_uuid = str(session.get("current_open_order_uuid") or "").strip()
        new_status = str(session.get("status") or "")
        if replacement is None and not current_open_order_uuid:
            new_status = "STOPPED"
        items.append(
            {
                "session_id": int(session["id"]),
                "market": str(session.get("market") or ""),
                "old_current_position_id": int(session["current_position_id"]),
                "old_position_status": old_status,
                "new_current_position_id": int(replacement["id"]) if replacement else None,
                "new_session_status": new_status,
                "reason": STALE_POINTER_REASON,
            }
        )
    return {"checked_count": len(sessions), "stale_count": len(items), "items": items}


def reconcile_stale_live_strategy_sessions(*, dry_run: bool = True) -> dict:
    probe = find_stale_live_strategy_sessions()
    fixed_count = 0
    if not dry_run:
        now_utc = _utc_now()
        for item in probe["items"]:
            updates: dict[str, Any] = {
                "current_position_id": item["new_current_position_id"],
                "last_risk_result": item["reason"],
            }
            if item["new_session_status"] == "STOPPED":
                updates.update(
                    {
                        "status": "STOPPED",
                        "auto_enabled": False,
                        "last_order_status": "STALE_POSITION_POINTER_CLEARED",
                        "stopped_at": now_utc,
                    }
                )
            update_live_strategy_session(int(item["session_id"]), updates)
            fixed_count += 1
    return {
        "checked_count": probe["checked_count"],
        "fixed_count": 0 if dry_run else fixed_count,
        "dry_run": dry_run,
        "items": probe["items"],
    }


def find_mismatched_position_slot_sessions() -> dict:
    rows = _rows(
        f"""
        SELECT
            ps.id AS slot_id,
            ps.slot_number,
            ps.status AS slot_status,
            ps.market AS slot_market,
            ps.candidate_strategy_id AS slot_candidate_strategy_id,
            ps.live_position_id,
            ps.live_strategy_session_id,
            ps.entry_order_uuid,
            s.market AS session_market,
            s.candidate_strategy_id AS session_candidate_strategy_id,
            s.status AS session_status
        FROM position_slots ps
        LEFT JOIN live_strategy_sessions s ON s.id = ps.live_strategy_session_id
        WHERE ps.live_strategy_session_id IS NOT NULL
          AND ps.status IN ({_status_set(ACTIVE_SLOT_STATUSES)})
          AND (
              s.id IS NULL
              OR COALESCE(ps.market, '') != COALESCE(s.market, '')
              OR COALESCE(ps.candidate_strategy_id, 0) != COALESCE(s.candidate_strategy_id, 0)
          )
        ORDER BY ps.slot_number ASC, ps.id ASC
        """,
        tuple(ACTIVE_SLOT_STATUSES),
    )
    items: list[dict] = []
    for row in rows:
        safe_to_release = (
            str(row.get("slot_status") or "").upper() == "RESERVED"
            and not row.get("live_position_id")
            and not row.get("entry_order_uuid")
        )
        item = dict(row)
        item["safe_to_release"] = safe_to_release
        item["reason"] = MISMATCHED_SLOT_REASON
        items.append(item)
    return {"checked_count": len(items), "items": items}


def reconcile_mismatched_position_slot_sessions(*, dry_run: bool = True, enqueue: bool = True) -> dict:
    probe = find_mismatched_position_slot_sessions()
    released_count = 0
    manual_review_count = 0
    now_utc = _utc_now()
    for item in probe["items"]:
        if not item["safe_to_release"]:
            manual_review_count += 1
            continue
        candidate_id = int(item.get("slot_candidate_strategy_id") or 0)
        market = str(item.get("slot_market") or "")
        session_id = int(item.get("live_strategy_session_id") or 0)
        if not dry_run:
            with get_connection() as conn:
                conn.execute(
                    """
                    UPDATE order_reservations
                    SET status = 'EXPIRED',
                        updated_at = ?
                    WHERE slot_id = ?
                      AND status IN ('RESERVED', 'ORDER_SUBMITTED')
                    """,
                    (now_utc, int(item["slot_id"])),
                )
                conn.execute(
                    """
                    UPDATE position_slots
                    SET status = 'EMPTY',
                        market = NULL,
                        candidate_strategy_id = NULL,
                        live_strategy_session_id = NULL,
                        allocated_krw = 0,
                        reserved_krw = 0,
                        entry_reason = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (MISMATCHED_SLOT_REASON, now_utc, int(item["slot_id"])),
                )
            if session_id:
                update_live_strategy_session(
                    session_id,
                    {
                        "status": "STOPPED",
                        "auto_enabled": False,
                        "last_risk_result": MISMATCHED_SLOT_REASON,
                        "last_order_status": "REPLACED",
                        "stopped_at": now_utc,
                    },
                )
            if candidate_id:
                promote_candidate_strategy(candidate_id, "LIVE_ELIGIBLE", reason=MISMATCHED_SLOT_REASON)
                if enqueue:
                    candidate = _row("SELECT * FROM candidate_strategies WHERE id = ?", (candidate_id,))
                    if candidate:
                        enqueue_next_entry(
                            {**candidate, "market": market, "status": "LIVE_ELIGIBLE"},
                            allocation_score=float(candidate.get("score") or 0.0),
                            blocked_reason=MISMATCHED_SLOT_REASON,
                        )
        released_count += 1
    return {
        "checked_count": probe["checked_count"],
        "released_count": 0 if dry_run else released_count,
        "manual_review_count": manual_review_count,
        "dry_run": dry_run,
        "items": probe["items"],
    }


def live_state_warnings() -> dict:
    orphan = find_orphan_live_active_candidates()
    stale = find_stale_live_strategy_sessions()
    mismatched = find_mismatched_position_slot_sessions()
    warnings: list[str] = []
    if orphan["orphan_count"]:
        warnings.append("ORPHAN_LIVE_ACTIVE_CANDIDATES_DETECTED")
    if stale["stale_count"]:
        warnings.append("STALE_SESSION_POSITION_POINTER_DETECTED")
    if mismatched["checked_count"]:
        warnings.append("MISMATCHED_POSITION_SLOT_SESSION_DETECTED")
    return {
        "warnings": warnings,
        "orphan_live_active_candidates_count": orphan["orphan_count"],
        "stale_session_position_pointer_count": stale["stale_count"],
        "mismatched_position_slot_session_count": mismatched["checked_count"],
        "orphan_live_active_candidates": orphan["items"],
        "stale_session_position_pointers": stale["items"],
        "mismatched_position_slot_sessions": mismatched["items"],
    }


def reconcile_live_state(*, dry_run: bool = True) -> dict:
    mismatched = reconcile_mismatched_position_slot_sessions(dry_run=dry_run)
    stale = reconcile_stale_live_strategy_sessions(dry_run=dry_run)
    orphan = reconcile_orphan_live_active_candidates(dry_run=dry_run)
    return {
        "dry_run": dry_run,
        "mismatched_position_slot_sessions": mismatched,
        "stale_session_pointers": stale,
        "orphan_live_active_candidates": orphan,
        "warnings_after": live_state_warnings() if not dry_run else None,
    }
