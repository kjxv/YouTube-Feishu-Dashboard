"""幂等创建飞书公共配置中心，并把非敏感 Table ID 回写到 `.env`。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from youtube_feishu_dashboard.api.feishu.protocols import FeishuTableAdminGateway
from youtube_feishu_dashboard.catalog.field_catalog import FieldCatalog
from youtube_feishu_dashboard.core.errors import ConfigurationError, ExternalServiceError
from youtube_feishu_dashboard.services.catalog_sync import sync_catalog_to_feishu


@dataclass(frozen=True, slots=True)
class FieldSpec:
    name: str
    field_type: int
    primary: bool = False
    critical: bool = False
    critical_note: str | None = None

    def as_create_payload(self) -> dict[str, Any]:
        return {"field_name": self.name, "type": self.field_type}


@dataclass(frozen=True, slots=True)
class TableSpec:
    name: str
    env_key: str
    fields: tuple[FieldSpec, ...]
    identity_fields: tuple[str, ...]
    seed_records: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    table_ids: dict[str, str]
    created_tables: tuple[str, ...]
    reused_tables: tuple[str, ...]
    created_fields: dict[str, tuple[str, ...]]
    seed_records_created: dict[str, int]
    catalog_sync: dict[str, int]
    env_keys_updated: tuple[str, ...]


class ConfigCenterBootstrapper:
    """同名表复用、缺失字段补建、已有用户配置不覆盖。"""

    def __init__(
        self,
        *,
        gateway: FeishuTableAdminGateway,
        app_token: str,
        catalog: FieldCatalog,
        env_file: Path,
        latest_video_main_table_id: str,
        latest_video_snapshot_table_id: str,
        latest_video_comparison_table_id: str,
        youtube_channel_id: str | None = None,
    ) -> None:
        self.gateway = gateway
        self.app_token = app_token
        self.catalog = catalog
        self.env_file = env_file
        self.latest_video_main_table_id = latest_video_main_table_id
        self.latest_video_snapshot_table_id = latest_video_snapshot_table_id
        self.latest_video_comparison_table_id = latest_video_comparison_table_id
        self.youtube_channel_id = youtube_channel_id or ""

    def bootstrap(self, *, write_env: bool = True) -> BootstrapResult:
        specs = load_builtin_config_center_schema()
        existing = self.gateway.list_tables(self.app_token)
        tables_by_name = self._unique_tables_by_name(existing)
        table_ids: dict[str, str] = {}
        created_tables: list[str] = []
        reused_tables: list[str] = []
        created_fields: dict[str, tuple[str, ...]] = {}
        seed_counts: dict[str, int] = {}

        for spec in specs:
            table = tables_by_name.get(spec.name)
            if table is None:
                self.gateway.create_table(
                    self.app_token,
                    name=spec.name,
                    fields=[field.as_create_payload() for field in spec.fields],
                )
                refreshed = self._unique_tables_by_name(self.gateway.list_tables(self.app_token))
                table = refreshed.get(spec.name)
                if table is None:
                    raise ExternalServiceError(
                        f"飞书已接受创建请求，但未能重新找到数据表“{spec.name}”。"
                    )
                tables_by_name = refreshed
                created_tables.append(spec.name)
            else:
                reused_tables.append(spec.name)

            table_id = _required_text(table.get("table_id"), f"{spec.name} Table ID")
            table_ids[spec.env_key] = table_id
            created_fields[spec.name] = self._ensure_fields(spec, table_id)
            seed_counts[spec.name] = self._seed_missing_records(spec, table_id)

        field_table_id = table_ids["YFD_FEISHU_API_FIELD_TABLE_ID"]
        catalog_result = sync_catalog_to_feishu(
            gateway=self.gateway,
            app_token=self.app_token,
            table_id=field_table_id,
            catalog=self.catalog,
        )
        updated_keys: tuple[str, ...] = ()
        if write_env:
            update_dotenv(self.env_file, table_ids)
            updated_keys = tuple(table_ids)
        return BootstrapResult(
            table_ids=table_ids,
            created_tables=tuple(created_tables),
            reused_tables=tuple(reused_tables),
            created_fields=created_fields,
            seed_records_created=seed_counts,
            catalog_sync=catalog_result,
            env_keys_updated=updated_keys,
        )

    def _ensure_fields(self, spec: TableSpec, table_id: str) -> tuple[str, ...]:
        actual = self.gateway.list_fields(self.app_token, table_id)
        by_name = {str(item.get("field_name")): item for item in actual if item.get("field_name")}
        created: list[str] = []
        for position, expected in enumerate(spec.fields):
            found = by_name.get(expected.name)
            if found is not None:
                actual_type = int(found.get("type", -1))
                if actual_type != expected.field_type:
                    raise ConfigurationError(
                        f"飞书表“{spec.name}”字段“{expected.name}”类型为 {actual_type}，"
                        f"模板要求 {expected.field_type}；初始化器不会自动改写已有字段。"
                    )
                if position == 0 and not bool(found.get("is_primary", expected.primary)):
                    raise ConfigurationError(f"飞书表“{spec.name}”的“{expected.name}”不是主字段。")
                continue
            if expected.primary or position == 0:
                raise ConfigurationError(
                    f"飞书表“{spec.name}”缺少主字段“{expected.name}”；"
                    "为保护已有数据，请人工确认表结构后再处理。"
                )
            self.gateway.create_field(
                self.app_token,
                table_id,
                field_name=expected.name,
                field_type=expected.field_type,
            )
            created.append(expected.name)
        return tuple(created)

    def _seed_missing_records(self, spec: TableSpec, table_id: str) -> int:
        if not spec.seed_records:
            return 0
        existing_records = self.gateway.list_records(self.app_token, table_id)
        existing_keys = {
            self._identity_key(record.get("fields", {}), spec.identity_fields)
            for record in existing_records
        }
        creates: list[dict[str, Any]] = []
        for raw in spec.seed_records:
            fields = self._resolve_seed_values(raw)
            key = self._identity_key(fields, spec.identity_fields)
            if key not in existing_keys:
                creates.append(fields)
                existing_keys.add(key)
        if creates:
            self.gateway.batch_create_records(self.app_token, table_id, creates)
        return len(creates)

    def _resolve_seed_values(self, fields: dict[str, Any]) -> dict[str, Any]:
        replacements = {
            "${LATEST_VIDEO_MAIN_TABLE_ID}": self.latest_video_main_table_id,
            "${LATEST_VIDEO_SNAPSHOT_TABLE_ID}": self.latest_video_snapshot_table_id,
            "${LATEST_VIDEO_COMPARISON_TABLE_ID}": self.latest_video_comparison_table_id,
            "${YOUTUBE_CHANNEL_ID}": self.youtube_channel_id,
        }
        return {
            name: replacements.get(value, value) if isinstance(value, str) else value
            for name, value in fields.items()
        }

    @staticmethod
    def _identity_key(fields: dict[str, Any], names: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(str(fields.get(name, "")).strip() for name in names)

    @staticmethod
    def _unique_tables_by_name(tables: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        duplicates: set[str] = set()
        for table in tables:
            name = str(table.get("name", "")).strip()
            if not name:
                continue
            if name in result:
                duplicates.add(name)
            result[name] = table
        if duplicates:
            names = "、".join(sorted(duplicates))
            raise ConfigurationError(f"同一 Base 中存在重名数据表：{names}。请先人工消除歧义。")
        return result


def load_builtin_config_center_schema() -> tuple[TableSpec, ...]:
    resource = resources.files("youtube_feishu_dashboard.api.feishu").joinpath(
        "config_center_schema.v2.json"
    )
    raw = json.loads(resource.read_text(encoding="utf-8"))
    tables: list[TableSpec] = []
    for table in raw.get("tables", []):
        fields = tuple(
            FieldSpec(
                name=str(item["name"]),
                field_type=int(item["type"]),
                primary=bool(item.get("primary", False)),
                critical=bool(item.get("critical", False)),
                critical_note=(
                    str(item["critical_note"]) if item.get("critical_note") else None
                ),
            )
            for item in table["fields"]
        )
        if not fields or not fields[0].primary:
            raise ConfigurationError(f"配置表“{table.get('name')}”的第一个字段必须标记为主字段。")
        tables.append(
            TableSpec(
                name=str(table["name"]),
                env_key=str(table["env_key"]),
                fields=fields,
                identity_fields=tuple(str(item) for item in table["identity_fields"]),
                seed_records=tuple(dict(item) for item in table.get("seed_records", [])),
            )
        )
    if not tables:
        raise ConfigurationError("内置飞书配置中心表结构为空。")
    return tuple(tables)


def update_dotenv(path: Path, values: dict[str, str]) -> None:
    """只更新指定非敏感键，保留 `.env` 中其他内容和 Secret。"""
    if not path.is_file():
        raise ConfigurationError(f"找不到 .env：{path}")
    original = path.read_text(encoding="utf-8")
    newline = "\r\n" if "\r\n" in original else "\n"
    lines = original.splitlines()
    pending = dict(values)
    updated: list[str] = []
    for line in lines:
        key = (
            line.split("=", 1)[0].strip()
            if "=" in line and not line.lstrip().startswith("#")
            else ""
        )
        if key in pending:
            updated.append(f"{key}={pending.pop(key)}")
        else:
            updated.append(line)
    if pending:
        if updated and updated[-1].strip():
            updated.append("")
        updated.append("# 飞书公共配置中心（由初始化命令写入，可重复执行）")
        updated.extend(f"{key}={value}" for key, value in pending.items())
    path.write_text(newline.join(updated) + newline, encoding="utf-8")


def _required_text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ExternalServiceError(f"飞书 API 返回结果缺少 {label}。")
    return text
