"""OmniBanner central FastAPI control plane, API routers, and background scheduler."""

from __future__ import annotations

import asyncio
import csv
import fcntl
import fnmatch
import hashlib
import html as html_lib
import io
import ipaddress
import json
import os
import re
import secrets
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import quote, urlencode, urlparse

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageOps
from sqlalchemy.orm import Session

import auth
import kuma
import smtp
import webhooks
from analytics_buffer import analytics_buffer, banner_cache
from config_store import (
    BASE_DIR,
    DATA_DIR,
    UPLOADS_DIR,
    decrypt_secret,
    encrypt_secret,
    resolve_public_base_url,
    sanitize_banner_html,
    sanitize_csv_cell,
    sanitize_custom_css,
    sanitize_hex_color,
    sanitize_url,
)
from database import (
    DEFAULT_SITE_SETTINGS,
    AuditLog,
    BannerTemplate,
    InboundWebhookRule,
    IntegrationWebhook,
    Notice,
    OIDCProvider,
    RevokedToken,
    SessionLocal,
    Site,
    SiteNoticeStat,
    SitePairingToken,
    SMTPSettings,
    Subscriber,
    SystemConfig,
    UptimeKumaSettings,
    User,
    init_db,
    utcnow,
)
from schemas import (
    BannerTemplateRequest,
    BrandUpdateRequest,
    ChangePasswordRequest,
    ClaimPairingRequest,
    HeartbeatRequest,
    InboundRuleRequest,
    KumaSettingsRequest,
    LoginStep1Request,
    LoginStep2Request,
    NoticeRequest,
    OIDCProviderRequest,
    OIDCTestRequest,
    PairingTokenRequest,
    PublicEventRequest,
    SetupRequest,
    SiteRequest,
    SMTPRequest,
    SubscriberRequest,
    TestKumaRequest,
    TestSMTPRequest,
    TOTPDisableRequest,
    TOTPVerifyRequest,
    UserCreateRequest,
    UserUpdateRequest,
    WebhookRequest,
)

# Rate limiters
login_limiter = auth.RateLimiter(max_requests=20, window_seconds=60)
claim_limiter = auth.RateLimiter(max_requests=30, window_seconds=60)
public_event_limiter = auth.RateLimiter(max_requests=240, window_seconds=60)

_PUBLIC_CORS_PREFIXES = (
    "/api/public/",
    "/api/sites/claim",
    "/api/sites/heartbeat",
    "/api/sites/config",
    "/static/brand/",
    "/static/uploads/",
    "/static/js/embed.js",
    "/embed.js",
)
_CSRF_EXEMPT_PREFIXES = (
    "/api/public/",
    "/api/login-step1",
    "/api/login-step2",
    "/api/setup",
    "/api/sites/claim",
    "/api/sites/heartbeat",
    "/api/inbound/",
)
_VALID_ROLES = ("admin", "editor", "viewer")
APP_VERSION = "2.1.0"
_PLUGIN_ARTIFACT_CACHE: dict[str, Any] = {"cache_key": None, "artifact": None}


def _reset_rate_limiters() -> None:
    login_limiter.reset()
    claim_limiter.reset()
    public_event_limiter.reset()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- BACKGROUND TASKS & LIFESPAN ---


async def analytics_flush_loop() -> None:
    while True:
        try:
            await asyncio.sleep(15)
            await asyncio.to_thread(analytics_buffer.flush_to_db, SessionLocal)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"Analytics flush error: {exc}")


async def _send_notice_email_campaign(
    db: Session,
    notice: Notice,
    smtp_conf: SMTPSettings,
    brand: Optional[SystemConfig],
) -> dict[str, Any]:
    """Send a notice's alert email campaign to active matching subscribers."""
    app_name = brand.app_name if brand else "OmniBanner"
    primary_color = brand.primary_color if brand else "#8b5cf6"
    public_base = (brand.public_base_url if brand else "") or ""

    all_subs = db.query(Subscriber).filter(Subscriber.active.is_(True)).all()
    required_tags = _parse_csv(notice.target_subscriber_tags)
    recipients: list[dict[str, str]] = []
    for sub in all_subs:
        if required_tags:
            sub_tags = _parse_csv(sub.tags)
            if not (required_tags & sub_tags):
                continue
        recipients.append(
            {
                "email": sub.email,
                "name": sub.name or "",
                "unsubscribe_token": sub.unsubscribe_token or "",
            }
        )

    if not recipients:
        notice.smtp_sent = True
        notice.smtp_error = ""
        db.commit()
        return {"sent": 0, "failed": 0, "error": ""}

    smtp_data = {
        "host": smtp_conf.host,
        "port": smtp_conf.port,
        "tls_mode": smtp_conf.tls_mode or ("ssl" if smtp_conf.port == 465 else "starttls"),
        "username": smtp_conf.username,
        "password": decrypt_secret(smtp_conf.password),
        "from_email": smtp_conf.from_email,
        "from_name": smtp_conf.from_name,
    }
    subject = notice.email_subject or f"[{app_name}] Notice: {(notice.title or notice.banner_text or '')[:50]}"
    start_s = notice.scheduled_start.strftime("%Y-%m-%d %H:%M:%S")
    end_s = notice.scheduled_end.strftime("%Y-%m-%d %H:%M:%S")
    target_label = notice.target_sites if notice.target_type in ("domains", "sites") else (
        f"Groups: {notice.target_groups}" if notice.target_type == "groups" else None
    )

    def render_recipient(rec: dict[str, str]):
        unsub = (
            f"{public_base.rstrip('/')}/unsubscribe/{rec['unsubscribe_token']}"
            if public_base and rec.get("unsubscribe_token")
            else None
        )
        html_body, plain_body = smtp.build_nice_email_html(
            app_name=app_name,
            primary_color=primary_color,
            notice_text=notice.email_text or notice.banner_text,
            start_time=start_s,
            end_time=end_s,
            target_sites=target_label,
            cta_text=notice.cta_text,
            cta_url=notice.cta_url,
            unsubscribe_url=unsub,
        )
        return html_body, plain_body, unsub

    batch_res = await asyncio.to_thread(
        smtp.send_campaign_batch, smtp_data, recipients, subject, render_recipient
    )
    notice.smtp_sent_count = (notice.smtp_sent_count or 0) + int(batch_res["sent"])
    notice.smtp_error = batch_res.get("error", "")
    if batch_res["sent"] > 0:
        notice.smtp_sent = True
    db.commit()
    return batch_res


async def _process_scheduler_tick() -> None:
    """Single background scheduler iteration for SMTP, Kuma, Webhooks, and Recurrence."""
    db = SessionLocal()
    try:
        now = utcnow()
        brand = db.query(SystemConfig).first()
        app_name = brand.app_name if brand else "OmniBanner"
        primary_color = brand.primary_color if brand else "#8b5cf6"

        active_notices = (
            db.query(Notice)
            .filter(
                Notice.enabled.is_(True),
                Notice.scheduled_start <= now,
                Notice.scheduled_end >= now,
            )
            .all()
        )

        smtp_conf = db.query(SMTPSettings).filter(SMTPSettings.enabled.is_(True)).first()
        kuma_conf = db.query(UptimeKumaSettings).filter(UptimeKumaSettings.enabled.is_(True)).first()

        # 1a. Pre-maintenance & active SMTP campaigns (supports email_lead_minutes)
        if smtp_conf:
            pending_email_notices = (
                db.query(Notice)
                .filter(
                    Notice.enabled.is_(True),
                    Notice.send_smtp.is_(True),
                    Notice.smtp_sent.is_(False),
                    Notice.scheduled_end >= now,
                )
                .all()
            )
            for notice in pending_email_notices:
                lead = max(0, int(notice.email_lead_minutes or 0))
                if now >= notice.scheduled_start - timedelta(minutes=lead):
                    await _send_notice_email_campaign(db, notice, smtp_conf, brand)

        # 1b. Active notices Kuma & Webhooks
        for notice in active_notices:
            if notice.uptime_kuma_integration and not notice.kuma_start_notified:
                if notice.uptime_kuma_push_url:
                    ok = await kuma.ping_kuma_push_url(
                        notice.uptime_kuma_push_url,
                        is_active=True,
                        msg=notice.kuma_text or notice.banner_text,
                        verify_tls=bool(kuma_conf.verify_tls) if kuma_conf else True,
                    )
                    if ok:
                        notice.kuma_start_notified = True
                        db.commit()
                elif kuma_conf and kuma_conf.api_mode == "pause" and notice.uptime_kuma_monitor_ids:
                    mids = [m.strip() for m in notice.uptime_kuma_monitor_ids.split(",") if m.strip()]
                    if mids:
                        ok = await asyncio.to_thread(
                            kuma.pause_kuma_monitors,
                            kuma_conf.url,
                            kuma_conf.username,
                            decrypt_secret(kuma_conf.password),
                            mids,
                            bool(kuma_conf.verify_tls),
                        )
                        if ok:
                            notice.kuma_start_notified = True
                            db.commit()

            if not notice.webhook_start_sent:
                await webhooks.dispatch_webhooks(
                    db, "notice.started", _serialize_notice(notice), app_name, primary_color
                )
                notice.webhook_start_sent = True
                db.commit()

        # 2. Expired notices cleanup (once only!)
        active_push_urls = {
            n.uptime_kuma_push_url
            for n in active_notices
            if n.uptime_kuma_integration and n.uptime_kuma_push_url
        }

        expired_kuma = (
            db.query(Notice)
            .filter(
                Notice.scheduled_end < now,
                Notice.uptime_kuma_integration.is_(True),
                Notice.kuma_start_notified.is_(True),
                Notice.kuma_end_notified.is_(False),
            )
            .all()
        )
        for notice in expired_kuma:
            if notice.uptime_kuma_push_url:
                if notice.uptime_kuma_push_url not in active_push_urls:
                    await kuma.ping_kuma_push_url(
                        notice.uptime_kuma_push_url,
                        is_active=False,
                        msg="Maintenance window finished",
                        verify_tls=bool(kuma_conf.verify_tls) if kuma_conf else True,
                    )
                notice.kuma_end_notified = True
                db.commit()
            elif kuma_conf and kuma_conf.api_mode == "pause" and notice.uptime_kuma_monitor_ids:
                mids = [m.strip() for m in notice.uptime_kuma_monitor_ids.split(",") if m.strip()]
                if mids:
                    await asyncio.to_thread(
                        kuma.resume_kuma_monitors,
                        kuma_conf.url,
                        kuma_conf.username,
                        decrypt_secret(kuma_conf.password),
                        mids,
                        bool(kuma_conf.verify_tls),
                    )
                notice.kuma_end_notified = True
                db.commit()

        expired_webhooks = (
            db.query(Notice)
            .filter(
                Notice.scheduled_end < now,
                Notice.webhook_start_sent.is_(True),
                Notice.webhook_end_sent.is_(False),
            )
            .all()
        )
        for notice in expired_webhooks:
            await webhooks.dispatch_webhooks(
                db, "notice.ended", _serialize_notice(notice), app_name, primary_color
            )
            notice.webhook_end_sent = True
            db.commit()

        # 3. Roll forward recurring notices for next cycle
        recur_deltas = {
            "daily": timedelta(days=1),
            "weekly": timedelta(days=7),
            "monthly": timedelta(days=30),
        }
        recurring_expired = (
            db.query(Notice)
            .filter(
                Notice.enabled.is_(True),
                Notice.scheduled_end < now,
                Notice.recurrence.in_(("daily", "weekly", "monthly")),
            )
            .all()
        )
        rolled = False
        for notice in recurring_expired:
            step = recur_deltas.get(notice.recurrence)
            if not step:
                continue
            while notice.scheduled_end < now:
                notice.scheduled_start += step
                notice.scheduled_end += step
            notice.smtp_sent = False
            notice.smtp_error = ""
            notice.kuma_start_notified = False
            notice.kuma_end_notified = False
            notice.webhook_start_sent = False
            notice.webhook_end_sent = False
            notice.kuma_maintenance_id = None
            rolled = True
        if rolled:
            db.commit()
            banner_cache.invalidate()

        # Periodically prune expired revoked tokens
        db.query(RevokedToken).filter(RevokedToken.expires_at < now).delete()
        db.commit()
    except Exception as exc:
        print(f"Scheduler Loop Error: {exc}")
    finally:
        db.close()


async def notification_scheduler_loop() -> None:
    while True:
        try:
            await _process_scheduler_tick()
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"Scheduler outer error: {exc}")
            await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    scheduler_task = None
    lock_fd = None

    if os.environ.get("OMNIBANNER_DISABLE_SCHEDULER") != "1":
        os.makedirs(DATA_DIR, exist_ok=True)
        lock_path = os.path.join(DATA_DIR, ".scheduler.lock")
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            scheduler_task = asyncio.create_task(notification_scheduler_loop())
        except BlockingIOError:
            os.close(lock_fd)
            lock_fd = None

    flush_task = asyncio.create_task(analytics_flush_loop())
    try:
        yield
    finally:
        tasks = [t for t in (scheduler_task, flush_task) if t is not None]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        analytics_buffer.flush_to_db(SessionLocal)
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)


app = FastAPI(title="OmniBanner Central Service", version=APP_VERSION, lifespan=lifespan)

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


# --- SCOPED CORS & CSRF MIDDLEWARE ---


@app.middleware("http")
async def security_cors_csrf_middleware(request: Request, call_next):
    path = request.url.path
    is_public_cors = any(path.startswith(prefix) or path == prefix for prefix in _PUBLIC_CORS_PREFIXES)

    if is_public_cors:
        cors_headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, X-OmniBanner-Site-Key",
            "Access-Control-Max-Age": "600",
        }
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=cors_headers)
        response = await call_next(request)
        for k, v in cors_headers.items():
            response.headers[k] = v
        return response

    # CSRF origin check for cookie-authenticated state-changing /api/ requests
    if (
        path.startswith("/api/")
        and request.method in ("POST", "PUT", "PATCH", "DELETE")
        and not any(path.startswith(prefix) for prefix in _CSRF_EXEMPT_PREFIXES)
    ):
        auth_header = request.headers.get("Authorization", "")
        cookie_token = request.cookies.get("access_token")
        if cookie_token and not auth_header.startswith("Bearer "):
            expected_host = (
                (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc)
                .split(",")[0]
                .strip()
                .lower()
            )
            origin = request.headers.get("origin") or request.headers.get("referer")
            if origin:
                origin_host = urlparse(origin).netloc.lower()
                if origin_host and expected_host and origin_host != expected_host:
                    return JSONResponse({"detail": "CSRF origin mismatch"}, status_code=403)

    return await call_next(request)


# --- HELPERS ---


def _parse_csv(raw: Optional[str]) -> set[str]:
    if not raw:
        return set()
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _extract_host(raw: Optional[str]) -> str:
    if not raw:
        return ""
    val = raw.strip()
    if "://" in val:
        parsed = urlparse(val)
        return (parsed.hostname or "").lower()
    return val.split("/")[0].split(":")[0].strip().lower()


