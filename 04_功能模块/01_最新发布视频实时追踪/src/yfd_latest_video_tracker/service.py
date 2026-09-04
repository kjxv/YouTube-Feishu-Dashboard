from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import mean, median
from typing import Any
from zoneinfo import ZoneInfo

from youtube_feishu_dashboard.api.youtube.extraction import DataApiExtraction
from youtube_feishu_dashboard.api.youtube.protocols import YouTubeDataGateway
from youtube_feishu_dashboard.api.youtube.schemas import ChannelResource, VideoResource
from youtube_feishu_dashboard.catalog.field_catalog import ApiRequestPlan
from youtube_feishu_dashboard.core.errors import (
    AuthenticationError,
    ConfigurationError,
    ExternalServiceError,
)
from youtube_feishu_dashboard.core.time import as_utc
from youtube_feishu_dashboard.db.models import VideoSnapshot
from youtube_feishu_dashboard.db.repositories import ComparableSnapshot, Storage
from youtube_feishu_dashboard.services.archive import ArchiveDecision, ArchiveService
from youtube_feishu_dashboard.services.dynamic_mapping import DynamicModulePlan
from youtube_feishu_dashboard.services.feishu_records import FeishuRecordService, SyncResult
from youtube_feishu_dashboard.services.tracking_video_config import parse_tracking_video_ids

from yfd_latest_video_tracker.analytics import VideoAnalyticsCollector
from yfd_latest_video_tracker.api_time_fields import (
    ANALYTICS_TIME_FIELD_IDS,
    REPORTING_TIME_FIELD_IDS,
    data_api_time_values,
)
from yfd_latest_video_tracker.manifest import DEFAULT_FIELD_MAPPING, MODULE_ID
from yfd_latest_video_tracker.reporting import VideoReachReportingCollector

ISO_DURATION = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+)S)?)?$"
)


@dataclass(frozen=True, slots=True)
class TableSyncConfig:
    table_id: str
    field_mapping: dict[str, str]
    table_name: str | None = None


@dataclass(frozen=True, slots=True)
class LatestTrackerConfig:
    channel_id: str | None
    tracking_days: int
    interval_minutes: int
    main_table: TableSyncConfig
    snapshot_table: TableSyncConfig
    comparison_table: TableSyncConfig
    timezone: str = "Asia/Shanghai"
    tracking_video_ids: tuple[str, ...] = ()
    analytics_interval_hours: int = 24
    reporting_interval_hours: int = 24


@dataclass(frozen=True, slots=True)
class PreparedVideo:
    resource: VideoResource
    duration_seconds: int | None
    video_type: str
    extraction: DataApiExtraction | None


