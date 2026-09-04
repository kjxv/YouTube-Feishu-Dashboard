from __future__ import annotations

import pytest
from youtube_feishu_dashboard.core.errors import ConfigurationError
from youtube_feishu_dashboard.services.tracking_video_config import (
    MAX_TRACKING_VIDEO_IDS,
    inspect_tracking_video_selection,
    load_tracking_video_selection,
    parse_tracking_video_ids,
)


def test_parses_single_manual_video_id() -> None:
    selection = load_tracking_video_selection({"tracking_video_ids": " okAkZVRx7ac "})

    assert selection.video_ids == ("okAkZVRx7ac",)
    assert selection.duplicate_video_ids == ()
    assert selection.submitted_count == 1


def test_parses_multiple_separators_and_deduplicates_in_original_order() -> None:
    selection = parse_tracking_video_ids(
        "okAkZVRx7ac，dQw4w9WgXcQ\nabc_DEF-123；okAkZVRx7ac;dQw4w9WgXcQ"
    )

    assert selection.video_ids == ("okAkZVRx7ac", "dQw4w9WgXcQ", "abc_DEF-123")
    assert selection.duplicate_video_ids == ("okAkZVRx7ac", "dQw4w9WgXcQ")
    assert selection.submitted_count == 5


@pytest.mark.parametrize("value", [None, "", "  \n，；  "])
def test_empty_manual_selection_never_falls_back_to_latest(value: object) -> None:
    with pytest.raises(ConfigurationError, match="不会自动改为追踪最新视频"):
        parse_tracking_video_ids(value)


def test_rejects_all_invalid_ids_in_one_actionable_error() -> None:
    with pytest.raises(ConfigurationError, match="too-short.*invalid id!"):
        parse_tracking_video_ids("too-short, invalid id!")


def test_rejects_more_than_ten_distinct_video_ids_without_truncating() -> None:
    value = ",".join(f"vid{index:08d}" for index in range(MAX_TRACKING_VIDEO_IDS + 1))

    with pytest.raises(ConfigurationError, match="11 个不同视频.*最多同时追踪 10 个"):
        parse_tracking_video_ids(value)


def test_duplicates_do_not_consume_the_active_video_limit() -> None:
    unique_ids = [f"vid{index:08d}" for index in range(MAX_TRACKING_VIDEO_IDS)]
    selection = parse_tracking_video_ids(",".join([*unique_ids, unique_ids[0]]))

    assert selection.video_ids == tuple(unique_ids)
    assert selection.duplicate_video_ids == (unique_ids[0],)
    assert selection.submitted_count == MAX_TRACKING_VIDEO_IDS + 1


def test_read_only_inspection_reports_ids_count_and_duplicates() -> None:
    report = inspect_tracking_video_selection(
        {"tracking_video_ids": "okAkZVRx7ac,dQw4w9WgXcQ,okAkZVRx7ac"}
    )

    assert report == {
        "ready": True,
        "config_key": "tracking_video_ids",
        "video_ids": ["okAkZVRx7ac", "dQw4w9WgXcQ"],
        "video_count": 2,
        "submitted_count": 3,
        "duplicate_video_ids_removed": ["okAkZVRx7ac"],
        "online_video_access_check": "deferred_to_script_8_or_formal_sync",
    }


def test_read_only_inspection_reports_invalid_config_without_raising() -> None:
    report = inspect_tracking_video_selection({"tracking_video_ids": "bad"})

    assert report["ready"] is False
    assert report["config_key"] == "tracking_video_ids"
    assert "格式无效" in report["error"]
    assert report["online_video_access_check"] == "deferred_to_script_8_or_formal_sync"
