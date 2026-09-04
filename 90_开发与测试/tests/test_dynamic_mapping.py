from __future__ import annotations

import pytest
from youtube_feishu_dashboard.api.feishu.config_center import ModuleFieldMapping
from youtube_feishu_dashboard.catalog.field_catalog import FieldCatalog
from youtube_feishu_dashboard.core.errors import ConfigurationError, FieldCatalogError
from youtube_feishu_dashboard.services.dynamic_mapping import (
    DynamicModulePlan,
    DynamicModulePlanCompiler,
)


def _mapping(
    field_id: str,
    column: str,
    *,
    table_id: str | None = "tbl-main",
    enabled: bool = True,
) -> ModuleFieldMapping:
    return ModuleFieldMapping(
        module_id="example",
        standard_field_id=field_id,
        feishu_column=column,
        target_table_id=table_id,
        enabled=enabled,
    )


def _compile(
    mappings: tuple[ModuleFieldMapping, ...],
    fields: list[dict[str, object]],
    *,
    table_ids: dict[str, str] | None = None,
    catalog: FieldCatalog | None = None,
) -> DynamicModulePlan:
    selected_table_ids = table_ids or {"主表": "tbl-main"}
    return DynamicModulePlanCompiler(
        catalog=catalog or FieldCatalog.load_builtin(),
        computed_field_ids=set(),
        required_data_field_ids=("VIDEO_ID",),
    ).compile(
        module_id="example",
        entity_level="video",
        table_ids=selected_table_ids,
        mappings=mappings,
        raw_fields_by_table_id={
            table_id: fields for table_id in set(selected_table_ids.values())
        },
    )


def test_feishu_catalog_overlay_allows_new_ordinary_data_api_scalar() -> None:
    catalog = FieldCatalog.load_builtin().overlay_feishu_records(
        [
            {
                "fields": {
                    "标准字段ID": "VIDEO_EXPERIMENT_LANGUAGE",
                    "中文名称": "实验语言字段",
                    "API来源": "YouTube Data API",
                    "官方字段": "snippet.defaultLanguage",
                    "数据类型": "string",
                    "数据层级": "video",
                    "查询组": "video_snippet",
                    "启用": True,
                }
            }
        ]
    )

    definition = catalog.get("VIDEO_EXPERIMENT_LANGUAGE")
    assert definition.default_part == "snippet"
    assert definition.required_scopes == ("youtube.readonly",)
    assert len(catalog.document.fields) == 167


def test_feishu_catalog_overlay_rejects_duplicate_remote_ids() -> None:
    records = [
        {"fields": {"标准字段ID": "VIDEO_TITLE"}},
        {"fields": {"标准字段ID": "VIDEO_TITLE"}},
    ]

    with pytest.raises(FieldCatalogError, match="重复的标准字段ID.*VIDEO_TITLE"):
        FieldCatalog.load_builtin().overlay_feishu_records(records)


def test_feishu_catalog_overlay_rejects_incomplete_and_new_non_data_fields() -> None:
    with pytest.raises(FieldCatalogError, match="缺少或无法识别.*官方字段"):
        FieldCatalog.load_builtin().overlay_feishu_records(
            [
                {
                    "fields": {
                        "标准字段ID": "VIDEO_TITLE",
                        "中文名称": "标题",
                        "API来源": "YouTube Data API",
                        "数据类型": "string",
                        "数据层级": "video",
                        "查询组": "video_snippet",
                    }
                }
            ]
        )

    with pytest.raises(FieldCatalogError, match="需要专用查询模板"):
        FieldCatalog.load_builtin().overlay_feishu_records(
            [
                {
                    "fields": {
                        "标准字段ID": "ANALYTICS_NEW_METRIC",
                        "中文名称": "新指标",
                        "API来源": "YouTube Analytics API",
                        "官方字段": "newMetric",
                        "数据类型": "number",
                        "数据层级": "video",
                        "查询组": "analytics_core",
                    }
                }
            ]
        )


def test_dynamic_plan_reports_live_target_types_and_adapts_by_mapping() -> None:
    table_ids = {"主表": "tbl-main"}
    mappings = (
        ModuleFieldMapping(
            module_id="example",
            standard_field_id="VIDEO_THUMBNAIL_URL",
            feishu_column="缩略图",
            target_table_id="tbl-main",
        ),
    )
    plan = DynamicModulePlanCompiler(
        catalog=FieldCatalog.load_builtin(),
        computed_field_ids=set(),
        required_data_field_ids=("VIDEO_ID",),
    ).compile(
        module_id="example",
        entity_level="video",
        table_ids=table_ids,
        mappings=mappings,
        raw_fields_by_table_id={
            "tbl-main": [
                {
                    "field_id": "fld-thumb",
                    "field_name": "缩略图",
                    "type": 15,
                }
            ]
        },
    )

    report = plan.as_report()
    mapping = report["tables"]["主表"]["mappings"][0]
    assert mapping["official_field"] == "snippet.thumbnails.high.url"
    assert mapping["feishu_type_name"] == "超链接"
    assert "VIDEO_THUMBNAIL_URL" in plan.request_plan.field_ids
    assert plan.adapt_table_record(
        "主表", {"VIDEO_THUMBNAIL_URL": "https://example.test/thumb.jpg"}
    ) == {
        "缩略图": {
            "link": "https://example.test/thumb.jpg",
            "text": "https://example.test/thumb.jpg",
        }
    }


