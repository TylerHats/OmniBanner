"""Uptime Kuma Push webhook and Socket.IO Direct API integrations."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import httpx

try:
    from uptime_kuma_api import MaintenanceStrategy, UptimeKumaApi
except ImportError:  # pragma: no cover
    MaintenanceStrategy = None  # type: ignore[assignment]
    UptimeKumaApi = None  # type: ignore[assignment]


async def ping_kuma_push_url(
    push_url: str,
    is_active: bool,
    msg: str = "OmniBanner notice active",
    verify_tls: bool = True,
) -> bool:
    """Ping an Uptime Kuma Push monitor with properly URL-encoded parameters."""
    clean_url = (push_url or "").strip()
    if not clean_url:
        return False
    status_val = "down" if is_active else "up"
    params = {"status": status_val, "msg": (msg or "OmniBanner notice")[:250]}

    async with httpx.AsyncClient(verify=verify_tls) as client:
        try:
            response = await client.get(clean_url, params=params, timeout=10.0)
            return 200 <= response.status_code < 300
        except Exception as exc:
            print(f"Failed to ping Uptime Kuma Push URL: {exc}")
            return False


def _require_kuma_lib() -> None:
    if UptimeKumaApi is None or MaintenanceStrategy is None:
        raise RuntimeError("uptime-kuma-api library is not installed")


def test_kuma_connection(
    kuma_url: str,
    username: str,
    password: str,
    verify_tls: bool = True,
) -> tuple[bool, int, str]:
    """Verify Uptime Kuma Socket.IO credentials and return (ok, monitor_count, detail)."""
    try:
        _require_kuma_lib()
        with UptimeKumaApi(kuma_url, ssl_verify=verify_tls) as api:
            api.login(username, password)
            monitors = api.get_monitors() or []
            return True, len(monitors), f"Connected ({len(monitors)} monitors found)"
    except Exception as exc:
        return False, 0, str(exc)


def list_kuma_monitors(
    kuma_url: str,
    username: str,
    password: str,
    verify_tls: bool = True,
) -> list[dict[str, Any]]:
    """Fetch monitors from Uptime Kuma for UI multi-select picker."""
    _require_kuma_lib()
    with UptimeKumaApi(kuma_url, ssl_verify=verify_tls) as api:
        api.login(username, password)
        monitors = api.get_monitors() or []
        results: list[dict[str, Any]] = []
        for mon in monitors:
            if mon.get("id") is None:
                continue
            results.append(
                {
                    "id": int(mon["id"]),
                    "name": mon.get("name") or f"Monitor #{mon['id']}",
                    "type": str(mon.get("type") or ""),
                    "active": bool(mon.get("active", True)),
                }
            )
        return results


def sync_kuma_direct_maintenance(
    kuma_url: str,
    username: str,
    password: str,
    title: str,
    description: str,
    start_time: datetime,
    end_time: datetime,
    monitor_ids: list[Any],
    existing_maintenance_id: Optional[int] = None,
    verify_tls: bool = True,
) -> Optional[int]:
    """Create or update a scheduled single maintenance window and link monitors."""
    try:
        _require_kuma_lib()
        start_str = start_time.strftime("%Y-%m-%d %H:%M:%S")
        end_str = end_time.strftime("%Y-%m-%d %H:%M:%S")

        valid_monitor_ids: list[int] = []
        for mid in monitor_ids:
            try:
                valid_monitor_ids.append(int(str(mid).strip()))
            except (ValueError, TypeError):
                continue

        with UptimeKumaApi(kuma_url, ssl_verify=verify_tls) as api:
            api.login(username, password)
            maintenance_id: Optional[int] = None

            if existing_maintenance_id:
                try:
                    api.edit_maintenance(
                        int(existing_maintenance_id),
                        title=title,
                        description=description,
                        strategy=MaintenanceStrategy.SINGLE,
                        active=True,
                        dateRange=[start_str, end_str],
                        timezoneOption="UTC",
                    )
                    maintenance_id = int(existing_maintenance_id)
                except Exception:
                    maintenance_id = None

            if not maintenance_id:
                res = api.add_maintenance(
                    title=title,
                    description=description,
                    strategy=MaintenanceStrategy.SINGLE,
                    active=True,
                    dateRange=[start_str, end_str],
                    timezoneOption="UTC",
                )
                raw_id = res.get("maintenanceID") or res.get("id")
                if raw_id is not None:
                    maintenance_id = int(raw_id)

            if maintenance_id and valid_monitor_ids:
                api.add_monitor_maintenance(
                    maintenance_id,
                    [{"id": m_id} for m_id in valid_monitor_ids],
                )

            return maintenance_id
    except Exception as exc:
        print(f"Uptime Kuma Direct Integration Error: {exc}")
        return existing_maintenance_id


def delete_kuma_maintenance(
    kuma_url: str,
    username: str,
    password: str,
    maintenance_id: int,
    verify_tls: bool = True,
) -> bool:
    """Delete a maintenance window in Uptime Kuma."""
    try:
        _require_kuma_lib()
        with UptimeKumaApi(kuma_url, ssl_verify=verify_tls) as api:
            api.login(username, password)
            api.delete_maintenance(int(maintenance_id))
            return True
    except Exception as exc:
        print(f"Uptime Kuma Delete Maintenance Error: {exc}")
        return False


def pause_kuma_monitors(
    kuma_url: str,
    username: str,
    password: str,
    monitor_ids: list[Any],
    verify_tls: bool = True,
) -> bool:
    """Connect to Uptime Kuma and pause specified monitors."""
    try:
        _require_kuma_lib()
        with UptimeKumaApi(kuma_url, ssl_verify=verify_tls) as api:
            api.login(username, password)
            for m_id in monitor_ids:
                try:
                    api.pause_monitor(int(str(m_id).strip()))
                except Exception as ex:
                    print(f"Failed to pause monitor {m_id}: {ex}")
            return True
    except Exception as exc:
        print(f"Uptime Kuma Pause Monitor Error: {exc}")
        return False


def resume_kuma_monitors(
    kuma_url: str,
    username: str,
    password: str,
    monitor_ids: list[Any],
    verify_tls: bool = True,
) -> bool:
    """Connect to Uptime Kuma and resume specified monitors."""
    try:
        _require_kuma_lib()
        with UptimeKumaApi(kuma_url, ssl_verify=verify_tls) as api:
            api.login(username, password)
            for m_id in monitor_ids:
                try:
                    api.resume_monitor(int(str(m_id).strip()))
                except Exception as ex:
                    print(f"Failed to resume monitor {m_id}: {ex}")
            return True
    except Exception as exc:
        print(f"Uptime Kuma Resume Monitor Error: {exc}")
        return False
