from __future__ import annotations

import os
import queue
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.database import (
    insert_notification_log,
    load_latest_notification_by_dedupe_key,
    load_notification_logs,
    load_sent_notification_by_dedupe_key,
    notification_log_stats_since,
    update_notification_log,
)
from app.discord_notifier import discord_config_status, send_discord_embed
from app.notification_events import event_summary, event_title, utc_now

_DELIVERY_QUEUE: queue.Queue[tuple[str, str, dict[str, Any]]] = queue.Queue()
_DELIVERY_THREAD: threading.Thread | None = None
_DELIVERY_THREAD_LOCK = threading.Lock()


def _utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = str(value).replace(" ", "T")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    if "+" not in normalized[-6:] and "-" not in normalized[-6:]:
        normalized = f"{normalized}+00:00"
    try:
        return datetime.fromisoformat(normalized).astimezone(timezone.utc)
    except ValueError:
        return None


def _date_kst() -> str:
    return (_utc_now_dt() + timedelta(hours=9)).date().isoformat()


def notification_config_status() -> dict[str, Any]:
    provider = os.getenv("NOTIFICATION_PROVIDER", "discord").strip().lower() or "discord"
    discord = discord_config_status()
    since = _utc_iso(_utc_now_dt() - timedelta(hours=1))
    webhook_label = discord["webhook_url"] if provider == "discord" else "not configured"
    return {
        "provider": provider,
        "enabled": discord["alerts_enabled"] if provider == "discord" else False,
        "status": "Enabled" if provider == "discord" and discord["alerts_enabled"] and discord["configured"] else "Disabled",
        "webhook_url": webhook_label,
        "discord": discord,
        "queue_depth": queued_notification_count(),
        "stats": notification_log_stats_since(since),
    }

def send_discord_notification(event_type: str, payload: dict[str, Any] | None = None) -> dict:
    return send_notification(event_type, payload or {}, provider="discord")


def _deliver_notification_log(event_id: str, event_type: str, payload: dict[str, Any]) -> dict | None:
    result = send_discord_embed(event_type, payload)
    if not result.get("ok"):
        time.sleep(0.2)
        result = send_discord_embed(event_type, payload)
    status = "SENT" if result.get("ok") else "FAILED"
    return update_notification_log(
        event_id,
        {
            "status": status,
            "dedupe_status": status,
            "error_message": "" if status == "SENT" else result.get("error_message", "DISCORD_SEND_FAILED"),
            "sent_at_utc": utc_now() if status == "SENT" else None,
        },
    )


def _notification_sender_loop() -> None:
    while True:
        event_id, event_type, payload = _DELIVERY_QUEUE.get()
        try:
            try:
                _deliver_notification_log(event_id, event_type, payload)
            except Exception:
                # Notification delivery must never take down the sender thread or trading worker.
                pass
        finally:
            _DELIVERY_QUEUE.task_done()


def _ensure_notification_sender() -> None:
    global _DELIVERY_THREAD
    if _DELIVERY_THREAD and _DELIVERY_THREAD.is_alive():
        return
    with _DELIVERY_THREAD_LOCK:
        if _DELIVERY_THREAD and _DELIVERY_THREAD.is_alive():
            return
        _DELIVERY_THREAD = threading.Thread(target=_notification_sender_loop, name="notification-discord-sender", daemon=True)
        _DELIVERY_THREAD.start()


def queued_notification_count() -> int:
    return _DELIVERY_QUEUE.qsize()


