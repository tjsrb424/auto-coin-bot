from __future__ import annotations

import os
import time
import uuid
from typing import Any

from app.database import insert_notification_log, load_notification_logs, update_notification_log
from app.discord_notifier import discord_config_status, send_discord_embed
from app.notification_events import event_summary, event_title, utc_now


def notification_config_status() -> dict[str, Any]:
    provider = os.getenv("NOTIFICATION_PROVIDER", "discord").strip().lower() or "discord"
    discord = discord_config_status()
    return {
        "provider": provider,
        "enabled": discord["alerts_enabled"] if provider == "discord" else False,
        "status": "Enabled" if provider == "discord" and discord["alerts_enabled"] and discord["configured"] else "Disabled",
        "webhook_url": discord["webhook_url"] if provider == "discord" else "미설정",
        "discord": discord,
    }


def send_discord_notification(event_type: str, payload: dict[str, Any] | None = None) -> dict:
    return send_notification(event_type, payload or {}, provider="discord")


def send_notification(event_type: str, payload: dict[str, Any] | None = None, *, provider: str | None = None, event_id: str | None = None) -> dict:
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
    payload = {**payload, "created_at_utc": created_at}
    log = insert_notification_log(
        {
            "event_id": event_id,
            "event_type": event_type,
            "provider": provider,
            "status": "PENDING",
            "title": title,
            "summary": summary,
            "payload": payload,
            "related_session_id": related_session_id,
            "related_run_id": related_run_id,
            "related_order_uuid": related_order_uuid,
            "created_at_utc": created_at,
        }
    )
    if str(log.get("status") or "").upper() != "PENDING":
        return log
    if provider != "discord":
        return update_notification_log(event_id, {"status": "SKIPPED", "error_message": "NOTIFICATION_PROVIDER_NOT_SUPPORTED"}) or log
    config = discord_config_status()
    if not config["alerts_enabled"]:
        return update_notification_log(event_id, {"status": "SKIPPED", "error_message": "PROTECTED_AUTO_ALERTS_DISABLED"}) or log
    if not config["configured"]:
        return update_notification_log(event_id, {"status": "SKIPPED", "error_message": "DISCORD_WEBHOOK_URL_NOT_CONFIGURED"}) or log

    result = send_discord_embed(event_type, payload)
    if not result.get("ok"):
        time.sleep(0.2)
        result = send_discord_embed(event_type, payload)
    status = "SENT" if result.get("ok") else "FAILED"
    return update_notification_log(
        event_id,
        {
            "status": status,
            "error_message": "" if status == "SENT" else result.get("error_message", "DISCORD_SEND_FAILED"),
            "sent_at_utc": utc_now() if status == "SENT" else None,
        },
    ) or log


def load_recent_notification_logs(limit: int = 50, event_type: str | None = None, provider: str | None = None) -> list[dict]:
    return load_notification_logs(limit=limit, event_type=event_type, provider=provider)
