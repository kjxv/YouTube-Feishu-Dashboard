from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from youtube_feishu_dashboard.api.youtube.protocols import YouTubeAnalyticsGateway
from youtube_feishu_dashboard.api.youtube.schemas import AnalyticsTable, VideoResource
from youtube_feishu_dashboard.core.time import as_utc
from youtube_feishu_dashboard.services.api_time_fields import PACIFIC_TIMEZONE

LONG_FORM_CONTENT_TYPE = "video_on_demand"
SHORT_FORM_CONTENT_TYPE = "shorts"
LIVE_STREAM_CONTENT_TYPE = "live_stream"
ANALYTICS_CONTENT_START = date(2019, 1, 1)


@dataclass(frozen=True, slots=True)
class ContentClassification:
    long_video_ids: tuple[str, ...]
    skipped: dict[str, str]
    creator_types: dict[str, tuple[str, ...]]
    api_requests: int


@dataclass(frozen=True, slots=True)
class DailyAnalytics:
    overall: dict[date, dict[str, int]]
    long_views: dict[date, int]
    video_views: dict[tuple[str, date], int]
    estimated_revenue_last_28d_usd: float | None
    revenue_window_start_date: date
    revenue_window_end_date: date
    revenue_data_through_date: date | None
    fetched_at: datetime
    data_through_date: date
    api_requests: int


