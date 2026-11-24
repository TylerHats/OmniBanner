"""SMTP email rendering and batch campaign delivery."""

from __future__ import annotations

import html
import re
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Callable, Optional

from config_store import sanitize_banner_html, sanitize_hex_color, sanitize_url


def _html_to_plain_text(raw_html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", raw_html, flags=re.IGNORECASE)
    text = re.sub(r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', r"\2 (\1)", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def build_nice_email_html(
    app_name: str,
    primary_color: str,
    notice_text: str,
    start_time: str,
    end_time: str,
    target_sites: Optional[str] = None,
    cta_text: Optional[str] = None,
    cta_url: Optional[str] = None,
    unsubscribe_url: Optional[str] = None,
) -> tuple[str, str]:
    """Build sanitized inline-CSS HTML email and plain-text alternative."""
    safe_app_name = html.escape(app_name or "OmniBanner")
    safe_color = sanitize_hex_color(primary_color, "#8b5cf6")

    raw_notice = (notice_text or "").replace("\r\n", "\n").replace("\n", "<br>")
    safe_notice_html = sanitize_banner_html(raw_notice)
    plain_notice = _html_to_plain_text(safe_notice_html)

    targets_label = "All Sites"
    if target_sites:
        cleaned_targets = [site.strip() for site in target_sites.split(",") if site.strip()]
        if cleaned_targets:
            targets_label = ", ".join(cleaned_targets)
    safe_targets = html.escape(targets_label)
    safe_start = html.escape(str(start_time))
    safe_end = html.escape(str(end_time))

    safe_cta_url = sanitize_url(cta_url, "")
    safe_cta_text = html.escape(cta_text or "")
    cta_block = ""
    cta_plain = ""
    if safe_cta_url and safe_cta_text:
        escaped_cta_href = html.escape(safe_cta_url, quote=True)
        cta_block = (
            f'<div style="margin: 24px 0; text-align: center;">'
            f'<a href="{escaped_cta_href}" target="_blank" rel="noopener noreferrer" '
            f'style="display: inline-block; background-color: {safe_color}; color: #ffffff; '
            f'text-decoration: none; font-weight: 600; font-size: 14px; padding: 12px 24px; '
            f'border-radius: 8px;">{safe_cta_text}</a></div>'
        )
        cta_plain = f"\n{cta_text}: {safe_cta_url}\n"

    unsub_block = ""
    unsub_plain = ""
    safe_unsub = sanitize_url(unsubscribe_url, "")
    if safe_unsub:
        escaped_unsub = html.escape(safe_unsub, quote=True)
        unsub_block = (
            f'<p style="margin: 8px 0 0 0; font-size: 12px; color: #9ca3af;">'
            f'<a href="{escaped_unsub}" style="color: #6b7280; text-decoration: underline;">Unsubscribe from these alerts</a></p>'
        )
        unsub_plain = f"\nUnsubscribe: {safe_unsub}\n"

    year = datetime.now(timezone.utc).year
    html_doc = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Scheduled Notification: {safe_app_name}</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f3f4f6; margin: 0; padding: 0;">
    <div style="width: 100%; background-color: #f3f4f6; padding: 36px 16px; box-sizing: border-box;">
        <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.08);">
            <div style="background-color: {safe_color}; padding: 28px 36px; text-align: center;">
                <h1 style="color: #ffffff; margin: 0; font-size: 22px; font-weight: 700;">{safe_app_name} Notification</h1>
            </div>
            <div style="padding: 36px; color: #374151; line-height: 1.6; font-size: 15px;">
                <p style="margin-top: 0;">Hello,</p>
                <p>We are writing to notify you of a scheduled event or maintenance window:</p>
                <div style="background-color: #fef2f2; border-left: 4px solid #ef4444; padding: 18px 20px; border-radius: 6px; font-size: 15px; color: #991b1b; margin: 20px 0; font-weight: 500;">
                    {safe_notice_html}
                </div>
                {cta_block}
                <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
                    <tr>
                        <td style="padding: 10px 0; border-bottom: 1px solid #e5e7eb; font-size: 14px; color: #6b7280; width: 130px; font-weight: 600;">Starts (UTC)</td>
                        <td style="padding: 10px 0; border-bottom: 1px solid #e5e7eb; font-size: 14px; color: #111827;">{safe_start}</td>
                    </tr>
                    <tr>
                        <td style="padding: 10px 0; border-bottom: 1px solid #e5e7eb; font-size: 14px; color: #6b7280; width: 130px; font-weight: 600;">Ends (UTC)</td>
                        <td style="padding: 10px 0; border-bottom: 1px solid #e5e7eb; font-size: 14px; color: #111827;">{safe_end}</td>
                    </tr>
                    <tr>
                        <td style="padding: 10px 0; border-bottom: 1px solid #e5e7eb; font-size: 14px; color: #6b7280; width: 130px; font-weight: 600;">Affected Sites</td>
                        <td style="padding: 10px 0; border-bottom: 1px solid #e5e7eb; font-size: 14px; color: #111827;">{safe_targets}</td>
                    </tr>
                </table>
                <p style="margin-bottom: 0;">Thank you for your patience and understanding.</p>
            </div>
            <div style="background-color: #f9fafb; padding: 20px 36px; text-align: center; border-top: 1px solid #e5e7eb;">
                <p style="margin: 0; font-size: 12px; color: #9ca3af;">You received this email because you are subscribed to alerts for {safe_app_name}.</p>
                {unsub_block}
                <p style="margin: 6px 0 0 0; font-size: 12px; color: #9ca3af;">&copy; {year} {safe_app_name}. All rights reserved.</p>
            </div>
        </div>
    </div>
