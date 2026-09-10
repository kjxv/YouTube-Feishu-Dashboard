from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from youtube_feishu_dashboard.core.errors import FieldCatalogError
from youtube_feishu_dashboard.db.repositories import Storage

FieldSource = Literal[
    "data_api",
    "analytics_api",
    "reporting_api",
    "system_generated",
    "system_time",
    "system_judgment",
    "system_calculated",
    "system_state",
    "system_enum",
    "system_aggregate",
    "feishu_formula",
    "manual",
]
FieldRole = Literal[
    "resource",
    "metric",
    "dimension",
    "report_column",
    "computed",
    "system",
    "formula",
    "manual",
]
CalculationMode = Literal[
    "none",
    "module_code",
    "safe_expression",
    "feishu_formula",
    "manual",
]
ImplementationStatus = Literal[
    "planned",
    "specified",
    "implemented",
    "tested",
    "deprecated",
]
NullPolicy = Literal["allow", "propagate", "zero", "error"]

API_SOURCES = frozenset({"data_api", "analytics_api", "reporting_api"})

FIELD_SOURCE_CN: dict[FieldSource, str] = {
    "data_api": "YouTube Data API",
    "analytics_api": "YouTube Analytics API",
    "reporting_api": "YouTube Reporting API",
    "system_generated": "非API（系统生成）",
    "system_time": "非API（系统时间）",
    "system_judgment": "非API（系统判断）",
    "system_calculated": "非API（系统计算）",
    "system_state": "非API（系统状态）",
    "system_enum": "非API（系统枚举）",
    "system_aggregate": "非API（系统聚合）",
    "feishu_formula": "非API（飞书公式）",
    "manual": "非API（手动填写）",
}

# 保留旧名称，避免现有扩展代码立即失效。
API_SOURCE_CN = FIELD_SOURCE_CN

FIELD_ROLE_CN: dict[FieldRole, str] = {
    "resource": "API资源字段",
    "metric": "API指标",
    "dimension": "API维度",
    "report_column": "报表列",
    "computed": "计算字段",
    "system": "系统字段",
    "formula": "飞书公式字段",
    "manual": "手动字段",
}

CALCULATION_MODE_CN: dict[CalculationMode, str] = {
    "none": "不需要计算",
    "module_code": "功能模块代码",
    "safe_expression": "通用计算引擎",
    "feishu_formula": "飞书多维表格",
    "manual": "手动填写",
}

NULL_POLICY_CN: dict[NullPolicy, str] = {
    "allow": "允许为空",
    "propagate": "任一依赖为空则为空",
    "zero": "空值按0",
    "error": "空值时报错",
}

CURRENT_AVAILABILITY_FIELD = "当前可用状态"
LEGACY_IMPLEMENTATION_STATUS_FIELD = "实现状态"

# 内部继续沿用既有英文状态值，以兼容本地 JSON、数据库和历史飞书记录；
# 面向用户只展示三个能直接指导操作的状态。
CURRENT_AVAILABILITY_CN: dict[ImplementationStatus, str] = {
    "planned": "底层未实现",
    "specified": "底层未实现",
    "implemented": "底层已实现，待接入主程序",
    "tested": "可直接使用",
    "deprecated": "底层未实现",
}

# 保留旧常量名，避免既有扩展代码导入失败。
IMPLEMENTATION_STATUS_CN = CURRENT_AVAILABILITY_CN


