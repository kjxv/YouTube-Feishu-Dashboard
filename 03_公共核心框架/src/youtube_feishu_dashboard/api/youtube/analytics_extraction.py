from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import TypeAlias

from youtube_feishu_dashboard.api.youtube.schemas import AnalyticsTable
from youtube_feishu_dashboard.catalog.field_catalog import FieldCatalog, FieldDefinition
from youtube_feishu_dashboard.core.errors import FieldCatalogError

AnalyticsMetricValue: TypeAlias = int | float | None

SUPPORTED_ANALYTICS_METRIC_TYPES = frozenset({"integer", "number"})


@dataclass(frozen=True, slots=True)
class AnalyticsMetricExtraction:
    """一个无维度汇总行转换成标准字段后的结果。"""

    values: dict[str, AnalyticsMetricValue]
    empty: bool


class AnalyticsMetricExtractor:
    """根据字段字典把 Analytics 官方指标列转换成标准字段。"""

    def __init__(self, fields: tuple[FieldDefinition, ...]) -> None:
        self.fields = fields
        self.official_metrics = tuple(
            str(field.official_field) for field in fields if field.official_field
        )

    @classmethod
    def compile(
        cls,
        catalog: FieldCatalog,
        field_ids: Sequence[str],
        *,
        entity_level: str = "video",
    ) -> AnalyticsMetricExtractor:
        unique_ids = tuple(dict.fromkeys(field_ids))
        catalog.validate_requirements(unique_ids)
        fields: list[FieldDefinition] = []
        errors: list[str] = []

        for field_id in unique_ids:
            definition = catalog.get(field_id)
            if definition.api_source != "analytics_api":
                errors.append(
                    f"{field_id} 来自 {definition.api_source}，不是 Analytics API 字段"
                )
                continue
            if definition.role != "metric":
                errors.append(f"{field_id} 是 {definition.role}，不是可汇总的指标")
                continue
            supported_levels = {
                item.strip() for item in definition.entity_level.split("/") if item.strip()
            }
            if entity_level not in supported_levels:
                errors.append(
                    f"{field_id} 属于 {definition.entity_level} 层级，不适用于 {entity_level}"
                )
                continue
            if definition.data_type not in SUPPORTED_ANALYTICS_METRIC_TYPES:
                errors.append(
                    f"{field_id} 的类型 {definition.data_type} 不是受支持的数值指标"
                )
                continue
            if not definition.official_field:
                errors.append(f"{field_id} 缺少 Analytics 官方指标名")
                continue
            fields.append(definition)

        if not unique_ids:
            errors.append("至少需要一个 Analytics 指标")
        if errors:
            raise FieldCatalogError("Analytics 指标计划不可执行：" + "；".join(errors))
        return cls(tuple(fields))

    def extract_summary(self, table: AnalyticsTable) -> AnalyticsMetricExtraction:
        index = _column_index(table.columns)
        missing = [metric for metric in self.official_metrics if metric not in index]
        if missing:
            raise FieldCatalogError(
                "Analytics 汇总结果缺少指标列：" + "、".join(missing)
            )
        if len(table.rows) > 1:
            raise FieldCatalogError(
                f"Analytics 无维度汇总应最多返回一行，实际返回 {len(table.rows)} 行"
            )
        if not table.rows:
            return AnalyticsMetricExtraction(
                values={field.standard_field_id: None for field in self.fields},
                empty=True,
            )

        row = table.rows[0]
        _require_row_width(row, table.columns)
        values = {
            field.standard_field_id: _normalize_metric(
                field,
                row[index[str(field.official_field)]],
            )
            for field in self.fields
        }
        return AnalyticsMetricExtraction(values=values, empty=False)


def latest_returned_day(table: AnalyticsTable) -> date | None:
    """读取 day 维度真实返回的最后日期，不使用请求 endDate 冒充。"""

    index = _column_index(table.columns)
    if "day" not in index:
        raise FieldCatalogError("Analytics 按日结果缺少 day 维度列")
    days: list[date] = []
    for row in table.rows:
        _require_row_width(row, table.columns)
        try:
            days.append(date.fromisoformat(str(row[index["day"]])))
        except ValueError as exc:
            raise FieldCatalogError(
                f"Analytics day 维度不是 YYYY-MM-DD：{row[index['day']]!r}"
            ) from exc
    return max(days) if days else None


def _column_index(columns: tuple[str, ...]) -> dict[str, int]:
    result = {name: position for position, name in enumerate(columns)}
    if len(result) != len(columns):
        raise FieldCatalogError("Analytics 结果包含重复列名")
    return result


def _require_row_width(row: tuple[object, ...], columns: tuple[str, ...]) -> None:
    if len(row) != len(columns):
        raise FieldCatalogError(
            f"Analytics 结果列数为 {len(columns)}，但某行包含 {len(row)} 个值"
        )


def _normalize_metric(
    definition: FieldDefinition,
    value: object,
) -> AnalyticsMetricValue:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise FieldCatalogError(
            f"Analytics 字段 {definition.standard_field_id} 返回了布尔值"
        )
    try:
        if definition.data_type == "integer":
            return int(str(value))
        return float(str(value))
    except (TypeError, ValueError) as exc:
        raise FieldCatalogError(
            f"Analytics 字段 {definition.standard_field_id} 无法按"
            f" {definition.data_type} 解析：{value!r}"
        ) from exc
