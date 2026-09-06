from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import ceil
from typing import Any
from zoneinfo import ZoneInfo

from youtube_feishu_dashboard.api.youtube.protocols import YouTubeDataGateway
from youtube_feishu_dashboard.api.youtube.schemas import ChannelResource, VideoResource
from youtube_feishu_dashboard.core.errors import ConfigurationError
from youtube_feishu_dashboard.core.time import as_utc
from youtube_feishu_dashboard.db.models import VideoSnapshot
from youtube_feishu_dashboard.db.repositories import Storage
from youtube_feishu_dashboard.services.api_time_fields import (
    BEIJING_TIMEZONE,
    analytics_time_values,
    data_api_time_values,
)
from youtube_feishu_dashboard.services.feishu_records import (
    EntityUpsert,
    FeishuRecordService,
    SyncResult,
)

from yfd_channel_history.analytics import ChannelAnalyticsCollector, DailyAnalytics
from yfd_channel_history.runtime import ChannelHistoryRuntimePlan

SNAPSHOT_SOURCE = "channel_history_daily"
FORTY_EIGHT_HOUR_MINUTES = 48 * 60
_FEISHU_BATCH_SIZE = 500
_REMOTE_KEY_FIELD_IDS = {
    "视频主表": "VIDEO_ID",
    "视频历史数据": "DAILY_VIDEO_RECORD_ID",
    "频道历史数据": "DAILY_CHANNEL_RECORD_ID",
}
_ISO_DURATION = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?"
    r"(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


@dataclass(frozen=True, slots=True)
class ChannelHistoryConfig:
    channel_id: str | None
    runtime_plan: ChannelHistoryRuntimePlan
    timezone: str = "Asia/Shanghai"
    ranking_window_days: int = 7
    analytics_lookback_days: int = 7
    forty_eight_hour_tolerance_minutes: int = 90
    max_upload_pages: int = 1000


@dataclass(frozen=True, slots=True)
class _SyncRequest:
    entity_type: str
    entity_key: str
    values: dict[str, object]


