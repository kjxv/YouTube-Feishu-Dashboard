from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from youtube_feishu_dashboard.api.feishu.protocols import FeishuTableAdminGateway
from youtube_feishu_dashboard.core.errors import ConfigurationError
from youtube_feishu_dashboard.services.catalog_sync import scalar_text
from youtube_feishu_dashboard.services.config_center_bootstrap import (
    READ_ONLY_FEISHU_FIELD_TYPES,
    FieldSpec,
)

from yfd_latest_video_tracker.manifest import MILESTONE_HOURS, MODULE_ID


@dataclass(frozen=True, slots=True)
class LatestMilestoneSetupResult:
    created_fields: tuple[str, ...]
    reused_fields: tuple[str, ...]
    created_mappings: int
    updated_mappings: int
    unchanged_mappings: int


BUSINESS_FIELDS = tuple(
    spec
    for hour in MILESTONE_HOURS
    for spec in (
        FieldSpec(name=f"发布{hour}小时播放量", field_type=2),
        FieldSpec(name=f"{hour}小时样本发布后分钟数", field_type=2),
        FieldSpec(name=f"{hour}小时样本采集时间（北京时间）", field_type=1),
    )
)


def mapping_records(video_main_table_id: str) -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = []
    common = {
        "模块ID": MODULE_ID,
        "模块中文名": "最新发布视频实时追踪",
        "目标表中文名": "视频追踪主表",
        "目标表ID": video_main_table_id,
        "API类型": "非API（系统计算）",
        "实现状态": "已接入",
        "启用": True,
    }
    for hour in MILESTONE_HOURS:
        minutes = hour * 60
        records.extend(
            (
                {
                    **common,
                    "映射名称": f"视频追踪主表｜发布{hour}小时播放量",
                    "飞书列名": f"发布{hour}小时播放量",
                    "标准字段ID": f"VIDEO_VIEWS_AT_{hour}H",
                    "标准字段中文名": f"发布后{hour}小时播放量",
                    "API官方字段": f"发布后{minutes}分钟附近的真实Data API快照",
                    "写入方式": "选择前后90分钟内最近的真实快照后写入",
                    "备注": "无合格快照时留空，不用当前值补造历史节点",
                },
                {
                    **common,
                    "映射名称": f"视频追踪主表｜{hour}小时样本发布后分钟数",
                    "飞书列名": f"{hour}小时样本发布后分钟数",
                    "标准字段ID": f"VIDEO_{hour}H_SAMPLE_AGE_MINUTES",
                    "标准字段中文名": f"{hour}小时样本发布后分钟数",
                    "API官方字段": "样本采集时间-视频发布时间",
                    "写入方式": "按真实时间差换算为完整分钟数",
                    "备注": f"用于核查样本与标准{minutes}分钟的偏差",
                },
                {
                    **common,
                    "映射名称": f"视频追踪主表｜{hour}小时样本采集时间（北京时间）",
                    "飞书列名": f"{hour}小时样本采集时间（北京时间）",
                    "标准字段ID": f"VIDEO_{hour}H_SAMPLE_AT_BEIJING",
                    "标准字段中文名": f"{hour}小时样本采集时间（北京时间）",
                    "API官方字段": "被选中真实Data API快照的采集时间",
                    "写入方式": "转换为带+08:00时区的北京时间文本",
                    "备注": "用于追溯节点播放量来自哪一条真实快照",
                },
            )
        )
    return tuple(records)


