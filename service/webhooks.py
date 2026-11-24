"""Outbound signed webhooks and ChatOps (Slack, Discord, Teams, ntfy) dispatchers."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any

import httpx

from config_store import decrypt_secret, sanitize_hex_color


def _sign_payload(secret: str, body_bytes: bytes) -> str:
    mac = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


def _build_webhook_request(
    webhook: Any,
    event_name: str,
    notice_dict: dict[str, Any],
    app_name: str,
    primary_color: str,
) -> tuple[dict[str, str], bytes]:
    kind = (getattr(webhook, "kind", "webhook") or "webhook").lower()
    secret = decrypt_secret(getattr(webhook, "secret", ""))
    color_hex = sanitize_hex_color(notice_dict.get("bg_color") or primary_color, "#8b5cf6")
    title = notice_dict.get("title") or f"{app_name}: {event_name}"
    text = notice_dict.get("banner_text") or notice_dict.get("text") or ""
    schedule = f"{notice_dict.get('scheduled_start', '')} → {notice_dict.get('scheduled_end', '')}"
    target = notice_dict.get("target_type", "all")
    if target == "domains" and notice_dict.get("target_sites"):
        target = notice_dict["target_sites"]
    elif target == "groups" and notice_dict.get("target_groups"):
        target = f"groups: {notice_dict['target_groups']}"

    headers = {"Content-Type": "application/json", "X-OmniBanner-Event": event_name}

    if kind == "slack":
        payload = {
            "text": f"*{app_name}* — `{event_name}`",
            "attachments": [
                {
                    "color": color_hex,
                    "title": title,
                    "text": text,
                    "fields": [
                        {"title": "Schedule (UTC)", "value": schedule, "short": True},
                        {"title": "Target", "value": target, "short": True},
                    ],
                }
            ],
        }
        body = json.dumps(payload).encode("utf-8")
    elif kind == "discord":
        color_int = int(color_hex.lstrip("#"), 16)
        payload = {
            "username": app_name,
            "embeds": [
                {
                    "title": f"{title} ({event_name})",
                    "description": text[:2000],
                    "color": color_int,
                    "fields": [
                        {"name": "Schedule (UTC)", "value": schedule, "inline": True},
                        {"name": "Target", "value": str(target), "inline": True},
                    ],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            ],
        }
        body = json.dumps(payload).encode("utf-8")
    elif kind == "teams":
        payload = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": color_hex.lstrip("#"),
            "summary": title,
            "sections": [
                {
                    "activityTitle": f"{app_name} — {event_name}",
                    "activitySubtitle": title,
                    "text": text,
                    "facts": [
                        {"name": "Schedule (UTC)", "value": schedule},
                        {"name": "Target", "value": str(target)},
                    ],
                }
            ],
        }
        body = json.dumps(payload).encode("utf-8")
    elif kind == "ntfy":
        headers = {
            "Content-Type": "text/plain; charset=utf-8",
            "Title": f"{app_name}: {event_name}",
            "Tags": "loudspeaker",
            "X-OmniBanner-Event": event_name,
        }
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
        body = f"{title}\n{text}\nSchedule: {schedule}\nTarget: {target}".encode("utf-8")
    else:
        payload = {
            "event": event_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "app_name": app_name,
            "notice": notice_dict,
        }
        body = json.dumps(payload, default=str).encode("utf-8")
        if secret:
            headers["X-OmniBanner-Signature"] = _sign_payload(secret, body)

    return headers, body


async def send_single_webhook(
    webhook: Any,
    event_name: str,
    notice_dict: dict[str, Any],
    app_name: str,
    primary_color: str,
) -> tuple[bool, str]:
    """Deliver a single webhook/ChatOps notification and return (ok, status_text)."""
    url = (getattr(webhook, "url", "") or "").strip()
    if not url:
        return False, "Missing URL"

    headers, body = _build_webhook_request(webhook, event_name, notice_dict, app_name, primary_color)
    verify_tls = bool(getattr(webhook, "verify_tls", True))

    try:
        async with httpx.AsyncClient(verify=verify_tls) as client:
            resp = await client.post(url, content=body, headers=headers, timeout=10.0)
        if 200 <= resp.status_code < 300:
            return True, f"HTTP {resp.status_code}"
        return False, f"HTTP {resp.status_code}: {resp.text[:150]}"
    except Exception as exc:
        return False, str(exc)


async def dispatch_webhooks(
    db: Any,
    event_name: str,
    notice_dict: dict[str, Any],
    app_name: str,
    primary_color: str,
) -> list[dict[str, Any]]:
    """Dispatch event to all matching enabled IntegrationWebhook rows."""
    from database import IntegrationWebhook

    hooks = db.query(IntegrationWebhook).filter(IntegrationWebhook.enabled.is_(True)).all()
    results: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    for hook in hooks:
        subscribed = {e.strip() for e in (hook.events or "").split(",") if e.strip()}
        if subscribed and event_name not in subscribed:
            continue
        ok, status_msg = await send_single_webhook(hook, event_name, notice_dict, app_name, primary_color)
        hook.last_status = status_msg
        hook.last_triggered_at = now
        results.append({"id": hook.id, "name": hook.name, "ok": ok, "status": status_msg})

    if results:
        db.commit()
    return results

