"""业务表同步前的只读结构检查。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from youtube_feishu_dashboard.api.feishu.config_center import ModuleFieldMapping
from youtube_feishu_dashboard.api.feishu.protocols import FeishuTableAdminGateway
from youtube_feishu_dashboard.catalog.field_catalog import FieldCatalog
from youtube_feishu_dashboard.core.errors import DashboardError
from youtube_feishu_dashboard.services.calculated_fields import CalculatedFieldEngine


def validate_field_execution_policy(
    *,
    catalog: FieldCatalog,
    module_id: str,
    mappings: Iterable[ModuleFieldMapping],
    module_code_field_ids: set[str],
    supported_api_sources: set[str] | None = None,
) -> dict[str, Any]:
    """只读检查映射字段由谁产生，以及当前程序是否能够安全写入。"""
    supported_sources = supported_api_sources or {"data_api"}
    enabled = [item for item in mappings if item.enabled and item.module_id == module_id]
    classifications: dict[str, list[str]] = {
        "data_api": [],
        "analytics_api": [],
        "reporting_api": [],
        "module_code": [],
        "safe_expression": [],
        "feishu_formula": [],
        "manual": [],
        "not_directly_usable": [],
        "unsupported": [],
    }
    reasons: list[str] = []
    safe_expression_ids: list[str] = []
    field_availability: dict[str, str] = {}

    for mapping in enabled:
        field_id = mapping.standard_field_id
        try:
            definition = catalog.get(field_id)
            catalog.validate_requirements((field_id,))
        except DashboardError as exc:
            _append_reason(reasons, f"{field_id}：{exc}")
            _append_unique(classifications["unsupported"], field_id)
            continue

        field_availability[field_id] = definition.current_availability_cn
        if not definition.is_directly_usable:
            _append_unique(classifications["not_directly_usable"], field_id)
            _append_reason(
                reasons,
                f"{field_id}：当前可用状态为“{definition.current_availability_cn}”；"
                "只有“可直接使用”的字段才能启用正式写入映射",
            )

        if definition.is_api_field:
            _append_unique(classifications[definition.api_source], field_id)
            if definition.api_source not in supported_sources:
                _append_reason(
                    reasons,
                    f"{field_id}：来自 {definition.api_source}，"
                    "最近视频追踪模块当前尚未接入该字段来源",
                )
            continue

        if definition.calculation_mode == "module_code":
            _append_unique(classifications["module_code"], field_id)
            if field_id not in module_code_field_ids:
                _append_reason(
                    reasons,
                    f"{field_id}：需要功能模块代码生成，"
                    "但当前模块没有在 output_field_ids 中登记",
                )
            if definition.implementation_module not in {None, module_id}:
                _append_reason(
                    reasons,
                    f"{field_id}：登记的实现模块是 {definition.implementation_module}，"
                    f"不是 {module_id}",
                )
            continue

        if definition.calculation_mode == "safe_expression":
            _append_unique(classifications["safe_expression"], field_id)
            _append_unique(safe_expression_ids, field_id)
            if definition.implementation_module not in {None, module_id}:
                _append_reason(
                    reasons,
                    f"{field_id}：安全表达式登记给模块"
                    f" {definition.implementation_module}，不是 {module_id}",
                )
            continue

        if definition.calculation_mode == "feishu_formula":
            _append_unique(classifications["feishu_formula"], field_id)
            _append_reason(
                reasons,
                f"{field_id}：应由飞书公式列自行计算，"
                "不能作为程序写入映射；请在映射表中停用或删除这一行",
            )
            continue

        if definition.calculation_mode == "manual":
            _append_unique(classifications["manual"], field_id)
            _append_reason(
                reasons,
                f"{field_id}：属于手动填写字段，"
                "不能作为程序写入映射；请在映射表中停用或删除这一行",
            )
            continue

        _append_unique(classifications["unsupported"], field_id)
        _append_reason(
            reasons,
            f"{field_id}：字段来源 {definition.api_source} 与计算方式"
            f" {definition.calculation_mode} 没有可执行路径",
        )

    for field_id in sorted(module_code_field_ids):
        try:
            definition = catalog.get(field_id)
            catalog.validate_requirements((field_id,))
        except DashboardError as exc:
            _append_reason(reasons, f"模块代码字段 {field_id}：{exc}")
            continue
        if definition.calculation_mode != "module_code":
            _append_reason(
                reasons,
                f"模块代码字段 {field_id}：字段字典计算方式是"
                f" {definition.calculation_mode}，必须保持为 module_code",
            )
        if not definition.is_directly_usable:
            _append_reason(
                reasons,
                f"模块代码字段 {field_id}：当前可用状态为"
                f"“{definition.current_availability_cn}”，暂不可执行",
            )
        if definition.implementation_module not in {None, module_id}:
            _append_reason(
                reasons,
                f"模块代码字段 {field_id}：登记的实现模块是"
                f" {definition.implementation_module}，不是 {module_id}",
            )

    compiled_safe_fields: list[str] = []
    if safe_expression_ids:
        try:
            engine = CalculatedFieldEngine.compile(
                catalog,
                safe_expression_ids,
                module_code_field_ids=module_code_field_ids,
            )
            compiled_safe_fields = list(engine.calculated_field_ids)
        except DashboardError as exc:
            _append_reason(reasons, f"安全表达式检查失败：{exc}")

    return {
        "ready": not reasons,
        "enabled_mapping_count": len(enabled),
        "mapped_standard_field_count": len(
            {item.standard_field_id for item in enabled}
        ),
        "field_current_availability": field_availability,
        "field_classifications": classifications,
        "compiled_safe_expression_field_ids": compiled_safe_fields,
        "blocking_reasons": reasons,
    }


class BusinessTableSyncValidator:
    """检查表 ID、远端字段和字段映射；不会创建或更新任何飞书内容。"""

    def __init__(
        self,
        *,
        gateway: FeishuTableAdminGateway,
        app_token: str,
    ) -> None:
        self.gateway = gateway
        self.app_token = app_token

    def validate(
        self,
        *,
        module_id: str,
        table_ids: Mapping[str, str | None],
        mappings: Iterable[ModuleFieldMapping],
        implemented_tables: set[str],
    ) -> dict[str, Any]:
        enabled = [item for item in mappings if item.enabled and item.module_id == module_id]
        configured_ids = {table_id for table_id in table_ids.values() if table_id}
        unknown_targets = sorted(
            {
                item.target_table_id
                for item in enabled
                if item.target_table_id and item.target_table_id not in configured_ids
            }
        )
        reports: dict[str, dict[str, Any]] = {}
        blocking_reasons: list[str] = []

        for table_name, table_id in table_ids.items():
            report = self._validate_table(
                table_name=table_name,
                table_id=table_id,
                mappings=enabled,
                implemented=table_name in implemented_tables,
            )
            reports[table_name] = report
            blocking_reasons.extend(str(item) for item in report["blocking_reasons"])

        configuration_ready = all(
            bool(report["configuration_ready"]) for report in reports.values()
        )
        implementation_ready = all(bool(report["implemented"]) for report in reports.values())
        snapshot_report = reports.get("视频实时快照表", {})
        return {
            "module_id": module_id,
            "mode": "feishu_read_only_sync_validation",
            "external_requests_made": True,
            "external_writes_made": False,
            "tables": reports,
            "unrecognized_mapping_target_table_ids": unknown_targets,
            "summary": {
                "current_snapshot_sync_ready": bool(
                    snapshot_report.get("configuration_ready")
                    and snapshot_report.get("implemented")
                ),
                "three_table_configuration_ready": configuration_ready,
                "three_table_implementation_ready": implementation_ready,
                "safe_to_run_full_three_table_sync": configuration_ready
                and implementation_ready
                and not unknown_targets,
                "blocking_reasons": blocking_reasons,
            },
        }

    def _validate_table(
        self,
        *,
        table_name: str,
        table_id: str | None,
        mappings: list[ModuleFieldMapping],
        implemented: bool,
    ) -> dict[str, Any]:
        table_mappings = [item for item in mappings if item.target_table_id == table_id]
        mapped_columns = [item.feishu_column for item in table_mappings]
        standard_ids = [item.standard_field_id for item in table_mappings]
        duplicate_standard_ids = sorted(
            field_id for field_id, count in Counter(standard_ids).items() if count > 1
        )
        duplicate_columns = sorted(
            column for column, count in Counter(mapped_columns).items() if count > 1
        )
        reasons: list[str] = []
        if not table_id:
            reasons.append(f"{table_name}：缺少 Table ID")
            return {
                "table_id": None,
                "accessible": False,
                "implemented": implemented,
                "mapping_count": 0,
                "mapped_columns": [],
                "missing_columns": [],
                "duplicate_standard_field_ids": [],
                "duplicate_mapped_columns": [],
                "configuration_ready": False,
                "blocking_reasons": reasons,
            }

        accessible = False
        actual_columns: set[str] = set()
        access_error: str | None = None
        try:
            fields = self.gateway.list_fields(self.app_token, table_id)
            actual_columns = {
                str(item.get("field_name"))
                for item in fields
                if item.get("field_name")
            }
            accessible = True
        except DashboardError as exc:
            access_error = str(exc)
            reasons.append(f"{table_name}：无法读取表结构：{exc}")

        missing_columns = sorted(set(mapped_columns) - actual_columns) if accessible else []
        if not table_mappings:
            reasons.append(f"{table_name}：没有已启用的字段映射")
        if missing_columns:
            reasons.append(f"{table_name}：映射列在业务表中不存在：{'、'.join(missing_columns)}")
        if duplicate_standard_ids:
            reasons.append(
                f"{table_name}：标准字段ID重复：{'、'.join(duplicate_standard_ids)}"
            )
        if duplicate_columns:
            reasons.append(f"{table_name}：飞书列重复映射：{'、'.join(duplicate_columns)}")
        if not implemented:
            reasons.append(f"{table_name}：写入逻辑尚未接入")

        configuration_ready = bool(
            accessible
            and table_mappings
            and not missing_columns
            and not duplicate_standard_ids
            and not duplicate_columns
        )
        return {
            "table_id": table_id,
            "accessible": accessible,
            "access_error": access_error,
            "implemented": implemented,
            "field_count": len(actual_columns),
            "mapping_count": len(table_mappings),
            "mapped_columns": sorted(set(mapped_columns)),
            "missing_columns": missing_columns,
            "duplicate_standard_field_ids": duplicate_standard_ids,
            "duplicate_mapped_columns": duplicate_columns,
            "configuration_ready": configuration_ready,
            "blocking_reasons": reasons,
        }


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def _append_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)
