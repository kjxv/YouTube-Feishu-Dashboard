"""一次性迁移频道表的太平洋时间与统一数据日期口径。"""

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
from youtube_feishu_dashboard.services.api_time_fields import PACIFIC_TIMEZONE, zoned_text

from yfd_channel_history.runtime import ChannelHistoryRuntimePlan

_DAY_KEY = re.compile(r"_(\d{4}-\d{2}-\d{2})_analytics$")
_BATCH_SIZE = 500
_TABLE_KEYS = {
    "视频主表": "VIDEO_ID",
    "视频历史数据": "DAILY_VIDEO_RECORD_ID",
    "频道历史数据": "DAILY_CHANNEL_RECORD_ID",
}
_PACIFIC_TIME_IDS = (
    "DATA_API_FETCHED_AT_PACIFIC",
    "ANALYTICS_FETCHED_AT_PACIFIC",
    "VIDEO_7D_SAMPLE_START_AT_PACIFIC",
    "VIDEO_7D_SAMPLE_END_AT_PACIFIC",
    "VIDEO_48H_SAMPLE_AT_PACIFIC",
)
_LEGACY_SOURCE_COLUMNS = {
    "DATA_API_FETCHED_AT_PACIFIC": (
        "Data API 数据获取时间（太平洋时间）",
        "Data API 数据获取时间（北京时间）",
    ),
    "ANALYTICS_FETCHED_AT_PACIFIC": (
        "Analytics API 数据获取时间（太平洋时间）",
        "Analytics API 数据获取时间（北京时间）",
    ),
    "VIDEO_7D_SAMPLE_START_AT_PACIFIC": (
        "近7天采样起点（太平洋时间）",
        "近7天采样起点（北京时间）",
    ),
    "VIDEO_7D_SAMPLE_END_AT_PACIFIC": (
        "近7天采样终点（太平洋时间）",
        "近7天采样终点（北京时间）",
    ),
    "VIDEO_48H_SAMPLE_AT_PACIFIC": (
        "48小时样本采集时间（太平洋时间）",
        "48小时样本采集时间（北京时间）",
    ),
}


@dataclass(frozen=True, slots=True)
class ChannelHistoryTimePolicyBackfillResult:
    video_main_records_to_change: int
    video_records_to_change: int
    channel_records_to_change: int
    records_changed: int
    feishu_batch_requests: int
    applied: bool
    backup_file: str | None