class ChannelHistoryService:
    def __init__(
        self,
        *,
        youtube: YouTubeDataGateway,
        analytics: ChannelAnalyticsCollector,
        storage: Storage,
        records: FeishuRecordService,
        config: ChannelHistoryConfig,
    ) -> None:
        self.youtube = youtube
        self.analytics = analytics
        self.storage = storage
        self.records = records
        self.config = config

    def collect(self, observed_at: datetime) -> tuple[dict[str, int], dict[str, Any]]:
        observed_at = as_utc(observed_at)
        timezone = ZoneInfo(self.config.timezone)
        snapshot_date = observed_at.astimezone(timezone).date()
        channel = self.youtube.get_channel(self.config.channel_id)
        upload_ids = self.youtube.list_upload_video_ids(
            channel.uploads_playlist_id,
            max_pages=self.config.max_upload_pages,
        )
        resources = self.youtube.list_videos(upload_ids)
        classification = self.analytics.classify_long_videos(resources, observed_at)
        by_id = {item.video_id: item for item in resources}
        long_videos = [by_id[video_id] for video_id in classification.long_video_ids]
        classification_complete = (
            len(by_id) == len(upload_ids)
            and not {
                "creator_content_type_unconfirmed",
                "mixed_or_unsupported_creator_content_type",
            }.intersection(classification.skipped.values())
        )
        daily = self.analytics.collect_daily(
            long_video_ids=classification.long_video_ids,
            observed_at=observed_at,
            lookback_days=self.config.analytics_lookback_days,
        )

        self._store_metadata(channel, long_videos)
        current_channel_snapshot_key = (
            f"{channel.channel_id}_{snapshot_date.isoformat()}_snapshot"
        )
        prior_subscribers = self._previous_subscriber_snapshot(
            exclude_entity_key=current_channel_snapshot_key
        )
        counts = self._empty_counts()
        video_main_requests: list[_SyncRequest] = []
        video_snapshot_requests: list[_SyncRequest] = []
        video_baselines: list[VideoSnapshot | None] = []
        video_48h_samples: list[VideoSnapshot | None] = []
        for video in long_videos:
            baseline = self._seven_day_baseline(video.video_id, observed_at)
            sample_48h = self._forty_eight_hour_sample(video, observed_at)
            values = self._video_values(
                video,
                observed_at,
                baseline,
                sample_48h,
                daily,
            )
            video_main_requests.append(
                _SyncRequest("channel_video_main", video.video_id, values)
            )
            snapshot_key = f"{video.video_id}_{snapshot_date.isoformat()}_snapshot"
            snapshot_values = {
                **values,
                "DAILY_VIDEO_RECORD_ID": snapshot_key,
                "DAILY_SNAPSHOT_DATE_BEIJING": snapshot_date,
                "DAILY_RECORD_TYPE": "每日采样快照",
            }
            video_snapshot_requests.append(
                _SyncRequest(
                    "channel_video_daily_snapshot", snapshot_key, snapshot_values
                )
            )
            self._store_snapshot(video, observed_at)
            video_baselines.append(baseline)
            video_48h_samples.append(sample_48h)

        video_analytics_requests = self._video_analytics_requests(long_videos, daily)
        channel_snapshot_request = self._channel_snapshot_request(
            channel=channel,
            long_videos=long_videos,
            observed_at=observed_at,
            snapshot_date=snapshot_date,
            previous_subscribers=prior_subscribers,
            classification_complete=classification_complete,
        )
        channel_analytics_requests = self._channel_analytics_requests(channel, daily)

        video_main_results, video_main_write_requests = self._sync_many(
            "视频主表", video_main_requests
        )
        video_history_results, video_history_write_requests = self._sync_many(
            "视频历史数据",
            [*video_snapshot_requests, *video_analytics_requests],
        )
        channel_results, channel_write_requests = self._sync_many(
            "频道历史数据",
            [channel_snapshot_request, *channel_analytics_requests],
        )
        counts["feishu_batch_requests"] += (
            video_main_write_requests
            + video_history_write_requests
            + channel_write_requests
        )

        video_snapshot_results = video_history_results[: len(video_snapshot_requests)]
        video_analytics_results = video_history_results[len(video_snapshot_requests) :]
        channel_snapshot = channel_results[0]
        channel_analytics = channel_results[1:]

        video_details: list[dict[str, Any]] = []
        for video, baseline, sample_48h, main, history in zip(
            long_videos,
            video_baselines,
            video_48h_samples,
            video_main_results,
            video_snapshot_results,
            strict=True,
        ):
            self._add_sync_count(counts, "video_main_records", main)
            self._add_sync_count(counts, "video_snapshot_records", history)
            video_details.append(
                {
                    "video_id": video.video_id,
                    "main_action": main.action,
                    "snapshot_action": history.action,
                    "seven_day_baseline_at": (
                        as_utc(baseline.observed_at).isoformat() if baseline else None
                    ),
                    "forty_eight_hour_sample_at": (
                        as_utc(sample_48h.observed_at).isoformat()
                        if sample_48h
                        else None
                    ),
                }
            )

        for sync in video_analytics_results:
            self._add_sync_count(counts, "video_analytics_records", sync)
        self._add_sync_count(counts, "channel_snapshot_records", channel_snapshot)
        for sync in channel_analytics:
            self._add_sync_count(counts, "channel_analytics_records", sync)
        current_records = {"CHANNEL_CURRENT_SNAPSHOT": channel_snapshot.record_id}
        if channel_analytics:
            current_records["CHANNEL_CURRENT_ANALYTICS_DAY"] = channel_analytics[-1].record_id
        flag_reset_records, flag_reset_requests = self._clear_previous_current_flags(
            current_records
        )
        counts["current_flag_reset_records"] = flag_reset_records
        counts["feishu_records_changed"] += flag_reset_records
        counts["feishu_batch_requests"] += flag_reset_requests

        counts["long_videos"] = len(long_videos)
        counts["skipped_videos"] = len(classification.skipped)
        return counts, {
            "channel_id": channel.channel_id,
            "snapshot_date_beijing": snapshot_date.isoformat(),
            "analytics_data_through_date_pacific": daily.data_through_date.isoformat(),
            "upload_video_count": len(upload_ids),
            "long_video_count": len(long_videos),
            "long_video_classification_complete": classification_complete,
            "skipped_video_count": len(classification.skipped),
            "skipped_videos": classification.skipped,
            "creator_content_types": classification.creator_types,
            "youtube_analytics_requests": (
                classification.api_requests + daily.api_requests
            ),
            "video_results": video_details,
        }

    def _video_values(
        self,
        video: VideoResource,
        observed_at: datetime,
        baseline: VideoSnapshot | None,
        sample_48h: VideoSnapshot | None,
        daily: DailyAnalytics,
    ) -> dict[str, object]:
        seven_day_views: int | None = None
        baseline_text: str | None = None
        if (
            baseline is not None
            and baseline.view_count is not None
            and video.view_count is not None
        ):
            value = video.view_count - baseline.view_count
            if value >= 0:
                seven_day_views = value
                baseline_text = as_utc(baseline.observed_at).astimezone(
                    BEIJING_TIMEZONE
                ).isoformat(timespec="seconds")
        return {
            **data_api_time_values(observed_at),
            **analytics_time_values(
                fetched_at=daily.fetched_at,
                data_through_date=daily.data_through_date,
            ),
            "VIDEO_ID": video.video_id,
            "VIDEO_TITLE": video.title,
            "VIDEO_PUBLISHED_AT": _epoch_ms(video.published_at),
            "VIDEO_URL": f"https://www.youtube.com/watch?v={video.video_id}",
            "VIDEO_THUMBNAIL_URL": _thumbnail_url(video),
            "VIDEO_VIEWS_PUBLIC": video.view_count,
            "VIDEO_LIKES_PUBLIC": video.like_count,
            "VIDEO_COMMENTS_PUBLIC": video.comment_count,
            "VIDEO_DURATION_TEXT": _duration_text(video.duration),
            "VIDEO_TYPE": "长视频",
            "VIDEO_VIEWS_LAST_7D_INFERRED": seven_day_views,
            "VIDEO_7D_SAMPLE_START_AT_BEIJING": baseline_text,
            "VIDEO_7D_SAMPLE_END_AT_BEIJING": observed_at.astimezone(
                BEIJING_TIMEZONE
            ).isoformat(timespec="seconds"),
            "VIDEO_VIEWS_AT_48H": sample_48h.view_count if sample_48h else None,
            "VIDEO_48H_SAMPLE_AGE_MINUTES": (
                int(
                    (
                        as_utc(sample_48h.observed_at) - as_utc(video.published_at)
                    ).total_seconds()
                    // 60
                )
                if sample_48h
                else None
            ),
            "VIDEO_48H_SAMPLE_AT_BEIJING": (
                as_utc(sample_48h.observed_at)
                .astimezone(BEIJING_TIMEZONE)
                .isoformat(timespec="seconds")
                if sample_48h
                else None
            ),
        }

    def _video_analytics_requests(
        self, videos: list[VideoResource], daily: DailyAnalytics
    ) -> list[_SyncRequest]:
        requests: list[_SyncRequest] = []
        time_values = analytics_time_values(
            fetched_at=daily.fetched_at,
            data_through_date=daily.data_through_date,
        )
        settled_days = sorted(daily.overall)
        for video in videos:
            for day in settled_days:
                entity_key = f"{video.video_id}_{day.isoformat()}_analytics"
                requests.append(
                    _SyncRequest(
                        "channel_video_analytics_day",
                        entity_key,
                        {
                            **time_values,
                            "DAILY_VIDEO_RECORD_ID": entity_key,
                            "VIDEO_ID": video.video_id,
                            "VIDEO_TITLE": video.title,
                            "VIDEO_PUBLISHED_AT": _epoch_ms(video.published_at),
                            "VIDEO_TYPE": "长视频",
                            "ANALYTICS_DAY": day,
                            "DAILY_RECORD_TYPE": "Analytics日统计",
                            "ANALYTICS_VIEWS": daily.video_views.get(
                                (video.video_id, day), 0
                            ),
                        },
                    )
                )
        return requests

    def _channel_snapshot_request(
        self,
        *,
        channel: ChannelResource,
        long_videos: list[VideoResource],
        observed_at: datetime,
        snapshot_date: date,
        previous_subscribers: int | None,
        classification_complete: bool,
    ) -> _SyncRequest:
        entity_key = f"{channel.channel_id}_{snapshot_date.isoformat()}_snapshot"
        view_counts = [item.view_count for item in long_videos]
        total_long_views = (
            sum(value for value in view_counts if value is not None)
            if classification_complete and all(value is not None for value in view_counts)
            else None
        )
        total_long_videos = len(long_videos) if classification_complete else None
        subscriber_change = (
            channel.subscriber_count - previous_subscribers
            if channel.subscriber_count is not None and previous_subscribers is not None
            else None
        )
        return _SyncRequest(
            "channel_daily_snapshot",
            entity_key,
            {
                **data_api_time_values(observed_at),
                "DAILY_CHANNEL_RECORD_ID": entity_key,
                "CHANNEL_ID": channel.channel_id,
                "DAILY_SNAPSHOT_DATE_BEIJING": snapshot_date,
                "DAILY_RECORD_TYPE": "频道采样快照",
                "CHANNEL_SUBSCRIBERS_PUBLIC": channel.subscriber_count,
                "CHANNEL_SUBSCRIBER_SNAPSHOT_CHANGE": subscriber_change,
                "CHANNEL_VIEWS_PUBLIC": channel.view_count,
                "CHANNEL_VIDEO_COUNT": channel.video_count,
                "CHANNEL_CURRENT_SNAPSHOT": True,
                "CHANNEL_CURRENT_ANALYTICS_DAY": False,
                "CHANNEL_LONG_VIDEO_VIEWS_PUBLIC": total_long_views,
                "CHANNEL_LONG_VIDEO_COUNT": total_long_videos,
                "SUBSCRIBER_DATA_SOURCE": "API快照",
                "SUBSCRIBER_DATE_BASIS": "北京时间",
            },
        )

    def _channel_analytics_requests(
        self, channel: ChannelResource, daily: DailyAnalytics
    ) -> list[_SyncRequest]:
        requests: list[_SyncRequest] = []
        days = sorted(daily.overall)
        time_values = analytics_time_values(
            fetched_at=daily.fetched_at,
            data_through_date=daily.data_through_date,
        )
        latest = days[-1] if days else None
        for day in days:
            metrics = daily.overall[day]
            entity_key = f"{channel.channel_id}_{day.isoformat()}_analytics"
            requests.append(
                _SyncRequest(
                    "channel_analytics_day",
                    entity_key,
                    {
                        **time_values,
                        "DAILY_CHANNEL_RECORD_ID": entity_key,
                        "ANALYTICS_DAY": day,
                        "CHANNEL_ID": channel.channel_id,
                        "DAILY_RECORD_TYPE": "Analytics日统计",
                        "ANALYTICS_VIEWS": metrics["views"],
                        "ANALYTICS_SUB_GAINED": metrics["subscribersGained"],
                        "ANALYTICS_SUB_LOST": metrics["subscribersLost"],
                        "CHANNEL_CURRENT_SNAPSHOT": False,
                        "CHANNEL_CURRENT_ANALYTICS_DAY": day == latest,
                        "CHANNEL_LONG_VIDEO_DAILY_VIEWS": daily.long_views.get(day, 0),
                        "SUBSCRIBER_DATE_BASIS": "太平洋时间",
                    },
                )
            )
        return requests

    def _seven_day_baseline(
        self, video_id: str, observed_at: datetime
    ) -> VideoSnapshot | None:
        target = observed_at - timedelta(days=self.config.ranking_window_days)
        with self.storage.transaction() as repos:
            baseline = repos.videos.latest_snapshot_at_or_before(
                video_id,
                target,
                source=SNAPSHOT_SOURCE,
            )
        if baseline is None:
            return None
        age = target - as_utc(baseline.observed_at)
        return baseline if age <= timedelta(hours=36) else None

    def _forty_eight_hour_sample(
        self, video: VideoResource, observed_at: datetime
    ) -> VideoSnapshot | None:
        target = as_utc(video.published_at) + timedelta(
            minutes=FORTY_EIGHT_HOUR_MINUTES
        )
        if as_utc(observed_at) < target:
            return None
        with self.storage.transaction() as repos:
            return repos.videos.nearest_snapshot_to(
                video.video_id,
                target,
                tolerance_minutes=self.config.forty_eight_hour_tolerance_minutes,
            )

    def _store_metadata(
        self, channel: ChannelResource, videos: list[VideoResource]
    ) -> None:
        with self.storage.transaction() as repos:
            repos.channels.upsert(
                channel.channel_id,
                title=channel.title,
                uploads_playlist_id=channel.uploads_playlist_id,
                timezone=self.config.timezone,
                raw_json=channel.raw,
            )
            for item in videos:
                repos.videos.upsert(
                    {
                        "id": item.video_id,
                        "channel_id": item.channel_id,
                        "title": item.title,
                        "published_at": as_utc(item.published_at),
                        "duration_seconds": _duration_seconds(item.duration),
                        "privacy_status": item.privacy_status,
                        "video_type": "长视频",
                        "raw_json": item.raw,
                    }
                )

    def _store_snapshot(self, video: VideoResource, observed_at: datetime) -> None:
        with self.storage.transaction() as repos:
            repos.videos.add_snapshot(
                video_id=video.video_id,
                observed_at=observed_at,
                view_count=video.view_count,
                like_count=video.like_count,
                comment_count=video.comment_count,
                raw_json=video.raw,
                source=SNAPSHOT_SOURCE,
            )

    def _previous_subscriber_snapshot(self, *, exclude_entity_key: str) -> int | None:
        table = self.config.runtime_plan.require_table("频道历史数据")
        key_column = table.mapping.get("DAILY_CHANNEL_RECORD_ID")
        record_type_column = table.mapping.get("DAILY_RECORD_TYPE")
        subscriber_column = table.mapping.get("CHANNEL_SUBSCRIBERS_PUBLIC")
        current_column = table.mapping.get("CHANNEL_CURRENT_SNAPSHOT")
        if not record_type_column or not subscriber_column:
            return None
        records = self.records.gateway.list_records(self.records.app_token, table.table_id)
        candidates: list[tuple[bool, str, int]] = []
        date_column = table.mapping.get("DAILY_SNAPSHOT_DATE_BEIJING")
        for record in records:
            fields = record.get("fields", {})
            if key_column and _scalar(fields.get(key_column)) == exclude_entity_key:
                continue
            if _scalar(fields.get(record_type_column)) != "频道采样快照":
                continue
            subscriber = _int_or_none(fields.get(subscriber_column))
            if subscriber is None:
                continue
            candidates.append(
                (
                    _truthy(fields.get(current_column)) if current_column else False,
                    str(_scalar(fields.get(date_column))) if date_column else "",
                    subscriber,
                )
            )
        return max(candidates)[2] if candidates else None

    def _clear_previous_current_flags(
        self, keep_records: dict[str, str]
    ) -> tuple[int, int]:
        table = self.config.runtime_plan.require_table("频道历史数据")
        current_columns = {
            field_id: column
            for field_id in keep_records
            if (column := table.mapping.get(field_id))
        }
        if not current_columns:
            return 0, 0
        updates: list[dict[str, object]] = []
        for record in self.records.gateway.list_records(
            self.records.app_token, table.table_id
        ):
            fields = record.get("fields", {})
            record_id = record.get("record_id")
            reset = {
                column: False
                for field_id, column in current_columns.items()
                if str(record_id) != keep_records[field_id]
                and _truthy(fields.get(column))
            }
            if reset and record_id:
                updates.append({"record_id": str(record_id), "fields": reset})
        if updates:
            self.records.gateway.batch_update_records(
                self.records.app_token, table.table_id, updates
            )
        return len(updates), ceil(len(updates) / _FEISHU_BATCH_SIZE)

    def _sync_many(
        self, table_name: str, requests: list[_SyncRequest]
    ) -> tuple[list[SyncResult], int]:
        table = self.config.runtime_plan.require_table(table_name)
        entities: list[EntityUpsert] = []
        for request in requests:
            fields = self.config.runtime_plan.adapt_partial(table_name, request.values)
            if not fields:
                raise ConfigurationError(f"{table_name} 没有可写字段。")
            entities.append(
                EntityUpsert(request.entity_type, request.entity_key, fields)
            )
        results = self.records.upsert_entities(
            table_id=table.table_id,
            entities=entities,
            remote_key_field=table.mapping[_REMOTE_KEY_FIELD_IDS[table_name]],
        )
        created = sum(result.action == "created" for result in results)
        updated = sum(result.action == "updated" for result in results)
        write_requests = ceil(created / _FEISHU_BATCH_SIZE) + ceil(
            updated / _FEISHU_BATCH_SIZE
        )
        return results, write_requests

    @staticmethod
    def _empty_counts() -> dict[str, int]:
        return {
            "long_videos": 0,
            "skipped_videos": 0,
            "video_main_records": 0,
            "video_snapshot_records": 0,
            "video_analytics_records": 0,
            "channel_snapshot_records": 0,
            "channel_analytics_records": 0,
            "current_flag_reset_records": 0,
            "feishu_bindings_adopted": 0,
            "feishu_records_changed": 0,
            "feishu_batch_requests": 0,
        }

    @staticmethod
    def _add_sync_count(
        counts: dict[str, int], key: str, result: SyncResult
    ) -> None:
        changed = int(result.action != "unchanged")
        counts[key] += changed
        counts["feishu_bindings_adopted"] += int(result.binding_adopted)
        counts["feishu_records_changed"] += changed


def _duration_seconds(value: str | None) -> int | None:
    if not value:
        return None
    match = _ISO_DURATION.fullmatch(value)
    if match is None:
        return None
    return (
        int(match.group("days") or 0) * 86400
        + int(match.group("hours") or 0) * 3600
        + int(match.group("minutes") or 0) * 60
        + int(match.group("seconds") or 0)
    )


def _duration_text(value: str | None) -> str | None:
    seconds = _duration_seconds(value)
    if seconds is None:
        return None
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _thumbnail_url(video: VideoResource) -> str | None:
    thumbnails = video.raw.get("snippet", {}).get("thumbnails", {})
    for name in ("maxres", "standard", "high", "medium", "default"):
        value = thumbnails.get(name, {}).get("url")
        if value:
            return str(value)
    return None


def _epoch_ms(value: datetime) -> int:
    return int(as_utc(value).timestamp() * 1000)


def _scalar(value: object) -> object:
    if isinstance(value, list) and len(value) == 1:
        return value[0]
    return value


def _int_or_none(value: object) -> int | None:
    value = _scalar(value)
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return int(float(str(value)))
    except ValueError:
        return None


def _truthy(value: object) -> bool:
    value = _scalar(value)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "是"}
