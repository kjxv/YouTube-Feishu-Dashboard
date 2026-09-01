from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from youtube_feishu_dashboard.core.time import as_utc, utc_now
from youtube_feishu_dashboard.db.repositories import Storage


@dataclass(frozen=True, slots=True)
class HealthStatus:
    instance_id: str
    status: str
    last_seen_at: datetime
    stale: bool
    details: dict[str, Any] | None


class HealthService:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def record(
        self,
        *,
        instance_id: str,
        status: str,
        version: str,
        details: dict[str, Any] | None = None,
        seen_at: datetime | None = None,
    ) -> None:
        with self.storage.transaction() as repos:
            repos.health.heartbeat(
                instance_id=instance_id,
                status=status,
                version=version,
                seen_at=seen_at or utc_now(),
                details=details,
            )

    def status(
        self,
        instance_id: str,
        *,
        now: datetime | None = None,
        stale_after_seconds: int = 1800,
    ) -> HealthStatus | None:
        observed_at = now or utc_now()
        with self.storage.transaction() as repos:
            heartbeat = repos.health.get(instance_id)
            if heartbeat is None:
                return None
            last_seen = as_utc(heartbeat.last_seen_at)
            return HealthStatus(
                instance_id=heartbeat.instance_id,
                status=heartbeat.status,
                last_seen_at=last_seen,
                stale=last_seen + timedelta(seconds=stale_after_seconds) < observed_at,
                details=heartbeat.details,
            )
