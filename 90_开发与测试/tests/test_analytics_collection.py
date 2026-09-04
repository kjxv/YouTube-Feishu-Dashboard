from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from yfd_latest_video_tracker.analytics import VideoAnalyticsCollector
from youtube_feishu_dashboard.api.youtube.analytics_api import YouTubeAnalyticsClient
from youtube_feishu_dashboard.api.youtube.schemas import AnalyticsTable
from youtube_feishu_dashboard.catalog.field_catalog import FieldCatalog
from youtube_feishu_dashboard.db.repositories import SqlAlchemyStorage

CORE_FIELD_IDS = (
    "ANALYTICS_ENGAGED_VIEWS",
    "ANALYTICS_WATCH_TIME_MINUTES",
    "ANALYTICS_AVG_VIEW_DURATION",
    "ANALYTICS_AVG_VIEW_PERCENT",
    "ANALYTICS_SUB_GAINED",
    "ANALYTICS_SUB_LOST",
    "ANALYTICS_SHARES",
)

CORE_METRICS = (
    "engagedViews",
    "estimatedMinutesWatched",
    "averageViewDuration",
    "averageViewPercentage",
    "subscribersGained",
    "subscribersLost",
    "shares",
)

AD_REVENUE_FIELD_ID = "ANALYTICS_EST_AD_REVENUE"
AD_REVENUE_METRIC = "estimatedAdRevenue"


class FakeAnalyticsGateway:
    def __init__(self, *, empty: bool = False) -> None:
        self.empty = empty
        self.calls: list[dict[str, Any]] = []

    def query(self, **kwargs: Any) -> AnalyticsTable:
        self.calls.append(dict(kwargs))
        metrics = tuple(kwargs["metrics"])
        first_day = {
            "engagedViews": 10,
            "estimatedMinutesWatched": 20.5,
            "averageViewDuration": 30.0,
            "averageViewPercentage": 40.0,
            "subscribersGained": 1,
            "subscribersLost": 0,
            "shares": 2,
            "estimatedAdRevenue": 0.75,
        }
        second_day = {
            "engagedViews": 12,
            "estimatedMinutesWatched": 25.5,
            "averageViewDuration": 35.0,
            "averageViewPercentage": 45.0,
            "subscribersGained": 2,
            "subscribersLost": 1,
            "shares": 3,
            "estimatedAdRevenue": 1.0,
        }
        summary = {
            "engagedViews": 22,
            "estimatedMinutesWatched": 46.0,
            "averageViewDuration": 32.7,
            "averageViewPercentage": 42.7,
            "subscribersGained": 3,
            "subscribersLost": 1,
            "shares": 5,
            "estimatedAdRevenue": 1.75,
        }
        if tuple(kwargs.get("dimensions", ())) == ("day",):
            rows: tuple[tuple[Any, ...], ...]
            if self.empty:
                rows = ()
            else:
                rows = (
                    ("2026-08-31", *(first_day[metric] for metric in metrics)),
                    ("2026-09-01", *(second_day[metric] for metric in metrics)),
                )
            return AnalyticsTable(
                columns=("day", *metrics),
                rows=rows,
                raw={"kind": "daily"},
            )
        return AnalyticsTable(
            columns=metrics,
            rows=(tuple(summary[metric] for metric in metrics),),
            raw={"kind": "summary"},
        )


class FakeRequest:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def execute(self) -> dict[str, Any]:
        return self.payload


class FakeAnalyticsService:
    def __init__(self) -> None:
        self.parameters: dict[str, Any] | None = None

    def reports(self) -> FakeAnalyticsService:
        return self

    def query(self, **kwargs: Any) -> FakeRequest:
        self.parameters = kwargs
        return FakeRequest(
            {
                "columnHeaders": [{"name": "day"}, {"name": "views"}],
                "rows": [["2026-09-01", 9]],
            }
        )


def _seed_video(storage: SqlAlchemyStorage, published_at: datetime) -> None:
    with storage.transaction() as repos:
        repos.channels.upsert(
            "UC_TEST",
            title="测试频道",
            uploads_playlist_id="UU_TEST",
            timezone="Asia/Shanghai",
        )
        repos.videos.upsert(
            {
                "id": "dQ0OtH9XIgY",
                "channel_id": "UC_TEST",
                "title": "测试长视频",
                "published_at": published_at,
            }
        )


def test_analytics_client_builds_targeted_query() -> None:
    service = FakeAnalyticsService()
    table = YouTubeAnalyticsClient(service).query(
        start_date=date(2026, 8, 28),
        end_date=date(2026, 9, 2),
        metrics=("views",),
        dimensions=("day",),
        filters="video==dQ0OtH9XIgY",
        sort="day",
    )

    assert service.parameters == {
        "ids": "channel==MINE",
        "startDate": "2026-08-28",
        "endDate": "2026-09-02",
        "metrics": "views",
        "startIndex": 1,
        "maxResults": 200,
        "dimensions": "day",
        "filters": "video==dQ0OtH9XIgY",
        "sort": "day",
    }
    assert table.columns == ("day", "views")
    assert table.rows == (("2026-09-01", 9),)


