from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from youtube_feishu_dashboard.core.errors import ConfigurationError
from youtube_feishu_dashboard.services.runtime_status import (
    RUNTIME_STATUS_FIELDS,
    RUNTIME_STATUS_KEY,
    RUNTIME_STATUS_TABLE_NAME,
    RuntimeStatusReporter,
    RuntimeStatusTableSetup,
)


class FakeAdminGateway:
    def __init__(self) -> None:
        self.tables: dict[str, str] = {}
        self.fields: dict[str, list[dict[str, Any]]] = {}
        self.records: dict[str, list[dict[str, Any]]] = {}
        self._next_record = 1

    def list_tables(self, app_token: str) -> list[dict[str, Any]]:
        return [
            {"name": name, "table_id": table_id}
            for name, table_id in self.tables.items()
        ]

    def create_table(
        self,
        app_token: str,
        *,
        name: str,
        fields: list[dict[str, Any]],
        default_view_name: str = "默认视图",
    ) -> dict[str, Any]:
        table_id = f"tbl-{len(self.tables) + 1}"
        self.tables[name] = table_id
        self.fields[table_id] = [
            {
                **field,
                "is_primary": index == 0,
                "field_id": f"fld-{index + 1}",
            }
            for index, field in enumerate(fields)
        ]
        self.records[table_id] = []
        return {"table_id": table_id}

    def list_fields(self, app_token: str, table_id: str) -> list[dict[str, Any]]:
        return list(self.fields[table_id])

    def create_field(
        self,
        app_token: str,
        table_id: str,
        *,
        field_name: str,
        field_type: int,
        property: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        field = {
            "field_name": field_name,
            "type": field_type,
            "is_primary": False,
            "field_id": f"fld-{len(self.fields[table_id]) + 1}",
        }
        self.fields[table_id].append(field)
        return field

    def update_field(
        self,
        app_token: str,
        table_id: str,
        field_id: str,
        *,
        field_name: str,
        field_type: int,
        property: dict[str, Any] | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        raise AssertionError("not used")

    def list_records(self, app_token: str, table_id: str) -> list[dict[str, Any]]:
        return list(self.records[table_id])

    def count_records(self, app_token: str, table_id: str) -> int:
        return len(self.records[table_id])

    def batch_create_records(
        self, app_token: str, table_id: str, fields: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        created: list[dict[str, Any]] = []
        for item in fields:
            record = {
                "record_id": f"rec-{self._next_record}",
                "fields": dict(item),
            }
            self._next_record += 1
            self.records[table_id].append(record)
            created.append(record)
        return created

    def batch_update_records(
        self, app_token: str, table_id: str, records: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        by_id = {str(item["record_id"]): item for item in self.records[table_id]}
        for update in records:
            by_id[str(update["record_id"])]["fields"].update(update["fields"])
        return records

    def batch_delete_records(
        self, app_token: str, table_id: str, record_ids: list[str]
    ) -> None:
        self.records[table_id] = [
            item
            for item in self.records[table_id]
            if str(item["record_id"]) not in record_ids
        ]


@dataclass(frozen=True)
class FakeOutcome:
    task_id: str
    status: str
    counts: dict[str, int] | None = None


def build_setup(gateway: FakeAdminGateway, env_file: Path) -> RuntimeStatusTableSetup:
    if not env_file.exists():
        env_file.write_text("", encoding="utf-8")
    return RuntimeStatusTableSetup(
        gateway=gateway,
        app_token="app",
        env_file=env_file,
    )


def test_runtime_status_setup_is_idempotent_and_writes_env(tmp_path: Path) -> None:
    gateway = FakeAdminGateway()
    env_file = tmp_path / ".env"
    env_file.write_text("YFD_FEISHU_APP_SECRET=keep\n", encoding="utf-8")

    first = build_setup(gateway, env_file).ensure()
    second = build_setup(gateway, env_file).ensure()

    assert first.table_created is True
    assert second.table_created is False
    assert first.record_created is True
    assert second.record_created is False
    assert set(first.fields_created) == set()
    assert set(second.fields_created) == set()
    table_id = gateway.tables[RUNTIME_STATUS_TABLE_NAME]
    assert len(gateway.records[table_id]) == 1
    assert gateway.records[table_id][0]["fields"]["状态Key"] == RUNTIME_STATUS_KEY
    env_text = env_file.read_text(encoding="utf-8")
    assert "YFD_FEISHU_APP_SECRET=keep" in env_text
    assert f"YFD_FEISHU_RUNTIME_STATUS_TABLE_ID={table_id}" in env_text


def test_runtime_status_setup_adds_missing_optional_fields(tmp_path: Path) -> None:
    gateway = FakeAdminGateway()
    gateway.create_table(
        "app",
        name=RUNTIME_STATUS_TABLE_NAME,
        fields=[{"field_name": "状态Key", "type": 1}],
    )

    result = build_setup(gateway, tmp_path / ".env").ensure()

    assert set(result.fields_created) == {
        name for name, field_type in RUNTIME_STATUS_FIELDS if name != "状态Key"
    }


def test_runtime_status_setup_rejects_wrong_field_type(tmp_path: Path) -> None:
    gateway = FakeAdminGateway()
    gateway.create_table(
        "app",
        name=RUNTIME_STATUS_TABLE_NAME,
        fields=[
            {"field_name": "状态Key", "type": 1},
            {"field_name": "最近状态", "type": 2},
        ],
    )

    with pytest.raises(ConfigurationError, match="最近状态"):
        build_setup(gateway, tmp_path / ".env").ensure()


def test_runtime_status_reporter_updates_one_row_through_lifecycle(tmp_path: Path) -> None:
    gateway = FakeAdminGateway()
    result = build_setup(gateway, tmp_path / ".env").ensure()
    reporter = RuntimeStatusReporter(
        gateway=gateway,
        app_token="app",
        table_id=result.table_id,
        instance_id="test-host:123",
        version="0.1.0",
    )
    started = datetime(2026, 9, 7, 0, 0, tzinfo=UTC)
    finished = datetime(2026, 9, 7, 0, 1, tzinfo=UTC)

    reporter.report_started(started_at=started)
    reporter.report_completed(
        started_at=started,
        finished_at=finished,
        outcomes=[
            FakeOutcome(
                "latest-video-tracker",
                "success",
                {"snapshots": 1, "feishu_writes": 5},
            ),
            FakeOutcome("channel-history-daily", "failed"),
        ],
    )

    assert len(gateway.records[result.table_id]) == 1
    fields = gateway.records[result.table_id][0]["fields"]
    assert fields["最近调度唤醒时间（北京时间）"] == 1788739200000
    assert fields["最近调度完成时间（北京时间）"] == 1788739260000
    assert fields["最近状态"] == "有失败"
    assert fields["本轮到期任务数"] == 2
    assert fields["本轮失败任务数"] == 1
    assert fields["本轮飞书写入记录数"] == 5
    assert fields["最近一次调度结果"] == (
        "2026-09-07 08:00｜调度有失败｜Data API 已采集 1 个视频｜飞书已更新 5 条"
    )
    assert fields["运行实例"] == "test-host:123"
    assert "channel-history-daily:failed" in fields["最近结果摘要"]


def test_runtime_status_reporter_marks_empty_tick_as_success(tmp_path: Path) -> None:
    gateway = FakeAdminGateway()
    result = build_setup(gateway, tmp_path / ".env").ensure()
    reporter = RuntimeStatusReporter(
        gateway=gateway,
        app_token="app",
        table_id=result.table_id,
        instance_id="test-host:456",
        version="0.1.0",
    )
    finished = datetime(2026, 9, 7, 1, 0, tzinfo=UTC)

    reporter.report_completed(
        started_at=finished,
        finished_at=finished,
        outcomes=[],
    )

    fields = gateway.records[result.table_id][0]["fields"]
    assert fields["最近状态"] == "正常"
    assert fields["本轮到期任务数"] == 0
    assert fields["本轮失败任务数"] == 0
    assert fields["本轮飞书写入记录数"] == 0
    assert fields["最近一次调度结果"] == (
        "2026-09-07 09:00｜调度正常｜本轮没有业务任务到期"
    )
    assert fields["最近成功时间（北京时间）"] == 1788742800000
    assert "没有业务任务到期" in fields["最近结果摘要"]


def test_runtime_status_reports_data_api_not_due_separately_from_failure(
    tmp_path: Path,
) -> None:
    gateway = FakeAdminGateway()
    result = build_setup(gateway, tmp_path / ".env").ensure()
    reporter = RuntimeStatusReporter(
        gateway=gateway,
        app_token="app",
        table_id=result.table_id,
        instance_id="test-host:789",
        version="0.1.0",
    )
    started = datetime(2026, 9, 7, 2, 0, tzinfo=UTC)

    reporter.report_completed(
        started_at=started,
        finished_at=started,
        outcomes=[
            FakeOutcome(
                "latest-video-tracker",
                "success",
                {"snapshots": 0, "feishu_writes": 0},
            )
        ],
    )

    fields = gateway.records[result.table_id][0]["fields"]
    assert fields["最近状态"] == "正常"
    assert fields["最近一次调度结果"] == (
        "2026-09-07 10:00｜调度正常｜Data API 本轮无需采集｜飞书业务数据无变化"
    )


def test_runtime_status_reports_failure_in_the_combined_result(tmp_path: Path) -> None:
    gateway = FakeAdminGateway()
    result = build_setup(gateway, tmp_path / ".env").ensure()
    reporter = RuntimeStatusReporter(
        gateway=gateway,
        app_token="app",
        table_id=result.table_id,
        instance_id="test-host:999",
        version="0.1.0",
    )
    started = datetime(2026, 9, 7, 3, 0, tzinfo=UTC)

    reporter.report_failed(
        started_at=started,
        finished_at=started,
        error=TimeoutError("模拟超时"),
    )

    fields = gateway.records[result.table_id][0]["fields"]
    assert fields["最近状态"] == "异常"
    assert fields["本轮飞书写入记录数"] == 0
    assert fields["最近一次调度结果"] == (
        "2026-09-07 11:00｜执行失败｜TimeoutError: 模拟超时"
    )