class FieldDefinition(BaseModel):
    """API 与非 API 标准字段共用的稳定定义。"""

    standard_field_id: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$")
    cn_name: str
    # 为兼容既有 JSON、数据库和调用方，内部暂时保留 api_source 名称；
    # 它现在表达完整的“字段来源类型”，不再只限于外部 API。
    api_source: FieldSource
    official_field: str | None = None
    data_type: str
    unit: str | None = None
    entity_level: str
    query_group: str
    role: FieldRole
    default_part: str | None = None
    required_scopes: tuple[str, ...] = ()
    calculation_mode: CalculationMode = "none"
    dependency_field_ids: tuple[str, ...] = ()
    calculation_description: str | None = None
    calculation_expression: str | None = None
    boundary_rules: str | None = None
    null_policy: NullPolicy = "allow"
    output_precision: int | None = Field(default=None, ge=0, le=12)
    implementation_module: str | None = None
    implementation_status: ImplementationStatus = "implemented"
    rule_version: str = "1"
    enabled: bool = True
    notes: str | None = None

    @model_validator(mode="after")
    def validate_source_contract(self) -> FieldDefinition:
        if self.api_source in API_SOURCES and not self.official_field:
            raise ValueError("API 字段必须填写 official_field。")
        if self.calculation_mode == "safe_expression" and not self.calculation_expression:
            raise ValueError("安全表达式字段必须填写 calculation_expression。")
        if self.calculation_mode == "feishu_formula" and self.api_source != "feishu_formula":
            raise ValueError("feishu_formula 计算方式必须使用 feishu_formula 字段来源。")
        if self.api_source == "feishu_formula" and self.calculation_mode != "feishu_formula":
            raise ValueError("飞书公式字段必须使用 feishu_formula 计算方式。")
        return self

    @property
    def source_type(self) -> FieldSource:
        """面向新代码提供语义准确的字段来源名称。"""
        return self.api_source

    @property
    def is_api_field(self) -> bool:
        return self.api_source in API_SOURCES

    @property
    def is_directly_usable(self) -> bool:
        """只有已经完成主程序接入的字段才允许进入正式写入映射。"""

        return self.implementation_status == "tested"

    @property
    def current_availability_cn(self) -> str:
        return CURRENT_AVAILABILITY_CN[self.implementation_status]

    def to_repository_dict(self) -> dict[str, object]:
        return {
            "standard_field_id": self.standard_field_id,
            "cn_name": self.cn_name,
            "api_source": self.api_source,
            "official_field": self.official_field or "",
            "data_type": self.data_type,
            "unit": self.unit,
            "entity_level": self.entity_level,
            "query_group": self.query_group,
            "enabled": self.enabled,
            "extra": {
                "role": self.role,
                "default_part": self.default_part,
                "required_scopes": list(self.required_scopes),
                "calculation_mode": self.calculation_mode,
                "dependency_field_ids": list(self.dependency_field_ids),
                "calculation_description": self.calculation_description,
                "calculation_expression": self.calculation_expression,
                "boundary_rules": self.boundary_rules,
                "null_policy": self.null_policy,
                "output_precision": self.output_precision,
                "implementation_module": self.implementation_module,
                "implementation_status": self.implementation_status,
                "rule_version": self.rule_version,
                "notes": self.notes,
            },
        }


class CatalogDocument(BaseModel):
    catalog_version: str
    generated_at: str
    coverage: str
    fields: tuple[FieldDefinition, ...]


class CurrentAvailabilityAudit(BaseModel):
    """内置字段逐项审计结果；每个字段必须且只能落入一个状态组。"""

    audit_version: str
    audited_at: str
    scope: str
    status_groups: dict[ImplementationStatus, tuple[str, ...]]
    criteria: dict[ImplementationStatus, str]


@dataclass(frozen=True, slots=True)
class ApiRequestPlan:
    module_id: str
    field_ids: tuple[str, ...]
    data_api_parts: tuple[str, ...]
    analytics_metrics: tuple[str, ...]
    analytics_dimensions: tuple[str, ...]
    reporting_columns: tuple[str, ...]
    required_scopes: tuple[str, ...]