def drain_notification_queue_for_tests(timeout_seconds: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while getattr(_DELIVERY_QUEUE, "unfinished_tasks", 0) > 0 and time.monotonic() < deadline:
        time.sleep(0.05)


def _event_identity(event_type: str, payload: dict[str, Any]) -> tuple[str | None, int | None]:
    explicit_dedupe_key = str(payload.get("dedupe_key") or payload.get("event_dedupe_key") or "").strip()
    if explicit_dedupe_key:
        return (f"{event_type}:{explicit_dedupe_key}", None)
    session_id = str(payload.get("protected_session_id") or payload.get("related_session_id") or "").strip()
    order_id = str(
        payload.get("exchange_order_uuid")
        or payload.get("exit_order_uuid")
        or payload.get("client_order_id")
        or payload.get("order_uuid")
        or payload.get("related_order_uuid")
        or ""
    ).strip()
    position_id = str(payload.get("position_id") or payload.get("related_position_id") or "").strip()
    stop_reason = str(payload.get("stop_reason") or payload.get("reason") or "").strip()[:120]
    error_type = str(payload.get("error_type") or payload.get("error_code") or payload.get("reason") or event_type).strip()[:120]

    if event_type == "PROTECTED_AUTO_STARTED":
        return (f"PROTECTED_AUTO_STARTED:{session_id}" if session_id else None, None)
    if event_type == "PROTECTED_AUTO_STOPPED":
        return (f"PROTECTED_AUTO_STOPPED:{session_id}:{stop_reason}" if session_id else None, None)
    if event_type == "PROTECTED_AUTO_STALE":
        return (f"PROTECTED_AUTO_STALE:{session_id}" if session_id else None, 15)
    if event_type == "TRADE_OPENED":
        key = order_id or position_id
        return (f"TRADE_OPENED:{key}" if key else None, None)
    if event_type == "TRADE_CLOSED":
        key = order_id or position_id
        return (f"TRADE_CLOSED:{key}" if key else None, None)
    if event_type == "SESSION_LOSS_LIMIT_REACHED":
        return (f"SESSION_LOSS_LIMIT_REACHED:{session_id}" if session_id else None, None)
    if event_type == "ACCOUNTING_ERROR":
        return (f"ACCOUNTING_ERROR:{session_id}:{error_type}" if session_id else None, 15)
    if event_type == "FEE_DIFF_ERROR":
        return (f"FEE_DIFF_ERROR:{session_id}" if session_id else None, 15)
    if event_type == "EQUITY_DIFF_ERROR":
        return (f"EQUITY_DIFF_ERROR:{session_id}" if session_id else None, 15)
    if event_type == "OPEN_ORDER_STALE":
        return (f"OPEN_ORDER_STALE:{session_id}:{order_id}" if session_id and order_id else None, None)
    if event_type == "DAILY_SUMMARY":
        return (f"DAILY_SUMMARY:{payload.get('date_kst') or _date_kst()}", None)
    return (None, None)


def _skip_notification(
    *,
    event_type: str,
    provider: str,
    event_id: str,
    status: str,
    title: str,
    summary: str,
    payload: dict[str, Any],
    event_dedupe_key: str | None,
    error_message: str,
    related_session_id: Any,
    related_run_id: Any,
    related_order_uuid: Any,
    related_position_id: Any,
    rate_limit_until_utc: str | None = None,
) -> dict:
    return insert_notification_log(
        {
            "event_id": event_id,
            "event_type": event_type,
            "provider": provider,
            "status": status,
            "event_dedupe_key": event_dedupe_key,
            "dedupe_status": status,
            "rate_limit_until_utc": rate_limit_until_utc,
            "title": title,
            "summary": summary,
            "payload": payload,
            "error_message": error_message,
            "related_session_id": related_session_id,
            "related_run_id": related_run_id,
            "related_order_uuid": related_order_uuid,
            "related_position_id": related_position_id,
            "created_at_utc": payload.get("created_at_utc") or utc_now(),
        }
    )


def send_notification(
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    provider: str | None = None,
    event_id: str | None = None,
    dispatch_async: bool = False,
) -> dict:
    event_type = str(event_type or "NOTIFICATION").upper()
    payload = dict(payload or {})
    provider = (provider or os.getenv("NOTIFICATION_PROVIDER", "discord") or "discord").strip().lower()
    created_at = utc_now()
    event_id = event_id or str(payload.get("event_id") or f"{event_type.lower()}-{uuid.uuid4().hex[:12]}")
    title = str(payload.get("title") or event_title(event_type))
    summary = str(payload.get("summary") or payload.get("message") or event_summary(event_type))
    related_session_id = payload.get("protected_session_id") or payload.get("related_session_id")
    related_run_id = payload.get("controlled_run_id") or payload.get("run_id") or payload.get("related_run_id")
    related_order_uuid = payload.get("exchange_order_uuid") or payload.get("order_uuid") or payload.get("related_order_uuid")
    related_position_id = payload.get("position_id") or payload.get("related_position_id")
    event_dedupe_key, rate_limit_minutes = _event_identity(event_type, payload)
    payload = {**payload, "created_at_utc": created_at}

    if event_dedupe_key:
        latest_for_dedupe = load_latest_notification_by_dedupe_key(event_dedupe_key)
        if latest_for_dedupe and rate_limit_minutes is None and str(latest_for_dedupe.get("status") or "").upper() in {"PENDING", "QUEUED"}:
            return _skip_notification(
                event_type=event_type,
                provider=provider,
                event_id=event_id,
                status="SKIPPED_DUPLICATE",
                title=title,
                summary=summary,
                payload=payload,
                event_dedupe_key=event_dedupe_key,
                error_message="DUPLICATE_NOTIFICATION_SUPPRESSED",
                related_session_id=related_session_id,
                related_run_id=related_run_id,
                related_order_uuid=related_order_uuid,
                related_position_id=related_position_id,
            )
        sent = load_sent_notification_by_dedupe_key(event_dedupe_key)
        if sent and rate_limit_minutes is None:
            return _skip_notification(
                event_type=event_type,
                provider=provider,
                event_id=event_id,
                status="SKIPPED_DUPLICATE",
                title=title,
                summary=summary,
                payload=payload,
                event_dedupe_key=event_dedupe_key,
                error_message="DUPLICATE_NOTIFICATION_SUPPRESSED",
                related_session_id=related_session_id,
                related_run_id=related_run_id,
                related_order_uuid=related_order_uuid,
                related_position_id=related_position_id,
            )
        latest = load_latest_notification_by_dedupe_key(event_dedupe_key) if rate_limit_minutes is not None else None
        latest_created = _parse_utc((latest or {}).get("created_at_utc"))
        if latest and latest_created and _utc_now_dt() - latest_created < timedelta(minutes=rate_limit_minutes):
            return _skip_notification(
                event_type=event_type,
                provider=provider,
                event_id=event_id,
                status="RATE_LIMITED",
                title=title,
                summary=summary,
                payload=payload,
                event_dedupe_key=event_dedupe_key,
                error_message="NOTIFICATION_RATE_LIMITED",
                related_session_id=related_session_id,
                related_run_id=related_run_id,
                related_order_uuid=related_order_uuid,
                related_position_id=related_position_id,
                rate_limit_until_utc=_utc_iso(_utc_now_dt() + timedelta(minutes=rate_limit_minutes)),
            )

    log = insert_notification_log(
        {
            "event_id": event_id,
            "event_type": event_type,
            "provider": provider,
            "status": "PENDING",
            "event_dedupe_key": event_dedupe_key,
            "dedupe_status": "PENDING",
            "title": title,
            "summary": summary,
            "payload": payload,
            "related_session_id": related_session_id,
            "related_run_id": related_run_id,
            "related_order_uuid": related_order_uuid,
            "related_position_id": related_position_id,
            "created_at_utc": created_at,
        }
    )
    if str(log.get("status") or "").upper() != "PENDING":
        return log
    if provider != "discord":
        return update_notification_log(event_id, {"status": "SKIPPED", "dedupe_status": "SKIPPED", "error_message": "NOTIFICATION_PROVIDER_NOT_SUPPORTED"}) or log
    config = discord_config_status()
    if not config["alerts_enabled"]:
        return update_notification_log(event_id, {"status": "SKIPPED", "dedupe_status": "SKIPPED", "error_message": "PROTECTED_AUTO_ALERTS_DISABLED"}) or log
    if not config["configured"]:
        return update_notification_log(event_id, {"status": "SKIPPED", "dedupe_status": "SKIPPED", "error_message": "DISCORD_WEBHOOK_URL_NOT_CONFIGURED"}) or log

    if dispatch_async:
        queued = update_notification_log(event_id, {"status": "QUEUED", "dedupe_status": "QUEUED", "error_message": ""}) or log
        _ensure_notification_sender()
        _DELIVERY_QUEUE.put((event_id, event_type, payload))
        return queued

    return _deliver_notification_log(event_id, event_type, payload) or log


def load_recent_notification_logs(limit: int = 50, event_type: str | None = None, provider: str | None = None) -> list[dict]:
    return load_notification_logs(limit=limit, event_type=event_type, provider=provider)
