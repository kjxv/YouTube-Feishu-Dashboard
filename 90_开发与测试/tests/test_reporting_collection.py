from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yfd_latest_video_tracker.reporting import (
    REACH_REPORT_TYPE_ID,
    VideoReachReportingCollector,
)
from youtube_feishu_dashboard.api.youtube.schemas import ReportingJob, ReportingReport
from youtube_feishu_dashboard.catalog.field_catalog import FieldCatalog
from youtube_feishu_dashboard.db.repositories import SqlAlchemyStorage

REACH_FIELD_IDS = ("ANALYTICS_IMPRESSIONS", "ANALYTICS_IMPRESSIONS_CTR")


class FakeReportingGateway:
    def __init__(self, *, has_job: bool = True) -> None:
        self.has_job = has_job
        self.calls: list[tuple[str, str | None]] = []

    def list_report_types(self) -> list[dict[str, Any]]:
        return []

    def list_jobs(self) -> list[ReportingJob]:
        self.calls.append(("list_jobs", None))
        if not self.has_job:
            return []
        return [ReportingJob("job-1", REACH_REPORT_TYPE_ID, "yfd-channel-reach-basic", {})]

    def create_job(self, report_type_id: str, name: str) -> ReportingJob:
        self.calls.append(("create_job", report_type_id))
        self.has_job = True
        return ReportingJob("job-1", report_type_id, name, {})

    def list_reports(self, job_id: str) -> list[ReportingReport]:
        self.calls.append(("list_reports", job_id))
        return [
            _report("report-1", "2026-08-31T07:00:00Z", "2026-09-01T12:00:00Z"),
            _report("report-2", "2026-09-01T07:00:00Z", "2026-09-02T12:00:00Z"),
        ]

    def download_report(self, report: ReportingReport, destination: Path) -> Path:
        self.calls.append(("download", report.report_id))
        destination.parent.mkdir(parents=True, exist_ok=True)
        values = {
            "report-1": ("2026-08-31", 100, 5.0),
            "report-2": ("2026-09-01", 300, 10.0),
        }
        report_date, impressions, ctr = values[report.report_id]
        destination.write_text(
            "date,channel_id,video_id,video_thumbnail_impressions,"
            "video_thumbnail_impressions_ctr\n"
            f"{report_date},UC_TEST,video-1,{impressions},{ctr}\n",
            encoding="utf-8",
        )
        return destination


def _report(report_id: str, start: str, created: str) -> ReportingReport:
    return ReportingReport(
        report_id=report_id,
        job_id="job-1",
        start_time=datetime.fromisoformat(start.replace("Z", "+00:00")),
        end_time=datetime.fromisoformat(start.replace("Z", "+00:00")),
        create_time=datetime.fromisoformat(created.replace("Z", "+00:00")),
        download_url=f"https://example.test/{report_id}",
        raw={},
    )


def _seed_video(storage: SqlAlchemyStorage) -> None:
    with storage.transaction() as repos:
        repos.channels.upsert(
            "UC_TEST",
            title="测试频道",
            uploads_playlist_id="UU_TEST",
            timezone="Asia/Shanghai",
        )
        repos.videos.upsert(
            {
                "id": "video-1",
                "channel_id": "UC_TEST",
                "title": "测试视频",
                "published_at": datetime(2026, 8, 31, 0, 0, tzinfo=UTC),
            }
        )


def test_reporting_collector_downloads_daily_reports_and_aggregates_reach(
    storage: SqlAlchemyStorage,
    tmp_path: Path,
) -> None:
    _seed_video(storage)
    gateway = FakeReportingGateway()
    collector = VideoReachReportingCollector(
        youtube=gateway,
        storage=storage,
        catalog=FieldCatalog.load_builtin(),
        field_ids=REACH_FIELD_IDS,
        cache_directory=tmp_path / "reports",
        lookback_days=9,
    )
    observed_at = datetime(2026, 9, 3, 1, 0, tzinfo=UTC)

    result = collector.collect_video(
        video_id="video-1",
        published_at=datetime(2026, 8, 31, 0, 0, tzinfo=UTC),
        observed_at=observed_at,
    )

    assert result.status == "available"
    assert result.api_request_count == 4
    assert result.downloaded_report_count == 2
    assert result.data_through_date.isoformat() == "2026-09-01"
    assert result.field_values == {
        "ANALYTICS_IMPRESSIONS": 400,
        "ANALYTICS_IMPRESSIONS_CTR": 8.75,
    }
    assert result.as_standard_values()["REPORTING_FETCHED_AT"] == observed_at
    assert result.as_standard_values()["REPORTING_FETCHED_AT_PACIFIC"] == (
        "2026-09-02T18:00:00-07:00"
    )
    assert result.as_standard_values()["REPORTING_FETCHED_AT_BEIJING"] == (
        "2026-09-03T09:00:00+08:00"
    )
    assert result.as_standard_values()["REPORTING_DATA_THROUGH_AT_PACIFIC"] == (
        "2026-09-01T23:59:59-07:00"
    )
    assert result.as_standard_values()["REPORTING_DATA_THROUGH_AT_BEIJING"] == (
        "2026-09-02T14:59:59+08:00"
    )

    cached = collector.latest_cached("video-1")
    assert cached is not None
    assert cached.field_values == result.field_values
    assert cached.data_through_date == result.data_through_date


