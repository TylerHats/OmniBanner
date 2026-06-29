# OmniBanner

<p align="center">
  <img src="service/static/brand/logo.png" alt="OmniBanner Logo" width="160" height="160" style="border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.15);" />
</p>

OmniBanner is a self-hosted notice banner manager featuring a central control service (written in Python/FastAPI) and a matching WordPress plugin. Schedule notifications, sync active events with Uptime Kuma maintenance profiles, send beautiful HTML alert campaigns to a subscriber list, and display notices dynamically on targeted websites without breaking page caching.

---

## Key Features

- **Onboarding Setup Flow:** Wizard on first boot to customize white-label name, brand accent colors, logo, auth modes, and connections.
- **Dynamic Frontend Display:** Client-side JavaScript banner fetching to ensure compatibility with WordPress page-caching plugins (e.g. WP Rocket, LiteSpeed Cache). Includes dismiss cooldown limits and duration timers.
- **Dual Uptime Kuma Integrations:** Coordinate schedules using simple Push webhooks or direct Socket.IO connection configurations to pause monitors and add native maintenance windows.
- **SMTP Notification Engine:** Broadcast beautiful, responsive HTML email campaigns to a subscriber list when notices start.
- **Secure Authentication:** Standard local admin login with client-side QR generation for TOTP MFA, or full OpenID Connect (OIDC) client endpoints auto-discovery (e.g. Authentik).
- **White-Label Branding:** Exposes configuration settings dynamically to customize both the central dashboard and the WordPress settings page.

---

## Directory Structure

```
OmniBanner/
├── service/              # Central administrative web service (FastAPI)
│   ├── config/           # White-label JSON parameters & logo backups
│   ├── static/           # Front-end CSS stylesheets and JS assets
│   ├── templates/        # Jinja2 layouts (Setup flow, login, pairing, dashboard)
│   ├── main.py           # Core FastAPI application router & scheduler
│   └── run.sh            # Quick server execution script
├── wp-plugin/            # Matching WordPress banner plugin
│   ├── assets/           # Dynamic branding sync JS/CSS & frontend assets
│   └── omnibanner.php    # Main PHP settings panel & hook enqueuer
└── README.md
```

---

## Getting Started

### 1. Central Web Service Setup

To deploy the service in a clean standalone directory on any linux machine, run this single command to fetch the installer script:

```bash
curl -sSL https://raw.githubusercontent.com/TylerHats/OmniBanner/main/install.sh | bash
```

Alternatively, if you have already cloned the repository:
1. Navigate to the `service` directory:
   ```bash
   cd service
   ```
2. Start the automated runner (which configures the `.venv` and installs packages):
   ```bash
   ./run.sh
   ```

Open your browser to `http://localhost:8000` to run the step-by-step **Onboarding Wizard** (`/setup`).

---

## WordPress Plugin Installation

1. Log in to your WordPress Dashboard.
2. Navigate to **Plugins** -> **Add New Plugin** and click **Upload Plugin** at the top.
3. Select and upload the packaged [omnibanner.zip](file:///home/tylerhats/Documents/GitHub/OmniBanner/omnibanner.zip) file and click **Install Now**.
4. Click **Activate Plugin** once the extraction completes.
5. Select the **OmniBanner** setting link on the options sidebar.
6. Input the URL of your OmniBanner central service (e.g., `http://localhost:8000`) and click **Sync Branding**.
7. Click **Save Settings** to enable the banner on all frontend pages.

