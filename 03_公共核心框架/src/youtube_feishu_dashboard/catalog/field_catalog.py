from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from youtube_feishu_dashboard.core.errors import FieldCatalogError
from youtube_feishu_dashboard.db.repositories import Storage

API_SOURCE_CN = {
    "data_api": "YouTube Data API",
    "analytics_api": "YouTube Analytics API",
    "reporting_api": "YouTube Reporting API",
}


class FieldDefinition(BaseModel):
    standard_field_id: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$")
    cn_name: str
    api_source: Literal["data_api", "analytics_api", "reporting_api"]
    official_field: str
    data_type: str
    unit: str | None = None
    entity_level: str
    query_group: str
    role: Literal["resource", "metric", "dimension", "report_column"]
    default_part: str | None = None
    required_scopes: tuple[str, ...] = ()
    enabled: bool = True
    notes: str | None = None

    def to_repository_dict(self) -> dict[str, object]:
        return {
            "standard_field_id": self.standard_field_id,
            "cn_name": self.cn_name,
            "api_source": self.api_source,
            "official_field": self.official_field,
            "data_type": self.data_type,
            "unit": self.unit,
            "entity_level": self.entity_level,
            "query_group": self.query_group,
            "enabled": self.enabled,
            "extra": {
                "role": self.role,
                "default_part": self.default_part,
                "required_scopes": list(self.required_scopes),
                "notes": self.notes,
            },
        }


class CatalogDocument(BaseModel):
    catalog_version: str
    generated_at: str
    coverage: str
    fields: tuple[FieldDefinition, ...]


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
            raise FieldCatalogError("API 字段目录包含重复的 standard_field_id。")

    @classmethod
    def load_builtin(cls) -> FieldCatalog:
        root = files("youtube_feishu_dashboard.catalog")
        base = CatalogDocument.model_validate_json(
            root.joinpath("api_fields.v1.json").read_text(encoding="utf-8")
        )
        extension = CatalogDocument.model_validate_json(
            root.joinpath("api_fields.v2.additions.json").read_text(encoding="utf-8")
        )
        combined = CatalogDocument(
            catalog_version=extension.catalog_version,
            generated_at=extension.generated_at,
            coverage=extension.coverage,
            fields=base.fields + extension.fields,
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
            if item.api_source == "analytics_api" and item.role == "metric"
        }
        analytics_dimensions = {
            item.official_field
            for item in selected
            if item.api_source == "analytics_api" and item.role == "dimension"
        }
        reporting_columns = {
            item.official_field for item in selected if item.api_source == "reporting_api"
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
                "API来源": API_SOURCE_CN[item.api_source],
                "官方字段": item.official_field,
                "数据类型": item.data_type,
                "单位": item.unit or "",
                "数据层级": item.entity_level,
                "查询组": item.query_group,
                "启用": item.enabled,
                "目录版本": self.document.catalog_version,
            }
            for item in self.document.fields
        ]

    def to_json(self) -> str:
        return json.dumps(self.document.model_dump(mode="json"), ensure_ascii=False, indent=2)