class FieldCatalog:
    def __init__(self, document: CatalogDocument) -> None:
        self.document = document
        self._by_id = {item.standard_field_id: item for item in document.fields}
        if len(self._by_id) != len(document.fields):
            raise FieldCatalogError("标准字段目录包含重复的 standard_field_id。")

    @classmethod
    def load_builtin(cls) -> FieldCatalog:
        root = files("youtube_feishu_dashboard.catalog")
        base = CatalogDocument.model_validate_json(
            root.joinpath("api_fields.v1.json").read_text(encoding="utf-8")
        )
        extension = CatalogDocument.model_validate_json(
            root.joinpath("api_fields.v2.additions.json").read_text(encoding="utf-8")
        )
        system = CatalogDocument.model_validate_json(
            root.joinpath("system_fields.v1.json").read_text(encoding="utf-8")
        )
        channel = CatalogDocument.model_validate_json(
            root.joinpath("channel_fields.v1.json").read_text(encoding="utf-8")
        )
        milestones = CatalogDocument.model_validate_json(
            root.joinpath("latest_milestone_fields.v1.json").read_text(encoding="utf-8")
        )
        availability = CurrentAvailabilityAudit.model_validate_json(
            root.joinpath("current_availability.v1.json").read_text(encoding="utf-8")
        )
        fields = _apply_current_availability_audit(
            base.fields
            + extension.fields
            + system.fields
            + channel.fields
            + milestones.fields,
            availability,
        )
        combined = CatalogDocument(
            catalog_version=milestones.catalog_version,
            generated_at=milestones.generated_at,
            coverage=(
                f"{extension.coverage}；{system.coverage}；"
                f"{channel.coverage}；{milestones.coverage}"
            ),
            fields=fields,
        )
        return cls(combined)

    @classmethod
    def load_path(cls, path: Path) -> FieldCatalog:
        return cls(CatalogDocument.model_validate_json(path.read_text(encoding="utf-8")))

    def get(self, field_id: str) -> FieldDefinition:
        try:
            return self._by_id[field_id]
        except KeyError as exc:
            raise FieldCatalogError(f"未知标准字段：{field_id}") from exc

    def overlay_feishu_records(self, records: list[dict[str, Any]]) -> FieldCatalog:
        """用飞书字段字典覆盖内置目录；新字段仅自动支持普通 Data API 资源字段。"""
        merged = {item.standard_field_id: item for item in self.document.fields}
        source_aliases: dict[str, FieldSource] = {
            "data_api": "data_api",
            "YouTube Data API": "data_api",
            "analytics_api": "analytics_api",
            "YouTube Analytics API": "analytics_api",
            "reporting_api": "reporting_api",
            "YouTube Reporting API": "reporting_api",
            "system_generated": "system_generated",
            "非API（系统生成）": "system_generated",
            "system_time": "system_time",
            "非API（系统时间）": "system_time",
            "system_judgment": "system_judgment",
            "非API（系统判断）": "system_judgment",
            "system_calculated": "system_calculated",
            "非API（系统计算）": "system_calculated",
            "system_state": "system_state",
            "非API（系统状态）": "system_state",
            "system_enum": "system_enum",
            "非API（系统枚举）": "system_enum",
            "system_aggregate": "system_aggregate",
            "非API（系统聚合）": "system_aggregate",
            "feishu_formula": "feishu_formula",
            "非API（飞书公式）": "feishu_formula",
            "manual": "manual",
            "非API（手动填写）": "manual",
        }
        remote_field_ids = [
            field_id
            for record in records
            if (field_id := _scalar_text(record.get("fields", {}).get("标准字段ID")))
        ]
        duplicate_remote_ids = sorted(
            field_id
            for field_id, count in Counter(remote_field_ids).items()
            if count > 1
        )
        if duplicate_remote_ids:
            raise FieldCatalogError(
                "飞书 API 字段字典包含重复的标准字段ID："
                + "、".join(duplicate_remote_ids)
            )
        for record in records:
            fields = record.get("fields", {})
            field_id = _scalar_text(fields.get("标准字段ID"))
            if not field_id:
                continue
            existing = merged.get(field_id)
            source_text = _scalar_text(fields.get("API来源"))
            api_source = source_aliases.get(source_text or "")
            official_field = _scalar_text(fields.get("官方字段"))
            data_type = _scalar_text(fields.get("数据类型"))
            entity_level = _scalar_text(fields.get("数据层级"))
            query_group = _scalar_text(fields.get("查询组"))
            cn_name = _scalar_text(fields.get("中文名称"))
            missing = [
                name
                for name, value in (
                    ("API来源", api_source),
                    ("数据类型", data_type),
                    ("数据层级", entity_level),
                    ("查询组", query_group),
                    ("中文名称", cn_name),
                )
                if not value
            ]
            if api_source in API_SOURCES and not official_field:
                missing.append("官方字段")
            if missing:
                raise FieldCatalogError(
                    f"飞书 API 字段字典中的 {field_id} 缺少或无法识别：{'、'.join(missing)}"
                )
            assert api_source and data_type and entity_level and query_group
            assert cn_name
            if existing is None and api_source in {"analytics_api", "reporting_api"}:
                raise FieldCatalogError(
                    f"飞书新增字段 {field_id} 来自 {api_source}，需要专用查询模板，"
                    "不能按普通 Data API 字段自动接入。"
                )
            default_part = (
                existing.default_part
                if existing is not None
                else (
                    official_field.split(".", maxsplit=1)[0]
                    if api_source == "data_api" and official_field
                    else None
                )
            )
            calculation_mode = (
                existing.calculation_mode
                if existing is not None
                else _calculation_mode(fields.get("计算位置"), api_source)
            )
            availability_value = fields.get(CURRENT_AVAILABILITY_FIELD)
            if availability_value in (None, ""):
                availability_value = fields.get(LEGACY_IMPLEMENTATION_STATUS_FIELD)
            merged[field_id] = FieldDefinition(
                standard_field_id=field_id,
                cn_name=cn_name,
                api_source=api_source,
                official_field=official_field,
                data_type=data_type,
                unit=_scalar_text(fields.get("单位")),
                entity_level=entity_level,
                query_group=query_group,
                role=(
                    existing.role
                    if existing is not None
                    else _field_role(fields.get("字段角色"), api_source)
                ),
                default_part=default_part,
                required_scopes=(
                    existing.required_scopes
                    if existing is not None
                    else (("youtube.readonly",) if api_source == "data_api" else ())
                ),
                calculation_mode=calculation_mode,
                dependency_field_ids=(
                    existing.dependency_field_ids
                    if existing is not None
                    else _text_tuple(fields.get("依赖标准字段"))
                ),
                calculation_description=(
                    _scalar_text(fields.get("计算说明"))
                    or (existing.calculation_description if existing is not None else None)
                ),
                calculation_expression=(
                    _scalar_text(fields.get("机器计算表达式"))
                    or (existing.calculation_expression if existing is not None else None)
                ),
                boundary_rules=(
                    _scalar_text(fields.get("边界与空值规则"))
                    or (existing.boundary_rules if existing is not None else None)
                ),
                null_policy=(
                    existing.null_policy
                    if existing is not None
                    else _null_policy(fields.get("空值策略"))
                ),
                output_precision=(
                    existing.output_precision
                    if existing is not None
                    else _optional_int(fields.get("输出精度"))
                ),
                implementation_module=(
                    _scalar_text(fields.get("实现模块"))
                    or (existing.implementation_module if existing is not None else None)
                ),
                implementation_status=(
                    _implementation_status(availability_value)
                    if availability_value not in (None, "")
                    else (
                        existing.implementation_status
                        if existing is not None
                        else "planned"
                    )
                ),
                rule_version=(
                    _scalar_text(fields.get("规则版本"))
                    or (existing.rule_version if existing is not None else "1")
                ),
                enabled=_enabled_value(fields.get("启用", True)),
                notes=(
                    _scalar_text(fields.get("备注"))
                    or (existing.notes if existing is not None else "飞书标准字段字典动态字段")
                ),
            )
        return FieldCatalog(
            CatalogDocument(
                catalog_version=f"{self.document.catalog_version}+feishu",
                generated_at=self.document.generated_at,
                coverage=self.document.coverage,
                fields=tuple(merged.values()),
            )
        )

    def validate_requirements(self, field_ids: list[str] | tuple[str, ...]) -> None:
        missing = sorted(set(field_ids) - self._by_id.keys())
        disabled = sorted(
            item for item in field_ids if item in self._by_id and not self._by_id[item].enabled
        )
        if missing or disabled:
            parts = []
            if missing:
                parts.append(f"未知字段：{', '.join(missing)}")
            if disabled:
                parts.append(f"已停用字段：{', '.join(disabled)}")
            raise FieldCatalogError("；".join(parts))

    def build_request_plan(self, module_id: str, field_ids: list[str]) -> ApiRequestPlan:
        self.validate_requirements(field_ids)
        selected = [self._by_id[item] for item in dict.fromkeys(field_ids)]
        parts = {
            item.default_part
            for item in selected
            if item.api_source == "data_api" and item.default_part
        }
        analytics_metrics = {
            item.official_field
            for item in selected
            if item.api_source == "analytics_api"
            and item.role == "metric"
            and item.official_field
        }
        analytics_dimensions = {
            item.official_field
            for item in selected
            if item.api_source == "analytics_api"
            and item.role == "dimension"
            and item.official_field
        }
        reporting_columns = {
            item.official_field
            for item in selected
            if item.api_source == "reporting_api" and item.official_field
        }
        scopes = {scope for item in selected for scope in item.required_scopes}
        return ApiRequestPlan(
            module_id=module_id,
            field_ids=tuple(item.standard_field_id for item in selected),
            data_api_parts=tuple(sorted(parts)),
            analytics_metrics=tuple(sorted(analytics_metrics)),
            analytics_dimensions=tuple(sorted(analytics_dimensions)),
            reporting_columns=tuple(sorted(reporting_columns)),
            required_scopes=tuple(sorted(scopes)),
        )

    def sync_to_storage(self, storage: Storage) -> None:
        with storage.transaction() as repos:
            repos.catalog.replace_catalog(
                self.document.catalog_version,
                [item.to_repository_dict() for item in self.document.fields],
            )

    def as_feishu_seed_records(self) -> list[dict[str, object]]:
        return [
            {
                "标准字段ID": item.standard_field_id,
                "中文名称": item.cn_name,
                "API来源": FIELD_SOURCE_CN[item.api_source],
                "官方字段": item.official_field or "",
                "数据类型": item.data_type,
                "单位": item.unit or "",
                "数据层级": item.entity_level,
                "查询组": item.query_group,
                "字段角色": FIELD_ROLE_CN[item.role],
                "计算位置": CALCULATION_MODE_CN[item.calculation_mode],
                "依赖标准字段": ", ".join(item.dependency_field_ids),
                "计算说明": item.calculation_description or "",
                "机器计算表达式": item.calculation_expression or "",
                "边界与空值规则": item.boundary_rules or "",
                "空值策略": NULL_POLICY_CN[item.null_policy],
                "输出精度": (
                    "" if item.output_precision is None else str(item.output_precision)
                ),
                "实现模块": item.implementation_module or "",
                CURRENT_AVAILABILITY_FIELD: item.current_availability_cn,
                "规则版本": item.rule_version,
                "备注": item.notes or "",
                "启用": item.enabled,
                "目录版本": self.document.catalog_version,
            }
            for item in self.document.fields
        ]

    def to_json(self) -> str:
        return json.dumps(self.document.model_dump(mode="json"), ensure_ascii=False, indent=2)


