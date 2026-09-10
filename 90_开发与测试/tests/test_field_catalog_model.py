from __future__ import annotations

from collections import Counter

import pytest
from pydantic import ValidationError
from youtube_feishu_dashboard.catalog.field_catalog import (
    FieldCatalog,
    FieldDefinition,
)


def test_builtin_catalog_remains_backward_compatible() -> None:
    catalog = FieldCatalog.load_builtin()

    api_fields = [item for item in catalog.document.fields if item.is_api_field]
    system_fields = [item for item in catalog.document.fields if not item.is_api_field]
    assert len(catalog.document.fields) == 204
    assert len(api_fields) == 129
    assert len(system_fields) == 75
    assert catalog.get("VIDEO_TITLE").source_type == "data_api"
    assert catalog.get("VIDEO_TITLE").implementation_status == "tested"


def test_builtin_catalog_has_complete_three_state_availability_audit() -> None:
    catalog = FieldCatalog.load_builtin()
    counts = Counter(item.implementation_status for item in catalog.document.fields)

    assert counts == {"tested": 125, "implemented": 75, "planned": 4}
    assert catalog.get("VIDEO_MADE_FOR_KIDS").current_availability_cn == "可直接使用"
    assert (
        catalog.get("ANALYTICS_EST_AD_REVENUE").current_availability_cn
        == "可直接使用"
    )
    assert catalog.get("CHANNEL_ID").current_availability_cn == "可直接使用"
    assert catalog.get("ANALYTICS_EST_REVENUE").current_availability_cn == "可直接使用"
    assert catalog.get("VIDEO_TAGS").current_availability_cn == "底层未实现"


def test_video_data_api_scalar_fields_are_directly_usable_but_arrays_are_not() -> None:
    catalog = FieldCatalog.load_builtin()
    video_data_fields = [
        item
        for item in catalog.document.fields
        if item.api_source == "data_api" and item.entity_level == "video"
    ]

    assert all(
        item.implementation_status == "tested"
        for item in video_data_fields
        if item.data_type != "string_array"
    )
    assert {
        item.standard_field_id
        for item in video_data_fields
        if item.implementation_status == "planned"
    } == {"VIDEO_TAGS", "VIDEO_REGION_ALLOWED", "VIDEO_REGION_BLOCKED"}


def test_current_latest_tracker_api_fields_are_marked_directly_usable() -> None:
    from yfd_latest_video_tracker.manifest import (
        ANALYTICS_FIELD_IDS,
        API_FIELD_IDS,
        REPORTING_FIELD_IDS,
    )

    catalog = FieldCatalog.load_builtin()

    assert all(
        catalog.get(field_id).is_directly_usable
        for field_id in (*API_FIELD_IDS, *ANALYTICS_FIELD_IDS, *REPORTING_FIELD_IDS)
    )

    revenue_plan = catalog.build_request_plan(
        "latest_video_tracker", ["ANALYTICS_EST_AD_REVENUE"]
    )
    assert revenue_plan.analytics_metrics == ("estimatedAdRevenue",)
    assert revenue_plan.required_scopes == ("yt-analytics-monetary.readonly",)


def test_latest_video_system_fields_are_registered_with_runtime_metadata() -> None:
    from yfd_latest_video_tracker.manifest import OUTPUT_FIELD_IDS

    catalog = FieldCatalog.load_builtin()
    definitions = [catalog.get(field_id) for field_id in OUTPUT_FIELD_IDS]

    assert len(definitions) == 58
    assert all(not item.is_api_field for item in definitions)
    assert all(item.calculation_mode == "module_code" for item in definitions)
    shared_field_ids = {
        "VIDEO_URL",
        "VIDEO_TYPE",
        "DATA_API_FETCHED_AT_BEIJING",
        "DATA_API_DATA_THROUGH_AT_BEIJING_INFERRED",
        "ANALYTICS_FETCHED_AT_BEIJING",
        "ANALYTICS_DATA_THROUGH_AT_BEIJING",
        "VIDEO_VIEWS_AT_48H",
        "VIDEO_48H_SAMPLE_AGE_MINUTES",
        "VIDEO_48H_SAMPLE_AT_BEIJING",
    }
    assert all(
        item.implementation_module
        == (None if item.standard_field_id in shared_field_ids else "latest_video_tracker")
        for item in definitions
    )
    assert all(item.implementation_status == "tested" for item in definitions)
    assert catalog.get("CONTENT_BATCH_ID").dependency_field_ids == (
        "VIDEO_ID",
        "VIDEO_PUBLISHED_AT",
    )
    assert catalog.get("VIDEO_VIEW_RATE_PER_HOUR").output_precision == 2
    assert catalog.get("SYSTEM_OBSERVED_AT").data_type == "datetime"


def test_feishu_seed_contains_api_and_system_calculation_metadata() -> None:
    records = {
        str(item["标准字段ID"]): item
        for item in FieldCatalog.load_builtin().as_feishu_seed_records()
    }

    assert len(records) == 204
    system = records["VIDEO_VIEW_RATE_PER_HOUR"]
    assert system["API来源"] == "非API（系统计算）"
    assert system["计算位置"] == "功能模块代码"
    assert system["依赖标准字段"] == (
        "VIDEO_VIEWS_PUBLIC, VIDEO_PUBLISHED_AT, SYSTEM_OBSERVED_AT"
    )
    assert system["计算说明"]
    assert system["边界与空值规则"]
    assert system["空值策略"]
    assert system["输出精度"] == "2"
    assert system["实现模块"] == "latest_video_tracker"
    assert system["当前可用状态"] == "可直接使用"
    assert "实现状态" not in system

    api = records["VIDEO_TITLE"]
    assert api["API来源"] == "YouTube Data API"
    assert api["计算位置"] == "不需要计算"
    assert api["依赖标准字段"] == ""

    inferred = records["DATA_API_DATA_THROUGH_AT_BEIJING_INFERRED"]
    assert inferred["中文名称"].endswith("（推定）")
    assert inferred["数据类型"] == "string"
    assert inferred["当前可用状态"] == "可直接使用"
    assert "目标飞书列必须为文本" in inferred["边界与空值规则"]


