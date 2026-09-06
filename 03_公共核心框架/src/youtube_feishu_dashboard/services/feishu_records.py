"""通过公共绑定表实现飞书记录幂等写入。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from youtube_feishu_dashboard.api.feishu.protocols import FeishuGateway
from youtube_feishu_dashboard.core.errors import ExternalServiceError
from youtube_feishu_dashboard.db.models import FeishuRecordBinding
from youtube_feishu_dashboard.db.repositories import Storage


@dataclass(frozen=True, slots=True)
class EntityUpsert:
    entity_type: str
    entity_key: str
    fields: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SyncResult:
    action: Literal["created", "updated", "unchanged"]
    record_id: str
    binding_adopted: bool = False


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
        return self.upsert_entities(
            table_id=table_id,
            entities=[EntityUpsert(entity_type, entity_key, fields)],
        )[0]

    def upsert_entities(
        self,
        *,
        table_id: str,
        entities: list[EntityUpsert],
        batch_size: int = 500,
        remote_key_field: str | None = None,
    ) -> list[SyncResult]:
        """幂等批量写入同一张表，并按输入顺序返回每条记录的结果。

        ``remote_key_field`` 仅供具有稳定业务唯一键的表显式启用。当前数据库
        缺少绑定时，先按该飞书字段认领唯一匹配的已有记录；找不到才创建，
        同键多行或绑定冲突则安全停止。
        """
        if batch_size <= 0:
            raise ValueError("batch_size 必须大于 0。")
        if not entities:
            return []

        identities = [(item.entity_type, item.entity_key) for item in entities]
        if len(identities) != len(set(identities)):
            raise ValueError("同一次批量写入中存在重复的实体类型和实体键。")
        if remote_key_field and len(entities) != len(
            {item.entity_key for item in entities}
        ):
            raise ValueError("启用远程认领时，同一次批量写入中存在重复的业务唯一键。")

        payload_hashes = [_payload_hash(item.fields) for item in entities]
        with self.storage.transaction() as repos:
            bindings = {
                (item.entity_type, item.entity_key): item
                for item in repos.bindings.list_for_table(table_id)
            }
        adopted_identities = self._adopt_missing_bindings(
            table_id=table_id,
            entities=entities,
            bindings=bindings,
            remote_key_field=remote_key_field,
        )
        if adopted_identities:
            with self.storage.transaction() as repos:
                bindings = {
                    (item.entity_type, item.entity_key): item
                    for item in repos.bindings.list_for_table(table_id)
                }

        results: list[SyncResult | None] = [None] * len(entities)
        creates: list[tuple[int, EntityUpsert, str]] = []
        updates: list[tuple[int, EntityUpsert, str, str]] = []
        for index, (item, payload_hash) in enumerate(zip(entities, payload_hashes, strict=True)):
            binding = bindings.get((item.entity_type, item.entity_key))
            if binding is not None and binding.last_payload_hash == payload_hash:
                results[index] = SyncResult("unchanged", binding.record_id)
            elif binding is not None:
                updates.append((index, item, payload_hash, binding.record_id))
            else:
                creates.append((index, item, payload_hash))

        for batch in _batches(updates, batch_size):
            self.gateway.batch_update_records(
                self.app_token,
                table_id,
                [
                    {"record_id": record_id, "fields": item.fields}
                    for _, item, _, record_id in batch
                ],
            )
            with self.storage.transaction() as repos:
                for index, item, payload_hash, record_id in batch:
                    repos.bindings.upsert(
                        table_id=table_id,
                        entity_type=item.entity_type,
                        entity_key=item.entity_key,
                        record_id=record_id,
                        payload_hash=payload_hash,
                    )
                    results[index] = SyncResult(
                        "updated",
                        record_id,
                        binding_adopted=(item.entity_type, item.entity_key)
                        in adopted_identities,
                    )

        for batch in _batches(creates, batch_size):
            response = self.gateway.batch_create_records(
                self.app_token,
                table_id,
                [item.fields for _, item, _ in batch],
            )
            if len(response) != len(batch):
                raise ExternalServiceError("飞书批量创建记录的响应数量与请求数量不一致。")
            record_ids: list[str] = []
            for created in response:
                if not created.get("record_id"):
                    raise ExternalServiceError("飞书创建记录成功响应缺少 record_id。")
                record_ids.append(str(created["record_id"]))
            with self.storage.transaction() as repos:
                for (index, item, payload_hash), record_id in zip(
                    batch, record_ids, strict=True
                ):
                    repos.bindings.upsert(
                        table_id=table_id,
                        entity_type=item.entity_type,
                        entity_key=item.entity_key,
                        record_id=record_id,
                        payload_hash=payload_hash,
                    )
                    results[index] = SyncResult("created", record_id)

        completed: list[SyncResult] = []
        for result in results:
            if result is None:
                raise RuntimeError("飞书批量写入结果不完整。")
            completed.append(result)
        return completed

    def _adopt_missing_bindings(
        self,
        *,
        table_id: str,
        entities: list[EntityUpsert],
        bindings: dict[tuple[str, str], FeishuRecordBinding],
        remote_key_field: str | None,
    ) -> set[tuple[str, str]]:
        if not remote_key_field:
            return set()
        missing = [
            item
            for item in entities
            if (item.entity_type, item.entity_key) not in bindings
        ]
        if not missing:
            return set()

        wanted_keys = {item.entity_key for item in missing}
        remote_by_key: dict[str, list[dict[str, Any]]] = {}
        for record in self.gateway.list_records(self.app_token, table_id):
            raw_key = _field_scalar(record.get("fields", {}).get(remote_key_field))
            if raw_key in (None, ""):
                continue
            key = str(raw_key)
            if key in wanted_keys:
                remote_by_key.setdefault(key, []).append(record)

        duplicate_keys = sorted(
            key for key, records in remote_by_key.items() if len(records) > 1
        )
        if duplicate_keys:
            sample = ", ".join(duplicate_keys[:5])
            raise ExternalServiceError(
                f"飞书表 {table_id} 的业务唯一键字段 {remote_key_field} 存在重复记录："
                f"{sample}；拒绝自动认领。"
            )

        bound_record_ids = {
            binding.record_id: identity for identity, binding in bindings.items()
        }
        adopted: set[tuple[str, str]] = set()
        with self.storage.transaction() as repos:
            for item in missing:
                matches = remote_by_key.get(item.entity_key, [])
                if not matches:
                    continue
                record_id = matches[0].get("record_id")
                if not record_id:
                    raise ExternalServiceError(
                        f"飞书表 {table_id} 的已有记录缺少 record_id，拒绝自动认领。"
                    )
                identity = (item.entity_type, item.entity_key)
                owner = bound_record_ids.get(str(record_id))
                if owner is not None and owner != identity:
                    raise ExternalServiceError(
                        f"飞书记录 {record_id} 已绑定其他业务实体，拒绝自动认领。"
                    )
                repos.bindings.upsert(
                    table_id=table_id,
                    entity_type=item.entity_type,
                    entity_key=item.entity_key,
                    record_id=str(record_id),
                    payload_hash=None,
                )
                bound_record_ids[str(record_id)] = identity
                adopted.add(identity)
        return adopted


def _payload_hash(fields: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(fields, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _field_scalar(value: Any) -> Any:
    if isinstance(value, list):
        return _field_scalar(value[0]) if value else None
    if isinstance(value, dict):
        for key in ("text", "name", "value"):
            if key in value:
                return _field_scalar(value[key])
    return value


def _batches(items: list[Any], size: int) -> list[list[Any]]:
    return [items[start : start + size] for start in range(0, len(items), size)]
