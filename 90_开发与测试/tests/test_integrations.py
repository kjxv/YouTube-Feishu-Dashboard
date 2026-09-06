from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select
from youtube_feishu_dashboard.api.feishu.config_center import FeishuConfigCenter
from youtube_feishu_dashboard.api.youtube.data_api import YouTubeDataClient
from youtube_feishu_dashboard.catalog.field_catalog import FieldCatalog
from youtube_feishu_dashboard.core.errors import ConfigurationError, ExternalServiceError
from youtube_feishu_dashboard.db.models import ApiFieldCapability, ArchiveBatch
from youtube_feishu_dashboard.db.repositories import SqlAlchemyStorage
from youtube_feishu_dashboard.services.archive import ArchiveService
from youtube_feishu_dashboard.services.catalog_sync import sync_catalog_to_feishu
from youtube_feishu_dashboard.services.feishu_records import (
    EntityUpsert,
    FeishuRecordService,
    SyncResult,
)


class FakeRequest:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def execute(self) -> dict[str, Any]:
        return self.payload


class FakeYouTubeService:
    def channels(self) -> FakeYouTubeService:
        return self

    def playlistItems(self) -> FakeYouTubeService:
        return self

    def videos(self) -> FakeYouTubeService:
        return self

    def list(self, **kwargs: Any) -> FakeRequest:
        if kwargs.get("mine"):
            return FakeRequest(
                {
                    "items": [
                        {
                            "id": "UC_TEST",
                            "snippet": {"title": "测试频道"},
                            "contentDetails": {"relatedPlaylists": {"uploads": "UU_TEST"}},
                            "statistics": {
                                "viewCount": "9876",
                                "subscriberCount": "1234",
                                "videoCount": "88",
                                "hiddenSubscriberCount": False,
                            },
                        }
                    ]
                }
            )
        if kwargs.get("playlistId"):
            return FakeRequest(
                {
                    "items": [
                        {
                            "contentDetails": {
                                "videoId": "video-1",
                                "videoPublishedAt": "2026-08-31T01:02:03Z",
                            }
                        }
                    ]
                }
            )
        return FakeRequest(
            {
                "items": [
                    {
                        "id": "video-1",
                        "snippet": {
                            "channelId": "UC_TEST",
                            "title": "第一条视频",
                            "publishedAt": "2026-08-31T01:02:03Z",
                        },
                        "contentDetails": {"duration": "PT1M2S"},
                        "status": {"privacyStatus": "public"},
                        "statistics": {
                            "viewCount": "123",
                            "likeCount": "10",
                            "commentCount": "2",
                        },
                    }
                ]
            }
        )


