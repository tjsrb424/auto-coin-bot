from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from app.notification_events import event_color, event_fields, event_summary, event_title, format_kst, format_utc, utc_now

DISCORD_USER_AGENT = "coin-bot-protected-auto/1.0"


def discord_webhook_url() -> str:
    for key in ("DISCORD_WEBHOOK_URL", "PROTECTED_AUTO_DISCORD_WEBHOOK_URL"):
        value = os.getenv(key, "").strip()
        if value and ("discord.com/api/webhooks" in value.lower() or "discordapp.com/api/webhooks" in value.lower()):
            return value
    legacy = os.getenv("PROTECTED_AUTO_WEBHOOK_URL", "").strip()
    if legacy:
        return legacy
    return ""


def discord_config_status() -> dict[str, Any]:
    url = discord_webhook_url()
    return {
        "provider": "discord",
        "configured": bool(url),
        "webhook_url": "configured" if url else "not configured",
        "alerts_enabled": os.getenv("PROTECTED_AUTO_ALERTS_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"},
        "language": os.getenv("PROTECTED_AUTO_ALERT_LANGUAGE", "ko").strip().lower() or "ko",
        "style": os.getenv("PROTECTED_AUTO_ALERT_STYLE", "embed").strip().lower() or "embed",
    }

def build_discord_embed(event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    now = utc_now()
    title = str(payload.get("title") or event_title(event_type))
    summary = str(payload.get("summary") or payload.get("message") or event_summary(event_type))
    fields = event_fields(event_type, payload)
    fields.append({"name": "KST", "value": format_kst(payload.get("created_at_utc") or now), "inline": True})
    fields.append({"name": "UTC", "value": format_utc(payload.get("created_at_utc") or now), "inline": True})
    return {
        "title": title[:256],
        "description": summary[:4096],
        "color": event_color(event_type),
        "fields": fields[:10],
        "footer": {"text": "auto-coin-bot - PROTECTED_FULL_AUTO_LIVE_V1"},
        "timestamp": format_utc(payload.get("created_at_utc") or now),
    }


def build_discord_payload(event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "username": "Coin Bot",
        "embeds": [build_discord_embed(event_type, payload)],
    }


def send_discord_embed(event_type: str, payload: dict[str, Any] | None = None, *, webhook_url: str | None = None) -> dict[str, Any]:
    url = (webhook_url if webhook_url is not None else discord_webhook_url()).strip()
    if not url:
        return {"ok": False, "status": "SKIPPED", "error_message": "DISCORD_WEBHOOK_URL_NOT_CONFIGURED"}
    body = json.dumps(build_discord_payload(event_type, payload), ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": DISCORD_USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            response.read()
            return {"ok": True, "status": "SENT", "status_code": getattr(response, "status", 204), "error_message": ""}
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        error = f"{exc.__class__.__name__}:{str(exc)[:240]}"
        if isinstance(exc, urllib.error.HTTPError):
            try:
                body_text = exc.read().decode("utf-8", "replace")[:240]
            except Exception:
                body_text = ""
            error = f"HTTPError:{exc.code}:{body_text or str(exc)[:200]}"
        return {"ok": False, "status": "FAILED", "error_message": error}