def test_dynamic_plan_separates_data_and_analytics_requests() -> None:
    plan = DynamicModulePlanCompiler(
        catalog=FieldCatalog.load_builtin(),
        computed_field_ids=set(),
        required_data_field_ids=("VIDEO_ID",),
        supported_api_sources={"data_api", "analytics_api"},
    ).compile(
        module_id="example",
        entity_level="video",
        table_ids={"主表": "tbl-main"},
        mappings=(_mapping("ANALYTICS_ENGAGED_VIEWS", "互动观看量"),),
        raw_fields_by_table_id={
            "tbl-main": [
                {"field_id": "fld-engaged", "field_name": "互动观看量", "type": 2}
            ]
        },
    )

    assert plan.data_api_field_ids == ("VIDEO_ID",)
    assert plan.analytics_field_ids == ("ANALYTICS_ENGAGED_VIEWS",)
    assert plan.reporting_field_ids == ()
    assert plan.request_plan.analytics_metrics == ("engagedViews",)
    assert tuple(field.standard_field_id for field in plan.extractor.fields) == ("VIDEO_ID",)
    assert plan.as_report()["analytics_field_ids"] == ["ANALYTICS_ENGAGED_VIEWS"]


def test_dynamic_plan_accepts_only_reach_columns_from_reporting_api() -> None:
    compiler = DynamicModulePlanCompiler(
        catalog=FieldCatalog.load_builtin(),
        computed_field_ids=set(),
        required_data_field_ids=("VIDEO_ID",),
        supported_api_sources={"data_api", "reporting_api"},
    )
    plan = compiler.compile(
        module_id="example",
        entity_level="video",
        table_ids={"主表": "tbl-main"},
        mappings=(_mapping("ANALYTICS_IMPRESSIONS", "展示次数"),),
        raw_fields_by_table_id={
            "tbl-main": [
                {"field_id": "fld-impressions", "field_name": "展示次数", "type": 2}
            ]
        },
    )

    assert plan.reporting_field_ids == ("ANALYTICS_IMPRESSIONS",)
    assert plan.request_plan.reporting_columns == ("video_thumbnail_impressions",)

    with pytest.raises(FieldCatalogError, match="不属于 Reach Basic"):
        compiler.compile(
            module_id="example",
            entity_level="video",
            table_ids={"主表": "tbl-main"},
            mappings=(_mapping("REPORT_VIEWS", "播放量"),),
            raw_fields_by_table_id={
                "tbl-main": [
                    {"field_id": "fld-views", "field_name": "播放量", "type": 2}
                ]
            },
        )


def test_dynamic_plan_auto_fetches_inputs_and_writes_safe_calculated_field() -> None:
    catalog = FieldCatalog.load_builtin().overlay_feishu_records(
        [
            {
                "fields": {
                    "标准字段ID": "VIDEO_PUBLIC_INTERACTIONS",
                    "中文名称": "公开互动数",
                    "API来源": "非API（系统计算）",
                    "官方字段": "",
                    "数据类型": "integer",
                    "数据层级": "video",
                    "查询组": "video_calculated",
                    "计算位置": "通用计算引擎",
                    "依赖标准字段": "VIDEO_LIKES_PUBLIC, VIDEO_COMMENTS_PUBLIC",
                    "计算说明": "点赞数加评论数",
                    "机器计算表达式": "VIDEO_LIKES_PUBLIC + VIDEO_COMMENTS_PUBLIC",
                    "空值策略": "空值按0",
                    "实现模块": "example",
                    "实现状态": "已接入",
                    "启用": True,
                }
            }
        ]
    )
    plan = DynamicModulePlanCompiler(
        catalog=catalog,
        computed_field_ids=set(),
        required_data_field_ids=("VIDEO_ID",),
    ).compile(
        module_id="example",
        entity_level="video",
        table_ids={"主表": "tbl-main"},
        mappings=(_mapping("VIDEO_PUBLIC_INTERACTIONS", "公开互动数"),),
        raw_fields_by_table_id={
            "tbl-main": [
                {"field_id": "fld-interactions", "field_name": "公开互动数", "type": 2}
            ]
        },
    )

    assert set(plan.request_plan.field_ids) == {
        "VIDEO_ID",
        "VIDEO_LIKES_PUBLIC",
        "VIDEO_COMMENTS_PUBLIC",
    }
    assert plan.as_report()["tables"]["主表"]["safe_calculated_field_ids"] == [
        "VIDEO_PUBLIC_INTERACTIONS"
    ]
    assert plan.adapt_table_record(
        "主表", {"VIDEO_LIKES_PUBLIC": 8, "VIDEO_COMMENTS_PUBLIC": 2}
    ) == {"公开互动数": 10}


