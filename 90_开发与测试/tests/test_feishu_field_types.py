from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from youtube_feishu_dashboard.api.feishu.field_types import (
    FeishuFieldSchema,
    FeishuTableSchema,
    FeishuValueAdapterRegistry,
)
from youtube_feishu_dashboard.core.errors import ConfigurationError


def _field(name: str, type_code: int) -> FeishuFieldSchema:
    return FeishuFieldSchema(
        field_id=f"fld-{type_code}-{name}",
        field_name=name,
        type_code=type_code,
        property={},
    )


def test_builds_live_schema_indexes_with_type_and_property() -> None:
    schema = FeishuTableSchema.from_api_fields(
        [
            {
                "field_id": "fld-title",
                "field_name": "视频标题",
                "type": 1,
                "is_primary": True,
            },
            {
                "field_id": "fld-link",
                "field_name": "视频缩略图",
                "type": "15",
                "property": {"multiple": False},
            },
        ]
    )

    assert schema.require_name("视频缩略图").field_id == "fld-link"
    assert schema.require_name("视频缩略图").type_name == "超链接"
    assert schema.require_name("视频缩略图").property == {"multiple": False}
    assert schema.require_id("fld-title").is_primary is True


def test_adapts_common_feishu_field_types() -> None:
    adapters = FeishuValueAdapterRegistry()

    assert adapters.adapt(_field("文本", 1), 123) == "123"
    assert adapters.adapt(_field("数字", 2), "123.5") == 123.5
    assert adapters.adapt(_field("单选", 3), "Shorts") == "Shorts"
    assert adapters.adapt(_field("多选", 4), ["中文", "教程"]) == ["中文", "教程"]
    assert adapters.adapt(_field("复选框", 7), "false") is False
    assert adapters.adapt(_field("日期", 5), date(2026, 9, 2)) == 1788307200000
    assert adapters.adapt(
        _field("时间", 5), datetime(2026, 9, 2, 1, 2, 3, tzinfo=UTC)
    ) == 1788310923000
    assert adapters.adapt(_field("空值", 1), None) is None


def test_url_type_uses_feishu_hyperlink_payload_without_business_field_branch() -> None:
    adapters = FeishuValueAdapterRegistry()
    url_field = _field("视频缩略图", 15)

    assert adapters.adapt(url_field, "https://example.test/thumb.jpg") == {
        "link": "https://example.test/thumb.jpg",
        "text": "https://example.test/thumb.jpg",
    }
    assert adapters.adapt(
        url_field,
        {"url": "https://example.test/thumb.jpg", "text": "查看缩略图"},
    ) == {
        "link": "https://example.test/thumb.jpg",
        "text": "查看缩略图",
    }


def test_adapts_whole_record_by_live_column_names() -> None:
    schema = FeishuTableSchema.from_api_fields(
        [
            {"field_id": "fld-title", "field_name": "视频标题", "type": 1},
            {"field_id": "fld-link", "field_name": "视频缩略图", "type": 15},
        ]
    )

    result = FeishuValueAdapterRegistry().adapt_record(
        schema,
        {
            "视频标题": "测试视频",
            "视频缩略图": "https://example.test/thumb.jpg",
        },
    )

    assert result["视频标题"] == "测试视频"
    assert result["视频缩略图"] == {
        "link": "https://example.test/thumb.jpg",
        "text": "https://example.test/thumb.jpg",
    }


def test_rejects_missing_columns_unsupported_types_and_invalid_values() -> None:
    schema = FeishuTableSchema.from_api_fields(
        [{"field_id": "fld-attachment", "field_name": "附件", "type": 17}]
    )
    adapters = FeishuValueAdapterRegistry()

    with pytest.raises(ConfigurationError, match="不存在映射列"):
        schema.require_name("不存在")
    with pytest.raises(ConfigurationError, match="当前通用写入器不支持"):
        adapters.adapt(schema.require_name("附件"), "file-token")
    with pytest.raises(ConfigurationError, match="无法转换为飞书超链接"):
        adapters.adapt(_field("网址", 15), "not-a-url")


def test_rejects_invalid_or_duplicate_live_schema() -> None:
    with pytest.raises(ConfigurationError, match="缺少 field_id"):
        FeishuTableSchema.from_api_fields([{"field_name": "标题", "type": 1}])
    with pytest.raises(ConfigurationError, match="重复字段名"):
        FeishuTableSchema.from_api_fields(
            [
                {"field_id": "fld-1", "field_name": "标题", "type": 1},
                {"field_id": "fld-2", "field_name": "标题", "type": 1},
            ]
        )
    with pytest.raises(ConfigurationError, match="重复字段 ID"):
        FeishuTableSchema.from_api_fields(
            [
                {"field_id": "fld-1", "field_name": "标题", "type": 1},
                {"field_id": "fld-1", "field_name": "描述", "type": 1},
            ]
        )
    with pytest.raises(ConfigurationError, match="类型编号无效"):
        FeishuTableSchema.from_api_fields(
            [{"field_id": "fld-1", "field_name": "标题", "type": "text"}]
        )
    with pytest.raises(ConfigurationError, match="property 不是对象"):
        FeishuTableSchema.from_api_fields(
            [
                {
                    "field_id": "fld-1",
                    "field_name": "标题",
                    "type": 1,
                    "property": "invalid",
                }
            ]
        )


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        (_field("数字", 2), True, "无法转换为飞书数字"),
        (_field("日期", 5), "2026-09-02 12:00:00", "无法转换为飞书日期时间"),
        (_field("复选框", 7), "yes", "无法转换为飞书复选框"),
        (_field("多选", 4), {"bad": "value"}, "无法转换为飞书多选"),
    ],
)
def test_invalid_values_are_rejected_with_target_context(
    field: FeishuFieldSchema, value: object, expected: str
) -> None:
    with pytest.raises(ConfigurationError, match=expected):
        FeishuValueAdapterRegistry().adapt(field, value)
