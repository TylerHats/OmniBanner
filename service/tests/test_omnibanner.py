"""Comprehensive automated test suite for OmniBanner Central Service & integrations."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

# Isolate DATA_DIR before importing service modules
_TEST_TMP_DIR = tempfile.mkdtemp(prefix="omnibanner_test_")
os.environ["OMNIBANNER_DATA_DIR"] = _TEST_TMP_DIR
os.environ["OMNIBANNER_PUBLIC_URL"] = "https://banners.test.local"

SERVICE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if SERVICE_DIR not in sys.path:
    sys.path.insert(0, SERVICE_DIR)

import jwt
import pyotp
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from PIL import Image

import auth
import config_store
import database
import kuma
import main
import smtp
import webhooks
from analytics_buffer import analytics_buffer, banner_cache


def _b64url_uint(val: int) -> str:
    raw = val.to_bytes((val.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class OmniBannerTestSuite(unittest.TestCase):
    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(_TEST_TMP_DIR, ignore_errors=True)

    def setUp(self) -> None:
        # Clean DB & caches cleanly between every test
        database.engine.dispose()
        db_file = os.path.join(_TEST_TMP_DIR, "omnibanner.db")
        for suffix in ("", "-wal", "-shm"):
            p = db_file + suffix
            if os.path.exists(p):
                os.remove(p)

        main._reset_rate_limiters()
        banner_cache.invalidate()
        with analytics_buffer._lock:
            analytics_buffer._counts.clear()
        auth._DISCOVERY_CACHE.clear()
        auth._JWKS_CACHE.clear()

        database.init_db()
        self.client = TestClient(main.app)

    def _complete_default_setup(self, enable_totp: bool = False) -> dict:
        resp = self.client.post(
            "/api/setup",
            json={
                "app_name": "AcmeAlerts",
                "primary_color": "#10b981",
                "secondary_color": "#3b82f6",
                "default_theme": "dark",
                "public_base_url": "https://banners.test.local",
                "auth_mode": "credentials",
                "admin_username": "admin",
                "admin_password": "VeryLongSecurePassword!" * 4,  # > 72 bytes test!
                "enable_totp": enable_totp,
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def _login_admin(self) -> None:
        resp = self.client.post(
            "/api/login-step1",
            json={"username": "admin", "password": "VeryLongSecurePassword!" * 4},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertFalse(resp.json()["mfa_required"])

    # --- 1. DB Init, Templates, Logo & Jinja2 Render Checks ---

    def test_01_init_templates_logo_and_jinja_no_duplicate_scripts(self) -> None:
        db = database.SessionLocal()
        try:
            self.assertGreaterEqual(db.query(database.BannerTemplate).count(), 5)
        finally:
            db.close()

        # Check resized logo sizes
        for rel in ["service/static/brand/logo.png", "wp-plugin/assets/logo.png"]:
            full = os.path.join(os.path.dirname(SERVICE_DIR), rel)
            self.assertLess(os.path.getsize(full), 100_000)
            with Image.open(full) as im:
                self.assertLessEqual(im.width, 256)
                self.assertLessEqual(im.height, 256)

        # Render templates and verify no duplicate script declarations
        r_setup = self.client.get("/setup")
        self.assertEqual(r_setup.status_code, 200)
        self.assertEqual(r_setup.text.count("function submitSetupForm"), 1)

        self._complete_default_setup()

        r_login = self.client.get("/login")
        self.assertEqual(r_login.status_code, 200)
        self.assertEqual(r_login.text.count("let pendingMfaToken"), 1)

        self._login_admin()
        r_dash = self.client.get("/dashboard")
        self.assertEqual(r_dash.status_code, 200)
        self.assertEqual(r_dash.text.count("dashboard_app.js"), 1)

        r_totp = self.client.get("/totp-setup")
        self.assertEqual(r_totp.status_code, 200)
        self.assertEqual(r_totp.text.count("async function verifyAndEnableMFA"), 1)
        self.assertEqual(r_totp.text.count("qrious.min.js"), 1)
        self.assertIn("Logged in as <strong>admin</strong>", r_totp.text)

    # --- 2. Legacy Schema Upgrade Migration ---

    def test_02_legacy_db_migration_preserves_notices_and_oidc(self) -> None:
        legacy_path = os.path.join(_TEST_TMP_DIR, "legacy_test.db")
        if os.path.exists(legacy_path):
            os.remove(legacy_path)

        conn = sqlite3.connect(legacy_path)
        conn.executescript(
            """
            CREATE TABLE system_config (id INTEGER PRIMARY KEY, setup_completed BOOLEAN, app_name TEXT, app_icon_url TEXT, primary_color TEXT);
            INSERT INTO system_config VALUES (1, 1, 'LegacyBrand', '/static/brand/logo.png', '#8b5cf6');
            CREATE TABLE notices (id INTEGER PRIMARY KEY, text TEXT, text_color TEXT, bg_color TEXT, display_duration INTEGER, dismissible BOOLEAN, dismiss_cooldown INTEGER, scheduled_start DATETIME, scheduled_end DATETIME, target_type TEXT, target_sites TEXT, send_smtp BOOLEAN, smtp_sent BOOLEAN, uptime_kuma_integration BOOLEAN);
            INSERT INTO notices (id, text, text_color, bg_color, display_duration, dismissible, dismiss_cooldown, scheduled_start, scheduled_end, target_type, target_sites, send_smtp, smtp_sent, uptime_kuma_integration)
            VALUES (1, 'Legacy Alert', '#ffffff', '#ef4444', 0, 1, 24, '2026-01-01 00:00:00', '2026-12-31 00:00:00', 'all', '', 0, 0, 0);
            CREATE TABLE oidc_settings (id INTEGER PRIMARY KEY, provider_name TEXT, issuer_url TEXT, client_id TEXT, client_secret TEXT, enabled BOOLEAN);
            INSERT INTO oidc_settings VALUES (1, 'AuthentikLegacy', 'https://auth.example.com/o/app/', 'cid', 'csec', 1);
            """
        )
        conn.commit()
        conn.close()

        test_engine = database.create_engine(f"sqlite:///{legacy_path}", connect_args={"check_same_thread": False})
        TestSession = database.sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
        database.init_db(target_engine=test_engine, session_factory=TestSession)

        db = TestSession()
        try:
            notice = db.query(database.Notice).filter(database.Notice.id == 1).first()
            self.assertIsNotNone(notice)
            self.assertEqual(notice.banner_text, "Legacy Alert")
            oidc_p = db.query(database.OIDCProvider).first()
            self.assertIsNotNone(oidc_p)
            self.assertEqual(oidc_p.name, "AuthentikLegacy")
        finally:
            db.close()
        test_engine.dispose()

    # --- 3. Setup TOTP (No Lockout), MFA Login, Backup Codes & Logout Revocation ---

    def test_03_setup_totp_flow_backup_codes_and_logout_revocation(self) -> None:
        setup_res = self._complete_default_setup(enable_totp=True)
        self.assertEqual(setup_res["redirect"], "/totp-setup")

        # Already authenticated via HttpOnly cookie from setup!
        totp_page = self.client.get("/totp-setup")
        self.assertEqual(totp_page.status_code, 200)

        secret = auth.generate_totp_secret()
        code = pyotp.TOTP(secret).now()
        verify_res = self.client.post("/api/totp/verify", json={"secret": secret, "code": code})
        self.assertEqual(verify_res.status_code, 200, verify_res.text)
        backup_codes = verify_res.json()["backup_codes"]
        self.assertEqual(len(backup_codes), 8)

        # Verify cookie works then logout revokes token
        me_before = self.client.get("/api/me")
        self.assertEqual(me_before.status_code, 200)
        old_cookie = self.client.cookies.get("access_token")
        self.assertTrue(old_cookie)

        logout_res = self.client.post("/api/logout")
        self.assertEqual(logout_res.status_code, 200)

        # Replay old JWT via Bearer header -> must be rejected as revoked!
        replay = self.client.get("/api/me", headers={"Authorization": f"Bearer {old_cookie}"})
        self.assertEqual(replay.status_code, 401)

        # Step 1 login returns mfa_token
        step1 = self.client.post(
            "/api/login-step1",
            json={"username": "admin", "password": "VeryLongSecurePassword!" * 4},
        )
        self.assertEqual(step1.status_code, 200)
        self.assertTrue(step1.json()["mfa_required"])
        mfa_token = step1.json()["mfa_token"]

        # Step 2 with backup code works once, fails second time
        step2 = self.client.post(
            "/api/login-step2",
            json={"mfa_token": mfa_token, "mfa_code": backup_codes[0]},
        )
        self.assertEqual(step2.status_code, 200)
        self.assertTrue(step2.json()["used_backup_code"])

        self.client.post("/api/logout")
        step1_b = self.client.post(
            "/api/login-step1",
            json={"username": "admin", "password": "VeryLongSecurePassword!" * 4},
        )
        step2_reuse = self.client.post(
            "/api/login-step2",
            json={"mfa_token": step1_b.json()["mfa_token"], "mfa_code": backup_codes[0]},
        )
        self.assertEqual(step2_reuse.status_code, 401)

        # Step 2 with live TOTP works, and disabling MFA requires re-auth
        step2_totp = self.client.post(
            "/api/login-step2",
            json={"mfa_token": step1_b.json()["mfa_token"], "mfa_code": pyotp.TOTP(secret).now()},
        )
        self.assertEqual(step2_totp.status_code, 200)

        bad_disable = self.client.post("/api/totp/disable", json={"password": "wrong"})
        self.assertEqual(bad_disable.status_code, 400)
        good_disable = self.client.post("/api/totp/disable", json={"password": "VeryLongSecurePassword!" * 4})
        self.assertEqual(good_disable.status_code, 200)

    # --- 4. Security Hardening Checks ---

    def test_04_security_auth_cors_csrf_encryption_xss_csv(self) -> None:
        self._complete_default_setup()

        # Unauthenticated test endpoints blocked after setup
        self.assertEqual(
            self.client.post("/api/test-smtp", json={"host": "127.0.0.1", "from_email": "a@b.com"}).status_code,
            401,
        )
        self.assertEqual(
            self.client.post("/api/test-kuma", json={"url": "http://127.0.0.1"}).status_code,
            401,
        )

        # CORS check: public routes allow *, admin routes do NOT
        pub_cors = self.client.get("/api/public/info", headers={"Origin": "https://wp-site.com"})
        self.assertEqual(pub_cors.headers.get("access-control-allow-origin"), "*")
        self.assertIsNone(pub_cors.headers.get("access-control-allow-credentials"))

        admin_cors = self.client.get("/api/notices", headers={"Origin": "https://evil.com"})
        self.assertIsNone(admin_cors.headers.get("access-control-allow-origin"))

        # Login and test CSRF protection
        self._login_admin()
        csrf_block = self.client.post(
            "/api/subscribers",
            json={"email": "x@y.com"},
            headers={"Origin": "https://evil.com"},
        )
        self.assertEqual(csrf_block.status_code, 403)

        # Secrets encrypted at rest and masked on GET
        self.client.post(
            "/api/smtp",
            json={
                "host": "smtp.example.com",
                "port": 587,
                "username": "mailer",
                "password": "SuperSecretSMTPPassword",
                "from_email": "noreply@example.com",
                "enabled": True,
            },
        )
        smtp_get = self.client.get("/api/smtp").json()
        self.assertEqual(smtp_get["password"], "")
        self.assertTrue(smtp_get["has_password"])

        db = database.SessionLocal()
        try:
            row = db.query(database.SMTPSettings).first()
            self.assertTrue(row.password.startswith("enc:"))
            self.assertNotIn("SuperSecretSMTPPassword", row.password)
            self.assertEqual(config_store.decrypt_secret(row.password), "SuperSecretSMTPPassword")
        finally:
            db.close()

        # XSS sanitization
        dirty = 'Hello <script>alert("xss")</script><img src=x onerror=alert(1)><a href="javascript:alert(2)" onclick="steal()">Bad</a> and <a href="https://status.example.com">Good</a>!'
        clean = config_store.sanitize_banner_html(dirty)
        self.assertNotIn("<script", clean.lower())
        self.assertNotIn("alert(", clean.lower())
        self.assertNotIn("onerror", clean.lower())
        self.assertNotIn("javascript:", clean.lower())
        self.assertIn('href="https://status.example.com"', clean)
        self.assertIn('rel="noopener noreferrer"', clean)

        # CSV formula injection neutralization
        self.client.post("/api/subscribers", json={"email": "alice@example.com", "name": "=cmd|'/C calc'!A0"})
        csv_resp = self.client.get("/api/subscribers/export")
        self.assertEqual(csv_resp.status_code, 200)
        self.assertIn("'=cmd|", csv_resp.text)

    # --- 5. Site Onboarding Links, Deep Link, Heartbeat & Per-Site Controls ---

    def test_05_site_onboarding_claim_and_per_site_controls(self) -> None:
        self._complete_default_setup()
        self._login_admin()

        pair_resp = self.client.post(
            "/api/sites/pairing-tokens",
            json={
                "preset_name": "Shop Site",
                "preset_groups": "production, shop",
                "wp_url": "https://shop.example.com",
                "preset_settings": {"max_banners": 2, "position": "top-fixed"},
            },
        )
        self.assertEqual(pair_resp.status_code, 200, pair_resp.text)
        pair_data = pair_resp.json()
        self.assertIn("/pair/obp_", pair_data["pairing_url"])
        self.assertIn("ob_pair=", pair_data["wp_deep_link"])

        # Human pairing info page works
        info_page = self.client.get(f"/pair/{pair_data['token']}")
        self.assertEqual(info_page.status_code, 200)
        self.assertIn(pair_data["pairing_url"], info_page.text)

        # Claim once -> succeeds and issues site_key
        claim_resp = self.client.post(
            "/api/sites/claim",
            json={
                "token": pair_data["pairing_url"],
                "domain": "shop.example.com",
                "site_url": "https://shop.example.com",
                "site_name": "Shop Site",
                "wp_version": "6.8",
                "plugin_version": "2.0.0",
            },
        )
        self.assertEqual(claim_resp.status_code, 200, claim_resp.text)
        claim_data = claim_resp.json()
        site_id = claim_data["site_id"]
        site_key = claim_data["site_key"]
        self.assertTrue(site_key.startswith("obs_"))
        self.assertEqual(claim_data["branding"]["name"], "AcmeAlerts")

        # Claiming same token again -> rejected!
        second_claim = self.client.post(
            "/api/sites/claim",
            json={"token": pair_data["token"], "domain": "attacker.example.com"},
        )
        self.assertEqual(second_claim.status_code, 400)

        # Heartbeat authenticates via X-OmniBanner-Site-Key
        hb = self.client.post(
            "/api/sites/heartbeat",
            json={"domain": "shop.example.com", "plugin_version": "2.0.1"},
            headers={"X-OmniBanner-Site-Key": site_key},
        )
        self.assertEqual(hb.status_code, 200)
        self.assertEqual(hb.json()["settings"]["max_banners"], 2)

        # Create group-targeted notice + global notice; verify ordering & analytics buffer
        now = datetime.now(timezone.utc)
        start_iso = (now - timedelta(minutes=10)).isoformat()
        end_iso = (now + timedelta(hours=2)).isoformat()

        n_global = self.client.post(
            "/api/notices",
            json={
                "title": "Global Notice",
                "banner_text": "Global announcement",
                "priority": 50,
                "target_type": "all",
                "scheduled_start": start_iso,
                "scheduled_end": end_iso,
            },
        ).json()
        n_group = self.client.post(
            "/api/notices",
            json={
                "title": "Shop Maintenance",
                "banner_text": "Shop maintenance active",
                "priority": 10,
                "target_type": "groups",
                "target_groups": "shop",
                "cta_text": "Status",
                "cta_url": "https://status.example.com",
                "scheduled_start": start_iso,
                "scheduled_end": end_iso,
            },
        ).json()

        # Public GET is read-only & orders group-specific notice ahead of global notice
        feed = self.client.get("/api/public/banners?domain=shop.example.com&path=/checkout")
        self.assertEqual(feed.status_code, 200)
        self.assertIn("max-age=30", feed.headers.get("cache-control", ""))
        banners = feed.json()["banners"]
        self.assertEqual(len(banners), 2)
        self.assertEqual(banners[0]["id"], n_group["id"])
        self.assertEqual(banners[1]["id"], n_global["id"])

        # Views count stays 0 on GET until explicit event beacon + buffer flush
        self.assertEqual(self.client.get("/api/notices").json()[0]["views_count"], 0)
        self.client.post(f"/api/public/banner/{n_group['id']}/event", json={"event": "view", "site_id": site_id, "domain": "shop.example.com"})
        self.client.post(f"/api/public/banner/{n_group['id']}/event", json={"event": "click", "site_id": site_id, "domain": "shop.example.com"})
        self.client.post(f"/api/public/banner/{n_group['id']}/dismiss?site_id={site_id}&domain=shop.example.com")
        flushed = analytics_buffer.flush_to_db(database.SessionLocal)
        self.assertEqual(flushed, 3)

        notices_after = {n["id"]: n for n in self.client.get("/api/notices").json()}
        self.assertEqual(notices_after[n_group["id"]]["views_count"], 1)
        self.assertEqual(notices_after[n_group["id"]]["clicks_count"], 1)
        self.assertEqual(notices_after[n_group["id"]]["dismiss_count"], 1)

        # Master kill-switch disables banners for that site immediately
        self.client.put(
            f"/api/sites/{site_id}",
            json={"name": "Shop Site", "domain": "shop.example.com", "groups": "production, shop", "enabled": False},
        )
        muted_feed = self.client.get("/api/public/banners?domain=shop.example.com").json()
        self.assertEqual(muted_feed["banners"], [])
        self.assertFalse(muted_feed["site_config"]["enabled"])

    # --- 6. End-to-End OIDC Flow with RSA-Signed ID Token & Authentik Group Mapping ---

    def test_06_oidc_pkce_jwks_and_authentik_group_mapping(self) -> None:
        self._complete_default_setup()
        self._login_admin()

        priv_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pub_nums = priv_key.public_key().public_numbers()
        kid = "test-key-1"
        jwks_doc = {
            "keys": [
                {
                    "kty": "RSA",
                    "kid": kid,
                    "use": "sig",
                    "alg": "RS256",
                    "n": _b64url_uint(pub_nums.n),
                    "e": _b64url_uint(pub_nums.e),
                }
            ]
        }
        issuer = "https://auth.example.com/application/o/omnibanner"
        discovery_doc = {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/authorize",
            "token_endpoint": f"{issuer}/token",
            "userinfo_endpoint": f"{issuer}/userinfo",
            "jwks_uri": f"{issuer}/jwks",
            "id_token_signing_alg_values_supported": ["RS256"],
        }

        self.client.post(
            "/api/oidc/providers",
            json={
                "name": "Authentik",
                "slug": "authentik",
                "issuer_url": issuer,
                "client_id": "omni-client-id",
                "client_secret": "omni-client-secret",
                "groups_claim": "groups",
                "admin_groups": "omnibanner-admins",
                "editor_groups": "omnibanner-editors",
                "require_group_match": True,
                "enabled": True,
            },
        )
        self.client.post("/api/logout")

        def run_oidc_with_groups(groups_list: list[str], sub_val: str):
            login_resp = self.client.get("/auth/oidc/authentik/login", follow_redirects=False)
            self.assertEqual(login_resp.status_code, 302)
            loc = urlparse(login_resp.headers["location"])
            qs = parse_qs(loc.query)
            state = qs["state"][0]
            nonce = qs["nonce"][0]
            self.assertEqual(qs["code_challenge_method"][0], "S256")
            self.assertEqual(qs["redirect_uri"][0], "https://banners.test.local/auth/oidc/authentik/callback")

            now_ts = int(datetime.now(timezone.utc).timestamp())
            id_token = jwt.encode(
                {
                    "iss": issuer,
                    "aud": "omni-client-id",
                    "sub": sub_val,
                    "nonce": nonce,
                    "preferred_username": f"user-{sub_val}",
                    "email": f"{sub_val}@example.com",
                    "groups": groups_list,
                    "iat": now_ts,
                    "exp": now_ts + 300,
                },
                priv_key,
                algorithm="RS256",
                headers={"kid": kid},
            )

            with (
                patch("auth.discover_oidc_endpoints", new=AsyncMock(return_value=discovery_doc)),
                patch("auth.fetch_jwks", new=AsyncMock(return_value=jwks_doc["keys"])),
                patch("auth.exchange_oidc_code", new=AsyncMock(return_value={"access_token": "at", "id_token": id_token})),
                patch("auth.fetch_oidc_userinfo", new=AsyncMock(return_value={"sub": sub_val, "groups": groups_list})),
            ):
                return self.client.get(
                    f"/auth/oidc/authentik/callback?code=authcode123&state={state}",
                    follow_redirects=False,
                )

        # Allowed Authentik editor group -> creates editor user & logs in!
        with patch("auth.discover_oidc_endpoints", new=AsyncMock(return_value=discovery_doc)):
            cb_editor = run_oidc_with_groups(["omnibanner-editors"], "sub-editor")
        self.assertEqual(cb_editor.status_code, 302)
        self.assertEqual(cb_editor.headers["location"], "/dashboard")
        me_editor = self.client.get("/api/me").json()
        self.assertEqual(me_editor["role"], "editor")

        # Denied when not in any allowed group
        self.client.post("/api/logout")
        with patch("auth.discover_oidc_endpoints", new=AsyncMock(return_value=discovery_doc)):
            cb_denied = run_oidc_with_groups(["random-users"], "sub-denied")
        self.assertEqual(cb_denied.status_code, 302)
        self.assertEqual(cb_denied.headers["location"], "/login?error=group_denied")

    # --- 7. Kuma, SMTP, Scheduler Single-Shot Expiry, Webhooks & Inbound Rules ---

    def test_07_kuma_smtp_webhooks_and_scheduler_no_spam(self) -> None:
        self._complete_default_setup()
        self._login_admin()

        # Test SMTP HTML + plain-text + unsubscribe link
        html_out, plain_out = smtp.build_nice_email_html(
            app_name="AcmeAlerts",
            primary_color="#10b981",
            notice_text="Planned reboot\nCheck <script>bad()</script><a href='https://status.example.com'>status</a>",
            start_time="2026-11-23 21:00:00",
            end_time="2026-11-23 22:00:00",
            cta_text="Details",
            cta_url="https://status.example.com",
            unsubscribe_url="https://banners.test.local/unsubscribe/tok123",
        )
        self.assertIn("https://banners.test.local/unsubscribe/tok123", html_out)
        self.assertIn("https://banners.test.local/unsubscribe/tok123", plain_out)
        self.assertNotIn("<script", html_out.lower())

        # Test one-click unsubscribe
        sub_res = self.client.post("/api/subscribers", json={"email": "bob@example.com", "name": "Bob"}).json()
        db = database.SessionLocal()
        try:
            sub_row = db.query(database.Subscriber).filter(database.Subscriber.id == sub_res["id"]).first()
            unsub_tok = sub_row.unsubscribe_token
        finally:
            db.close()
        unsub_page = self.client.get(f"/unsubscribe/{unsub_tok}")
        self.assertEqual(unsub_page.status_code, 200)
        self.assertIn("unsubscribed", unsub_page.text.lower())
        self.assertEqual(self.client.get("/api/subscribers").json(), [])

        # Test Kuma sync_kuma_direct_maintenance payload against mocked UptimeKumaApi
        fake_api = MagicMock()
        fake_api.__enter__ = MagicMock(return_value=fake_api)
        fake_api.__exit__ = MagicMock(return_value=False)
        fake_api.add_maintenance.return_value = {"msg": "Added", "maintenanceID": 42}
        with patch.object(kuma, "UptimeKumaApi", return_value=fake_api):
            mid = kuma.sync_kuma_direct_maintenance(
                "https://kuma.example.com",
                "admin",
                "pass",
                "Maint",
                "Desc",
                datetime(2026, 11, 23, 21, 0),
                datetime(2026, 11, 23, 22, 0),
                ["1", "4"],
            )
            self.assertEqual(mid, 42)
            fake_api.add_maintenance.assert_called_once()
            _, kw = fake_api.add_maintenance.call_args
            self.assertEqual(kw["strategy"], kuma.MaintenanceStrategy.SINGLE)
            fake_api.add_monitor_maintenance.assert_called_once_with(42, [{"id": 1}, {"id": 4}])

        # Test Scheduler single-shot push notification (no infinite expired spam!)
        db = database.SessionLocal()
        try:
            past_start = database.utcnow() - timedelta(hours=2)
            past_end = database.utcnow() - timedelta(hours=1)
            db.add(
                database.Notice(
                    title="Finished Maint",
                    banner_text="Done",
                    enabled=True,
                    scheduled_start=past_start,
                    scheduled_end=past_end,
                    uptime_kuma_integration=True,
                    uptime_kuma_push_url="https://kuma.example.com/api/push/abc",
                    kuma_start_notified=True,
                    kuma_end_notified=False,
                )
            )
            db.commit()
        finally:
            db.close()

        with patch("kuma.ping_kuma_push_url", new=AsyncMock(return_value=True)) as mock_ping:
            asyncio.run(main._process_scheduler_tick())
            self.assertEqual(mock_ping.call_count, 1)
            # Second tick must NOT re-ping expired notice!
            asyncio.run(main._process_scheduler_tick())
            self.assertEqual(mock_ping.call_count, 1)

        # Test Signed Outbound Webhook HMAC-SHA256
        wh = database.IntegrationWebhook(
            name="Signed Hook",
            kind="webhook",
            url="https://hooks.example.com/ob",
            secret=config_store.encrypt_secret("hook-secret-xyz"),
            events="notice.test",
            enabled=True,
        )
        captured = {}

        class FakeAsyncClient:
            def __init__(self, **_kwargs):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *_args):
                return False
            async def post(self, url, content=b"", headers=None, timeout=10.0):
                captured["url"] = url
                captured["content"] = content
                captured["headers"] = headers or {}
                resp = MagicMock()
                resp.status_code = 200
                resp.text = "ok"
                return resp

        with patch("webhooks.httpx.AsyncClient", FakeAsyncClient):
            ok, _status = asyncio.run(
                webhooks.send_single_webhook(wh, "notice.test", {"banner_text": "Hi"}, "AcmeAlerts", "#10b981")
            )
            self.assertTrue(ok)
            expected_sig = "sha256=" + hmac.new(b"hook-secret-xyz", captured["content"], hashlib.sha256).hexdigest()
            self.assertEqual(captured["headers"]["X-OmniBanner-Signature"], expected_sig)

        # Test Inbound Webhook auto-incident banner trigger & recovery clear
        rule_res = self.client.post(
            "/api/inbound-rules",
            json={"name": "API Health", "banner_template_text": "Alert on {monitor_name}: {msg}"},
        ).json()
        tok = rule_res["token"]

        trig = self.client.post(
            f"/api/inbound/{tok}",
            json={"heartbeat": {"status": 0, "msg": "502 Bad Gateway"}, "monitor": {"name": "Gateway"}},
        )
        self.assertEqual(trig.status_code, 200)
        self.assertEqual(trig.json()["status"], "triggered")

        banner_cache.invalidate()
        live = self.client.get("/api/public/banners").json()["banners"]
        self.assertTrue(any("Alert on Gateway: 502 Bad Gateway" in b["text"] for b in live))

        resolved = self.client.post(
            f"/api/inbound/{tok}",
            json={"heartbeat": {"status": 1, "msg": "200 OK"}, "monitor": {"name": "Gateway"}},
        )
        self.assertEqual(resolved.status_code, 200)
        self.assertEqual(resolved.json()["status"], "resolved")
        live_after = self.client.get("/api/public/banners").json()["banners"]
        self.assertEqual(live_after, [])

    # --- 8. Partial Branding PATCH & Self-Service Password Change ---

    def test_08_partial_brand_update_and_change_password(self) -> None:
        self._complete_default_setup()
        self._login_admin()

        # Save full branding customizations
        self.client.post(
            "/api/brand",
            json={
                "app_name": "BrandCo",
                "public_base_url": "https://custom.brand.local",
                "login_subtitle": "Welcome aboard",
                "footer_text": "BrandCo Footer",
                "custom_css": ".custom { color: red; }",
            },
        )

        # Partial update (like saveAuthPolicies) must preserve unrelated fields
        patched = self.client.post(
            "/api/brand",
            json={"hide_local_login": True, "allow_proxy_auth": True},
        )
        self.assertEqual(patched.status_code, 200)
        body = patched.json()
        self.assertTrue(body["hide_local_login"])
        self.assertEqual(body["app_name"], "BrandCo")
        self.assertEqual(body["public_base_url"], "https://custom.brand.local")
        self.assertEqual(body["login_subtitle"], "Welcome aboard")
        self.assertEqual(body["footer_text"], "BrandCo Footer")
        self.assertIn(".custom", body["custom_css"])

        # Self-service password change
        bad_pw = self.client.post(
            "/api/me/password",
            json={"current_password": "wrong", "new_password": "NewStrongPassword123!"},
        )
        self.assertEqual(bad_pw.status_code, 400)

        ok_pw = self.client.post(
            "/api/me/password",
            json={"current_password": "VeryLongSecurePassword!" * 4, "new_password": "NewStrongPassword123!"},
        )
        self.assertEqual(ok_pw.status_code, 200)

        self.client.post("/api/logout")
        relogin = self.client.post(
            "/api/login-step1",
            json={"username": "admin", "password": "NewStrongPassword123!"},
        )
        self.assertEqual(relogin.status_code, 200)

    # --- 9. Forward-Auth Trusted Proxy & Groups + OIDC RP-Initiated Logout ---

    def test_09_forward_auth_proxy_ip_groups_and_oidc_rp_logout(self) -> None:
        self._complete_default_setup()
        self._login_admin()

        self.client.post(
            "/api/brand",
            json={
                "allow_proxy_auth": True,
                "proxy_auth_header": "X-authentik-username",
                "proxy_auth_groups_header": "X-authentik-groups",
                "trusted_proxy_ips": "127.0.0.1,10.0.0.0/8",
                "proxy_admin_groups": "ak-admins",
                "proxy_editor_groups": "ak-editors",
            },
        )
        self.client.post("/api/logout")

        # Trusted peer (testclient treated as 127.0.0.1) + Authentik pipe-separated groups
        proxy_me = self.client.get(
            "/api/me",
            headers={"X-authentik-username": "alice_proxy", "X-authentik-groups": "users|ak-editors"},
        )
        self.assertEqual(proxy_me.status_code, 200)
        self.assertEqual(proxy_me.json()["username"], "alice_proxy")
        self.assertEqual(proxy_me.json()["role"], "editor")

        # Untrusted external IP must NOT trust forward-auth headers
        untrusted_client = TestClient(main.app, client=("203.0.113.99", 54321))
        spoofed = untrusted_client.get(
            "/api/me",
            headers={"X-authentik-username": "admin", "X-authentik-groups": "ak-admins"},
        )
        self.assertEqual(spoofed.status_code, 401)

        # OIDC RP-Initiated Logout returns end_session_endpoint
        self._login_admin()
        provider = self.client.post(
            "/api/oidc/providers",
            json={
                "name": "AuthentikRP",
                "slug": "ak-rp",
                "issuer_url": "https://auth.example.com/o/app",
                "client_id": "rp-cid",
                "end_session_logout": True,
            },
        ).json()
        db = database.SessionLocal()
        try:
            u = database.User(
                username="rp-user",
                role="editor",
                is_active=True,
                oidc_provider_id=provider["id"],
                oidc_sub="sub-rp",
            )
            db.add(u)
            db.commit()
            db.refresh(u)
            tok, _, _ = auth.create_access_token(u.id, u.username, u.role)
        finally:
            db.close()

        disc = {"end_session_endpoint": "https://auth.example.com/o/app/end-session/"}
        with patch("auth.discover_oidc_endpoints", new=AsyncMock(return_value=disc)):
            rp_logout = self.client.post(
                "/api/logout",
                headers={"Authorization": f"Bearer {tok}"},
            )
        self.assertEqual(rp_logout.status_code, 200)
        self.assertIn("https://auth.example.com/o/app/end-session/", rp_logout.json()["redirect_url"])
        self.assertIn("client_id=rp-cid", rp_logout.json()["redirect_url"])

    # --- 10. Per-Site Cache TTL, Email Lead Time, Manual Send & Weekly Recurrence ---

    def test_10_cache_ttl_email_lead_manual_send_and_recurrence(self) -> None:
        self._complete_default_setup()
        self._login_admin()

        self.client.post(
            "/api/sites",
            json={"name": "Fast Site", "domain": "fast.example.com", "settings": {"cache_ttl": 120}},
        )
        feed = self.client.get("/api/public/banners?domain=fast.example.com")
        self.assertEqual(feed.status_code, 200)
        self.assertIn("max-age=120", feed.headers.get("cache-control", ""))

        # Configure SMTP & subscriber
        self.client.post(
            "/api/smtp",
            json={"host": "smtp.example.com", "port": 587, "from_email": "noreply@example.com", "enabled": True},
        )
        self.client.post("/api/subscribers", json={"email": "ops@example.com", "name": "Ops"})

        now = datetime.now(timezone.utc)
        # Future notice starting in 30 min, with 60 min lead time -> scheduler sends now!
        n_lead = self.client.post(
            "/api/notices",
            json={
                "title": "Upcoming Reboot",
                "banner_text": "Reboot in 30m",
                "send_smtp": True,
                "email_lead_minutes": 60,
                "scheduled_start": (now + timedelta(minutes=30)).isoformat(),
                "scheduled_end": (now + timedelta(minutes=90)).isoformat(),
            },
        ).json()

        with patch("smtp.send_campaign_batch", return_value={"sent": 1, "failed": 0, "error": ""}) as mock_batch:
            asyncio.run(main._process_scheduler_tick())
            self.assertEqual(mock_batch.call_count, 1)

        # Manual Send Email Now endpoint
        with patch("smtp.send_campaign_batch", return_value={"sent": 1, "failed": 0, "error": ""}):
            manual = self.client.post(f"/api/notices/{n_lead['id']}/send-email")
            self.assertEqual(manual.status_code, 200)
            self.assertEqual(manual.json()["sent"], 1)

        # Recurring weekly notice expired 1 hour ago -> rolls forward 7 days & resets flags
        db = database.SessionLocal()
        try:
            old_start = database.utcnow() - timedelta(hours=3)
            old_end = database.utcnow() - timedelta(hours=1)
            rec = database.Notice(
                title="Weekly Window",
                banner_text="Weekly maintenance",
                enabled=True,
                recurrence="weekly",
                scheduled_start=old_start,
                scheduled_end=old_end,
                smtp_sent=True,
                kuma_start_notified=True,
                kuma_end_notified=True,
            )
            db.add(rec)
            db.commit()
            rec_id = rec.id
        finally:
            db.close()

        asyncio.run(main._process_scheduler_tick())
        db = database.SessionLocal()
        try:
            rolled = db.query(database.Notice).filter(database.Notice.id == rec_id).first()
            self.assertEqual(rolled.scheduled_start, old_start + timedelta(days=7))
            self.assertEqual(rolled.scheduled_end, old_end + timedelta(days=7))
            self.assertFalse(rolled.smtp_sent)
            self.assertFalse(rolled.kuma_start_notified)
            self.assertFalse(rolled.kuma_end_notified)
        finally:
            db.close()

    # --- 11. Status Page, ICS Feed, Plugin ZIP Download & Config Backup/Restore ---

    def test_11_status_page_ics_plugin_download_and_backup_restore(self) -> None:
        import zipfile

        self._complete_default_setup()
        self._login_admin()

        now = datetime.now(timezone.utc)
        self.client.post(
            "/api/notices",
            json={
                "title": "Core Upgrade",
                "banner_text": "Core upgrade in progress",
                "scheduled_start": (now - timedelta(minutes=5)).isoformat(),
                "scheduled_end": (now + timedelta(hours=1)).isoformat(),
            },
        )

        status_html = self.client.get("/status")
        self.assertEqual(status_html.status_code, 200)
        self.assertIn("Core Upgrade", status_html.text)

        ics = self.client.get("/status.ics")
        self.assertEqual(ics.status_code, 200)
        self.assertIn("BEGIN:VCALENDAR", ics.text)
        self.assertIn("SUMMARY:Core Upgrade", ics.text)

        # Disable public status page -> 404
        self.client.post("/api/brand", json={"public_status_page": False})
        self.assertEqual(self.client.get("/status").status_code, 404)
        self.assertEqual(self.client.get("/status.ics").status_code, 404)

        # Plugin download returns valid zip
        dl = self.client.get("/api/plugin/download")
        self.assertEqual(dl.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(dl.content)) as zf:
            self.assertIn("omnibanner/omnibanner.php", zf.namelist())

        # Backup export & import round-trip
        self.client.post("/api/sites", json={"name": "Portal", "domain": "portal.example.com"})
        self.client.post("/api/subscribers", json={"email": "backup@example.com", "name": "Backup"})
        backup = self.client.get("/api/backup/export").json()
        self.assertTrue(any(s["domain"] == "portal.example.com" for s in backup["sites"]))

        # Modify a site and verify import restores it cleanly
        db = database.SessionLocal()
        try:
            db.query(database.Site).delete()
            db.query(database.Subscriber).delete()
            db.commit()
        finally:
            db.close()

        imp = self.client.post("/api/backup/import", json=backup)
        self.assertEqual(imp.status_code, 200)
        self.assertGreaterEqual(imp.json()["restored"]["sites"], 1)
        self.assertGreaterEqual(imp.json()["restored"]["subscribers"], 1)

    # --- 12. Self-Hosted WordPress Auto-Update Manifest, Checksum & Fleet Outdated Flag ---

    def test_12_self_hosted_plugin_manifest_download_and_outdated_badge(self) -> None:
        import hashlib
        import zipfile

        self._complete_default_setup()
        self._login_admin()

        manifest_res = self.client.get("/api/public/plugin/manifest.json")
        self.assertEqual(manifest_res.status_code, 200)
        manifest = manifest_res.json()
        self.assertEqual(manifest["slug"], "omnibanner")
        self.assertEqual(manifest["plugin"], "omnibanner/omnibanner.php")
        self.assertEqual(manifest["version"], "2.1.0")
        self.assertRegex(manifest["sha256"], r"^[a-f0-9]{64}$")
        self.assertTrue(manifest["download_url"].endswith("/api/public/plugin/download"))
        self.assertIn("2.1.0", manifest["sections"]["changelog"])

        pkg_res = self.client.get("/api/public/plugin/download")
        self.assertEqual(pkg_res.status_code, 200)
        actual_sha = hashlib.sha256(pkg_res.content).hexdigest()
        self.assertEqual(actual_sha, manifest["sha256"])
        self.assertEqual(pkg_res.headers.get("x-omnibanner-sha256"), actual_sha)
        with zipfile.ZipFile(io.BytesIO(pkg_res.content)) as zf:
            self.assertIn("omnibanner/omnibanner.php", zf.namelist())
            php_src = zf.read("omnibanner/omnibanner.php").decode("utf-8")
            self.assertIn("Update URI: false", php_src)

        pair = self.client.post(
            "/api/sites/pairing-tokens",
            json={"preset_name": "WP Updater Test", "wp_url": "https://wp-upd.example.com"},
        ).json()
        claim = self.client.post(
            "/api/sites/claim",
            json={
                "token": pair["token"],
                "domain": "wp-upd.example.com",
                "site_url": "https://wp-upd.example.com",
                "site_name": "WP Updater Test",
                "wp_version": "6.8",
                "plugin_version": "2.0.0",
            },
        )
        self.assertEqual(claim.status_code, 200)
        site_key = claim.json()["site_key"]

        sites_before = self.client.get("/api/sites").json()
        target_before = next(s for s in sites_before if s["domain"] == "wp-upd.example.com")
        self.assertEqual(target_before["latest_plugin_version"], "2.1.0")
        self.assertTrue(target_before["update_available"])

        hb = self.client.post(
            "/api/sites/heartbeat",
            headers={"X-OmniBanner-Site-Key": site_key},
            json={
                "domain": "wp-upd.example.com",
                "site_url": "https://wp-upd.example.com",
                "wp_version": "6.8",
                "plugin_version": "2.1.0",
            },
        )
        self.assertEqual(hb.status_code, 200)

        sites_after = self.client.get("/api/sites").json()
        target_after = next(s for s in sites_after if s["domain"] == "wp-upd.example.com")
        self.assertFalse(target_after["update_available"])


if __name__ == "__main__":
    unittest.main()