def test_dynamic_plan_extracts_multiple_fields_and_omits_explicit_null_on_write() -> None:
    plan = _compile(
        (
            _mapping("VIDEO_TITLE", "视频标题"),
            _mapping("VIDEO_THUMBNAIL_URL", "视频缩略图"),
        ),
        [
            {"field_id": "fld-title", "field_name": "视频标题", "type": 1},
            {"field_id": "fld-thumb", "field_name": "视频缩略图", "type": 15},
        ],
    )

    extracted = plan.extractor.extract(
        {
            "id": "video-1",
            "snippet": {"title": "测试视频", "thumbnails": {"high": {"url": None}}},
        }
    )

    assert extracted.null_field_ids == ("VIDEO_THUMBNAIL_URL",)
    assert plan.adapt_table_record("主表", extracted.values) == {"视频标题": "测试视频"}


@pytest.mark.parametrize(
    ("mappings", "fields", "expected"),
    [
        (
            (_mapping("VIDEO_TITLE", "标题", table_id=None),),
            [{"field_id": "fld-title", "field_name": "标题", "type": 1}],
            "缺少目标表ID",
        ),
        (
            (_mapping("VIDEO_TITLE", "标题", table_id="tbl-other"),),
            [{"field_id": "fld-title", "field_name": "标题", "type": 1}],
            "指向未配置的业务表",
        ),
        (
            (_mapping("VIDEO_TITLE", "不存在"),),
            [{"field_id": "fld-title", "field_name": "标题", "type": 1}],
            "不存在映射列",
        ),
        (
            (_mapping("VIDEO_TITLE", "附件"),),
            [{"field_id": "fld-attachment", "field_name": "附件", "type": 17}],
            "当前通用写入器不支持",
        ),
        (
            (_mapping("VIDEO_VIEWS_PUBLIC", "发布日期"),),
            [{"field_id": "fld-date", "field_name": "发布日期", "type": 5}],
            "类型冲突.*integer.*日期时间",
        ),
    ],
)
def test_dynamic_plan_rejects_invalid_targets_before_runtime(
    mappings: tuple[ModuleFieldMapping, ...],
    fields: list[dict[str, object]],
    expected: str,
) -> None:
    with pytest.raises(ConfigurationError, match=expected):
        _compile(mappings, fields)


@pytest.mark.parametrize(
    ("mappings", "expected"),
    [
        (
            (_mapping("VIDEO_TITLE", "标题"), _mapping("VIDEO_TITLE", "备用标题")),
            "标准字段ID重复",
        ),
        (
            (_mapping("VIDEO_TITLE", "标题"), _mapping("VIDEO_DESCRIPTION", "标题")),
            "飞书列重复",
        ),
    ],
)
def test_dynamic_plan_rejects_duplicate_mappings(
    mappings: tuple[ModuleFieldMapping, ...], expected: str
) -> None:
    fields = [
        {"field_id": "fld-title", "field_name": "标题", "type": 1},
        {"field_id": "fld-alt", "field_name": "备用标题", "type": 1},
    ]
    with pytest.raises(ConfigurationError, match=expected):
        _compile(mappings, fields)


def test_dynamic_plan_rejects_duplicate_business_table_ids() -> None:
    with pytest.raises(ConfigurationError, match="同一个目标表ID"):
        _compile(
            (_mapping("VIDEO_TITLE", "标题"),),
            [{"field_id": "fld-title", "field_name": "标题", "type": 1}],
            table_ids={"主表": "tbl-main", "快照表": "tbl-main"},
        )


def test_dynamic_plan_rejects_unknown_and_disabled_standard_fields() -> None:
    fields = [{"field_id": "fld-value", "field_name": "值", "type": 1}]
    with pytest.raises(FieldCatalogError, match="未知标准字段"):
        _compile((_mapping("VIDEO_NOT_REAL", "值"),), fields)

    disabled_catalog = FieldCatalog.load_builtin().overlay_feishu_records(
        [
            {
                "fields": {
                    "标准字段ID": "VIDEO_TITLE",
                    "中文名称": "标题",
                    "API来源": "YouTube Data API",
                    "官方字段": "snippet.title",
                    "数据类型": "string",
                    "数据层级": "video",
                    "查询组": "video_snippet",
                    "启用": False,
                }
            }
        ]
    )
    with pytest.raises(FieldCatalogError, match="已停用字段"):
        _compile((_mapping("VIDEO_TITLE", "值"),), fields, catalog=disabled_catalog)
