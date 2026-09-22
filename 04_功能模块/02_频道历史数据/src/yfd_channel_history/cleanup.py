from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from math import ceil
from pathlib import Path
from typing import Any

from youtube_feishu_dashboard.api.feishu.protocols import FeishuGateway
from youtube_feishu_dashboard.core.errors import ConfigurationError
from youtube_feishu_dashboard.db.repositories import Storage

from yfd_channel_history.runtime import ChannelHistoryRuntimePlan

_FEISHU_BATCH_SIZE = 500


@dataclass(frozen=True, slots=True)
class PlaceholderZeroCleanupResult:
    analytics_day: str
    expected_count: int
    matched_zero_records: int
    matching_nonzero_records_skipped: int
    records_deleted: int
    bindings_deleted: int
    feishu_batch_requests: int
    applied: bool
    backup_file: str | None


class PlaceholderZeroDayCleaner:
    """备份并精确删除一个日期的占位零及其本地绑定。"""

    def __init__(
        self,
        *,
        gateway: FeishuGateway,
        app_token: str,
        runtime_plan: ChannelHistoryRuntimePlan,
        storage: Storage,
        backup_file: Path | None = None,
    ) -> None:
        self.gateway = gateway
        self.app_token = app_token
        self.runtime_plan = runtime_plan
        self.storage = storage
        self.backup_file = backup_file

    def run(
        self,
        *,
        analytics_day: date,
        expected_count: int,
        apply: bool,
    ) -> PlaceholderZeroCleanupResult:
        if expected_count <= 0:
            raise ConfigurationError("expected_count 必须大于 0。")
        table = self.runtime_plan.require_table("视频历史数据")
        key_column = table.mapping["DAILY_VIDEO_RECORD_ID"]
        day_column = table.mapping["DAILY_DATA_DATE_PACIFIC"]
        views_column = table.mapping["ANALYTICS_VIEWS"]
        record_type_column = table.mapping["DAILY_RECORD_TYPE"]
        suffix = f"_{analytics_day.isoformat()}_analytics"
        candidates: list[dict[str, object]] = []
        entity_keys: list[str] = []
        backup_records: list[dict[str, Any]] = []
        nonzero = 0
        for record in self.gateway.list_records(self.app_token, table.table_id):
            fields = record.get("fields", {})
            key = str(_scalar(fields.get(key_column)) or "")
            if not key.endswith(suffix):
                continue
            if _scalar(fields.get(record_type_column)) != "Analytics日统计":
                continue
            if _scalar(fields.get(day_column)) in (None, ""):
                continue
            if not _is_zero_or_blank(fields.get(views_column)):
                nonzero += 1
                continue
            record_id = record.get("record_id")
            if record_id:
                candidates.append({"record_id": str(record_id)})
                entity_keys.append(key)
                backup_records.append({"record_id": str(record_id), "fields": dict(fields)})
        if len(candidates) != expected_count:
            raise ConfigurationError(
                "安全检查未通过："
                f"预期找到 {expected_count} 条占位零，实际找到 {len(candidates)} 条；"
                "未修改飞书数据。"
            )
        if apply:
            if self.backup_file is None:
                raise ConfigurationError("执行清理时必须配置本地备份文件。")
            self.backup_file.parent.mkdir(parents=True, exist_ok=True)
            self.backup_file.write_text(
                json.dumps(backup_records, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            with self.storage.transaction() as repos:
                bindings_deleted = repos.bindings.delete_many(
                    table_id=table.table_id,
                    entity_type="channel_video_analytics_day",
                    entity_keys=entity_keys,
                )
            for batch in _batches(
                [str(item["record_id"]) for item in candidates],
                _FEISHU_BATCH_SIZE,
            ):
                self.gateway.batch_delete_records(
                    self.app_token,
                    table.table_id,
                    batch,
                )
        else:
            bindings_deleted = 0
        return PlaceholderZeroCleanupResult(
            analytics_day=analytics_day.isoformat(),
            expected_count=expected_count,
            matched_zero_records=len(candidates),
            matching_nonzero_records_skipped=nonzero,
            records_deleted=len(candidates) if apply else 0,
            bindings_deleted=bindings_deleted,
            feishu_batch_requests=ceil(len(candidates) / _FEISHU_BATCH_SIZE) if apply else 0,
            applied=apply,
            backup_file=str(self.backup_file) if apply and self.backup_file else None,
        )


def _scalar(value: Any) -> Any:
    if isinstance(value, list):
        return _scalar(value[0]) if value else None
    if isinstance(value, dict):
        for key in ("text", "name", "value"):
            if key in value:
                return _scalar(value[key])
    return value


def _is_zero_or_blank(value: Any) -> bool:
    scalar = _scalar(value)
    if scalar in (None, ""):
        return True
    try:
        return int(str(scalar)) == 0
    except (TypeError, ValueError):
        return False


def _batches(items: list[Any], size: int) -> list[list[Any]]:
    return [items[start : start + size] for start in range(0, len(items), size)]
