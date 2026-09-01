"""在飞书字段说明中标记配置中心的程序关键字段。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from youtube_feishu_dashboard.api.feishu.protocols import FeishuTableAdminGateway
from youtube_feishu_dashboard.core.errors import ConfigurationError
from youtube_feishu_dashboard.services.config_center_bootstrap import (
    FieldSpec,
    load_builtin_config_center_schema,
)


@dataclass(frozen=True, slots=True)
class CriticalFieldMarkerResult:
    backup_file: str
    updated_fields: dict[str, tuple[str, ...]]
    unchanged_fields: dict[str, tuple[str, ...]]
    critical_fields: dict[str, tuple[str, ...]]


class ConfigCenterCriticalFieldMarker:
    """只更新字段说明，不改列名、字段类型、记录或用户配置值。"""

    def __init__(
        self,
        *,
        gateway: FeishuTableAdminGateway,
        app_token: str,
        table_ids: dict[str, str],
        backup_file: Path,
    ) -> None:
        self.gateway = gateway
        self.app_token = app_token
        self.table_ids = table_ids
        self.backup_file = backup_file

    def mark(self) -> CriticalFieldMarkerResult:
        specs = load_builtin_config_center_schema()
        expected_tables = {spec.name for spec in specs}
        missing_tables = sorted(expected_tables - self.table_ids.keys())
        if missing_tables:
            raise ConfigurationError("缺少配置表ID：" + "、".join(missing_tables))

        current_fields = {
            spec.name: self.gateway.list_fields(self.app_token, self.table_ids[spec.name])
            for spec in specs
        }
        self._write_backup(current_fields)

        updated: dict[str, tuple[str, ...]] = {}
        unchanged: dict[str, tuple[str, ...]] = {}
        critical: dict[str, tuple[str, ...]] = {}
        for spec in specs:
            actual_by_name = {
                str(item.get("field_name")): item
                for item in current_fields[spec.name]
                if item.get("field_name")
            }
            updated_names: list[str] = []
            unchanged_names: list[str] = []
            critical_names: list[str] = []
            for field in spec.fields:
                if not field.critical:
                    continue
                critical_names.append(field.name)
                actual = actual_by_name.get(field.name)
                if actual is None:
                    raise ConfigurationError(
                        f"飞书表“{spec.name}”缺少关键字段“{field.name}”，已停止标记。"
                    )
                description = self._description(field)
                if self._description_text(actual.get("description")) == description:
                    unchanged_names.append(field.name)
                    continue
                field_id = str(actual.get("field_id") or "").strip()
                if not field_id:
                    raise ConfigurationError(
                        f"飞书表“{spec.name}”字段“{field.name}”缺少 field_id。"
                    )
                property_value = actual.get("property")
                self.gateway.update_field(
                    self.app_token,
                    self.table_ids[spec.name],
                    field_id,
                    field_name=field.name,
                    field_type=int(actual.get("type", field.field_type)),
                    property=property_value if isinstance(property_value, dict) else None,
                    description=description,
                )
                updated_names.append(field.name)
            updated[spec.name] = tuple(updated_names)
            unchanged[spec.name] = tuple(unchanged_names)
            critical[spec.name] = tuple(critical_names)

        return CriticalFieldMarkerResult(
            backup_file=str(self.backup_file),
            updated_fields=updated,
            unchanged_fields=unchanged,
            critical_fields=critical,
        )

    def _write_backup(self, current_fields: dict[str, list[dict[str, Any]]]) -> None:
        if self.backup_file.is_file():
            return
        payload = {
            "说明": "关键字段标记前的字段结构备份；不含 App Secret、OAuth Token 或记录值。",
            "tables": {
                name: {"table_id": self.table_ids[name], "fields": fields}
                for name, fields in current_fields.items()
            },
        }
        self.backup_file.parent.mkdir(parents=True, exist_ok=True)
        self.backup_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    @staticmethod
    def _description(field: FieldSpec) -> str:
        note = field.critical_note or "主程序会读取并识别此字段。"
        return f"【关键字段｜请勿删除或改名】{note} 填写错误会影响程序结果。"

    @staticmethod
    def _description_text(value: Any) -> str:
        if isinstance(value, dict):
            return str(value.get("text") or "").strip()
        return str(value or "").strip()
