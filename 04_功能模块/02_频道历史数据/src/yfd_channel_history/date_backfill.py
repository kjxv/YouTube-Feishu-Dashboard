"""一次性迁移旧版 Analytics 日统计行的三种日期口径。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from math import ceil
from pathlib import Path
from typing import Any

from youtube_feishu_dashboard.api.feishu.protocols import FeishuGateway
from youtube_feishu_dashboard.core.errors import ConfigurationError
from youtube_feishu_dashboard.services.api_time_fields import (
    BEIJING_TIMEZONE,
    official_reporting_day_end_values,
)

from yfd_channel_history.runtime import ChannelHistoryRuntimePlan

_DAY_KEY = re.compile(r"_(\d{4}-\d{2}-\d{2})_analytics$")
_BATCH_SIZE = 500
_TABLE_KEYS = {
    "视频历史数据": "DAILY_VIDEO_RECORD_ID",
    "频道历史数据": "DAILY_CHANNEL_RECORD_ID",
}


@dataclass(frozen=True, slots=True)
class AnalyticsDailyDateBackfillResult:
    video_records_to_change: int
    channel_records_to_change: int
    records_changed: int
    feishu_batch_requests: int
    applied: bool
    backup_file: str | None


class AnalyticsDailyDateBackfill:
    """按唯一键和真实获取时间回填截止日、统计日和记录日。"""

    def __init__(
        self,
        *,
        gateway: FeishuGateway,
        app_token: str,
        runtime_plan: ChannelHistoryRuntimePlan,
        backup_file: Path,
    ) -> None:
        self.gateway = gateway
        self.app_token = app_token
        self.runtime_plan = runtime_plan
        self.backup_file = backup_file

    def run(
        self,
        *,
        expected_video_count: int | None,
        expected_channel_count: int | None,
        apply: bool,
    ) -> AnalyticsDailyDateBackfillResult:
        if (expected_video_count is not None and expected_video_count < 0) or (
            expected_channel_count is not None and expected_channel_count < 0
        ):
            raise ConfigurationError("预期修改数量不能为负数。")
        if apply and (expected_video_count is None or expected_channel_count is None):
            raise ConfigurationError("确认回填时必须提供两张表的预期修改数量。")
        updates_by_table: dict[str, list[dict[str, Any]]] = {}
        backups: list[dict[str, Any]] = []
        for table_name, key_id in _TABLE_KEYS.items():
            table = self.runtime_plan.require_table(table_name)
            required = (
                key_id,
                "DAILY_RECORD_TYPE",
                "ANALYTICS_DAY",
                "ANALYTICS_STAT_DATE_BEIJING",
                "DAILY_SNAPSHOT_DATE_BEIJING",
                "ANALYTICS_FETCHED_AT_BEIJING",
            )
            missing = [field_id for field_id in required if field_id not in table.mapping]
            if missing:
                raise ConfigurationError(
                    f"{table_name} 缺少日期回填所需映射：{', '.join(missing)}"
                )
            updates: list[dict[str, Any]] = []
            seen_keys: set[str] = set()
            for record in self.gateway.list_records(self.app_token, table.table_id):
                fields = record.get("fields", {})
                if _scalar(fields.get(table.mapping["DAILY_RECORD_TYPE"])) != "Analytics日统计":
                    continue
                key = str(_scalar(fields.get(table.mapping[key_id])) or "")
                match = _DAY_KEY.search(key)
                if not match or key in seen_keys:
                    raise ConfigurationError(f"{table_name} 的日统计唯一键缺失、异常或重复：{key}")
                seen_keys.add(key)
                day = date.fromisoformat(match.group(1))
                cutoff = official_reporting_day_end_values(
                    prefix="ANALYTICS", data_through_date=day
                )["ANALYTICS_DATA_THROUGH_AT_BEIJING"]
                if cutoff is None:
                    raise RuntimeError("有效统计日期未能换算截止时间。")
                fetched_at = _read_datetime(
                    fields.get(table.mapping["ANALYTICS_FETCHED_AT_BEIJING"])
                )
                wanted = self.runtime_plan.adapt_partial(
                    table_name,
                    {
                        "ANALYTICS_DAY": day,
                        "ANALYTICS_STAT_DATE_BEIJING": date.fromisoformat(cutoff[:10]),
                        "DAILY_SNAPSHOT_DATE_BEIJING": fetched_at.astimezone(
                            BEIJING_TIMEZONE
                        ).date(),
                    },
                )
                if all(
                    str(_scalar(fields.get(column))) == str(value)
                    for column, value in wanted.items()
                ):
                    continue
                record_id = record.get("record_id")
                if not record_id:
                    raise ConfigurationError(f"{table_name} 的日统计记录缺少 record_id：{key}")
                updates.append({"record_id": str(record_id), "fields": wanted})
                backups.append(
                    {
                        "table_name": table_name,
                        "table_id": table.table_id,
                        "record_id": str(record_id),
                        "fields": dict(fields),
                    }
                )
            updates_by_table[table_name] = updates

        video_count = len(updates_by_table["视频历史数据"])
        channel_count = len(updates_by_table["频道历史数据"])
        if (expected_video_count is not None and video_count != expected_video_count) or (
            expected_channel_count is not None and channel_count != expected_channel_count
        ):
            raise ConfigurationError(
                "预期修改数量与实时飞书记录不一致："
                f"视频 {video_count}、频道 {channel_count}；未写入。"
            )
        requests = 0
        if apply and backups:
            self.backup_file.parent.mkdir(parents=True, exist_ok=True)
            self.backup_file.write_text(
                json.dumps(backups, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            for table_name, updates in updates_by_table.items():
                table_id = self.runtime_plan.require_table(table_name).table_id
                for start in range(0, len(updates), _BATCH_SIZE):
                    self.gateway.batch_update_records(
                        self.app_token, table_id, updates[start : start + _BATCH_SIZE]
                    )
                requests += ceil(len(updates) / _BATCH_SIZE)
        return AnalyticsDailyDateBackfillResult(
            video_records_to_change=video_count,
            channel_records_to_change=channel_count,
            records_changed=video_count + channel_count if apply else 0,
            feishu_batch_requests=requests,
            applied=apply,
            backup_file=str(self.backup_file) if apply and backups else None,
        )


def _scalar(value: Any) -> Any:
    if isinstance(value, list):
        return _scalar(value[0]) if value else None
    if isinstance(value, dict):
        for key in ("text", "name", "value"):
            if key in value:
                return _scalar(value[key])
    return value


def _read_datetime(value: Any) -> datetime:
    raw = _scalar(value)
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(float(raw) / 1000, tz=UTC)
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConfigurationError(f"无法读取 Analytics API 数据获取时间：{raw}") from exc
    if parsed.tzinfo is None:
        raise ConfigurationError(f"Analytics API 数据获取时间缺少时区：{raw}")
    return parsed
