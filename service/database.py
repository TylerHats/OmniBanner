"""SQLAlchemy 2.0 models, WAL configuration, and safe incremental schema migrations."""

from __future__ import annotations

import json
import os
import secrets
import shutil
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    Text,
    create_engine,
    event,
    inspect,
    text,
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config_store import BASE_DIR, DATA_DIR


def utcnow() -> datetime:
    """Return timezone-naive UTC datetime for consistent SQLite storage."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _resolve_database_url() -> str:
    env_url = os.environ.get("OMNIBANNER_DATABASE_URL", "").strip()
    if env_url:
        return env_url

    explicit_path = os.environ.get("OMNIBANNER_DB_PATH", "").strip()
    if explicit_path:
        db_path = os.path.abspath(explicit_path)
    else:
        db_path = os.path.abspath(os.path.join(DATA_DIR, "omnibanner.db"))
        legacy_path = os.path.abspath(os.path.join(BASE_DIR, "omnibanner.db"))
        if os.path.exists(legacy_path) and not os.path.exists(db_path):
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            shutil.copy2(legacy_path, db_path)

    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    return f"sqlite:///{db_path}"


DATABASE_URL = _resolve_database_url()
_IS_SQLITE = DATABASE_URL.startswith("sqlite")

engine_kwargs: dict[str, Any] = {}
if _IS_SQLITE:
    engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 15}

engine = create_engine(DATABASE_URL, **engine_kwargs)

if _IS_SQLITE:
    @event.listens_for(engine, "connect")
    def _configure_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA busy_timeout=5000;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 Declarative Base."""


DEFAULT_SITE_SETTINGS: dict[str, Any] = {
    "position": "inherit",
    "mount_selector": "",
    "z_index": 999999,
    "max_banners": 3,
    "include_paths": "",
    "exclude_paths": "",
    "audience": "all",
    "allowed_roles": "",
    "device": "all",
    "inherit_font": False,
    "custom_css": "",
    "cache_ttl": 30,
    "lock_local_overrides": False,
}


class SystemConfig(Base):
    __tablename__ = "system_config"

    id = Column(Integer, primary_key=True, index=True)
    setup_completed = Column(Boolean, default=False)
    app_name = Column(String, default="OmniBanner")
    app_icon_url = Column(String, default="/static/brand/logo.png")
    favicon_url = Column(String, default="/static/brand/logo.png")
    primary_color = Column(String, default="#8b5cf6")
    secondary_color = Column(String, default="#6366f1")
    default_theme = Column(String, default="dark")
    custom_css = Column(Text, default="")
    login_subtitle = Column(String, default="")
    footer_text = Column(String, default="")
    public_base_url = Column(String, default="")
    default_timezone = Column(String, default="UTC")
    hide_local_login = Column(Boolean, default=False)
    allow_proxy_auth = Column(Boolean, default=False)
    proxy_auth_header = Column(String, default="X-authentik-username")
    proxy_auth_groups_header = Column(String, default="X-authentik-groups")
    trusted_proxy_ips = Column(String, default="127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16")
    proxy_admin_groups = Column(String, default="")
    proxy_editor_groups = Column(String, default="")
    public_status_page = Column(Boolean, default=True)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String, default="")
    role = Column(String, default="admin")  # admin | editor | viewer
    is_active = Column(Boolean, default=True)
    totp_secret = Column(String, nullable=True)
    totp_enabled = Column(Boolean, default=False)
    totp_backup_codes = Column(Text, nullable=True)  # JSON list of bcrypt hashes
    oidc_provider_id = Column(Integer, nullable=True, index=True)
    oidc_sub = Column(String, nullable=True, index=True)
    email = Column(String, nullable=True)
    theme_preference = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    last_login_at = Column(DateTime, nullable=True)


class RevokedToken(Base):
    __tablename__ = "revoked_tokens"

    id = Column(Integer, primary_key=True, index=True)
    jti = Column(String, unique=True, index=True)
    expires_at = Column(DateTime, index=True)


