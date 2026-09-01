"""通过公共绑定表实现飞书记录幂等写入。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from youtube_feishu_dashboard.api.feishu.protocols import FeishuGateway
from youtube_feishu_dashboard.core.errors import ExternalServiceError
from youtube_feishu_dashboard.db.repositories import Storage


@dataclass(frozen=True, slots=True)
class SyncResult:
    action: Literal["created", "updated", "unchanged"]
    record_id: str


class FeishuRecordService:
    def __init__(self, gateway: FeishuGateway, storage: Storage, app_token: str) -> None:
        self.gateway = gateway
        self.storage = storage
        self.app_token = app_token

    def upsert_entity(
        self,
        *,
        table_id: str,
        entity_type: str,
        entity_key: str,
        fields: dict[str, Any],
    ) -> SyncResult:
        payload_hash = hashlib.sha256(
            json.dumps(fields, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        with self.storage.transaction() as repos:
            binding = repos.bindings.get(table_id, entity_type, entity_key)
            if binding is not None and binding.last_payload_hash == payload_hash:
                return SyncResult("unchanged", binding.record_id)
            record_id = binding.record_id if binding is not None else None

        if record_id:
            self.gateway.batch_update_records(
                self.app_token,
                table_id,
                [{"record_id": record_id, "fields": fields}],
            )
            action: Literal["created", "updated"] = "updated"
        else:
            response = self.gateway.batch_create_records(self.app_token, table_id, [fields])
            if not response or not response[0].get("record_id"):
                raise ExternalServiceError("飞书创建记录成功响应缺少 record_id。")
            record_id = str(response[0]["record_id"])
            action = "created"

        with self.storage.transaction() as repos:
            repos.bindings.upsert(
                table_id=table_id,
                entity_type=entity_type,
                entity_key=entity_key,
                record_id=record_id,
                payload_hash=payload_hash,
            )
        return SyncResult(action, record_id)