class ChannelHistoryTimePolicyBackfill:
    """转换精确时间为太平洋时间，并按记录类型回填唯一业务日期。"""

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
        expected_video_main_count: int | None = None,
        expected_video_count: int | None,
        expected_channel_count: int | None,
        apply: bool,
    ) -> ChannelHistoryTimePolicyBackfillResult:
        expected = {
            "视频主表": expected_video_main_count,
            "视频历史数据": expected_video_count,
            "频道历史数据": expected_channel_count,
        }
        if any(value is not None and value < 0 for value in expected.values()):
            raise ConfigurationError("预期修改数量不能为负数。")
        if apply and any(value is None for value in expected.values()):
            raise ConfigurationError("确认回填时必须提供三张表的预期修改数量。")

        updates_by_table: dict[str, list[dict[str, Any]]] = {}
        backups: list[dict[str, Any]] = []
        for table_name, key_id in _TABLE_KEYS.items():
            table = self.runtime_plan.require_table(table_name)
            key_column = table.mapping.get(key_id)
            if not key_column:
                raise ConfigurationError(f"{table_name} 缺少唯一键映射 {key_id}。")
            record_type_column = table.mapping.get("DAILY_RECORD_TYPE")
            data_fetched_column = table.mapping.get("DATA_API_FETCHED_AT_PACIFIC")
            updates: list[dict[str, Any]] = []
            for record in self.gateway.list_records(self.app_token, table.table_id):
                fields = record.get("fields", {})
                values: dict[str, object] = {}
                for field_id in _PACIFIC_TIME_IDS:
                    column = table.mapping.get(field_id)
                    raw = _first_present(
                        fields,
                        tuple(
                            item
                            for item in (column, *_LEGACY_SOURCE_COLUMNS[field_id])
                            if item
                        ),
                    )
                    if raw not in (None, ""):
                        values[field_id] = zoned_text(_read_datetime(raw), PACIFIC_TIMEZONE)

                if record_type_column:
                    record_type = str(_scalar(fields.get(record_type_column)) or "")
                    if record_type in {"每日采样快照", "频道采样快照"}:
                        if not data_fetched_column:
                            raise ConfigurationError(
                                f"{table_name} 缺少 Data API 太平洋获取时间映射。"
                            )
                        fetched = _first_present(
                            fields,
                            tuple(
                                item
                                for item in (
                                    data_fetched_column,
                                    *_LEGACY_SOURCE_COLUMNS[
                                        "DATA_API_FETCHED_AT_PACIFIC"
                                    ],
                                )
                                if item
                            ),
                        )
                        if fetched in (None, ""):
                            raise ConfigurationError(
                                f"{table_name} 的快照行缺少真实 Data API 获取时间。"
                            )
                        values["DAILY_DATA_DATE_PACIFIC"] = _read_datetime(
                            fetched
                        ).astimezone(PACIFIC_TIMEZONE).date()
                    elif record_type == "Analytics日统计":
                        key = str(_scalar(fields.get(key_column)) or "")
                        match = _DAY_KEY.search(key)
                        if not match:
                            raise ConfigurationError(
                                f"{table_name} 的日统计唯一键无法解析太平洋日期：{key}"
                            )
                        values["DAILY_DATA_DATE_PACIFIC"] = date.fromisoformat(match.group(1))

                wanted = self.runtime_plan.adapt_partial(table_name, values)
                if not wanted or all(
                    str(_scalar(fields.get(column))) == str(value)
                    for column, value in wanted.items()
                ):
                    continue
                record_id = str(record.get("record_id") or "").strip()
                if not record_id:
                    raise ConfigurationError(f"{table_name} 发现缺少 record_id 的记录。")
                updates.append({"record_id": record_id, "fields": wanted})
                backups.append(
                    {
                        "table_name": table_name,
                        "table_id": table.table_id,
                        "record_id": record_id,
                        "fields": dict(fields),
                    }
                )
            updates_by_table[table_name] = updates

        actual = {name: len(items) for name, items in updates_by_table.items()}
        if any(expected[name] is not None and expected[name] != actual[name] for name in expected):
            raise ConfigurationError(
                "预期修改数量与实时飞书记录不一致："
                f"视频主表 {actual['视频主表']}、视频历史 {actual['视频历史数据']}、"
                f"频道历史 {actual['频道历史数据']}；未写入。"
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

        total = sum(actual.values())
        return ChannelHistoryTimePolicyBackfillResult(
            video_main_records_to_change=actual["视频主表"],
            video_records_to_change=actual["视频历史数据"],
            channel_records_to_change=actual["频道历史数据"],
            records_changed=total if apply else 0,
            feishu_batch_requests=requests,
            applied=apply,
            backup_file=str(self.backup_file) if apply and backups else None,
        )


# 兼容旧导入路径；新代码和命令应使用语义更准确的新名称。
AnalyticsDailyDateBackfill = ChannelHistoryTimePolicyBackfill
AnalyticsDailyDateBackfillResult = ChannelHistoryTimePolicyBackfillResult


def _scalar(value: Any) -> Any:
    if isinstance(value, list):
        return _scalar(value[0]) if value else None
    if isinstance(value, dict):
        for key in ("text", "name", "value"):
            if key in value:
                return _scalar(value[key])
    return value


def _first_present(fields: dict[str, Any], columns: tuple[str, ...]) -> Any:
    for column in columns:
        value = fields.get(column)
        if value not in (None, ""):
            return value
    return None


def _read_datetime(value: Any) -> datetime:
    raw = _scalar(value)
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(float(raw) / 1000, tz=UTC)
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConfigurationError(f"无法读取 API 时间：{raw}") from exc
    if parsed.tzinfo is None:
        raise ConfigurationError(f"API 时间缺少时区：{raw}")
    return parsed