class OIDCSettings(Base):
    """Legacy single-provider table retained for upgrade migration."""

    __tablename__ = "oidc_settings"

    id = Column(Integer, primary_key=True, index=True)
    provider_name = Column(String, default="Authentik")
    issuer_url = Column(String, default="")
    client_id = Column(String, default="")
    client_secret = Column(String, default="")
    enabled = Column(Boolean, default=False)


class OIDCProvider(Base):
    __tablename__ = "oidc_providers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, default="Authentik")
    slug = Column(String, unique=True, index=True)
    issuer_url = Column(String, default="")
    client_id = Column(String, default="")
    client_secret = Column(String, default="")
    scopes = Column(String, default="openid profile email groups")
    button_label = Column(String, default="")
    groups_claim = Column(String, default="groups")
    admin_groups = Column(String, default="")
    editor_groups = Column(String, default="")
    viewer_groups = Column(String, default="")
    require_group_match = Column(Boolean, default=False)
    auto_create_users = Column(Boolean, default=True)
    verify_tls = Column(Boolean, default=True)
    ca_bundle_path = Column(String, default="")
    end_session_logout = Column(Boolean, default=False)
    enabled = Column(Boolean, default=True)


class Site(Base):
    __tablename__ = "sites"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, default="")
    domain = Column(String, index=True)
    site_url = Column(String, default="")
    site_key_hash = Column(String, index=True, default="")
    site_key_prefix = Column(String, default="")
    groups = Column(String, default="")  # comma-separated tags
    enabled = Column(Boolean, default=True)
    plugin_version = Column(String, default="")
    wp_version = Column(String, default="")
    last_seen_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    settings_json = Column(Text, default=lambda: json.dumps(DEFAULT_SITE_SETTINGS))


class SitePairingToken(Base):
    __tablename__ = "site_pairing_tokens"

    id = Column(Integer, primary_key=True, index=True)
    token_hash = Column(String, unique=True, index=True)
    site_id = Column(Integer, nullable=True)
    preset_name = Column(String, default="")
    preset_groups = Column(String, default="")
    preset_settings_json = Column(Text, default="{}")
    created_by = Column(String, default="")
    created_at = Column(DateTime, default=utcnow)
    expires_at = Column(DateTime)
    used_at = Column(DateTime, nullable=True)
    used_by_domain = Column(String, nullable=True)


class BannerTemplate(Base):
    __tablename__ = "banner_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    category = Column(String, default="general")
    banner_text = Column(Text, default="")
    bg_color = Column(String, default="#3b82f6")
    text_color = Column(String, default="#ffffff")
    position = Column(String, default="top")
    icon = Column(String, default="")
    cta_text = Column(String, default="")
    cta_url = Column(String, default="")
    cta_bg_color = Column(String, default="#ffffff")
    cta_text_color = Column(String, default="#0f172a")
    dismissible = Column(Boolean, default=True)
    dismiss_cooldown = Column(Integer, default=24)
    display_duration = Column(Integer, default=0)
    show_countdown = Column(Boolean, default=False)


