from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from yfd_channel_history.analytics import ChannelAnalyticsCollector
from yfd_channel_history.manifest import (
    DEFAULT_CHANNEL_HISTORY_MAPPING,
    DEFAULT_VIDEO_HISTORY_MAPPING,
    DEFAULT_VIDEO_MAIN_MAPPING,
)
from yfd_channel_history.runtime import ChannelHistoryRuntimePlan, ChannelTablePlan
from yfd_channel_history.service import ChannelHistoryConfig, ChannelHistoryService
from yfd_channel_history.task import ChannelHistoryDailyTask
from youtube_feishu_dashboard.api.feishu.config_center import ModuleFieldMapping
from youtube_feishu_dashboard.api.feishu.field_types import FeishuTableSchema
from youtube_feishu_dashboard.api.youtube.schemas import (
    AnalyticsTable,
    ChannelResource,
    VideoResource,
)
from youtube_feishu_dashboard.catalog.field_catalog import FieldCatalog
from youtube_feishu_dashboard.db.database import Database
from youtube_feishu_dashboard.db.repositories import SqlAlchemyStorage
from youtube_feishu_dashboard.scheduler.task import TaskContext
from youtube_feishu_dashboard.services.feishu_records import FeishuRecordService


class FakeFeishu:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = {
            "main": [],
            "video_history": [],
            "channel_history": [],
        }
        self.sequence = 0
        self.create_batches: list[tuple[str, int]] = []
        self.update_batches: list[tuple[str, int]] = []

    def list_records(self, app_token: str, table_id: str) -> list[dict[str, Any]]:
        return self.tables[table_id]

    def count_records(self, app_token: str, table_id: str) -> int:
        return len(self.tables[table_id])

    def batch_create_records(
        self, app_token: str, table_id: str, fields: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        self.create_batches.append((table_id, len(fields)))
        created = []
        for values in fields:
            self.sequence += 1
            item = {"record_id": f"rec{self.sequence}", "fields": dict(values)}
            self.tables[table_id].append(item)
            created.append(item)
        return created

    def batch_update_records(
        self, app_token: str, table_id: str, records: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        self.update_batches.append((table_id, len(records)))
        by_id = {item["record_id"]: item for item in self.tables[table_id]}
        for update in records:
            by_id[update["record_id"]]["fields"].update(update["fields"])
        return records

    def batch_delete_records(
        self, app_token: str, table_id: str, record_ids: list[str]
    ) -> None:
        self.tables[table_id] = [
            item for item in self.tables[table_id] if item["record_id"] not in record_ids
        ]


class FakeDataApi:
    def __init__(self, observed_at: datetime) -> None:
        self.observed_at = observed_at
        self.long_views = 100
        self.subscribers = 1000

    def get_channel(self, channel_id: str | None = None) -> ChannelResource:
        return ChannelResource(
            channel_id="UC_TEST",
            title="测试频道",
            uploads_playlist_id="UU_TEST",
            raw={"id": "UC_TEST"},
            view_count=5000,
            subscriber_count=self.subscribers,
            video_count=3,
            hidden_subscriber_count=False,
        )

    def list_upload_video_ids(
        self,
        uploads_playlist_id: str,
        *,
        published_after: datetime | None = None,
        max_pages: int = 20,
    ) -> list[str]:
        return ["long", "short", "live"]

    def list_videos(
        self, video_ids: Any, *, parts: Any = None
    ) -> list[VideoResource]:
        published = datetime(2026, 8, 1, tzinfo=UTC)
        return [
            _video("long", published, "PT10M", self.long_views, "none"),
            _video("short", published, "PT30S", 30, "none"),
            _video("live", published, "PT1H", 400, "live"),
        ]


class FakeAnalyticsApi:
    def __init__(
        self,
        *,
        confirm_long_video: bool = True,
        actual_creator_content_type_case: bool = False,
    ) -> None:
        self.confirm_long_video = confirm_long_video
        self.long_type = (
            "videoOnDemand"
            if actual_creator_content_type_case
            else "VIDEO_ON_DEMAND"
        )
        self.shorts_type = "shorts" if actual_creator_content_type_case else "SHORTS"

    def query(self, **kwargs: Any) -> AnalyticsTable:
        dimensions = tuple(kwargs.get("dimensions", ()))
        if dimensions == ("video", "creatorContentType"):
            if not self.confirm_long_video:
                return _table(
                    ("video", "creatorContentType", "views"),
                    (("short", self.shorts_type, 30),),
                )
            return _table(
                ("video", "creatorContentType", "views"),
                (
                    ("long", self.long_type, 100),
                    ("short", self.shorts_type, 30),
                ),
            )
        if dimensions == ("day",):
            return _table(
                ("day", "views", "subscribersGained", "subscribersLost"),
                (("2026-09-02", 50, 3, 1), ("2026-09-03", 60, 4, 1)),
            )
        if dimensions == ("day", "creatorContentType"):
            return _table(
                ("day", "creatorContentType", "views"),
                (
                    ("2026-09-02", self.long_type, 40),
                    ("2026-09-03", self.long_type, 45),
                ),
            )
        if dimensions == ("day", "video", "creatorContentType"):
            return _table(
                ("day", "video", "creatorContentType", "views"),
                (
                    ("2026-09-02", "long", self.long_type, 40),
                    ("2026-09-03", "long", self.long_type, 45),
                ),
            )
        raise AssertionError(f"unexpected dimensions: {dimensions}")


def test_official_content_type_classification_excludes_shorts_and_live() -> None:
    now = datetime(2026, 9, 5, 1, tzinfo=UTC)
    videos = FakeDataApi(now).list_videos([])

    result = ChannelAnalyticsCollector(FakeAnalyticsApi()).classify_long_videos(videos, now)

    assert result.long_video_ids == ("long",)
    assert result.skipped == {"short": "shorts", "live": "live_or_upcoming"}


def test_actual_creator_content_type_casing_is_supported() -> None:
    now = datetime(2026, 9, 5, 1, tzinfo=UTC)
    videos = FakeDataApi(now).list_videos([])
    collector = ChannelAnalyticsCollector(
        FakeAnalyticsApi(actual_creator_content_type_case=True)
    )

    classification = collector.classify_long_videos(videos, now)
    daily = collector.collect_daily(
        long_video_ids=classification.long_video_ids,
        observed_at=now,
        lookback_days=7,
    )

    assert classification.long_video_ids == ("long",)
    assert classification.skipped == {
        "short": "shorts",
        "live": "live_or_upcoming",
    }
    assert daily.long_views == {
        date(2026, 9, 2): 40,
        date(2026, 9, 3): 45,
    }
    assert daily.video_views == {
        ("long", date(2026, 9, 2)): 40,
        ("long", date(2026, 9, 3)): 45,
    }
    assert daily.data_through_date == date(2026, 9, 3)


def test_daily_collection_writes_three_tables_idempotently_and_builds_7d_delta(
    storage: SqlAlchemyStorage,
) -> None:
    observed_at = datetime(2026, 9, 5, 1, tzinfo=UTC)
    data = FakeDataApi(observed_at)
    feishu = FakeFeishu()
    service = ChannelHistoryService(
        youtube=data,
        analytics=ChannelAnalyticsCollector(FakeAnalyticsApi()),
        storage=storage,
        records=FeishuRecordService(feishu, storage, "base"),
        config=ChannelHistoryConfig(
            channel_id="UC_TEST",
            runtime_plan=_runtime_plan(),
        ),
    )

    counts, details = service.collect(observed_at)

    assert details["long_video_count"] == 1
    assert "feishu_writes" not in counts
    assert counts["feishu_records_changed"] == 7
    assert counts["feishu_batch_requests"] == 3
    assert counts["feishu_bindings_adopted"] == 0
    assert counts["current_flag_reset_records"] == 0
    assert len(feishu.tables["main"]) == 1
    assert len(feishu.tables["video_history"]) == 3
    assert len(feishu.tables["channel_history"]) == 3
    assert feishu.create_batches == [
        ("main", 1),
        ("video_history", 3),
        ("channel_history", 3),
    ]
    assert "VIDEO_VIEWS_LAST_7D_INFERRED" not in feishu.tables["main"][0]["fields"]
    assert "VIDEO_VIEWS_AT_48H" not in feishu.tables["main"][0]["fields"]

    repeated, _ = service.collect(observed_at)
    assert repeated["feishu_records_changed"] == 0
    assert repeated["feishu_batch_requests"] == 0
    assert repeated["feishu_bindings_adopted"] == 0
    assert repeated["current_flag_reset_records"] == 0
    assert feishu.create_batches == [
        ("main", 1),
        ("video_history", 3),
        ("channel_history", 3),
    ]
    assert feishu.update_batches == []
    assert len(feishu.tables["main"]) == 1
    current_snapshots = [
        item
        for item in feishu.tables["channel_history"]
        if item["fields"].get("CHANNEL_CURRENT_SNAPSHOT") is True
    ]
    assert len(current_snapshots) == 1

    data.long_views = 175
    data.subscribers = 1100
    later = observed_at.replace(day=13)
    later_counts, _ = service.collect(later)
    assert later_counts["feishu_records_changed"] == 4
    assert later_counts["feishu_batch_requests"] == 4
    assert later_counts["feishu_bindings_adopted"] == 0
    assert later_counts["current_flag_reset_records"] == 1
    main = feishu.tables["main"][0]["fields"]
    assert main["VIDEO_VIEWS_LAST_7D_INFERRED"] == 75
    latest_snapshot = [
        item
        for item in feishu.tables["channel_history"]
        if item["fields"].get("CHANNEL_CURRENT_SNAPSHOT") is True
    ][0]
    assert latest_snapshot["fields"]["CHANNEL_SUBSCRIBER_SNAPSHOT_CHANGE"] == 100


def test_channel_main_uses_nearest_real_snapshot_for_48h_views(
    storage: SqlAlchemyStorage,
) -> None:
    observed_at = datetime(2026, 9, 5, 1, tzinfo=UTC)
    published_at = datetime(2026, 8, 1, tzinfo=UTC)
    target_at = published_at + timedelta(hours=48)
    with storage.transaction() as repos:
        repos.channels.upsert(
            "UC_TEST",
            title="测试频道",
            uploads_playlist_id="UU_TEST",
            timezone="Asia/Shanghai",
        )
        repos.videos.upsert(
            {
                "id": "long",
                "channel_id": "UC_TEST",
                "title": "long",
                "published_at": published_at,
                "duration_seconds": 600,
                "privacy_status": "public",
                "video_type": "长视频",
            }
        )
        for offset_minutes, views in ((-35, 75), (35, 90), (120, 120)):
            repos.videos.add_snapshot(
                video_id="long",
                observed_at=target_at + timedelta(minutes=offset_minutes),
                view_count=views,
                like_count=1,
                comment_count=1,
                raw_json={},
                source="youtube_data_api",
            )

    feishu = FakeFeishu()
    service = ChannelHistoryService(
        youtube=FakeDataApi(observed_at),
        analytics=ChannelAnalyticsCollector(FakeAnalyticsApi()),
        storage=storage,
        records=FeishuRecordService(feishu, storage, "base"),
        config=ChannelHistoryConfig(
            channel_id="UC_TEST",
            runtime_plan=_runtime_plan(),
        ),
    )

    _, details = service.collect(observed_at)

    main = feishu.tables["main"][0]["fields"]
    assert main["VIDEO_VIEWS_AT_48H"] == 90
    assert main["VIDEO_48H_SAMPLE_AGE_MINUTES"] == 2915
    assert main["VIDEO_48H_SAMPLE_AT_BEIJING"] == "2026-08-03T08:35:00+08:00"
    assert details["video_results"][0]["forty_eight_hour_sample_at"] == (
        "2026-08-03T00:35:00+00:00"
    )


def test_daily_collection_adopts_all_existing_rows_on_a_fresh_computer(
    storage: SqlAlchemyStorage, tmp_path: Any
) -> None:
    observed_at = datetime(2026, 9, 5, 1, tzinfo=UTC)
    data = FakeDataApi(observed_at)
    feishu = FakeFeishu()
    first_service = ChannelHistoryService(
        youtube=data,
        analytics=ChannelAnalyticsCollector(FakeAnalyticsApi()),
        storage=storage,
        records=FeishuRecordService(feishu, storage, "base"),
        config=ChannelHistoryConfig(
            channel_id="UC_TEST",
            runtime_plan=_runtime_plan(),
        ),
    )
    first_service.collect(observed_at)
    original_create_batches = list(feishu.create_batches)

    fresh_database = Database(f"sqlite:///{tmp_path / 'fresh-computer.db'}")
    fresh_database.create_schema_for_tests()
    fresh_storage = SqlAlchemyStorage(fresh_database)
    try:
        fresh_service = ChannelHistoryService(
            youtube=data,
            analytics=ChannelAnalyticsCollector(FakeAnalyticsApi()),
            storage=fresh_storage,
            records=FeishuRecordService(feishu, fresh_storage, "base"),
            config=ChannelHistoryConfig(
                channel_id="UC_TEST",
                runtime_plan=_runtime_plan(),
            ),
        )

        counts, _ = fresh_service.collect(observed_at)

        assert counts["feishu_bindings_adopted"] == 7
        assert counts["feishu_records_changed"] == 7
        assert counts["feishu_batch_requests"] == 3
        assert feishu.create_batches == original_create_batches
        assert len(feishu.tables["main"]) == 1
        assert len(feishu.tables["video_history"]) == 3
        assert len(feishu.tables["channel_history"]) == 3
        with fresh_storage.transaction() as repos:
            assert len(repos.bindings.list_for_table("main")) == 1
            assert len(repos.bindings.list_for_table("video_history")) == 3
            assert len(repos.bindings.list_for_table("channel_history")) == 3
    finally:
        fresh_database.dispose()


def test_incomplete_long_video_classification_omits_channel_long_totals(
    storage: SqlAlchemyStorage,
) -> None:
    observed_at = datetime(2026, 9, 5, 1, tzinfo=UTC)
    feishu = FakeFeishu()
    service = ChannelHistoryService(
        youtube=FakeDataApi(observed_at),
        analytics=ChannelAnalyticsCollector(
            FakeAnalyticsApi(confirm_long_video=False)
        ),
        storage=storage,
        records=FeishuRecordService(feishu, storage, "base"),
        config=ChannelHistoryConfig(
            channel_id="UC_TEST",
            runtime_plan=_runtime_plan(),
        ),
    )

    _, details = service.collect(observed_at)

    assert details["long_video_classification_complete"] is False
    current = [
        item
        for item in feishu.tables["channel_history"]
        if item["fields"].get("CHANNEL_CURRENT_SNAPSHOT") is True
    ][0]["fields"]
    assert "CHANNEL_LONG_VIDEO_VIEWS_PUBLIC" not in current
    assert "CHANNEL_LONG_VIDEO_COUNT" not in current


@dataclass
class _TaskConfig:
    timezone: str = "Asia/Shanghai"


class _TaskService:
    def __init__(self) -> None:
        self.config = _TaskConfig()
        self.calls = 0

    def collect(self, observed_at: datetime) -> tuple[dict[str, int], dict[str, Any]]:
        self.calls += 1
        return {"feishu_writes": 1}, {"ok": True}


def test_daily_task_catches_up_after_eight_and_uses_cursor() -> None:
    service = _TaskService()
    task = ChannelHistoryDailyTask(service, daily_collection_hour=8)  # type: ignore[arg-type]
    before_eight = datetime(2026, 9, 4, 23, tzinfo=UTC)
    skipped = task.execute(
        TaskContext(before_eight, before_eight, 1, force=False, cursor=None)
    )
    assert skipped.details["reason"] == "before_daily_collection_hour"
    assert service.calls == 0

    after_eight = datetime(2026, 9, 5, 1, tzinfo=UTC)
    completed = task.execute(
        TaskContext(after_eight, after_eight, 1, force=False, cursor=None)
    )
    assert completed.cursor == {"last_collection_date": "2026-09-05"}
    repeated = task.execute(
        TaskContext(
            after_eight,
            after_eight,
            1,
            force=False,
            cursor=completed.cursor,
        )
    )
    assert repeated.details["reason"] == "already_collected_today"
    assert service.calls == 1


def test_runtime_plan_compiles_all_enabled_shared_dictionary_mappings() -> None:
    catalog = FieldCatalog.load_builtin()
    table_mappings = {
        "main": DEFAULT_VIDEO_MAIN_MAPPING,
        "video_history": DEFAULT_VIDEO_HISTORY_MAPPING,
        "channel_history": {
            key: value
            for key, value in DEFAULT_CHANNEL_HISTORY_MAPPING.items()
            if key != "HISTORY_IMPORT_BATCH_ID"
        },
    }
    mappings = [
        ModuleFieldMapping(
            module_id="channel_history",
            standard_field_id=field_id,
            feishu_column=column,
            target_table_id=table_id,
        )
        for table_id, field_mapping in table_mappings.items()
        for field_id, column in field_mapping.items()
    ]
    raw_fields = {
        table_id: [
            {
                "field_id": f"{table_id}-{index}",
                "field_name": column,
                "type": _feishu_type(catalog.get(field_id).data_type),
                "property": {},
                "is_primary": index == 1,
            }
            for index, (field_id, column) in enumerate(field_mapping.items(), start=1)
        ]
        for table_id, field_mapping in table_mappings.items()
    }

    plan = ChannelHistoryRuntimePlan.compile(
        catalog=catalog,
        table_ids={
            "视频主表": "main",
            "视频历史数据": "video_history",
            "频道历史数据": "channel_history",
        },
        mappings=mappings,
        raw_fields_by_table_id=raw_fields,
    )

    assert len(plan.require_table("视频主表").mapping) == 20
    assert len(plan.require_table("视频历史数据").mapping) == 14
    assert len(plan.require_table("频道历史数据").mapping) == 23
    assert catalog.document.catalog_version == "2026.09.v7"
    assert catalog.get("HISTORY_IMPORT_BATCH_ID").implementation_status == "planned"


def _runtime_plan() -> ChannelHistoryRuntimePlan:
    main_fields = {
        "VIDEO_ID": 1,
        "VIDEO_TITLE": 1,
        "VIDEO_TYPE": 3,
        "VIDEO_VIEWS_PUBLIC": 2,
        "VIDEO_VIEWS_LAST_7D_INFERRED": 2,
        "VIDEO_VIEWS_AT_48H": 2,
        "VIDEO_48H_SAMPLE_AGE_MINUTES": 2,
        "VIDEO_48H_SAMPLE_AT_BEIJING": 1,
        "DATA_API_FETCHED_AT_BEIJING": 1,
        "ANALYTICS_FETCHED_AT_BEIJING": 1,
    }
    video_history_fields = {
        "DAILY_VIDEO_RECORD_ID": 1,
        "VIDEO_ID": 1,
        "VIDEO_TITLE": 1,
        "VIDEO_TYPE": 3,
        "DAILY_SNAPSHOT_DATE_BEIJING": 5,
        "ANALYTICS_DAY": 5,
        "VIDEO_VIEWS_PUBLIC": 2,
        "DAILY_RECORD_TYPE": 3,
        "ANALYTICS_VIEWS": 2,
    }
    channel_fields = {
        "DAILY_CHANNEL_RECORD_ID": 1,
        "ANALYTICS_DAY": 5,
        "CHANNEL_ID": 1,
        "DAILY_SNAPSHOT_DATE_BEIJING": 5,
        "DAILY_RECORD_TYPE": 3,
        "CHANNEL_SUBSCRIBERS_PUBLIC": 2,
        "CHANNEL_SUBSCRIBER_SNAPSHOT_CHANGE": 2,
        "CHANNEL_VIEWS_PUBLIC": 2,
        "CHANNEL_VIDEO_COUNT": 2,
        "ANALYTICS_VIEWS": 2,
        "ANALYTICS_SUB_GAINED": 2,
        "ANALYTICS_SUB_LOST": 2,
        "CHANNEL_CURRENT_SNAPSHOT": 7,
        "CHANNEL_CURRENT_ANALYTICS_DAY": 7,
        "CHANNEL_LONG_VIDEO_VIEWS_PUBLIC": 2,
        "CHANNEL_LONG_VIDEO_COUNT": 2,
        "CHANNEL_LONG_VIDEO_DAILY_VIEWS": 2,
        "SUBSCRIBER_DATA_SOURCE": 3,
        "SUBSCRIBER_DATE_BASIS": 3,
    }
    return ChannelHistoryRuntimePlan(
        (
            _table_plan("视频主表", "main", main_fields),
            _table_plan("视频历史数据", "video_history", video_history_fields),
            _table_plan("频道历史数据", "channel_history", channel_fields),
        )
    )


def _table_plan(name: str, table_id: str, fields: dict[str, int]) -> ChannelTablePlan:
    schema = FeishuTableSchema.from_api_fields(
        [
            {
                "field_id": f"fld{index}",
                "field_name": field_name,
                "type": field_type,
                "property": {},
                "is_primary": index == 1,
            }
            for index, (field_name, field_type) in enumerate(fields.items(), start=1)
        ]
    )
    return ChannelTablePlan(name, table_id, schema, {key: key for key in fields})


def _feishu_type(data_type: str) -> int:
    if data_type in {"integer", "number", "duration"}:
        return 2
    if data_type in {"date", "datetime"}:
        return 5
    if data_type == "boolean":
        return 7
    return 1


def _video(
    video_id: str,
    published_at: datetime,
    duration: str,
    views: int,
    live_status: str,
) -> VideoResource:
    return VideoResource(
        video_id=video_id,
        channel_id="UC_TEST",
        title=video_id,
        published_at=published_at,
        duration=duration,
        privacy_status="public",
        view_count=views,
        like_count=10,
        comment_count=2,
        raw={
            "id": video_id,
            "snippet": {
                "liveBroadcastContent": live_status,
                "thumbnails": {"high": {"url": f"https://example.test/{video_id}.jpg"}},
            },
        },
    )


def _table(columns: tuple[str, ...], rows: tuple[tuple[Any, ...], ...]) -> AnalyticsTable:
    return AnalyticsTable(columns=columns, rows=rows, raw={})
