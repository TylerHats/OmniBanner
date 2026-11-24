"""Pydantic v2 request schemas with safe defaults."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class FlexibleModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SetupRequest(FlexibleModel):
    app_name: str = "OmniBanner"
    app_icon_url: str = "/static/brand/logo.png"
    favicon_url: Optional[str] = None
    primary_color: str = "#8b5cf6"
    secondary_color: str = "#6366f1"
    default_theme: str = "dark"
    public_base_url: str = ""
    default_timezone: str = "UTC"
    auth_mode: str = "credentials"
    admin_username: str = "admin"
    admin_password: str = ""
    enable_totp: bool = False
    oidc_provider_name: str = "Authentik"
    oidc_issuer_url: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_scopes: str = "openid profile email groups"
    oidc_groups_claim: str = "groups"
    oidc_admin_groups: str = ""
    oidc_verify_tls: bool = True
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_tls_mode: str = "starttls"
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_from_name: str = "OmniBanner Alerts"
    kuma_url: str = ""
    kuma_username: str = ""
    kuma_password: str = ""
    kuma_verify_tls: bool = True


class LoginStep1Request(FlexibleModel):
    username: str
    password: str


class LoginStep2Request(FlexibleModel):
    mfa_token: str = ""
    mfa_code: str = ""
    username: str = ""
    password: str = ""


class TOTPVerifyRequest(FlexibleModel):
    secret: str
    code: str


class TOTPDisableRequest(FlexibleModel):
    password: str = ""
    code: str = ""


class OIDCProviderRequest(FlexibleModel):
    name: str = "Authentik"
    slug: str = ""
    issuer_url: str = ""
    client_id: str = ""
    client_secret: str = ""
    scopes: str = "openid profile email groups"
    button_label: str = ""
    groups_claim: str = "groups"
    admin_groups: str = ""
    editor_groups: str = ""
    viewer_groups: str = ""
    require_group_match: bool = False
    auto_create_users: bool = True
    verify_tls: bool = True
    ca_bundle_path: str = ""
    end_session_logout: bool = False
    enabled: bool = True


class OIDCTestRequest(FlexibleModel):
    issuer_url: str
    verify_tls: bool = True
    ca_bundle_path: str = ""


class UserCreateRequest(FlexibleModel):
    username: str
    password: str = ""
    email: str = ""
    role: str = "editor"
    is_active: bool = True


class UserUpdateRequest(FlexibleModel):
    email: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None


class ChangePasswordRequest(FlexibleModel):
    current_password: str
    new_password: str


class BrandUpdateRequest(FlexibleModel):
    app_name: Optional[str] = None
    app_icon_url: Optional[str] = None
    favicon_url: Optional[str] = None
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    default_theme: Optional[str] = None
    custom_css: Optional[str] = None
    login_subtitle: Optional[str] = None
    footer_text: Optional[str] = None
    public_base_url: Optional[str] = None
    default_timezone: Optional[str] = None
    hide_local_login: Optional[bool] = None
    allow_proxy_auth: Optional[bool] = None
    proxy_auth_header: Optional[str] = None
    proxy_auth_groups_header: Optional[str] = None
    trusted_proxy_ips: Optional[str] = None
    proxy_admin_groups: Optional[str] = None
    proxy_editor_groups: Optional[str] = None
    public_status_page: Optional[bool] = None


class NoticeRequest(FlexibleModel):
    title: str = ""
    banner_text: Optional[str] = None
    text: Optional[str] = None
    bg_color: str = "#ef4444"
    text_color: str = "#ffffff"
    enabled: bool = True
    priority: int = 10
    position: str = "top"
    icon: str = ""
    cta_text: str = ""
    cta_url: str = ""
    cta_new_tab: bool = True
    cta_bg_color: str = "#ffffff"
    cta_text_color: str = "#0f172a"
    show_countdown: bool = False
    display_duration: int = 0
    dismissible: bool = True
    dismiss_cooldown: int = 24
    scheduled_start: str
    scheduled_end: str
    target_type: str = "all"
    target_sites: str = ""
    target_groups: str = ""
    exclude_sites: str = ""
    target_paths: str = ""
    audience: str = "all"
    send_smtp: bool = False
    resend_smtp: bool = False
    target_subscriber_tags: str = ""
    email_lead_minutes: int = 0
    recurrence: str = "none"
    uptime_kuma_integration: bool = False
    uptime_kuma_push_url: Optional[str] = None
    uptime_kuma_monitor_ids: Optional[str] = None
    kuma_text: Optional[str] = None
    email_subject: Optional[str] = None
    email_text: Optional[str] = None


class BannerTemplateRequest(FlexibleModel):
    name: str
    category: str = "general"
    banner_text: str
    bg_color: str = "#3b82f6"
    text_color: str = "#ffffff"
    position: str = "top"
    icon: str = ""
    cta_text: str = ""
    cta_url: str = ""
    cta_bg_color: str = "#ffffff"
    cta_text_color: str = "#0f172a"
    dismissible: bool = True
    dismiss_cooldown: int = 24
    display_duration: int = 0
    show_countdown: bool = False


class SiteRequest(FlexibleModel):
    name: str = ""
    domain: str
    site_url: str = ""
    groups: str = ""
    enabled: bool = True
    settings: Optional[dict[str, Any]] = None


class PairingTokenRequest(FlexibleModel):
    site_id: Optional[int] = None
    preset_name: str = ""
    preset_groups: str = ""
    preset_settings: Optional[dict[str, Any]] = None
    wp_url: str = ""
    ttl_minutes: int = 15


class ClaimPairingRequest(FlexibleModel):
    token: str
    domain: str
    site_url: str = ""
    site_name: str = ""
    wp_version: str = ""
    plugin_version: str = ""


class HeartbeatRequest(FlexibleModel):
    domain: str = ""
    site_url: str = ""
    site_name: str = ""
    wp_version: str = ""
    plugin_version: str = ""


class PublicEventRequest(FlexibleModel):
    event: str = "view"
    domain: str = ""
    site_id: Optional[int] = None


class SMTPRequest(FlexibleModel):
    host: str = ""
    port: int = 587
    tls_mode: str = "starttls"
    username: str = ""
    password: str = ""
    from_email: str = ""
    from_name: str = "OmniBanner Alerts"
    enabled: bool = False


class TestSMTPRequest(FlexibleModel):
    host: str = ""
    port: int = 587
    tls_mode: str = "starttls"
    username: str = ""
    password: str = ""
    from_email: str = ""
    from_name: str = "OmniBanner Alerts"
    to_email: str = ""


class SubscriberRequest(FlexibleModel):
    email: str
    name: str = ""
    tags: str = ""


class KumaSettingsRequest(FlexibleModel):
    url: str = ""
    username: str = ""
    password: str = ""
    verify_tls: bool = True
    api_mode: str = "maintenance"
    enabled: bool = False


class TestKumaRequest(FlexibleModel):
    url: str = ""
    username: str = ""
    password: str = ""
    verify_tls: bool = True


class WebhookRequest(FlexibleModel):
    name: str
    kind: str = "webhook"
    url: str
    secret: str = ""
    events: str = "notice.started,notice.ended"
    verify_tls: bool = True
    enabled: bool = True


class InboundRuleRequest(FlexibleModel):
    name: str
    banner_template_text: str = "⚠️ Service degradation detected: {monitor_name} ({msg})"
    bg_color: str = "#ef4444"
    text_color: str = "#ffffff"
    position: str = "top"
    priority: int = 100
    target_type: str = "all"
    target_sites: str = ""
    target_groups: str = ""
    auto_expire_minutes: int = 120
    enabled: bool = True