class Notice(Base):
    __tablename__ = "notices"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, default="")
    banner_text = Column(Text)
    text_color = Column(String, default="#ffffff")
    bg_color = Column(String, default="#ef4444")
    enabled = Column(Boolean, default=True)
    priority = Column(Integer, default=10)
    position = Column(String, default="top")  # top | top-fixed | bottom-fixed | floating-br | floating-bl
    icon = Column(String, default="")
    cta_text = Column(String, default="")
    cta_url = Column(String, default="")
    cta_new_tab = Column(Boolean, default=True)
    cta_bg_color = Column(String, default="#ffffff")
    cta_text_color = Column(String, default="#0f172a")
    show_countdown = Column(Boolean, default=False)
    display_duration = Column(Integer, default=0)
    dismissible = Column(Boolean, default=True)
    dismiss_cooldown = Column(Integer, default=24)
    scheduled_start = Column(DateTime)
    scheduled_end = Column(DateTime)
    target_type = Column(String, default="all")  # all | domains | sites | groups
    target_sites = Column(String, default="")
    target_groups = Column(String, default="")
    exclude_sites = Column(String, default="")
    target_paths = Column(String, default="")
    audience = Column(String, default="all")  # all | guests | logged_in
    send_smtp = Column(Boolean, default=False)
    smtp_sent = Column(Boolean, default=False)
    smtp_error = Column(Text, default="")
    smtp_sent_count = Column(Integer, default=0)
    target_subscriber_tags = Column(String, default="")
    uptime_kuma_integration = Column(Boolean, default=False)
    uptime_kuma_push_url = Column(String, nullable=True)
    uptime_kuma_monitor_ids = Column(String, nullable=True)
    kuma_text = Column(Text, nullable=True)
    kuma_maintenance_id = Column(Integer, nullable=True)
    kuma_start_notified = Column(Boolean, default=False)
    kuma_end_notified = Column(Boolean, default=False)
    webhook_start_sent = Column(Boolean, default=False)
    webhook_end_sent = Column(Boolean, default=False)
    email_subject = Column(String, nullable=True)
    email_text = Column(Text, nullable=True)
    email_lead_minutes = Column(Integer, default=0)
    recurrence = Column(String, default="none")  # none | daily | weekly | monthly
    views_count = Column(Integer, default=0)
    dismiss_count = Column(Integer, default=0)
    clicks_count = Column(Integer, default=0)
    created_by = Column(String, default="")
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow)


class SiteNoticeStat(Base):
    __tablename__ = "site_notice_stats"

    id = Column(Integer, primary_key=True, index=True)
    notice_id = Column(Integer, index=True)
    site_id = Column(Integer, nullable=True, index=True)
    domain = Column(String, default="", index=True)
    views_count = Column(Integer, default=0)
    dismiss_count = Column(Integer, default=0)
    clicks_count = Column(Integer, default=0)


class SMTPSettings(Base):
    __tablename__ = "smtp_settings"

    id = Column(Integer, primary_key=True, index=True)
    host = Column(String, default="")
    port = Column(Integer, default=587)
    tls_mode = Column(String, default="starttls")  # starttls | ssl | none
    username = Column(String, default="")
    password = Column(String, default="")
    from_email = Column(String, default="")
    from_name = Column(String, default="OmniBanner Alerts")
    enabled = Column(Boolean, default=False)


class Subscriber(Base):
    __tablename__ = "subscribers"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    name = Column(String, nullable=True)
    tags = Column(String, default="")
    unsubscribe_token = Column(String, unique=True, index=True, default=lambda: secrets.token_urlsafe(24))
    active = Column(Boolean, default=True)


class UptimeKumaSettings(Base):
    __tablename__ = "uptime_kuma_settings"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, default="")
    username = Column(String, default="")
    password = Column(String, default="")
    verify_tls = Column(Boolean, default=True)
    api_mode = Column(String, default="maintenance")  # maintenance | pause
    enabled = Column(Boolean, default=False)


class IntegrationWebhook(Base):
    __tablename__ = "integration_webhooks"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, default="")
    kind = Column(String, default="webhook")  # webhook | slack | discord | teams | ntfy
    url = Column(String, default="")
    secret = Column(String, default="")
    events = Column(String, default="notice.started,notice.ended")
    verify_tls = Column(Boolean, default=True)
    enabled = Column(Boolean, default=True)
    last_status = Column(String, default="")
    last_triggered_at = Column(DateTime, nullable=True)