def test_analytics_client_sends_explicit_usd_for_ad_revenue() -> None:
    service = FakeAnalyticsService()

    YouTubeAnalyticsClient(service).query(
        start_date=date(2026, 8, 28),
        end_date=date(2026, 9, 2),
        metrics=(AD_REVENUE_METRIC,),
        filters="video==dQ0OtH9XIgY",
        currency="USD",
    )

    assert service.parameters is not None
    assert service.parameters["metrics"] == AD_REVENUE_METRIC
    assert service.parameters["currency"] == "USD"


def test_collector_uses_actual_last_day_and_round_trips_cache(
    storage: SqlAlchemyStorage,
) -> None:
    published_at = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
    observed_at = datetime(2026, 9, 3, 1, 30, tzinfo=UTC)
    _seed_video(storage, published_at)
    gateway = FakeAnalyticsGateway()
    collector = VideoAnalyticsCollector(
        youtube=gateway,
        storage=storage,
        catalog=FieldCatalog.load_builtin(),
        field_ids=CORE_FIELD_IDS,
        channel_id="UC_TEST",
    )

    result = collector.collect_video(
        video_id="dQ0OtH9XIgY",
        published_at=published_at,
        observed_at=observed_at,
    )

    assert len(gateway.calls) == 2
    assert gateway.calls[0]["dimensions"] == ("day",)
    assert gateway.calls[0]["metrics"] == CORE_METRICS
    assert gateway.calls[0]["filters"] == "video==dQ0OtH9XIgY"
    assert gateway.calls[1]["end_date"] == date(2026, 9, 1)
    assert result.data_through_date == date(2026, 9, 1)
    assert result.api_request_count == 2
    assert result.metric_values == {
        "ANALYTICS_ENGAGED_VIEWS": 22,
        "ANALYTICS_WATCH_TIME_MINUTES": 46.0,
        "ANALYTICS_AVG_VIEW_DURATION": 32.7,
        "ANALYTICS_AVG_VIEW_PERCENT": 42.7,
        "ANALYTICS_SUB_GAINED": 3,
        "ANALYTICS_SUB_LOST": 1,
        "ANALYTICS_SHARES": 5,
    }
    assert result.as_standard_values()["ANALYTICS_FETCHED_AT"] == observed_at
    assert result.as_standard_values()["ANALYTICS_DATA_THROUGH_DATE"] == date(
        2026, 9, 1
    )
    assert result.as_standard_values()["ANALYTICS_FETCHED_AT_PACIFIC"] == (
        "2026-09-02T18:30:00-07:00"
    )
    assert result.as_standard_values()["ANALYTICS_FETCHED_AT_BEIJING"] == (
        "2026-09-03T09:30:00+08:00"
    )
    assert result.as_standard_values()["ANALYTICS_DATA_THROUGH_AT_PACIFIC"] == (
        "2026-09-01T23:59:59-07:00"
    )
    assert result.as_standard_values()["ANALYTICS_DATA_THROUGH_AT_BEIJING"] == (
        "2026-09-02T14:59:59+08:00"
    )

    cached = collector.latest_cached("dQ0OtH9XIgY")
    assert cached is not None
    assert cached.metric_values == result.metric_values
    assert cached.data_through_date == result.data_through_date


def test_collector_caches_successful_empty_result_without_fake_cutoff(
    storage: SqlAlchemyStorage,
) -> None:
    published_at = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    observed_at = datetime(2026, 9, 3, 1, 30, tzinfo=UTC)
    _seed_video(storage, published_at)
    gateway = FakeAnalyticsGateway(empty=True)
    collector = VideoAnalyticsCollector(
        youtube=gateway,
        storage=storage,
        catalog=FieldCatalog.load_builtin(),
        field_ids=CORE_FIELD_IDS,
    )

    result = collector.collect_video(
        video_id="dQ0OtH9XIgY",
        published_at=published_at,
        observed_at=observed_at,
    )

    assert len(gateway.calls) == 1
    assert result.empty is True
    assert result.api_request_count == 1
    assert result.data_through_date is None
    assert all(value is None for value in result.metric_values.values())
    cached = collector.latest_cached("dQ0OtH9XIgY")
    assert cached is not None
    assert cached.empty is True


def test_collector_fetches_estimated_ad_revenue_explicitly_in_usd(
    storage: SqlAlchemyStorage,
) -> None:
    published_at = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
    observed_at = datetime(2026, 9, 3, 1, 30, tzinfo=UTC)
    _seed_video(storage, published_at)
    gateway = FakeAnalyticsGateway()
    collector = VideoAnalyticsCollector(
        youtube=gateway,
        storage=storage,
        catalog=FieldCatalog.load_builtin(),
        field_ids=(*CORE_FIELD_IDS, AD_REVENUE_FIELD_ID),
        channel_id="UC_TEST",
    )

    result = collector.collect_video(
        video_id="dQ0OtH9XIgY",
        published_at=published_at,
        observed_at=observed_at,
    )

    assert result.metric_values[AD_REVENUE_FIELD_ID] == 1.75
    assert all(call["currency"] == "USD" for call in gateway.calls)
