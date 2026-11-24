"""In-memory buffered analytics aggregation and read-only public banner TTL cache."""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any, Callable, Optional


class AnalyticsBuffer:
    """Thread-safe counter buffer flushed periodically to SQLite/SQL DB."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[tuple[int, Optional[int], str], dict[str, int]] = defaultdict(
            lambda: {"views": 0, "dismisses": 0, "clicks": 0}
        )

    def record(
        self,
        notice_id: int,
        event_type: str,
        site_id: Optional[int] = None,
        domain: str = "",
    ) -> None:
        field_map = {"view": "views", "dismiss": "dismisses", "click": "clicks"}
        field = field_map.get(event_type)
        if not field:
            return
        norm_domain = (domain or "").strip().lower()[:255]
        key = (int(notice_id), int(site_id) if site_id else None, norm_domain)
        with self._lock:
            self._counts[key][field] += 1

    def pending_total(self) -> int:
        with self._lock:
            return sum(sum(v.values()) for v in self._counts.values())

    def flush_to_db(self, session_factory: Callable[[], Any]) -> int:
        """Atomically drain buffered counters and persist batched increments."""
        with self._lock:
            if not self._counts:
                return 0
            snapshot = dict(self._counts)
            self._counts.clear()

        from database import Notice, Site, SiteNoticeStat

        flushed = 0
        db = session_factory()
        try:
            notice_totals: dict[int, dict[str, int]] = defaultdict(
                lambda: {"views": 0, "dismisses": 0, "clicks": 0}
            )
            for (notice_id, site_id, domain), deltas in snapshot.items():
                notice_totals[notice_id]["views"] += deltas["views"]
                notice_totals[notice_id]["dismisses"] += deltas["dismisses"]
                notice_totals[notice_id]["clicks"] += deltas["clicks"]

                resolved_site_id = site_id
                if resolved_site_id is None and domain:
                    site = db.query(Site.id).filter(Site.domain == domain).first()
                    if site:
                        resolved_site_id = site[0]

                if resolved_site_id is not None or domain:
                    q = db.query(SiteNoticeStat).filter(SiteNoticeStat.notice_id == notice_id)
                    if resolved_site_id is not None:
                        q = q.filter(SiteNoticeStat.site_id == resolved_site_id)
                    else:
                        q = q.filter(SiteNoticeStat.domain == domain)
                    stat = q.first()
                    if not stat:
                        stat = SiteNoticeStat(
                            notice_id=notice_id,
                            site_id=resolved_site_id,
                            domain=domain,
                            views_count=0,
                            dismiss_count=0,
                            clicks_count=0,
                        )
                        db.add(stat)
                    stat.views_count = (stat.views_count or 0) + deltas["views"]
                    stat.dismiss_count = (stat.dismiss_count or 0) + deltas["dismisses"]
                    stat.clicks_count = (stat.clicks_count or 0) + deltas["clicks"]

            for notice_id, deltas in notice_totals.items():
                notice = db.query(Notice).filter(Notice.id == notice_id).first()
                if notice:
                    notice.views_count = (notice.views_count or 0) + deltas["views"]
                    notice.dismiss_count = (notice.dismiss_count or 0) + deltas["dismisses"]
                    notice.clicks_count = (notice.clicks_count or 0) + deltas["clicks"]
                    flushed += deltas["views"] + deltas["dismisses"] + deltas["clicks"]

            db.commit()
        except Exception as exc:
            db.rollback()
            print(f"AnalyticsBuffer flush failed: {exc}")
        finally:
            db.close()
        return flushed


class BannerResponseCache:
    """Thread-safe short-TTL response cache for public banner queries."""

    def __init__(self, ttl_seconds: int = 15) -> None:
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if not entry:
                return None
            expires_at, value = entry
            if now >= expires_at:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        expires_at = time.monotonic() + self.ttl_seconds
        with self._lock:
            if len(self._store) > 2000:
                self._store.clear()
            self._store[key] = (expires_at, value)

    def invalidate(self) -> None:
        with self._lock:
            self._store.clear()


analytics_buffer = AnalyticsBuffer()
banner_cache = BannerResponseCache(ttl_seconds=15)

