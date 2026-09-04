from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from youtube_feishu_dashboard.api.feishu.config_center import ModuleFieldMapping
from youtube_feishu_dashboard.api.feishu.field_types import (
    FeishuTableSchema,
    FeishuValueAdapterRegistry,
    FeishuWriteValue,
)
from youtube_feishu_dashboard.api.youtube.analytics_extraction import (
    AnalyticsMetricExtractor,
)
from youtube_feishu_dashboard.api.youtube.extraction import DataApiScalarExtractor
from youtube_feishu_dashboard.api.youtube.reporting_extraction import (
    ReportingReachExtractor,
)
from youtube_feishu_dashboard.catalog.field_catalog import ApiRequestPlan, FieldCatalog
from youtube_feishu_dashboard.core.errors import ConfigurationError
from youtube_feishu_dashboard.services.calculated_fields import CalculatedFieldEngine

_COMPATIBLE_FEISHU_TYPES_BY_SOURCE: dict[str, frozenset[int]] = {
    "string": frozenset({1, 3, 4}),
    "url": frozenset({1, 15}),
    "integer": frozenset({1, 2}),
    "number": frozenset({1, 2}),
    "duration": frozenset({1, 2}),
    "boolean": frozenset({1, 3, 7}),
    "date": frozenset({1, 5}),
    "datetime": frozenset({1, 5}),
}


@dataclass(frozen=True, slots=True)
class CompiledFieldMapping:
    standard_field_id: str
    feishu_column: str
    feishu_field_id: str
    feishu_type_code: int
    feishu_type_name: str
    source_kind: Literal["data_api", "analytics_api", "reporting_api", "computed"]
    official_field: str | None
    source_data_type: str | None


@dataclass(frozen=True, slots=True)
class CompiledTablePlan:
    table_name: str
    table_id: str
    schema: FeishuTableSchema
    mappings: tuple[CompiledFieldMapping, ...]
    calculator: CalculatedFieldEngine

    @property
    def field_mapping(self) -> dict[str, str]:
        return {item.standard_field_id: item.feishu_column for item in self.mappings}


@dataclass(frozen=True, slots=True)
class DynamicModulePlan:
    module_id: str
    request_plan: ApiRequestPlan
    extractor: DataApiScalarExtractor
    data_api_field_ids: tuple[str, ...]
    analytics_field_ids: tuple[str, ...]
    reporting_field_ids: tuple[str, ...]
    tables: tuple[CompiledTablePlan, ...]

    def require_table(self, table_name: str) -> CompiledTablePlan:
        for table in self.tables:
            if table.table_name == table_name:
                return table
        raise ConfigurationError(f"动态计划缺少业务表：{table_name}")

    def adapt_table_record(
        self,
        table_name: str,
        values: Mapping[str, object],
        *,
        adapters: FeishuValueAdapterRegistry | None = None,
    ) -> dict[str, FeishuWriteValue]:
        registry = adapters or FeishuValueAdapterRegistry()
        table = self.require_table(table_name)
        enriched_values = table.calculator.evaluate(dict(values))
        output: dict[str, FeishuWriteValue] = {}
        for mapping in table.mappings:
            if mapping.standard_field_id not in enriched_values:
                raise ConfigurationError(
                    f"运行结果缺少已映射字段：{mapping.standard_field_id} "
                    f"（{table_name} / {mapping.feishu_column}）"
                )
            value = enriched_values[mapping.standard_field_id]
            if value is None:
                continue
            field = table.schema.require_name(mapping.feishu_column)
            output[mapping.feishu_column] = registry.adapt(field, value)
        return output

    def as_report(self) -> dict[str, Any]:
        return {
            "module_id": self.module_id,
            "data_api_field_ids": list(self.data_api_field_ids),
            "data_api_parts": list(self.request_plan.data_api_parts),
            "analytics_field_ids": list(self.analytics_field_ids),
            "analytics_metrics": list(self.request_plan.analytics_metrics),
            "analytics_dimensions": list(self.request_plan.analytics_dimensions),
            "reporting_field_ids": list(self.reporting_field_ids),
            "reporting_columns": list(self.request_plan.reporting_columns),
            "tables": {
                table.table_name: {
                    "table_id": table.table_id,
                    "mapping_count": len(table.mappings),
                    "safe_calculated_field_ids": list(table.calculator.calculated_field_ids),
                    "mappings": [
                        {
                            "standard_field_id": item.standard_field_id,
                            "feishu_column": item.feishu_column,
                            "feishu_field_id": item.feishu_field_id,
                            "feishu_type_code": item.feishu_type_code,
                            "feishu_type_name": item.feishu_type_name,
                            "source_kind": item.source_kind,
                            "official_field": item.official_field,
                            "source_data_type": item.source_data_type,
                        }
                        for item in table.mappings
                    ],
                }
                for table in self.tables
            },
        }


