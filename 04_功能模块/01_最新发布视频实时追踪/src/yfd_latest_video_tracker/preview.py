"""最新视频动态只读预览；读取配置与 YouTube，但不写业务数据库或飞书。"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any

from youtube_feishu_dashboard.api.feishu.field_types import FeishuValueAdapterRegistry
from youtube_feishu_dashboard.api.youtube.protocols import YouTubeDataGateway
from youtube_feishu_dashboard.api.youtube.schemas import VideoResource
from youtube_feishu_dashboard.catalog.field_catalog import ApiRequestPlan
from youtube_feishu_dashboard.core.errors import DashboardError
from youtube_feishu_dashboard.core.time import as_utc
from youtube_feishu_dashboard.services.dynamic_mapping import DynamicModulePlan
from youtube_feishu_dashboard.services.tracking_video_config import parse_tracking_video_ids

from yfd_latest_video_tracker.api_time_fields import data_api_time_values
from yfd_latest_video_tracker.manifest import MODULE_ID
from yfd_latest_video_tracker.service import parse_iso_duration_seconds


class LatestVideoPreviewService:
    """逐条读取手动指定的视频，返回脱敏后的标准字段和预计写入。"""

    def __init__(
        self,
        *,
        youtube: YouTubeDataGateway,
        request_plan: ApiRequestPlan,
        channel_id: str | None,
        tracking_days: int,
        tracking_video_ids: tuple[str, ...] = (),
        dynamic_plan: DynamicModulePlan | None = None,
    ) -> None:
        self.youtube = youtube
        self.request_plan = request_plan
        self.channel_id = channel_id
        self.tracking_days = tracking_days
        self.tracking_video_ids = tracking_video_ids
        self.dynamic_plan = dynamic_plan

    def preview(self, observed_at: datetime) -> dict[str, Any]:
        selection = parse_tracking_video_ids("\n".join(self.tracking_video_ids))
        requested_ids = selection.video_ids
        channel = self.youtube.get_channel(self.channel_id)
        resources = self.youtube.list_videos(
            requested_ids,
            parts=self.request_plan.data_api_parts,
        )
        base: dict[str, Any] = {
            "module_id": MODULE_ID,
            "mode": "youtube_manual_multi_video_read_only_preview",
            "external_requests_made": True,
            "external_writes_made": False,
            "local_business_database_writes_made": False,
            "local_token_may_refresh": True,
            "request_plan": asdict(self.request_plan),
            "channel": {
                "channel_id": channel.channel_id,
                "channel_title": channel.title,
            },
            "observed_at": observed_at,
            "requested_video_ids": list(requested_ids),
            "duplicate_video_ids_removed": list(selection.duplicate_video_ids),
        }
        by_id: dict[str, VideoResource] = {}
        duplicate_response_ids: list[str] = []
        for resource in resources:
            if resource.video_id in by_id:
                duplicate_response_ids.append(resource.video_id)
                continue
            by_id[resource.video_id] = resource
        requested_set = set(requested_ids)
        unexpected_response_ids = sorted(set(by_id) - requested_set)
        configured_channel_mismatch = bool(
            self.channel_id and self.channel_id != channel.channel_id
        )

        video_results: list[dict[str, Any]] = []
        for video_id in requested_ids:
            video = by_id.get(video_id)
            if configured_channel_mismatch:
                video_results.append(
                    self._error_result(
                        video_id,
                        reason="configured_channel_mismatch",
                        message=(
                            f"配置频道 {self.channel_id} 与授权返回频道 "
                            f"{channel.channel_id} 不一致。"
                        ),
                    )
                )
                continue
            if video is None:
                video_results.append(
                    self._error_result(
                        video_id,
                        reason="video_not_accessible",
                        message="视频不存在、已删除、私密或当前账号无权访问。",
                    )
                )
                continue
            if video.channel_id != channel.channel_id:
                video_results.append(
                    self._error_result(
                        video_id,
                        reason="wrong_channel",
                        message=f"视频不属于当前频道 {channel.channel_id}。",
                    )
                )
                continue
            try:
                video_results.append(self._preview_video(video, observed_at))
            except DashboardError as exc:
                video_results.append(
                    self._error_result(
                        video_id,
                        reason="resource_parse_failed",
                        message=str(exc),
                    )
                )

        ready_count = sum(item["status"] == "ready" for item in video_results)
        expired_count = sum(item["status"] == "expired" for item in video_results)
        failed_count = sum(item["status"] == "error" for item in video_results)
        response_has_blocking_warnings = bool(
            duplicate_response_ids or unexpected_response_ids
        )
        result = {
            **base,
            "videos": video_results,
            "response_warnings": {
                "duplicate_response_video_ids": list(dict.fromkeys(duplicate_response_ids)),
                "unexpected_response_video_ids": unexpected_response_ids,
            },
            "summary": {
                "requested_video_count": len(requested_ids),
                "ready_video_count": ready_count,
                "expired_video_count": expired_count,
                "failed_video_count": failed_count,
                "maximum_main_writes": ready_count,
                "maximum_snapshot_writes": ready_count,
                "maximum_comparison_writes": ready_count * 3,
                "maximum_total_feishu_writes": ready_count * 5,
                "safe_to_run_formal_sync": (
                    failed_count == 0 and not response_has_blocking_warnings
                ),
            },
        }
        if len(video_results) == 1:
            item = video_results[0]
            if item["status"] == "error":
                result.update({"video": None, "reason": item["reason"]})
            else:
                result.update(
                    {
                        key: value
                        for key, value in item.items()
                        if key
                        in {
                            "video",
                            "dynamic_extraction",
                            "feishu_write_preview",
                            "preview_unavailable_computed_fields",
                            "tracking",
                            "estimated_writes",
                        }
                    }
                )
        return result

    def _preview_video(self, video: VideoResource, observed_at: datetime) -> dict[str, Any]:
        age_minutes = max(
            0,
            int((as_utc(observed_at) - as_utc(video.published_at)).total_seconds() // 60),
        )
        tracking_cutoff = as_utc(observed_at) - timedelta(days=self.tracking_days)
        within_tracking_window = as_utc(video.published_at) >= tracking_cutoff
        video_values: dict[str, Any] = {}
        extraction = self.dynamic_plan.extractor.extract(video.raw) if self.dynamic_plan else None
        if extraction:
            video_values.update(extraction.values)
        video_values.update({
            **data_api_time_values(observed_at),
            "VIDEO_ID": video.video_id,
            "VIDEO_CHANNEL_ID": video.channel_id,
            "VIDEO_TITLE": video.title,
            "VIDEO_PUBLISHED_AT": video.published_at,
            "VIDEO_DURATION": parse_iso_duration_seconds(video.duration),
            "VIDEO_PRIVACY_STATUS": video.privacy_status,
            "VIDEO_AGE_MINUTES": age_minutes,
            "VIDEO_VIEWS_PUBLIC": video.view_count,
            "VIDEO_LIKES_PUBLIC": video.like_count,
            "VIDEO_COMMENTS_PUBLIC": video.comment_count,
            "VIDEO_URL": f"https://www.youtube.com/watch?v={video.video_id}",
        })
        write_preview: dict[str, dict[str, Any]] = {}
        unavailable: dict[str, list[str]] = {}
        if self.dynamic_plan:
            adapters = FeishuValueAdapterRegistry()
            for table in self.dynamic_plan.tables:
                available_values = {
                    item.standard_field_id: video_values[item.standard_field_id]
                    for item in table.mappings
                    if item.standard_field_id in video_values
                    and video_values[item.standard_field_id] is not None
                }
                write_preview[table.table_name] = self.dynamic_plan.adapt_table_record(
                    table.table_name,
                    {
                        **{
                            item.standard_field_id: None
                            for item in table.mappings
                            if item.standard_field_id not in available_values
                        },
                        **available_values,
                    },
                    adapters=adapters,
                )
                unavailable[table.table_name] = [
                    item.standard_field_id
                    for item in table.mappings
                    if item.standard_field_id not in available_values
                ]
        estimated_writes = {
            "main": 1 if within_tracking_window else 0,
            "snapshot": 1 if within_tracking_window else 0,
            "comparison": 3 if within_tracking_window else 0,
            "total": 5 if within_tracking_window else 0,
            "note": "预估上限；正式运行可能因幂等未变化或容量保护减少写入。",
        }
        return {
            "video_id": video.video_id,
            "status": "ready" if within_tracking_window else "expired",
            "reason": None if within_tracking_window else "outside_tracking_window",
            "video": video_values,
            "dynamic_extraction": {
                "enabled": extraction is not None,
                "missing_field_ids": list(extraction.missing_field_ids) if extraction else [],
                "null_field_ids": list(extraction.null_field_ids) if extraction else [],
            },
            "feishu_write_preview": write_preview,
            "preview_unavailable_computed_fields": unavailable,
            "tracking": {
                "tracking_days": self.tracking_days,
                "within_tracking_window": within_tracking_window,
            },
            "estimated_writes": estimated_writes,
        }

    @staticmethod
    def _error_result(video_id: str, *, reason: str, message: str) -> dict[str, Any]:
        return {
            "video_id": video_id,
            "status": "error",
            "reason": reason,
            "message": message,
            "estimated_writes": {"main": 0, "snapshot": 0, "comparison": 0, "total": 0},
        }
