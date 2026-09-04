"""飞书公共配置中心与最近一次有效配置缓存。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel

from youtube_feishu_dashboard.api.feishu.protocols import FeishuGateway
from youtube_feishu_dashboard.catalog.field_catalog import CatalogDocument, FieldCatalog
from youtube_feishu_dashboard.core.errors import (
    ConfigurationError,
    DashboardError,
    ExternalServiceError,
)
from youtube_feishu_dashboard.core.time import as_utc, utc_now
from youtube_feishu_dashboard.db.repositories import Storage

CACHE_KEY = "feishu_config_center_v1"


class ModuleFieldMapping(BaseModel):
    module_id: str
    standard_field_id: str
    feishu_column: str
    target_table_id: str | None = None
    enabled: bool = True


class ConfigSnapshot(BaseModel):
    project_config: dict[str, Any]
    account_config: dict[str, Any]
    module_mappings: tuple[ModuleFieldMapping, ...]
    version_hash: str
    loaded_at: datetime
    source: Literal["feishu", "cache"]
    fallback_error: str | None = None
    catalog_document: CatalogDocument | None = None


class FeishuConfigCenter:
    def __init__(
        self,
        *,
        gateway: FeishuGateway,
        storage: Storage,
        catalog: FieldCatalog,
        app_token: str,
        project_config_table_id: str,
        account_config_table_id: str,
        module_mapping_table_id: str,
        api_field_table_id: str | None = None,
        cache_ttl_minutes: int = 1440,
        additional_field_ids: set[str] | None = None,
    ) -> None:
        self.gateway = gateway
        self.storage = storage
        self.catalog = catalog
        self.app_token = app_token
        self.project_config_table_id = project_config_table_id
        self.account_config_table_id = account_config_table_id
        self.module_mapping_table_id = module_mapping_table_id
        self.api_field_table_id = api_field_table_id
        self.cache_ttl_minutes = cache_ttl_minutes
        self.additional_field_ids = additional_field_ids or set()

    def load(self, *, now: datetime | None = None) -> ConfigSnapshot:
        observed_at = now or utc_now()
        try:
            snapshot = self._fetch(observed_at)
            self._save_cache(snapshot, observed_at)
            return snapshot
        except Exception as exc:
            cached = self._load_valid_cache(observed_at, str(exc))
            if cached is not None:
                return cached
            if isinstance(exc, DashboardError):
                raise
            raise ExternalServiceError(f"读取飞书配置中心失败：{exc}") from exc

    def _fetch(self, observed_at: datetime) -> ConfigSnapshot:
        project_records = self.gateway.list_records(self.app_token, self.project_config_table_id)
        account_records = self.gateway.list_records(self.app_token, self.account_config_table_id)
        mapping_records = self.gateway.list_records(self.app_token, self.module_mapping_table_id)
        api_field_records = (
            self.gateway.list_records(self.app_token, self.api_field_table_id)
            if self.api_field_table_id
            else []
        )
        runtime_catalog = (
            self.catalog.overlay_feishu_records(api_field_records)
            if api_field_records
            else self.catalog
        )
        project_config = self._parse_key_value_records(project_records)
        account_config = self._parse_key_value_records(account_records)
        mappings = self._parse_mapping_records(mapping_records)
        enabled_fields = [
            item.standard_field_id
            for item in mappings
            if item.enabled and item.standard_field_id not in self.additional_field_ids
        ]
        runtime_catalog.validate_requirements(enabled_fields)

        material: dict[str, Any] = {
            "project_config": project_config,
            "account_config": account_config,
            "module_mappings": [item.model_dump(mode="json") for item in mappings],
            "catalog_document": runtime_catalog.document.model_dump(mode="json"),
        }
        version_hash = hashlib.sha256(
            json.dumps(material, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        return ConfigSnapshot(
            project_config=project_config,
            account_config=account_config,
            module_mappings=mappings,
            version_hash=version_hash,
            loaded_at=observed_at,
            source="feishu",
            catalog_document=runtime_catalog.document,
        )

    def _save_cache(self, snapshot: ConfigSnapshot, observed_at: datetime) -> None:
        with self.storage.transaction() as repos:
            repos.config_cache.put(
                cache_key=CACHE_KEY,
                version_hash=snapshot.version_hash,
                payload=snapshot.model_dump(mode="json"),
                fetched_at=observed_at,
                valid_until=observed_at + timedelta(minutes=self.cache_ttl_minutes),
            )

    def _load_valid_cache(self, observed_at: datetime, error_message: str) -> ConfigSnapshot | None:
        with self.storage.transaction() as repos:
            cached = repos.config_cache.get(CACHE_KEY)
            if cached is None or as_utc(cached.valid_until) < as_utc(observed_at):
                return None
            cached.last_error = error_message[:2000]
            payload = dict(cached.payload)
        payload["source"] = "cache"
        payload["fallback_error"] = error_message
        return ConfigSnapshot.model_validate(payload)

    @staticmethod
    def _parse_key_value_records(records: list[dict[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for record in records:
            fields = record.get("fields", {})
            if not FeishuConfigCenter._enabled(fields.get("启用", fields.get("enabled", True))):
                continue
            key = FeishuConfigCenter._scalar(fields.get("配置键", fields.get("key")))
            if not key:
                continue
            value = fields.get("配置值", fields.get("value"))
            result[str(key)] = FeishuConfigCenter._scalar(value)
        return result

    @staticmethod
    def _parse_mapping_records(records: list[dict[str, Any]]) -> tuple[ModuleFieldMapping, ...]:
        result: list[ModuleFieldMapping] = []
        for record in records:
            fields = record.get("fields", {})
            module_id = FeishuConfigCenter._scalar(fields.get("模块ID", fields.get("module_id")))
            field_id = FeishuConfigCenter._scalar(
                fields.get("标准字段ID", fields.get("standard_field_id"))
            )
            column = FeishuConfigCenter._scalar(fields.get("飞书列名", fields.get("feishu_column")))
            if not module_id or not field_id or not column:
                continue
            result.append(
                ModuleFieldMapping(
                    module_id=str(module_id),
                    standard_field_id=str(field_id),
                    feishu_column=str(column),
                    target_table_id=FeishuConfigCenter._optional_text(
                        fields.get("目标表ID", fields.get("target_table_id"))
                    ),
                    enabled=FeishuConfigCenter._enabled(
                        fields.get("启用", fields.get("enabled", True))
                    ),
                )
            )
        if not result:
            raise ConfigurationError("飞书“模块字段需求与映射”表没有有效记录。")
        return tuple(result)

    @staticmethod
    def _scalar(value: Any) -> Any:
        if isinstance(value, list):
            if not value:
                return None
            return FeishuConfigCenter._scalar(value[0])
        if isinstance(value, dict):
            for key in ("text", "name", "value"):
                if key in value:
                    return FeishuConfigCenter._scalar(value[key])
        return value

    @staticmethod
    def _enabled(value: Any) -> bool:
        scalar = FeishuConfigCenter._scalar(value)
        if isinstance(scalar, str):
            return scalar.strip().lower() not in {"false", "0", "否", "停用", "disabled"}
        return bool(scalar)

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        scalar = FeishuConfigCenter._scalar(value)
        return str(scalar).strip() if scalar not in (None, "") else None
