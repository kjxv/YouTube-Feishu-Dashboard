from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from yfd_latest_video_tracker.analytics import VideoAnalyticsResult
from yfd_latest_video_tracker.manifest import (
    API_FIELD_IDS,
    DEFAULT_COMPARISON_FIELD_MAPPING,
    DEFAULT_MAIN_FIELD_MAPPING,
    DEFAULT_SNAPSHOT_FIELD_MAPPING,
    MANIFEST,
)
from yfd_latest_video_tracker.preview import LatestVideoPreviewService
from yfd_latest_video_tracker.reporting import VideoReportingResult
from yfd_latest_video_tracker.service import (
    LatestTrackerConfig,
    LatestVideoTrackerService,
    TableSyncConfig,
    parse_iso_duration_seconds,
)
from yfd_latest_video_tracker.task import LatestVideoTrackingTask
from youtube_feishu_dashboard.api.feishu.config_center import ModuleFieldMapping
from youtube_feishu_dashboard.api.youtube.schemas import ChannelResource, VideoResource
from youtube_feishu_dashboard.catalog.field_catalog import FieldCatalog
from youtube_feishu_dashboard.core.errors import ConfigurationError, ExternalServiceError
from youtube_feishu_dashboard.db.models import Video, VideoSnapshot
from youtube_feishu_dashboard.db.repositories import SqlAlchemyStorage
from youtube_feishu_dashboard.scheduler.task import TaskContext
from youtube_feishu_dashboard.services.archive import ArchiveService
from youtube_feishu_dashboard.services.dynamic_mapping import DynamicModulePlanCompiler
from youtube_feishu_dashboard.services.feishu_records import FeishuRecordService


class FakeYouTube:
    def __init__(self, published_at: datetime) -> None:
        self.published_at = published_at
        self.view_count = 100

    def get_channel(self, channel_id: str | None = None) -> ChannelResource:
        return ChannelResource(
            channel_id="UC_TEST",
            title="测试频道",
            uploads_playlist_id="UU_TEST",
            raw={"id": "UC_TEST"},
        )

    def list_upload_video_ids(
        self,
        uploads_playlist_id: str,
        *,
        published_after: datetime | None = None,
        max_pages: int = 20,
    ) -> list[str]:
        return ["okAkZVRx7ac"]

    def list_videos(self, video_ids: Any, *, parts: Any = None) -> list[VideoResource]:
        return [
            VideoResource(
                video_id="okAkZVRx7ac",
                channel_id="UC_TEST",
                title="最新视频",
                published_at=self.published_at,
                duration="PT2M3S",
                privacy_status="public",
                view_count=self.view_count,
                like_count=10,
                comment_count=2,
                raw={
                    "id": "okAkZVRx7ac",
                    "snippet": {
                        "channelId": "UC_TEST",
                        "title": "最新视频",
                        "publishedAt": self.published_at.isoformat().replace("+00:00", "Z"),
                        "thumbnails": {
                            "high": {"url": "https://example.test/okAkZVRx7ac.jpg"}
                        },
                    },
                    "contentDetails": {"duration": "PT2M3S"},
                    "status": {"privacyStatus": "public"},
                    "statistics": {
                        "viewCount": str(self.view_count),
                        "likeCount": "10",
                        "commentCount": "2",
                    },
                },
            )
        ]


def make_video(
    video_id: str,
    published_at: datetime,
    *,
    channel_id: str = "UC_TEST",
    view_count: int = 100,
) -> VideoResource:
    return VideoResource(
        video_id=video_id,
        channel_id=channel_id,
        title=f"测试视频-{video_id}",
        published_at=published_at,
        duration="PT10M",
        privacy_status="public",
        view_count=view_count,
        like_count=10,
        comment_count=2,
        raw={
            "id": video_id,
            "snippet": {
                "channelId": channel_id,
                "title": f"测试视频-{video_id}",
                "publishedAt": published_at.isoformat().replace("+00:00", "Z"),
            },
            "contentDetails": {"duration": "PT10M"},
            "status": {"privacyStatus": "public"},
            "statistics": {
                "viewCount": str(view_count),
                "likeCount": "10",
                "commentCount": "2",
            },
        },
    )


class BatchFakeYouTube:
    def __init__(
        self,
        resources: list[VideoResource],
        *,
        response_order: tuple[str, ...] | None = None,
    ) -> None:
        self.resources = {item.video_id: item for item in resources}
        self.response_order = response_order
        self.requested_batches: list[tuple[str, ...]] = []

    def get_channel(self, channel_id: str | None = None) -> ChannelResource:
        return ChannelResource(
            channel_id="UC_TEST",
            title="测试频道",
            uploads_playlist_id="UU_TEST",
            raw={"id": "UC_TEST"},
        )

    def list_upload_video_ids(
        self,
        uploads_playlist_id: str,
        *,
        published_after: datetime | None = None,
        max_pages: int = 20,
    ) -> list[str]:
        raise AssertionError("手动追踪服务不应再读取频道最新上传列表")

    def list_videos(self, video_ids: Any, *, parts: Any = None) -> list[VideoResource]:
        requested = tuple(video_ids)
        self.requested_batches.append(requested)
        ordered_ids = self.response_order or requested
        return [self.resources[video_id] for video_id in ordered_ids if video_id in self.resources]


