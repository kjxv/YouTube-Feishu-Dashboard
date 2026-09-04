from __future__ import annotations

import math
from builtins import property as builtin_property
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any, TypeAlias

from youtube_feishu_dashboard.core.errors import ConfigurationError

FeishuWriteValue: TypeAlias = object
FieldAdapter: TypeAlias = Callable[["FeishuFieldSchema", object], FeishuWriteValue]

FEISHU_FIELD_TYPE_NAMES: dict[int, str] = {
    1: "文本",
    2: "数字",
    3: "单选",
    4: "多选",
    5: "日期时间",
    7: "复选框",
    11: "人员",
    13: "电话号码",
    15: "超链接",
    17: "附件",
    18: "单向关联",
    19: "查找引用",
    20: "公式",
    21: "双向关联",
    22: "地理位置",
    23: "群组",
    1001: "创建时间",
    1002: "最后更新时间",
    1003: "创建人",
    1004: "修改人",
    1005: "自动编号",
}


@dataclass(frozen=True, slots=True)
class FeishuFieldSchema:
    field_id: str
    field_name: str
    type_code: int
    property: dict[str, Any]
    is_primary: bool = False

    @builtin_property
    def type_name(self) -> str:
        return FEISHU_FIELD_TYPE_NAMES.get(self.type_code, f"未知类型({self.type_code})")


class FeishuTableSchema:
    """从飞书 list_fields 实时响应建立的只读字段索引。"""

    def __init__(self, fields: tuple[FeishuFieldSchema, ...]) -> None:
        self.fields = fields
        self._by_name = {field.field_name: field for field in fields}
        self._by_id = {field.field_id: field for field in fields}
        if len(self._by_name) != len(fields):
            raise ConfigurationError("飞书表结构包含重复字段名，无法确定映射目标。")
        if len(self._by_id) != len(fields):
            raise ConfigurationError("飞书表结构包含重复字段 ID，无法建立字段索引。")

    @classmethod
    def from_api_fields(cls, raw_fields: Iterable[Mapping[str, object]]) -> FeishuTableSchema:
        fields: list[FeishuFieldSchema] = []
        for position, raw in enumerate(raw_fields, start=1):
            field_id = str(raw.get("field_id") or "").strip()
            field_name = str(raw.get("field_name") or "").strip()
            raw_type = raw.get("type")
            if not field_id or not field_name or raw_type is None:
                raise ConfigurationError(
                    f"飞书第 {position} 个字段缺少 field_id、field_name 或 type。"
                )
            try:
                type_code = int(str(raw_type))
            except ValueError as exc:
                raise ConfigurationError(
                    f"飞书字段 {field_name} 的类型编号无效：{raw_type!r}"
                ) from exc
            raw_property = raw.get("property")
            if raw_property is None:
                field_property: dict[str, Any] = {}
            elif isinstance(raw_property, Mapping):
                field_property = dict(raw_property)
            else:
                raise ConfigurationError(f"飞书字段 {field_name} 的 property 不是对象。")
            fields.append(
                FeishuFieldSchema(
                    field_id=field_id,
                    field_name=field_name,
                    type_code=type_code,
                    property=field_property,
                    is_primary=bool(raw.get("is_primary", False)),
                )
            )
        return cls(tuple(fields))

    def require_name(self, field_name: str) -> FeishuFieldSchema:
        try:
            return self._by_name[field_name]
        except KeyError as exc:
            raise ConfigurationError(f"飞书业务表不存在映射列：{field_name}") from exc

    def require_id(self, field_id: str) -> FeishuFieldSchema:
        try:
            return self._by_id[field_id]
        except KeyError as exc:
            raise ConfigurationError(f"飞书业务表不存在字段 ID：{field_id}") from exc


