"""把统一调度器的开始、完成和失败状态镜像到飞书单行状态表。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from youtube_feishu_dashboard.api.feishu.protocols import (
    FeishuGateway,
    FeishuTableAdminGateway,
)
from youtube_feishu_dashboard.core.errors import ConfigurationError, ExternalServiceError
from youtube_feishu_dashboard.core.time import as_utc
from youtube_feishu_dashboard.services.config_center_bootstrap import update_dotenv

RUNTIME_STATUS_TABLE_NAME = "系统运行状态"
RUNTIME_STATUS_KEY = "统一调度器"
RUNTIME_STATUS_ENV_KEY = "YFD_FEISHU_RUNTIME_STATUS_TABLE_ID"

RUNTIME_STATUS_FIELDS: tuple[tuple[str, int], ...] = (
    ("状态Key", 1),
    ("最近调度唤醒时间（北京时间）", 5),
    ("最近调度完成时间（北京时间）", 5),
    ("最近成功时间（北京时间）", 5),
    ("最近状态", 1),
    ("本轮到期任务数", 2),
    ("本轮失败任务数", 2),
    ("本轮飞书写入记录数", 2),
    ("最近一次调度结果", 1),
    ("最近结果摘要", 1),
    ("运行实例", 1),
    ("程序版本", 1),
)


class RuntimeOutcome(Protocol):
    @property
    def task_id(self) -> str: ...

    @property
    def status(self) -> str: ...

    @property
    def counts(self) -> dict[str, int] | None: ...


@dataclass(frozen=True, slots=True)
class RuntimeStatusSetupResult:
    table_id: str
    table_created: bool
    fields_created: tuple[str, ...]
    record_created: bool
    env_key_updated: str


class RuntimeStatusTableSetup:
    """幂等创建或补齐“系统运行状态”表，不覆盖用户已有状态。"""

    def __init__(
        self,
        *,
        gateway: FeishuTableAdminGateway,
        app_token: str,
        env_file: Path,
    ) -> None:
        self.gateway = gateway
        self.app_token = app_token
        self.env_file = env_file

    def ensure(self) -> RuntimeStatusSetupResult:
        tables = self.gateway.list_tables(self.app_token)
        table = self._find_unique_table(tables)
        table_created = table is None
        if table is None:
            self.gateway.create_table(
                self.app_token,
                name=RUNTIME_STATUS_TABLE_NAME,
                fields=[
                    _field_create_payload(name, field_type)
                    for name, field_type in RUNTIME_STATUS_FIELDS
                ],
            )
            table = self._find_unique_table(self.gateway.list_tables(self.app_token))
            if table is None:
                raise ExternalServiceError("飞书已接受状态表创建请求，但重新读取时没有找到。")

        table_id = _required_text(table.get("table_id"), "系统运行状态 Table ID")
        fields_created = self._ensure_fields(table_id)
        record_created = self._ensure_single_record(table_id)
        update_dotenv(self.env_file, {RUNTIME_STATUS_ENV_KEY: table_id})
        return RuntimeStatusSetupResult(
            table_id=table_id,
            table_created=table_created,
            fields_created=fields_created,
            record_created=record_created,
            env_key_updated=RUNTIME_STATUS_ENV_KEY,
        )

    def _find_unique_table(self, tables: list[dict[str, Any]]) -> dict[str, Any] | None:
        matches = [
            item
            for item in tables
            if str(item.get("name", "")).strip() == RUNTIME_STATUS_TABLE_NAME
        ]
        if len(matches) > 1:
            raise ConfigurationError(
                f"同一 Base 中存在多个“{RUNTIME_STATUS_TABLE_NAME}”表，请先人工消除重名。"
            )
        return matches[0] if matches else None

    def _ensure_fields(self, table_id: str) -> tuple[str, ...]:
        actual = self.gateway.list_fields(self.app_token, table_id)
        by_name = {
            str(item.get("field_name")): item
            for item in actual
            if item.get("field_name")
        }
        created: list[str] = []
        for position, (name, field_type) in enumerate(RUNTIME_STATUS_FIELDS):
            field = by_name.get(name)
            if field is not None:
                actual_type = int(field.get("type", -1))
                if actual_type != field_type:
                    raise ConfigurationError(
                        f"飞书状态表字段“{name}”类型为 {actual_type}，应为 {field_type}。"
                    )
                if position == 0 and not bool(field.get("is_primary", True)):
                    raise ConfigurationError("飞书状态表的“状态Key”必须是主字段。")
                continue
            if position == 0:
                raise ConfigurationError("飞书状态表缺少主字段“状态Key”，拒绝自动改造。")
            self.gateway.create_field(
                self.app_token,
                table_id,
                field_name=name,
                field_type=field_type,
                property=(
                    {"date_formatter": "yyyy-MM-dd HH:mm"}
                    if field_type == 5
                    else None
                ),
            )
            created.append(name)
        return tuple(created)

    def _ensure_single_record(self, table_id: str) -> bool:
        matches = _status_records(self.gateway.list_records(self.app_token, table_id))
        if len(matches) > 1:
            raise ConfigurationError("系统运行状态表存在多条“统一调度器”记录，请先删除重复行。")
        if matches:
            return False
        created = self.gateway.batch_create_records(
            self.app_token,
            table_id,
            [
                {
                    "状态Key": RUNTIME_STATUS_KEY,
                    "最近状态": "尚未运行",
                    "本轮到期任务数": 0,
                    "本轮失败任务数": 0,
                    "本轮飞书写入记录数": 0,
                    "最近一次调度结果": "尚未运行",
                    "最近结果摘要": "等待 Windows 或 VPS 统一调度器首次唤醒",
                }
            ],
        )
        if len(created) != 1 or not created[0].get("record_id"):
            raise ExternalServiceError("创建系统运行状态记录后，飞书响应缺少 record_id。")
        return True


class RuntimeStatusReporter:
    """更新同一条飞书状态记录；每次进程只查找一次 record_id。"""

    def __init__(
        self,
        *,
        gateway: FeishuGateway,
        app_token: str,
        table_id: str,
        instance_id: str,
        version: str,
    ) -> None:
        self.gateway = gateway
        self.app_token = app_token
        self.table_id = table_id
        self.instance_id = instance_id
        self.version = version
        self._record_id: str | None = None

    def report_started(self, *, started_at: datetime) -> None:
        time_label = _beijing_time_label(started_at)
        self._update(
            {
                "最近调度唤醒时间（北京时间）": _timestamp_ms(started_at),
                "最近状态": "运行中",
                "本轮到期任务数": 0,
                "本轮失败任务数": 0,
                "本轮飞书写入记录数": 0,
                "最近一次调度结果": f"{time_label}｜正在执行",
                "最近结果摘要": "统一调度器已启动，等待本轮完成",
                "运行实例": self.instance_id,
                "程序版本": self.version,
            }
        )

    def report_completed(
        self,
        *,
        started_at: datetime,
        finished_at: datetime,
        outcomes: Sequence[RuntimeOutcome],
    ) -> None:
        failed = sum(item.status == "failed" for item in outcomes)
        non_success = sum(item.status != "success" for item in outcomes)
        if failed:
            status = "有失败"
        elif non_success:
            status = "有跳过"
        else:
            status = "正常"
        writes = sum(_feishu_write_count(item.counts) for item in outcomes)
        if outcomes:
            summary = "；".join(f"{item.task_id}:{item.status}" for item in outcomes)
        else:
            summary = "本轮正常完成，没有业务任务到期"
        display_result = _display_result(
            started_at=started_at,
            outcomes=outcomes,
            writes=writes,
            status=status,
        )
        fields: dict[str, Any] = {
            "最近调度完成时间（北京时间）": _timestamp_ms(finished_at),
            "最近状态": status,
            "本轮到期任务数": len(outcomes),
            "本轮失败任务数": failed,
            "本轮飞书写入记录数": writes,
            "最近一次调度结果": display_result,
            "最近结果摘要": summary[:1000],
            "运行实例": self.instance_id,
            "程序版本": self.version,
        }
        if non_success == 0:
            fields["最近成功时间（北京时间）"] = _timestamp_ms(finished_at)
        self._update(fields)

    def report_failed(
        self,
        *,
        started_at: datetime,
        finished_at: datetime,
        error: Exception,
    ) -> None:
        summary = f"{type(error).__name__}: {error}"
        self._update(
            {
                "最近调度完成时间（北京时间）": _timestamp_ms(finished_at),
                "最近状态": "异常",
                "本轮到期任务数": 0,
                "本轮失败任务数": 1,
                "本轮飞书写入记录数": 0,
                "最近一次调度结果": (
                    f"{_beijing_time_label(started_at)}｜执行失败｜{summary[:500]}"
                ),
                "最近结果摘要": summary[:1000],
                "运行实例": self.instance_id,
                "程序版本": self.version,
            }
        )

    def _update(self, fields: dict[str, Any]) -> None:
        record_id = self._resolve_record_id()
        self.gateway.batch_update_records(
            self.app_token,
            self.table_id,
            [{"record_id": record_id, "fields": fields}],
        )

    def _resolve_record_id(self) -> str:
        if self._record_id:
            return self._record_id
        records = self.gateway.list_records(self.app_token, self.table_id)
        matches = _status_records(records)
        if len(matches) > 1:
            raise ConfigurationError("系统运行状态表存在多条“统一调度器”记录，拒绝写入。")
        if not matches:
            created = self.gateway.batch_create_records(
                self.app_token,
                self.table_id,
                [{"状态Key": RUNTIME_STATUS_KEY, "最近状态": "初始化"}],
            )
            if len(created) != 1 or not created[0].get("record_id"):
                raise ExternalServiceError("创建系统运行状态记录失败。")
            self._record_id = str(created[0]["record_id"])
            return self._record_id
        record_id = _required_text(matches[0].get("record_id"), "系统运行状态 record_id")
        self._record_id = record_id
        return record_id


def _status_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        record
        for record in records
        if _scalar(record.get("fields", {}).get("状态Key")) == RUNTIME_STATUS_KEY
    ]


def _scalar(value: Any) -> str:
    if isinstance(value, list):
        return _scalar(value[0]) if value else ""
    if isinstance(value, dict):
        for key in ("text", "name", "value"):
            if key in value:
                return _scalar(value[key])
    return str(value or "").strip()


def _timestamp_ms(value: datetime) -> int:
    return int(as_utc(value).timestamp() * 1000)


def _required_text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ExternalServiceError(f"飞书 API 返回结果缺少 {label}。")
    return text


def _field_create_payload(name: str, field_type: int) -> dict[str, Any]:
    payload: dict[str, Any] = {"field_name": name, "type": field_type}
    if field_type == 5:
        payload["property"] = {"date_formatter": "yyyy-MM-dd HH:mm"}
    return payload


def _feishu_write_count(counts: dict[str, int] | None) -> int:
    if not counts:
        return 0
    return max(0, int(counts.get("feishu_writes", 0))) + max(
        0,
        int(counts.get("feishu_records_changed", 0)),
    )


def _display_result(
    *,
    started_at: datetime,
    outcomes: Sequence[RuntimeOutcome],
    writes: int,
    status: str,
) -> str:
    time_label = _beijing_time_label(started_at)
    if not outcomes:
        return f"{time_label}｜调度正常｜本轮没有业务任务到期"

    latest = next(
        (item for item in outcomes if item.task_id == "latest-video-tracker"),
        None,
    )
    if latest is None:
        data_result = "最新视频 Data API 任务未到期"
    elif latest.status == "failed":
        data_result = "最新视频 Data API 任务执行失败"
    elif latest.status != "success":
        data_result = f"最新视频 Data API 任务已跳过（{latest.status}）"
    else:
        snapshots = max(0, int((latest.counts or {}).get("snapshots", 0)))
        data_result = (
            f"Data API 已采集 {snapshots} 个视频"
            if snapshots
            else "Data API 本轮无需采集"
        )

    write_result = f"飞书已更新 {writes} 条" if writes else "飞书业务数据无变化"
    return f"{time_label}｜调度{status}｜{data_result}｜{write_result}"[:1000]


def _beijing_time_label(value: datetime) -> str:
    return as_utc(value).astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")
