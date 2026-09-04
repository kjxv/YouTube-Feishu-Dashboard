"""手动指定单个或多个 YouTube 追踪视频的配置解析。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from youtube_feishu_dashboard.core.errors import ConfigurationError

TRACKING_VIDEO_IDS_KEY = "tracking_video_ids"
MAX_TRACKING_VIDEO_IDS = 10

_SEPARATOR_PATTERN = re.compile(r"[\r\n,，;；]+")
_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")


@dataclass(frozen=True, slots=True)
class TrackingVideoSelection:
    """经过规范化、可安全交给后续批量预检查的视频选择。"""

    video_ids: tuple[str, ...]
    duplicate_video_ids: tuple[str, ...]
    submitted_count: int


def load_tracking_video_selection(
    account_config: Mapping[str, Any],
    *,
    max_video_ids: int = MAX_TRACKING_VIDEO_IDS,
) -> TrackingVideoSelection:
    """从账号非敏感配置读取手动追踪列表；缺失时禁止回退到最新视频。"""

    return parse_tracking_video_ids(
        account_config.get(TRACKING_VIDEO_IDS_KEY),
        max_video_ids=max_video_ids,
    )


def inspect_tracking_video_selection(
    account_config: Mapping[str, Any],
) -> dict[str, Any]:
    """只检查手动视频配置；不会请求 YouTube 或写入业务数据。"""

    try:
        selection = load_tracking_video_selection(account_config)
    except ConfigurationError as exc:
        return {
            "ready": False,
            "config_key": TRACKING_VIDEO_IDS_KEY,
            "error": str(exc),
            "online_video_access_check": "deferred_to_script_8_or_formal_sync",
        }
    return {
        "ready": True,
        "config_key": TRACKING_VIDEO_IDS_KEY,
        "video_ids": list(selection.video_ids),
        "video_count": len(selection.video_ids),
        "submitted_count": selection.submitted_count,
        "duplicate_video_ids_removed": list(selection.duplicate_video_ids),
        "online_video_access_check": "deferred_to_script_8_or_formal_sync",
    }


def parse_tracking_video_ids(
    value: Any,
    *,
    max_video_ids: int = MAX_TRACKING_VIDEO_IDS,
) -> TrackingVideoSelection:
    """解析换行或中英文逗号、分号分隔的 YouTube 视频 ID。"""

    if max_video_ids < 1:
        raise ValueError("max_video_ids 必须大于 0")

    raw_text = "" if value is None else str(value)
    submitted = tuple(
        item.strip() for item in _SEPARATOR_PATTERN.split(raw_text) if item.strip()
    )
    if not submitted:
        raise ConfigurationError(
            "账号非敏感配置缺少“当前追踪视频ID列表”（tracking_video_ids）；"
            "手动模式不会自动改为追踪最新视频。"
        )

    invalid = tuple(item for item in submitted if not _VIDEO_ID_PATTERN.fullmatch(item))
    if invalid:
        raise ConfigurationError(
            "当前追踪视频ID格式无效："
            + "、".join(dict.fromkeys(invalid))
            + "。每个 YouTube 视频 ID 必须为 11 位，且只能包含字母、数字、下划线或连字符。"
        )

    video_ids: list[str] = []
    seen: set[str] = set()
    duplicate_video_ids: list[str] = []
    duplicate_seen: set[str] = set()
    for video_id in submitted:
        if video_id in seen:
            if video_id not in duplicate_seen:
                duplicate_video_ids.append(video_id)
                duplicate_seen.add(video_id)
            continue
        video_ids.append(video_id)
        seen.add(video_id)

    if len(video_ids) > max_video_ids:
        raise ConfigurationError(
            f"“当前追踪视频ID列表”包含 {len(video_ids)} 个不同视频，"
            f"第一版最多同时追踪 {max_video_ids} 个；请删除暂不追踪的视频 ID。"
        )

    return TrackingVideoSelection(
        video_ids=tuple(video_ids),
        duplicate_video_ids=tuple(duplicate_video_ids),
        submitted_count=len(submitted),
    )