class FakeFeishuGateway:
    def __init__(self) -> None:
        self.fail = False
        self.total = 10
        self.next_record = 1
        self.tables: dict[str, list[dict[str, Any]]] = {
            "project": [
                {"fields": {"配置键": "latest_interval_minutes", "配置值": "30", "启用": True}}
            ],
            "account": [
                {"fields": {"配置键": "youtube_channel_id", "配置值": "UC_TEST", "启用": True}}
            ],
            "mapping": [
                {
                    "fields": {
                        "模块ID": "latest_video_tracker",
                        "标准字段ID": "VIDEO_VIEWS_PUBLIC",
                        "飞书列名": "播放量",
                        "目标表ID": "latest",
                        "启用": True,
                    }
                }
            ],
        }
        self.created: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []
        self.create_batches: list[int] = []
        self.update_batches: list[int] = []

    def list_records(self, app_token: str, table_id: str) -> list[dict[str, Any]]:
        if self.fail:
            raise ExternalServiceError("模拟飞书临时失败")
        return self.tables[table_id]

    def count_records(self, app_token: str, table_id: str) -> int:
        return self.total

    def batch_create_records(
        self, app_token: str, table_id: str, fields: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        self.create_batches.append(len(fields))
        result = []
        for item in fields:
            record = {"record_id": f"rec-{self.next_record}", "fields": item}
            self.next_record += 1
            self.created.append(record)
            result.append(record)
        return result

    def batch_update_records(
        self, app_token: str, table_id: str, records: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        self.update_batches.append(len(records))
        self.updated.extend(records)
        return records

    def batch_delete_records(self, app_token: str, table_id: str, record_ids: list[str]) -> None:
        raise AssertionError("测试不应删除飞书记录")


def test_builtin_catalog_builds_request_plan_and_syncs(
    storage: SqlAlchemyStorage,
) -> None:
    catalog = FieldCatalog.load_builtin()
    plan = catalog.build_request_plan(
        "latest_video_tracker",
        ["VIDEO_ID", "VIDEO_TITLE", "VIDEO_VIEWS_PUBLIC", "ANALYTICS_DAY", "ANALYTICS_VIEWS"],
    )

    assert set(plan.data_api_parts) == {"id", "snippet", "statistics"}
    assert plan.analytics_dimensions == ("day",)
    assert plan.analytics_metrics == ("views",)
    catalog.sync_to_storage(storage)
    with storage.transaction() as repos:
        count = repos.session.scalar(select(func.count()).select_from(ApiFieldCapability))
    assert count == len(catalog.document.fields)
    assert count == 186
    assert catalog.document.catalog_version == "2026.09.v7"
    assert catalog.get("ANALYTICS_TRAFFIC_SOURCE_TYPE").official_field == (
        "insightTrafficSourceType"
    )
    assert catalog.get("REPORT_VIDEO_THUMBNAIL_IMPRESSIONS_CTR").official_field == (
        "video_thumbnail_impressions_ctr"
    )
    assert catalog.get("ANALYTICS_IMPRESSIONS").api_source == "reporting_api"
    assert catalog.get("ANALYTICS_IMPRESSIONS").official_field == (
        "video_thumbnail_impressions"
    )
    assert catalog.get("ANALYTICS_IMPRESSIONS_CTR").api_source == "reporting_api"
    assert catalog.get("ANALYTICS_FETCHED_AT").implementation_status == "tested"
    assert catalog.get("ANALYTICS_DATA_THROUGH_DATE").dependency_field_ids == (
        "ANALYTICS_DAY",
    )
    assert catalog.get("REPORTING_DATA_THROUGH_DATE").dependency_field_ids == (
        "REPORT_DATE",
    )


def test_youtube_data_client_normalizes_channel_uploads_and_statistics() -> None:
    client = YouTubeDataClient(FakeYouTubeService())

    channel = client.get_channel()
    ids = client.list_upload_video_ids(channel.uploads_playlist_id)
    videos = client.list_videos(ids)

    assert channel.channel_id == "UC_TEST"
    assert channel.view_count == 9876
    assert channel.subscriber_count == 1234
    assert channel.video_count == 88
    assert channel.hidden_subscriber_count is False
    assert ids == ["video-1"]
    assert videos[0].view_count == 123
    assert videos[0].published_at.tzinfo is not None


def test_config_center_falls_back_to_last_valid_cache(storage: SqlAlchemyStorage) -> None:
    now = datetime(2026, 8, 31, 8, 0, tzinfo=UTC)
    gateway = FakeFeishuGateway()
    center = FeishuConfigCenter(
        gateway=gateway,
        storage=storage,
        catalog=FieldCatalog.load_builtin(),
        app_token="base-token",
        project_config_table_id="project",
        account_config_table_id="account",
        module_mapping_table_id="mapping",
        cache_ttl_minutes=60,
    )

    live = center.load(now=now)
    gateway.fail = True
    cached = center.load(now=now)

    assert live.source == "feishu"
    assert cached.source == "cache"
    assert cached.version_hash == live.version_hash
    assert cached.fallback_error is not None


def test_record_upsert_is_idempotent_and_archive_is_plan_only(
    storage: SqlAlchemyStorage,
) -> None:
    gateway = FakeFeishuGateway()
    records = FeishuRecordService(gateway, storage, "base-token")

    first = records.upsert_entity(
        table_id="latest",
        entity_type="video_latest",
        entity_key="video-1",
        fields={"视频ID": "video-1", "播放量": 123},
    )
    second = records.upsert_entity(
        table_id="latest",
        entity_type="video_latest",
        entity_key="video-1",
        fields={"视频ID": "video-1", "播放量": 123},
    )
    third = records.upsert_entity(
        table_id="latest",
        entity_type="video_latest",
        entity_key="video-1",
        fields={"视频ID": "video-1", "播放量": 124},
    )

    assert (first.action, second.action, third.action) == ("created", "unchanged", "updated")
    gateway.total = 19000
    archive = ArchiveService(
        gateway=gateway,
        storage=storage,
        app_token="base-token",
        row_threshold=19000,
    )
    decision = archive.ensure_capacity(module_id="latest_video_tracker", table_id="latest")
    repeated = archive.ensure_capacity(module_id="latest_video_tracker", table_id="latest")
    assert decision.status == "archive_required"
    assert decision.batch_id == repeated.batch_id
    with storage.transaction() as repos:
        batches = list(repos.session.scalars(select(ArchiveBatch)))
    assert len(batches) == 1
    assert batches[0].status == "required"


def test_record_batch_upsert_groups_requests_and_preserves_order(
    storage: SqlAlchemyStorage,
) -> None:
    gateway = FakeFeishuGateway()
    records = FeishuRecordService(gateway, storage, "base-token")
    entities = [
        EntityUpsert("video", f"video-{index}", {"播放量": index})
        for index in range(1, 6)
    ]

    created = records.upsert_entities(
        table_id="latest", entities=entities, batch_size=2
    )
    unchanged = records.upsert_entities(
        table_id="latest", entities=entities, batch_size=2
    )
    changed_entities = [
        *entities[:2],
        EntityUpsert("video", "video-3", {"播放量": 30}),
        *entities[3:],
    ]
    updated = records.upsert_entities(
        table_id="latest", entities=changed_entities, batch_size=2
    )

    assert [item.action for item in created] == ["created"] * 5
    assert [item.record_id for item in created] == [f"rec-{index}" for index in range(1, 6)]
    assert [item.action for item in unchanged] == ["unchanged"] * 5
    assert [item.action for item in updated] == [
        "unchanged",
        "unchanged",
        "updated",
        "unchanged",
        "unchanged",
    ]
    assert gateway.create_batches == [2, 2, 1]
    assert gateway.update_batches == [1]


def test_record_batch_upsert_rejects_duplicate_entity_keys(
    storage: SqlAlchemyStorage,
) -> None:
    records = FeishuRecordService(FakeFeishuGateway(), storage, "base-token")

    with pytest.raises(ValueError, match="重复"):
        records.upsert_entities(
            table_id="latest",
            entities=[
                EntityUpsert("video", "video-1", {"播放量": 1}),
                EntityUpsert("video", "video-1", {"播放量": 2}),
            ],
        )


def test_record_batch_upsert_adopts_unique_remote_record_before_update(
    storage: SqlAlchemyStorage,
) -> None:
    gateway = FakeFeishuGateway()
    gateway.tables["latest"] = [
        {
            "record_id": "rec-existing",
            "fields": {"唯一键": [{"text": "video-1"}], "播放量": 123},
        }
    ]
    records = FeishuRecordService(gateway, storage, "base-token")

    adopted = records.upsert_entities(
        table_id="latest",
        entities=[EntityUpsert("video", "video-1", {"唯一键": "video-1", "播放量": 124})],
        remote_key_field="唯一键",
    )
    repeated = records.upsert_entities(
        table_id="latest",
        entities=[EntityUpsert("video", "video-1", {"唯一键": "video-1", "播放量": 124})],
        remote_key_field="唯一键",
    )

    assert adopted == [SyncResult("updated", "rec-existing", binding_adopted=True)]
    assert repeated == [SyncResult("unchanged", "rec-existing")]
    assert gateway.created == []
    assert gateway.update_batches == [1]
    with storage.transaction() as repos:
        binding = repos.bindings.get("latest", "video", "video-1")
    assert binding is not None
    assert binding.record_id == "rec-existing"
    assert binding.last_payload_hash is not None


def test_record_batch_upsert_refuses_duplicate_remote_business_keys(
    storage: SqlAlchemyStorage,
) -> None:
    gateway = FakeFeishuGateway()
    gateway.tables["latest"] = [
        {"record_id": "rec-1", "fields": {"唯一键": "video-1"}},
        {"record_id": "rec-2", "fields": {"唯一键": "video-1"}},
    ]
    records = FeishuRecordService(gateway, storage, "base-token")

    with pytest.raises(ExternalServiceError, match="存在重复记录"):
        records.upsert_entities(
            table_id="latest",
            entities=[EntityUpsert("video", "video-1", {"唯一键": "video-1"})],
            remote_key_field="唯一键",
        )

    assert gateway.created == []
    assert gateway.updated == []
    with storage.transaction() as repos:
        assert repos.bindings.get("latest", "video", "video-1") is None


def test_catalog_sync_updates_existing_and_creates_missing() -> None:
    gateway = FakeFeishuGateway()
    gateway.tables["catalog"] = [
        {
            "record_id": "rec-existing",
            "fields": {
                "标准字段ID": "VIDEO_ID",
                "中文名称": "旧名称",
                "用户自定义标签": "必须保留",
            },
        }
    ]

    result = sync_catalog_to_feishu(
        gateway=gateway,
        app_token="base-token",
        table_id="catalog",
        catalog=FieldCatalog.load_builtin(),
    )

    assert result["updated"] == 1
    assert result["created"] == 185
    assert result["api_fields"] == 129
    assert result["system_fields"] == 57
    assert gateway.updated[0]["record_id"] == "rec-existing"
    assert "用户自定义标签" not in gateway.updated[0]["fields"]


def test_catalog_sync_refuses_duplicate_standard_field_ids() -> None:
    gateway = FakeFeishuGateway()
    gateway.tables["catalog"] = [
        {"record_id": "rec-1", "fields": {"标准字段ID": "VIDEO_ID"}},
        {"record_id": "rec-2", "fields": {"标准字段ID": "VIDEO_ID"}},
    ]

    with pytest.raises(ConfigurationError, match="重复的标准字段ID"):
        sync_catalog_to_feishu(
            gateway=gateway,
            app_token="base-token",
            table_id="catalog",
            catalog=FieldCatalog.load_builtin(),
        )
