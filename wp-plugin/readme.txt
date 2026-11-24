=== OmniBanner ===
Contributors: tylerhats
Tags: banner, announcement, maintenance, uptime kuma, notification
Requires at least: 5.8
Tested up to: 6.8
Requires PHP: 7.4
Stable tag: 2.1.0
License: GPLv3 or later
License URI: https://www.gnu.org/licenses/gpl-3.0.html

Display scheduled, cache-safe announcement and maintenance banners across your WordPress sites managed from a central self-hosted OmniBanner service.

== Description ==

OmniBanner connects your WordPress site to a central, self-hosted **OmniBanner** control server so you can schedule maintenance windows, outage notices, and fleet-wide announcements from one dashboard.

Because banners are fetched asynchronously via client-side JavaScript (`fetch`), OmniBanner works seamlessly alongside page caching plugins (WP Rocket, LiteSpeed Cache, W3 Total Cache, Cloudflare) without requiring cache purges when schedules start or end.

### Key Features
* **One-Paste Onboarding Link:** Generate a short-lived pairing link in OmniBanner and paste it in WordPress to automatically bind the site, sync white-label branding, and pull per-site display rules.
* **Per-Site Controls:** Centrally control banner placement, `#wpadminbar` offset, custom CSS mount selectors, URL path globs, logged-in vs. guest targeting, WordPress role filters, and a master kill-switch.
* **Cache-Friendly & Privacy-Conscious:** Uses `localStorage` dismiss cooldowns and batched `sendBeacon` analytics.
* **Safe Sanitized Rendering:** Remote banner HTML is parsed and filtered through a strict DOM allowlist before insertion.

== External Services ==

This plugin requires connecting to a user-configured **OmniBanner** server instance (self-hosted by the site owner or organization). No third-party cloud service is contacted by default until an administrator enters or pastes an OmniBanner server URL or onboarding link.

When connected, the plugin communicates with your configured OmniBanner server in the following situations:
1. **Onboarding & Sync (Admin only):** Sends the site domain, site URL, site name, WordPress version, and plugin version to `/api/sites/claim` and `/api/sites/heartbeat` to register the site and retrieve branding/display settings.
2. **Diagnostics (Admin only, manual button):** Sends requests to `/api/health`, `/api/public/info`, and `/api/public/banners` to verify connectivity and latency.
3. **Frontend Banner Display (Visitors):** Visitors' browsers send a GET request to `/api/public/banners` with the site domain, site ID, current path, and anonymous audience flag (`guest` or `logged_in`). When a banner is shown, dismissed, or its CTA button is clicked, a non-identifying counter event is sent to `/api/public/banner/{id}/event`. No visitor PII, cookies, or user emails are transmitted to OmniBanner.
4. **Self-Hosted Plugin Updates (Admin / WP Cron):** WordPress checks `/api/public/plugin/manifest.json` on your connected OmniBanner server for new versions and downloads signed/SHA-256 verified updates from `/api/public/plugin/download`.

Links:
* Project source & documentation: https://github.com/TylerHats/OmniBanner
* License (GPLv3 or later): https://www.gnu.org/licenses/gpl-3.0.html

== Installation ==

1. Upload the `omnibanner` folder to `/wp-content/plugins/` or install the ZIP file via **Plugins -> Add New -> Upload Plugin**.
2. Activate the plugin through the **Plugins** menu in WordPress.
3. Open **Settings -> OmniBanner**.
4. In your OmniBanner central dashboard, open **Sites & Onboarding**, generate a one-time onboarding link, copy it, and paste it into **Quick Connect via Onboarding Link** inside WordPress (or use the direct WordPress setup deep link).
5. Click **Connect Site**.

== Frequently Asked Questions ==

= Does this break full-page caching? =
No. WordPress outputs static script configuration for the OmniBanner endpoint, and visitor browsers fetch active notices dynamically.

= Does OmniBanner connect to any external service out of the box? =
No. Nothing is sent externally until you connect your own self-hosted OmniBanner server.

== Changelog ==

= 2.1.0 =
* Added self-hosted WordPress plugin updates directly from your connected OmniBanner server (`Update URI: false`, native Plugins auto-update toggle, changelog modal, and SHA-256 package checksum verification).
* Added automatic post-upgrade heartbeat reporting back to OmniBanner so outdated-version badges clear automatically.
* Added a manual "Check for Updates" control under Settings -> OmniBanner.

= 2.0.0 =
* Added one-paste Onboarding Link pairing and deep-link confirmation flow.
* Added per-site placement, path glob, WordPress role, device, and master kill-switch controls.
* Added multi-banner stacking, CTA buttons, live countdown timers, and automatic `#wpadminbar` offset.
* Replaced unsafe HTML injection with strict client-side DOM allowlist sanitization.
* Enabled TLS certificate verification by default (`omnibanner_http_sslverify` filter).
* Added `uninstall.php` cleanup and WordPress.org Plugin Check compliance fixes.

= 1.0.0 =
* Initial release.