class DynamicModulePlanCompiler:
    """把字段目录、启用映射和飞书实时字段结构编译成可执行计划。"""

    def __init__(
        self,
        *,
        catalog: FieldCatalog,
        computed_field_ids: set[str],
        required_data_field_ids: Sequence[str],
        supported_api_sources: set[str] | None = None,
        adapters: FeishuValueAdapterRegistry | None = None,
    ) -> None:
        self.catalog = catalog
        self.computed_field_ids = computed_field_ids
        self.required_data_field_ids = tuple(required_data_field_ids)
        self.supported_api_sources = supported_api_sources or {"data_api"}
        self.adapters = adapters or FeishuValueAdapterRegistry()

    def compile(
        self,
        *,
        module_id: str,
        entity_level: str,
        table_ids: Mapping[str, str],
        mappings: Iterable[ModuleFieldMapping],
        raw_fields_by_table_id: Mapping[str, Iterable[Mapping[str, object]]],
    ) -> DynamicModulePlan:
        self._validate_module_code_fields(module_id)
        enabled = [item for item in mappings if item.enabled and item.module_id == module_id]
        duplicate_table_ids = sorted(
            table_id
            for table_id, count in Counter(table_ids.values()).items()
            if count > 1
        )
        if duplicate_table_ids:
            raise ConfigurationError(
                "多个业务表配置成了同一个目标表ID：" + "、".join(duplicate_table_ids)
            )
        unassigned = sorted(
            f"{item.standard_field_id}/{item.feishu_column}"
            for item in enabled
            if not item.target_table_id
        )
        if unassigned:
            raise ConfigurationError(
                "已启用字段映射缺少目标表ID：" + "、".join(unassigned)
            )
        configured_ids = set(table_ids.values())
        unknown_targets = sorted(
            {
                item.target_table_id
                for item in enabled
                if item.target_table_id and item.target_table_id not in configured_ids
            }
        )
        if unknown_targets:
            raise ConfigurationError(
                "字段映射指向未配置的业务表：" + "、".join(unknown_targets)
            )

        table_plans: list[CompiledTablePlan] = []
        mapped_api_field_ids: list[str] = []
        for table_name, table_id in table_ids.items():
            table_mappings = [item for item in enabled if item.target_table_id == table_id]
            if not table_mappings:
                raise ConfigurationError(f"{table_name} 没有已启用的字段映射。")
            _reject_duplicates(table_name, table_mappings)
            raw_fields = raw_fields_by_table_id.get(table_id)
            if raw_fields is None:
                raise ConfigurationError(f"没有读取到 {table_name} 的实时字段结构。")
            schema = FeishuTableSchema.from_api_fields(raw_fields)
            compiled_mappings: list[CompiledFieldMapping] = []
            safe_calculated_field_ids: list[str] = []
            for mapping in table_mappings:
                target = schema.require_name(mapping.feishu_column)
                if not self.adapters.supports(target):
                    raise ConfigurationError(
                        f"{table_name} / {mapping.feishu_column} 使用 {target.type_name}，"
                        "当前通用写入器不支持。"
                    )
                definition = self.catalog.get(mapping.standard_field_id)
                self.catalog.validate_requirements((mapping.standard_field_id,))
                if mapping.standard_field_id in self.computed_field_ids:
                    source_kind: Literal[
                        "data_api", "analytics_api", "reporting_api", "computed"
                    ] = "computed"
                    official_field = None
                    source_data_type = definition.data_type
                elif definition.calculation_mode == "safe_expression":
                    if definition.implementation_module not in {None, module_id}:
                        raise ConfigurationError(
                            f"安全计算字段 {mapping.standard_field_id} 登记给模块"
                            f" {definition.implementation_module}，不能由 {module_id} 使用。"
                        )
                    source_kind = "computed"
                    official_field = None
                    source_data_type = definition.data_type
                    safe_calculated_field_ids.append(mapping.standard_field_id)
                elif definition.is_api_field:
                    if definition.api_source not in self.supported_api_sources:
                        raise ConfigurationError(
                            f"字段 {mapping.standard_field_id} 来自 {definition.api_source}，"
                            f"当前模块尚未接入该来源。"
                        )
                    if definition.api_source == "data_api":
                        source_kind = "data_api"
                    elif definition.api_source == "analytics_api":
                        source_kind = "analytics_api"
                    else:
                        source_kind = "reporting_api"
                    official_field = definition.official_field
                    source_data_type = definition.data_type
                    mapped_api_field_ids.append(mapping.standard_field_id)
                else:
                    raise ConfigurationError(
                        f"字段 {mapping.standard_field_id} 的计算方式是"
                        f" {definition.calculation_mode}，既不是当前模块登记的输出字段，"
                        "也不是可自动执行的安全表达式字段。"
                    )
                _require_compatible_target_type(
                    table_name=table_name,
                    feishu_column=mapping.feishu_column,
                    source_data_type=source_data_type,
                    feishu_type_code=target.type_code,
                    feishu_type_name=target.type_name,
                )
                compiled_mappings.append(
                    CompiledFieldMapping(
                        standard_field_id=mapping.standard_field_id,
                        feishu_column=mapping.feishu_column,
                        feishu_field_id=target.field_id,
                        feishu_type_code=target.type_code,
                        feishu_type_name=target.type_name,
                        source_kind=source_kind,
                        official_field=official_field,
                        source_data_type=source_data_type,
                    )
                )
            calculator = CalculatedFieldEngine.compile(
                self.catalog,
                safe_calculated_field_ids,
                module_code_field_ids=self.computed_field_ids,
            )
            for field_id in calculator.required_api_field_ids:
                definition = self.catalog.get(field_id)
                if definition.api_source not in self.supported_api_sources:
                    raise ConfigurationError(
                        f"安全计算字段依赖 {field_id}，来源是 {definition.api_source}，"
                        "当前模块尚未接入该来源。"
                    )
                _append_unique(mapped_api_field_ids, field_id)
            table_plans.append(
                CompiledTablePlan(
                    table_name=table_name,
                    table_id=table_id,
                    schema=schema,
                    mappings=tuple(compiled_mappings),
                    calculator=calculator,
                )
            )

        all_api_field_ids = list(
            dict.fromkeys((*self.required_data_field_ids, *mapped_api_field_ids))
        )
        data_field_ids = [
            field_id
            for field_id in all_api_field_ids
            if self.catalog.get(field_id).api_source == "data_api"
        ]
        analytics_field_ids = [
            field_id
            for field_id in all_api_field_ids
            if self.catalog.get(field_id).api_source == "analytics_api"
        ]
        reporting_field_ids = [
            field_id
            for field_id in all_api_field_ids
            if self.catalog.get(field_id).api_source == "reporting_api"
        ]
        if analytics_field_ids:
            AnalyticsMetricExtractor.compile(
                self.catalog,
                analytics_field_ids,
                entity_level=entity_level,
            )
        if reporting_field_ids:
            ReportingReachExtractor.compile(self.catalog, reporting_field_ids)
        extractor = DataApiScalarExtractor.compile(
            self.catalog,
            data_field_ids,
            entity_level=entity_level,
        )
        request_plan = self.catalog.build_request_plan(module_id, all_api_field_ids)
        return DynamicModulePlan(
            module_id=module_id,
            request_plan=request_plan,
            extractor=extractor,
            data_api_field_ids=tuple(data_field_ids),
            analytics_field_ids=tuple(analytics_field_ids),
            reporting_field_ids=tuple(reporting_field_ids),
            tables=tuple(table_plans),
        )

    def _validate_module_code_fields(self, module_id: str) -> None:
        for field_id in sorted(self.computed_field_ids):
            self.catalog.validate_requirements((field_id,))
            definition = self.catalog.get(field_id)
            if definition.calculation_mode != "module_code":
                raise ConfigurationError(
                    f"模块 {module_id} 将 {field_id} 登记为代码输出，"
                    f"但字段字典中的计算方式是 {definition.calculation_mode}。"
                )
            if definition.implementation_status in {"planned", "deprecated"}:
                raise ConfigurationError(
                    f"模块 {module_id} 的代码输出字段 {field_id} 实现状态是"
                    f" {definition.implementation_status}，暂不可执行。"
                )
            if definition.implementation_module not in {None, module_id}:
                raise ConfigurationError(
                    f"模块 {module_id} 将 {field_id} 登记为代码输出，"
                    f"但字段字典登记的实现模块是 {definition.implementation_module}。"
                )


