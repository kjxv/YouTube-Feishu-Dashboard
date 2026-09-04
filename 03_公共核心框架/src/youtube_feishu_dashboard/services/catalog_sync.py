from __future__ import annotations

from typing import Any

from youtube_feishu_dashboard.api.feishu.protocols import FeishuGateway
from youtube_feishu_dashboard.catalog.field_catalog import (
    CURRENT_AVAILABILITY_FIELD,
    LEGACY_IMPLEMENTATION_STATUS_FIELD,
    FieldCatalog,
)
from youtube_feishu_dashboard.core.errors import ConfigurationError


def sync_catalog_to_feishu(
    *,
    gateway: FeishuGateway,
    app_token: str,
    table_id: str,
    catalog: FieldCatalog,
) -> dict[str, int]:
    existing = gateway.list_records(app_token, table_id)
    list_fields = getattr(gateway, "list_fields", None)
    live_field_names = (
        {
            str(item.get("field_name"))
            for item in list_fields(app_token, table_id)
            if item.get("field_name")
        }
        if callable(list_fields)
        else {CURRENT_AVAILABILITY_FIELD}
    )
    by_field_id: dict[str, str] = {}
    duplicate_field_ids: set[str] = set()
    for record in existing:
        fields = record.get("fields", {})
        field_id = scalar_text(fields.get("标准字段ID"))
        record_id = record.get("record_id")
        if field_id and record_id:
            if field_id in by_field_id:
                duplicate_field_ids.add(field_id)
            by_field_id[field_id] = str(record_id)
    if duplicate_field_ids:
        raise ConfigurationError(
            "飞书标准字段字典存在重复的标准字段ID："
            + "、".join(sorted(duplicate_field_ids))
            + "。为避免更新错误记录，本次同步已停止。"
        )

    creates: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    for raw_fields in catalog.as_feishu_seed_records():
        fields = dict(raw_fields)
        if (
            CURRENT_AVAILABILITY_FIELD not in live_field_names
            and LEGACY_IMPLEMENTATION_STATUS_FIELD in live_field_names
        ):
            fields[LEGACY_IMPLEMENTATION_STATUS_FIELD] = fields.pop(
                CURRENT_AVAILABILITY_FIELD
            )
        field_id = str(fields["标准字段ID"])
        record_id = by_field_id.get(field_id)
        if record_id:
            updates.append({"record_id": record_id, "fields": fields})
        else:
            creates.append(fields)
    if updates:
        gateway.batch_update_records(app_token, table_id, updates)
    if creates:
        gateway.batch_create_records(app_token, table_id, creates)
    api_count = sum(1 for item in catalog.document.fields if item.is_api_field)
    system_count = len(catalog.document.fields) - api_count
    return {
        "created": len(creates),
        "updated": len(updates),
        "total": len(creates) + len(updates),
        "api_fields": api_count,
        "system_fields": system_count,
    }


def scalar_text(value: Any) -> str | None:
    if isinstance(value, list):
        return scalar_text(value[0]) if value else None
    if isinstance(value, dict):
        for key in ("text", "name", "value"):
            if key in value:
                return scalar_text(value[key])
    if value in (None, ""):
        return None
    return str(value)