class InboundWebhookRule(Base):
    __tablename__ = "inbound_webhook_rules"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, default="")
    token_hash = Column(String, unique=True, index=True)
    token_prefix = Column(String, default="")
    banner_template_text = Column(Text, default="⚠️ Service degradation detected: {monitor_name} ({msg})")
    bg_color = Column(String, default="#ef4444")
    text_color = Column(String, default="#ffffff")
    position = Column(String, default="top")
    priority = Column(Integer, default=100)
    target_type = Column(String, default="all")
    target_sites = Column(String, default="")
    target_groups = Column(String, default="")
    auto_expire_minutes = Column(Integer, default=120)
    enabled = Column(Boolean, default=True)
    active_notice_id = Column(Integer, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=utcnow, index=True)
    username = Column(String, default="")
    action = Column(String, default="")
    resource_type = Column(String, default="")
    resource_id = Column(String, default="")
    details = Column(Text, default="")
    ip_address = Column(String, default="")


_DEFAULT_TEMPLATES = [
    {
        "name": "Scheduled Maintenance",
        "category": "maintenance",
        "banner_text": "🔧 Scheduled maintenance is underway. Some features may be briefly unavailable.",
        "bg_color": "#f59e0b",
        "text_color": "#0f172a",
        "position": "top",
        "icon": "🔧",
        "cta_text": "Status Page",
        "cta_url": "",
        "cta_bg_color": "#0f172a",
        "cta_text_color": "#ffffff",
        "dismissible": True,
        "dismiss_cooldown": 4,
        "display_duration": 0,
        "show_countdown": True,
    },
    {
        "name": "Active Incident / Outage",
        "category": "incident",
        "banner_text": "🚨 We are investigating degraded performance across our services.",
        "bg_color": "#ef4444",
        "text_color": "#ffffff",
        "position": "top-fixed",
        "icon": "🚨",
        "cta_text": "Live Updates",
        "cta_url": "",
        "cta_bg_color": "#ffffff",
        "cta_text_color": "#991b1b",
        "dismissible": False,
        "dismiss_cooldown": 1,
        "display_duration": 0,
        "show_countdown": False,
    },
    {
        "name": "General Announcement",
        "category": "announcement",
        "banner_text": "📢 Welcome! Check out our latest updates and improvements.",
        "bg_color": "#3b82f6",
        "text_color": "#ffffff",
        "position": "top",
        "icon": "📢",
        "cta_text": "Learn More",
        "cta_url": "",
        "cta_bg_color": "#ffffff",
        "cta_text_color": "#1e3a8a",
        "dismissible": True,
        "dismiss_cooldown": 24,
        "display_duration": 0,
        "show_countdown": False,
    },
    {
        "name": "Security Advisory",
        "category": "security",
        "banner_text": "🛡️ Important security update required. Please review our advisory.",
        "bg_color": "#7c3aed",
        "text_color": "#ffffff",
        "position": "top-fixed",
        "icon": "🛡️",
        "cta_text": "Read Advisory",
        "cta_url": "",
        "cta_bg_color": "#ffffff",
        "cta_text_color": "#4c1d95",
        "dismissible": True,
        "dismiss_cooldown": 12,
        "display_duration": 0,
        "show_countdown": False,
    },
    {
        "name": "Feature / Promo Toast",
        "category": "promo",
        "banner_text": "✨ New release available now with faster performance and improved controls!",
        "bg_color": "#10b981",
        "text_color": "#ffffff",
        "position": "floating-br",
        "icon": "✨",
        "cta_text": "Explore",
        "cta_url": "",
        "cta_bg_color": "#064e3b",
        "cta_text_color": "#ffffff",
        "dismissible": True,
        "dismiss_cooldown": 72,
        "display_duration": 0,
        "show_countdown": False,
    },
]


def _column_sql_type(col: Column) -> str:
    col_type = col.type.__class__.__name__.lower()
    if col_type in ("integer", "biginteger", "smallinteger"):
        return "INTEGER"
    if col_type == "boolean":
        return "BOOLEAN"
    if col_type == "datetime":
        return "DATETIME"
    return "TEXT"


