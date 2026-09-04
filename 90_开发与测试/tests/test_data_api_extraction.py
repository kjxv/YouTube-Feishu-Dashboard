from __future__ import annotations

from datetime import UTC, datetime

import pytest
from youtube_feishu_dashboard.api.youtube.extraction import DataApiScalarExtractor
from youtube_feishu_dashboard.catalog.field_catalog import FieldCatalog
from youtube_feishu_dashboard.core.errors import FieldCatalogError


def test_compiles_parts_and_extracts_nested_typed_scalar_fields() -> None:
    extractor = DataApiScalarExtractor.compile(
        FieldCatalog.load_builtin(),
        [
            "VIDEO_ID",
            "VIDEO_THUMBNAIL_URL",
            "VIDEO_VIEWS_PUBLIC",
            "VIDEO_CAPTION_AVAILABLE",
            "VIDEO_PUBLISHED_AT",
            "VIDEO_DURATION",
        ],
        entity_level="video",
    )

    result = extractor.extract(
        {
            "id": "video-1",
            "snippet": {
                "publishedAt": "2026-09-02T01:02:03Z",
                "thumbnails": {"high": {"url": "https://example.test/thumb.jpg"}},
            },
            "contentDetails": {"caption": "true", "duration": "P1DT1H2M3S"},
            "statistics": {"viewCount": "123"},
        }
    )

    assert extractor.required_parts == ("contentDetails", "id", "snippet", "statistics")
    assert result.values == {
        "VIDEO_ID": "video-1",
        "VIDEO_THUMBNAIL_URL": "https://example.test/thumb.jpg",
        "VIDEO_VIEWS_PUBLIC": 123,
        "VIDEO_CAPTION_AVAILABLE": True,
        "VIDEO_PUBLISHED_AT": datetime(2026, 9, 2, 1, 2, 3, tzinfo=UTC),
        "VIDEO_DURATION": 90123,
    }
    assert result.missing_field_ids == ()
    assert result.null_field_ids == ()


def test_extracts_new_catalog_scalar_without_field_specific_code() -> None:
    extractor = DataApiScalarExtractor.compile(
        FieldCatalog.load_builtin(),
        ["VIDEO_DEFAULT_LANGUAGE", "VIDEO_DEFAULT_AUDIO_LANGUAGE"],
        entity_level="video",
    )

    result = extractor.extract(
        {
            "snippet": {
                "defaultLanguage": "zh-Hans",
                "defaultAudioLanguage": "zh",
            }
        }
    )

    assert result.values == {
        "VIDEO_DEFAULT_LANGUAGE": "zh-Hans",
        "VIDEO_DEFAULT_AUDIO_LANGUAGE": "zh",
    }


def test_reports_missing_path_separately_from_explicit_null() -> None:
    extractor = DataApiScalarExtractor.compile(
        FieldCatalog.load_builtin(),
        ["VIDEO_DEFAULT_LANGUAGE", "VIDEO_DEFAULT_AUDIO_LANGUAGE"],
        entity_level="video",
    )

    result = extractor.extract({"snippet": {"defaultAudioLanguage": None}})

    assert result.values == {
        "VIDEO_DEFAULT_LANGUAGE": None,
        "VIDEO_DEFAULT_AUDIO_LANGUAGE": None,
    }
    assert result.missing_field_ids == ("VIDEO_DEFAULT_LANGUAGE",)
    assert result.null_field_ids == ("VIDEO_DEFAULT_AUDIO_LANGUAGE",)


@pytest.mark.parametrize(
    ("field_ids", "entity_level", "expected"),
    [
        (["VIDEO_TAGS"], "video", "不是普通单值类型"),
        (["ANALYTICS_VIEWS"], "video", "不是 Data API"),
        (["CHANNEL_TITLE"], "video", "不适用于 video 资源"),
    ],
)
def test_rejects_fields_outside_ordinary_data_api_scalar_scope(
    field_ids: list[str], entity_level: str, expected: str
) -> None:
    with pytest.raises(FieldCatalogError, match=expected):
        DataApiScalarExtractor.compile(
            FieldCatalog.load_builtin(), field_ids, entity_level=entity_level
        )


def test_invalid_source_value_names_the_standard_field() -> None:
    extractor = DataApiScalarExtractor.compile(
        FieldCatalog.load_builtin(), ["VIDEO_VIEWS_PUBLIC"], entity_level="video"
    )

    with pytest.raises(FieldCatalogError, match="VIDEO_VIEWS_PUBLIC"):
        extractor.extract({"statistics": {"viewCount": "not-a-number"}})


def test_wrong_but_well_formed_official_path_is_reported_as_missing_at_runtime() -> None:
    catalog = FieldCatalog.load_builtin().overlay_feishu_records(
        [
            {
                "fields": {
                    "标准字段ID": "VIDEO_TEST_WRONG_PATH",
                    "中文名称": "测试错误路径",
                    "API来源": "YouTube Data API",
                    "官方字段": "snippet.pathThatDoesNotExist",
                    "数据类型": "string",
                    "数据层级": "video",
                    "查询组": "video_snippet",
                    "启用": True,
                }
            }
        ]
    )
    extractor = DataApiScalarExtractor.compile(
        catalog, ["VIDEO_TEST_WRONG_PATH"], entity_level="video"
    )

    result = extractor.extract({"snippet": {"title": "测试"}})

    assert result.values == {"VIDEO_TEST_WRONG_PATH": None}
    assert result.missing_field_ids == ("VIDEO_TEST_WRONG_PATH",)


def test_rejects_malformed_official_path_before_api_request() -> None:
    catalog = FieldCatalog.load_builtin().overlay_feishu_records(
        [
            {
                "fields": {
                    "标准字段ID": "VIDEO_TEST_BAD_PATH",
                    "中文名称": "测试坏路径",
                    "API来源": "YouTube Data API",
                    "官方字段": "snippet..title",
                    "数据类型": "string",
                    "数据层级": "video",
                    "查询组": "video_snippet",
                    "启用": True,
                }
            }
        ]
    )

    with pytest.raises(FieldCatalogError, match="官方字段路径无效"):
        DataApiScalarExtractor.compile(
            catalog, ["VIDEO_TEST_BAD_PATH"], entity_level="video"
        )
