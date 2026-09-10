from __future__ import annotations

from pathlib import Path
from typing import Any

from youtube_feishu_dashboard.catalog.field_catalog import FieldCatalog
from youtube_feishu_dashboard.services.config_center_bootstrap import (
    ConfigCenterBootstrapper,
    load_builtin_config_center_schema,
)
from youtube_feishu_dashboard.services.config_center_critical_fields import (
    ConfigCenterCriticalFieldMarker,
)
from youtube_feishu_dashboard.services.config_center_localization import (
    ConfigCenterLocalizationService,
)


class FakeAdminGateway:
    def __init__(self) -> None:
        self.tables: dict[str, str] = {}
        self.fields: dict[str, list[dict[str, Any]]] = {}
        self.records: dict[str, list[dict[str, Any]]] = {}
        self._next_record = 1

    def list_tables(self, app_token: str) -> list[dict[str, Any]]:
        return [{"name": name, "table_id": table_id} for name, table_id in self.tables.items()]

    def create_table(
        self,
        app_token: str,
        *,
        name: str,
        fields: list[dict[str, Any]],
        default_view_name: str = "默认视图",
    ) -> dict[str, Any]:
        table_id = f"tbl-{len(self.tables) + 1}"
        self.tables[name] = table_id
        self.fields[table_id] = [
            {**field, "is_primary": index == 0, "field_id": f"fld-{index + 1}"}
            for index, field in enumerate(fields)
        ]
        self.records[table_id] = []
        return {"table_id": table_id}

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
        field = {
            "field_name": field_name,
            "type": field_type,
            "is_primary": False,
            "field_id": f"fld-{len(self.fields[table_id]) + 1}",
        }
        self.fields[table_id].append(field)
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
        field = next(item for item in self.fields[table_id] if item["field_id"] == field_id)
        old_name = field["field_name"]
        field["field_name"] = field_name
        field["type"] = field_type
        if description is not None:
            field["description"] = description
        for record in self.records[table_id]:
            if old_name in record["fields"]:
                record["fields"][field_name] = record["fields"].pop(old_name)
        return field

    def list_records(self, app_token: str, table_id: str) -> list[dict[str, Any]]:
        return list(self.records[table_id])

    def count_records(self, app_token: str, table_id: str) -> int:
        return len(self.records[table_id])

    def batch_create_records(
        self, app_token: str, table_id: str, fields: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        created: list[dict[str, Any]] = []
        for item in fields:
            record = {"record_id": f"rec-{self._next_record}", "fields": dict(item)}
            self._next_record += 1
            self.records[table_id].append(record)
            created.append(record)
        return created

    def batch_update_records(
        self, app_token: str, table_id: str, records: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        by_id = {item["record_id"]: item for item in self.records[table_id]}
        for update in records:
            by_id[update["record_id"]]["fields"].update(update["fields"])
        return records

    def batch_delete_records(self, app_token: str, table_id: str, record_ids: list[str]) -> None:
        self.records[table_id] = [
            item for item in self.records[table_id] if item["record_id"] not in record_ids
        ]


def test_config_center_bootstrap_is_idempotent_and_preserves_env_secret(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "YFD_FEISHU_APP_SECRET=keep-this-secret\n"
        "YFD_FEISHU_API_FIELD_TABLE_ID=\n"
        "YFD_FEISHU_MODULE_MAPPING_TABLE_ID=\n"
        "YFD_FEISHU_PROJECT_CONFIG_TABLE_ID=\n"
        "YFD_FEISHU_ACCOUNT_CONFIG_TABLE_ID=\n",
        encoding="utf-8",
    )
    gateway = FakeAdminGateway()
    bootstrapper = ConfigCenterBootstrapper(
        gateway=gateway,
        app_token="base-token",
        catalog=FieldCatalog.load_builtin(),
        env_file=env_file,
        latest_video_main_table_id="tbl-main",
        latest_video_snapshot_table_id="tbl-snapshot",
        latest_video_comparison_table_id="tbl-comparison",
    )

    first = bootstrapper.bootstrap()

    assert set(first.created_tables) == {
        "API字段字典",
        "模块字段需求与映射",
        "数据项目配置",
        "账号非敏感配置",
    }
    assert first.seed_records_created["模块字段需求与映射"] == 66
    assert first.catalog_sync["created"] > 40
    env_text = env_file.read_text(encoding="utf-8")
    assert "YFD_FEISHU_APP_SECRET=keep-this-secret" in env_text
    assert "YFD_FEISHU_API_FIELD_TABLE_ID=tbl-1" in env_text

    project_id = gateway.tables["数据项目配置"]
    gateway.records[project_id][0]["fields"]["配置值"] = "120"
    catalog_id = gateway.tables["API字段字典"]
    availability_field = next(
        item
        for item in gateway.fields[catalog_id]
        if item["field_name"] == "当前可用状态"
    )
    availability_field["field_name"] = "实现状态"
    mapping_id = gateway.tables["模块字段需求与映射"]
    lookup_columns = {
        "模块中文名",
        "目标表中文名",
        "标准字段中文名",
        "API类型",
        "API官方字段",
        "写入方式",
        "实现状态",
        "备注",
    }
    for field in gateway.fields[mapping_id]:
        if field["field_name"] == "映射名称":
            field["type"] = 20
        elif field["field_name"] in lookup_columns:
            field["type"] = 19
    gateway.records[mapping_id][0]["fields"]["备注"] = "用户的查找引用结果"
    second = bootstrapper.bootstrap()

    assert not second.created_tables
    assert set(second.reused_tables) == set(first.created_tables)
    assert all(count == 0 for count in second.seed_records_created.values())
    assert gateway.records[project_id][0]["fields"]["配置值"] == "120"
    assert gateway.records[mapping_id][0]["fields"]["备注"] == "用户的查找引用结果"
    assert not any(
        item["field_name"] == "当前可用状态" for item in gateway.fields[catalog_id]
    )
    assert any(item["field_name"] == "实现状态" for item in gateway.fields[catalog_id])
    assert all(
        "实现状态" in item["fields"] for item in gateway.records[catalog_id]
    )
    assert second.catalog_sync["api_fields"] == 129
    assert second.catalog_sync["system_fields"] == 75


def test_mapping_lookup_columns_are_compatible_and_not_written() -> None:
    specs = {item.name: item for item in load_builtin_config_center_schema()}
    mapping_spec = specs["模块字段需求与映射"]
    by_name = {item.name: item for item in mapping_spec.fields}

    assert by_name["标准字段中文名"].accepts_type(19)
    assert by_name["备注"].accepts_type(20)
    assert not by_name["标准字段ID"].accepts_type(19)

    gateway = FakeAdminGateway()
    bootstrapper = ConfigCenterBootstrapper(
        gateway=gateway,
        app_token="base-token",
        catalog=FieldCatalog.load_builtin(),
        env_file=Path("unused.env"),
        latest_video_main_table_id="tbl-main",
        latest_video_snapshot_table_id="tbl-snapshot",
        latest_video_comparison_table_id="tbl-comparison",
    )
    bootstrapper.bootstrap(write_env=False)
    mapping_id = gateway.tables["模块字段需求与映射"]
    removed = gateway.records[mapping_id].pop()
    removed_field_id = removed["fields"]["标准字段ID"]
    read_only_names = {
        "映射名称",
        "模块中文名",
        "目标表中文名",
        "标准字段中文名",
        "API类型",
        "API官方字段",
        "写入方式",
        "实现状态",
        "备注",
    }
    for field in gateway.fields[mapping_id]:
        if field["field_name"] in read_only_names:
            field["type"] = 20 if field["field_name"] == "映射名称" else 19

    result = bootstrapper.bootstrap(write_env=False)

    assert result.seed_records_created["模块字段需求与映射"] == 1
    recreated = next(
        item
        for item in gateway.records[mapping_id]
        if item["fields"].get("标准字段ID") == removed_field_id
    )
    assert read_only_names.isdisjoint(recreated["fields"])
    assert {
        "模块ID",
        "目标表ID",
        "飞书列名",
        "标准字段ID",
        "启用",
    }.issubset(recreated["fields"])


def test_critical_field_marker_only_describes_schema_marked_fields(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("YFD_FEISHU_APP_SECRET=keep\n", encoding="utf-8")
    gateway = FakeAdminGateway()
    ConfigCenterBootstrapper(
        gateway=gateway,
        app_token="base-token",
        catalog=FieldCatalog.load_builtin(),
        env_file=env_file,
        latest_video_main_table_id="tbl-main",
        latest_video_snapshot_table_id="tbl-snapshot",
        latest_video_comparison_table_id="tbl-comparison",
    ).bootstrap(write_env=False)
    table_ids = {
        name: gateway.tables[name]
        for name in ("API字段字典", "模块字段需求与映射", "数据项目配置", "账号非敏感配置")
    }
    marker = ConfigCenterCriticalFieldMarker(
        gateway=gateway,
        app_token="base-token",
        table_ids=table_ids,
        backup_file=tmp_path / "critical-fields-backup.json",
    )

    first = marker.mark()
    second = marker.mark()

    assert sum(len(fields) for fields in first.updated_fields.values()) == 21
    assert sum(len(fields) for fields in second.updated_fields.values()) == 0
    assert sum(len(fields) for fields in second.unchanged_fields.values()) == 21
    assert (tmp_path / "critical-fields-backup.json").is_file()
    mapping_fields = {
        item["field_name"]: item for item in gateway.fields[table_ids["模块字段需求与映射"]]
    }
    assert mapping_fields["标准字段ID"]["description"].startswith("【关键字段")
    assert "description" not in mapping_fields["标准字段中文名"]


def test_localization_upgrades_v1_tables_without_deleting_records(tmp_path: Path) -> None:
    gateway = FakeAdminGateway()
    gateway.tables = {
        "模块字段需求与映射": "tbl-mapping",
        "数据项目配置": "tbl-project",
        "账号非敏感配置": "tbl-account",
        "视频追踪主表": "tbl-main",
        "视频实时快照表": "tbl-snapshot",
        "视频同期对比表": "tbl-comparison",
    }
    gateway.fields["tbl-mapping"] = [
        {"field_id": "fld-module", "field_name": "模块ID", "type": 1, "is_primary": True},
        {"field_id": "fld-standard", "field_name": "标准字段ID", "type": 1},
        {"field_id": "fld-column", "field_name": "飞书列名", "type": 1},
        {"field_id": "fld-target", "field_name": "目标表ID", "type": 1},
        {"field_id": "fld-enabled", "field_name": "启用", "type": 7},
    ]
    gateway.fields["tbl-project"] = [
        {"field_id": "fld-key", "field_name": "配置键", "type": 1, "is_primary": True},
        {"field_id": "fld-value", "field_name": "配置值", "type": 1},
        {"field_id": "fld-enabled", "field_name": "启用", "type": 7},
    ]
    gateway.fields["tbl-account"] = [
        {"field_id": "fld-key", "field_name": "配置键", "type": 1, "is_primary": True},
        {"field_id": "fld-value", "field_name": "配置值", "type": 1},
        {"field_id": "fld-enabled", "field_name": "启用", "type": 7},
    ]
    mapping_spec = next(
        item for item in load_builtin_config_center_schema() if item.name == "模块字段需求与映射"
    )
    business_table_ids = {
        "视频追踪主表": "tbl-main",
        "视频实时快照表": "tbl-snapshot",
        "视频同期对比表": "tbl-comparison",
    }
    gateway.records["tbl-mapping"] = [
        {
            "record_id": f"rec-map-{index}",
            "fields": {
                "模块ID": "latest_video_tracker",
                "标准字段ID": seed["标准字段ID"],
                "飞书列名": seed["飞书列名"],
                "目标表ID": business_table_ids[str(seed["目标表中文名"])],
                "启用": True,
            },
        }
        for index, seed in enumerate(mapping_spec.seed_records)
    ]
    gateway.records["tbl-project"] = [
        {
            "record_id": "rec-project-1",
            "fields": {"配置键": "latest_interval_minutes", "配置值": "30", "启用": True},
        },
        {
            "record_id": "rec-project-2",
            "fields": {"配置键": "latest_tracking_days", "配置值": "7", "启用": True},
        },
    ]
    gateway.records["tbl-account"] = [
        {
            "record_id": "rec-account-1",
            "fields": {"配置键": "youtube_channel_id", "配置值": "", "启用": True},
        },
        {
            "record_id": "rec-account-2",
            "fields": {
                "配置键": "latest_video_table_id",
                "配置值": "tbl-snapshot",
                "启用": True,
            },
        },
    ]
    for table_name, table_id in business_table_ids.items():
        gateway.fields[table_id] = [
            {
                "field_id": f"fld-{table_id}-{index}",
                "field_name": seed["飞书列名"],
                "type": 1,
            }
            for index, seed in enumerate(mapping_spec.seed_records)
            if seed["目标表中文名"] == table_name
        ]
        gateway.records[table_id] = []
    service = ConfigCenterLocalizationService(
        gateway=gateway,
        app_token="base-token",
        catalog=FieldCatalog.load_builtin(),
        mapping_table_id="tbl-mapping",
        project_config_table_id="tbl-project",
        account_config_table_id="tbl-account",
        business_tables={
            "视频追踪主表": "tbl-main",
            "视频实时快照表": "tbl-snapshot",
            "视频同期对比表": "tbl-comparison",
        },
        backup_file=tmp_path / "backup.json",
    )

    first = service.upgrade()
    second = service.upgrade()

    assert first.mapping_records_updated == 66
    assert first.account_records_created == 3
    assert second.mapping_records_updated == 0
    assert second.project_records_updated == 0
    assert second.account_records_updated == 0
    assert second.account_records_created == 0
    assert (tmp_path / "backup.json").is_file()
    mapping_fields = {item["field_name"]: item for item in gateway.fields["tbl-mapping"]}
    assert mapping_fields["映射名称"]["is_primary"] is True
    assert "模块ID" in mapping_fields
    first_mapping = gateway.records["tbl-mapping"][0]["fields"]
    assert first_mapping["映射名称"].startswith("视频追踪主表｜")
    assert first_mapping["模块ID"] == "latest_video_tracker"
    assert first_mapping["API类型"]
    account_keys = {item["fields"]["配置键"] for item in gateway.records["tbl-account"]}
    assert account_keys == {
        "youtube_channel_id",
        "tracking_video_ids",
        "latest_video_main_table_id",
        "latest_video_snapshot_table_id",
        "latest_video_comparison_table_id",
    }
