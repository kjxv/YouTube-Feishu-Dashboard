from __future__ import annotations

from typing import Any

import pytest
from yfd_channel_history.feishu_setup import (
    Channel48HourFeishuSetup,
    ChannelAnalyticsDateFieldsSetup,
    ChannelHistoryFeishuSwitch,
)
from yfd_channel_history.legacy_time_cleanup import LegacyTimeFieldCleaner
from yfd_latest_video_tracker.feishu_setup import LatestVideoMilestoneFeishuSetup
from youtube_feishu_dashboard.core.errors import ConfigurationError


class FakeAdminGateway:
    def __init__(self) -> None:
        self.fields: dict[str, list[dict[str, Any]]] = {
            "video-main": [{"field_name": "Video ID", "type": 1, "field_id": "fld-1"}],
            "mappings": [
                {"field_name": "映射名称", "type": 1},
                {"field_name": "模块ID", "type": 1},
                {"field_name": "模块中文名", "type": 1},
                {"field_name": "目标表中文名", "type": 1},
                {"field_name": "目标表ID", "type": 1},
                {"field_name": "飞书列名", "type": 1},
                {"field_name": "标准字段ID", "type": 1},
                {"field_name": "标准字段中文名", "type": 1},
                {"field_name": "API类型", "type": 1},
                {"field_name": "API官方字段", "type": 1},
                {"field_name": "写入方式", "type": 1},
                {"field_name": "实现状态", "type": 1},
                {"field_name": "启用", "type": 7},
                {"field_name": "备注", "type": 1},
            ],
        }
        self.records: dict[str, list[dict[str, Any]]] = {
            "video-main": [],
            "mappings": [],
        }
        self.created_fields: list[str] = []

    def list_tables(self, app_token: str) -> list[dict[str, Any]]:
        return []

    def create_table(
        self,
        app_token: str,
        *,
        name: str,
        fields: list[dict[str, Any]],
        default_view_name: str = "默认视图",
    ) -> dict[str, Any]:
        raise AssertionError("not used")

    def list_fields(self, app_token: str, table_id: str) -> list[dict[str, Any]]:
        return list(self.fields[table_id])

    def create_field(
        self,
        app_token: str,
        table_id: str,
        *,
        field_name: str,
        field_type: int,
        property: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        field = {"field_name": field_name, "type": field_type, "field_id": field_name}
        self.fields[table_id].append(field)
        self.created_fields.append(field_name)
        return field

    def update_field(
        self,
        app_token: str,
        table_id: str,
        field_id: str,
        *,
        field_name: str,
        field_type: int,
        property: dict[str, Any] | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        for field in self.fields[table_id]:
            if str(field.get("field_id")) == field_id:
                field.update(
                    {
                        "field_name": field_name,
                        "type": field_type,
                        "property": property or {},
                    }
                )
                if description is not None:
                    field["description"] = description
                return field
        raise AssertionError(f"unknown field: {table_id}/{field_id}")

    def list_records(self, app_token: str, table_id: str) -> list[dict[str, Any]]:
        return list(self.records[table_id])

    def count_records(self, app_token: str, table_id: str) -> int:
        return len(self.records[table_id])

    def batch_create_records(
        self, app_token: str, table_id: str, fields: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in fields:
            record = {
                "record_id": f"rec-{len(self.records[table_id]) + 1}",
                "fields": dict(item),
            }
            self.records[table_id].append(record)
            result.append(record)
        return result

    def batch_update_records(
        self, app_token: str, table_id: str, records: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        by_id = {str(item["record_id"]): item for item in self.records[table_id]}
        for update in records:
            by_id[str(update["record_id"])]["fields"].update(update["fields"])
        return records

    def batch_delete_records(
        self, app_token: str, table_id: str, record_ids: list[str]
    ) -> None:
        ids = set(record_ids)
        self.records[table_id] = [
            item for item in self.records[table_id] if item.get("record_id") not in ids
        ]

    def delete_field(self, app_token: str, table_id: str, field_id: str) -> None:
        self.fields[table_id] = [
            item for item in self.fields[table_id] if str(item.get("field_id")) != field_id
        ]


def service(gateway: FakeAdminGateway) -> Channel48HourFeishuSetup:
    return Channel48HourFeishuSetup(
        gateway=gateway,
        app_token="app",
        video_main_table_id="video-main",
        mapping_table_id="mappings",
    )


def test_channel_48h_setup_creates_then_reuses_without_duplicates() -> None:
    gateway = FakeAdminGateway()

    first = service(gateway).apply()
    second = service(gateway).apply()

    assert first.created_fields == (
        "发布后48小时播放量",
        "48小时样本发布后分钟数",
        "48小时样本采集时间（太平洋时间）",
    )
    assert first.created_mappings == 3
    assert second.created_fields == ()
    assert second.created_mappings == 0
    assert second.updated_mappings == 0
    assert second.unchanged_mappings == 3
    assert len(gateway.records["mappings"]) == 3


def test_channel_48h_setup_updates_disabled_existing_mapping() -> None:
    gateway = FakeAdminGateway()
    service(gateway).apply()
    gateway.records["mappings"][0]["fields"]["启用"] = False

    result = service(gateway).apply()

    assert result.updated_mappings == 1
    assert gateway.records["mappings"][0]["fields"]["启用"] is True


def test_channel_48h_setup_stops_before_writes_on_wrong_field_type() -> None:
    gateway = FakeAdminGateway()
    gateway.fields["video-main"].append(
        {"field_name": "发布后48小时播放量", "type": 1, "field_id": "wrong"}
    )

    with pytest.raises(ConfigurationError, match="程序要求 2"):
        service(gateway).apply()

    assert gateway.created_fields == []
    assert gateway.records["mappings"] == []


def test_channel_48h_setup_stops_before_writes_on_duplicate_mapping_identity() -> None:
    gateway = FakeAdminGateway()
    duplicate_fields = {
        "映射名称": "重复",
        "模块ID": "channel_history",
        "目标表ID": "video-main",
        "飞书列名": "发布后48小时播放量",
        "标准字段ID": "VIDEO_VIEWS_AT_48H",
        "启用": True,
    }
    gateway.records["mappings"] = [
        {"record_id": "rec-1", "fields": duplicate_fields},
        {"record_id": "rec-2", "fields": duplicate_fields},
    ]

    with pytest.raises(ConfigurationError, match="存在重复"):
        service(gateway).apply()

    assert gateway.created_fields == []


def test_channel_analytics_date_setup_migrates_fields_and_mappings_idempotently() -> None:
    gateway = FakeAdminGateway()
    for table_id in ("video-history", "channel-history"):
        gateway.fields[table_id] = [
            {"field_name": "统计日期", "type": 5, "field_id": f"{table_id}-stat"},
            {
                "field_name": "数据截止日期",
                "type": 5,
                "field_id": f"{table_id}-cutoff-day",
            },
            {
                "field_name": "记录日期（北京时间）",
                "type": 5,
                "field_id": f"{table_id}-record-day",
            },
            {
                "field_name": "Analytics API 数据获取时间（北京时间）",
                "type": 1,
                "field_id": f"{table_id}-analytics-fetched",
            },
            {
                "field_name": "Analytics API 数据截止时间（北京时间）",
                "type": 1,
                "field_id": f"{table_id}-analytics-through",
            },
        ]
        gateway.records[table_id] = []
        table_name = "视频历史数据" if table_id == "video-history" else "频道历史数据"
        for standard_id, column in (
            ("ANALYTICS_DAY", "统计日期"),
            ("DAILY_SNAPSHOT_DATE_BEIJING", "记录日期（北京时间）"),
            (
                "ANALYTICS_DATA_THROUGH_AT_BEIJING",
                "Analytics API 数据截止时间（北京时间）",
            ),
        ):
            gateway.records["mappings"].append(
                {
                    "record_id": f"{table_id}-{standard_id}",
                    "fields": {
                        "模块ID": "channel_history",
                        "模块中文名": "频道每日统计（长视频）",
                        "目标表中文名": table_name,
                        "目标表ID": table_id,
                        "飞书列名": column,
                        "标准字段ID": standard_id,
                        "启用": True,
                    },
                }
            )
    setup = ChannelAnalyticsDateFieldsSetup(
        gateway=gateway,
        app_token="app",
        history_table_ids={
            "视频历史数据": "video-history",
            "频道历史数据": "channel-history",
        },
        mapping_table_id="mappings",
    )

    first = setup.apply()
    second = setup.apply()

    assert len(first.created_fields) == 6
    assert first.renamed_fields == ()
    assert first.created_mappings == 6
    assert second.created_fields == ()
    assert second.renamed_fields == ()
    assert second.created_mappings == 0
    active = {
        (item["fields"]["目标表ID"], item["fields"]["标准字段ID"]): item["fields"]
        for item in gateway.records["mappings"]
    }
    for table_id in ("video-history", "channel-history"):
        assert active[(table_id, "DAILY_DATA_DATE_PACIFIC")]["飞书列名"] == (
            "数据日期（太平洋时间）"
        )
        assert active[(table_id, "DAILY_DATA_DATE_PACIFIC")]["启用"] is True
        assert active[(table_id, "ANALYTICS_DAY")]["飞书列名"] == "统计日期"
        assert active[(table_id, "ANALYTICS_DAY")]["启用"] is False
        assert active[(table_id, "DAILY_SNAPSHOT_DATE_BEIJING")]["飞书列名"] == (
            "记录日期（北京时间）"
        )
        assert active[(table_id, "DAILY_SNAPSHOT_DATE_BEIJING")]["启用"] is False
        assert active[(table_id, "ANALYTICS_DATA_THROUGH_AT_BEIJING")]["启用"] is False
        assert active[(table_id, "ANALYTICS_FETCHED_AT_PACIFIC")]["启用"] is True
        assert "ANALYTICS_DATA_THROUGH_AT_PACIFIC" not in {
            standard_id for mapped_table, standard_id in active if mapped_table == table_id
        }
        field_names = {item["field_name"] for item in gateway.fields[table_id]}
        assert "统计日期" in field_names
        assert "记录日期（北京时间）" in field_names
        assert "数据日期（太平洋时间）" in field_names


def test_legacy_time_cleanup_previews_backs_up_and_deletes_exact_targets(
    tmp_path: Any,
) -> None:
    gateway = FakeAdminGateway()
    required = {
        "video-main": (
            "Data API 数据获取时间（太平洋时间）",
            "Analytics API 数据获取时间（太平洋时间）",
            "近7天采样起点（太平洋时间）",
            "近7天采样终点（太平洋时间）",
            "48小时样本采集时间（太平洋时间）",
        ),
        "video-history": (
            "数据日期（太平洋时间）",
            "Data API 数据获取时间（太平洋时间）",
            "Analytics API 数据获取时间（太平洋时间）",
        ),
        "channel-history": (
            "数据日期（太平洋时间）",
            "Data API 数据获取时间（太平洋时间）",
            "Analytics API 数据获取时间（太平洋时间）",
        ),
    }
    for table_id, names in required.items():
        gateway.fields[table_id] = [
            {"field_name": name, "type": 1, "field_id": f"{table_id}-{index}"}
            for index, name in enumerate(names)
        ]
        gateway.records[table_id] = []
    gateway.fields["video-main"].append(
        {"field_name": "Data API 数据获取时间（北京时间）", "type": 1, "field_id": "old-main"}
    )
    gateway.records["video-main"] = [
        {
            "record_id": "video-1",
            "fields": {"Data API 数据获取时间（北京时间）": "2026-09-22T08:00:00+08:00"},
        }
    ]
    gateway.fields["video-history"].append(
        {"field_name": "统计日期", "type": 5, "field_id": "old-video-date"}
    )
    gateway.fields["channel-history"].append(
        {
            "field_name": "Analytics API 数据截止时间（太平洋时间）",
            "type": 1,
            "field_id": "old-channel-cutoff",
        }
    )
    active_mappings = {
        "video-main": {
            "DATA_API_FETCHED_AT_PACIFIC": "Data API 数据获取时间（太平洋时间）",
            "ANALYTICS_FETCHED_AT_PACIFIC": "Analytics API 数据获取时间（太平洋时间）",
            "VIDEO_7D_SAMPLE_START_AT_PACIFIC": "近7天采样起点（太平洋时间）",
            "VIDEO_7D_SAMPLE_END_AT_PACIFIC": "近7天采样终点（太平洋时间）",
            "VIDEO_48H_SAMPLE_AT_PACIFIC": "48小时样本采集时间（太平洋时间）",
        },
        "video-history": {
            "DAILY_DATA_DATE_PACIFIC": "数据日期（太平洋时间）",
            "DATA_API_FETCHED_AT_PACIFIC": "Data API 数据获取时间（太平洋时间）",
            "ANALYTICS_FETCHED_AT_PACIFIC": "Analytics API 数据获取时间（太平洋时间）",
        },
        "channel-history": {
            "DAILY_DATA_DATE_PACIFIC": "数据日期（太平洋时间）",
            "DATA_API_FETCHED_AT_PACIFIC": "Data API 数据获取时间（太平洋时间）",
            "ANALYTICS_FETCHED_AT_PACIFIC": "Analytics API 数据获取时间（太平洋时间）",
        },
    }
    gateway.records["mappings"] = [
        {
            "record_id": f"active-{table_id}-{standard_id}",
            "fields": {
                "模块ID": "channel_history",
                "目标表ID": table_id,
                "标准字段ID": standard_id,
                "飞书列名": column,
                "启用": True,
            },
        }
        for table_id, mappings in active_mappings.items()
        for standard_id, column in mappings.items()
    ] + [
        {
            "record_id": "old-mapping",
            "fields": {
                "模块ID": "channel_history",
                "目标表ID": "video-history",
                "标准字段ID": "ANALYTICS_DAY",
                "启用": False,
            },
        }
    ]
    cleaner = LegacyTimeFieldCleaner(
        gateway=gateway,
        app_token="app",
        table_ids={
            "视频主表": "video-main",
            "视频历史数据": "video-history",
            "频道历史数据": "channel-history",
        },
        mapping_table_id="mappings",
        backup_file=tmp_path / "legacy.json",
    )

    preview = cleaner.run(
        expected_field_count=None, expected_mapping_count=None, apply=False
    )
    assert preview.fields_to_delete == 3
    assert preview.mappings_to_delete == 1
    assert preview.records_with_legacy_values == 1
    assert not (tmp_path / "legacy.json").exists()
    with pytest.raises(ConfigurationError, match="预期删除"):
        cleaner.run(expected_field_count=2, expected_mapping_count=1, apply=True)

    result = cleaner.run(expected_field_count=3, expected_mapping_count=1, apply=True)
    assert result.fields_deleted == 3
    assert result.mappings_deleted == 1
    assert (tmp_path / "legacy.json").exists()
    assert "2026-09-22T08:00:00+08:00" in (tmp_path / "legacy.json").read_text(
        encoding="utf-8"
    )
    assert len(gateway.records["mappings"]) == 11
    assert all(
        "北京时间" not in str(field.get("field_name"))
        for fields in gateway.fields.values()
        for field in fields
    )


def test_channel_history_switch_updates_existing_value_and_is_idempotent() -> None:
    gateway = FakeAdminGateway()
    gateway.fields["project-config"] = [
        {"field_name": "配置键", "type": 1},
        {"field_name": "配置值", "type": 1},
        {"field_name": "启用", "type": 7},
    ]
    gateway.records["project-config"] = [
        {
            "record_id": "switch",
            "fields": {
                "配置键": "channel_history_enabled",
                "配置值": "false",
                "启用": True,
            },
        }
    ]
    switch = ChannelHistoryFeishuSwitch(
        gateway=gateway,
        app_token="app",
        project_config_table_id="project-config",
    )

    first = switch.set_enabled(True)
    second = switch.set_enabled(True)

    assert first.updated is True
    assert second.unchanged is True
    assert gateway.records["project-config"][0]["fields"]["配置值"] == "true"


def test_channel_history_switch_rejects_duplicate_config_rows() -> None:
    gateway = FakeAdminGateway()
    gateway.fields["project-config"] = [
        {"field_name": "配置键", "type": 1},
        {"field_name": "配置值", "type": 1},
        {"field_name": "启用", "type": 7},
    ]
    row = {
        "fields": {
            "配置键": "channel_history_enabled",
            "配置值": "false",
            "启用": True,
        }
    }
    gateway.records["project-config"] = [
        {"record_id": "one", **row},
        {"record_id": "two", **row},
    ]

    with pytest.raises(ConfigurationError, match="存在多条"):
        ChannelHistoryFeishuSwitch(
            gateway=gateway,
            app_token="app",
            project_config_table_id="project-config",
        ).set_enabled(True)


def test_latest_milestone_setup_creates_all_fields_and_is_idempotent() -> None:
    gateway = FakeAdminGateway()
    setup = LatestVideoMilestoneFeishuSetup(
        gateway=gateway,
        app_token="app",
        video_main_table_id="video-main",
        mapping_table_id="mappings",
    )

    first = setup.apply()
    second = setup.apply()

    assert len(first.created_fields) == 21
    assert first.created_mappings == 21
    assert second.created_fields == ()
    assert second.created_mappings == 0
    assert second.updated_mappings == 0
    assert second.unchanged_mappings == 21
    assert len(gateway.records["mappings"]) == 21


def test_latest_milestone_setup_reuses_existing_view_columns() -> None:
    gateway = FakeAdminGateway()
    for hour in (1, 3, 6, 12, 24, 48, 72):
        gateway.fields["video-main"].append(
            {
                "field_name": f"发布{hour}小时播放量",
                "type": 2,
                "field_id": f"views-{hour}",
            }
        )

    result = LatestVideoMilestoneFeishuSetup(
        gateway=gateway,
        app_token="app",
        video_main_table_id="video-main",
        mapping_table_id="mappings",
    ).apply()

    assert len(result.reused_fields) == 7
    assert len(result.created_fields) == 14
    assert result.created_mappings == 21
