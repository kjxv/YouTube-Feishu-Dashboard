from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from yfd_latest_video_tracker.manifest import API_FIELD_IDS
from yfd_latest_video_tracker.service import (
    LatestTrackerConfig,
    LatestVideoTrackerService,
    merge_field_mapping,
    parse_iso_duration_seconds,
)
from youtube_feishu_dashboard.api.youtube.schemas import ChannelResource, VideoResource
from youtube_feishu_dashboard.catalog.field_catalog import FieldCatalog
from youtube_feishu_dashboard.db.models import VideoSnapshot
from youtube_feishu_dashboard.db.repositories import SqlAlchemyStorage
from youtube_feishu_dashboard.services.archive import ArchiveService
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
        return ["video-latest"]

    def list_videos(self, video_ids: Any, *, parts: Any = None) -> list[VideoResource]:
        return [
            VideoResource(
                video_id="video-latest",
                channel_id="UC_TEST",
                title="最新视频",
                published_at=self.published_at,
                duration="PT2M3S",
                privacy_status="public",
                view_count=self.view_count,
                like_count=10,
                comment_count=2,
                raw={"id": "video-latest", "statistics": {"viewCount": str(self.view_count)}},
            )
        ]


class FakeFeishu:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.total_override: int | None = None

    def count_records(self, app_token: str, table_id: str) -> int:
        return self.total_override if self.total_override is not None else len(self.created)

    def batch_create_records(
        self, app_token: str, table_id: str, fields: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        output = []
        for item in fields:
            record = {"record_id": f"rec-{len(self.created) + 1}", "fields": item}
            self.created.append(record)
            output.append(record)
        return output

    def batch_update_records(
        self, app_token: str, table_id: str, records: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        return records

    def list_records(self, app_token: str, table_id: str) -> list[dict[str, Any]]:
        return self.created

    def batch_delete_records(self, app_token: str, table_id: str, record_ids: list[str]) -> None:
        raise AssertionError("实时追踪不应直接删除记录")


def build_service(
    storage: SqlAlchemyStorage, youtube: FakeYouTube, feishu: FakeFeishu
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
            target_table_id="latest",
            field_mapping=merge_field_mapping(),
        ),
        request_plan=plan,
    )


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
    assert len(feishu.created) == 2
    assert feishu.created[0]["fields"]["本周期新增播放量"] == 0
    assert feishu.created[1]["fields"]["本周期新增播放量"] == 50
    with storage.transaction() as repos:
        count = repos.session.scalar(select(func.count()).select_from(VideoSnapshot))
    assert count == 2


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
    assert details["reason"] == "latest_video_outside_tracking_window"
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

    assert counts == {"videos": 1, "snapshots": 1, "feishu_writes": 0}
    assert details["reason"] == "feishu_archive_required"
    assert feishu.created == []
