from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def parse_api_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ChannelResource:
    channel_id: str
    title: str
    uploads_playlist_id: str
    raw: dict[str, Any] = field(repr=False)
    view_count: int | None = None
    subscriber_count: int | None = None
    video_count: int | None = None
    hidden_subscriber_count: bool | None = None


@dataclass(frozen=True, slots=True)
class VideoResource:
    video_id: str
    channel_id: str
    title: str
    published_at: datetime
    duration: str | None
    privacy_status: str | None
    view_count: int | None
    like_count: int | None
    comment_count: int | None
    raw: dict[str, Any] = field(repr=False)


@dataclass(frozen=True, slots=True)
class AnalyticsTable:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    raw: dict[str, Any] = field(repr=False)


@dataclass(frozen=True, slots=True)
class ReportingJob:
    job_id: str
    report_type_id: str
    name: str
    raw: dict[str, Any] = field(repr=False)


@dataclass(frozen=True, slots=True)
class ReportingReport:
    report_id: str
    job_id: str
    start_time: datetime | None
    end_time: datetime | None
    create_time: datetime | None
    download_url: str
    raw: dict[str, Any] = field(repr=False)


def optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(str(value))