class FeishuValueAdapterRegistry:
    """按飞书实时字段类型把标准值转换为记录写入值。"""

    def __init__(self, adapters: Mapping[int, FieldAdapter] | None = None) -> None:
        defaults: dict[int, FieldAdapter] = {
            1: _adapt_text,
            2: _adapt_number,
            3: _adapt_single_select,
            4: _adapt_multi_select,
            5: _adapt_datetime,
            7: _adapt_checkbox,
            15: _adapt_url,
        }
        if adapters:
            defaults.update(adapters)
        self._adapters = defaults

    @property
    def supported_type_codes(self) -> tuple[int, ...]:
        return tuple(sorted(self._adapters))

    def supports(self, field: FeishuFieldSchema) -> bool:
        return field.type_code in self._adapters

    def adapt(self, field: FeishuFieldSchema, value: object) -> FeishuWriteValue:
        if value is None:
            return None
        adapter = self._adapters.get(field.type_code)
        if adapter is None:
            raise ConfigurationError(
                f"飞书字段 {field.field_name} 使用 {field.type_name}，当前通用写入器不支持。"
            )
        try:
            return adapter(field, value)
        except ConfigurationError:
            raise
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(
                f"字段 {field.field_name} 无法转换为飞书{field.type_name}：{value!r}"
            ) from exc

    def adapt_record(
        self,
        schema: FeishuTableSchema,
        values_by_column: Mapping[str, object],
    ) -> dict[str, FeishuWriteValue]:
        return {
            column: self.adapt(schema.require_name(column), value)
            for column, value in values_by_column.items()
        }


def _adapt_text(field: FeishuFieldSchema, value: object) -> str:
    del field
    if isinstance(value, (Mapping, list, tuple, set)):
        raise ValueError("复合值不能写入文本字段")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _adapt_number(field: FeishuFieldSchema, value: object) -> int | float:
    del field
    if isinstance(value, bool):
        raise ValueError("布尔值不能写入数字字段")
    if isinstance(value, int):
        return value
    number = float(str(value))
    if not math.isfinite(number):
        raise ValueError("数字不能是 NaN 或无穷大")
    return number


def _adapt_single_select(field: FeishuFieldSchema, value: object) -> str:
    del field
    if isinstance(value, (Mapping, list, tuple, set)):
        raise ValueError("复合值不能写入单选字段")
    return str(value)


def _adapt_multi_select(field: FeishuFieldSchema, value: object) -> list[str]:
    del field
    if isinstance(value, str):
        return [value]
    if not isinstance(value, (list, tuple, set)):
        raise ValueError("多选字段需要字符串或一组值")
    if any(isinstance(item, (Mapping, list, tuple, set)) for item in value):
        raise ValueError("多选选项不能是复合值")
    return [str(item) for item in value]


def _adapt_datetime(field: FeishuFieldSchema, value: object) -> int:
    del field
    if isinstance(value, bool):
        raise ValueError("布尔值不能写入日期字段")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("时间戳不能是 NaN 或无穷大")
        return int(value)
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min, tzinfo=UTC)
    else:
        raw = str(value).strip()
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            parsed = datetime.combine(date.fromisoformat(raw), time.min, tzinfo=UTC)
    if parsed.tzinfo is None:
        raise ValueError("日期时间必须包含时区")
    return int(parsed.timestamp() * 1000)


def _adapt_checkbox(field: FeishuFieldSchema, value: object) -> bool:
    del field
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"true", "1"}:
        return True
    if normalized in {"false", "0"}:
        return False
    raise ValueError("无法识别复选框值")


def _adapt_url(field: FeishuFieldSchema, value: object) -> dict[str, str]:
    del field
    if isinstance(value, Mapping):
        raw_link = value.get("link", value.get("url"))
        if not raw_link:
            raise ValueError("超链接对象缺少 link 或 url")
        link = str(raw_link)
        text = str(value.get("text") or link)
    else:
        link = str(value)
        text = link
    if not link.startswith(("http://", "https://")):
        raise ValueError("超链接必须使用 http:// 或 https://")
    return {"link": link, "text": text}
