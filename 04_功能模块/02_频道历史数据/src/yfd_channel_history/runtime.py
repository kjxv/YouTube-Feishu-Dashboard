from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from youtube_feishu_dashboard.api.feishu.config_center import ModuleFieldMapping
from youtube_feishu_dashboard.api.feishu.field_types import (
    FeishuTableSchema,
    FeishuValueAdapterRegistry,
    FeishuWriteValue,
)
from youtube_feishu_dashboard.catalog.field_catalog import FieldCatalog
from youtube_feishu_dashboard.core.errors import ConfigurationError

from yfd_channel_history.manifest import MODULE_ID

_COMPATIBLE_TYPES: dict[str, frozenset[int]] = {
    "string": frozenset({1, 3, 4}),
    "url": frozenset({1, 15}),
    "integer": frozenset({1, 2}),
    "number": frozenset({1, 2}),
    "duration": frozenset({1, 2}),
    "boolean": frozenset({1, 3, 7}),
    "date": frozenset({1, 5}),
    "datetime": frozenset({1, 5}),
}

_SHARED_OUTPUT_FIELD_IDS = {
    "VIDEO_URL",
    "VIDEO_TYPE",
    "DATA_API_FETCHED_AT_BEIJING",
    "DATA_API_DATA_THROUGH_AT_BEIJING_INFERRED",
    "ANALYTICS_FETCHED_AT_BEIJING",
    "ANALYTICS_DATA_THROUGH_AT_BEIJING",
}


@dataclass(frozen=True, slots=True)
class ChannelTablePlan:
    name: str
    table_id: str
    schema: FeishuTableSchema
    mapping: dict[str, str]


@dataclass(frozen=True, slots=True)
class ChannelHistoryRuntimePlan:
    tables: tuple[ChannelTablePlan, ...]

    @classmethod
    def compile(
        cls,
        *,
        catalog: FieldCatalog,
        table_ids: Mapping[str, str],
        mappings: Iterable[ModuleFieldMapping],
        raw_fields_by_table_id: Mapping[str, Iterable[Mapping[str, object]]],
    ) -> ChannelHistoryRuntimePlan:
        enabled = [
            item for item in mappings if item.enabled and item.module_id == MODULE_ID
        ]
        if len(set(table_ids.values())) != len(table_ids):
            raise ConfigurationError("频道统计的三张业务表不能使用相同 Table ID。")
        configured = set(table_ids.values())
        unknown_targets = sorted(
            {
                str(item.target_table_id)
                for item in enabled
                if item.target_table_id not in configured
            }
        )
        if unknown_targets:
            raise ConfigurationError(
                "频道统计映射指向未配置的业务表：" + "、".join(unknown_targets)
            )

        plans: list[ChannelTablePlan] = []
        for table_name, table_id in table_ids.items():
            selected = [item for item in enabled if item.target_table_id == table_id]
            if not selected:
                raise ConfigurationError(f"{table_name} 没有已启用的 channel_history 映射。")
            ids = Counter(item.standard_field_id for item in selected)
            columns = Counter(item.feishu_column for item in selected)
            duplicate_ids = sorted(key for key, count in ids.items() if count > 1)
            duplicate_columns = sorted(key for key, count in columns.items() if count > 1)
            if duplicate_ids or duplicate_columns:
                raise ConfigurationError(
                    f"{table_name} 存在重复字段或列："
                    + "、".join((*duplicate_ids, *duplicate_columns))
                )
            raw_fields = raw_fields_by_table_id.get(table_id)
            if raw_fields is None:
                raise ConfigurationError(f"没有读取到 {table_name} 的实时字段结构。")
            schema = FeishuTableSchema.from_api_fields(raw_fields)
            mapping: dict[str, str] = {}
            for item in selected:
                catalog.validate_requirements((item.standard_field_id,))
                definition = catalog.get(item.standard_field_id)
                if definition.implementation_status != "tested":
                    raise ConfigurationError(
                        f"字段 {item.standard_field_id} 尚未达到可直接使用状态。"
                    )
                if definition.calculation_mode == "module_code" and (
                    definition.implementation_module not in {None, MODULE_ID}
                    and item.standard_field_id not in _SHARED_OUTPUT_FIELD_IDS
                ):
                    raise ConfigurationError(
                        f"字段 {item.standard_field_id} 登记给模块"
                        f" {definition.implementation_module}，不能由 {MODULE_ID} 使用。"
                    )
                target = schema.require_name(item.feishu_column)
                if not FeishuValueAdapterRegistry().supports(target):
                    raise ConfigurationError(
                        f"{table_name} / {item.feishu_column} 的飞书类型暂不支持写入。"
                    )
                compatible = _COMPATIBLE_TYPES.get(definition.data_type)
                if compatible is not None and target.type_code not in compatible:
                    raise ConfigurationError(
                        f"{table_name} / {item.feishu_column} 类型与"
                        f" {item.standard_field_id} 不兼容。"
                    )
                mapping[item.standard_field_id] = item.feishu_column
            plans.append(ChannelTablePlan(table_name, table_id, schema, mapping))
        return cls(tuple(plans))

    def require_table(self, name: str) -> ChannelTablePlan:
        for table in self.tables:
            if table.name == name:
                return table
        raise ConfigurationError(f"频道统计运行计划缺少业务表：{name}")

    def adapt_partial(
        self, table_name: str, values: Mapping[str, object]
    ) -> dict[str, FeishuWriteValue]:
        table = self.require_table(table_name)
        adapters = FeishuValueAdapterRegistry()
        return {
            column: adapters.adapt(table.schema.require_name(column), values[field_id])
            for field_id, column in table.mapping.items()
            if field_id in values and values[field_id] is not None
        }

    def column(self, table_name: str, field_id: str) -> str | None:
        return self.require_table(table_name).mapping.get(field_id)

    def as_report(self) -> dict[str, Any]:
        return {
            "module_id": MODULE_ID,
            "tables": {
                item.name: {
                    "table_id": item.table_id,
                    "mapping_count": len(item.mapping),
                    "mapping": item.mapping,
                }
                for item in self.tables
            },
        }