class FakeFeishu:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.total_override: int | None = None
        self.count_calls: list[str] = []

    def count_records(self, app_token: str, table_id: str) -> int:
        self.count_calls.append(table_id)
        if self.total_override is not None:
            return self.total_override
        return sum(item["table_id"] == table_id for item in self.created)

    def batch_create_records(
        self, app_token: str, table_id: str, fields: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        output = []
        for item in fields:
            record = {
                "record_id": f"rec-{len(self.created) + 1}",
                "table_id": table_id,
                "fields": dict(item),
            }
            self.created.append(record)
            output.append(record)
        return output

    def batch_update_records(
        self, app_token: str, table_id: str, records: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        by_id = {item["record_id"]: item for item in self.created if item["table_id"] == table_id}
        for update in records:
            by_id[update["record_id"]]["fields"].update(update["fields"])
        return records

    def list_records(self, app_token: str, table_id: str) -> list[dict[str, Any]]:
        return [item for item in self.created if item["table_id"] == table_id]

    def batch_delete_records(self, app_token: str, table_id: str, record_ids: list[str]) -> None:
        raise AssertionError("实时追踪不应直接删除记录")


class FakeAnalyticsCollector:
    def __init__(
        self,
        *,
        field_ids: tuple[str, ...] = ("ANALYTICS_ENGAGED_VIEWS",),
        initial_cache: VideoAnalyticsResult | None = None,
    ) -> None:
        self.field_ids = field_ids
        self.cached = initial_cache
        self.collect_calls = 0

    def collect_video(
        self,
        *,
        video_id: str,
        published_at: datetime,
        observed_at: datetime,
    ) -> VideoAnalyticsResult:
        self.collect_calls += 1
        result = VideoAnalyticsResult(
            video_id=video_id,
            start_date=date(2026, 8, 31),
            requested_end_date=date(2026, 9, 1),
            data_through_date=date(2026, 9, 1),
            fetched_at=observed_at,
            metric_values={
                field_id: (
                    1.75 if field_id == "ANALYTICS_EST_AD_REVENUE" else 88
                )
                for field_id in self.field_ids
            },
            empty=False,
            api_request_count=2,
            raw={},
        )
        self.cached = result
        return result

    def latest_cached(self, video_id: str) -> VideoAnalyticsResult | None:
        return self.cached


class FailingAnalyticsCollector(FakeAnalyticsCollector):
    def collect_video(
        self,
        *,
        video_id: str,
        published_at: datetime,
        observed_at: datetime,
    ) -> VideoAnalyticsResult:
        self.collect_calls += 1
        raise ExternalServiceError("测试用 Analytics 刷新失败", retryable=False)


class FakeReportingCollector:
    field_ids = ("ANALYTICS_IMPRESSIONS",)

    def __init__(self) -> None:
        self.cached: VideoReportingResult | None = None
        self.collect_calls = 0

    def collect_video(
        self,
        *,
        video_id: str,
        published_at: datetime,
        observed_at: datetime,
    ) -> VideoReportingResult:
        self.collect_calls += 1
        result = VideoReportingResult(
            video_id=video_id,
            checked_at=observed_at,
            data_fetched_at=observed_at,
            data_through_date=date(2026, 9, 1),
            field_values={"ANALYTICS_IMPRESSIONS": 500},
            status="available",
            api_request_count=3,
            downloaded_report_count=1,
            raw={},
        )
        self.cached = result
        return result

    def latest_cached(self, video_id: str) -> VideoReportingResult | None:
        return self.cached


def build_service(
    storage: SqlAlchemyStorage,
    youtube: Any,
    feishu: FakeFeishu,
    *,
    video_ids: tuple[str, ...] = ("okAkZVRx7ac",),
    analytics: Any = None,
    reporting: Any = None,
) -> LatestVideoTrackerService:
    catalog = FieldCatalog.load_builtin()
    plan = catalog.build_request_plan("latest_video_tracker", list(API_FIELD_IDS))
    return LatestVideoTrackerService(
        youtube=youtube,
        storage=storage,
        records=FeishuRecordService(feishu, storage, "base"),
        archive=ArchiveService(
            gateway=feishu,
            storage=storage,
            app_token="base",
            row_threshold=19000,
        ),
        config=LatestTrackerConfig(
            channel_id="UC_TEST",
            tracking_days=7,
            interval_minutes=30,
            main_table=TableSyncConfig("main", dict(DEFAULT_MAIN_FIELD_MAPPING)),
            snapshot_table=TableSyncConfig("snapshot", dict(DEFAULT_SNAPSHOT_FIELD_MAPPING)),
            comparison_table=TableSyncConfig(
                "comparison", dict(DEFAULT_COMPARISON_FIELD_MAPPING)
            ),
            tracking_video_ids=video_ids,
        ),
        request_plan=plan,
        analytics=analytics,
        reporting=reporting,
    )


def test_task_dry_run_shows_manual_video_batch_without_external_requests(
    storage: SqlAlchemyStorage,
) -> None:
    published_at = datetime(2026, 8, 31, 0, 0, tzinfo=UTC)
    service = build_service(
        storage,
        FakeYouTube(published_at),
        FakeFeishu(),
        video_ids=("okAkZVRx7ac", "dQw4w9WgXcQ"),
    )
    task = LatestVideoTrackingTask(service, service.request_plan)

    result = task.execute(
        TaskContext(
            now=published_at,
            scheduled_for=published_at,
            attempt=1,
            dry_run=True,
        )
    )

    assert result.counts == {}
    assert result.details["dry_run"] is True
    assert result.details["tracking_video_config"] == {
        "video_ids": ["okAkZVRx7ac", "dQw4w9WgXcQ"],
        "video_count": 2,
    }


def test_latest_tracker_records_real_snapshots_and_deltas(
    storage: SqlAlchemyStorage,
) -> None:
    published_at = datetime(2026, 8, 31, 0, 0, tzinfo=UTC)
    youtube = FakeYouTube(published_at)
    feishu = FakeFeishu()
    service = build_service(storage, youtube, feishu)

    first_counts, _ = service.track(published_at + timedelta(minutes=10))
    youtube.view_count = 150
    second_counts, _ = service.track(published_at + timedelta(minutes=40))

    assert first_counts["snapshots"] == second_counts["snapshots"] == 1
    assert first_counts["feishu_writes"] == second_counts["feishu_writes"] == 5
    assert len(feishu.created) == 9
    main_records = [item for item in feishu.created if item["table_id"] == "main"]
    snapshot_records = [item for item in feishu.created if item["table_id"] == "snapshot"]
    comparison_records = [item for item in feishu.created if item["table_id"] == "comparison"]
    assert len(main_records) == 1
    assert main_records[0]["fields"]["播放量"] == 150
    assert len(snapshot_records) == 2
    assert snapshot_records[0]["fields"]["本周期新增播放量"] == 0
    assert snapshot_records[1]["fields"]["本周期新增播放量"] == 50
    assert len(comparison_records) == 6
    assert {item["fields"]["对比对象"] for item in comparison_records} == {
        "当前视频",
        "最近10条平均",
        "最近10条中位数",
    }
    with storage.transaction() as repos:
        count = repos.session.scalar(select(func.count()).select_from(VideoSnapshot))
    assert count == 2


def test_analytics_refreshes_once_then_uses_24_hour_cache(
    storage: SqlAlchemyStorage,
) -> None:
    published_at = datetime(2026, 8, 31, 0, 0, tzinfo=UTC)
    analytics = FakeAnalyticsCollector()
    service = build_service(
        storage,
        FakeYouTube(published_at),
        FakeFeishu(),
        analytics=analytics,
    )

    _, first_details = service.track(published_at + timedelta(hours=1))
    _, second_details = service.track(published_at + timedelta(hours=2))

    assert analytics.collect_calls == 1
    assert first_details["analytics"]["status"] == "refreshed"
    assert first_details["analytics"]["api_requests"] == 2
    assert second_details["analytics"]["status"] == "cache_hit"
    assert second_details["analytics"]["api_requests"] == 0


def test_analytics_new_metric_bypasses_incomplete_legacy_cache(
    storage: SqlAlchemyStorage,
) -> None:
    published_at = datetime(2026, 8, 31, 0, 0, tzinfo=UTC)
    cached_at = published_at + timedelta(minutes=30)
    legacy_cache = VideoAnalyticsResult(
        video_id="okAkZVRx7ac",
        start_date=date(2026, 8, 31),
        requested_end_date=date(2026, 8, 31),
        data_through_date=date(2026, 8, 31),
        fetched_at=cached_at,
        metric_values={"ANALYTICS_ENGAGED_VIEWS": 77},
        empty=False,
        api_request_count=0,
        raw={},
    )
    analytics = FakeAnalyticsCollector(
        field_ids=("ANALYTICS_ENGAGED_VIEWS", "ANALYTICS_EST_AD_REVENUE"),
        initial_cache=legacy_cache,
    )
    service = build_service(
        storage,
        FakeYouTube(published_at),
        FakeFeishu(),
        analytics=analytics,
    )

    _, details = service.track(published_at + timedelta(hours=1))

    assert analytics.collect_calls == 1
    assert details["analytics"]["status"] == "refreshed"
    assert details["analytics"]["cache_upgrade_field_ids"] == [
        "ANALYTICS_EST_AD_REVENUE"
    ]


def test_analytics_failed_cache_upgrade_returns_explicit_null_instead_of_missing_field(
    storage: SqlAlchemyStorage,
) -> None:
    published_at = datetime(2026, 8, 31, 0, 0, tzinfo=UTC)
    observed_at = published_at + timedelta(hours=1)
    legacy_cache = VideoAnalyticsResult(
        video_id="okAkZVRx7ac",
        start_date=date(2026, 8, 31),
        requested_end_date=date(2026, 8, 31),
        data_through_date=date(2026, 8, 31),
        fetched_at=published_at + timedelta(minutes=30),
        metric_values={"ANALYTICS_ENGAGED_VIEWS": 77},
        empty=False,
        api_request_count=0,
        raw={},
    )
    analytics = FailingAnalyticsCollector(
        field_ids=("ANALYTICS_ENGAGED_VIEWS", "ANALYTICS_EST_AD_REVENUE"),
        initial_cache=legacy_cache,
    )
    youtube = FakeYouTube(published_at)
    service = build_service(
        storage,
        youtube,
        FakeFeishu(),
        analytics=analytics,
    )
    video = youtube.list_videos(("okAkZVRx7ac",))[0]

    values, details = service._analytics_values(video, observed_at=observed_at)

    assert analytics.collect_calls == 1
    assert values["ANALYTICS_ENGAGED_VIEWS"] == 77
    assert "ANALYTICS_EST_AD_REVENUE" in values
    assert values["ANALYTICS_EST_AD_REVENUE"] is None
    assert details["status"] == "stale_cache"
    assert details["missing_cached_metric_field_ids"] == [
        "ANALYTICS_EST_AD_REVENUE"
    ]


def test_reporting_checks_once_then_uses_24_hour_cache(
    storage: SqlAlchemyStorage,
) -> None:
    published_at = datetime(2026, 8, 31, 0, 0, tzinfo=UTC)
    reporting = FakeReportingCollector()
    service = build_service(
        storage,
        FakeYouTube(published_at),
        FakeFeishu(),
        reporting=reporting,
    )

    _, first_details = service.track(published_at + timedelta(hours=1))
    _, second_details = service.track(published_at + timedelta(hours=2))

    assert reporting.collect_calls == 1
    assert first_details["reporting"]["status"] == "available"
    assert first_details["reporting"]["api_requests"] == 3
    assert second_details["reporting"]["status"] == "cache_hit"
    assert second_details["reporting"]["source_status"] == "available"


def test_reporting_value_flows_through_dynamic_mapping_to_main_table(
    storage: SqlAlchemyStorage,
) -> None:
    published_at = datetime(2026, 8, 31, 0, 0, tzinfo=UTC)
    youtube = FakeYouTube(published_at)
    feishu = FakeFeishu()
    table_ids = {
        "视频追踪主表": "main",
        "视频实时快照表": "snapshot",
        "视频同期对比表": "comparison",
    }
    mappings = (
        ModuleFieldMapping(
            module_id=MANIFEST.module_id,
            standard_field_id="VIDEO_ID",
            feishu_column="视频唯一编号",
            target_table_id="main",
        ),
        ModuleFieldMapping(
            module_id=MANIFEST.module_id,
            standard_field_id="ANALYTICS_IMPRESSIONS",
            feishu_column="展示次数",
            target_table_id="main",
        ),
        ModuleFieldMapping(
            module_id=MANIFEST.module_id,
            standard_field_id="MODULE_UNIQUE_KEY",
            feishu_column="快照唯一编号",
            target_table_id="snapshot",
        ),
        ModuleFieldMapping(
            module_id=MANIFEST.module_id,
            standard_field_id="COMPARISON_RECORD_ID",
            feishu_column="对比记录编号",
            target_table_id="comparison",
        ),
    )
    raw_fields = {
        "main": [
            {"field_id": "fld-main-id", "field_name": "视频唯一编号", "type": 1},
            {"field_id": "fld-main-reach", "field_name": "展示次数", "type": 2},
        ],
        "snapshot": [
            {"field_id": "fld-snapshot-id", "field_name": "快照唯一编号", "type": 1}
        ],
        "comparison": [
            {"field_id": "fld-comparison-id", "field_name": "对比记录编号", "type": 1}
        ],
    }
    plan = DynamicModulePlanCompiler(
        catalog=FieldCatalog.load_builtin(),
        computed_field_ids=set(MANIFEST.output_field_ids),
        required_data_field_ids=API_FIELD_IDS,
        supported_api_sources={"data_api", "reporting_api"},
    ).compile(
        module_id=MANIFEST.module_id,
        entity_level="video",
        table_ids=table_ids,
        mappings=mappings,
        raw_fields_by_table_id=raw_fields,
    )
    service = LatestVideoTrackerService(
        youtube=youtube,
        storage=storage,
        records=FeishuRecordService(feishu, storage, "base"),
        archive=ArchiveService(
            gateway=feishu,
            storage=storage,
            app_token="base",
            row_threshold=19000,
        ),
        config=LatestTrackerConfig(
            channel_id="UC_TEST",
            tracking_days=7,
            interval_minutes=60,
            main_table=TableSyncConfig(
                "main",
                plan.require_table("视频追踪主表").field_mapping,
                "视频追踪主表",
            ),
            snapshot_table=TableSyncConfig(
                "snapshot",
                plan.require_table("视频实时快照表").field_mapping,
                "视频实时快照表",
            ),
            comparison_table=TableSyncConfig(
                "comparison",
                plan.require_table("视频同期对比表").field_mapping,
                "视频同期对比表",
            ),
            tracking_video_ids=("okAkZVRx7ac",),
        ),
        request_plan=plan.request_plan,
        dynamic_plan=plan,
        reporting=FakeReportingCollector(),  # type: ignore[arg-type]
    )

    service.track(published_at + timedelta(hours=1))

    main = next(item for item in feishu.created if item["table_id"] == "main")
    assert main["fields"]["展示次数"] == 500


def test_estimated_ad_revenue_flows_through_dynamic_mapping_to_main_table(
    storage: SqlAlchemyStorage,
) -> None:
    published_at = datetime(2026, 8, 31, 0, 0, tzinfo=UTC)
    youtube = FakeYouTube(published_at)
    feishu = FakeFeishu()
    table_ids = {
        "视频追踪主表": "main",
        "视频实时快照表": "snapshot",
        "视频同期对比表": "comparison",
    }
    mappings = (
        ModuleFieldMapping(
            module_id=MANIFEST.module_id,
            standard_field_id="VIDEO_ID",
            feishu_column="视频唯一编号",
            target_table_id="main",
        ),
        ModuleFieldMapping(
            module_id=MANIFEST.module_id,
            standard_field_id="ANALYTICS_EST_AD_REVENUE",
            feishu_column="预计广告收入（美元）",
            target_table_id="main",
        ),
        ModuleFieldMapping(
            module_id=MANIFEST.module_id,
            standard_field_id="MODULE_UNIQUE_KEY",
            feishu_column="快照唯一编号",
            target_table_id="snapshot",
        ),
        ModuleFieldMapping(
            module_id=MANIFEST.module_id,
            standard_field_id="COMPARISON_RECORD_ID",
            feishu_column="对比记录编号",
            target_table_id="comparison",
        ),
    )
    raw_fields = {
        "main": [
            {"field_id": "fld-main-id", "field_name": "视频唯一编号", "type": 1},
            {
                "field_id": "fld-main-ad-revenue",
                "field_name": "预计广告收入（美元）",
                "type": 2,
            },
        ],
        "snapshot": [
            {"field_id": "fld-snapshot-id", "field_name": "快照唯一编号", "type": 1}
        ],
        "comparison": [
            {"field_id": "fld-comparison-id", "field_name": "对比记录编号", "type": 1}
        ],
    }
    plan = DynamicModulePlanCompiler(
        catalog=FieldCatalog.load_builtin(),
        computed_field_ids=set(MANIFEST.output_field_ids),
        required_data_field_ids=API_FIELD_IDS,
        supported_api_sources={"data_api", "analytics_api"},
    ).compile(
        module_id=MANIFEST.module_id,
        entity_level="video",
        table_ids=table_ids,
        mappings=mappings,
        raw_fields_by_table_id=raw_fields,
    )
    analytics = FakeAnalyticsCollector(
        field_ids=("ANALYTICS_EST_AD_REVENUE",)
    )
    service = LatestVideoTrackerService(
        youtube=youtube,
        storage=storage,
        records=FeishuRecordService(feishu, storage, "base"),
        archive=ArchiveService(
            gateway=feishu,
            storage=storage,
            app_token="base",
            row_threshold=19000,
        ),
        config=LatestTrackerConfig(
            channel_id="UC_TEST",
            tracking_days=7,
            interval_minutes=60,
            main_table=TableSyncConfig(
                "main",
                plan.require_table("视频追踪主表").field_mapping,
                "视频追踪主表",
            ),
            snapshot_table=TableSyncConfig(
                "snapshot",
                plan.require_table("视频实时快照表").field_mapping,
                "视频实时快照表",
            ),
            comparison_table=TableSyncConfig(
                "comparison",
                plan.require_table("视频同期对比表").field_mapping,
                "视频同期对比表",
            ),
            tracking_video_ids=("okAkZVRx7ac",),
        ),
        request_plan=plan.request_plan,
        dynamic_plan=plan,
        analytics=analytics,  # type: ignore[arg-type]
    )

    service.track(published_at + timedelta(hours=1))

    assert plan.request_plan.analytics_metrics == ("estimatedAdRevenue",)
    assert set(plan.request_plan.required_scopes) == {
        "youtube.readonly",
        "yt-analytics-monetary.readonly",
    }
    main = next(item for item in feishu.created if item["table_id"] == "main")
    assert main["fields"]["预计广告收入（美元）"] == 1.75


def test_manual_multi_video_sync_batches_ids_and_aggregates_three_table_counts(
    storage: SqlAlchemyStorage,
) -> None:
    observed_at = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    first_id = "okAkZVRx7ac"
    second_id = "dQw4w9WgXcQ"
    youtube = BatchFakeYouTube(
        [
            make_video(first_id, observed_at - timedelta(hours=2), view_count=200),
            make_video(second_id, observed_at - timedelta(hours=1), view_count=300),
        ],
        response_order=(second_id, first_id),
    )
    feishu = FakeFeishu()

    counts, details = build_service(
        storage,
        youtube,
        feishu,
        video_ids=(first_id, second_id),
    ).track(observed_at)

    assert youtube.requested_batches == [(first_id, second_id)]
    assert counts == {
        "videos": 2,
        "snapshots": 2,
        "main_records": 2,
        "snapshot_records": 2,
        "comparison_records": 6,
        "feishu_writes": 10,
    }
    assert details["requested_video_count"] == 2
    assert details["active_video_count"] == 2
    assert details["expired_video_count"] == 0
    assert [item["video_id"] for item in details["video_results"]] == [first_id, second_id]
    assert all(item["status"] == "synced" for item in details["video_results"])
    assert feishu.count_calls == ["snapshot", "comparison"]
    assert len(feishu.created) == 10


def test_batch_preflight_missing_video_blocks_all_local_and_feishu_writes(
    storage: SqlAlchemyStorage,
) -> None:
    observed_at = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    first_id = "okAkZVRx7ac"
    missing_id = "dQw4w9WgXcQ"
    youtube = BatchFakeYouTube(
        [make_video(first_id, observed_at - timedelta(hours=1))]
    )
    feishu = FakeFeishu()

    with pytest.raises(ConfigurationError, match=f"未读取到指定视频.*{missing_id}"):
        build_service(
            storage,
            youtube,
            feishu,
            video_ids=(first_id, missing_id),
        ).track(observed_at)

    assert feishu.created == []
    assert feishu.count_calls == []
    with storage.transaction() as repos:
        assert repos.session.scalar(select(func.count()).select_from(Video)) == 0
        assert repos.session.scalar(select(func.count()).select_from(VideoSnapshot)) == 0


def test_batch_preflight_wrong_channel_blocks_entire_batch(
    storage: SqlAlchemyStorage,
) -> None:
    observed_at = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    first_id = "okAkZVRx7ac"
    other_id = "dQw4w9WgXcQ"
    youtube = BatchFakeYouTube(
        [
            make_video(first_id, observed_at - timedelta(hours=2)),
            make_video(
                other_id,
                observed_at - timedelta(hours=1),
                channel_id="UC_OTHER",
            ),
        ]
    )
    feishu = FakeFeishu()

    with pytest.raises(ConfigurationError, match=f"不属于当前频道.*{other_id}"):
        build_service(
            storage,
            youtube,
            feishu,
            video_ids=(first_id, other_id),
        ).track(observed_at)

    assert feishu.created == []
    assert feishu.count_calls == []
    with storage.transaction() as repos:
        assert repos.session.scalar(select(func.count()).select_from(VideoSnapshot)) == 0


def test_batch_preparation_error_happens_before_any_local_or_feishu_write(
    storage: SqlAlchemyStorage,
) -> None:
    observed_at = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    first_id = "okAkZVRx7ac"
    invalid_id = "dQw4w9WgXcQ"
    invalid = replace(
        make_video(invalid_id, observed_at - timedelta(hours=1)),
        duration="not-an-iso-duration",
    )
    youtube = BatchFakeYouTube(
        [make_video(first_id, observed_at - timedelta(hours=2)), invalid]
    )
    feishu = FakeFeishu()

    with pytest.raises(ConfigurationError, match="无法解析 YouTube ISO 8601 时长"):
        build_service(
            storage,
            youtube,
            feishu,
            video_ids=(first_id, invalid_id),
        ).track(observed_at)

    assert feishu.created == []
    assert feishu.count_calls == []
    with storage.transaction() as repos:
        assert repos.session.scalar(select(func.count()).select_from(VideoSnapshot)) == 0


def test_expired_video_skips_only_itself_and_preserves_requested_order(
    storage: SqlAlchemyStorage,
) -> None:
    observed_at = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    expired_id = "dQw4w9WgXcQ"
    active_id = "okAkZVRx7ac"
    youtube = BatchFakeYouTube(
        [
            make_video(expired_id, observed_at - timedelta(days=8)),
            make_video(active_id, observed_at - timedelta(hours=1)),
        ]
    )
    feishu = FakeFeishu()

    counts, details = build_service(
        storage,
        youtube,
        feishu,
        video_ids=(expired_id, active_id),
    ).track(observed_at)

    assert counts == {
        "videos": 2,
        "snapshots": 1,
        "main_records": 1,
        "snapshot_records": 1,
        "comparison_records": 3,
        "feishu_writes": 5,
    }
    assert details["active_video_count"] == 1
    assert details["expired_video_count"] == 1
    assert [item["video_id"] for item in details["video_results"]] == [
        expired_id,
        active_id,
    ]
    assert [item["status"] for item in details["video_results"]] == ["skipped", "synced"]
    assert len(feishu.created) == 5


def test_latest_tracker_does_not_invent_snapshots_after_tracking_window(
    storage: SqlAlchemyStorage,
) -> None:
    published_at = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    youtube = FakeYouTube(published_at)
    feishu = FakeFeishu()

    counts, details = build_service(storage, youtube, feishu).track(
        datetime(2026, 8, 31, 0, 0, tzinfo=UTC)
    )

    assert counts["snapshots"] == 0
    assert details["reason"] == "all_configured_videos_outside_tracking_window"
    assert feishu.created == []


def test_youtube_duration_parser() -> None:
    assert parse_iso_duration_seconds("PT2M3S") == 123
    assert parse_iso_duration_seconds("P1DT1H") == 90000


def test_archive_threshold_keeps_snapshot_in_database_but_stops_feishu_write(
    storage: SqlAlchemyStorage,
) -> None:
    published_at = datetime(2026, 8, 31, 0, 0, tzinfo=UTC)
    youtube = FakeYouTube(published_at)
    feishu = FakeFeishu()
    feishu.total_override = 19000

    counts, details = build_service(storage, youtube, feishu).track(
        published_at + timedelta(minutes=10)
    )

    assert counts == {
        "videos": 1,
        "snapshots": 1,
        "main_records": 1,
        "snapshot_records": 0,
        "comparison_records": 0,
        "feishu_writes": 1,
    }
    assert details["snapshot_archive_status"] == "archive_required"
    assert details["comparison_archive_status"] == "archive_required"
    assert len(feishu.created) == 1
    assert feishu.created[0]["table_id"] == "main"


def test_latest_video_preview_reads_standard_fields_without_external_writes() -> None:
    observed_at = datetime(2026, 8, 31, 1, 0, tzinfo=UTC)
    published_at = observed_at - timedelta(minutes=15)
    youtube = FakeYouTube(published_at)
    plan = FieldCatalog.load_builtin().build_request_plan(
        "latest_video_tracker", list(API_FIELD_IDS)
    )

    result = LatestVideoPreviewService(
        youtube=youtube,
        request_plan=plan,
        channel_id=None,
        tracking_days=7,
        tracking_video_ids=("okAkZVRx7ac",),
    ).preview(observed_at)

    assert result["mode"] == "youtube_manual_multi_video_read_only_preview"
    assert result["external_requests_made"] is True
    assert result["external_writes_made"] is False
    assert result["video"]["VIDEO_ID"] == "okAkZVRx7ac"
    assert result["video"]["VIDEO_DURATION"] == 123
    assert result["video"]["VIDEO_AGE_MINUTES"] == 15
    assert result["tracking"]["within_tracking_window"] is True


def test_multi_video_preview_reports_ready_expired_missing_and_wrong_channel() -> None:
    observed_at = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    ready_id = "okAkZVRx7ac"
    expired_id = "dQw4w9WgXcQ"
    missing_id = "abc_DEF-123"
    wrong_channel_id = "vid00000001"
    youtube = BatchFakeYouTube(
        [
            make_video(ready_id, observed_at - timedelta(hours=1)),
            make_video(expired_id, observed_at - timedelta(days=8)),
            make_video(
                wrong_channel_id,
                observed_at - timedelta(hours=2),
                channel_id="UC_OTHER",
            ),
        ],
        response_order=(wrong_channel_id, expired_id, ready_id),
    )
    plan = FieldCatalog.load_builtin().build_request_plan(
        "latest_video_tracker", list(API_FIELD_IDS)
    )

    result = LatestVideoPreviewService(
        youtube=youtube,
        request_plan=plan,
        channel_id="UC_TEST",
        tracking_days=7,
        tracking_video_ids=(ready_id, expired_id, missing_id, wrong_channel_id),
    ).preview(observed_at)

    assert youtube.requested_batches == [
        (ready_id, expired_id, missing_id, wrong_channel_id)
    ]
    assert [item["video_id"] for item in result["videos"]] == [
        ready_id,
        expired_id,
        missing_id,
        wrong_channel_id,
    ]
    assert [item["status"] for item in result["videos"]] == [
        "ready",
        "expired",
        "error",
        "error",
    ]
    assert result["videos"][0]["estimated_writes"]["total"] == 5
    assert result["videos"][1]["estimated_writes"]["total"] == 0
    assert result["videos"][2]["reason"] == "video_not_accessible"
    assert result["videos"][3]["reason"] == "wrong_channel"
    assert result["summary"] == {
        "requested_video_count": 4,
        "ready_video_count": 1,
        "expired_video_count": 1,
        "failed_video_count": 2,
        "maximum_main_writes": 1,
        "maximum_snapshot_writes": 1,
        "maximum_comparison_writes": 3,
        "maximum_total_feishu_writes": 5,
        "safe_to_run_formal_sync": False,
    }
    assert result["external_writes_made"] is False
    assert result["local_business_database_writes_made"] is False


def test_dynamic_plan_extracts_and_adapts_new_thumbnail_mapping_in_real_service_path(
    storage: SqlAlchemyStorage,
) -> None:
    published_at = datetime(2026, 8, 31, 0, 0, tzinfo=UTC)
    youtube = FakeYouTube(published_at)
    feishu = FakeFeishu()
    table_ids = {
        "视频追踪主表": "main",
        "视频实时快照表": "snapshot",
        "视频同期对比表": "comparison",
    }
    mappings = tuple(
        ModuleFieldMapping(
            module_id=MANIFEST.module_id,
            standard_field_id=field_id,
            feishu_column=column,
            target_table_id=table_ids[table_name],
        )
        for table_name, items in {
            "视频追踪主表": (
                ("VIDEO_ID", "视频唯一编号"),
                ("VIDEO_THUMBNAIL_URL", "视频缩略图"),
                ("DATA_API_FETCHED_AT_BEIJING", "Data API 数据获取时间（北京时间）"),
            ),
            "视频实时快照表": (
                ("MODULE_UNIQUE_KEY", "快照唯一编号"),
                ("VIDEO_THUMBNAIL_URL", "视频缩略图"),
            ),
            "视频同期对比表": (
                ("COMPARISON_RECORD_ID", "对比记录编号"),
                ("VIDEO_THUMBNAIL_URL", "视频缩略图"),
            ),
        }.items()
        for field_id, column in items
    )
    raw_fields = {
        table_id: [
            {
                "field_id": f"fld-{table_id}-id",
                "field_name": next(
                    item.feishu_column
                    for item in mappings
                    if item.target_table_id == table_id
                    and item.standard_field_id
                    in {"VIDEO_ID", "MODULE_UNIQUE_KEY", "COMPARISON_RECORD_ID"}
                ),
                "type": 1,
            },
            {
                "field_id": f"fld-{table_id}-thumb",
                "field_name": "视频缩略图",
                "type": 15,
            },
        ]
        for table_id in table_ids.values()
    }
    raw_fields["main"].append(
        {
            "field_id": "fld-main-data-fetched-beijing",
            "field_name": "Data API 数据获取时间（北京时间）",
            "type": 1,
        }
    )
    plan = DynamicModulePlanCompiler(
        catalog=FieldCatalog.load_builtin(),
        computed_field_ids=set(MANIFEST.output_field_ids),
        required_data_field_ids=API_FIELD_IDS,
    ).compile(
        module_id=MANIFEST.module_id,
        entity_level="video",
        table_ids=table_ids,
        mappings=mappings,
        raw_fields_by_table_id=raw_fields,
    )
    service = LatestVideoTrackerService(
        youtube=youtube,
        storage=storage,
        records=FeishuRecordService(feishu, storage, "base"),
        archive=ArchiveService(
            gateway=feishu,
            storage=storage,
            app_token="base",
            row_threshold=19000,
        ),
        config=LatestTrackerConfig(
            channel_id="UC_TEST",
            tracking_days=7,
            interval_minutes=30,
            main_table=TableSyncConfig(
                "main", plan.require_table("视频追踪主表").field_mapping, "视频追踪主表"
            ),
            snapshot_table=TableSyncConfig(
                "snapshot",
                plan.require_table("视频实时快照表").field_mapping,
                "视频实时快照表",
            ),
            comparison_table=TableSyncConfig(
                "comparison",
                plan.require_table("视频同期对比表").field_mapping,
                "视频同期对比表",
            ),
            tracking_video_ids=("okAkZVRx7ac",),
        ),
        request_plan=plan.request_plan,
        dynamic_plan=plan,
    )

    counts, details = service.track(published_at + timedelta(minutes=10))

    assert counts["feishu_writes"] == 5
    assert details["missing_dynamic_api_fields"] == []
    main = next(item for item in feishu.created if item["table_id"] == "main")
    assert main["fields"]["视频缩略图"] == {
        "link": "https://example.test/okAkZVRx7ac.jpg",
        "text": "https://example.test/okAkZVRx7ac.jpg",
    }
    assert main["fields"]["Data API 数据获取时间（北京时间）"] == (
        "2026-08-31T08:10:00+08:00"
    )
