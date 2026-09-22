"""备份并物理删除频道三表的旧北京时间与误导性截止时间字段。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from youtube_feishu_dashboard.api.feishu.protocols import FeishuTableAdminGateway
from youtube_feishu_dashboard.core.errors import ConfigurationError
from youtube_feishu_dashboard.services.catalog_sync import scalar_text

from yfd_channel_history.feishu_setup import _LEGACY_STANDARD_IDS
from yfd_channel_history.manifest import MODULE_ID

_COMMON_LEGACY_COLUMNS = frozenset(
    {
        "Data API 数据获取时间（北京时间）",
        "Data API 数据截止时间（北京时间）（推定）",
        "Data API 数据截止时间（太平洋时间）（推定）",
        "Analytics API 数据获取时间（北京时间）",
        "Analytics API 数据截止时间（北京时间）",
        "Analytics API 数据截止时间（太平洋时间）",
    }
)
_VIDEO_MAIN_LEGACY_COLUMNS = frozenset(
    {
        "近7天采样起点（北京时间）",
        "近7天采样终点（北京时间）",
        "48小时样本采集时间（北京时间）",
    }
)
_HISTORY_LEGACY_COLUMNS = frozenset(
    {
        "统计日期",
        "数据截止日期",
        "数据截止日期（旧版停用）",
        "记录日期（北京时间）",
        "记录日期（旧版停用）",
    }
)
_REQUIRED_NEW_COLUMNS = {
    "视频主表": frozenset(
        {
            "Data API 数据获取时间（太平洋时间）",
            "Analytics API 数据获取时间（太平洋时间）",
            "近7天采样起点（太平洋时间）",
            "近7天采样终点（太平洋时间）",
            "48小时样本采集时间（太平洋时间）",
        }
    ),
    "视频历史数据": frozenset(
        {
            "数据日期（太平洋时间）",
            "Data API 数据获取时间（太平洋时间）",
            "Analytics API 数据获取时间（太平洋时间）",
        }
    ),
    "频道历史数据": frozenset(
        {
            "数据日期（太平洋时间）",
            "Data API 数据获取时间（太平洋时间）",
            "Analytics API 数据获取时间（太平洋时间）",
        }
    ),
}
_REQUIRED_NEW_MAPPINGS = {
    "视频主表": {
        "DATA_API_FETCHED_AT_PACIFIC": "Data API 数据获取时间（太平洋时间）",
        "ANALYTICS_FETCHED_AT_PACIFIC": "Analytics API 数据获取时间（太平洋时间）",
        "VIDEO_7D_SAMPLE_START_AT_PACIFIC": "近7天采样起点（太平洋时间）",
        "VIDEO_7D_SAMPLE_END_AT_PACIFIC": "近7天采样终点（太平洋时间）",
        "VIDEO_48H_SAMPLE_AT_PACIFIC": "48小时样本采集时间（太平洋时间）",
    },
    "视频历史数据": {
        "DAILY_DATA_DATE_PACIFIC": "数据日期（太平洋时间）",
        "DATA_API_FETCHED_AT_PACIFIC": "Data API 数据获取时间（太平洋时间）",
        "ANALYTICS_FETCHED_AT_PACIFIC": "Analytics API 数据获取时间（太平洋时间）",
    },
    "频道历史数据": {
        "DAILY_DATA_DATE_PACIFIC": "数据日期（太平洋时间）",
        "DATA_API_FETCHED_AT_PACIFIC": "Data API 数据获取时间（太平洋时间）",
        "ANALYTICS_FETCHED_AT_PACIFIC": "Analytics API 数据获取时间（太平洋时间）",
    },
}


@dataclass(frozen=True, slots=True)
class LegacyTimeCleanupResult:
    fields_to_delete: int
    mappings_to_delete: int
    records_with_legacy_values: int
    fields_deleted: int
    mappings_deleted: int
    applied: bool
    backup_file: str | None
    field_names: tuple[str, ...]


class LegacyTimeFieldCleaner:
    """只有预期数量完全匹配时，才删除已停用的旧字段与映射。"""

    def __init__(
        self,
        *,
        gateway: FeishuTableAdminGateway,
        app_token: str,
        table_ids: dict[str, str],
        mapping_table_id: str,
        backup_file: Path,
    ) -> None:
        self.gateway = gateway
        self.app_token = app_token
        self.table_ids = table_ids
        self.mapping_table_id = mapping_table_id
        self.backup_file = backup_file

    def run(
        self,
        *,
        expected_field_count: int | None,
        expected_mapping_count: int | None,
        apply: bool,
    ) -> LegacyTimeCleanupResult:
        if apply and (expected_field_count is None or expected_mapping_count is None):
            raise ConfigurationError("确认删除时必须提供字段和映射的预期数量。")
        if any(
            value is not None and value < 0
            for value in (expected_field_count, expected_mapping_count)
        ):
            raise ConfigurationError("预期删除数量不能为负数。")

        fields_to_delete: list[dict[str, Any]] = []
        for table_name, table_id in self.table_ids.items():
            raw_fields = self.gateway.list_fields(self.app_token, table_id)
            names = {str(item.get("field_name") or "").strip() for item in raw_fields}
            missing_new = sorted(_REQUIRED_NEW_COLUMNS[table_name] - names)
            if missing_new:
                raise ConfigurationError(
                    f"{table_name} 尚未建好新的太平洋字段："
                    f"{'、'.join(missing_new)}；禁止删除旧字段。"
                )
            legacy_names = set(_COMMON_LEGACY_COLUMNS)
            legacy_names.update(
                _VIDEO_MAIN_LEGACY_COLUMNS
                if table_name == "视频主表"
                else _HISTORY_LEGACY_COLUMNS
            )
            for field in raw_fields:
                name = str(field.get("field_name") or "").strip()
                if name not in legacy_names:
                    continue
                field_id = str(field.get("field_id") or "").strip()
                if not field_id:
                    raise ConfigurationError(f"{table_name} 旧字段“{name}”缺少 field_id。")
                fields_to_delete.append(
                    {
                        "table_name": table_name,
                        "table_id": table_id,
                        "field_id": field_id,
                        "field": field,
                    }
                )

        all_mappings = self.gateway.list_records(self.app_token, self.mapping_table_id)
        by_identity = {
            (
                scalar_text(record.get("fields", {}).get("目标表ID")) or "",
                scalar_text(record.get("fields", {}).get("标准字段ID")) or "",
            ): record
            for record in all_mappings
            if scalar_text(record.get("fields", {}).get("模块ID")) == MODULE_ID
        }
        for table_name, table_id in self.table_ids.items():
            for standard_id, column in _REQUIRED_NEW_MAPPINGS[table_name].items():
                record = by_identity.get((table_id, standard_id))
                fields = record.get("fields", {}) if record else {}
                if (
                    record is None
                    or scalar_text(fields.get("飞书列名")) != column
                    or not _enabled(fields.get("启用"))
                ):
                    raise ConfigurationError(
                        f"{table_name} 的新映射 {standard_id} 尚未正确启用；禁止删除旧字段。"
                    )

        mappings_to_delete: list[dict[str, Any]] = []
        table_id_set = set(self.table_ids.values())
        for record in all_mappings:
            fields = record.get("fields", {})
            if (
                scalar_text(fields.get("模块ID")) == MODULE_ID
                and scalar_text(fields.get("目标表ID")) in table_id_set
                and scalar_text(fields.get("标准字段ID")) in _LEGACY_STANDARD_IDS
            ):
                record_id = str(record.get("record_id") or "").strip()
                if not record_id:
                    raise ConfigurationError("旧字段映射记录缺少 record_id。")
                if _enabled(fields.get("启用")):
                    raise ConfigurationError(
                        "仍有旧时间映射处于启用状态；请先运行字段升级命令。"
                    )
                mappings_to_delete.append(record)

        actual_fields = len(fields_to_delete)
        actual_mappings = len(mappings_to_delete)
        if expected_field_count is not None and expected_field_count != actual_fields:
            raise ConfigurationError(
                f"预期删除 {expected_field_count} 个字段，实时发现 {actual_fields} 个；未删除。"
            )
        if expected_mapping_count is not None and expected_mapping_count != actual_mappings:
            raise ConfigurationError(
                f"预期删除 {expected_mapping_count} 条映射，实时发现 {actual_mappings} 条；未删除。"
            )

        names_by_table: dict[str, set[str]] = {}
        for item in fields_to_delete:
            names_by_table.setdefault(str(item["table_id"]), set()).add(
                str(item["field"]["field_name"])
            )
        record_values: list[dict[str, Any]] = []
        for table_name, table_id in self.table_ids.items():
            legacy_names = names_by_table.get(table_id, set())
            if not legacy_names:
                continue
            for record in self.gateway.list_records(self.app_token, table_id):
                fields = record.get("fields", {})
                legacy_values = {
                    name: fields[name] for name in legacy_names if name in fields
                }
                if legacy_values:
                    record_values.append(
                        {
                            "table_name": table_name,
                            "table_id": table_id,
                            "record_id": record.get("record_id"),
                            "fields": legacy_values,
                        }
                    )

        backup_path: str | None = None
        if apply:
            payload = {
                "fields": fields_to_delete,
                "mapping_table_id": self.mapping_table_id,
                "mappings": mappings_to_delete,
                "record_values": record_values,
            }
            self.backup_file.parent.mkdir(parents=True, exist_ok=True)
            self.backup_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            backup_path = str(self.backup_file)
            mapping_ids = [str(item["record_id"]) for item in mappings_to_delete]
            if mapping_ids:
                self.gateway.batch_delete_records(
                    self.app_token, self.mapping_table_id, mapping_ids
                )
            for item in fields_to_delete:
                self.gateway.delete_field(
                    self.app_token,
                    str(item["table_id"]),
                    str(item["field_id"]),
                )

        return LegacyTimeCleanupResult(
            fields_to_delete=actual_fields,
            mappings_to_delete=actual_mappings,
            records_with_legacy_values=len(record_values),
            fields_deleted=actual_fields if apply else 0,
            mappings_deleted=actual_mappings if apply else 0,
            applied=apply,
            backup_file=backup_path,
            field_names=tuple(
                f"{item['table_name']}/{item['field']['field_name']}"
                for item in fields_to_delete
            ),
        )


def _enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = scalar_text(value)
    return text is not None and text.strip().lower() not in {"false", "0", "否", "停用"}
