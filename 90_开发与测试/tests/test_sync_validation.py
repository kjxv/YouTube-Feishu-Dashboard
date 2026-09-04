from __future__ import annotations

from typing import Any

from youtube_feishu_dashboard.api.feishu.config_center import ModuleFieldMapping
from youtube_feishu_dashboard.catalog.field_catalog import FieldCatalog
from youtube_feishu_dashboard.services.sync_validation import (
    BusinessTableSyncValidator,
    validate_field_execution_policy,
)


def _mark_directly_usable(catalog: FieldCatalog, *field_ids: str) -> FieldCatalog:
    selected = set(field_ids)
    document = catalog.document.model_copy(
        update={
            "fields": tuple(
                item.model_copy(update={"implementation_status": "tested"})
                if item.standard_field_id in selected
                else item
                for item in catalog.document.fields
            )
        }
    )
    return FieldCatalog(document)


class FakeTableGateway:
    def __init__(self) -> None:
        self.fields = {
            "main": [{"field_name": "视频唯一编号"}],
            "snapshot": [
                {"field_name": "视频唯一编号"},
                {"field_name": "累计播放量"},
            ],
            "comparison": [{"field_name": "视频唯一编号"}],
        }

    def list_fields(self, app_token: str, table_id: str) -> list[dict[str, Any]]:
        return self.fields[table_id]


def _mapping(
    field_id: str,
    *,
    column: str | None = None,
    table_id: str = "snapshot",
) -> ModuleFieldMapping:
    return ModuleFieldMapping(
        module_id="latest_video_tracker",
        standard_field_id=field_id,
        feishu_column=column or field_id,
        target_table_id=table_id,
    )


def _calculated_record(
    field_id: str,
    *,
    source: str = "非API（系统计算）",
    calculation_location: str = "通用计算引擎",
    expression: str = "VIDEO_VIEWS_PUBLIC + 1",
    dependencies: str = "VIDEO_VIEWS_PUBLIC",
) -> dict[str, object]:
    return {
        "fields": {
            "标准字段ID": field_id,
            "中文名称": field_id,
            "API来源": source,
            "官方字段": "",
            "数据类型": "number",
            "单位": "",
            "数据层级": "video",
            "查询组": "test_calculated",
            "计算位置": calculation_location,
            "依赖标准字段": dependencies,
            "计算说明": "测试规则",
            "机器计算表达式": expression,
            "空值策略": "任一依赖为空则为空",
            "实现模块": "latest_video_tracker",
            "实现状态": "已接入",
            "规则版本": "1",
            "启用": True,
        }
    }


def test_three_table_validator_distinguishes_current_and_full_readiness() -> None:
    validator = BusinessTableSyncValidator(
        gateway=FakeTableGateway(),  # type: ignore[arg-type]
        app_token="base",
    )
    result = validator.validate(
        module_id="latest_video_tracker",
        table_ids={
            "视频追踪主表": "main",
            "视频实时快照表": "snapshot",
            "视频同期对比表": "comparison",
        },
        mappings=(
            ModuleFieldMapping(
                module_id="latest_video_tracker",
                standard_field_id="VIDEO_ID",
                feishu_column="视频唯一编号",
                target_table_id="snapshot",
            ),
            ModuleFieldMapping(
                module_id="latest_video_tracker",
                standard_field_id="VIDEO_VIEWS_PUBLIC",
                feishu_column="累计播放量",
                target_table_id="snapshot",
            ),
        ),
        implemented_tables={"视频实时快照表"},
    )

    assert result["external_writes_made"] is False
    assert result["tables"]["视频实时快照表"]["configuration_ready"] is True
    assert result["summary"]["current_snapshot_sync_ready"] is True
    assert result["summary"]["three_table_configuration_ready"] is False
    assert result["summary"]["three_table_implementation_ready"] is False
    assert result["summary"]["safe_to_run_full_three_table_sync"] is False


def test_three_table_validator_reports_missing_business_column() -> None:
    validator = BusinessTableSyncValidator(
        gateway=FakeTableGateway(),  # type: ignore[arg-type]
        app_token="base",
    )
    result = validator.validate(
        module_id="latest_video_tracker",
        table_ids={"视频实时快照表": "snapshot"},
        mappings=(
            ModuleFieldMapping(
                module_id="latest_video_tracker",
                standard_field_id="VIDEO_TITLE",
                feishu_column="不存在的列",
                target_table_id="snapshot",
            ),
        ),
        implemented_tables={"视频实时快照表"},
    )

    table = result["tables"]["视频实时快照表"]
    assert table["configuration_ready"] is False
    assert table["missing_columns"] == ["不存在的列"]


def test_field_execution_policy_accepts_supported_field_sources() -> None:
    catalog = FieldCatalog.load_builtin().overlay_feishu_records(
        [_calculated_record("VIDEO_TEST_SCORE")]
    )
    catalog = _mark_directly_usable(catalog, "VIDEO_ID")

    report = validate_field_execution_policy(
        catalog=catalog,
        module_id="latest_video_tracker",
        mappings=(
            _mapping("VIDEO_ID"),
            _mapping("CONTENT_BATCH_ID"),
            _mapping("VIDEO_TEST_SCORE"),
        ),
        module_code_field_ids={"CONTENT_BATCH_ID"},
    )

    assert report["ready"] is True
    assert report["field_classifications"]["data_api"] == ["VIDEO_ID"]
    assert report["field_classifications"]["module_code"] == ["CONTENT_BATCH_ID"]
    assert report["field_classifications"]["safe_expression"] == ["VIDEO_TEST_SCORE"]
    assert report["compiled_safe_expression_field_ids"] == ["VIDEO_TEST_SCORE"]
    assert report["blocking_reasons"] == []


