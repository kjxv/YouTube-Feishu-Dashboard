from __future__ import annotations

import pytest
from youtube_feishu_dashboard.catalog.field_catalog import FieldCatalog
from youtube_feishu_dashboard.core.errors import ConfigurationError
from youtube_feishu_dashboard.services.calculated_fields import CalculatedFieldEngine


def _record(
    field_id: str,
    expression: str,
    dependencies: str,
    *,
    data_type: str = "number",
    null_policy: str = "任一依赖为空则为空",
) -> dict[str, object]:
    return {
        "fields": {
            "标准字段ID": field_id,
            "中文名称": field_id,
            "API来源": "非API（系统计算）",
            "官方字段": "",
            "数据类型": data_type,
            "单位": "",
            "数据层级": "video",
            "查询组": "video_calculated",
            "计算位置": "通用计算引擎",
            "依赖标准字段": dependencies,
            "计算说明": "测试计算规则",
            "机器计算表达式": expression,
            "边界与空值规则": "按表达式和空值策略执行",
            "空值策略": null_policy,
            "输出精度": 2,
            "实现模块": "latest_video_tracker",
            "实现状态": "已接入",
            "规则版本": "1",
            "启用": True,
        }
    }


def test_safe_expression_uses_api_and_registered_module_inputs() -> None:
    catalog = FieldCatalog.load_builtin().overlay_feishu_records(
        [
            _record(
                "VIDEO_COMMENTS_PER_DAY",
                "SAFE_DIVIDE(VIDEO_COMMENTS_PUBLIC, MAX(VIDEO_AGE_MINUTES / 1440, 1))",
                "VIDEO_COMMENTS_PUBLIC, VIDEO_AGE_MINUTES",
            )
        ]
    )

    engine = CalculatedFieldEngine.compile(
        catalog,
        ["VIDEO_COMMENTS_PER_DAY"],
        module_code_field_ids={"VIDEO_AGE_MINUTES"},
    )

    assert engine.required_api_field_ids == ("VIDEO_COMMENTS_PUBLIC",)
    assert engine.required_module_field_ids == ("VIDEO_AGE_MINUTES",)
    assert engine.evaluate(
        {"VIDEO_COMMENTS_PUBLIC": 9, "VIDEO_AGE_MINUTES": 2880}
    )["VIDEO_COMMENTS_PER_DAY"] == 4.5
    assert engine.evaluate(
        {"VIDEO_COMMENTS_PUBLIC": None, "VIDEO_AGE_MINUTES": 2880}
    )["VIDEO_COMMENTS_PER_DAY"] is None


def test_safe_expression_resolves_nested_calculations_in_dependency_order() -> None:
    catalog = FieldCatalog.load_builtin().overlay_feishu_records(
        [
            _record(
                "VIDEO_PUBLIC_INTERACTIONS",
                "VIDEO_LIKES_PUBLIC + VIDEO_COMMENTS_PUBLIC",
                "VIDEO_LIKES_PUBLIC, VIDEO_COMMENTS_PUBLIC",
            ),
            _record(
                "VIDEO_INTERACTION_RATE",
                "SAFE_DIVIDE(VIDEO_PUBLIC_INTERACTIONS, VIDEO_VIEWS_PUBLIC, 0)",
                "VIDEO_PUBLIC_INTERACTIONS, VIDEO_VIEWS_PUBLIC",
            ),
        ]
    )

    engine = CalculatedFieldEngine.compile(catalog, ["VIDEO_INTERACTION_RATE"])
    result = engine.evaluate(
        {
            "VIDEO_LIKES_PUBLIC": 8,
            "VIDEO_COMMENTS_PUBLIC": 2,
            "VIDEO_VIEWS_PUBLIC": 100,
        }
    )

    assert engine.calculated_field_ids == (
        "VIDEO_PUBLIC_INTERACTIONS",
        "VIDEO_INTERACTION_RATE",
    )
    assert set(engine.required_api_field_ids) == {
        "VIDEO_LIKES_PUBLIC",
        "VIDEO_COMMENTS_PUBLIC",
        "VIDEO_VIEWS_PUBLIC",
    }
    assert result["VIDEO_PUBLIC_INTERACTIONS"] == 10
    assert result["VIDEO_INTERACTION_RATE"] == 0.1


def test_safe_expression_rejects_arbitrary_python_and_undeclared_dependencies() -> None:
    unsafe_catalog = FieldCatalog.load_builtin().overlay_feishu_records(
        [_record("VIDEO_UNSAFE", "__import__('os').system('echo')", "VIDEO_ID")]
    )
    with pytest.raises(ConfigurationError, match="禁止的语法|未允许的函数"):
        CalculatedFieldEngine.compile(unsafe_catalog, ["VIDEO_UNSAFE"])

    undeclared_catalog = FieldCatalog.load_builtin().overlay_feishu_records(
        [_record("VIDEO_UNDECLARED", "VIDEO_VIEWS_PUBLIC + 1", "VIDEO_LIKES_PUBLIC")]
    )
    with pytest.raises(ConfigurationError, match="未声明依赖.*VIDEO_VIEWS_PUBLIC"):
        CalculatedFieldEngine.compile(undeclared_catalog, ["VIDEO_UNDECLARED"])


def test_safe_expression_rejects_cycles_and_unregistered_module_inputs() -> None:
    cyclic_catalog = FieldCatalog.load_builtin().overlay_feishu_records(
        [
            _record("VIDEO_CALC_A", "VIDEO_CALC_B + 1", "VIDEO_CALC_B"),
            _record("VIDEO_CALC_B", "VIDEO_CALC_A + 1", "VIDEO_CALC_A"),
        ]
    )
    with pytest.raises(ConfigurationError, match="循环依赖"):
        CalculatedFieldEngine.compile(cyclic_catalog, ["VIDEO_CALC_A"])

    module_input_catalog = FieldCatalog.load_builtin().overlay_feishu_records(
        [_record("VIDEO_AGE_DAYS", "VIDEO_AGE_MINUTES / 1440", "VIDEO_AGE_MINUTES")]
    )
    with pytest.raises(ConfigurationError, match="没有在 output_field_ids 中登记"):
        CalculatedFieldEngine.compile(module_input_catalog, ["VIDEO_AGE_DAYS"])
