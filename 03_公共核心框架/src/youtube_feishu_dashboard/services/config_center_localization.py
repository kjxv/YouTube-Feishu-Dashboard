"""把 V1 飞书公共配置表就地升级为中英文对照结构。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from youtube_feishu_dashboard.api.feishu.protocols import FeishuTableAdminGateway
from youtube_feishu_dashboard.catalog.field_catalog import FieldCatalog
from youtube_feishu_dashboard.core.errors import ConfigurationError
from youtube_feishu_dashboard.services.config_center_bootstrap import (
    TableSpec,
    load_builtin_config_center_schema,
)


@dataclass(frozen=True, slots=True)
class ConfigCenterLocalizationResult:
    backup_file: str
    renamed_fields: tuple[str, ...]
    created_fields: dict[str, tuple[str, ...]]
    mapping_records_updated: int
    project_records_updated: int
    account_records_updated: int
    account_records_created: int
    verified_business_tables: dict[str, str]


class ConfigCenterLocalizationService:
    """只增加字段和更新系统初始化记录，不删除任何表、字段或记录。"""

    def __init__(
        self,
        *,
        gateway: FeishuTableAdminGateway,
        app_token: str,
        catalog: FieldCatalog,
        mapping_table_id: str,
        project_config_table_id: str,
        account_config_table_id: str,
        business_tables: dict[str, str],
        backup_file: Path,
    ) -> None:
        self.gateway = gateway
        self.app_token = app_token
        self.catalog = catalog
        self.mapping_table_id = mapping_table_id
        self.project_config_table_id = project_config_table_id
        self.account_config_table_id = account_config_table_id
        self.business_tables = business_tables
        self.backup_file = backup_file

    def upgrade(self) -> ConfigCenterLocalizationResult:
        specs = {item.name: item for item in load_builtin_config_center_schema()}
        mapping_spec = specs["模块字段需求与映射"]
        project_spec = specs["数据项目配置"]
        account_spec = specs["账号非敏感配置"]
        self._preflight_mapping_columns(mapping_spec)
        self._write_backup()

        renamed: list[str] = []
        if self._rename_mapping_primary_if_needed():
            renamed.append("模块字段需求与映射：模块ID → 映射名称")

        created_fields = {
            "模块字段需求与映射": self._ensure_fields(mapping_spec, self.mapping_table_id),
            "数据项目配置": self._ensure_fields(project_spec, self.project_config_table_id),
            "账号非敏感配置": self._ensure_fields(account_spec, self.account_config_table_id),
        }
        mapping_updated = self._update_mapping_records(mapping_spec)
        project_updated = self._update_metadata_records(
            project_spec,
            self.project_config_table_id,
            identity_field="配置键",
            preserved_fields={"配置值", "启用"},
        )
        account_updated, account_created = self._upgrade_account_records(account_spec)
        return ConfigCenterLocalizationResult(
            backup_file=str(self.backup_file),
            renamed_fields=tuple(renamed),
            created_fields=created_fields,
            mapping_records_updated=mapping_updated,
            project_records_updated=project_updated,
            account_records_updated=account_updated,
            account_records_created=account_created,
            verified_business_tables=dict(self.business_tables),
        )

    def _preflight_mapping_columns(self, spec: TableSpec) -> None:
        required_names = {"视频追踪主表", "视频实时快照表", "视频同期对比表"}
        missing_tables = sorted(required_names - self.business_tables.keys())
        if missing_tables:
            raise ConfigurationError(f"缺少业务表：{'、'.join(missing_tables)}")
        snapshot_id = self.business_tables["视频实时快照表"]
        actual_columns = {
            str(item.get("field_name"))
            for item in self.gateway.list_fields(self.app_token, snapshot_id)
            if item.get("field_name")
        }
        expected_columns = {
            str(self._resolve_seed(record).get("飞书列名")) for record in spec.seed_records
        }
        missing_columns = sorted(expected_columns - actual_columns)
        if missing_columns:
            raise ConfigurationError(
                "模块映射中的飞书列名与“视频实时快照表”不一致，缺少：" + "、".join(missing_columns)
            )

    def _write_backup(self) -> None:
        if self.backup_file.is_file():
            return
        payload: dict[str, Any] = {
            "说明": "公共配置表中文化升级前自动备份；不含 App Secret 或 OAuth Token。",
            "business_tables": self.business_tables,
            "tables": {},
        }
        for name, table_id in (
            ("模块字段需求与映射", self.mapping_table_id),
            ("数据项目配置", self.project_config_table_id),
            ("账号非敏感配置", self.account_config_table_id),
        ):
            payload["tables"][name] = {
                "table_id": table_id,
                "fields": self.gateway.list_fields(self.app_token, table_id),
                "records": self.gateway.list_records(self.app_token, table_id),
            }
        self.backup_file.parent.mkdir(parents=True, exist_ok=True)
        self.backup_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )

    def _rename_mapping_primary_if_needed(self) -> bool:
        fields = self.gateway.list_fields(self.app_token, self.mapping_table_id)
        by_name = {str(item.get("field_name")): item for item in fields}
        current = by_name.get("映射名称")
        if current is not None:
            if not bool(current.get("is_primary")):
                raise ConfigurationError("“映射名称”字段已经存在，但不是主字段。")
            return False
        legacy = by_name.get("模块ID")
        if legacy is None or not bool(legacy.get("is_primary")):
            raise ConfigurationError("找不到旧版主字段“模块ID”，无法安全升级。")
        field_id = str(legacy.get("field_id") or "").strip()
        if not field_id:
            raise ConfigurationError("旧版主字段缺少 field_id，无法安全升级。")
        self.gateway.update_field(
            self.app_token,
            self.mapping_table_id,
            field_id,
            field_name="映射名称",
            field_type=1,
        )
        return True

    def _ensure_fields(self, spec: TableSpec, table_id: str) -> tuple[str, ...]:
        existing = {
            str(item.get("field_name")): item
            for item in self.gateway.list_fields(self.app_token, table_id)
            if item.get("field_name")
        }
        created: list[str] = []
        for field in spec.fields:
            actual = existing.get(field.name)
            if actual is not None:
                if int(actual.get("type", -1)) != field.field_type:
                    raise ConfigurationError(
                        f"表“{spec.name}”字段“{field.name}”类型不符合中文化模板。"
                    )
                continue
            if field.primary:
                raise ConfigurationError(f"表“{spec.name}”缺少主字段“{field.name}”。")
            self.gateway.create_field(
                self.app_token,
                table_id,
                field_name=field.name,
                field_type=field.field_type,
            )
            created.append(field.name)
        return tuple(created)

    def _update_mapping_records(self, spec: TableSpec) -> int:
        seeds = {str(item["标准字段ID"]): self._resolve_seed(item) for item in spec.seed_records}
        updates: list[dict[str, Any]] = []
        managed_fields = {
            "映射名称",
            "模块ID",
            "模块中文名",
            "目标表中文名",
            "标准字段中文名",
            "API类型",
            "API官方字段",
            "写入方式",
            "实现状态",
            "备注",
        }
        for record in self.gateway.list_records(self.app_token, self.mapping_table_id):
            fields = dict(record.get("fields", {}))
            standard_id = str(fields.get("标准字段ID") or "").strip()
            desired = seeds.get(standard_id)
            record_id = str(record.get("record_id") or "").strip()
            if desired is None or not record_id:
                continue
            changes = {
                name: desired[name]
                for name in managed_fields
                if fields.get(name) != desired.get(name)
            }
            if changes:
                updates.append({"record_id": record_id, "fields": changes})
        if updates:
            self.gateway.batch_update_records(self.app_token, self.mapping_table_id, updates)
        return len(updates)

    def _update_metadata_records(
        self,
        spec: TableSpec,
        table_id: str,
        *,
        identity_field: str,
        preserved_fields: set[str],
    ) -> int:
        seeds = {str(item[identity_field]): self._resolve_seed(item) for item in spec.seed_records}
        updates: list[dict[str, Any]] = []
        for record in self.gateway.list_records(self.app_token, table_id):
            fields = dict(record.get("fields", {}))
            key = str(fields.get(identity_field) or "").strip()
            desired = seeds.get(key)
            record_id = str(record.get("record_id") or "").strip()
            if desired is None or not record_id:
                continue
            changes = {
                name: value
                for name, value in desired.items()
                if name not in preserved_fields
                and name != identity_field
                and fields.get(name) != value
            }
            if changes:
                updates.append({"record_id": record_id, "fields": changes})
        if updates:
            self.gateway.batch_update_records(self.app_token, table_id, updates)
        return len(updates)

    def _upgrade_account_records(self, spec: TableSpec) -> tuple[int, int]:
        records = self.gateway.list_records(self.app_token, self.account_config_table_id)
        by_key = {str(item.get("fields", {}).get("配置键") or "").strip(): item for item in records}
        legacy = by_key.get("latest_video_table_id")
        current = by_key.get("latest_video_snapshot_table_id")
        updates_by_id: dict[str, dict[str, Any]] = {}
        if legacy is not None and current is None:
            record_id = str(legacy.get("record_id") or "").strip()
            if record_id:
                updates_by_id[record_id] = {"配置键": "latest_video_snapshot_table_id"}
                legacy["fields"]["配置键"] = "latest_video_snapshot_table_id"
                by_key["latest_video_snapshot_table_id"] = legacy
                by_key.pop("latest_video_table_id", None)

        seeds = {str(item["配置键"]): self._resolve_seed(item) for item in spec.seed_records}
        for key, record in by_key.items():
            desired = seeds.get(key)
            record_id = str(record.get("record_id") or "").strip()
            if desired is None or not record_id:
                continue
            current_fields = dict(record.get("fields", {}))
            changes = {
                name: value
                for name, value in desired.items()
                if name not in {"配置键", "配置值", "启用"} and current_fields.get(name) != value
            }
            if changes:
                updates_by_id.setdefault(record_id, {}).update(changes)

        creates = [seed for key, seed in seeds.items() if key not in by_key]
        updates = [
            {"record_id": record_id, "fields": fields}
            for record_id, fields in updates_by_id.items()
        ]
        if updates:
            self.gateway.batch_update_records(self.app_token, self.account_config_table_id, updates)
        if creates:
            self.gateway.batch_create_records(self.app_token, self.account_config_table_id, creates)
        return len(updates), len(creates)

    def _resolve_seed(self, fields: dict[str, Any]) -> dict[str, Any]:
        replacements = {
            "${LATEST_VIDEO_MAIN_TABLE_ID}": self.business_tables["视频追踪主表"],
            "${LATEST_VIDEO_SNAPSHOT_TABLE_ID}": self.business_tables["视频实时快照表"],
            "${LATEST_VIDEO_COMPARISON_TABLE_ID}": self.business_tables["视频同期对比表"],
        }
        return {
            name: replacements.get(value, value) if isinstance(value, str) else value
            for name, value in fields.items()
        }
