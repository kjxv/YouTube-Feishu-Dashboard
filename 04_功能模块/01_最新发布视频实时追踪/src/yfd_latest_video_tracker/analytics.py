from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from youtube_feishu_dashboard.api.youtube.analytics_extraction import (
    AnalyticsMetricExtractor,
    AnalyticsMetricValue,
    latest_returned_day,
)
from youtube_feishu_dashboard.api.youtube.protocols import YouTubeAnalyticsGateway
from youtube_feishu_dashboard.api.youtube.schemas import AnalyticsTable
from youtube_feishu_dashboard.catalog.field_catalog import FieldCatalog
from youtube_feishu_dashboard.core.errors import ConfigurationError
from youtube_feishu_dashboard.core.time import as_utc
from youtube_feishu_dashboard.db.repositories import Storage

from yfd_latest_video_tracker.api_time_fields import analytics_time_values

YOUTUBE_REPORTING_TIMEZONE = ZoneInfo("America/Los_Angeles")
USD_REVENUE_METRICS = frozenset(
    {
        "estimatedRevenue",
        "estimatedAdRevenue",
        "estimatedRedPartnerRevenue",
        "grossRevenue",
        "cpm",
        "playbackBasedCpm",
    }
)


@dataclass(frozen=True, slots=True)
class VideoAnalyticsResult:
    video_id: str
    start_date: date
    requested_end_date: date
    data_through_date: date | None
    fetched_at: datetime
    metric_values: dict[str, AnalyticsMetricValue]
    empty: bool
    api_request_count: int
    raw: dict[str, Any]

    def missing_metric_field_ids(self, required_field_ids: Sequence[str]) -> tuple[str, ...]:
        """列出旧缓存中尚不存在的当前请求指标。"""

        return tuple(
            field_id for field_id in required_field_ids if field_id not in self.metric_values
        )

    def as_standard_values(
        self,
        *,
        required_metric_field_ids: Sequence[str] = (),
    ) -> dict[str, object]:
        """返回可与 Data API 标准值直接合并的字段集合。"""

        return {
            **{
                field_id: self.metric_values.get(field_id)
                for field_id in required_metric_field_ids
            },
            **self.metric_values,
            "ANALYTICS_DAY": self.data_through_date,
            "ANALYTICS_FETCHED_AT": self.fetched_at,
            "ANALYTICS_DATA_THROUGH_DATE": self.data_through_date,
            **analytics_time_values(
                fetched_at=self.fetched_at,
                data_through_date=self.data_through_date,
            ),
        }


class VideoAnalyticsCollector:
    """查询单个视频的 Analytics 汇总并保存可复用的本地快照。"""

    def __init__(
        self,
        *,
        youtube: YouTubeAnalyticsGateway,
        storage: Storage,
        catalog: FieldCatalog,
        field_ids: tuple[str, ...],
        channel_id: str | None = None,
    ) -> None:
        self.youtube = youtube
        self.storage = storage
        self.extractor = AnalyticsMetricExtractor.compile(catalog, field_ids)
        self.channel_id = channel_id

    @property
    def field_ids(self) -> tuple[str, ...]:
        return tuple(field.standard_field_id for field in self.extractor.fields)

    def collect_video(
        self,
        *,
        video_id: str,
        published_at: datetime,
        observed_at: datetime,
    ) -> VideoAnalyticsResult:
        fetched_at = as_utc(observed_at)
        start_date = as_utc(published_at).astimezone(YOUTUBE_REPORTING_TIMEZONE).date()
        requested_end_date = fetched_at.astimezone(YOUTUBE_REPORTING_TIMEZONE).date()
        if start_date > requested_end_date:
            raise ConfigurationError(
                f"视频 {video_id} 的发布时间晚于本次 Analytics 查询时间"
            )

        metrics = self.extractor.official_metrics
        currency = "USD" if USD_REVENUE_METRICS.intersection(metrics) else None
        ids = f"channel=={self.channel_id}" if self.channel_id else "channel==MINE"
        daily = self.youtube.query(
            ids=ids,
            start_date=start_date,
            end_date=requested_end_date,
            metrics=metrics,
            dimensions=("day",),
            filters=f"video=={video_id}",
            sort="day",
            currency=currency,
        )
        data_through_date = latest_returned_day(daily)

        if data_through_date is None:
            summary = AnalyticsTable(columns=metrics, rows=(), raw={})
        else:
            summary = self.youtube.query(
                ids=ids,
                start_date=start_date,
                end_date=data_through_date,
                metrics=metrics,
                filters=f"video=={video_id}",
                currency=currency,
            )
        extraction = self.extractor.extract_summary(summary)
        raw = {"daily": daily.raw, "summary": summary.raw}
        result = VideoAnalyticsResult(
            video_id=video_id,
            start_date=start_date,
            requested_end_date=requested_end_date,
            data_through_date=data_through_date,
            fetched_at=fetched_at,
            metric_values=extraction.values,
            empty=extraction.empty,
            api_request_count=1 if data_through_date is None else 2,
            raw=raw,
        )
        with self.storage.transaction() as repos:
            repos.video_analytics.add_snapshot(
                video_id=video_id,
                fetched_at=fetched_at,
                start_date=start_date,
                requested_end_date=requested_end_date,
                data_through_date=data_through_date,
                metric_values=extraction.values,
                raw_json=raw,
            )
        return result

    def latest_cached(self, video_id: str) -> VideoAnalyticsResult | None:
        with self.storage.transaction() as repos:
            cached = repos.video_analytics.latest(video_id)
            if cached is None:
                return None
            metric_values = {
                str(field_id): _cached_metric(value)
                for field_id, value in cached.metric_values.items()
            }
            return VideoAnalyticsResult(
                video_id=cached.video_id,
                start_date=cached.start_date,
                requested_end_date=cached.requested_end_date,
                data_through_date=cached.data_through_date,
                fetched_at=as_utc(cached.fetched_at),
                metric_values=metric_values,
                empty=all(value is None for value in metric_values.values()),
                api_request_count=0,
                raw=dict(cached.raw_json or {}),
            )


def _cached_metric(value: object) -> AnalyticsMetricValue:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"Analytics 本地缓存包含无效指标值：{value!r}")
    return value