def test_field_definition_supports_program_calculated_metadata() -> None:
    item = FieldDefinition(
        standard_field_id="VIDEO_COMMENTS_PER_DAY",
        cn_name="视频日均评论数",
        api_source="system_calculated",
        official_field=None,
        data_type="number",
        unit="条/天",
        entity_level="video",
        query_group="video_calculated",
        role="computed",
        calculation_mode="safe_expression",
        dependency_field_ids=("VIDEO_COMMENTS_PUBLIC", "VIDEO_AGE_MINUTES"),
        calculation_description="累计评论数除以发布后经过天数",
        calculation_expression=(
            "SAFE_DIVIDE(VIDEO_COMMENTS_PUBLIC, MAX(VIDEO_AGE_MINUTES / 1440, 1))"
        ),
        boundary_rules="发布不足一天按一天；依赖为空则为空",
        null_policy="propagate",
        output_precision=2,
        implementation_module="latest_video_tracker",
        implementation_status="specified",
        rule_version="1",
    )

    assert item.source_type == "system_calculated"
    assert not item.is_api_field
    assert item.dependency_field_ids == (
        "VIDEO_COMMENTS_PUBLIC",
        "VIDEO_AGE_MINUTES",
    )
    assert item.to_repository_dict()["official_field"] == ""
    assert item.to_repository_dict()["extra"]["calculation_mode"] == "safe_expression"


def test_api_field_still_requires_official_field() -> None:
    with pytest.raises(ValidationError, match="official_field"):
        FieldDefinition(
            standard_field_id="VIDEO_TEST_FIELD",
            cn_name="测试字段",
            api_source="data_api",
            official_field=None,
            data_type="string",
            entity_level="video",
            query_group="video_test",
            role="resource",
        )


def test_formula_source_and_calculation_mode_must_match() -> None:
    with pytest.raises(ValidationError, match="飞书公式字段"):
        FieldDefinition(
            standard_field_id="VIDEO_FORMULA_TEST",
            cn_name="公式测试",
            api_source="feishu_formula",
            data_type="number",
            entity_level="video",
            query_group="video_formula",
            role="formula",
            calculation_mode="none",
        )


def test_feishu_overlay_can_describe_planned_system_field() -> None:
    catalog = FieldCatalog.load_builtin().overlay_feishu_records(
        [
            {
                "fields": {
                    "标准字段ID": "VIDEO_COMMENTS_PER_DAY",
                    "中文名称": "视频日均评论数",
                    "API来源": "非API（系统计算）",
                    "官方字段": "",
                    "数据类型": "number",
                    "单位": "条/天",
                    "数据层级": "video",
                    "查询组": "video_calculated",
                    "计算位置": "通用计算引擎",
                    "依赖标准字段": "VIDEO_COMMENTS_PUBLIC, VIDEO_AGE_MINUTES",
                    "计算说明": "累计评论数除以发布后经过天数",
                    "机器计算表达式": (
                        "SAFE_DIVIDE(VIDEO_COMMENTS_PUBLIC, "
                        "MAX(VIDEO_AGE_MINUTES / 1440, 1))"
                    ),
                    "边界与空值规则": "发布不足一天按一天",
                    "空值策略": "任一依赖为空则为空",
                    "输出精度": 2,
                    "实现模块": "latest_video_tracker",
                    "当前可用状态": "底层未实现",
                    "规则版本": "1",
                    "启用": True,
                }
            }
        ]
    )

    item = catalog.get("VIDEO_COMMENTS_PER_DAY")
    assert len(catalog.document.fields) == 205
    assert item.api_source == "system_calculated"
    assert item.role == "computed"
    assert item.calculation_mode == "safe_expression"
    assert item.implementation_status == "specified"
    assert item.output_precision == 2


def test_feishu_overlay_accepts_new_and_legacy_availability_labels() -> None:
    base = FieldCatalog.load_builtin()
    common = {
        "中文名称": "视频标题",
        "API来源": "YouTube Data API",
        "官方字段": "snippet.title",
        "数据类型": "string",
        "数据层级": "video",
        "查询组": "video_snippet",
        "启用": True,
    }
    current = base.overlay_feishu_records(
        [
            {
                "fields": {
                    "标准字段ID": "VIDEO_TITLE",
                    **common,
                    "当前可用状态": "可直接使用",
                }
            }
        ]
    )
    legacy = base.overlay_feishu_records(
        [
            {
                "fields": {
                    "标准字段ID": "VIDEO_TITLE",
                    **common,
                    "实现状态": "已接入",
                }
            }
        ]
    )

    assert current.get("VIDEO_TITLE").is_directly_usable is True
    assert current.get("VIDEO_TITLE").current_availability_cn == "可直接使用"
    assert legacy.get("VIDEO_TITLE").is_directly_usable is True