class ChannelAnalyticsCollector:
    def __init__(self, youtube: YouTubeAnalyticsGateway) -> None:
        self.youtube = youtube

    def classify_long_videos(
        self, videos: list[VideoResource], observed_at: datetime
    ) -> ContentClassification:
        end_date = as_utc(observed_at).astimezone(PACIFIC_TIMEZONE).date() - timedelta(days=1)
        types: dict[str, set[str]] = defaultdict(set)
        requests = 0
        candidates = [
            item
            for item in videos
            if item.privacy_status == "public"
            and str(item.raw.get("snippet", {}).get("liveBroadcastContent", "none")) == "none"
        ]
        for batch in _batches([item.video_id for item in candidates], 200):
            if end_date < ANALYTICS_CONTENT_START:
                break
            for table in self._paged_query(
                start_date=ANALYTICS_CONTENT_START,
                end_date=end_date,
                metrics=("views",),
                dimensions=("video", "creatorContentType"),
                filters="video==" + ",".join(batch),
            ):
                requests += 1
                for row in _rows(table):
                    video_id = str(row.get("video", ""))
                    content_type = _normalize_creator_content_type(
                        row.get("creatorContentType")
                    )
                    if video_id and content_type:
                        types[video_id].add(content_type)

        skipped: dict[str, str] = {}
        long_ids: list[str] = []
        candidate_ids = {item.video_id for item in candidates}
        for video in videos:
            if video.privacy_status != "public":
                skipped[video.video_id] = "not_public"
                continue
            if video.video_id not in candidate_ids:
                skipped[video.video_id] = "live_or_upcoming"
                continue
            creator_types = types.get(video.video_id, set())
            if creator_types == {LONG_FORM_CONTENT_TYPE}:
                long_ids.append(video.video_id)
            elif SHORT_FORM_CONTENT_TYPE in creator_types:
                skipped[video.video_id] = "shorts"
            elif LIVE_STREAM_CONTENT_TYPE in creator_types:
                skipped[video.video_id] = "live_stream"
            elif creator_types:
                skipped[video.video_id] = "mixed_or_unsupported_creator_content_type"
            else:
                skipped[video.video_id] = "creator_content_type_unconfirmed"
        return ContentClassification(
            long_video_ids=tuple(long_ids),
            skipped=skipped,
            creator_types={key: tuple(sorted(value)) for key, value in types.items()},
            api_requests=requests,
        )

    def collect_daily(
        self,
        *,
        long_video_ids: tuple[str, ...],
        observed_at: datetime,
        lookback_days: int,
        revenue_window_days: int = 28,
        include_revenue: bool = False,
    ) -> DailyAnalytics:
        end_date = as_utc(observed_at).astimezone(PACIFIC_TIMEZONE).date() - timedelta(days=1)
        start_date = end_date - timedelta(days=max(1, lookback_days) - 1)
        revenue_start_date = end_date - timedelta(days=max(1, revenue_window_days) - 1)
        overall: dict[date, dict[str, int]] = {}
        long_views: dict[date, int] = {}
        video_views: dict[tuple[str, date], int] = {}
        revenue_by_day: dict[date, float] = {}
        requests = 0

        for table in self._paged_query(
            start_date=start_date,
            end_date=end_date,
            metrics=("views", "subscribersGained", "subscribersLost"),
            dimensions=("day",),
        ):
            requests += 1
            for row in _rows(table):
                day = date.fromisoformat(str(row["day"]))
                overall[day] = {
                    "views": _metric_int(row.get("views")),
                    "subscribersGained": _metric_int(row.get("subscribersGained")),
                    "subscribersLost": _metric_int(row.get("subscribersLost")),
                }

        for table in self._paged_query(
            start_date=start_date,
            end_date=end_date,
            metrics=("views",),
            dimensions=("day", "creatorContentType"),
        ):
            requests += 1
            for row in _rows(table):
                if (
                    _normalize_creator_content_type(row.get("creatorContentType"))
                    == LONG_FORM_CONTENT_TYPE
                ):
                    long_views[date.fromisoformat(str(row["day"]))] = _metric_int(
                        row.get("views")
                    )

        for batch in _batches(list(long_video_ids), 200):
            for table in self._paged_query(
                start_date=start_date,
                end_date=end_date,
                metrics=("views",),
                dimensions=("day", "video", "creatorContentType"),
                filters="video==" + ",".join(batch),
            ):
                requests += 1
                for row in _rows(table):
                    if (
                        _normalize_creator_content_type(
                            row.get("creatorContentType")
                        )
                        != LONG_FORM_CONTENT_TYPE
                    ):
                        continue
                    day = date.fromisoformat(str(row["day"]))
                    video_views[(str(row["video"]), day)] = _metric_int(row.get("views"))

        # YouTube Studio 概览中的“过去 28 天估算收入”对应
        # YouTube Analytics API 的 estimatedRevenue，而不是 estimatedAdRevenue。
        # 按 day 查询并求和，既能得到滚动窗口总额，也能保留 API 实际返回到哪一天。
        if include_revenue:
            for table in self._paged_query(
                start_date=revenue_start_date,
                end_date=end_date,
                metrics=("estimatedRevenue",),
                dimensions=("day",),
                currency="USD",
            ):
                requests += 1
                for row in _rows(table):
                    day = date.fromisoformat(str(row["day"]))
                    revenue_by_day[day] = _metric_float(row.get("estimatedRevenue"))

        returned_days = (
            set(overall)
            | set(long_views)
            | {day for _, day in video_views}
        )
        actual_data_through_date = max(returned_days, default=end_date)
        return DailyAnalytics(
            overall=overall,
            long_views=long_views,
            video_views=video_views,
            estimated_revenue_last_28d_usd=(
                round(sum(revenue_by_day.values()), 6) if revenue_by_day else None
            ),
            revenue_window_start_date=revenue_start_date,
            revenue_window_end_date=end_date,
            revenue_data_through_date=max(revenue_by_day, default=None),
            fetched_at=as_utc(observed_at),
            data_through_date=actual_data_through_date,
            api_requests=requests,
        )

    def _paged_query(self, **kwargs: Any) -> list[AnalyticsTable]:
        tables: list[AnalyticsTable] = []
        start_index = 1
        while True:
            table = self.youtube.query(start_index=start_index, max_results=200, **kwargs)
            tables.append(table)
            if len(table.rows) < 200:
                return tables
            start_index += len(table.rows)


def _rows(table: AnalyticsTable) -> list[dict[str, object]]:
    return [dict(zip(table.columns, row, strict=True)) for row in table.rows]


def _batches(items: list[str], size: int) -> list[list[str]]:
    return [items[start : start + size] for start in range(0, len(items), size)]


def _metric_int(value: object) -> int:
    return int(str(value or 0))


def _metric_float(value: object) -> float:
    return float(str(value or 0))


def _normalize_creator_content_type(value: object) -> str:
    """兼容 Analytics API 实际 camelCase 与旧版常量式返回值。"""
    compact = "".join(character for character in str(value or "").lower() if character.isalnum())
    aliases = {
        "videoondemand": LONG_FORM_CONTENT_TYPE,
        "shorts": SHORT_FORM_CONTENT_TYPE,
        "livestream": LIVE_STREAM_CONTENT_TYPE,
    }
    return aliases.get(compact, str(value or "").strip())