def _apply_current_availability_audit(
    fields: tuple[FieldDefinition, ...],
    audit: CurrentAvailabilityAudit,
) -> tuple[FieldDefinition, ...]:
    allowed_statuses: set[ImplementationStatus] = {"tested", "implemented", "planned"}
    unexpected_statuses = sorted(set(audit.status_groups) - allowed_statuses)
    missing_status_groups = sorted(allowed_statuses - set(audit.status_groups))
    if unexpected_statuses or missing_status_groups:
        parts: list[str] = []
        if unexpected_statuses:
            parts.append("包含非当前三态值：" + "、".join(unexpected_statuses))
        if missing_status_groups:
            parts.append("缺少状态组：" + "、".join(missing_status_groups))
        raise FieldCatalogError("当前可用状态审计文件无效：" + "；".join(parts))

    audited_pairs = [
        (field_id, status)
        for status, field_ids in audit.status_groups.items()
        for field_id in field_ids
    ]
    counts = Counter(field_id for field_id, _ in audited_pairs)
    duplicates = sorted(field_id for field_id, count in counts.items() if count > 1)
    known_ids = {item.standard_field_id for item in fields}
    audited_ids = set(counts)
    unknown = sorted(audited_ids - known_ids)
    missing = sorted(known_ids - audited_ids)
    if duplicates or unknown or missing:
        parts = []
        if duplicates:
            parts.append("重复字段：" + "、".join(duplicates))
        if unknown:
            parts.append("未知字段：" + "、".join(unknown))
        if missing:
            parts.append("未审计字段：" + "、".join(missing))
        raise FieldCatalogError("当前可用状态审计未完整覆盖内置目录：" + "；".join(parts))

    status_by_id = {field_id: status for field_id, status in audited_pairs}
    return tuple(
        item.model_copy(
            update={"implementation_status": status_by_id[item.standard_field_id]}
        )
        for item in fields
    )