def test_reporting_collector_creates_missing_job_and_caches_pending_check(
    storage: SqlAlchemyStorage,
    tmp_path: Path,
) -> None:
    _seed_video(storage)
    gateway = FakeReportingGateway(has_job=False)
    collector = VideoReachReportingCollector(
        youtube=gateway,
        storage=storage,
        catalog=FieldCatalog.load_builtin(),
        field_ids=REACH_FIELD_IDS,
        cache_directory=tmp_path / "reports",
        lookback_days=9,
    )

    result = collector.collect_video(
        video_id="video-1",
        published_at=datetime(2026, 8, 31, 0, 0, tzinfo=UTC),
        observed_at=datetime(2026, 9, 3, 1, 0, tzinfo=UTC),
    )

    assert result.status == "job_created_waiting_for_report"
    assert result.api_request_count == 2
    assert result.data_fetched_at is None
    assert all(value is None for value in result.field_values.values())
    assert gateway.calls == [
        ("list_jobs", None),
        ("create_job", REACH_REPORT_TYPE_ID),
    ]


def test_reporting_check_without_new_report_preserves_real_data_fetch_time(
    storage: SqlAlchemyStorage,
    tmp_path: Path,
) -> None:
    _seed_video(storage)
    gateway = FakeReportingGateway()
    collector = VideoReachReportingCollector(
        youtube=gateway,
        storage=storage,
        catalog=FieldCatalog.load_builtin(),
        field_ids=REACH_FIELD_IDS,
        cache_directory=tmp_path / "reports",
        lookback_days=9,
    )
    first_checked_at = datetime(2026, 9, 3, 1, 0, tzinfo=UTC)
    second_checked_at = datetime(2026, 9, 4, 1, 0, tzinfo=UTC)

    first = collector.collect_video(
        video_id="video-1",
        published_at=datetime(2026, 8, 31, 0, 0, tzinfo=UTC),
        observed_at=first_checked_at,
    )
    second = collector.collect_video(
        video_id="video-1",
        published_at=datetime(2026, 8, 31, 0, 0, tzinfo=UTC),
        observed_at=second_checked_at,
    )

    assert first.downloaded_report_count == 2
    assert second.downloaded_report_count == 0
    assert second.checked_at == second_checked_at
    assert second.data_fetched_at == first_checked_at
    assert second.as_standard_values()["REPORTING_FETCHED_AT_BEIJING"] == (
        "2026-09-03T09:00:00+08:00"
    )


def test_reporting_does_not_claim_cutoff_or_fetch_time_for_another_video(
    storage: SqlAlchemyStorage,
    tmp_path: Path,
) -> None:
    _seed_video(storage)
    with storage.transaction() as repos:
        repos.videos.upsert(
            {
                "id": "video-without-report-row",
                "channel_id": "UC_TEST",
                "title": "尚未进入报表的视频",
                "published_at": datetime(2026, 8, 31, 0, 0, tzinfo=UTC),
            }
        )
    collector = VideoReachReportingCollector(
        youtube=FakeReportingGateway(),
        storage=storage,
        catalog=FieldCatalog.load_builtin(),
        field_ids=REACH_FIELD_IDS,
        cache_directory=tmp_path / "reports",
        lookback_days=9,
    )

    result = collector.collect_video(
        video_id="video-without-report-row",
        published_at=datetime(2026, 8, 31, 0, 0, tzinfo=UTC),
        observed_at=datetime(2026, 9, 3, 1, 0, tzinfo=UTC),
    )

    assert result.status == "waiting_for_video_report_row"
    assert result.data_fetched_at is None
    assert result.data_through_date is None
    assert result.field_values == {
        "ANALYTICS_IMPRESSIONS": 0,
        "ANALYTICS_IMPRESSIONS_CTR": 0.0,
    }
    assert result.as_standard_values()["REPORTING_FETCHED_AT_BEIJING"] is None
    assert result.as_standard_values()["REPORTING_DATA_THROUGH_AT_PACIFIC"] is None