@dataclass(frozen=True, slots=True)
class StoredVideoContext:
    previous: VideoSnapshot | None
    tracking_started_at: datetime
    comparison_candidates: list[ComparableSnapshot]


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
        dynamic_plan: DynamicModulePlan | None = None,
        analytics: VideoAnalyticsCollector | None = None,
        reporting: VideoReachReportingCollector | None = None,
    ) -> None:
        self.youtube = youtube
        self.storage = storage
        self.records = records
        self.archive = archive
        self.config = config
        self.request_plan = request_plan
        self.dynamic_plan = dynamic_plan
        self.analytics = analytics
        self.reporting = reporting

    def track(self, observed_at: datetime) -> tuple[dict[str, int], dict[str, Any]]:
        selection = parse_tracking_video_ids("\n".join(self.config.tracking_video_ids))
        requested_ids = selection.video_ids
        channel = self.youtube.get_channel(self.config.channel_id)
        resources = self.youtube.list_videos(
            requested_ids,
            parts=self.request_plan.data_api_parts,
        )
        ordered_resources = self._validate_requested_resources(
            requested_ids=requested_ids,
            resources=resources,
            channel=channel,
        )
        tracking_cutoff = as_utc(observed_at) - timedelta(days=self.config.tracking_days)
        expired = tuple(
            video for video in ordered_resources if as_utc(video.published_at) < tracking_cutoff
        )
        active_resources = tuple(
            video for video in ordered_resources if as_utc(video.published_at) >= tracking_cutoff
        )
        prepared = tuple(self._prepare_video(video) for video in active_resources)

        counts = self._empty_counts()
        counts["videos"] = len(ordered_resources)
        video_results_by_id: dict[str, dict[str, Any]] = {
            video.video_id: {
                "video_id": video.video_id,
                "status": "skipped",
                "reason": "outside_tracking_window",
                "published_at": as_utc(video.published_at).isoformat(),
            }
            for video in expired
        }
        details: dict[str, Any] = {
            "requested_video_ids": list(requested_ids),
            "requested_video_count": len(requested_ids),
            "active_video_count": len(prepared),
            "expired_video_count": len(expired),
            "failed_video_count": 0,
            "duplicate_video_ids_removed": list(selection.duplicate_video_ids),
            "video_results": [],
        }
        if not prepared:
            details["video_results"] = [
                video_results_by_id[video_id] for video_id in requested_ids
            ]
            details["reason"] = "all_configured_videos_outside_tracking_window"
            if len(expired) == 1:
                details["video_id"] = expired[0].video_id
            return counts, details

        snapshot_archive = self.archive.ensure_capacity(
            module_id=MODULE_ID,
            table_id=self.config.snapshot_table.table_id,
        )
        comparison_archive = self.archive.ensure_capacity(
            module_id=MODULE_ID,
            table_id=self.config.comparison_table.table_id,
        )
        contexts = self._store_batch_context(
            channel=channel,
            prepared=prepared,
            observed_at=observed_at,
        )
        for item in prepared:
            item_counts, item_details = self._sync_prepared_video(
                item,
                contexts[item.resource.video_id],
                observed_at=observed_at,
                snapshot_archive=snapshot_archive,
                comparison_archive=comparison_archive,
            )
            for key in (
                "snapshots",
                "main_records",
                "snapshot_records",
                "comparison_records",
                "feishu_writes",
            ):
                counts[key] += item_counts[key]
            video_results_by_id[item.resource.video_id] = item_details

        video_results = [video_results_by_id[video_id] for video_id in requested_ids]
        details["video_results"] = video_results

        if len(prepared) == 1 and not expired:
            details.update(
                {
                    key: value
                    for key, value in video_results[0].items()
                    if key not in {"status"}
                }
            )
        return counts, details

    def _validate_requested_resources(
        self,
        *,
        requested_ids: tuple[str, ...],
        resources: list[VideoResource],
        channel: ChannelResource,
    ) -> tuple[VideoResource, ...]:
        errors: list[str] = []
        if self.config.channel_id and channel.channel_id != self.config.channel_id:
            errors.append(
                f"配置频道 {self.config.channel_id} 与授权返回频道 {channel.channel_id} 不一致"
            )

        by_id: dict[str, VideoResource] = {}
        duplicate_response_ids: list[str] = []
        for video in resources:
            if video.video_id in by_id:
                duplicate_response_ids.append(video.video_id)
                continue
            by_id[video.video_id] = video
        if duplicate_response_ids:
            errors.append(
                "YouTube 重复返回视频："
                + "、".join(dict.fromkeys(duplicate_response_ids))
            )

        requested_set = set(requested_ids)
        unexpected_ids = sorted(set(by_id) - requested_set)
        if unexpected_ids:
            errors.append("YouTube 返回了未请求的视频：" + "、".join(unexpected_ids))
        missing_ids = [video_id for video_id in requested_ids if video_id not in by_id]
        if missing_ids:
            errors.append(
                "未读取到指定视频（可能不存在、已删除、私密或当前账号无权访问）："
                + "、".join(missing_ids)
            )
        wrong_channel = [
            video.video_id
            for video in by_id.values()
            if video.video_id in requested_set and video.channel_id != channel.channel_id
        ]
        if wrong_channel:
            errors.append(
                f"以下视频不属于当前频道 {channel.channel_id}：" + "、".join(wrong_channel)
            )
        if errors:
            raise ConfigurationError("手动追踪视频批量检查未通过：" + "；".join(errors))
        return tuple(by_id[video_id] for video_id in requested_ids)

    def _prepare_video(self, video: VideoResource) -> PreparedVideo:
        duration_seconds = parse_iso_duration_seconds(video.duration)
        extraction = self.dynamic_plan.extractor.extract(video.raw) if self.dynamic_plan else None
        return PreparedVideo(
            resource=video,
            duration_seconds=duration_seconds,
            video_type=infer_video_type(duration_seconds),
            extraction=extraction,
        )

    def _store_batch_context(
        self,
        *,
        channel: ChannelResource,
        prepared: tuple[PreparedVideo, ...],
        observed_at: datetime,
    ) -> dict[str, StoredVideoContext]:
        with self.storage.transaction() as repos:
            repos.channels.upsert(
                channel.channel_id,
                title=channel.title,
                uploads_playlist_id=channel.uploads_playlist_id,
                timezone=self.config.timezone,
                raw_json=channel.raw,
            )
            partial: dict[str, tuple[VideoSnapshot | None, datetime]] = {}
            for item in prepared:
                video = item.resource
                previous = repos.videos.latest_snapshot_before(video.video_id, observed_at)
                stored_video = repos.videos.upsert(
                    {
                        "id": video.video_id,
                        "channel_id": video.channel_id,
                        "title": video.title,
                        "published_at": video.published_at,
                        "duration_seconds": item.duration_seconds,
                        "privacy_status": video.privacy_status,
                        "video_type": item.video_type,
                        "raw_json": video.raw,
                    }
                )
                partial[video.video_id] = (previous, stored_video.first_seen_at)

            contexts = {
                item.resource.video_id: StoredVideoContext(
                    previous=partial[item.resource.video_id][0],
                    tracking_started_at=partial[item.resource.video_id][1],
                    comparison_candidates=repos.videos.list_comparison_snapshots(
                        channel_id=channel.channel_id,
                        exclude_video_id=item.resource.video_id,
                        video_type=item.video_type,
                    ),
                )
                for item in prepared
            }
            for item in prepared:
                video = item.resource
                repos.videos.add_snapshot(
                    video_id=video.video_id,
                    observed_at=observed_at,
                    view_count=video.view_count,
                    like_count=video.like_count,
                    comment_count=video.comment_count,
                    raw_json=video.raw,
                )
        return contexts

    def _sync_prepared_video(
        self,
        prepared: PreparedVideo,
        context: StoredVideoContext,
        *,
        observed_at: datetime,
        snapshot_archive: ArchiveDecision,
        comparison_archive: ArchiveDecision,
    ) -> tuple[dict[str, int], dict[str, Any]]:
        video = prepared.resource
        extraction = prepared.extraction
        values: dict[str, object] = dict(extraction.values) if extraction else {}
        values.update(self._standard_values(
            video,
            context.previous,
            observed_at,
            tracking_started_at=context.tracking_started_at,
            duration_seconds=prepared.duration_seconds,
            video_type=prepared.video_type,
        ))
        analytics_values, analytics_details = self._analytics_values(
            video,
            observed_at=observed_at,
        )
        values.update(analytics_values)
        reporting_values, reporting_details = self._reporting_values(
            video,
            observed_at=observed_at,
        )
        values.update(reporting_values)
        actions: dict[str, Any] = {}
        write_count = 0

        main_sync = self._sync(
            table=self.config.main_table,
            entity_type="latest_video_main",
            entity_key=video.video_id,
            values=values,
        )
        actions["main"] = main_sync.action
        write_count += int(main_sync.action != "unchanged")

        if snapshot_archive.status == "archive_required":
            actions["snapshot"] = "skipped_archive_required"
        else:
            snapshot_sync = self._sync(
                table=self.config.snapshot_table,
                entity_type="latest_video_snapshot",
                entity_key=str(values["MODULE_UNIQUE_KEY"]),
                values=values,
            )
            actions["snapshot"] = snapshot_sync.action
            write_count += int(snapshot_sync.action != "unchanged")

        comparison_actions: dict[str, str] = {}
        samples = self._select_comparison_samples(
            context.comparison_candidates,
            target_age_minutes=int(str(values["VIDEO_AGE_BUCKET_MINUTES"])),
        )
        if comparison_archive.status == "archive_required":
            comparison_actions["all"] = "skipped_archive_required"
        else:
            for label, row_values in self._comparison_rows(values, samples).items():
                sync = self._sync(
                    table=self.config.comparison_table,
                    entity_type="latest_video_comparison",
                    entity_key=str(row_values["COMPARISON_RECORD_ID"]),
                    values=row_values,
                )
                comparison_actions[label] = sync.action
                write_count += int(sync.action != "unchanged")
        actions["comparison"] = comparison_actions

        counts = {
            "videos": 1,
            "snapshots": 1,
            "main_records": int(main_sync.action != "unchanged"),
            "snapshot_records": int(
                actions["snapshot"] not in {"unchanged", "skipped_archive_required"}
            ),
            "comparison_records": sum(
                int(action not in {"unchanged", "skipped_archive_required"})
                for action in comparison_actions.values()
            ),
            "feishu_writes": write_count,
        }
        return counts, {
            "status": "synced",
            "video_id": video.video_id,
            "video_type": prepared.video_type,
            "sync_actions": actions,
            "comparison_sample_count": len(samples),
            "snapshot_archive_status": snapshot_archive.status,
            "snapshot_archive_batch_id": snapshot_archive.batch_id,
            "comparison_archive_status": comparison_archive.status,
            "comparison_archive_batch_id": comparison_archive.batch_id,
            "missing_dynamic_api_fields": list(extraction.missing_field_ids) if extraction else [],
            "null_dynamic_api_fields": list(extraction.null_field_ids) if extraction else [],
            "analytics": analytics_details,
            "reporting": reporting_details,
        }

    def _analytics_values(
        self,
        video: VideoResource,
        *,
        observed_at: datetime,
    ) -> tuple[dict[str, object], dict[str, Any]]:
        """按独立周期刷新后台分析数据，其余小时级任务复用成功缓存。"""

        if self.analytics is None:
            return {
                **{field_id: None for field_id in ANALYTICS_TIME_FIELD_IDS},
                "ANALYTICS_FETCHED_AT": None,
                "ANALYTICS_DATA_THROUGH_DATE": None,
            }, {"status": "disabled", "api_requests": 0}

        cached = self.analytics.latest_cached(video.video_id)
        missing_cached_field_ids = (
            cached.missing_metric_field_ids(self.analytics.field_ids)
            if cached is not None
            else ()
        )
        refresh_after = timedelta(hours=self.config.analytics_interval_hours)
        if (
            cached is not None
            and not missing_cached_field_ids
            and as_utc(observed_at) - cached.fetched_at < refresh_after
        ):
            return cached.as_standard_values(), {
                "status": "cache_hit",
                "api_requests": 0,
                "fetched_at": cached.fetched_at.isoformat(),
                "data_through_date": (
                    cached.data_through_date.isoformat()
                    if cached.data_through_date is not None
                    else None
                ),
            }

        try:
            result = self.analytics.collect_video(
                video_id=video.video_id,
                published_at=video.published_at,
                observed_at=observed_at,
            )
        except (AuthenticationError, ExternalServiceError) as exc:
            if cached is not None:
                return cached.as_standard_values(
                    required_metric_field_ids=self.analytics.field_ids
                ), {
                    "status": "stale_cache",
                    "api_requests": 0,
                    "fetched_at": cached.fetched_at.isoformat(),
                    "data_through_date": (
                        cached.data_through_date.isoformat()
                        if cached.data_through_date is not None
                        else None
                    ),
                    "missing_cached_metric_field_ids": list(missing_cached_field_ids),
                    "refresh_error": str(exc),
                }
            unavailable: dict[str, object] = {
                field_id: None for field_id in self.analytics.field_ids
            }
            unavailable.update(
                {
                    "ANALYTICS_FETCHED_AT": None,
                    "ANALYTICS_DATA_THROUGH_DATE": None,
                    **{field_id: None for field_id in ANALYTICS_TIME_FIELD_IDS},
                }
            )
            return unavailable, {
                "status": "unavailable",
                "api_requests": 0,
                "refresh_error": str(exc),
            }

        return result.as_standard_values(), {
            "status": "refreshed",
            "api_requests": result.api_request_count,
            "fetched_at": result.fetched_at.isoformat(),
            "data_through_date": (
                result.data_through_date.isoformat()
                if result.data_through_date is not None
                else None
            ),
            "empty": result.empty,
            "cache_upgrade_field_ids": list(missing_cached_field_ids),
        }

    def _reporting_values(
        self,
        video: VideoResource,
        *,
        observed_at: datetime,
    ) -> tuple[dict[str, object], dict[str, Any]]:
        """按日报周期检查 Reach 报表，其余小时级任务复用最近检查结果。"""

        if self.reporting is None:
            return {
                **{field_id: None for field_id in REPORTING_TIME_FIELD_IDS},
                "REPORTING_FETCHED_AT": None,
                "REPORTING_DATA_THROUGH_DATE": None,
            }, {"status": "disabled", "api_requests": 0}

        cached = self.reporting.latest_cached(video.video_id)
        refresh_after = timedelta(hours=self.config.reporting_interval_hours)
        if cached is not None and as_utc(observed_at) - cached.checked_at < refresh_after:
            return cached.as_standard_values(), {
                "status": "cache_hit",
                "source_status": cached.status,
                "api_requests": 0,
                "checked_at": cached.checked_at.isoformat(),
                "data_fetched_at": (
                    cached.data_fetched_at.isoformat()
                    if cached.data_fetched_at is not None
                    else None
                ),
                "data_through_date": (
                    cached.data_through_date.isoformat()
                    if cached.data_through_date is not None
                    else None
                ),
            }

        try:
            result = self.reporting.collect_video(
                video_id=video.video_id,
                published_at=video.published_at,
                observed_at=observed_at,
            )
        except (AuthenticationError, ExternalServiceError) as exc:
            if cached is not None:
                return cached.as_standard_values(), {
                    "status": "stale_cache",
                    "source_status": cached.status,
                    "api_requests": 0,
                    "checked_at": cached.checked_at.isoformat(),
                    "refresh_error": str(exc),
                }
            unavailable: dict[str, object] = {
                field_id: None for field_id in self.reporting.field_ids
            }
            unavailable.update(
                {
                    "REPORTING_FETCHED_AT": None,
                    "REPORTING_DATA_THROUGH_DATE": None,
                    **{field_id: None for field_id in REPORTING_TIME_FIELD_IDS},
                }
            )
            return unavailable, {
                "status": "unavailable",
                "api_requests": 0,
                "refresh_error": str(exc),
            }

        return result.as_standard_values(), {
            "status": result.status,
            "api_requests": result.api_request_count,
            "downloaded_reports": result.downloaded_report_count,
            "checked_at": result.checked_at.isoformat(),
            "data_fetched_at": (
                result.data_fetched_at.isoformat()
                if result.data_fetched_at is not None
                else None
            ),
            "data_through_date": (
                result.data_through_date.isoformat()
                if result.data_through_date is not None
                else None
            ),
        }

    def _standard_values(
        self,
        video: VideoResource,
        previous: VideoSnapshot | None,
        observed_at: datetime,
        *,
        tracking_started_at: datetime,
        duration_seconds: int | None,
        video_type: str,
    ) -> dict[str, Any]:
        bucket_seconds = self.config.interval_minutes * 60
        bucket_timestamp = int(as_utc(observed_at).timestamp()) // bucket_seconds * bucket_seconds
        bucket = datetime.fromtimestamp(bucket_timestamp, tz=as_utc(observed_at).tzinfo)
        age_minutes = max(
            0,
            int((as_utc(observed_at) - as_utc(video.published_at)).total_seconds() // 60),
        )
        age_bucket = age_minutes // self.config.interval_minutes * self.config.interval_minutes
        return {
            **data_api_time_values(observed_at),
            "MODULE_UNIQUE_KEY": f"{video.video_id}_{bucket.isoformat()}",
            "CONTENT_BATCH_ID": build_content_batch_id(video, self.config.timezone),
            "SYSTEM_OBSERVED_AT": to_epoch_milliseconds(observed_at),
            "SYSTEM_LAST_SYNCED_AT": to_epoch_milliseconds(observed_at),
            "SYSTEM_CALCULATED_AT": to_epoch_milliseconds(observed_at),
            "VIDEO_ID": video.video_id,
            "VIDEO_CHANNEL_ID": video.channel_id,
            "VIDEO_TITLE": video.title,
            "VIDEO_PUBLISHED_AT": to_epoch_milliseconds(video.published_at),
            "VIDEO_DURATION": duration_seconds,
            "VIDEO_PRIVACY_STATUS": video.privacy_status,
            "VIDEO_TYPE": video_type,
            "VIDEO_AGE_MINUTES": age_minutes,
            "VIDEO_AGE_BUCKET_MINUTES": age_bucket,
            "VIDEO_VIEWS_PUBLIC": video.view_count,
            "VIDEO_VIEW_DELTA": delta(video.view_count, previous.view_count if previous else None),
            "VIDEO_VIEW_RATE_PER_HOUR": view_rate_per_hour(
                video.view_count,
                video.published_at,
                observed_at,
                previous,
            ),
            "VIDEO_LIKES_PUBLIC": video.like_count,
            "VIDEO_LIKE_DELTA": delta(video.like_count, previous.like_count if previous else None),
            "VIDEO_COMMENTS_PUBLIC": video.comment_count,
            "VIDEO_COMMENT_DELTA": delta(
                video.comment_count,
                previous.comment_count if previous else None,
            ),
            "VIDEO_URL": f"https://www.youtube.com/watch?v={video.video_id}",
            "TRACKING_STATUS": "追踪中",
            "CURRENT_TRACKING_VIDEO": "是",
            "TRACKING_STARTED_AT": to_epoch_milliseconds(tracking_started_at),
            "TRACKING_ENDS_AT": to_epoch_milliseconds(
                as_utc(video.published_at) + timedelta(days=self.config.tracking_days)
            ),
            "CURRENT_COMPARISON_BATCH": "是",
        }

    def _select_comparison_samples(
        self,
        candidates: list[ComparableSnapshot],
        *,
        target_age_minutes: int,
    ) -> list[ComparableSnapshot]:
        tolerance = max(30, self.config.interval_minutes)
        closest: dict[str, tuple[int, ComparableSnapshot]] = {}
        for item in candidates:
            age_minutes = max(
                0,
                int((as_utc(item.observed_at) - as_utc(item.published_at)).total_seconds() // 60),
            )
            distance = abs(age_minutes - target_age_minutes)
            if distance > tolerance:
                continue
            previous = closest.get(item.video_id)
            if previous is None or distance < previous[0]:
                closest[item.video_id] = (distance, item)
        ordered = sorted(
            (item for _, item in closest.values()),
            key=lambda item: as_utc(item.published_at),
            reverse=True,
        )
        return ordered[:10]

    def _comparison_rows(
        self,
        current_values: dict[str, Any],
        samples: list[ComparableSnapshot],
    ) -> dict[str, dict[str, Any]]:
        video_id = str(current_values["VIDEO_ID"])
        age_bucket = int(current_values["VIDEO_AGE_BUCKET_MINUTES"])
        base_id = f"{video_id}_{age_bucket}"
        sample_views = [item.view_count for item in samples if item.view_count is not None]
        sample_rates = [
            historical_view_rate(item) for item in samples if item.view_count is not None
        ]
        current = dict(current_values)
        current.update(
            {
                "COMPARISON_RECORD_ID": f"{base_id}_current",
                "CURRENT_COMPARISON_BATCH": "是",
                "COMPARISON_OBJECT": "当前视频",
                "COMPARISON_SAMPLE_COUNT": 1,
            }
        )
        average = dict(current_values)
        average.update(
            {
                "COMPARISON_RECORD_ID": f"{base_id}_recent10_average",
                "CURRENT_COMPARISON_BATCH": "否",
                "COMPARISON_OBJECT": "最近10条平均",
                "COMPARISON_SAMPLE_COUNT": len(samples),
                "VIDEO_VIEWS_PUBLIC": aggregate_or_none(sample_views, mean),
                "VIDEO_VIEW_RATE_PER_HOUR": aggregate_or_none(sample_rates, mean),
            }
        )
        midpoint = dict(current_values)
        midpoint.update(
            {
                "COMPARISON_RECORD_ID": f"{base_id}_recent10_median",
                "CURRENT_COMPARISON_BATCH": "否",
                "COMPARISON_OBJECT": "最近10条中位数",
                "COMPARISON_SAMPLE_COUNT": len(samples),
                "VIDEO_VIEWS_PUBLIC": aggregate_or_none(sample_views, median),
                "VIDEO_VIEW_RATE_PER_HOUR": aggregate_or_none(sample_rates, median),
            }
        )
        return {"current": current, "recent10_average": average, "recent10_median": midpoint}

    def _sync(
        self,
        *,
        table: TableSyncConfig,
        entity_type: str,
        entity_key: str,
        values: dict[str, Any],
    ) -> SyncResult:
        if self.dynamic_plan and table.table_name:
            fields = self.dynamic_plan.adapt_table_record(table.table_name, values)
        else:
            fields = {
                column: values[field_id]
                for field_id, column in table.field_mapping.items()
                if field_id in values and values[field_id] is not None and column
            }
        return self.records.upsert_entity(
            table_id=table.table_id,
            entity_type=entity_type,
            entity_key=entity_key,
            fields=fields,
        )

    @staticmethod
    def _empty_counts() -> dict[str, int]:
        return {
            "videos": 0,
            "snapshots": 0,
            "main_records": 0,
            "snapshot_records": 0,
            "comparison_records": 0,
            "feishu_writes": 0,
        }


def merge_field_mapping(
    overrides: dict[str, str] | None = None,
    *,
    use_defaults: bool = True,
    defaults: dict[str, str] | None = None,
) -> dict[str, str]:
    result = dict(defaults or DEFAULT_FIELD_MAPPING) if use_defaults else {}
    result.update(overrides or {})
    return result


def parse_iso_duration_seconds(value: str | None) -> int | None:
    if not value:
        return None
    match = ISO_DURATION.fullmatch(value)
    if match is None:
        raise ConfigurationError(f"无法解析 YouTube ISO 8601 时长：{value}")
    parts = {key: int(raw or 0) for key, raw in match.groupdict().items()}
    return (
        parts["days"] * 86400
        + parts["hours"] * 3600
        + parts["minutes"] * 60
        + parts["seconds"]
    )


def infer_video_type(duration_seconds: int | None) -> str:
    """Data API 不提供画面比例；V1 按 YouTube 当前 3 分钟时长上限推定 Shorts。"""
    return "Shorts" if duration_seconds is not None and duration_seconds <= 180 else "长视频"


def build_content_batch_id(video: VideoResource, timezone: str) -> str:
    published = as_utc(video.published_at).astimezone(ZoneInfo(timezone))
    return f"{published:%Y%m%d}-{video.video_id}"


def delta(current: int | None, previous: int | None) -> int:
    if current is None or previous is None:
        return 0
    return current - previous


def view_rate_per_hour(
    current: int | None,
    published_at: datetime,
    observed_at: datetime,
    previous: VideoSnapshot | None,
) -> float | None:
    if current is None:
        return None
    if previous is not None and previous.view_count is not None:
        elapsed_hours = (as_utc(observed_at) - as_utc(previous.observed_at)).total_seconds() / 3600
        if elapsed_hours > 0:
            return round((current - previous.view_count) / elapsed_hours, 2)
    age_hours = (as_utc(observed_at) - as_utc(published_at)).total_seconds() / 3600
    if age_hours <= 0:
        return 0.0
    return round(current / age_hours, 2)


def historical_view_rate(item: ComparableSnapshot) -> float:
    if item.view_count is None:
        return 0.0
    age_hours = (as_utc(item.observed_at) - as_utc(item.published_at)).total_seconds() / 3600
    if age_hours <= 0:
        return 0.0
    return round(item.view_count / age_hours, 2)


def aggregate_or_none(values: list[int] | list[float], reducer: Any) -> float | None:
    if not values:
        return None
    return round(float(reducer(values)), 2)


def to_epoch_milliseconds(value: datetime) -> int:
    return int(as_utc(value).timestamp() * 1000)