def _scalar_text(value: Any) -> str | None:
    if isinstance(value, list):
        return _scalar_text(value[0]) if value else None
    if isinstance(value, dict):
        for key in ("text", "name", "value"):
            if key in value:
                return _scalar_text(value[key])
    if value is None or not str(value).strip():
        return None
    return str(value).strip()


def _enabled_value(value: Any) -> bool:
    text = _scalar_text(value)
    if text is None:
        return bool(value)
    return text.lower() not in {"false", "0", "否", "停用", "disabled"}


def _default_role(source: FieldSource) -> FieldRole:
    if source == "feishu_formula":
        return "formula"
    if source == "manual":
        return "manual"
    if source in API_SOURCES:
        return "resource"
    if source in {"system_generated", "system_time", "system_state", "system_enum"}:
        return "system"
    return "computed"


def _field_role(value: Any, source: FieldSource) -> FieldRole:
    aliases: dict[str, FieldRole] = {
        "resource": "resource",
        "API资源字段": "resource",
        "metric": "metric",
        "API指标": "metric",
        "dimension": "dimension",
        "API维度": "dimension",
        "report_column": "report_column",
        "报表列": "report_column",
        "computed": "computed",
        "计算字段": "computed",
        "system": "system",
        "系统字段": "system",
        "formula": "formula",
        "飞书公式字段": "formula",
        "manual": "manual",
        "手动字段": "manual",
    }
    text = _scalar_text(value)
    return aliases.get(text or "", _default_role(source))


