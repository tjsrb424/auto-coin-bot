from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from app.database import (
    insert_protected_auto_notification,
    load_protected_auto_notifications,
    update_protected_auto_notification_delivery,
)

PROTECTED_ALERT_EVENTS = {
    "PROTECTED_AUTO_STARTED",
    "PROTECTED_AUTO_STOPPED",
    "PROTECTED_AUTO_STALE",
    "TRADE_OPENED",
    "TRADE_CLOSED",
    "SESSION_LOSS_LIMIT_REACHED",
    "ACCOUNTING_ERROR",
    "MISSING_LEDGER_FILL",
    "DUPLICATE_FILL",
    "FEE_DIFF_ERROR",
    "EQUITY_DIFF_ERROR",
    "OPEN_ORDER_STALE",
    "DAILY_SUMMARY",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _webhook_url() -> str:
    for key in (
        "PROTECTED_AUTO_WEBHOOK_URL",
        "PROTECTED_AUTO_DISCORD_WEBHOOK_URL",
        "DISCORD_WEBHOOK_URL",
        "PROTECTED_AUTO_GOOGLE_CHAT_WEBHOOK_URL",
        "GOOGLE_CHAT_WEBHOOK_URL",
        "PROTECTED_AUTO_TELEGRAM_WEBHOOK_URL",
        "TELEGRAM_WEBHOOK_URL",
    ):
        value = os.getenv(key, "").strip()
        if value:
            return value
    return ""


def _channel_for_url(url: str) -> str:
    lowered = url.lower()
    if "discord.com/api/webhooks" in lowered or "discordapp.com/api/webhooks" in lowered:
        return "DISCORD"
    if "chat.googleapis.com" in lowered:
        return "GOOGLE_CHAT"
    if "api.telegram.org" in lowered or "telegram" in lowered:
        return "TELEGRAM"
    return "WEBHOOK"


def _message(event_type: str, severity: str, message: str, payload: dict[str, Any]) -> str:
    session_id = payload.get("protected_session_id") or "-"
    status = payload.get("status") or payload.get("worker_status") or ""
    pnl = payload.get("protected_strategy_pnl")
    parts = [f"[{severity}] {event_type}", message, f"session={session_id}"]
    if status:
        parts.append(f"status={status}")
    if pnl is not None:
        parts.append(f"protected_pnl={pnl}")
    return "\n".join(str(part) for part in parts if part)


def _webhook_payload(channel: str, text: str) -> dict[str, Any]:
    if channel == "DISCORD":
        return {"content": text[:2000]}
    if channel == "TELEGRAM":
        chat_id = os.getenv("PROTECTED_AUTO_TELEGRAM_CHAT_ID", os.getenv("TELEGRAM_CHAT_ID", "")).strip()
        payload: dict[str, Any] = {"text": text}
        if chat_id:
            payload["chat_id"] = chat_id
        return payload
    return {"text": text}


def _post_webhook(url: str, channel: str, text: str) -> None:
    body = json.dumps(_webhook_payload(channel, text), ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        response.read()


def notify_protected_auto_event(
    event_type: str,
    *,
    severity: str = "INFO",
    exchange: str = "bithumb",
    protected_session_id: str | None = None,
    controlled_run_id: str | None = None,
    message: str = "",
    payload: dict[str, Any] | None = None,
    event_id: str | None = None,
) -> dict:
    event_type = str(event_type or "PROTECTED_AUTO_EVENT").upper()
    severity = str(severity or "INFO").upper()
    payload = {
        **(payload or {}),
        "protected_session_id": protected_session_id,
        "controlled_run_id": controlled_run_id,
    }
    url = _webhook_url()
    channel = _channel_for_url(url) if url else "DB_ONLY"
    created = insert_protected_auto_notification(
        {
            "event_id": event_id or f"{protected_session_id or 'no-session'}:{controlled_run_id or 'no-run'}:{event_type}:{payload.get('dedupe_key') or ''}",
            "event_type": event_type,
            "severity": severity,
            "exchange": exchange,
            "protected_session_id": protected_session_id,
            "controlled_run_id": controlled_run_id,
            "message": message,
            "payload": payload,
            "channel": channel,
            "webhook_configured": bool(url),
            "delivery_status": "PENDING" if url else "DB_ONLY",
        }
    )
    if created.get("delivery_status") != "PENDING" or not url:
        return created
    text = _message(event_type, severity, message, payload)
    try:
        _post_webhook(url, channel, text)
        return update_protected_auto_notification_delivery(
            str(created["event_id"]),
            {
                "channel": channel,
                "webhook_configured": 1,
                "delivery_status": "SENT",
                "delivery_error": "",
                "sent_at_utc": _utc_now(),
            },
        ) or created
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return update_protected_auto_notification_delivery(
            str(created["event_id"]),
            {
                "channel": channel,
                "webhook_configured": 1,
                "delivery_status": "FAILED",
                "delivery_error": f"{exc.__class__.__name__}:{str(exc)[:240]}",
            },
        ) or created


def latest_protected_auto_notification() -> dict | None:
    events = load_protected_auto_notifications(1)
    return events[0] if events else None
