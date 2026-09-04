from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import TypeAlias

from youtube_feishu_dashboard.api.youtube.schemas import parse_api_datetime
from youtube_feishu_dashboard.catalog.field_catalog import FieldCatalog, FieldDefinition
from youtube_feishu_dashboard.core.errors import FieldCatalogError

DataApiScalarValue: TypeAlias = str | int | float | bool | date | datetime | None

SUPPORTED_DATA_API_SCALAR_TYPES = frozenset(
    {"string", "integer", "number", "boolean", "date", "datetime", "duration", "url"}
)

_MISSING = object()
_DURATION_PATTERN = re.compile(
    r"^P(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


@dataclass(frozen=True, slots=True)
class DataApiExtraction:
    """一次资源提取结果；缺失路径与明确返回 null 分开报告。"""

    values: dict[str, DataApiScalarValue]
    missing_field_ids: tuple[str, ...]
    null_field_ids: tuple[str, ...]


class DataApiScalarExtractor:
    """根据字段目录中的官方路径提取 YouTube Data API 普通单值字段。"""

    def __init__(self, *, entity_level: str, fields: tuple[FieldDefinition, ...]) -> None:
        self.entity_level = entity_level
        self.fields = fields
        self.required_parts = tuple(
            sorted({field.default_part for field in fields if field.default_part})
        )

    @classmethod
    def compile(
        cls,
        catalog: FieldCatalog,
        field_ids: Sequence[str],
        *,
        entity_level: str,
    ) -> DataApiScalarExtractor:
        unique_ids = tuple(dict.fromkeys(field_ids))
        catalog.validate_requirements(unique_ids)
        selected: list[FieldDefinition] = []
        errors: list[str] = []

        for field_id in unique_ids:
            definition = catalog.get(field_id)
            if definition.api_source != "data_api":
                errors.append(f"{field_id} 来自 {definition.api_source}，不是 Data API 资源字段")
                continue
            if definition.entity_level != entity_level:
                errors.append(
                    f"{field_id} 属于 {definition.entity_level} 层级，不适用于 {entity_level} 资源"
                )
                continue
            if definition.data_type not in SUPPORTED_DATA_API_SCALAR_TYPES:
                errors.append(
                    f"{field_id} 的类型 {definition.data_type} 不是普通单值类型"
                )
                continue
            if definition.official_field is None or not _valid_path(
                definition.official_field
            ):
                errors.append(f"{field_id} 的官方字段路径无效：{definition.official_field!r}")
                continue
            selected.append(definition)

        if errors:
            raise FieldCatalogError("Data API 普通字段计划不可执行：" + "；".join(errors))
        return cls(entity_level=entity_level, fields=tuple(selected))

    def extract(self, resource: Mapping[str, object]) -> DataApiExtraction:
        values: dict[str, DataApiScalarValue] = {}
        missing: list[str] = []
        nulls: list[str] = []

        for definition in self.fields:
            official_field = definition.official_field
            if official_field is None:
                raise FieldCatalogError(
                    f"Data API 字段 {definition.standard_field_id} 缺少官方字段路径"
                )
            raw_value = _read_path(resource, official_field)
            if raw_value is _MISSING:
                values[definition.standard_field_id] = None
                missing.append(definition.standard_field_id)
                continue
            if raw_value is None:
                values[definition.standard_field_id] = None
                nulls.append(definition.standard_field_id)
                continue
            values[definition.standard_field_id] = _normalize_value(definition, raw_value)

        return DataApiExtraction(
            values=values,
            missing_field_ids=tuple(missing),
            null_field_ids=tuple(nulls),
        )


def _valid_path(path: str) -> bool:
    return bool(path) and all(segment.strip() for segment in path.split("."))


def _read_path(resource: Mapping[str, object], path: str) -> object:
    current: object = resource
    for segment in path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            return _MISSING
        current = current[segment]
    return current


def _normalize_value(definition: FieldDefinition, value: object) -> DataApiScalarValue:
    try:
        if definition.data_type in {"string", "url"}:
            if isinstance(value, (Mapping, list, tuple, set)):
                raise ValueError("需要单值字符串")
            return str(value)
        if definition.data_type == "integer":
            if isinstance(value, bool):
                raise ValueError("布尔值不能作为整数")
            return int(str(value))
        if definition.data_type == "number":
            if isinstance(value, bool):
                raise ValueError("布尔值不能作为数字")
            return float(str(value))
        if definition.data_type == "boolean":
            return _normalize_boolean(value)
        if definition.data_type == "datetime":
            parsed = parse_api_datetime(str(value))
            if parsed is None:
                raise ValueError("日期时间为空")
            return parsed
        if definition.data_type == "date":
            return date.fromisoformat(str(value))
        if definition.data_type == "duration":
            return _duration_seconds(str(value))
    except (TypeError, ValueError) as exc:
        raise FieldCatalogError(
            f"字段 {definition.standard_field_id} 无法按 {definition.data_type} 解析：{value!r}"
        ) from exc
    raise FieldCatalogError(
        f"字段 {definition.standard_field_id} 使用了未注册类型：{definition.data_type}"
    )


def _normalize_boolean(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"true", "1"}:
        return True
    if normalized in {"false", "0"}:
        return False
    raise ValueError("无法识别布尔值")


def _duration_seconds(value: str) -> int:
    match = _DURATION_PATTERN.fullmatch(value)
    if match is None or not any(match.groupdict().values()):
        raise ValueError("不是受支持的 ISO 8601 时长")
    return (
        int(match.group("days") or 0) * 86400
        + int(match.group("hours") or 0) * 3600
        + int(match.group("minutes") or 0) * 60
        + int(match.group("seconds") or 0)
    )