def _column_default_sql(col: Column) -> str:
    if col.default is None:
        return ""
    arg = getattr(col.default, "arg", None)
    if callable(arg):
        return ""
    if isinstance(arg, bool):
        return f" DEFAULT {1 if arg else 0}"
    if isinstance(arg, (int, float)):
        return f" DEFAULT {arg}"
    if isinstance(arg, str):
        escaped = arg.replace("'", "''")
        return f" DEFAULT '{escaped}'"
    return ""


def run_schema_migrations(target_engine=engine) -> None:
    """Safely add missing columns and migrate legacy data on existing databases."""
    inspector = inspect(target_engine)
    existing_tables = set(inspector.get_table_names())

    with target_engine.begin() as conn:
        for table_name, table in Base.metadata.tables.items():
            if table_name not in existing_tables:
                continue
            existing_cols = {c["name"] for c in inspector.get_columns(table_name)}
            for col in table.columns:
                if col.name not in existing_cols:
                    sql_type = _column_sql_type(col)
                    default_sql = _column_default_sql(col)
                    conn.execute(
                        text(f'ALTER TABLE "{table_name}" ADD COLUMN "{col.name}" {sql_type}{default_sql}')
                    )

            # Copy legacy notices.text into notices.banner_text if upgrading from initial schema
            if table_name == "notices" and "text" in existing_cols:
                conn.execute(
                    text(
                        "UPDATE notices SET banner_text = text "
                        "WHERE (banner_text IS NULL OR banner_text = '') AND text IS NOT NULL"
                    )
                )


def init_db(target_engine=engine, session_factory=SessionLocal) -> None:
    """Create tables, apply migrations, backfill legacy data, and seed defaults."""
    Base.metadata.create_all(bind=target_engine)
    run_schema_migrations(target_engine)

    db = session_factory()
    try:
        config = db.query(SystemConfig).first()
        if not config:
            brand_defaults: dict[str, Any] = {}
            brand_json_path = os.path.join(BASE_DIR, "config", "brand.json")
            if os.path.exists(brand_json_path):
                try:
                    with open(brand_json_path, "r", encoding="utf-8") as fh:
                        loaded = json.load(fh)
                        if isinstance(loaded, dict):
                            brand_defaults = loaded
                except Exception:
                    brand_defaults = {}

            config = SystemConfig(
                setup_completed=False,
                app_name=brand_defaults.get("app_name", "OmniBanner"),
                app_icon_url=brand_defaults.get("app_icon_url", "/static/brand/logo.png"),
                favicon_url=brand_defaults.get("app_icon_url", "/static/brand/logo.png"),
                primary_color=brand_defaults.get("primary_color", "#8b5cf6"),
                default_theme=brand_defaults.get("theme", "dark"),
            )
            db.add(config)
            db.commit()

        # Migrate legacy OIDCSettings row into OIDCProvider if needed
        if db.query(OIDCProvider).count() == 0:
            legacy_oidc = db.query(OIDCSettings).first()
            if legacy_oidc and legacy_oidc.issuer_url:
                slug = (legacy_oidc.provider_name or "sso").lower().replace(" ", "-")
                db.add(
                    OIDCProvider(
                        name=legacy_oidc.provider_name or "Authentik",
                        slug=slug or "sso",
                        issuer_url=legacy_oidc.issuer_url,
                        client_id=legacy_oidc.client_id or "",
                        client_secret=legacy_oidc.client_secret or "",
                        enabled=bool(legacy_oidc.enabled),
                    )
                )
                db.commit()

        # Backfill missing unsubscribe tokens on existing subscribers
        subs_missing = db.query(Subscriber).filter(
            (Subscriber.unsubscribe_token.is_(None)) | (Subscriber.unsubscribe_token == "")
        ).all()
        if subs_missing:
            for s in subs_missing:
                s.unsubscribe_token = secrets.token_urlsafe(24)
            db.commit()

        # Seed default banner templates once
        if db.query(BannerTemplate).count() == 0:
            for tpl in _DEFAULT_TEMPLATES:
                db.add(BannerTemplate(**tpl))
            db.commit()
    finally:
        db.close()
