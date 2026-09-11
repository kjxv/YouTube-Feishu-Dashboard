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
from youtube_feishu_dashboard.db.models import Video, VideoSnapshot
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
from yfd_latest_video_tracker.cadence import (
    TrackingCadencePolicy,
    VideoCadenceDecision,
    VideoCadenceState,
)
from yfd_latest_video_tracker.manifest import DEFAULT_FIELD_MAPPING, MILESTONE_HOURS, MODULE_ID
from yfd_latest_video_tracker.reporting import VideoReachReportingCollector

ISO_DURATION = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+)S)?)?$"
)

_REMOTE_KEY_FIELD_IDS = {
    "latest_video_main": "VIDEO_ID",
    "latest_video_snapshot": "MODULE_UNIQUE_KEY",
    "latest_video_comparison": "COMPARISON_RECORD_ID",
}


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
    reporting_interval_hours: int = 6
    hourly_tracking_hours: int = 72
    daily_collection_hour: int = 8
    milestone_tolerance_minutes: int = 90


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

    def track(
        self,
        observed_at: datetime,
        *,
        video_ids: tuple[str, ...] | None = None,
        enforce_tracking_window: bool = True,
        force_analytics_ids: frozenset[str] | None = None,
        force_reporting_ids: frozenset[str] | None = None,
        current_tracking_video_id: str | None = None,
    ) -> tuple[dict[str, int], dict[str, Any]]:
        raw_video_ids = video_ids if video_ids is not None else self.config.tracking_video_ids
        selection = parse_tracking_video_ids(
            "\n".join(raw_video_ids),
            max_video_ids=max(50, len(raw_video_ids)),
        )
        requested_ids = selection.video_ids
        analytics_force = force_analytics_ids or frozenset()
        reporting_force = force_reporting_ids or frozenset()
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
        expired = (
            tuple(
                video
                for video in ordered_resources
                if as_utc(video.published_at) < tracking_cutoff
            )
            if enforce_tracking_window
            else ()
        )
        active_resources = (
            tuple(
                video
                for video in ordered_resources
                if as_utc(video.published_at) >= tracking_cutoff
            )
            if enforce_tracking_window
            else ordered_resources
        )
        prepared = tuple(self._prepare_video(video) for video in active_resources)
        if current_tracking_video_id is None and prepared:
            current_tracking_video_id = max(
                prepared,
                key=lambda item: as_utc(item.resource.published_at),
            ).resource.video_id

        if expired:
            self._store_video_metadata(channel=channel, resources=expired)

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
                force_analytics=item.resource.video_id in analytics_force,
                force_reporting=item.resource.video_id in reporting_force,
                current_tracking_video_id=current_tracking_video_id,
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

    def track_automatic(
        self,
        observed_at: datetime,
    ) -> tuple[dict[str, int], dict[str, Any]]:
        """自动发现频道最近发布的长视频，并强制刷新当前追踪集合。"""
        requested_ids, current_video_id, discovery = self._automatic_selection(observed_at)
        lifecycle_before = self._reconcile_main_lifecycle(
            observed_at,
            current_video_id=current_video_id,
        )
        if not requested_ids:
            return self._empty_counts(), {
                "mode": "automatic_discovery_force",
                "discovery": discovery,
                "lifecycle": lifecycle_before,
                "reason": "no_public_long_video_in_tracking_window",
            }
        counts, details = self.track(
            observed_at,
            video_ids=requested_ids,
            current_tracking_video_id=current_video_id,
        )
        lifecycle_after = self._reconcile_main_lifecycle(
            observed_at,
            current_video_id=current_video_id,
        )
        details.update(
            {
                "mode": "automatic_discovery_force",
                "discovery": discovery,
                "lifecycle": self._merge_lifecycle_results(
                    lifecycle_before,
                    lifecycle_after,
                ),
            }
        )
        return counts, details

    def track_scheduled(self, observed_at: datetime) -> tuple[dict[str, int], dict[str, Any]]:
        """按每个视频、每个数据源的节奏运行一次整点检查。"""
        requested_ids, current_video_id, discovery = self._automatic_selection(observed_at)
        lifecycle_before = self._reconcile_main_lifecycle(
            observed_at,
            current_video_id=current_video_id,
        )
        if not requested_ids:
            return self._empty_counts(), {
                "mode": "scheduled_cadence",
                "requested_video_ids": [],
                "new_video_ids": [],
                "decisions": {},
                "runs": [],
                "discovery": discovery,
                "lifecycle": lifecycle_before,
                "reason": "no_public_long_video_in_tracking_window",
            }
        policy = TrackingCadencePolicy(
            timezone=self.config.timezone,
            hourly_tracking_hours=self.config.hourly_tracking_hours,
            daily_collection_hour=self.config.daily_collection_hour,
            tracking_days=self.config.tracking_days,
            reporting_interval_hours=self.config.reporting_interval_hours,
        )
        decisions: dict[str, VideoCadenceDecision] = {}
        unknown_ids: list[str] = []
        stored_by_id: dict[str, Video] = {}
        with self.storage.transaction() as repos:
            for video_id in requested_ids:
                stored = repos.videos.get(video_id)
                if stored is None:
                    unknown_ids.append(video_id)
                    continue
                latest_data = repos.videos.latest_snapshot(video_id)
                latest_analytics = repos.video_analytics.latest(video_id)
                latest_reporting = repos.video_reporting.latest(video_id)
                stored_by_id[video_id] = stored
                raw_decision = policy.decide(
                    VideoCadenceState(
                        published_at=stored.published_at,
                        last_data_at=latest_data.observed_at if latest_data else None,
                        last_analytics_at=(
                            latest_analytics.fetched_at if latest_analytics else None
                        ),
                        last_reporting_at=(
                            latest_reporting.checked_at if latest_reporting else None
                        ),
                    ),
                    observed_at,
                )
                decisions[video_id] = VideoCadenceDecision(
                    data_api_due=raw_decision.data_api_due,
                    analytics_api_due=(
                        raw_decision.analytics_api_due and self.analytics is not None
                    ),
                    reporting_api_due=(
                        raw_decision.reporting_api_due and self.reporting is not None
                    ),
                    data_reason=raw_decision.data_reason,
                )

        counts = self._empty_counts()
        run_details: list[dict[str, Any]] = []

        if unknown_ids:
            unknown_counts, unknown_details = self.track(
                observed_at,
                video_ids=tuple(unknown_ids),
                enforce_tracking_window=True,
                current_tracking_video_id=current_video_id,
            )
            self._add_counts(counts, unknown_counts)
            run_details.append({"kind": "new_video_discovery", **unknown_details})

        data_ids = tuple(
            video_id
            for video_id, decision in decisions.items()
            if decision.data_api_due
        )
        if data_ids:
            data_counts, data_details = self.track(
                observed_at,
                video_ids=data_ids,
                enforce_tracking_window=False,
                force_analytics_ids=frozenset(
                    video_id
                    for video_id in data_ids
                    if decisions[video_id].analytics_api_due
                ),
                force_reporting_ids=frozenset(
                    video_id
                    for video_id in data_ids
                    if decisions[video_id].reporting_api_due
                ),
                current_tracking_video_id=current_video_id,
            )
            self._add_counts(counts, data_counts)
            run_details.append({"kind": "data_api", **data_details})

        supplemental_ids = tuple(
            video_id
            for video_id, decision in decisions.items()
            if not decision.data_api_due
            and (decision.analytics_api_due or decision.reporting_api_due)
        )
        if supplemental_ids:
            supplemental_counts, supplemental_details = self._refresh_supplemental(
                observed_at,
                stored_by_id=stored_by_id,
                decisions=decisions,
                video_ids=supplemental_ids,
            )
            self._add_counts(counts, supplemental_counts)
            run_details.append({"kind": "supplemental_api", **supplemental_details})

        lifecycle_after = self._reconcile_main_lifecycle(
            observed_at,
            current_video_id=current_video_id,
        )
        return counts, {
            "mode": "scheduled_cadence",
            "requested_video_ids": list(requested_ids),
            "new_video_ids": unknown_ids,
            "decisions": {
                video_id: {
                    "data_api_due": decision.data_api_due,
                    "analytics_api_due": decision.analytics_api_due,
                    "reporting_api_due": decision.reporting_api_due,
                    "data_reason": decision.data_reason,
                }
                for video_id, decision in decisions.items()
            },
            "runs": run_details,
            "discovery": discovery,
            "lifecycle": self._merge_lifecycle_results(
                lifecycle_before,
                lifecycle_after,
            ),
        }

    def _automatic_selection(
        self,
        observed_at: datetime,
    ) -> tuple[tuple[str, ...], str | None, dict[str, Any]]:
        """从频道上传列表发现追踪窗口内已公开的长视频。"""
        current = as_utc(observed_at)
        cutoff = current - timedelta(days=self.config.tracking_days)
        channel = self.youtube.get_channel(self.config.channel_id)
        upload_ids = self.youtube.list_upload_video_ids(
            channel.uploads_playlist_id,
            published_after=cutoff,
        )
        discovery_parts = tuple(
            dict.fromkeys(
                (*self.request_plan.data_api_parts, "snippet", "contentDetails", "status")
            )
        )
        resources = self.youtube.list_videos(upload_ids, parts=discovery_parts)
        excluded: dict[str, str] = {}
        candidates: list[VideoResource] = []
        for video in resources:
            if video.channel_id != channel.channel_id:
                excluded[video.video_id] = "wrong_channel"
                continue
            if video.privacy_status != "public":
                excluded[video.video_id] = "not_public"
                continue
            published = as_utc(video.published_at)
            if published > current:
                excluded[video.video_id] = "not_published"
                continue
            if published < cutoff:
                excluded[video.video_id] = "outside_tracking_window"
                continue
            duration_seconds = parse_iso_duration_seconds(video.duration)
            if infer_video_type(duration_seconds) != "长视频":
                excluded[video.video_id] = "shorts"
                continue
            candidates.append(video)

        candidates.sort(key=lambda item: as_utc(item.published_at), reverse=True)
        discovered_ids = tuple(item.video_id for item in candidates)
        requested_ids = tuple(
            dict.fromkeys((*discovered_ids, *self.config.tracking_video_ids))
        )
        current_video_id = discovered_ids[0] if discovered_ids else None
        return requested_ids, current_video_id, {
            "mode": "channel_uploads_automatic",
            "tracking_days": self.config.tracking_days,
            "upload_video_count": len(upload_ids),
            "public_long_video_ids": list(discovered_ids),
            "manual_fallback_video_ids": list(self.config.tracking_video_ids),
            "selected_video_ids": list(requested_ids),
            "current_video_id": current_video_id,
            "excluded_video_reasons": excluded,
        }

    def _reconcile_main_lifecycle(
        self,
        observed_at: datetime,
        *,
        current_video_id: str | None,
    ) -> dict[str, Any]:
        """保证视频主表一视频一行，并维护当前视频及追踪状态。"""
        table = self.config.main_table
        video_id_column = table.field_mapping.get("VIDEO_ID")
        current_column = table.field_mapping.get("CURRENT_TRACKING_VIDEO")
        status_column = table.field_mapping.get("TRACKING_STATUS")
        started_column = table.field_mapping.get("TRACKING_STARTED_AT")
        ends_column = table.field_mapping.get("TRACKING_ENDS_AT")
        if not video_id_column:
            return {
                "status": "skipped",
                "reason": "main_video_id_mapping_missing",
                "duplicates_removed": 0,
                "records_updated": 0,
            }

        remote_records = self.records.gateway.list_records(
            self.records.app_token,
            table.table_id,
        )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for record in remote_records:
            video_id = _field_scalar(record.get("fields", {}).get(video_id_column))
            if video_id not in (None, ""):
                grouped.setdefault(str(video_id), []).append(record)

        with self.storage.transaction() as repos:
            bindings = {
                item.entity_key: item
                for item in repos.bindings.list_for_table(table.table_id)
                if item.entity_type == "latest_video_main"
            }

        canonical_by_video: dict[str, dict[str, Any]] = {}
        duplicate_record_ids: list[str] = []
        for video_id, records in grouped.items():
            bound = bindings.get(video_id)
            records_with_start = (
                [
                    item
                    for item in records
                    if started_column
                    and _as_datetime(item.get("fields", {}).get(started_column)) is not None
                ]
                if started_column
                else []
            )
            canonical = (
                min(
                    records_with_start,
                    key=lambda item: (
                        _as_datetime(item.get("fields", {}).get(started_column)),
                        _remote_record_order(item),
                    ),
                )
                if records_with_start
                else next(
                    (
                        item
                        for item in records
                        if bound is not None
                        and str(item.get("record_id")) == bound.record_id
                    ),
                    None,
                )
            )
            if canonical is None:
                canonical = min(records, key=_remote_record_order)
            canonical_id = str(canonical.get("record_id") or "")
            if not canonical_id:
                raise ExternalServiceError(
                    f"视频主表中的视频 {video_id} 记录缺少 record_id。"
                )
            canonical_by_video[video_id] = canonical
            duplicate_record_ids.extend(
                str(item["record_id"])
                for item in records
                if str(item.get("record_id") or "") != canonical_id
                and item.get("record_id")
            )
            if bound is None or bound.record_id != canonical_id:
                with self.storage.transaction() as repos:
                    repos.bindings.upsert(
                        table_id=table.table_id,
                        entity_type="latest_video_main",
                        entity_key=video_id,
                        record_id=canonical_id,
                        payload_hash=None,
                    )

        updates: list[dict[str, Any]] = []
        current_utc = as_utc(observed_at)
        with self.storage.transaction() as repos:
            stored_by_video = {
                video_id: repos.videos.get(video_id)
                for video_id in canonical_by_video
            }
        for video_id, record in canonical_by_video.items():
            existing_fields = record.get("fields", {})
            desired: dict[str, Any] = {}
            if current_column:
                desired[current_column] = "是" if video_id == current_video_id else "否"
            if status_column:
                stored = stored_by_video.get(video_id)
                end_at = (
                    as_utc(stored.published_at) + timedelta(days=self.config.tracking_days)
                    if stored is not None
                    else _as_datetime(existing_fields.get(ends_column)) if ends_column else None
                )
                if end_at is not None:
                    desired[status_column] = (
                        "追踪结束" if current_utc > end_at else "追踪中"
                    )
            changed = {
                column: value
                for column, value in desired.items()
                if _field_scalar(existing_fields.get(column)) != value
            }
            if changed:
                updates.append(
                    {"record_id": str(record["record_id"]), "fields": changed}
                )

        if updates:
            self.records.gateway.batch_update_records(
                self.records.app_token,
                table.table_id,
                updates,
            )
        if duplicate_record_ids:
            self.records.gateway.batch_delete_records(
                self.records.app_token,
                table.table_id,
                duplicate_record_ids,
            )
        return {
            "status": "reconciled",
            "current_video_id": current_video_id,
            "main_video_count": len(canonical_by_video),
            "duplicates_removed": len(duplicate_record_ids),
            "records_updated": len(updates),
        }

    @staticmethod
    def _merge_lifecycle_results(
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            **after,
            "duplicates_removed": int(before.get("duplicates_removed", 0))
            + int(after.get("duplicates_removed", 0)),
            "records_updated": int(before.get("records_updated", 0))
            + int(after.get("records_updated", 0)),
        }

    def _store_video_metadata(
        self,
        *,
        channel: ChannelResource,
        resources: tuple[VideoResource, ...],
    ) -> None:
        prepared = tuple(self._prepare_video(video) for video in resources)
        with self.storage.transaction() as repos:
            repos.channels.upsert(
                channel.channel_id,
                title=channel.title,
                uploads_playlist_id=channel.uploads_playlist_id,
                timezone=self.config.timezone,
                raw_json=channel.raw,
            )
            for item in prepared:
                video = item.resource
                repos.videos.upsert(
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
        force_analytics: bool = False,
        force_reporting: bool = False,
        current_tracking_video_id: str | None = None,
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
            current_tracking_video=(video.video_id == current_tracking_video_id),
        ))
        values.update(self._milestone_values(video, observed_at))
        analytics_values, analytics_details = self._analytics_values(
            video,
            observed_at=observed_at,
            force_refresh=force_analytics,
        )
        values.update(analytics_values)
        reporting_values, reporting_details = self._reporting_values(
            video,
            observed_at=observed_at,
            force_refresh=force_reporting,
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
        force_refresh: bool = False,
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
            not force_refresh
            and cached is not None
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
        force_refresh: bool = False,
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
        if (
            not force_refresh
            and cached is not None
            and as_utc(observed_at) - cached.checked_at < refresh_after
        ):
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

    def _refresh_supplemental(
        self,
        observed_at: datetime,
        *,
        stored_by_id: dict[str, Video],
        decisions: dict[str, VideoCadenceDecision],
        video_ids: tuple[str, ...],
    ) -> tuple[dict[str, int], dict[str, Any]]:
        counts = self._empty_counts()
        results: list[dict[str, Any]] = []
        for video_id in video_ids:
            stored = stored_by_id[video_id]
            decision = decisions[video_id]
            resource = VideoResource(
                video_id=stored.id,
                channel_id=stored.channel_id,
                title=stored.title,
                published_at=stored.published_at,
                duration=None,
                privacy_status=stored.privacy_status,
                view_count=None,
                like_count=None,
                comment_count=None,
                raw=stored.raw_json or {},
            )
            values: dict[str, object] = {
                "VIDEO_ID": stored.id,
                "VIDEO_TITLE": stored.title,
            }
            analytics_details: dict[str, Any] = {"status": "not_due", "api_requests": 0}
            reporting_details: dict[str, Any] = {"status": "not_due", "api_requests": 0}
            if decision.analytics_api_due:
                analytics_values, analytics_details = self._analytics_values(
                    resource,
                    observed_at=observed_at,
                    force_refresh=True,
                )
                values.update(analytics_values)
            if decision.reporting_api_due:
                reporting_values, reporting_details = self._reporting_values(
                    resource,
                    observed_at=observed_at,
                    force_refresh=True,
                )
                values.update(reporting_values)

            fields = self._adapt_partial_main_record(values)
            action = "unchanged"
            if fields:
                sync = self.records.upsert_entity(
                    table_id=self.config.main_table.table_id,
                    entity_type="latest_video_main",
                    entity_key=stored.id,
                    fields=fields,
                    remote_key_field=self.config.main_table.field_mapping.get("VIDEO_ID"),
                )
                action = sync.action
                changed = int(sync.action != "unchanged")
                counts["main_records"] += changed
                counts["feishu_writes"] += changed
            counts["videos"] += 1
            results.append(
                {
                    "video_id": stored.id,
                    "main_action": action,
                    "analytics": analytics_details,
                    "reporting": reporting_details,
                }
            )
        return counts, {"video_results": results}

    def _adapt_partial_main_record(self, values: dict[str, object]) -> dict[str, Any]:
        table = self.config.main_table
        if self.dynamic_plan and table.table_name:
            return self.dynamic_plan.adapt_table_partial_record(table.table_name, values)
        return {
            column: values[field_id]
            for field_id, column in table.field_mapping.items()
            if field_id in values and values[field_id] is not None and column
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
        current_tracking_video: bool,
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
            "CURRENT_TRACKING_VIDEO": "是" if current_tracking_video else "否",
            "TRACKING_STARTED_AT": to_epoch_milliseconds(tracking_started_at),
            "TRACKING_ENDS_AT": to_epoch_milliseconds(
                as_utc(video.published_at) + timedelta(days=self.config.tracking_days)
            ),
            "CURRENT_COMPARISON_BATCH": "是",
        }

    def _milestone_values(
        self,
        video: VideoResource,
        observed_at: datetime,
    ) -> dict[str, Any]:
        """从真实 Data API 快照选取发布后各节点附近的累计播放量。"""

        current = as_utc(observed_at)
        published = as_utc(video.published_at)
        # 动态映射会严格确认每个已启用字段都由模块声明。节点尚未到达，或
        # 本地没有容差范围内的真实快照时，必须明确返回 None；适配器会跳过
        # 空值并保留飞书原值，而不能把“合法留空”误判成程序漏实现字段。
        result: dict[str, Any] = {
            field_id: None
            for hours in MILESTONE_HOURS
            for field_id in (
                f"VIDEO_VIEWS_AT_{hours}H",
                f"VIDEO_{hours}H_SAMPLE_AGE_MINUTES",
                f"VIDEO_{hours}H_SAMPLE_AT_BEIJING",
            )
        }
        with self.storage.transaction() as repos:
            for hours in MILESTONE_HOURS:
                target = published + timedelta(hours=hours)
                if current < target:
                    continue
                sample = repos.videos.nearest_snapshot_to(
                    video.video_id,
                    target,
                    tolerance_minutes=self.config.milestone_tolerance_minutes,
                )
                if sample is None or sample.view_count is None:
                    continue
                sample_at = as_utc(sample.observed_at)
                result[f"VIDEO_VIEWS_AT_{hours}H"] = sample.view_count
                result[f"VIDEO_{hours}H_SAMPLE_AGE_MINUTES"] = max(
                    0,
                    int((sample_at - published).total_seconds() // 60),
                )
                result[f"VIDEO_{hours}H_SAMPLE_AT_BEIJING"] = sample_at.astimezone(
                    ZoneInfo(self.config.timezone)
                ).isoformat(timespec="seconds")
        return result

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
            remote_key_field=table.field_mapping.get(
                _REMOTE_KEY_FIELD_IDS.get(entity_type, "")
            ),
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

    @staticmethod
    def _add_counts(target: dict[str, int], source: dict[str, int]) -> None:
        for key, value in source.items():
            target[key] = target.get(key, 0) + value


def _field_scalar(value: Any) -> Any:
    if isinstance(value, list):
        return _field_scalar(value[0]) if value else None
    if isinstance(value, dict):
        for key in ("text", "name", "value"):
            if key in value:
                return _field_scalar(value[key])
    return value


def _as_datetime(value: Any) -> datetime | None:
    scalar = _field_scalar(value)
    if scalar in (None, ""):
        return None
    if isinstance(scalar, (int, float)):
        timestamp = float(scalar)
        if abs(timestamp) > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=ZoneInfo("UTC"))
    text = str(scalar).strip()
    if text.isdigit():
        return _as_datetime(int(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
    return as_utc(parsed)


def _remote_record_order(record: dict[str, Any]) -> tuple[int, str]:
    raw_created = record.get("created_time")
    try:
        created = int(str(raw_created))
    except (TypeError, ValueError):
        created = 2**63 - 1
    return created, str(record.get("record_id") or "")


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