def _reject_duplicates(table_name: str, mappings: list[ModuleFieldMapping]) -> None:
    standard_counts = Counter(item.standard_field_id for item in mappings)
    column_counts = Counter(item.feishu_column for item in mappings)
    duplicate_ids = sorted(key for key, count in standard_counts.items() if count > 1)
    duplicate_columns = sorted(key for key, count in column_counts.items() if count > 1)
    if duplicate_ids or duplicate_columns:
        parts = []
        if duplicate_ids:
            parts.append("标准字段ID重复：" + "、".join(duplicate_ids))
        if duplicate_columns:
            parts.append("飞书列重复：" + "、".join(duplicate_columns))
        raise ConfigurationError(f"{table_name} 映射冲突：" + "；".join(parts))


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def _require_compatible_target_type(
    *,
    table_name: str,
    feishu_column: str,
    source_data_type: str,
    feishu_type_code: int,
    feishu_type_name: str,
) -> None:
    compatible_types = _COMPATIBLE_FEISHU_TYPES_BY_SOURCE.get(source_data_type)
    if compatible_types is None:
        return
    if feishu_type_code not in compatible_types:
        raise ConfigurationError(
            f"{table_name} / {feishu_column} 类型冲突：来源是 {source_data_type}，"
            f"目标是飞书{feishu_type_name}。"
        )