def _calculation_mode(value: Any, source: FieldSource) -> CalculationMode:
    aliases: dict[str, CalculationMode] = {
        "none": "none",
        "不需要计算": "none",
        "module_code": "module_code",
        "功能模块代码": "module_code",
        "safe_expression": "safe_expression",
        "通用计算引擎": "safe_expression",
        "feishu_formula": "feishu_formula",
        "飞书多维表格": "feishu_formula",
        "manual": "manual",
        "手动填写": "manual",
    }
    text = _scalar_text(value)
    if text in aliases:
        return aliases[text]
    if source in API_SOURCES:
        return "none"
    if source == "feishu_formula":
        return "feishu_formula"
    if source == "manual":
        return "manual"
    return "module_code"


def _implementation_status(value: Any) -> ImplementationStatus:
    aliases: dict[str, ImplementationStatus] = {
        "底层未实现": "specified",
        "底层已实现，待接入主程序": "implemented",
        "底层已实现,待接入主程序": "implemented",
        "可直接使用": "tested",
        "planned": "planned",
        "待规划": "planned",
        "待配置": "planned",
        "specified": "specified",
        "已定义": "specified",
        "待实现": "specified",
        "implemented": "implemented",
        "已实现": "implemented",
        "tested": "tested",
        "已接入": "tested",
        "已测试": "tested",
        "飞书公式实现": "tested",
        "deprecated": "deprecated",
        "已停用": "deprecated",
        "暂停使用": "deprecated",
    }
    text = _scalar_text(value)
    return aliases.get(text or "", "planned")


def _null_policy(value: Any) -> NullPolicy:
    aliases: dict[str, NullPolicy] = {
        "allow": "allow",
        "允许为空": "allow",
        "propagate": "propagate",
        "任一依赖为空则为空": "propagate",
        "zero": "zero",
        "空值按0": "zero",
        "error": "error",
        "空值时报错": "error",
    }
    text = _scalar_text(value)
    return aliases.get(text or "", "allow")


def _text_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        values = [_scalar_text(item) for item in value]
        return tuple(dict.fromkeys(item for item in values if item))
    text = _scalar_text(value)
    if not text:
        return ()
    normalized = text.replace("；", ",").replace(";", ",").replace("\n", ",")
    return tuple(dict.fromkeys(part.strip() for part in normalized.split(",") if part.strip()))


def _optional_int(value: Any) -> int | None:
    scalar = _scalar_text(value)
    if scalar is None:
        return None
    try:
        return int(float(scalar))
    except (TypeError, ValueError) as exc:
        raise FieldCatalogError(f"输出精度必须是整数：{scalar}") from exc