def _parse_iso_to_utc_naive(raw: Optional[str]) -> datetime:
    if not raw:
        raise ValueError("Missing timestamp")
    cleaned = raw.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _fmt_utc(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _set_session_cookie(response: Response, request: Request, token: str) -> None:
    is_https = request.url.scheme == "https" or (
        request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower() == "https"
    )
    response.set_cookie(
        key="access_token",
        value=token,
        max_age=86400,
        path="/",
        httponly=True,
        samesite="lax",
        secure=is_https,
    )


def _get_brand_context(db: Session, user: Optional[User] = None) -> dict[str, Any]:
    config = db.query(SystemConfig).first()
    if not config:
        config = SystemConfig(
            app_name="OmniBanner",
            app_icon_url="/static/brand/logo.png",
            favicon_url="/static/brand/logo.png",
            primary_color="#8b5cf6",
            secondary_color="#6366f1",
            default_theme="dark",
        )
    theme = (user.theme_preference if user and user.theme_preference else None) or config.default_theme or "dark"
    return {
        "app_name": config.app_name or "OmniBanner",
        "app_icon_url": config.app_icon_url or "/static/brand/logo.png",
        "favicon_url": config.favicon_url or config.app_icon_url or "/static/brand/logo.png",
        "primary_color": sanitize_hex_color(config.primary_color, "#8b5cf6"),
        "secondary_color": sanitize_hex_color(config.secondary_color, "#6366f1"),
        "default_theme": config.default_theme or "dark",
        "active_theme": theme,
        "custom_css": sanitize_custom_css(config.custom_css),
        "login_subtitle": config.login_subtitle or "",
        "footer_text": config.footer_text or "",
        "public_base_url": config.public_base_url or "",
        "default_timezone": config.default_timezone or "UTC",
        "hide_local_login": bool(config.hide_local_login),
        "allow_proxy_auth": bool(config.allow_proxy_auth),
        "proxy_auth_header": config.proxy_auth_header or "X-authentik-username",
        "proxy_auth_groups_header": config.proxy_auth_groups_header or "X-authentik-groups",
        "trusted_proxy_ips": config.trusted_proxy_ips or "127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16",
        "proxy_admin_groups": config.proxy_admin_groups or "",
        "proxy_editor_groups": config.proxy_editor_groups or "",
        "public_status_page": bool(config.public_status_page if config.public_status_page is not None else True),
    }


def is_setup_completed(db: Session) -> bool:
    config = db.query(SystemConfig).first()
    return bool(config and config.setup_completed)


def _log_audit(
    db: Session,
    username: str,
    action: str,
    resource_type: str,
    resource_id: str = "",
    details: str = "",
    request: Optional[Request] = None,
) -> None:
    entry = AuditLog(
        username=username or "system",
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id or ""),
        details=details[:1000],
        ip_address=_client_ip(request) if request else "",
    )
    db.add(entry)
    db.commit()


def _parse_site_settings(raw_json: Optional[str]) -> dict[str, Any]:
    merged = dict(DEFAULT_SITE_SETTINGS)
    if raw_json:
        try:
            loaded = json.loads(raw_json)
            if isinstance(loaded, dict):
                merged.update(loaded)
        except (ValueError, TypeError):
            pass
    return merged


def _parse_semver_tuple(version_str: str) -> tuple[int, ...]:
    nums = [int(n) for n in re.findall(r"\d+", version_str or "")]
    return tuple(nums) if nums else (0,)


def _extract_wp_header(php_text: str, field: str, default: str = "") -> str:
    m = re.search(rf"^\s*\*\s*{re.escape(field)}\s*:\s*(.+?)\s*$", php_text, flags=re.MULTILINE)
    return m.group(1).strip() if m else default


def _format_wp_changelog_html(changelog_body: str) -> str:
    lines = []
    in_list = False
    for raw_line in changelog_body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        heading_match = re.match(r"^=+\s*(.+?)\s*=+$", line)
        if heading_match:
            if in_list:
                lines.append("</ul>")
                in_list = False
            lines.append(f"<h4>{html_lib.escape(heading_match.group(1))}</h4>")
            continue
        bullet_match = re.match(r"^[-*]\s+(.+)$", line)
        if bullet_match:
            if not in_list:
                lines.append("<ul>")
                in_list = True
            lines.append(f"<li>{html_lib.escape(bullet_match.group(1))}</li>")
            continue
        if in_list:
            lines.append("</ul>")
            in_list = False
        lines.append(f"<p>{html_lib.escape(line)}</p>")
    if in_list:
        lines.append("</ul>")
    return "\n".join(lines) or "<p>Self-hosted OmniBanner WordPress plugin update.</p>"


def _get_plugin_artifact() -> Optional[dict[str, Any]]:
    """Load packaged omnibanner.zip (or zip wp-plugin/) with cached metadata and SHA-256."""
    zip_bytes: Optional[bytes] = None
    cache_key: Any = None
    mtime_ts = 0.0

    for candidate in (
        os.path.join(BASE_DIR, "omnibanner.zip"),
        os.path.join(os.path.dirname(BASE_DIR), "omnibanner.zip"),
    ):
        if os.path.isfile(candidate):
            st = os.stat(candidate)
            cache_key = ("zip", candidate, st.st_mtime_ns, st.st_size)
            if _PLUGIN_ARTIFACT_CACHE.get("cache_key") == cache_key and _PLUGIN_ARTIFACT_CACHE.get("artifact"):
                return _PLUGIN_ARTIFACT_CACHE["artifact"]
            with open(candidate, "rb") as f:
                zip_bytes = f.read()
            mtime_ts = st.st_mtime
            break

    if zip_bytes is None:
        wp_dir = os.path.join(os.path.dirname(BASE_DIR), "wp-plugin")
        if os.path.isdir(wp_dir):
            mtimes = []
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for dirpath, _, filenames in os.walk(wp_dir):
                    for fn in sorted(filenames):
                        full = os.path.join(dirpath, fn)
                        mtimes.append((full, os.stat(full).st_mtime_ns))
                        zf.write(full, os.path.join("omnibanner", os.path.relpath(full, wp_dir)))
            zip_bytes = buf.getvalue()
            mtime_ts = max((m[1] for m in mtimes), default=0) / 1_000_000_000
            cache_key = ("dir", tuple(mtimes))

    if not zip_bytes:
        return None

    sha256 = hashlib.sha256(zip_bytes).hexdigest()
    php_text = ""
    readme_text = ""
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            if "omnibanner/omnibanner.php" in zf.namelist():
                php_text = zf.read("omnibanner/omnibanner.php").decode("utf-8", errors="replace")
            if "omnibanner/readme.txt" in zf.namelist():
                readme_text = zf.read("omnibanner/readme.txt").decode("utf-8", errors="replace")
    except Exception:
        pass

    tested_match = re.search(r"^\s*Tested up to\s*:\s*(.+?)\s*$", readme_text, flags=re.MULTILINE)
    changelog_match = re.search(r"==\s*Changelog\s*==\s*(.*)$", readme_text, flags=re.DOTALL | re.IGNORECASE)
    changelog_html = _format_wp_changelog_html(changelog_match.group(1) if changelog_match else "")

    description = _extract_wp_header(
        php_text,
        "Description",
        "Connect your WordPress site to a self-hosted OmniBanner server to display cache-safe scheduled notices.",
    )
    last_updated = datetime.fromtimestamp(mtime_ts or utcnow().timestamp(), tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    artifact = {
        "bytes": zip_bytes,
        "sha256": sha256,
        "size": len(zip_bytes),
        "name": _extract_wp_header(php_text, "Plugin Name", "OmniBanner"),
        "version": _extract_wp_header(php_text, "Version", APP_VERSION),
        "requires": _extract_wp_header(php_text, "Requires at least", "5.8"),
        "requires_php": _extract_wp_header(php_text, "Requires PHP", "7.4"),
        "tested": tested_match.group(1).strip() if tested_match else "6.8",
        "author": _extract_wp_header(php_text, "Author", "Tyler Hatfield"),
        "homepage": _extract_wp_header(php_text, "Plugin URI", "https://github.com/TylerHats/OmniBanner"),
        "description": description,
        "changelog_html": changelog_html,
        "last_updated": last_updated,
    }
    _PLUGIN_ARTIFACT_CACHE["cache_key"] = cache_key
    _PLUGIN_ARTIFACT_CACHE["artifact"] = artifact
    return artifact


def _serialize_site(
    site: Site,
    stats: Optional[dict[str, int]] = None,
    latest_plugin_version: Optional[str] = None,
) -> dict[str, Any]:
    now = utcnow()
    online = bool(site.last_seen_at and (now - site.last_seen_at) <= timedelta(hours=24))
    if latest_plugin_version is None:
        art = _get_plugin_artifact()
        latest_plugin_version = (art.get("version") if art else None) or APP_VERSION
    cur_ver = (site.plugin_version or "").strip()
    update_available = bool(
        cur_ver and _parse_semver_tuple(cur_ver) < _parse_semver_tuple(latest_plugin_version)
    )
    return {
        "id": site.id,
        "name": site.name,
        "domain": site.domain,
        "site_url": site.site_url,
        "site_key_prefix": site.site_key_prefix,
        "groups": site.groups or "",
        "enabled": bool(site.enabled),
        "plugin_version": cur_ver,
        "latest_plugin_version": latest_plugin_version,
        "update_available": update_available,
        "wp_version": site.wp_version or "",
        "last_seen_at": _fmt_utc(site.last_seen_at),
        "created_at": _fmt_utc(site.created_at),
        "online": online,
        "settings": _parse_site_settings(site.settings_json),
        "stats": stats or {"views": 0, "dismisses": 0, "clicks": 0},
    }


def _serialize_notice(n: Notice, site_stats: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    now = utcnow()
    if not n.enabled:
        status_label = "paused"
    elif now < n.scheduled_start:
        status_label = "scheduled"
    elif now > n.scheduled_end:
        status_label = "expired"
    else:
        status_label = "active"

    return {
        "id": n.id,
        "title": n.title or "",
        "banner_text": n.banner_text or "",
        "text": n.banner_text or "",  # Backward-compatible alias
        "bg_color": n.bg_color or "#ef4444",
        "text_color": n.text_color or "#ffffff",
        "enabled": bool(n.enabled),
        "status": status_label,
        "priority": n.priority if n.priority is not None else 10,
        "position": n.position or "top",
        "icon": n.icon or "",
        "cta_text": n.cta_text or "",
        "cta_url": n.cta_url or "",
        "cta_new_tab": bool(n.cta_new_tab if n.cta_new_tab is not None else True),
        "cta_bg_color": n.cta_bg_color or "#ffffff",
        "cta_text_color": n.cta_text_color or "#0f172a",
        "show_countdown": bool(n.show_countdown),
        "display_duration": n.display_duration or 0,
        "dismissible": bool(n.dismissible),
        "dismiss_cooldown": n.dismiss_cooldown or 24,
        "scheduled_start": _fmt_utc(n.scheduled_start),
        "scheduled_end": _fmt_utc(n.scheduled_end),
        "target_type": n.target_type or "all",
        "target_sites": n.target_sites or "",
        "target_groups": n.target_groups or "",
        "exclude_sites": n.exclude_sites or "",
        "target_paths": n.target_paths or "",
        "audience": n.audience or "all",
        "send_smtp": bool(n.send_smtp),
        "smtp_sent": bool(n.smtp_sent),
        "smtp_error": n.smtp_error or "",
        "smtp_sent_count": n.smtp_sent_count or 0,
        "target_subscriber_tags": n.target_subscriber_tags or "",
        "uptime_kuma_integration": bool(n.uptime_kuma_integration),
        "uptime_kuma_push_url": n.uptime_kuma_push_url or "",
        "uptime_kuma_monitor_ids": n.uptime_kuma_monitor_ids or "",
        "kuma_text": n.kuma_text or "",
        "kuma_maintenance_id": n.kuma_maintenance_id,
        "email_subject": n.email_subject or "",
        "email_text": n.email_text or "",
        "email_lead_minutes": n.email_lead_minutes or 0,
        "recurrence": n.recurrence or "none",
        "views_count": n.views_count or 0,
        "dismiss_count": n.dismiss_count or 0,
        "clicks_count": n.clicks_count or 0,
        "created_by": n.created_by or "",
        "created_at": _fmt_utc(n.created_at),
        "updated_at": _fmt_utc(n.updated_at),
        "site_stats": site_stats or [],
    }


def _serialize_oidc_provider(p: OIDCProvider) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "slug": p.slug,
        "issuer_url": p.issuer_url,
        "client_id": p.client_id,
        "has_secret": bool(p.client_secret),
        "scopes": p.scopes or "openid profile email groups",
        "button_label": p.button_label or "",
        "groups_claim": p.groups_claim or "groups",
        "admin_groups": p.admin_groups or "",
        "editor_groups": p.editor_groups or "",
        "viewer_groups": p.viewer_groups or "",
        "require_group_match": bool(p.require_group_match),
        "auto_create_users": bool(p.auto_create_users),
        "verify_tls": bool(p.verify_tls),
        "ca_bundle_path": p.ca_bundle_path or "",
        "end_session_logout": bool(p.end_session_logout),
        "enabled": bool(p.enabled),
    }


# --- AUTH DEPENDENCIES ---


def _is_trusted_proxy_ip(client_host: Optional[str], trusted_csv: str) -> bool:
    if not client_host:
        return False
    entries = [e.strip() for e in (trusted_csv or "").split(",") if e.strip()]
    if "*" in entries:
        return True
    if client_host == "testclient":
        return ("testclient" in entries) or ("127.0.0.1" in entries)
    try:
        addr = ipaddress.ip_address(client_host)
    except ValueError:
        return client_host in entries
    for entry in entries:
        try:
            if addr in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            if client_host == entry:
                return True
    return False


def _parse_proxy_groups(raw: Optional[str]) -> set[str]:
    if not raw:
        return set()
    parts = re.split(r"[|,;]+", raw)
    return {p.strip().lower() for p in parts if p.strip()}


async def get_current_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    token = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
    if not token:
        token = request.cookies.get("access_token")

    if token:
        payload = auth.decode_access_token(token)
        if not payload:
            return None
        jti = payload.get("jti")
        if jti and db.query(RevokedToken.id).filter(RevokedToken.jti == jti).first():
            return None
        username = payload.get("sub")
        if not username:
            return None
        user = db.query(User).filter(User.username == username, User.is_active.is_(True)).first()
        return user

    # Optional reverse-proxy Forward-Auth (e.g. Authentik Proxy Provider)
    config = db.query(SystemConfig).first()
    if config and config.allow_proxy_auth and config.setup_completed:
        peer_host = request.client.host if request.client else ""
        if not _is_trusted_proxy_ip(peer_host, config.trusted_proxy_ips or ""):
            return None

        hdr_name = config.proxy_auth_header or "X-authentik-username"
        proxy_user = request.headers.get(hdr_name)
        if proxy_user:
            clean_name = proxy_user.strip()
            groups_hdr = config.proxy_auth_groups_header or "X-authentik-groups"
            raw_groups = request.headers.get(groups_hdr, "")
            user_groups = _parse_proxy_groups(raw_groups)
            admin_groups = _parse_csv(config.proxy_admin_groups)
            editor_groups = _parse_csv(config.proxy_editor_groups)

            mapped_role = "viewer"
            if admin_groups and (user_groups & admin_groups):
                mapped_role = "admin"
            elif editor_groups and (user_groups & editor_groups):
                mapped_role = "editor"

            user = db.query(User).filter(User.username == clean_name).first()
            if not user:
                user = User(username=clean_name, hashed_password="", role=mapped_role, is_active=True)
                db.add(user)
                db.commit()
                db.refresh(user)
            elif not user.is_active:
                return None
            elif (admin_groups or editor_groups) and user.role != mapped_role:
                user.role = mapped_role
                db.commit()
            return user
    return None


async def require_user(user: Optional[User] = Depends(get_current_user)) -> User:
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user


async def require_editor(user: User = Depends(require_user)) -> User:
    if user.role not in ("admin", "editor"):
        raise HTTPException(status_code=403, detail="Editor or admin role required")
    return user


async def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Administrator role required")
    return user


# --- PAGE ROUTERS ---


@app.get("/", response_class=HTMLResponse)
async def read_root(db: Session = Depends(get_db)):
    if not is_setup_completed(db):
        return RedirectResponse(url="/setup")
    return RedirectResponse(url="/dashboard")


@app.get("/setup", response_class=HTMLResponse)
async def get_setup(request: Request, db: Session = Depends(get_db)):
    if is_setup_completed(db):
        return RedirectResponse(url="/login")
    ctx = {
        "request": request,
        "app_name": "OmniBanner Initial Setup",
        "app_icon_url": "/static/brand/logo.png",
        "favicon_url": "/static/brand/logo.png",
        "primary_color": "#8b5cf6",
        "secondary_color": "#6366f1",
        "active_theme": "dark",
        "custom_css": "",
        "footer_text": "",
        "show_nav": False,
    }
    return templates.TemplateResponse(request, "setup.html", ctx)


@app.get("/login", response_class=HTMLResponse)
async def get_login(request: Request, error: Optional[str] = None, local: Optional[str] = None, db: Session = Depends(get_db)):
    if not is_setup_completed(db):
        return RedirectResponse(url="/setup")
    user = await get_current_user(request, db)
    if user:
        return RedirectResponse(url="/dashboard")

    brand = _get_brand_context(db)
    providers = db.query(OIDCProvider).filter(OIDCProvider.enabled.is_(True)).all()
    show_local = (not brand["hide_local_login"]) or (not providers) or (local == "1")

    error_map = {
        "oidc_cancelled": "Single Sign-On was cancelled or denied by your identity provider.",
        "invalid_state": "Login session expired or state mismatch. Please try again.",
        "token_exchange": "Failed to exchange authorization code with identity provider.",
        "invalid_id_token": "Identity provider ID token signature or nonce verification failed.",
        "group_denied": "Your account does not belong to an authorized group for OmniBanner.",
        "provisioning_disabled": "Automatic user provisioning is disabled. Contact an administrator.",
        "user_disabled": "Your account has been disabled.",
    }
    error_message = error_map.get(error, error) if error else ""

    ctx = {
        "request": request,
        **brand,
        "show_nav": False,
        "oidc_providers": providers,
        "show_local_form": show_local,
        "error_message": error_message,
    }
    return templates.TemplateResponse(request, "login.html", ctx)


@app.get("/dashboard", response_class=HTMLResponse)
async def get_dashboard(request: Request, db: Session = Depends(get_db)):
    if not is_setup_completed(db):
        return RedirectResponse(url="/setup")
    user = await get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/login")

    brand = _get_brand_context(db, user)
    ctx = {
        "request": request,
        **brand,
        "show_nav": True,
        "user_display": user.username,
        "user_role": user.role,
    }
    return templates.TemplateResponse(request, "dashboard.html", ctx)


@app.get("/totp-setup", response_class=HTMLResponse)
async def get_totp_setup(request: Request, db: Session = Depends(get_db)):
    if not is_setup_completed(db):
        return RedirectResponse(url="/setup")
    user = await get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/login")

    brand = _get_brand_context(db, user)
    secret = auth.generate_totp_secret()
    totp_uri = auth.get_totp_uri(secret, user.username, issuer_name=brand["app_name"])
    ctx = {
        "request": request,
        **brand,
        "show_nav": True,
        "user_display": user.username,
        "user_role": user.role,
        "totp_secret": secret,
        "totp_uri": totp_uri,
    }
    return templates.TemplateResponse(request, "totp_setup.html", ctx)


@app.get("/pair/{token}", response_class=HTMLResponse)
async def get_pair_page(token: str, request: Request, db: Session = Depends(get_db)):
    brand = _get_brand_context(db)
    token_hash = auth.hash_token(token.strip())
    record = db.query(SitePairingToken).filter(SitePairingToken.token_hash == token_hash).first()
    now = utcnow()
    if not record:
        status_state = "invalid"
    elif record.used_at is not None:
        status_state = "used"
    elif record.expires_at < now:
        status_state = "expired"
    else:
        status_state = "valid"

    base_url = resolve_public_base_url(request, brand.get("public_base_url", ""))
    pairing_url = f"{base_url}/pair/{token.strip()}"
    ctx = {
        "request": request,
        **brand,
        "show_nav": False,
        "status_state": status_state,
        "pairing_url": pairing_url,
        "preset_name": record.preset_name if record else "",
        "preset_groups": record.preset_groups if record else "",
        "expires_at": _fmt_utc(record.expires_at) if record else "",
    }
    return templates.TemplateResponse(request, "pair_info.html", ctx)


@app.get("/unsubscribe/{token}", response_class=HTMLResponse)
async def get_unsubscribe(token: str, request: Request, db: Session = Depends(get_db)):
    brand = _get_brand_context(db)
    sub = db.query(Subscriber).filter(Subscriber.unsubscribe_token == token).first()
    unsubscribed = False
    email_label = ""
    if sub:
        sub.active = False
        db.commit()
        unsubscribed = True
        email_label = sub.email
    ctx = {
        "request": request,
        **brand,
        "show_nav": False,
        "unsubscribed": unsubscribed,
        "email_label": email_label,
    }
    return templates.TemplateResponse(request, "unsubscribe.html", ctx)


@app.get("/logout")
async def get_logout(request: Request, db: Session = Depends(get_db)):
    return await _perform_logout(request, db)


@app.post("/api/logout")
async def post_logout(request: Request, db: Session = Depends(get_db)):
    return await _perform_logout(request, db, json_response=True)


async def _perform_logout(request: Request, db: Session, json_response: bool = False):
    token = None
    ah = request.headers.get("Authorization", "")
    if ah.startswith("Bearer "):
        token = ah[7:].strip()
    if not token:
        token = request.cookies.get("access_token")

    oidc_logout_url: Optional[str] = None
    if token:
        payload = auth.decode_access_token(token)
        if payload:
            username = payload.get("sub")
            if username:
                user = db.query(User).filter(User.username == username).first()
                if user and user.oidc_provider_id:
                    provider = db.query(OIDCProvider).filter(
                        OIDCProvider.id == user.oidc_provider_id,
                        OIDCProvider.enabled.is_(True),
                        OIDCProvider.end_session_logout.is_(True),
                    ).first()
                    if provider:
                        discovery = await auth.discover_oidc_endpoints(
                            provider.issuer_url,
                            verify_tls=bool(provider.verify_tls),
                            ca_bundle_path=provider.ca_bundle_path or "",
                        )
                        if discovery and discovery.get("end_session_endpoint"):
                            config = db.query(SystemConfig).first()
                            base_url = resolve_public_base_url(request, config.public_base_url if config else "")
                            end_ep = discovery["end_session_endpoint"]
                            qs = urlencode({
                                "client_id": provider.client_id,
                                "post_logout_redirect_uri": f"{base_url}/login",
                            })
                            oidc_logout_url = f"{end_ep}{'&' if '?' in end_ep else '?'}{qs}"

            if payload.get("jti"):
                exp_ts = payload.get("exp")
                exp_dt = (
                    datetime.fromtimestamp(exp_ts, tz=timezone.utc).replace(tzinfo=None)
                    if exp_ts
                    else utcnow() + timedelta(days=1)
                )
                if not db.query(RevokedToken).filter(RevokedToken.jti == payload["jti"]).first():
                    db.add(RevokedToken(jti=payload["jti"], expires_at=exp_dt))
                    db.commit()

    if json_response:
        body: dict[str, Any] = {"success": True}
        if oidc_logout_url:
            body["redirect_url"] = oidc_logout_url
        resp: Response = JSONResponse(body)
    else:
        resp = RedirectResponse(url=oidc_logout_url or "/login", status_code=302)
    resp.delete_cookie("access_token", path="/")
    return resp


@app.get("/status", response_class=HTMLResponse)
async def get_public_status_page(request: Request, db: Session = Depends(get_db)):
    config = db.query(SystemConfig).first()
    if config and config.public_status_page is False:
        raise HTTPException(status_code=404, detail="Public status page is disabled")

    brand = _get_brand_context(db)
    now = utcnow()
    notices = (
        db.query(Notice)
        .filter(Notice.enabled.is_(True), Notice.scheduled_end >= now)
        .order_by(Notice.priority.desc(), Notice.scheduled_start.asc())
        .all()
    )
    active_list = [_serialize_notice(n) for n in notices if n.scheduled_start <= now]
    upcoming_list = [_serialize_notice(n) for n in notices if n.scheduled_start > now]
    return templates.TemplateResponse(
        request,
        "status.html",
        {
            "request": request,
            **brand,
            "show_nav": False,
            "active_notices": active_list,
            "upcoming_notices": upcoming_list,
        },
    )


def _ics_escape(val: Optional[str]) -> str:
    text = (val or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n")
    return text


@app.get("/status.ics")
async def get_public_status_ics(db: Session = Depends(get_db)):
    config = db.query(SystemConfig).first()
    if config and config.public_status_page is False:
        raise HTTPException(status_code=404, detail="Public status feed is disabled")

    brand = _get_brand_context(db)
    now = utcnow()
    notices = (
        db.query(Notice)
        .filter(Notice.enabled.is_(True), Notice.scheduled_end >= now)
        .order_by(Notice.scheduled_start.asc())
        .all()
    )
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//OmniBanner//Status Feed//EN",
        f"X-WR-CALNAME:{_ics_escape(brand['app_name'])} Maintenance & Notices",
    ]
    for n in notices:
        plain_desc = re.sub(r"<[^>]+>", "", n.banner_text or "")
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:omnibanner-{n.id}@omnibanner",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{n.scheduled_start.strftime('%Y%m%dT%H%M%SZ')}",
            f"DTEND:{n.scheduled_end.strftime('%Y%m%dT%H%M%SZ')}",
            f"SUMMARY:{_ics_escape(n.title or plain_desc[:60] or 'Scheduled Notice')}",
            f"DESCRIPTION:{_ics_escape(plain_desc)}",
            "END:VEVENT",
        ])
    lines.append("END:VCALENDAR")
    ics_payload = "\r\n".join(lines) + "\r\n"
    return Response(
        content=ics_payload,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=omnibanner-status.ics"},
    )


