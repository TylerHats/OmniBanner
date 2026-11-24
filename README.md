# OmniBanner

<p align="center">
  <img src="service/static/brand/logo.png" alt="OmniBanner Logo" width="140" height="140" style="border-radius: 12px;" />
</p>

OmniBanner is a self-hosted notice banner control plane (Python / FastAPI) with a companion **WordPress plugin** and **universal JS embed**. Schedule announcements, maintenance windows, and outage alerts centrally across all connected sites without breaking full-page caching.

---

## Highlights

- **Customizable White-Label Web UI:** Custom app name, uploaded logo & favicon, primary/secondary colors, Light/Dark/System theme toggle, login subtitle, footer text, custom CSS, banner preset templates, CTA buttons, live countdowns, and a 3-channel live preview (Website Banner + Desktop/Mobile + Email + Uptime Kuma Markdown).
- **Per-Site Controls & Fleet Management:** Register sites via single-use **Onboarding Links** (or WordPress deep links), organize sites into groups/tags, override placement/mount selectors/z-index/paths/device/WP-role rules per site, and toggle a master kill-switch per site.
- **OIDC Single Sign-On (Authentik & Others):** Multi-provider OIDC with PKCE (`S256`), `state`/`nonce` validation, JWKS ID-token signature verification, live `.well-known/openid-configuration` tester, Authentik group-to-role mapping (`admin` / `editor` / `viewer`), optional Forward-Auth proxy headers, and break-glass local admin + TOTP MFA with backup codes.
- **Integrations:**
  - **Uptime Kuma:** Push monitors, Socket.IO Direct API (auto-creating/updating scheduled maintenance windows or pausing monitors with a live monitor picker), plus **inbound alert webhooks** to automatically publish incident banners when a monitor goes down.
  - **SMTP Campaigns:** Multipart HTML + plain-text emails, tag filtering, CSV import/export, and signed one-click unsubscribe links.
  - **Outbound Webhooks & ChatOps:** Signed HMAC-SHA256 JSON webhooks, Slack, Discord, Microsoft Teams, and ntfy.
- **Cache-Safe Performance:** Read-only cached public banner endpoints (`Cache-Control` + `ETag`) paired with `sendBeacon` buffered analytics and SQLite WAL mode (or PostgreSQL via `OMNIBANNER_DATABASE_URL`).

---

## Quick Start

### Option 1: Docker Hub / Docker Compose

Pull the prebuilt image from Docker Hub or build locally with Compose:

```bash
docker run -d --name omnibanner \
  -p 8000:8000 \
  -e OMNIBANNER_PUBLIC_URL=http://localhost:8000 \
  -v omnibanner_data:/app/data \
  -v omnibanner_uploads:/app/static/uploads \
  tylerhats/omnibanner:latest
```

Or using Docker Compose:

```bash
cp .env.example .env
# Edit .env with your OMNIBANNER_PUBLIC_URL and optional OMNIBANNER_SECRET_KEY
docker compose up -d
```

### Option 2: Standalone Linux / Local Run

```bash
cd service
./run.sh
```

Or deploy on a fresh Linux host via the installer script:

```bash
curl -sSL https://raw.githubusercontent.com/TylerHats/OmniBanner/main/install.sh | bash
```

Visit `http://localhost:8000` to run the 4-step **Onboarding Setup Wizard** (`/setup`).

---

## WordPress Quick Connect Onboarding

1. Download [omnibanner.zip](omnibanner.zip) directly from the repo or GitHub Releases (or click **Download WP Plugin (.zip)** inside your OmniBanner dashboard under **Sites & Onboarding**).
2. In WordPress, install and activate it via **Plugins -> Add New -> Upload Plugin**.
3. In your **OmniBanner** dashboard, open **Sites & Onboarding** and click **Create One-Time Onboarding Link** (optionally enter your WordPress URL to generate a direct setup deep link).
4. Paste the onboarding link into WordPress under **Settings -> OmniBanner -> Quick Connect via Onboarding Link** and click **Connect Site** (or click the deep link and confirm).
5. Branding, site ID, per-site site key, and per-site display controls sync automatically in one step.

### Self-Hosted WordPress Auto-Updates

- Connected WordPress sites automatically check your OmniBanner server (`/api/public/plugin/manifest.json`) for plugin updates, verify package SHA-256 checksums (`/api/public/plugin/download`), display native **View version details** changelogs, and support WordPress's built-in **Enable auto-updates** toggle on **Plugins -> Installed Plugins**.
- When an OmniBanner server upgrade bundles a newer plugin version, any connected site running an older version shows an **Update** badge in **Sites & Onboarding** until upgraded.

### Public Status Page & Calendar Feed
- Enable or customize the built-in public maintenance page at `/status` and iCalendar feed at `/status.ics` under **Branding**.

### Non-WordPress Websites (Universal JS Embed)

Copy the `<script src="https://banners.example.com/embed.js" data-api="https://banners.example.com" async></script>` snippet from the **Sites & Onboarding** tab into any HTML page.

---

## Authentik OIDC Setup

1. Create an **OAuth2/OpenID Provider** in Authentik (Confidential client, scopes `openid`, `profile`, `email`, `groups`).
2. Copy the **Callback Redirect URI** shown in OmniBanner (**Auth & OIDC** tab, e.g., `https://banners.example.com/auth/oidc/authentik/callback`) into Authentik's Redirect URIs.
3. Paste Authentik's **Issuer URL**, **Client ID**, and **Client Secret** into OmniBanner, optionally map `omnibanner-admins` / `omnibanner-editors` / `omnibanner-viewers`, and click **Test Discovery**.

---

## Development & Tests

```bash
python3 -m py_compile service/*.py
python3 -m unittest discover -s service/tests -v
php -l wp-plugin/omnibanner.php
```