def test_field_execution_policy_accepts_analytics_only_when_declared_supported() -> None:
    mapping = (_mapping("ANALYTICS_ENGAGED_VIEWS"),)
    catalog = _mark_directly_usable(
        FieldCatalog.load_builtin(), "ANALYTICS_ENGAGED_VIEWS"
    )

    blocked = validate_field_execution_policy(
        catalog=catalog,
        module_id="latest_video_tracker",
        mappings=mapping,
        module_code_field_ids=set(),
    )
    accepted = validate_field_execution_policy(
        catalog=catalog,
        module_id="latest_video_tracker",
        mappings=mapping,
        module_code_field_ids=set(),
        supported_api_sources={"data_api", "analytics_api"},
    )

    assert blocked["ready"] is False
    assert accepted["ready"] is True
    assert accepted["field_classifications"]["analytics_api"] == [
        "ANALYTICS_ENGAGED_VIEWS"
    ]


def test_field_execution_policy_accepts_integrated_estimated_ad_revenue() -> None:
    report = validate_field_execution_policy(
        catalog=FieldCatalog.load_builtin(),
        module_id="latest_video_tracker",
        mappings=(_mapping("ANALYTICS_EST_AD_REVENUE"),),
        module_code_field_ids=set(),
        supported_api_sources={"analytics_api"},
    )

    assert report["ready"] is True
    assert report["field_current_availability"]["ANALYTICS_EST_AD_REVENUE"] == (
        "可直接使用"
    )
    assert report["field_classifications"]["analytics_api"] == [
        "ANALYTICS_EST_AD_REVENUE"
    ]
    assert report["blocking_reasons"] == []


def test_field_execution_policy_blocks_formula_and_manual_write_mappings() -> None:
    catalog = FieldCatalog.load_builtin().overlay_feishu_records(
        [
            _calculated_record(
                "VIDEO_FEISHU_SCORE",
                source="非API（飞书公式）",
                calculation_location="飞书多维表格",
                expression="",
                dependencies="",
            ),
            _calculated_record(
                "VIDEO_EDITOR_NOTE",
                source="非API（手动填写）",
                calculation_location="手动填写",
                expression="",
                dependencies="",
            ),
        ]
    )

    report = validate_field_execution_policy(
        catalog=catalog,
        module_id="latest_video_tracker",
        mappings=(
            _mapping("VIDEO_FEISHU_SCORE"),
            _mapping("VIDEO_EDITOR_NOTE"),
        ),
        module_code_field_ids=set(),
    )

    assert report["ready"] is False
    assert report["field_classifications"]["feishu_formula"] == [
        "VIDEO_FEISHU_SCORE"
    ]
    assert report["field_classifications"]["manual"] == ["VIDEO_EDITOR_NOTE"]
    assert any("飞书公式列自行计算" in item for item in report["blocking_reasons"])
    assert any("手动填写字段" in item for item in report["blocking_reasons"])


def test_field_execution_policy_blocks_unregistered_module_code_field() -> None:
    report = validate_field_execution_policy(
        catalog=FieldCatalog.load_builtin(),
        module_id="latest_video_tracker",
        mappings=(_mapping("CONTENT_BATCH_ID"),),
        module_code_field_ids=set(),
    )

    assert report["ready"] is False
    assert any("没有在 output_field_ids 中登记" in item for item in report["blocking_reasons"])


def test_field_execution_policy_blocks_invalid_safe_expression() -> None:
    catalog = FieldCatalog.load_builtin().overlay_feishu_records(
        [
            _calculated_record(
                "VIDEO_INVALID_SCORE",
                expression="VIDEO_VIEWS_PUBLIC + VIDEO_LIKES_PUBLIC",
                dependencies="VIDEO_VIEWS_PUBLIC",
            )
        ]
    )

    report = validate_field_execution_policy(
        catalog=catalog,
        module_id="latest_video_tracker",
        mappings=(_mapping("VIDEO_INVALID_SCORE"),),
        module_code_field_ids=set(),
    )

    assert report["ready"] is False
    assert any("未声明依赖" in item for item in report["blocking_reasons"])


def test_three_table_validator_reports_duplicate_feishu_column_mapping() -> None:
    validator = BusinessTableSyncValidator(
        gateway=FakeTableGateway(),  # type: ignore[arg-type]
        app_token="base",
    )
    result = validator.validate(
        module_id="latest_video_tracker",
        table_ids={"视频实时快照表": "snapshot"},
        mappings=(
            _mapping("VIDEO_ID", column="视频唯一编号"),
            _mapping("VIDEO_TITLE", column="视频唯一编号"),
        ),
        implemented_tables={"视频实时快照表"},
    )

    table = result["tables"]["视频实时快照表"]
    assert table["configuration_ready"] is False
    assert table["duplicate_mapped_columns"] == ["视频唯一编号"]
    assert any("飞书列重复映射" in item for item in table["blocking_reasons"])
