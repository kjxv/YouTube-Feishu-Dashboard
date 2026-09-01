from __future__ import annotations

from typing import Any

from youtube_feishu_dashboard.api.feishu.protocols import FeishuGateway
from youtube_feishu_dashboard.catalog.field_catalog import FieldCatalog


def sync_catalog_to_feishu(
    *,
    gateway: FeishuGateway,
    app_token: str,
    table_id: str,
    catalog: FieldCatalog,
) -> dict[str, int]:
    existing = gateway.list_records(app_token, table_id)
    by_field_id: dict[str, str] = {}
    for record in existing:
        fields = record.get("fields", {})
        field_id = scalar_text(fields.get("标准字段ID"))
        record_id = record.get("record_id")
        if field_id and record_id:
            by_field_id[field_id] = str(record_id)

    creates: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    for fields in catalog.as_feishu_seed_records():
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
    return {"created": len(creates), "updated": len(updates), "total": len(creates) + len(updates)}


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
