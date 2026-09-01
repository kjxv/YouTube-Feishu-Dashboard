from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from youtube_feishu_dashboard.api.youtube.protocols import YouTubeDataGateway
from youtube_feishu_dashboard.api.youtube.schemas import VideoResource
from youtube_feishu_dashboard.catalog.field_catalog import ApiRequestPlan
from youtube_feishu_dashboard.core.errors import ConfigurationError
from youtube_feishu_dashboard.db.models import VideoSnapshot
from youtube_feishu_dashboard.db.repositories import Storage
from youtube_feishu_dashboard.services.archive import ArchiveService
from youtube_feishu_dashboard.services.feishu_records import FeishuRecordService

from yfd_latest_video_tracker.manifest import (
    DEFAULT_FIELD_MAPPING,
    MODULE_ID,
)

ISO_DURATION = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+)S)?)?$"
)


@dataclass(frozen=True, slots=True)
class LatestTrackerConfig:
    channel_id: str | None
    tracking_days: int
    interval_minutes: int
    target_table_id: str
    field_mapping: dict[str, str]
    timezone: str = "Asia/Shanghai"


class LatestVideoTrackerService:
    def __init__(
        self,
        *,
        youtube: YouTubeDataGateway,
        storage: Storage,
        records: FeishuRecordService,
        archive: ArchiveService,
        config: LatestTrackerConfig,
        request_plan: ApiRequestPlan,
    ) -> None:
        self.youtube = youtube
        self.storage = storage
        self.records = records
        self.archive = archive
        self.config = config
        self.request_plan = request_plan

    def track(self, observed_at: datetime) -> tuple[dict[str, int], dict[str, Any]]:
        channel = self.youtube.get_channel(self.config.channel_id)
        latest_ids = self.youtube.list_upload_video_ids(
            channel.uploads_playlist_id,
            max_pages=1,
        )
        if not latest_ids:
            return {"videos": 0, "snapshots": 0, "feishu_writes": 0}, {
                "reason": "channel_has_no_uploaded_video"
            }
        resources = self.youtube.list_videos(
            latest_ids[:1],
            parts=self.request_plan.data_api_parts,
        )
        if not resources:
            return {"videos": 0, "snapshots": 0, "feishu_writes": 0}, {
                "reason": "latest_video_not_accessible"
            }
        video = resources[0]
        tracking_cutoff = observed_at - timedelta(days=self.config.tracking_days)
        if video.published_at < tracking_cutoff:
            return {"videos": 1, "snapshots": 0, "feishu_writes": 0}, {
                "reason": "latest_video_outside_tracking_window",
                "video_id": video.video_id,
            }

        with self.storage.transaction() as repos:
            previous = repos.videos.latest_snapshot_before(video.video_id, observed_at)
            repos.channels.upsert(
                channel.channel_id,
                title=channel.title,
                uploads_playlist_id=channel.uploads_playlist_id,
                timezone=self.config.timezone,
                raw_json=channel.raw,
            )
            repos.videos.upsert(
                {
                    "id": video.video_id,
                    "channel_id": video.channel_id or channel.channel_id,
                    "title": video.title,
                    "published_at": video.published_at,
                    "duration_seconds": parse_iso_duration_seconds(video.duration),
                    "privacy_status": video.privacy_status,
                    "raw_json": video.raw,
                }
            )
            repos.videos.add_snapshot(
                video_id=video.video_id,
                observed_at=observed_at,
                view_count=video.view_count,
                like_count=video.like_count,
                comment_count=video.comment_count,
                raw_json=video.raw,
            )

        archive_decision = self.archive.ensure_capacity(
            module_id=MODULE_ID, table_id=self.config.target_table_id
        )
        if archive_decision.status == "archive_required":
            return {"videos": 1, "snapshots": 1, "feishu_writes": 0}, {
                "video_id": video.video_id,
                "reason": "feishu_archive_required",
                "archive_status": archive_decision.status,
                "archive_batch_id": archive_decision.batch_id,
            }
        values = self._standard_values(video, previous, observed_at)
        fields = {
            column: values[field_id]
            for field_id, column in self.config.field_mapping.items()
            if field_id in values and values[field_id] is not None and column
        }
        unique_key = str(values["MODULE_UNIQUE_KEY"])
        sync = self.records.upsert_entity(
            table_id=self.config.target_table_id,
            entity_type="latest_video_snapshot",
            entity_key=unique_key,
            fields=fields,
        )
        return {"videos": 1, "snapshots": 1, "feishu_writes": int(sync.action != "unchanged")}, {
            "video_id": video.video_id,
            "sync_action": sync.action,
            "archive_status": archive_decision.status,
            "archive_batch_id": archive_decision.batch_id,
        }

    def _standard_values(
        self,
        video: VideoResource,
        previous: VideoSnapshot | None,
        observed_at: datetime,
    ) -> dict[str, Any]:
        bucket_seconds = self.config.interval_minutes * 60
        bucket_timestamp = int(observed_at.timestamp()) // bucket_seconds * bucket_seconds
        bucket = datetime.fromtimestamp(bucket_timestamp, tz=observed_at.tzinfo)
        age_minutes = max(0, int((observed_at - video.published_at).total_seconds() // 60))
        return {
            "MODULE_UNIQUE_KEY": f"{video.video_id}_{bucket.isoformat()}",
            "SYSTEM_OBSERVED_AT": to_epoch_milliseconds(observed_at),
            "VIDEO_ID": video.video_id,
            "VIDEO_CHANNEL_ID": video.channel_id,
            "VIDEO_TITLE": video.title,
            "VIDEO_PUBLISHED_AT": to_epoch_milliseconds(video.published_at),
            "VIDEO_DURATION": parse_iso_duration_seconds(video.duration),
            "VIDEO_PRIVACY_STATUS": video.privacy_status,
            "VIDEO_AGE_MINUTES": age_minutes,
            "VIDEO_VIEWS_PUBLIC": video.view_count,
            "VIDEO_VIEW_DELTA": delta(video.view_count, previous.view_count if previous else None),
            "VIDEO_LIKES_PUBLIC": video.like_count,
            "VIDEO_LIKE_DELTA": delta(video.like_count, previous.like_count if previous else None),
            "VIDEO_COMMENTS_PUBLIC": video.comment_count,
            "VIDEO_COMMENT_DELTA": delta(
                video.comment_count, previous.comment_count if previous else None
            ),
            "VIDEO_URL": {
                "link": f"https://www.youtube.com/watch?v={video.video_id}",
                "text": "打开视频",
            },
            "SYSTEM_LAST_SYNCED_AT": to_epoch_milliseconds(observed_at),
        }


def merge_field_mapping(
    overrides: dict[str, str] | None = None, *, use_defaults: bool = True
) -> dict[str, str]:
    result = dict(DEFAULT_FIELD_MAPPING) if use_defaults else {}
    result.update(overrides or {})
    return result


def parse_iso_duration_seconds(value: str | None) -> int | None:
    if not value:
        return None
    match = ISO_DURATION.fullmatch(value)
    if match is None:
        raise ConfigurationError(f"无法解析 YouTube ISO 8601 时长：{value}")
    parts = {key: int(raw or 0) for key, raw in match.groupdict().items()}
    return parts["days"] * 86400 + parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"]


def delta(current: int | None, previous: int | None) -> int:
    if current is None or previous is None:
        return 0
    return current - previous


def to_epoch_milliseconds(value: datetime) -> int:
    return int(value.timestamp() * 1000)
