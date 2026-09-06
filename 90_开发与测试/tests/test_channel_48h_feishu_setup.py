from __future__ import annotations

from typing import Any

import pytest
from yfd_channel_history.feishu_setup import (
    Channel48HourFeishuSetup,
    ChannelHistoryFeishuSwitch,
)
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
        raise AssertionError("not used")

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
        raise AssertionError("not used")


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
        "48小时样本采集时间（北京时间）",
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