@app.get("/embed.js")
async def get_embed_js():
    embed_path = os.path.join(BASE_DIR, "static", "js", "embed.js")
    return FileResponse(
        embed_path,
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "version": APP_VERSION}


# --- SETUP API ---


@app.post("/api/setup")
async def post_setup(data: SetupRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    config = db.query(SystemConfig).first()
    if config and config.setup_completed:
        raise HTTPException(status_code=400, detail="Setup already completed")

    if not config:
        config = SystemConfig()
        db.add(config)

    config.app_name = data.app_name.strip() or "OmniBanner"
    config.app_icon_url = sanitize_url(data.app_icon_url, "/static/brand/logo.png") or "/static/brand/logo.png"
    config.favicon_url = sanitize_url(data.favicon_url, config.app_icon_url) or config.app_icon_url
    config.primary_color = sanitize_hex_color(data.primary_color, "#8b5cf6")
    config.secondary_color = sanitize_hex_color(data.secondary_color, "#6366f1")
    config.default_theme = data.default_theme if data.default_theme in ("dark", "light", "system") else "dark"
    config.public_base_url = (data.public_base_url or "").strip().rstrip("/")
    config.default_timezone = data.default_timezone or "UTC"

    if data.auth_mode == "credentials" and not data.admin_password:
        raise HTTPException(status_code=400, detail="Password is required for credentials setup")

    db.query(User).delete()
    admin_password = data.admin_password or secrets.token_urlsafe(24)
    admin = User(
        username=(data.admin_username or "admin").strip() or "admin",
        hashed_password=auth.get_password_hash(admin_password),
        role="admin",
        is_active=True,
        totp_enabled=False,  # Verified via /totp-setup so user is never locked out!
    )
    db.add(admin)

    if data.auth_mode == "oidc" or (data.oidc_issuer_url and data.oidc_client_id):
        if data.auth_mode == "oidc" and (not data.oidc_issuer_url or not data.oidc_client_id):
            raise HTTPException(status_code=400, detail="OIDC Issuer URL and Client ID are required")
        slug = re.sub(r"[^a-z0-9-]+", "-", (data.oidc_provider_name or "authentik").lower()).strip("-") or "sso"
        db.add(
            OIDCProvider(
                name=data.oidc_provider_name or "Authentik",
                slug=slug,
                issuer_url=data.oidc_issuer_url.strip(),
                client_id=data.oidc_client_id.strip(),
                client_secret=encrypt_secret(data.oidc_client_secret),
                scopes=data.oidc_scopes or "openid profile email groups",
                groups_claim=data.oidc_groups_claim or "groups",
                admin_groups=data.oidc_admin_groups or "",
                verify_tls=data.oidc_verify_tls,
                enabled=True,
            )
        )

    if data.smtp_host.strip():
        smtp_conf = db.query(SMTPSettings).first() or SMTPSettings()
        if not smtp_conf.id:
            db.add(smtp_conf)
        smtp_conf.host = data.smtp_host.strip()
        smtp_conf.port = int(data.smtp_port or 587)
        smtp_conf.tls_mode = data.smtp_tls_mode or ("ssl" if smtp_conf.port == 465 else "starttls")
        smtp_conf.username = data.smtp_username.strip()
        smtp_conf.password = encrypt_secret(data.smtp_password)
        smtp_conf.from_email = data.smtp_from_email.strip()
        smtp_conf.from_name = data.smtp_from_name.strip() or "OmniBanner Alerts"
        smtp_conf.enabled = True

    if data.kuma_url.strip():
        kuma_conf = db.query(UptimeKumaSettings).first() or UptimeKumaSettings()
        if not kuma_conf.id:
            db.add(kuma_conf)
        kuma_conf.url = data.kuma_url.strip().rstrip("/")
        kuma_conf.username = data.kuma_username.strip()
        kuma_conf.password = encrypt_secret(data.kuma_password)
        kuma_conf.verify_tls = data.kuma_verify_tls
        kuma_conf.enabled = True

    config.setup_completed = True
    db.commit()
    db.refresh(admin)

    _log_audit(db, admin.username, "setup_completed", "system", "1", f"auth_mode={data.auth_mode}", request)

    if data.enable_totp:
        token, _jti, _exp = auth.create_access_token(admin.id, admin.username, admin.role)
        _set_session_cookie(response, request, token)
        return {"success": True, "redirect": "/totp-setup"}

    return {"success": True, "redirect": "/login"}


# --- AUTH API ---


@app.post("/api/login-step1")
async def login_step1(credentials: LoginStep1Request, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = _client_ip(request)
    if not login_limiter.allow(f"login:{ip}"):
        raise HTTPException(status_code=429, detail="Too many login attempts. Please wait a minute.")

    user = db.query(User).filter(User.username == credentials.username.strip()).first()
    if not user or not user.is_active or not auth.verify_password(credentials.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if user.totp_enabled:
        mfa_token = auth.create_mfa_challenge_token(user.id, user.username)
        return {"mfa_required": True, "mfa_token": mfa_token}

    user.last_login_at = utcnow()
    db.commit()
    token, _jti, _exp = auth.create_access_token(user.id, user.username, user.role)
    _set_session_cookie(response, request, token)
    _log_audit(db, user.username, "login", "user", str(user.id), "local_credentials", request)
    return {"mfa_required": False, "user": {"username": user.username, "role": user.role}}


@app.post("/api/login-step2")
async def login_step2(credentials: LoginStep2Request, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = _client_ip(request)
    if not login_limiter.allow(f"mfa:{ip}"):
        raise HTTPException(status_code=429, detail="Too many verification attempts. Please wait.")

    user: Optional[User] = None
    if credentials.mfa_token:
        payload = auth.decode_token(credentials.mfa_token, expected_type="mfa_challenge")
        if not payload:
            raise HTTPException(status_code=401, detail="MFA challenge expired. Please sign in again.")
        user = db.query(User).filter(User.id == payload.get("uid"), User.is_active.is_(True)).first()
    elif credentials.username and credentials.password:
        candidate = db.query(User).filter(User.username == credentials.username.strip(), User.is_active.is_(True)).first()
        if candidate and auth.verify_password(credentials.password, candidate.hashed_password):
            user = candidate

    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.totp_enabled:
        raise HTTPException(status_code=400, detail="MFA is not enabled on this account")

    secret = decrypt_secret(user.totp_secret)
    if not secret:
        raise HTTPException(status_code=500, detail="MFA secret key is missing")

    verified = auth.verify_totp_code(secret, credentials.mfa_code)
    used_backup = False
    if not verified:
        matched_backup, updated_json = auth.consume_backup_code(user.totp_backup_codes, credentials.mfa_code)
        if matched_backup:
            verified = True
            used_backup = True
            user.totp_backup_codes = updated_json

    if not verified:
        raise HTTPException(status_code=401, detail="Invalid verification or backup code")

    user.last_login_at = utcnow()
    db.commit()
    token, _jti, _exp = auth.create_access_token(user.id, user.username, user.role)
    _set_session_cookie(response, request, token)
    _log_audit(
        db,
        user.username,
        "login_mfa",
        "user",
        str(user.id),
        "backup_code" if used_backup else "totp",
        request,
    )
    return {"success": True, "used_backup_code": used_backup, "user": {"username": user.username, "role": user.role}}


@app.get("/api/me")
async def get_me(user: User = Depends(require_user), db: Session = Depends(get_db)):
    brand = _get_brand_context(db, user)
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email or "",
        "role": user.role,
        "totp_enabled": bool(user.totp_enabled),
        "is_oidc": bool(user.oidc_sub),
        "theme_preference": user.theme_preference or "",
        "active_theme": brand["active_theme"],
    }


@app.put("/api/me/theme")
async def update_my_theme(data: dict, user: User = Depends(require_user), db: Session = Depends(get_db)):
    theme = data.get("theme", "dark")
    if theme not in ("dark", "light", "system", ""):
        raise HTTPException(status_code=400, detail="Invalid theme")
    user.theme_preference = theme or None
    db.commit()
    return {"success": True, "theme_preference": user.theme_preference}


@app.post("/api/me/password")
async def change_my_password(
    data: ChangePasswordRequest, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    if user.oidc_sub or not user.hashed_password:
        raise HTTPException(status_code=400, detail="Password changes are managed by your SSO provider")
    if not auth.verify_password(data.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if len(data.new_password or "") < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")
    user.hashed_password = auth.get_password_hash(data.new_password)
    db.commit()
    _log_audit(db, user.username, "password_changed", "user", str(user.id), "", request)
    return {"success": True}


@app.get("/api/security/status")
async def security_status(user: User = Depends(require_user)):
    return {
        "totp_enabled": bool(user.totp_enabled),
        "is_oidc": bool(user.oidc_sub),
        "role": user.role,
    }


@app.post("/api/totp/verify")
async def verify_totp(data: TOTPVerifyRequest, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    if not auth.verify_totp_code(data.secret, data.code):
        raise HTTPException(status_code=400, detail="Invalid verification code")

    plain_codes, hashed_json = auth.generate_backup_codes(8)
    user.totp_secret = encrypt_secret(data.secret)
    user.totp_enabled = True
    user.totp_backup_codes = hashed_json
    db.commit()
    _log_audit(db, user.username, "totp_enabled", "user", str(user.id), "", request)
    return {"success": True, "backup_codes": plain_codes}


@app.post("/api/totp/disable")
async def disable_totp(data: TOTPDisableRequest, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    if not user.totp_enabled:
        return {"success": True}

    secret = decrypt_secret(user.totp_secret)
    authorized = False
    if data.password and user.hashed_password and auth.verify_password(data.password, user.hashed_password):
        authorized = True
    elif data.code and secret and auth.verify_totp_code(secret, data.code):
        authorized = True
    elif data.code and user.totp_backup_codes:
        ok, _ = auth.consume_backup_code(user.totp_backup_codes, data.code)
        authorized = ok

    if not authorized:
        raise HTTPException(status_code=400, detail="Current password or valid MFA code required to disable MFA")

    user.totp_enabled = False
    user.totp_secret = None
    user.totp_backup_codes = None
    db.commit()
    _log_audit(db, user.username, "totp_disabled", "user", str(user.id), "", request)
    return {"success": True}


# --- OIDC PROVIDERS & AUTH FLOW ---


@app.get("/api/oidc/providers")
async def list_oidc_providers(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    providers = db.query(OIDCProvider).order_by(OIDCProvider.id.asc()).all()
    return [_serialize_oidc_provider(p) for p in providers]


@app.post("/api/oidc/providers")
async def create_oidc_provider(data: OIDCProviderRequest, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    raw_slug = data.slug or data.name or "oidc"
    slug = re.sub(r"[^a-z0-9-]+", "-", raw_slug.lower()).strip("-") or "oidc"
    if db.query(OIDCProvider).filter(OIDCProvider.slug == slug).first():
        slug = f"{slug}-{secrets.token_hex(2)}"

    provider = OIDCProvider(
        name=data.name.strip() or "OIDC Provider",
        slug=slug,
        issuer_url=data.issuer_url.strip(),
        client_id=data.client_id.strip(),
        client_secret=encrypt_secret(data.client_secret),
        scopes=data.scopes.strip() or "openid profile email groups",
        button_label=data.button_label.strip(),
        groups_claim=data.groups_claim.strip() or "groups",
        admin_groups=data.admin_groups.strip(),
        editor_groups=data.editor_groups.strip(),
        viewer_groups=data.viewer_groups.strip(),
        require_group_match=data.require_group_match,
        auto_create_users=data.auto_create_users,
        verify_tls=data.verify_tls,
        ca_bundle_path=data.ca_bundle_path.strip(),
        end_session_logout=data.end_session_logout,
        enabled=data.enabled,
    )
    db.add(provider)
    db.commit()
    db.refresh(provider)
    _log_audit(db, user.username, "oidc_provider_created", "oidc_provider", str(provider.id), provider.slug, request)
    return _serialize_oidc_provider(provider)


@app.put("/api/oidc/providers/{provider_id}")
async def update_oidc_provider(
    provider_id: int, data: OIDCProviderRequest, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)
):
    provider = db.query(OIDCProvider).filter(OIDCProvider.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="OIDC provider not found")

    provider.name = data.name.strip() or provider.name
    if data.slug.strip():
        candidate = re.sub(r"[^a-z0-9-]+", "-", data.slug.lower()).strip("-")
        if candidate and not db.query(OIDCProvider).filter(OIDCProvider.slug == candidate, OIDCProvider.id != provider_id).first():
            provider.slug = candidate
    provider.issuer_url = data.issuer_url.strip()
    provider.client_id = data.client_id.strip()
    if data.client_secret:
        provider.client_secret = encrypt_secret(data.client_secret)
    provider.scopes = data.scopes.strip() or "openid profile email groups"
    provider.button_label = data.button_label.strip()
    provider.groups_claim = data.groups_claim.strip() or "groups"
    provider.admin_groups = data.admin_groups.strip()
    provider.editor_groups = data.editor_groups.strip()
    provider.viewer_groups = data.viewer_groups.strip()
    provider.require_group_match = data.require_group_match
    provider.auto_create_users = data.auto_create_users
    provider.verify_tls = data.verify_tls
    provider.ca_bundle_path = data.ca_bundle_path.strip()
    provider.end_session_logout = data.end_session_logout
    provider.enabled = data.enabled
    db.commit()
    _log_audit(db, user.username, "oidc_provider_updated", "oidc_provider", str(provider.id), provider.slug, request)
    return _serialize_oidc_provider(provider)


@app.delete("/api/oidc/providers/{provider_id}")
async def delete_oidc_provider(provider_id: int, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    provider = db.query(OIDCProvider).filter(OIDCProvider.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="OIDC provider not found")
    db.delete(provider)
    db.commit()
    _log_audit(db, user.username, "oidc_provider_deleted", "oidc_provider", str(provider_id), "", request)
    return {"success": True}


@app.post("/api/oidc/test")
async def test_oidc_endpoint(data: OIDCTestRequest, request: Request, db: Session = Depends(get_db)):
    if is_setup_completed(db):
        user = await get_current_user(request, db)
        if not user or user.role != "admin":
            raise HTTPException(status_code=401, detail="Administrator authorization required")
    return await auth.test_oidc_provider(
        data.issuer_url, verify_tls=data.verify_tls, ca_bundle_path=data.ca_bundle_path
    )


async def _start_oidc_login(request: Request, db: Session, slug: Optional[str] = None):
    q = db.query(OIDCProvider).filter(OIDCProvider.enabled.is_(True))
    provider = q.filter(OIDCProvider.slug == slug).first() if slug else q.order_by(OIDCProvider.id.asc()).first()
    if not provider:
        raise HTTPException(status_code=400, detail="OIDC provider is not configured or enabled")

    discovery = await auth.discover_oidc_endpoints(
        provider.issuer_url, verify_tls=bool(provider.verify_tls), ca_bundle_path=provider.ca_bundle_path or ""
    )
    if not discovery or not discovery.get("authorization_endpoint"):
        raise HTTPException(status_code=502, detail="OIDC discovery failed")

    config = db.query(SystemConfig).first()
    base_url = resolve_public_base_url(request, config.public_base_url if config else "")
    redirect_uri = f"{base_url}/auth/oidc/{provider.slug}/callback"

    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier, challenge = auth.generate_pkce_pair()
    tx_token = auth.create_oidc_tx_token(provider.slug, state, nonce, verifier, "/dashboard")
    auth_url = auth.build_oidc_authorization_url(
        discovery=discovery,
        client_id=provider.client_id,
        redirect_uri=redirect_uri,
        scopes=provider.scopes or "openid profile email",
        state=state,
        nonce=nonce,
        code_challenge=challenge,
    )

    resp = RedirectResponse(url=auth_url, status_code=302)
    is_https = request.url.scheme == "https" or (
        request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower() == "https"
    )
    resp.set_cookie(
        key="oidc_tx",
        value=tx_token,
        max_age=600,
        path="/auth/oidc",
        httponly=True,
        samesite="lax",
        secure=is_https,
    )
    return resp


@app.get("/auth/oidc/login")
async def oidc_login_default(request: Request, provider: Optional[str] = None, db: Session = Depends(get_db)):
    return await _start_oidc_login(request, db, slug=provider)


@app.get("/auth/oidc/{slug}/login")
async def oidc_login_slug(slug: str, request: Request, db: Session = Depends(get_db)):
    return await _start_oidc_login(request, db, slug=slug)


async def _handle_oidc_callback(
    request: Request,
    db: Session,
    slug: Optional[str] = None,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    if error:
        return RedirectResponse(url="/login?error=oidc_cancelled", status_code=302)

    tx = auth.decode_oidc_tx_token(request.cookies.get("oidc_tx"))
    if not tx or not state or not code:
        return RedirectResponse(url="/login?error=invalid_state", status_code=302)
    if not secrets.compare_digest(str(tx.get("state", "")), str(state)):
        return RedirectResponse(url="/login?error=invalid_state", status_code=302)

    provider_slug = slug or tx.get("provider_slug")
    if slug and tx.get("provider_slug") != slug:
        return RedirectResponse(url="/login?error=invalid_state", status_code=302)

    provider = db.query(OIDCProvider).filter(OIDCProvider.slug == provider_slug, OIDCProvider.enabled.is_(True)).first()
    if not provider:
        return RedirectResponse(url="/login?error=invalid_state", status_code=302)

    discovery = await auth.discover_oidc_endpoints(
        provider.issuer_url, verify_tls=bool(provider.verify_tls), ca_bundle_path=provider.ca_bundle_path or ""
    )
    if not discovery:
        return RedirectResponse(url="/login?error=token_exchange", status_code=302)

    config = db.query(SystemConfig).first()
    base_url = resolve_public_base_url(request, config.public_base_url if config else "")
    redirect_uri = f"{base_url}/auth/oidc/{provider.slug}/callback"

    tokens = await auth.exchange_oidc_code(
        token_endpoint=discovery.get("token_endpoint", ""),
        client_id=provider.client_id,
        client_secret=decrypt_secret(provider.client_secret),
        code=code,
        redirect_uri=redirect_uri,
        code_verifier=tx.get("code_verifier", ""),
        verify_tls=bool(provider.verify_tls),
        ca_bundle_path=provider.ca_bundle_path or "",
    )
    if not tokens:
        return RedirectResponse(url="/login?error=token_exchange", status_code=302)

    id_token = tokens.get("id_token", "")
    id_claims = await auth.verify_id_token(
        id_token=id_token,
        discovery=discovery,
        client_id=provider.client_id,
        expected_nonce=tx.get("nonce", ""),
        verify_tls=bool(provider.verify_tls),
        ca_bundle_path=provider.ca_bundle_path or "",
    )
    if not id_claims:
        return RedirectResponse(url="/login?error=invalid_id_token", status_code=302)

    userinfo: dict[str, Any] = {}
    if discovery.get("userinfo_endpoint") and tokens.get("access_token"):
        fetched = await auth.fetch_oidc_userinfo(
            discovery["userinfo_endpoint"],
            tokens["access_token"],
            verify_tls=bool(provider.verify_tls),
            ca_bundle_path=provider.ca_bundle_path or "",
        )
        if isinstance(fetched, dict):
            userinfo = fetched

    merged_claims = dict(userinfo)
    merged_claims.update(id_claims)
    groups_claim_name = provider.groups_claim or "groups"
    if not merged_claims.get(groups_claim_name) and userinfo.get(groups_claim_name):
        merged_claims[groups_claim_name] = userinfo[groups_claim_name]

    role = auth.resolve_oidc_role(merged_claims, provider)
    if role is None:
        return RedirectResponse(url="/login?error=group_denied", status_code=302)

    sub = str(id_claims.get("sub"))
    email = merged_claims.get("email") or userinfo.get("email")
    desired_username = (
        merged_claims.get("preferred_username")
        or merged_claims.get("username")
        or email
        or sub
    )

    user = (
        db.query(User)
        .filter(User.oidc_provider_id == provider.id, User.oidc_sub == sub)
        .first()
    )
    if not user:
        user = db.query(User).filter(User.oidc_sub == sub, User.oidc_provider_id.is_(None)).first()

    if not user:
        if not provider.auto_create_users:
            return RedirectResponse(url="/login?error=provisioning_disabled", status_code=302)
        candidate_name = str(desired_username).strip() or f"oidc-{sub[:8]}"
        if db.query(User).filter(User.username == candidate_name).first():
            suffix = hashlib.sha256(f"{provider.id}:{sub}".encode()).hexdigest()[:6]
            candidate_name = f"{candidate_name}-{suffix}"
        user = User(
            username=candidate_name,
            hashed_password="",
            role=role,
            is_active=True,
            oidc_provider_id=provider.id,
            oidc_sub=sub,
            email=email,
        )
        db.add(user)
    else:
        if not user.is_active:
            return RedirectResponse(url="/login?error=user_disabled", status_code=302)
        user.oidc_provider_id = provider.id
        user.role = role
        if email:
            user.email = email

    user.last_login_at = utcnow()
    db.commit()
    db.refresh(user)

    jwt_token, _jti, _exp = auth.create_access_token(user.id, user.username, user.role)
    _log_audit(db, user.username, "login_oidc", "user", str(user.id), f"provider={provider.slug} role={role}", request)

    resp = RedirectResponse(url="/dashboard", status_code=302)
    _set_session_cookie(resp, request, jwt_token)
    resp.delete_cookie("oidc_tx", path="/auth/oidc")
    return resp


@app.get("/auth/oidc/callback")
async def oidc_callback_default(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: Session = Depends(get_db),
):
    return await _handle_oidc_callback(request, db, slug=None, code=code, state=state, error=error)


@app.get("/auth/oidc/{slug}/callback")
async def oidc_callback_slug(
    slug: str,
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: Session = Depends(get_db),
):
    return await _handle_oidc_callback(request, db, slug=slug, code=code, state=state, error=error)


# --- USERS & AUDIT LOG API ---


@app.get("/api/users")
async def list_users(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.id.asc()).all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "email": u.email or "",
            "role": u.role,
            "is_active": bool(u.is_active),
            "totp_enabled": bool(u.totp_enabled),
            "is_oidc": bool(u.oidc_sub),
            "last_login_at": _fmt_utc(u.last_login_at),
            "created_at": _fmt_utc(u.created_at),
        }
        for u in users
    ]


@app.post("/api/users")
async def create_user(data: UserCreateRequest, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    uname = data.username.strip()
    if not uname:
        raise HTTPException(status_code=400, detail="Username is required")
    if data.role not in _VALID_ROLES:
        raise HTTPException(status_code=400, detail="Invalid role")
    if db.query(User).filter(User.username == uname).first():
        raise HTTPException(status_code=400, detail="Username already exists")
    if not data.password:
        raise HTTPException(status_code=400, detail="Password is required for local user")

    new_user = User(
        username=uname,
        hashed_password=auth.get_password_hash(data.password),
        email=data.email.strip() or None,
        role=data.role,
        is_active=data.is_active,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    _log_audit(db, user.username, "user_created", "user", str(new_user.id), f"{uname} ({data.role})", request)
    return {"id": new_user.id, "username": new_user.username, "role": new_user.role, "is_active": new_user.is_active}


@app.put("/api/users/{user_id}")
async def update_user(
    user_id: int, data: UserUpdateRequest, request: Request, current: User = Depends(require_admin), db: Session = Depends(get_db)
):
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if data.role is not None:
        if data.role not in _VALID_ROLES:
            raise HTTPException(status_code=400, detail="Invalid role")
        if target.role == "admin" and data.role != "admin":
            other_admins = db.query(User).filter(User.role == "admin", User.is_active.is_(True), User.id != target.id).count()
            if other_admins == 0:
                raise HTTPException(status_code=400, detail="Cannot demote the last active administrator")
        target.role = data.role

    if data.is_active is not None:
        if target.role == "admin" and not data.is_active:
            other_admins = db.query(User).filter(User.role == "admin", User.is_active.is_(True), User.id != target.id).count()
            if other_admins == 0:
                raise HTTPException(status_code=400, detail="Cannot deactivate the last active administrator")
        target.is_active = data.is_active

    if data.email is not None:
        target.email = data.email.strip() or None
    if data.password:
        target.hashed_password = auth.get_password_hash(data.password)

    db.commit()
    _log_audit(db, current.username, "user_updated", "user", str(target.id), f"role={target.role}", request)
    return {"success": True}


@app.delete("/api/users/{user_id}")
async def delete_user(user_id: int, request: Request, current: User = Depends(require_admin), db: Session = Depends(get_db)):
    if user_id == current.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.role == "admin" and target.is_active:
        other_admins = db.query(User).filter(User.role == "admin", User.is_active.is_(True), User.id != target.id).count()
        if other_admins == 0:
            raise HTTPException(status_code=400, detail="Cannot delete the last active administrator")
    uname = target.username
    db.delete(target)
    db.commit()
    _log_audit(db, current.username, "user_deleted", "user", str(user_id), uname, request)
    return {"success": True}


@app.get("/api/audit-logs")
async def get_audit_logs(limit: int = 100, _user: User = Depends(require_admin), db: Session = Depends(get_db)):
    capped = max(1, min(limit, 500))
    logs = db.query(AuditLog).order_by(AuditLog.timestamp.desc()).limit(capped).all()
    return [
        {
            "id": log.id,
            "timestamp": _fmt_utc(log.timestamp),
            "username": log.username,
            "action": log.action,
            "resource_type": log.resource_type,
            "resource_id": log.resource_id,
            "details": log.details,
            "ip_address": log.ip_address,
        }
        for log in logs
    ]


# --- BRANDING & LOGO UPLOAD API ---


@app.get("/api/brand")
async def get_brand(db: Session = Depends(get_db), user: Optional[User] = Depends(get_current_user)):
    return _get_brand_context(db, user)


@app.post("/api/brand")
async def save_brand(data: BrandUpdateRequest, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    config = db.query(SystemConfig).first()
    if not config:
        config = SystemConfig()
        db.add(config)

    if data.app_name is not None:
        config.app_name = data.app_name.strip() or "OmniBanner"
    if data.app_icon_url is not None:
        config.app_icon_url = sanitize_url(data.app_icon_url, "/static/brand/logo.png") or "/static/brand/logo.png"
    if data.favicon_url is not None:
        config.favicon_url = sanitize_url(data.favicon_url, config.app_icon_url or "/static/brand/logo.png") or config.app_icon_url
    if data.primary_color is not None:
        config.primary_color = sanitize_hex_color(data.primary_color, "#8b5cf6")
    if data.secondary_color is not None:
        config.secondary_color = sanitize_hex_color(data.secondary_color, "#6366f1")
    if data.default_theme is not None and data.default_theme in ("dark", "light", "system"):
        config.default_theme = data.default_theme
    if data.custom_css is not None:
        config.custom_css = sanitize_custom_css(data.custom_css)
    if data.login_subtitle is not None:
        config.login_subtitle = data.login_subtitle.strip()
    if data.footer_text is not None:
        config.footer_text = data.footer_text.strip()
    if data.public_base_url is not None:
        config.public_base_url = data.public_base_url.strip().rstrip("/")
    if data.default_timezone is not None:
        config.default_timezone = data.default_timezone.strip() or "UTC"
    if data.hide_local_login is not None:
        config.hide_local_login = data.hide_local_login
    if data.allow_proxy_auth is not None:
        config.allow_proxy_auth = data.allow_proxy_auth
    if data.proxy_auth_header is not None:
        config.proxy_auth_header = data.proxy_auth_header.strip() or "X-authentik-username"
    if data.proxy_auth_groups_header is not None:
        config.proxy_auth_groups_header = data.proxy_auth_groups_header.strip() or "X-authentik-groups"
    if data.trusted_proxy_ips is not None:
        config.trusted_proxy_ips = data.trusted_proxy_ips.strip()
    if data.proxy_admin_groups is not None:
        config.proxy_admin_groups = data.proxy_admin_groups.strip()
    if data.proxy_editor_groups is not None:
        config.proxy_editor_groups = data.proxy_editor_groups.strip()
    if data.public_status_page is not None:
        config.public_status_page = bool(data.public_status_page)

    db.commit()
    _log_audit(db, user.username, "branding_updated", "system", "1", config.app_name or "OmniBanner", request)
    return _get_brand_context(db, user)


async def _process_brand_image_upload(file: UploadFile, max_dim: int, prefix: str) -> str:
    raw = await file.read()
    if len(raw) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image file must be 2 MB or smaller")
    try:
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGBA")
        img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid or unsupported image file")

    os.makedirs(UPLOADS_DIR, exist_ok=True)
    filename = f"{prefix}-{secrets.token_hex(6)}.png"
    out_path = os.path.join(UPLOADS_DIR, filename)
    img.save(out_path, format="PNG", optimize=True)
    return f"/static/uploads/{filename}"


@app.post("/api/brand/logo")
async def upload_brand_logo(
    request: Request, file: UploadFile = File(...), user: User = Depends(require_admin), db: Session = Depends(get_db)
):
    url = await _process_brand_image_upload(file, 256, "logo")
    config = db.query(SystemConfig).first()
    if config:
        config.app_icon_url = url
        db.commit()
    _log_audit(db, user.username, "logo_uploaded", "system", "1", url, request)
    return {"success": True, "url": url}


@app.post("/api/brand/favicon")
async def upload_brand_favicon(
    request: Request, file: UploadFile = File(...), user: User = Depends(require_admin), db: Session = Depends(get_db)
):
    url = await _process_brand_image_upload(file, 64, "favicon")
    config = db.query(SystemConfig).first()
    if config:
        config.favicon_url = url
        db.commit()
    _log_audit(db, user.username, "favicon_uploaded", "system", "1", url, request)
    return {"success": True, "url": url}


# --- SITES & ONBOARDING PAIRING API ---


@app.get("/api/sites")
async def list_sites(_user: User = Depends(require_user), db: Session = Depends(get_db)):
    sites = db.query(Site).order_by(Site.id.desc()).all()
    stat_rows = db.query(SiteNoticeStat).all()
    by_site: dict[int, dict[str, int]] = {}
    for row in stat_rows:
        if row.site_id is None:
            continue
        bucket = by_site.setdefault(row.site_id, {"views": 0, "dismisses": 0, "clicks": 0})
        bucket["views"] += row.views_count or 0
        bucket["dismisses"] += row.dismiss_count or 0
        bucket["clicks"] += row.clicks_count or 0
    art = _get_plugin_artifact()
    latest_ver = (art.get("version") if art else None) or APP_VERSION
    return [_serialize_site(s, by_site.get(s.id), latest_ver) for s in sites]


@app.post("/api/sites")
async def create_site(data: SiteRequest, request: Request, user: User = Depends(require_editor), db: Session = Depends(get_db)):
    domain = _extract_host(data.domain or data.site_url)
    if not domain:
        raise HTTPException(status_code=400, detail="Valid site domain is required")

    raw_key = f"obs_{secrets.token_urlsafe(24)}"
    merged_settings = dict(DEFAULT_SITE_SETTINGS)
    if data.settings:
        merged_settings.update(data.settings)

    site = Site(
        name=data.name.strip() or domain,
        domain=domain,
        site_url=sanitize_url(data.site_url, f"https://{domain}") or f"https://{domain}",
        site_key_hash=auth.hash_token(raw_key),
        site_key_prefix=raw_key[:10],
        groups=data.groups.strip(),
        enabled=data.enabled,
        settings_json=json.dumps(merged_settings),
    )
    db.add(site)
    db.commit()
    db.refresh(site)
    banner_cache.invalidate()
    _log_audit(db, user.username, "site_created", "site", str(site.id), domain, request)
    result = _serialize_site(site)
    result["site_key"] = raw_key
    return result


@app.put("/api/sites/{site_id}")
async def update_site(site_id: int, data: SiteRequest, request: Request, user: User = Depends(require_editor), db: Session = Depends(get_db)):
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")

    if data.name.strip():
        site.name = data.name.strip()
    if data.domain.strip():
        site.domain = _extract_host(data.domain)
    if data.site_url.strip():
        site.site_url = sanitize_url(data.site_url, site.site_url)
    site.groups = data.groups.strip()
    site.enabled = data.enabled
    if data.settings is not None:
        merged = _parse_site_settings(site.settings_json)
        merged.update(data.settings)
        site.settings_json = json.dumps(merged)

    db.commit()
    banner_cache.invalidate()
    _log_audit(db, user.username, "site_updated", "site", str(site.id), site.domain, request)
    return _serialize_site(site)


@app.delete("/api/sites/{site_id}")
async def delete_site(site_id: int, request: Request, user: User = Depends(require_editor), db: Session = Depends(get_db)):
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    domain = site.domain
    db.delete(site)
    db.commit()
    banner_cache.invalidate()
    _log_audit(db, user.username, "site_deleted", "site", str(site_id), domain, request)
    return {"success": True}


@app.post("/api/sites/{site_id}/rotate-key")
async def rotate_site_key(site_id: int, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    site = db.query(Site).filter(Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    raw_key = f"obs_{secrets.token_urlsafe(24)}"
    site.site_key_hash = auth.hash_token(raw_key)
    site.site_key_prefix = raw_key[:10]
    db.commit()
    _log_audit(db, user.username, "site_key_rotated", "site", str(site.id), site.domain, request)
    return {"success": True, "site_id": site.id, "site_key": raw_key, "site_key_prefix": site.site_key_prefix}


@app.post("/api/sites/pairing-tokens")
async def create_pairing_token(
    data: PairingTokenRequest, request: Request, user: User = Depends(require_editor), db: Session = Depends(get_db)
):
    config = db.query(SystemConfig).first()
    base_url = resolve_public_base_url(request, config.public_base_url if config else "")

    raw_token = f"obp_{secrets.token_urlsafe(24)}"
    ttl = max(1, min(int(data.ttl_minutes or 15), 1440))
    expires_at = utcnow() + timedelta(minutes=ttl)

    row = SitePairingToken(
        token_hash=auth.hash_token(raw_token),
        site_id=data.site_id,
        preset_name=data.preset_name.strip(),
        preset_groups=data.preset_groups.strip(),
        preset_settings_json=json.dumps(data.preset_settings or {}),
        created_by=user.username,
        expires_at=expires_at,
    )
    db.add(row)
    db.commit()

    pairing_url = f"{base_url}/pair/{raw_token}"
    wp_deep_link = ""
    if data.wp_url.strip():
        wp_base = sanitize_url(data.wp_url.strip(), "").rstrip("/")
        if wp_base:
            wp_deep_link = f"{wp_base}/wp-admin/options-general.php?page=omnibanner-settings&ob_pair={quote(pairing_url, safe='')}"

    _log_audit(db, user.username, "pairing_token_created", "site_pairing", str(row.id), data.preset_name or "", request)
    return {
        "token": raw_token,
        "pairing_url": pairing_url,
        "wp_deep_link": wp_deep_link,
        "expires_at": _fmt_utc(expires_at),
    }


def _extract_pairing_token(raw_input: str) -> str:
    val = (raw_input or "").strip()
    if "/pair/" in val:
        val = val.split("/pair/", 1)[1]
    val = val.split("?", 1)[0].split("#", 1)[0].strip("/")
    return val


@app.post("/api/sites/claim")
async def claim_pairing_token(data: ClaimPairingRequest, request: Request, db: Session = Depends(get_db)):
    ip = _client_ip(request)
    if not claim_limiter.allow(f"claim:{ip}"):
        raise HTTPException(status_code=429, detail="Too many pairing attempts")

    raw_token = _extract_pairing_token(data.token)
    if not raw_token:
        raise HTTPException(status_code=400, detail="Invalid onboarding link or token")

    token_hash = auth.hash_token(raw_token)
    pairing = db.query(SitePairingToken).filter(SitePairingToken.token_hash == token_hash).first()
    now = utcnow()
    if not pairing:
        raise HTTPException(status_code=404, detail="Onboarding token not found")
    if pairing.used_at is not None:
        raise HTTPException(status_code=400, detail="Onboarding token has already been used")
    if pairing.expires_at < now:
        raise HTTPException(status_code=400, detail="Onboarding token has expired")

    domain = _extract_host(data.domain or data.site_url)
    if not domain:
        raise HTTPException(status_code=400, detail="Valid domain is required")

    raw_site_key = f"obs_{secrets.token_urlsafe(24)}"
    site_url = sanitize_url(data.site_url, f"https://{domain}") or f"https://{domain}"

    site: Optional[Site] = None
    if pairing.site_id:
        site = db.query(Site).filter(Site.id == pairing.site_id).first()
    if not site:
        site = db.query(Site).filter(Site.domain == domain).first()

    preset_settings = {}
    try:
        preset_settings = json.loads(pairing.preset_settings_json or "{}")
    except (ValueError, TypeError):
        preset_settings = {}

    if not site:
        merged = dict(DEFAULT_SITE_SETTINGS)
        if isinstance(preset_settings, dict):
            merged.update(preset_settings)
        site = Site(
            name=pairing.preset_name or data.site_name.strip() or domain,
            domain=domain,
            site_url=site_url,
            site_key_hash=auth.hash_token(raw_site_key),
            site_key_prefix=raw_site_key[:10],
            groups=pairing.preset_groups or "",
            enabled=True,
            plugin_version=data.plugin_version.strip(),
            wp_version=data.wp_version.strip(),
            last_seen_at=now,
            settings_json=json.dumps(merged),
        )
        db.add(site)
    else:
        site.domain = domain
        site.site_url = site_url
        if pairing.preset_name or data.site_name.strip():
            site.name = pairing.preset_name or data.site_name.strip()
        if pairing.preset_groups:
            site.groups = pairing.preset_groups
        if isinstance(preset_settings, dict) and preset_settings:
            merged = _parse_site_settings(site.settings_json)
            merged.update(preset_settings)
            site.settings_json = json.dumps(merged)
        site.site_key_hash = auth.hash_token(raw_site_key)
        site.site_key_prefix = raw_site_key[:10]
        site.plugin_version = data.plugin_version.strip() or site.plugin_version
        site.wp_version = data.wp_version.strip() or site.wp_version
        site.last_seen_at = now

    pairing.used_at = now
    pairing.used_by_domain = domain
    db.commit()
    db.refresh(site)
    banner_cache.invalidate()
    _log_audit(db, f"site:{domain}", "site_paired", "site", str(site.id), domain, request)

    brand = _get_brand_context(db)
    return {
        "success": True,
        "site_id": site.id,
        "site_key": raw_site_key,
        "site_name": site.name,
        "domain": site.domain,
        "groups": site.groups,
        "enabled": bool(site.enabled),
        "branding": {
            "name": brand["app_name"],
            "icon_url": brand["app_icon_url"],
            "color": brand["primary_color"],
            "secondary_color": brand["secondary_color"],
        },
        "settings": _parse_site_settings(site.settings_json),
    }


def _authenticate_site(request: Request, db: Session) -> Site:
    site_key = (request.headers.get("X-OmniBanner-Site-Key") or "").strip()
    if not site_key:
        raise HTTPException(status_code=401, detail="Missing X-OmniBanner-Site-Key header")
    key_hash = auth.hash_token(site_key)
    site = db.query(Site).filter(Site.site_key_hash == key_hash).first()
    if not site:
        raise HTTPException(status_code=401, detail="Invalid site key")
    return site


@app.post("/api/sites/heartbeat")
async def site_heartbeat(data: HeartbeatRequest, request: Request, db: Session = Depends(get_db)):
    site = _authenticate_site(request, db)
    site.last_seen_at = utcnow()
    if data.plugin_version.strip():
        site.plugin_version = data.plugin_version.strip()
    if data.wp_version.strip():
        site.wp_version = data.wp_version.strip()
    if data.site_url.strip():
        site.site_url = sanitize_url(data.site_url, site.site_url)
    db.commit()

    brand = _get_brand_context(db)
    return {
        "success": True,
        "site_id": site.id,
        "enabled": bool(site.enabled),
        "branding": {
            "name": brand["app_name"],
            "icon_url": brand["app_icon_url"],
            "color": brand["primary_color"],
            "secondary_color": brand["secondary_color"],
        },
        "settings": _parse_site_settings(site.settings_json),
    }


@app.get("/api/sites/config")
async def get_site_config_authenticated(request: Request, db: Session = Depends(get_db)):
    site = _authenticate_site(request, db)
    brand = _get_brand_context(db)
    return {
        "site_id": site.id,
        "name": site.name,
        "domain": site.domain,
        "groups": site.groups or "",
        "enabled": bool(site.enabled),
        "branding": {
            "name": brand["app_name"],
            "icon_url": brand["app_icon_url"],
            "color": brand["primary_color"],
            "secondary_color": brand["secondary_color"],
        },
        "settings": _parse_site_settings(site.settings_json),
    }


# --- NOTICES & TEMPLATES API ---


@app.get("/api/templates")
async def list_templates(_user: User = Depends(require_user), db: Session = Depends(get_db)):
    rows = db.query(BannerTemplate).order_by(BannerTemplate.id.asc()).all()
    return [
        {
            "id": t.id,
            "name": t.name,
            "category": t.category,
            "banner_text": t.banner_text,
            "bg_color": t.bg_color,
            "text_color": t.text_color,
            "position": t.position,
            "icon": t.icon,
            "cta_text": t.cta_text,
            "cta_url": t.cta_url,
            "cta_bg_color": t.cta_bg_color,
            "cta_text_color": t.cta_text_color,
            "dismissible": bool(t.dismissible),
            "dismiss_cooldown": t.dismiss_cooldown,
            "display_duration": t.display_duration,
            "show_countdown": bool(t.show_countdown),
        }
        for t in rows
    ]


@app.post("/api/templates")
async def create_template(data: BannerTemplateRequest, _user: User = Depends(require_editor), db: Session = Depends(get_db)):
    tpl = BannerTemplate(
        name=data.name.strip(),
        category=data.category.strip() or "general",
        banner_text=sanitize_banner_html(data.banner_text),
        bg_color=sanitize_hex_color(data.bg_color, "#3b82f6"),
        text_color=sanitize_hex_color(data.text_color, "#ffffff"),
        position=data.position or "top",
        icon=data.icon.strip(),
        cta_text=data.cta_text.strip(),
        cta_url=sanitize_url(data.cta_url, ""),
        cta_bg_color=sanitize_hex_color(data.cta_bg_color, "#ffffff"),
        cta_text_color=sanitize_hex_color(data.cta_text_color, "#0f172a"),
        dismissible=data.dismissible,
        dismiss_cooldown=max(1, int(data.dismiss_cooldown)),
        display_duration=max(0, int(data.display_duration)),
        show_countdown=data.show_countdown,
    )
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return {"id": tpl.id, "name": tpl.name}


@app.delete("/api/templates/{template_id}")
async def delete_template(template_id: int, _user: User = Depends(require_editor), db: Session = Depends(get_db)):
    tpl = db.query(BannerTemplate).filter(BannerTemplate.id == template_id).first()
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")
    db.delete(tpl)
    db.commit()
    return {"success": True}


@app.get("/api/notices")
async def list_notices(_user: User = Depends(require_user), db: Session = Depends(get_db)):
    notices = db.query(Notice).order_by(Notice.priority.desc(), Notice.scheduled_start.desc()).all()
    stat_rows = db.query(SiteNoticeStat).all()
    stats_by_notice: dict[int, list[dict[str, Any]]] = {}
    for row in stat_rows:
        stats_by_notice.setdefault(row.notice_id, []).append(
            {
                "site_id": row.site_id,
                "domain": row.domain,
                "views_count": row.views_count,
                "dismiss_count": row.dismiss_count,
                "clicks_count": row.clicks_count,
            }
        )
    return [_serialize_notice(n, stats_by_notice.get(n.id)) for n in notices]


def _apply_notice_fields(notice: Notice, data: NoticeRequest) -> None:
    raw_text = data.banner_text if data.banner_text is not None else (data.text or "")
    notice.title = data.title.strip()
    notice.banner_text = sanitize_banner_html(raw_text)
    notice.bg_color = sanitize_hex_color(data.bg_color, "#ef4444")
    notice.text_color = sanitize_hex_color(data.text_color, "#ffffff")
    notice.enabled = bool(data.enabled)
    notice.priority = int(data.priority)
    notice.position = data.position or "top"
    notice.icon = data.icon.strip()
    notice.cta_text = data.cta_text.strip()
    notice.cta_url = sanitize_url(data.cta_url, "")
    notice.cta_new_tab = bool(data.cta_new_tab)
    notice.cta_bg_color = sanitize_hex_color(data.cta_bg_color, "#ffffff")
    notice.cta_text_color = sanitize_hex_color(data.cta_text_color, "#0f172a")
    notice.show_countdown = bool(data.show_countdown)
    notice.display_duration = max(0, int(data.display_duration or 0))
    notice.dismissible = bool(data.dismissible)
    notice.dismiss_cooldown = max(1, int(data.dismiss_cooldown or 24))
    notice.scheduled_start = _parse_iso_to_utc_naive(data.scheduled_start)
    notice.scheduled_end = _parse_iso_to_utc_naive(data.scheduled_end)
    if notice.scheduled_end < notice.scheduled_start:
        raise HTTPException(status_code=400, detail="scheduled_end must be after scheduled_start")
    notice.target_type = data.target_type or "all"
    notice.target_sites = data.target_sites.strip()
    notice.target_groups = data.target_groups.strip()
    notice.exclude_sites = data.exclude_sites.strip()
    notice.target_paths = data.target_paths.strip()
    notice.audience = data.audience or "all"
    notice.send_smtp = bool(data.send_smtp)
    notice.target_subscriber_tags = data.target_subscriber_tags.strip()
    notice.uptime_kuma_integration = bool(data.uptime_kuma_integration)
    notice.uptime_kuma_push_url = sanitize_url(data.uptime_kuma_push_url, "") or None
    notice.uptime_kuma_monitor_ids = (data.uptime_kuma_monitor_ids or "").strip() or None
    notice.kuma_text = data.kuma_text
    notice.email_subject = data.email_subject
    notice.email_text = data.email_text
    notice.email_lead_minutes = max(0, int(data.email_lead_minutes or 0))
    notice.recurrence = data.recurrence if data.recurrence in ("none", "daily", "weekly", "monthly") else "none"
    notice.updated_at = utcnow()


async def _sync_notice_kuma_maintenance(notice: Notice, db: Session) -> None:
    if not notice.uptime_kuma_integration or not notice.uptime_kuma_monitor_ids:
        return
    kuma_conf = db.query(UptimeKumaSettings).filter(UptimeKumaSettings.enabled.is_(True)).first()
    if not kuma_conf or not kuma_conf.url or (kuma_conf.api_mode or "maintenance") != "maintenance":
        return
    monitor_ids = [m.strip() for m in notice.uptime_kuma_monitor_ids.split(",") if m.strip()]
    if not monitor_ids:
        return

    maint_id = await asyncio.to_thread(
        kuma.sync_kuma_direct_maintenance,
        kuma_conf.url,
        kuma_conf.username,
        decrypt_secret(kuma_conf.password),
        notice.title or f"OmniBanner Notice #{notice.id}",
        notice.kuma_text or notice.banner_text or "",
        notice.scheduled_start,
        notice.scheduled_end,
        monitor_ids,
        notice.kuma_maintenance_id,
        bool(kuma_conf.verify_tls),
    )
    if maint_id:
        notice.kuma_maintenance_id = maint_id
        db.commit()


@app.post("/api/notices")
async def create_notice(data: NoticeRequest, request: Request, user: User = Depends(require_editor), db: Session = Depends(get_db)):
    notice = Notice(created_by=user.username)
    try:
        _apply_notice_fields(notice, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid datetime format: {exc}")

    db.add(notice)
    db.commit()
    db.refresh(notice)
    await _sync_notice_kuma_maintenance(notice, db)

    banner_cache.invalidate()
    brand = _get_brand_context(db)
    await webhooks.dispatch_webhooks(db, "notice.created", _serialize_notice(notice), brand["app_name"], brand["primary_color"])
    _log_audit(db, user.username, "notice_created", "notice", str(notice.id), notice.title or notice.banner_text[:80], request)
    return _serialize_notice(notice)


@app.put("/api/notices/{notice_id}")
async def update_notice(
    notice_id: int, data: NoticeRequest, request: Request, user: User = Depends(require_editor), db: Session = Depends(get_db)
):
    notice = db.query(Notice).filter(Notice.id == notice_id).first()
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")
    try:
        _apply_notice_fields(notice, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid datetime format: {exc}")

    if data.resend_smtp:
        notice.smtp_sent = False
    if notice.scheduled_end > utcnow():
        notice.kuma_end_notified = False
        notice.webhook_end_sent = False

    db.commit()
    await _sync_notice_kuma_maintenance(notice, db)

    banner_cache.invalidate()
    brand = _get_brand_context(db)
    await webhooks.dispatch_webhooks(db, "notice.updated", _serialize_notice(notice), brand["app_name"], brand["primary_color"])
    _log_audit(db, user.username, "notice_updated", "notice", str(notice.id), notice.title or "", request)
    return _serialize_notice(notice)


@app.post("/api/notices/{notice_id}/toggle")
async def toggle_notice(notice_id: int, request: Request, user: User = Depends(require_editor), db: Session = Depends(get_db)):
    notice = db.query(Notice).filter(Notice.id == notice_id).first()
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")
    notice.enabled = not bool(notice.enabled)
    notice.updated_at = utcnow()
    db.commit()
    banner_cache.invalidate()
    _log_audit(
        db,
        user.username,
        "notice_resumed" if notice.enabled else "notice_paused",
        "notice",
        str(notice.id),
        "",
        request,
    )
    return _serialize_notice(notice)


@app.post("/api/notices/{notice_id}/duplicate")
async def duplicate_notice(notice_id: int, request: Request, user: User = Depends(require_editor), db: Session = Depends(get_db)):
    orig = db.query(Notice).filter(Notice.id == notice_id).first()
    if not orig:
        raise HTTPException(status_code=404, detail="Notice not found")

    copy = Notice(
        title=f"{orig.title} (Copy)" if orig.title else "",
        banner_text=orig.banner_text,
        text_color=orig.text_color,
        bg_color=orig.bg_color,
        enabled=False,  # Safe: start paused until reviewed
        priority=orig.priority,
        position=orig.position,
        icon=orig.icon,
        cta_text=orig.cta_text,
        cta_url=orig.cta_url,
        cta_new_tab=orig.cta_new_tab,
        cta_bg_color=orig.cta_bg_color,
        cta_text_color=orig.cta_text_color,
        show_countdown=orig.show_countdown,
        display_duration=orig.display_duration,
        dismissible=orig.dismissible,
        dismiss_cooldown=orig.dismiss_cooldown,
        scheduled_start=orig.scheduled_start,
        scheduled_end=orig.scheduled_end,
        target_type=orig.target_type,
        target_sites=orig.target_sites,
        target_groups=orig.target_groups,
        exclude_sites=orig.exclude_sites,
        target_paths=orig.target_paths,
        audience=orig.audience,
        send_smtp=orig.send_smtp,
        target_subscriber_tags=orig.target_subscriber_tags,
        uptime_kuma_integration=orig.uptime_kuma_integration,
        uptime_kuma_push_url=orig.uptime_kuma_push_url,
        uptime_kuma_monitor_ids=orig.uptime_kuma_monitor_ids,
        kuma_text=orig.kuma_text,
        email_subject=orig.email_subject,
        email_text=orig.email_text,
        email_lead_minutes=orig.email_lead_minutes,
        recurrence=orig.recurrence,
        created_by=user.username,
    )
    db.add(copy)
    db.commit()
    db.refresh(copy)
    _log_audit(db, user.username, "notice_duplicated", "notice", str(copy.id), f"from={orig.id}", request)
    return _serialize_notice(copy)


@app.post("/api/notices/{notice_id}/send-email")
async def send_notice_email_now(notice_id: int, request: Request, user: User = Depends(require_editor), db: Session = Depends(get_db)):
    notice = db.query(Notice).filter(Notice.id == notice_id).first()
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")
    smtp_conf = db.query(SMTPSettings).filter(SMTPSettings.enabled.is_(True)).first()
    if not smtp_conf or not smtp_conf.host:
        raise HTTPException(status_code=400, detail="SMTP is not configured or enabled")

    brand = db.query(SystemConfig).first()
    result = await _send_notice_email_campaign(db, notice, smtp_conf, brand)
    _log_audit(
        db,
        user.username,
        "notice_email_sent",
        "notice",
        str(notice.id),
        f"sent={result['sent']} failed={result['failed']}",
        request,
    )
    return {"success": True, **result}


@app.delete("/api/notices/{notice_id}")
async def delete_notice(notice_id: int, request: Request, user: User = Depends(require_editor), db: Session = Depends(get_db)):
    notice = db.query(Notice).filter(Notice.id == notice_id).first()
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")

    if notice.kuma_maintenance_id:
        kuma_conf = db.query(UptimeKumaSettings).filter(UptimeKumaSettings.enabled.is_(True)).first()
        if kuma_conf and kuma_conf.url:
            await asyncio.to_thread(
                kuma.delete_kuma_maintenance,
                kuma_conf.url,
                kuma_conf.username,
                decrypt_secret(kuma_conf.password),
                notice.kuma_maintenance_id,
                bool(kuma_conf.verify_tls),
            )

    db.delete(notice)
    db.commit()
    banner_cache.invalidate()
    _log_audit(db, user.username, "notice_deleted", "notice", str(notice_id), "", request)
    return {"success": True}


# --- PUBLIC BANNER EVALUATION & ANALYTICS ---


def _match_path_globs(patterns_csv: Optional[str], req_path: str) -> bool:
    if not patterns_csv:
        return True
    patterns = [p.strip() for p in patterns_csv.split(",") if p.strip()]
    if not patterns:
        return True
    norm_path = req_path or "/"
    return any(fnmatch.fnmatch(norm_path, pat) for pat in patterns)


def _evaluate_public_payload(
    db: Session,
    domain: Optional[str],
    site_id: Optional[int],
    req_path: Optional[str],
    audience: Optional[str],
    device: Optional[str],
) -> dict[str, Any]:
    norm_domain = _extract_host(domain)
    cache_key = f"{norm_domain}|{site_id or ''}|{req_path or ''}|{audience or 'all'}|{device or 'all'}"
    cached = banner_cache.get(cache_key)
    if cached is not None:
        return cached

    site: Optional[Site] = None
    if site_id:
        site = db.query(Site).filter(Site.id == site_id).first()
    if not site and norm_domain:
        site = db.query(Site).filter(Site.domain == norm_domain).first()

    site_settings = _parse_site_settings(site.settings_json if site else None)
    if site and not site.enabled:
        result = {"banners": [], "site_config": {**site_settings, "enabled": False}}
        banner_cache.set(cache_key, result)
        return result

    # Respect site-level audience/device/path constraints
    req_audience = (audience or "guest").lower()
    site_audience = (site_settings.get("audience") or "all").lower()
    if site_audience == "guests" and req_audience != "guest":
        result = {"banners": [], "site_config": {**site_settings, "enabled": True}}
        banner_cache.set(cache_key, result)
        return result
    if site_audience == "logged_in" and req_audience != "logged_in":
        result = {"banners": [], "site_config": {**site_settings, "enabled": True}}
        banner_cache.set(cache_key, result)
        return result

    now = utcnow()
    active_notices = (
        db.query(Notice)
        .filter(
            Notice.enabled.is_(True),
            Notice.scheduled_start <= now,
            Notice.scheduled_end >= now,
        )
        .all()
    )

    site_groups = _parse_csv(site.groups if site else "")
    site_id_str = str(site.id) if site else ""
    matched: list[tuple[int, int, datetime, Notice]] = []

    for n in active_notices:
        # Exclusions
        excludes = _parse_csv(n.exclude_sites)
        if (norm_domain and norm_domain in excludes) or (site_id_str and site_id_str in excludes):
            continue

        # Target matching + specificity score
        score = 0
        if n.target_type == "all":
            score = 1
        elif n.target_type in ("domains", "sites"):
            targets = _parse_csv(n.target_sites)
            if (norm_domain and norm_domain in targets) or (site_id_str and site_id_str in targets):
                score = 3
        elif n.target_type == "groups":
            notice_groups = _parse_csv(n.target_groups)
            if site_groups and (site_groups & notice_groups):
                score = 2

        if score == 0:
            continue

        if req_path is not None and n.target_paths and not _match_path_globs(n.target_paths, req_path):
            continue

        notice_aud = (n.audience or "all").lower()
        if notice_aud == "guests" and req_audience != "guest":
            continue
        if notice_aud == "logged_in" and req_audience != "logged_in":
            continue

        matched.append((score, int(n.priority or 0), n.scheduled_start or now, n))

    matched.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    max_banners = max(1, min(int(site_settings.get("max_banners") or 3), 10))
    selected = [item[3] for item in matched[:max_banners]]

    banners_out = [
        {
            "id": n.id,
            "title": n.title or "",
            "text": n.banner_text or "",
            "banner_text": n.banner_text or "",
            "bg_color": n.bg_color or "#ef4444",
            "text_color": n.text_color or "#ffffff",
            "position": n.position or "top",
            "icon": n.icon or "",
            "cta_text": n.cta_text or "",
            "cta_url": n.cta_url or "",
            "cta_new_tab": bool(n.cta_new_tab if n.cta_new_tab is not None else True),
            "cta_bg_color": n.cta_bg_color or "#ffffff",
            "cta_text_color": n.cta_text_color or "#0f172a",
            "show_countdown": bool(n.show_countdown),
            "scheduled_end": _fmt_utc(n.scheduled_end),
            "display_duration": n.display_duration or 0,
            "dismissible": bool(n.dismissible),
            "dismiss_cooldown": n.dismiss_cooldown or 24,
        }
        for n in selected
    ]

    result = {
        "banners": banners_out,
        "site_config": {**site_settings, "enabled": True, "site_id": site.id if site else None},
    }
    banner_cache.set(cache_key, result)
    return result


def _cache_headers_for_payload(payload: Any, ttl_seconds: int = 30) -> dict[str, str]:
    ttl = max(5, min(int(ttl_seconds or 30), 3600))
    etag_src = json.dumps(payload, sort_keys=True).encode("utf-8")
    etag = f'"{hashlib.sha256(etag_src).hexdigest()[:16]}"'
    return {
        "Cache-Control": f"public, max-age={ttl}, stale-while-revalidate={ttl * 2}",
        "ETag": etag,
    }


@app.get("/api/public/info")
async def public_info(db: Session = Depends(get_db)):
    brand = _get_brand_context(db)
    return {
        "name": brand["app_name"],
        "icon_url": brand["app_icon_url"],
        "color": brand["primary_color"],
        "secondary_color": brand["secondary_color"],
    }


@app.get("/api/public/banners")
async def get_public_banners(
    domain: Optional[str] = None,
    site_id: Optional[int] = None,
    path: Optional[str] = None,
    audience: Optional[str] = None,
    device: Optional[str] = None,
    db: Session = Depends(get_db),
):
    payload = _evaluate_public_payload(db, domain, site_id, path, audience, device)
    ttl = payload["site_config"].get("cache_ttl", 30)
    return JSONResponse(content=payload, headers=_cache_headers_for_payload(payload, ttl))


@app.get("/api/public/banner")
async def get_public_banner(
    domain: Optional[str] = None,
    site_id: Optional[int] = None,
    path: Optional[str] = None,
    audience: Optional[str] = None,
    device: Optional[str] = None,
    db: Session = Depends(get_db),
):
    payload = _evaluate_public_payload(db, domain, site_id, path, audience, device)
    ttl = payload["site_config"].get("cache_ttl", 30)
    banners = payload["banners"]
    if not banners:
        return JSONResponse(content={}, status_code=200, headers=_cache_headers_for_payload({}, ttl))
    top = dict(banners[0])
    top["banners"] = banners
    top["site_config"] = payload["site_config"]
    return JSONResponse(content=top, headers=_cache_headers_for_payload(top, ttl))


@app.post("/api/public/banner/{notice_id}/event")
async def record_public_banner_event(notice_id: int, data: PublicEventRequest, request: Request):
    ip = _client_ip(request)
    if not public_event_limiter.allow(f"evt:{ip}"):
        return JSONResponse({"success": False}, status_code=429)
    analytics_buffer.record(notice_id, data.event, site_id=data.site_id, domain=_extract_host(data.domain))
    return {"success": True}


@app.post("/api/public/banner/{notice_id}/dismiss")
async def dismiss_public_banner(notice_id: int, request: Request, domain: str = "", site_id: Optional[int] = None):
    ip = _client_ip(request)
    if not public_event_limiter.allow(f"evt:{ip}"):
        return JSONResponse({"success": False}, status_code=429)
    analytics_buffer.record(notice_id, "dismiss", site_id=site_id, domain=_extract_host(domain))
    return {"success": True}


# --- SMTP & SUBSCRIBERS API ---


@app.get("/api/smtp")
async def get_smtp(_user: User = Depends(require_user), db: Session = Depends(get_db)):
    conf = db.query(SMTPSettings).first()
    if not conf:
        return {
            "host": "",
            "port": 587,
            "tls_mode": "starttls",
            "username": "",
            "password": "",
            "has_password": False,
            "from_email": "",
            "from_name": "OmniBanner Alerts",
            "enabled": False,
        }
    return {
        "host": conf.host or "",
        "port": conf.port or 587,
        "tls_mode": conf.tls_mode or "starttls",
        "username": conf.username or "",
        "password": "",
        "has_password": bool(conf.password),
        "from_email": conf.from_email or "",
        "from_name": conf.from_name or "OmniBanner Alerts",
        "enabled": bool(conf.enabled),
    }


@app.post("/api/smtp")
async def save_smtp(data: SMTPRequest, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    conf = db.query(SMTPSettings).first()
    if not conf:
        conf = SMTPSettings()
        db.add(conf)

    conf.host = data.host.strip()
    conf.port = int(data.port or 587)
    conf.tls_mode = data.tls_mode if data.tls_mode in ("starttls", "ssl", "none") else "starttls"
    conf.username = data.username.strip()
    if data.password:
        conf.password = encrypt_secret(data.password)
    conf.from_email = data.from_email.strip()
    conf.from_name = data.from_name.strip() or "OmniBanner Alerts"
    conf.enabled = bool(data.enabled)
    db.commit()
    _log_audit(db, user.username, "smtp_updated", "smtp", "1", conf.host, request)
    return {"success": True, "has_password": bool(conf.password)}


@app.post("/api/test-smtp")
async def test_smtp(data: TestSMTPRequest, request: Request, db: Session = Depends(get_db)):
    if is_setup_completed(db):
        user = await get_current_user(request, db)
        if not user or user.role != "admin":
            raise HTTPException(status_code=401, detail="Administrator authorization required")

    stored = db.query(SMTPSettings).first()
    host = data.host.strip() or (stored.host if stored else "")
    port = int(data.port or (stored.port if stored else 587))
    username = data.username.strip() or (stored.username if stored else "")
    password = data.password or (decrypt_secret(stored.password) if stored else "")
    from_email = data.from_email.strip() or (stored.from_email if stored else "")
    from_name = data.from_name.strip() or (stored.from_name if stored else "OmniBanner Alerts")
    to_email = data.to_email.strip() or from_email

    if not host or not to_email or not from_email:
        raise HTTPException(status_code=400, detail="Host, sender email, and test recipient email are required")

    brand = _get_brand_context(db)
    now_str = utcnow().strftime("%Y-%m-%d %H:%M:%S")
    html_body, plain_body = smtp.build_nice_email_html(
        app_name=brand["app_name"],
        primary_color=brand["primary_color"],
        notice_text="🎉 Congrats! SMTP connection is verified and ready to send alert notifications.",
        start_time=now_str,
        end_time=now_str,
    )
    smtp_payload = {
        "host": host,
        "port": port,
        "tls_mode": data.tls_mode or (stored.tls_mode if stored else "starttls"),
        "username": username,
        "password": password,
        "from_email": from_email,
        "from_name": from_name,
    }
    ok, err = await asyncio.to_thread(
        smtp.send_alert_email,
        smtp_payload,
        to_email,
        f"{brand['app_name']} - Live SMTP verification alert",
        html_body,
        plain_body,
    )
    return {"success": ok, "detail": err}


@app.get("/api/subscribers")
async def list_subscribers(_user: User = Depends(require_user), db: Session = Depends(get_db)):
    subs = db.query(Subscriber).filter(Subscriber.active.is_(True)).order_by(Subscriber.id.desc()).all()
    return [{"id": s.id, "email": s.email, "name": s.name or "", "tags": s.tags or ""} for s in subs]


@app.post("/api/subscribers")
async def add_subscriber(data: SubscriberRequest, _user: User = Depends(require_editor), db: Session = Depends(get_db)):
    email = data.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid email address required")
    existing = db.query(Subscriber).filter(Subscriber.email == email).first()
    if existing:
        if existing.active:
            raise HTTPException(status_code=400, detail="Subscriber email already registered")
        existing.active = True
        existing.name = data.name.strip()
        existing.tags = data.tags.strip()
        if not existing.unsubscribe_token:
            existing.unsubscribe_token = secrets.token_urlsafe(24)
        db.commit()
        return {"id": existing.id, "email": existing.email, "name": existing.name, "tags": existing.tags}

    sub = Subscriber(
        email=email,
        name=data.name.strip(),
        tags=data.tags.strip(),
        unsubscribe_token=secrets.token_urlsafe(24),
        active=True,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return {"id": sub.id, "email": sub.email, "name": sub.name, "tags": sub.tags}


@app.delete("/api/subscribers/{sub_id}")
async def delete_subscriber(sub_id: int, _user: User = Depends(require_editor), db: Session = Depends(get_db)):
    sub = db.query(Subscriber).filter(Subscriber.id == sub_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscriber not found")
    db.delete(sub)
    db.commit()
    return {"success": True}


@app.post("/api/subscribers/import")
async def import_subscribers(
    file: UploadFile = File(...), _user: User = Depends(require_editor), db: Session = Depends(get_db)
):
    content = await file.read()
    if len(content) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="CSV file exceeds 2 MB limit")
    text_data = content.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text_data))

    imported = 0
    for row in reader:
        if not row:
            continue
        first_cell = row[0].strip().lower()
        if first_cell == "email":
            continue
        if not first_cell or "@" not in first_cell:
            continue

        name = row[1].strip() if len(row) > 1 else ""
        tags = row[2].strip() if len(row) > 2 else ""
        existing = db.query(Subscriber).filter(Subscriber.email == first_cell).first()
        if existing:
            existing.active = True
            if name:
                existing.name = name
            if tags:
                existing.tags = tags
            if not existing.unsubscribe_token:
                existing.unsubscribe_token = secrets.token_urlsafe(24)
        else:
            db.add(
                Subscriber(
                    email=first_cell,
                    name=name,
                    tags=tags,
                    unsubscribe_token=secrets.token_urlsafe(24),
                    active=True,
                )
            )
        imported += 1

    db.commit()
    return {"success": True, "count": imported}


@app.get("/api/subscribers/export")
async def export_subscribers(_user: User = Depends(require_user), db: Session = Depends(get_db)):
    subscribers = db.query(Subscriber).filter(Subscriber.active.is_(True)).all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["email", "name", "tags"])
    for s in subscribers:
        writer.writerow([sanitize_csv_cell(s.email), sanitize_csv_cell(s.name or ""), sanitize_csv_cell(s.tags or "")])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=subscribers.csv"},
    )


# --- UPTIME KUMA API ---


@app.get("/api/kuma")
async def get_kuma(_user: User = Depends(require_user), db: Session = Depends(get_db)):
    conf = db.query(UptimeKumaSettings).first()
    if not conf:
        return {
            "url": "",
            "username": "",
            "password": "",
            "has_password": False,
            "verify_tls": True,
            "api_mode": "maintenance",
            "enabled": False,
        }
    return {
        "url": conf.url or "",
        "username": conf.username or "",
        "password": "",
        "has_password": bool(conf.password),
        "verify_tls": bool(conf.verify_tls if conf.verify_tls is not None else True),
        "api_mode": conf.api_mode or "maintenance",
        "enabled": bool(conf.enabled),
    }


@app.post("/api/kuma")
async def save_kuma(data: KumaSettingsRequest, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    conf = db.query(UptimeKumaSettings).first()
    if not conf:
        conf = UptimeKumaSettings()
        db.add(conf)
    conf.url = data.url.strip().rstrip("/")
    conf.username = data.username.strip()
    if data.password:
        conf.password = encrypt_secret(data.password)
    conf.verify_tls = bool(data.verify_tls)
    conf.api_mode = data.api_mode if data.api_mode in ("maintenance", "pause") else "maintenance"
    conf.enabled = bool(data.enabled)
    db.commit()
    _log_audit(db, user.username, "kuma_updated", "kuma", "1", conf.url, request)
    return {"success": True, "has_password": bool(conf.password)}


@app.post("/api/test-kuma")
async def test_kuma(data: TestKumaRequest, request: Request, db: Session = Depends(get_db)):
    if is_setup_completed(db):
        user = await get_current_user(request, db)
        if not user or user.role != "admin":
            raise HTTPException(status_code=401, detail="Administrator authorization required")

    stored = db.query(UptimeKumaSettings).first()
    url = data.url.strip().rstrip("/") or (stored.url if stored else "")
    username = data.username.strip() or (stored.username if stored else "")
    password = data.password or (decrypt_secret(stored.password) if stored else "")
    if not url:
        raise HTTPException(status_code=400, detail="Uptime Kuma URL is required")

    ok, monitor_count, detail = await asyncio.to_thread(
        kuma.test_kuma_connection, url, username, password, bool(data.verify_tls)
    )
    return {"success": ok, "monitor_count": monitor_count, "detail": detail}


@app.get("/api/kuma/monitors")
async def get_kuma_monitors(_user: User = Depends(require_user), db: Session = Depends(get_db)):
    conf = db.query(UptimeKumaSettings).first()
    if not conf or not conf.url:
        raise HTTPException(status_code=400, detail="Uptime Kuma is not configured")
    try:
        monitors = await asyncio.to_thread(
            kuma.list_kuma_monitors,
            conf.url,
            conf.username,
            decrypt_secret(conf.password),
            bool(conf.verify_tls),
        )
        return {"monitors": monitors}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch Uptime Kuma monitors: {exc}")


# --- OUTBOUND WEBHOOKS & CHATOPS API ---


@app.get("/api/webhooks")
async def list_webhooks(_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(IntegrationWebhook).order_by(IntegrationWebhook.id.asc()).all()
    return [
        {
            "id": w.id,
            "name": w.name,
            "kind": w.kind,
            "url": w.url,
            "has_secret": bool(w.secret),
            "events": w.events or "",
            "verify_tls": bool(w.verify_tls),
            "enabled": bool(w.enabled),
            "last_status": w.last_status or "",
            "last_triggered_at": _fmt_utc(w.last_triggered_at),
        }
        for w in rows
    ]


@app.post("/api/webhooks")
async def create_webhook(data: WebhookRequest, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    hook = IntegrationWebhook(
        name=data.name.strip(),
        kind=data.kind if data.kind in ("webhook", "slack", "discord", "teams", "ntfy") else "webhook",
        url=sanitize_url(data.url, ""),
        secret=encrypt_secret(data.secret),
        events=data.events.strip() or "notice.started,notice.ended",
        verify_tls=data.verify_tls,
        enabled=data.enabled,
    )
    if not hook.url:
        raise HTTPException(status_code=400, detail="Valid http/https webhook URL is required")
    db.add(hook)
    db.commit()
    db.refresh(hook)
    _log_audit(db, user.username, "webhook_created", "webhook", str(hook.id), hook.name, request)
    return {"id": hook.id, "name": hook.name}


@app.put("/api/webhooks/{webhook_id}")
async def update_webhook(
    webhook_id: int, data: WebhookRequest, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)
):
    hook = db.query(IntegrationWebhook).filter(IntegrationWebhook.id == webhook_id).first()
    if not hook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    url = sanitize_url(data.url, "")
    if not url:
        raise HTTPException(status_code=400, detail="Valid URL required")
    hook.name = data.name.strip()
    hook.kind = data.kind if data.kind in ("webhook", "slack", "discord", "teams", "ntfy") else "webhook"
    hook.url = url
    if data.secret:
        hook.secret = encrypt_secret(data.secret)
    hook.events = data.events.strip()
    hook.verify_tls = data.verify_tls
    hook.enabled = data.enabled
    db.commit()
    _log_audit(db, user.username, "webhook_updated", "webhook", str(hook.id), hook.name, request)
    return {"success": True}


@app.delete("/api/webhooks/{webhook_id}")
async def delete_webhook(webhook_id: int, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    hook = db.query(IntegrationWebhook).filter(IntegrationWebhook.id == webhook_id).first()
    if not hook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    db.delete(hook)
    db.commit()
    _log_audit(db, user.username, "webhook_deleted", "webhook", str(webhook_id), "", request)
    return {"success": True}


@app.post("/api/webhooks/{webhook_id}/test")
async def test_webhook(webhook_id: int, _user: User = Depends(require_admin), db: Session = Depends(get_db)):
    hook = db.query(IntegrationWebhook).filter(IntegrationWebhook.id == webhook_id).first()
    if not hook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    brand = _get_brand_context(db)
    sample_notice = {
        "id": 0,
        "title": "Test Notification",
        "banner_text": "⚡ Live integration test from OmniBanner",
        "bg_color": brand["primary_color"],
        "scheduled_start": _fmt_utc(utcnow()),
        "scheduled_end": _fmt_utc(utcnow() + timedelta(hours=1)),
        "target_type": "all",
    }
    ok, status_msg = await webhooks.send_single_webhook(
        hook, "notice.test", sample_notice, brand["app_name"], brand["primary_color"]
    )
    hook.last_status = status_msg
    hook.last_triggered_at = utcnow()
    db.commit()
    return {"success": ok, "detail": status_msg}


# --- INBOUND ALERT WEBHOOK RULES ---


@app.get("/api/inbound-rules")
async def list_inbound_rules(_user: User = Depends(require_admin), db: Session = Depends(get_db)):
    rules = db.query(InboundWebhookRule).order_by(InboundWebhookRule.id.desc()).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "token_prefix": r.token_prefix,
            "banner_template_text": r.banner_template_text,
            "bg_color": r.bg_color,
            "text_color": r.text_color,
            "position": r.position,
            "priority": r.priority,
            "target_type": r.target_type,
            "target_sites": r.target_sites,
            "target_groups": r.target_groups,
            "auto_expire_minutes": r.auto_expire_minutes,
            "enabled": bool(r.enabled),
            "active_notice_id": r.active_notice_id,
        }
        for r in rules
    ]


@app.post("/api/inbound-rules")
async def create_inbound_rule(data: InboundRuleRequest, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    raw_token = f"obi_{secrets.token_urlsafe(24)}"
    rule = InboundWebhookRule(
        name=data.name.strip(),
        token_hash=auth.hash_token(raw_token),
        token_prefix=raw_token[:10],
        banner_template_text=data.banner_template_text,
        bg_color=sanitize_hex_color(data.bg_color, "#ef4444"),
        text_color=sanitize_hex_color(data.text_color, "#ffffff"),
        position=data.position or "top",
        priority=int(data.priority or 100),
        target_type=data.target_type or "all",
        target_sites=data.target_sites.strip(),
        target_groups=data.target_groups.strip(),
        auto_expire_minutes=max(5, int(data.auto_expire_minutes or 120)),
        enabled=data.enabled,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    config = db.query(SystemConfig).first()
    base_url = resolve_public_base_url(request, config.public_base_url if config else "")
    _log_audit(db, user.username, "inbound_rule_created", "inbound_rule", str(rule.id), rule.name, request)
    return {"id": rule.id, "token": raw_token, "webhook_url": f"{base_url}/api/inbound/{raw_token}"}


@app.delete("/api/inbound-rules/{rule_id}")
async def delete_inbound_rule(rule_id: int, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    rule = db.query(InboundWebhookRule).filter(InboundWebhookRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Inbound rule not found")
    db.delete(rule)
    db.commit()
    _log_audit(db, user.username, "inbound_rule_deleted", "inbound_rule", str(rule_id), "", request)
    return {"success": True}


@app.post("/api/inbound/{token}")
async def receive_inbound_webhook(token: str, payload: dict, db: Session = Depends(get_db)):
    rule = db.query(InboundWebhookRule).filter(
        InboundWebhookRule.token_hash == auth.hash_token(token.strip()),
        InboundWebhookRule.enabled.is_(True),
    ).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Inbound webhook rule not found or disabled")

    # Parse Uptime Kuma or generic payload
    heartbeat = payload.get("heartbeat") or {}
    monitor = payload.get("monitor") or {}
    raw_status = heartbeat.get("status") if "status" in heartbeat else payload.get("status")
    monitor_name = monitor.get("name") or payload.get("monitor_name") or payload.get("service") or rule.name
    msg = heartbeat.get("msg") or payload.get("msg") or payload.get("message") or ""

    is_down = raw_status in (0, "0", "down", "DOWN", "firing", "alert")
    is_up = raw_status in (1, "1", "up", "UP", "resolved", "ok")

    now = utcnow()
    if is_down:
        rendered = (
            (rule.banner_template_text or "⚠️ {monitor_name}: {msg}")
            .replace("{monitor_name}", str(monitor_name))
            .replace("{msg}", str(msg or "Incident detected"))
        )
        safe_text = sanitize_banner_html(rendered)
        expire_dt = now + timedelta(minutes=int(rule.auto_expire_minutes or 120))

        notice = None
        if rule.active_notice_id:
            notice = db.query(Notice).filter(Notice.id == rule.active_notice_id).first()
        if not notice:
            notice = Notice(
                title=f"Auto-Incident: {monitor_name}",
                banner_text=safe_text,
                bg_color=rule.bg_color,
                text_color=rule.text_color,
                position=rule.position,
                priority=rule.priority,
                enabled=True,
                dismissible=False,
                scheduled_start=now,
                scheduled_end=expire_dt,
                target_type=rule.target_type,
                target_sites=rule.target_sites,
                target_groups=rule.target_groups,
                created_by=f"inbound:{rule.name}",
            )
            db.add(notice)
            db.commit()
            db.refresh(notice)
            rule.active_notice_id = notice.id
        else:
            notice.banner_text = safe_text
            notice.enabled = True
            notice.scheduled_end = expire_dt
            notice.updated_at = now
        db.commit()
        banner_cache.invalidate()
        return {"status": "triggered", "notice_id": notice.id}

    if is_up and rule.active_notice_id:
        notice = db.query(Notice).filter(Notice.id == rule.active_notice_id).first()
        if notice:
            notice.enabled = False
            notice.scheduled_end = now
            notice.updated_at = now
        rule.active_notice_id = None
        db.commit()
        banner_cache.invalidate()
        return {"status": "resolved"}

    return {"status": "ignored"}


# --- LOCAL WORDPRESS PLUGIN DOWNLOAD & CONFIG BACKUP/RESTORE ---


@app.get("/api/public/plugin/manifest.json")
async def get_public_plugin_manifest(request: Request, db: Session = Depends(get_db)):
    artifact = _get_plugin_artifact()
    if not artifact:
        raise HTTPException(status_code=404, detail="WordPress plugin archive not found")

    config = db.query(SystemConfig).first()
    base_url = resolve_public_base_url(request, config.public_base_url if config else "")
    icon_path = (config.app_icon_url if config else "") or "/static/brand/logo.png"
    icon_url = icon_path if icon_path.startswith("http") else f"{base_url}/{icon_path.lstrip('/')}"
    download_url = f"{base_url}/api/public/plugin/download"
    brand_name = (config.app_name if config and config.app_name else artifact["name"])

    return JSONResponse(
        content={
            "slug": "omnibanner",
            "plugin": "omnibanner/omnibanner.php",
            "name": brand_name,
            "version": artifact["version"],
            "author": artifact["author"],
            "homepage": artifact["homepage"],
            "requires": artifact["requires"],
            "tested": artifact["tested"],
            "requires_php": artifact["requires_php"],
            "last_updated": artifact["last_updated"],
            "download_url": download_url,
            "package": download_url,
            "sha256": artifact["sha256"],
            "size": artifact["size"],
            "icons": {
                "1x": icon_url,
                "2x": icon_url,
                "default": icon_url,
            },
            "sections": {
                "description": f"<p>{html_lib.escape(artifact['description'])}</p>",
                "changelog": artifact["changelog_html"],
            },
        },
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get("/api/public/plugin/download")
async def download_public_wp_plugin():
    artifact = _get_plugin_artifact()
    if not artifact:
        raise HTTPException(status_code=404, detail="WordPress plugin archive not found")
    return Response(
        content=artifact["bytes"],
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="omnibanner.zip"',
            "X-OmniBanner-SHA256": artifact["sha256"],
            "ETag": f'"{artifact["sha256"]}"',
            "Cache-Control": "public, max-age=300",
        },
    )


@app.get("/api/plugin/download")
async def download_wp_plugin(_user: User = Depends(require_user)):
    artifact = _get_plugin_artifact()
    if not artifact:
        raise HTTPException(status_code=404, detail="WordPress plugin archive not found")
    return Response(
        content=artifact["bytes"],
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="omnibanner.zip"',
            "X-OmniBanner-SHA256": artifact["sha256"],
        },
    )


@app.get("/api/backup/export")
async def export_backup(request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    brand = _get_brand_context(db)
    templates_rows = db.query(BannerTemplate).order_by(BannerTemplate.id.asc()).all()
    sites_rows = db.query(Site).order_by(Site.id.asc()).all()
    subs_rows = db.query(Subscriber).order_by(Subscriber.id.asc()).all()
    oidc_rows = db.query(OIDCProvider).order_by(OIDCProvider.id.asc()).all()
    hook_rows = db.query(IntegrationWebhook).order_by(IntegrationWebhook.id.asc()).all()

    payload = {
        "version": APP_VERSION,
        "exported_at": _fmt_utc(utcnow()),
        "brand": brand,
        "templates": [
            {
                "name": t.name,
                "category": t.category,
                "banner_text": t.banner_text,
                "bg_color": t.bg_color,
                "text_color": t.text_color,
                "position": t.position,
                "icon": t.icon,
                "cta_text": t.cta_text,
                "cta_url": t.cta_url,
                "cta_bg_color": t.cta_bg_color,
                "cta_text_color": t.cta_text_color,
                "dismissible": bool(t.dismissible),
                "dismiss_cooldown": t.dismiss_cooldown,
                "display_duration": t.display_duration,
                "show_countdown": bool(t.show_countdown),
            }
            for t in templates_rows
        ],
        "sites": [
            {
                "name": s.name,
                "domain": s.domain,
                "site_url": s.site_url,
                "groups": s.groups,
                "enabled": bool(s.enabled),
                "settings": _parse_site_settings(s.settings_json),
            }
            for s in sites_rows
        ],
        "subscribers": [
            {"email": s.email, "name": s.name or "", "tags": s.tags or "", "active": bool(s.active)}
            for s in subs_rows
        ],
        "oidc_providers": [_serialize_oidc_provider(p) for p in oidc_rows],
        "webhooks": [
            {
                "name": w.name,
                "kind": w.kind,
                "url": w.url,
                "events": w.events,
                "verify_tls": bool(w.verify_tls),
                "enabled": bool(w.enabled),
            }
            for w in hook_rows
        ],
    }
    _log_audit(db, user.username, "backup_exported", "system", "1", "", request)
    return JSONResponse(
        content=payload,
        headers={"Content-Disposition": 'attachment; filename="omnibanner-backup.json"'},
    )


@app.post("/api/backup/import")
async def import_backup(data: dict, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Invalid backup JSON")

    restored = {"templates": 0, "sites": 0, "subscribers": 0}

    if isinstance(data.get("brand"), dict):
        b = data["brand"]
        cfg = db.query(SystemConfig).first()
        if cfg:
            if b.get("app_name"):
                cfg.app_name = str(b["app_name"]).strip()
            if b.get("primary_color"):
                cfg.primary_color = sanitize_hex_color(b["primary_color"], cfg.primary_color)
            if b.get("secondary_color"):
                cfg.secondary_color = sanitize_hex_color(b["secondary_color"], cfg.secondary_color)
            if b.get("default_theme") in ("dark", "light", "system"):
                cfg.default_theme = b["default_theme"]
            if "login_subtitle" in b:
                cfg.login_subtitle = str(b.get("login_subtitle") or "")
            if "footer_text" in b:
                cfg.footer_text = str(b.get("footer_text") or "")
            if "custom_css" in b:
                cfg.custom_css = sanitize_custom_css(b.get("custom_css"))
            if "public_status_page" in b:
                cfg.public_status_page = bool(b.get("public_status_page"))

    for tpl in data.get("templates") or []:
        name = str(tpl.get("name") or "").strip()
        if not name:
            continue
        existing = db.query(BannerTemplate).filter(BannerTemplate.name == name).first()
        if not existing:
            db.add(
                BannerTemplate(
                    name=name,
                    category=str(tpl.get("category") or "general"),
                    banner_text=sanitize_banner_html(tpl.get("banner_text") or ""),
                    bg_color=sanitize_hex_color(tpl.get("bg_color"), "#3b82f6"),
                    text_color=sanitize_hex_color(tpl.get("text_color"), "#ffffff"),
                    position=str(tpl.get("position") or "top"),
                    icon=str(tpl.get("icon") or ""),
                    cta_text=str(tpl.get("cta_text") or ""),
                    cta_url=sanitize_url(tpl.get("cta_url"), ""),
                    cta_bg_color=sanitize_hex_color(tpl.get("cta_bg_color"), "#ffffff"),
                    cta_text_color=sanitize_hex_color(tpl.get("cta_text_color"), "#0f172a"),
                    dismissible=bool(tpl.get("dismissible", True)),
                    dismiss_cooldown=max(1, int(tpl.get("dismiss_cooldown") or 24)),
                    display_duration=max(0, int(tpl.get("display_duration") or 0)),
                    show_countdown=bool(tpl.get("show_countdown", False)),
                )
            )
            restored["templates"] += 1

    for site_item in data.get("sites") or []:
        dom = _extract_host(site_item.get("domain") or site_item.get("site_url"))
        if not dom:
            continue
        existing_site = db.query(Site).filter(Site.domain == dom).first()
        settings = dict(DEFAULT_SITE_SETTINGS)
        if isinstance(site_item.get("settings"), dict):
            settings.update(site_item["settings"])
        if existing_site:
            existing_site.name = str(site_item.get("name") or existing_site.name)
            existing_site.groups = str(site_item.get("groups") or "")
            existing_site.enabled = bool(site_item.get("enabled", True))
            existing_site.settings_json = json.dumps(settings)
        else:
            db.add(
                Site(
                    name=str(site_item.get("name") or dom),
                    domain=dom,
                    site_url=sanitize_url(site_item.get("site_url"), f"https://{dom}"),
                    groups=str(site_item.get("groups") or ""),
                    enabled=bool(site_item.get("enabled", True)),
                    settings_json=json.dumps(settings),
                )
            )
        restored["sites"] += 1

    for sub_item in data.get("subscribers") or []:
        email = str(sub_item.get("email") or "").strip().lower()
        if not email or "@" not in email:
            continue
        existing_sub = db.query(Subscriber).filter(Subscriber.email == email).first()
        if existing_sub:
            existing_sub.name = str(sub_item.get("name") or existing_sub.name or "")
            existing_sub.tags = str(sub_item.get("tags") or existing_sub.tags or "")
            existing_sub.active = bool(sub_item.get("active", True))
        else:
            db.add(
                Subscriber(
                    email=email,
                    name=str(sub_item.get("name") or ""),
                    tags=str(sub_item.get("tags") or ""),
                    unsubscribe_token=secrets.token_urlsafe(24),
                    active=bool(sub_item.get("active", True)),
                )
            )
        restored["subscribers"] += 1

    db.commit()
    banner_cache.invalidate()
    _log_audit(db, user.username, "backup_imported", "system", "1", json.dumps(restored), request)
    return {"success": True, "restored": restored}