class LatestVideoMilestoneFeishuSetup:
    """幂等补齐视频追踪主表的节点字段和共享映射。"""

    def __init__(
        self,
        *,
        gateway: FeishuTableAdminGateway,
        app_token: str,
        video_main_table_id: str,
        mapping_table_id: str,
    ) -> None:
        self.gateway = gateway
        self.app_token = app_token
        self.video_main_table_id = video_main_table_id
        self.mapping_table_id = mapping_table_id

    def apply(self) -> LatestMilestoneSetupResult:
        missing_fields, reused_fields = self._preflight_business_fields()
        creates, updates, unchanged = self._preflight_mappings()
        for spec in missing_fields:
            self.gateway.create_field(
                self.app_token,
                self.video_main_table_id,
                field_name=spec.name,
                field_type=spec.field_type,
            )
        if updates:
            self.gateway.batch_update_records(
                self.app_token, self.mapping_table_id, updates
            )
        if creates:
            self.gateway.batch_create_records(
                self.app_token, self.mapping_table_id, creates
            )
        return LatestMilestoneSetupResult(
            created_fields=tuple(item.name for item in missing_fields),
            reused_fields=tuple(reused_fields),
            created_mappings=len(creates),
            updated_mappings=len(updates),
            unchanged_mappings=unchanged,
        )

    def validate(self) -> None:
        self._preflight_business_fields()
        self._preflight_mappings()

    def _preflight_business_fields(self) -> tuple[list[FieldSpec], list[str]]:
        by_name: dict[str, dict[str, Any]] = {}
        duplicates: set[str] = set()
        for raw in self.gateway.list_fields(self.app_token, self.video_main_table_id):
            name = str(raw.get("field_name") or "").strip()
            if not name:
                continue
            if name in by_name:
                duplicates.add(name)
            by_name[name] = raw
        if duplicates:
            raise ConfigurationError(
                "视频追踪主表存在重复字段名："
                + "、".join(sorted(duplicates))
                + "。为避免写错列，本次升级已停止。"
            )

        missing: list[FieldSpec] = []
        reused: list[str] = []
        for spec in BUSINESS_FIELDS:
            found = by_name.get(spec.name)
            if found is None:
                missing.append(spec)
                continue
            actual_type = int(found.get("type", -1))
            if not spec.accepts_type(actual_type):
                raise ConfigurationError(
                    f"视频追踪主表字段“{spec.name}”类型为 {actual_type}，"
                    f"程序要求 {spec.field_type}；不会自动覆盖已有字段。"
                )
            reused.append(spec.name)
        return missing, reused

    def _preflight_mappings(
        self,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
        mapping_fields = self.gateway.list_fields(
            self.app_token, self.mapping_table_id
        )
        field_names = {
            str(item.get("field_name") or "").strip()
            for item in mapping_fields
            if item.get("field_name")
        }
        required = {"模块ID", "目标表ID", "飞书列名", "标准字段ID", "启用"}
        missing_columns = sorted(required - field_names)
        if missing_columns:
            raise ConfigurationError(
                "飞书共享映射表缺少程序关键列：" + "、".join(missing_columns)
            )
        read_only = {
            str(item.get("field_name"))
            for item in mapping_fields
            if int(item.get("type", -1)) in READ_ONLY_FEISHU_FIELD_TYPES
        }

        by_identity: dict[tuple[str, str, str], dict[str, Any]] = {}
        duplicate_identities: set[tuple[str, str, str]] = set()
        mapped_columns: dict[tuple[str, str, str], str] = {}
        for record in self.gateway.list_records(self.app_token, self.mapping_table_id):
            fields = record.get("fields", {})
            module_id = scalar_text(fields.get("模块ID")) or ""
            table_id = scalar_text(fields.get("目标表ID")) or ""
            standard_id = scalar_text(fields.get("标准字段ID")) or ""
            column = scalar_text(fields.get("飞书列名")) or ""
            identity = (module_id, table_id, standard_id)
            if all(identity):
                if identity in by_identity:
                    duplicate_identities.add(identity)
                by_identity[identity] = record
            column_key = (module_id, table_id, column)
            if all(column_key) and standard_id:
                previous = mapped_columns.get(column_key)
                if previous and previous != standard_id:
                    raise ConfigurationError(
                        f"共享映射表中列“{column}”同时对应 {previous} 和 "
                        f"{standard_id}；本次升级已停止。"
                    )
                mapped_columns[column_key] = standard_id
        if duplicate_identities:
            rendered = "、".join(
                "/".join(item) for item in sorted(duplicate_identities)
            )
            raise ConfigurationError(
                "飞书共享映射表存在重复的模块/目标表/标准字段组合："
                + rendered
                + "。请先清理重复行。"
            )

        creates: list[dict[str, Any]] = []
        updates: list[dict[str, Any]] = []
        unchanged = 0
        for desired in mapping_records(self.video_main_table_id):
            identity = (
                str(desired["模块ID"]),
                str(desired["目标表ID"]),
                str(desired["标准字段ID"]),
            )
            writable = {
                name: value
                for name, value in desired.items()
                if name in field_names and name not in read_only
            }
            existing = by_identity.get(identity)
            if existing is None:
                creates.append(writable)
                continue
            record_id = str(existing.get("record_id") or "").strip()
            if not record_id:
                raise ConfigurationError("共享映射表中的已有记录缺少 record_id。")
            existing_fields = existing.get("fields", {})
            if all(
                self._equivalent(existing_fields.get(name), value)
                for name, value in writable.items()
            ):
                unchanged += 1
                continue
            updates.append({"record_id": record_id, "fields": writable})
        return creates, updates, unchanged

    @staticmethod
    def _equivalent(actual: Any, expected: Any) -> bool:
        if isinstance(expected, bool):
            scalar = scalar_text(actual)
            if isinstance(actual, bool):
                return actual is expected
            if scalar is None:
                return not expected
            return (
                scalar.strip().lower() not in {"false", "0", "否", "停用"}
            ) is expected
        return (scalar_text(actual) or "") == str(expected)
