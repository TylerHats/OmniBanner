import httpx
from uptime_kuma_api import UptimeKumaAPI, MaintenanceStrategy

async def ping_kuma_push_url(push_url: str, is_active: bool, msg: str = "OmniBanner notice active") -> bool:
    """
    Pings Uptime Kuma Push Monitor.
    If is_active is True, it represents the notice is running, which can trigger a down state
    or maintenance state depending on how the user configured it, or simple status ping.
    Typically, users set Push monitors in Uptime Kuma.
    By default, we ping:
    - Down: status=down&msg=Message
    - Up: status=up&msg=Message
    """
    status = "down" if is_active else "up"
    # Format url to add parameters
    connector = "&" if "?" in push_url else "?"
    full_url = f"{push_url}{connector}status={status}&msg={msg}"
    
    async with httpx.AsyncClient(verify=False) as client:
        try:
            response = await client.get(full_url, timeout=10.0)
            if response.status_code == 200:
                return True
        except Exception as e:
            print(f"Failed to ping Uptime Kuma Push URL: {e}")
    return False

def sync_kuma_direct_maintenance(kuma_url: str, username: str, password: str, title: str, description: str, start_time, end_time, monitor_ids: list) -> bool:
    """
    Direct Socket.IO API connection to Uptime Kuma using the third party python library.
    We connect, log in, and add a Maintenance Window for the specified monitor IDs.
    """
    try:
        # Connect using the client
        # UptimeKumaAPI requires url as the first parameter
        with UptimeKumaAPI(kuma_url) as api:
            api.login(username, password)
            
            # Format dates to string as required by Uptime Kuma Socket API
            # Format: 'YYYY-MM-DD HH:MM:SS'
            start_str = start_time.strftime("%Y-%m-%d %H:%M:%S")
            end_str = end_time.strftime("%Y-%m-%d %H:%M:%S")
            
            # Create a maintenance window
            # Strategy: 'manual' (we can manage active manually) or 'cron' or 'schedule'
            # Let's check: strategy parameter in uptime_kuma_api expects the type or string
            # In Uptime Kuma, the strategies are:
            # - cron (Cron expression)
            # - manual (Manual toggle)
            # - schedule (One-time scheduled window)
            # We want 'schedule' for scheduled notices!
            maintenance = api.add_maintenance(
                title=title,
                description=description,
                strategy="schedule",
                start_date=start_str,
                end_date=end_str,
                active=True
            )
            
            maintenance_id = maintenance.get("id")
            
            # Link monitors to maintenance
            # We can use the api.edit_maintenance or Uptime Kuma lets us assign monitor list
            # In Uptime Kuma API: edit_maintenance(id, title, ..., monitor_list=[1, 2, 3])
            # Let's check: the add_maintenance can take monitor_list or we edit it.
            # Usually, monitors are linked in the edit / creation object as 'monitors' or 'monitor_list'.
            # The library supports monitor_list in add_maintenance / edit_maintenance.
            # Let's map integer monitor IDs:
            valid_monitor_ids = []
            for mid in monitor_ids:
                try:
                    valid_monitor_ids.append(int(mid))
                except ValueError:
                    pass
            
            if valid_monitor_ids:
                # We can call edit_maintenance to link monitors or include it in add_maintenance
                # Let's edit it to link the monitors:
                api.edit_maintenance(
                    id=maintenance_id,
                    title=title,
                    description=description,
                    strategy="schedule",
                    start_date=start_str,
                    end_date=end_str,
                    active=True,
                    monitors=[{"id": m_id} for m_id in valid_monitor_ids]
                )
            
            return True
    except Exception as e:
        print(f"Uptime Kuma Direct Integration Error: {e}")
        return False

def pause_kuma_monitors(kuma_url: str, username: str, password: str, monitor_ids: list) -> bool:
    """
    Alternative action: Connect to Uptime Kuma and pause the monitors immediately.
    """
    try:
        with UptimeKumaAPI(kuma_url) as api:
            api.login(username, password)
            for m_id in monitor_ids:
                try:
                    api.pause_monitor(int(m_id))
                except Exception as ex:
                    print(f"Failed to pause monitor {m_id}: {ex}")
            return True
    except Exception as e:
        print(f"Uptime Kuma Pause Monitor Error: {e}")
        return False

def resume_kuma_monitors(kuma_url: str, username: str, password: str, monitor_ids: list) -> bool:
    """
    Alternative action: Connect to Uptime Kuma and resume the monitors.
    """
    try:
        with UptimeKumaAPI(kuma_url) as api:
            api.login(username, password)
            for m_id in monitor_ids:
                try:
                    api.resume_monitor(int(m_id))
                except Exception as ex:
                    print(f"Failed to resume monitor {m_id}: {ex}")
            return True
    except Exception as e:
        print(f"Uptime Kuma Resume Monitor Error: {e}")
        return False