</body>
</html>"""

    plain_doc = (
        f"{app_name or 'OmniBanner'} Notification\n"
        f"{'=' * 40}\n\n"
        f"{plain_notice}\n"
        f"{cta_plain}\n"
        f"Starts (UTC): {start_time}\n"
        f"Ends (UTC): {end_time}\n"
        f"Affected Sites: {targets_label}\n"
        f"{unsub_plain}"
    ).strip()
    return html_doc, plain_doc


def _open_smtp_connection(smtp_config: dict[str, Any]):
    port = int(smtp_config.get("port") or 587)
    host = (smtp_config.get("host") or "").strip()
    username = (smtp_config.get("username") or "").strip()
    password = smtp_config.get("password") or ""
    tls_mode = (smtp_config.get("tls_mode") or "").strip().lower()
    if not tls_mode:
        tls_mode = "ssl" if port == 465 else "starttls"

    if not host:
        raise ValueError("SMTP host is empty")

    if tls_mode == "ssl":
        server = smtplib.SMTP_SSL(host, port, timeout=12.0)
    else:
        server = smtplib.SMTP(host, port, timeout=12.0)
        server.ehlo()
        if tls_mode == "starttls":
            server.starttls()
            server.ehlo()

    if username and password:
        server.login(username, password)
    return server


def _build_mime_message(
    smtp_config: dict[str, Any],
    to_email: str,
    subject: str,
    html_content: str,
    text_content: str = "",
    unsubscribe_url: Optional[str] = None,
) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    from_name = (smtp_config.get("from_name") or "OmniBanner Alerts").strip()
    from_email = (smtp_config.get("from_email") or "").strip()
    msg["From"] = f"{from_name} <{from_email}>" if from_name else from_email
    msg["To"] = to_email
    if unsubscribe_url:
        msg["List-Unsubscribe"] = f"<{unsubscribe_url}>"

    plain_part = text_content or _html_to_plain_text(html_content)
    msg.attach(MIMEText(plain_part, "plain", "utf-8"))
    msg.attach(MIMEText(html_content, "html", "utf-8"))
    return msg


def send_alert_email(
    smtp_config: dict[str, Any],
    to_email: str,
    subject: str,
    html_content: str,
    text_content: str = "",
    unsubscribe_url: Optional[str] = None,
) -> tuple[bool, str]:
    """Send a single alert email and return (success, error_message)."""
    try:
        msg = _build_mime_message(
            smtp_config, to_email, subject, html_content, text_content=text_content, unsubscribe_url=unsubscribe_url
        )
        server = _open_smtp_connection(smtp_config)
        try:
            server.sendmail(smtp_config.get("from_email", ""), to_email, msg.as_string())
        finally:
            try:
                server.quit()
            except Exception:
                pass
        return True, ""
    except Exception as exc:
        print(f"SMTP Error while sending email to {to_email}: {exc}")
        return False, str(exc)


def send_campaign_batch(
    smtp_config: dict[str, Any],
    recipients: list[dict[str, str]],
    subject: str,
    render_fn: Callable[[dict[str, str]], tuple[str, str, Optional[str]]],
) -> dict[str, Any]:
    """Send a batch campaign over one reused SMTP connection."""
    sent = 0
    failed = 0
    last_error = ""
    if not recipients:
        return {"sent": 0, "failed": 0, "error": ""}

    try:
        server = _open_smtp_connection(smtp_config)
    except Exception as exc:
        return {"sent": 0, "failed": len(recipients), "error": str(exc)}

    try:
        from_email = (smtp_config.get("from_email") or "").strip()
        for recipient in recipients:
            to_email = recipient["email"]
            try:
                html_body, plain_body, unsub_url = render_fn(recipient)
                msg = _build_mime_message(
                    smtp_config,
                    to_email,
                    subject,
                    html_body,
                    text_content=plain_body,
                    unsubscribe_url=unsub_url,
                )
                server.sendmail(from_email, to_email, msg.as_string())
                sent += 1
            except Exception as exc:
                failed += 1
                last_error = str(exc)
    finally:
        try:
            server.quit()
        except Exception:
            pass

    return {"sent": sent, "failed": failed, "error": last_error}
