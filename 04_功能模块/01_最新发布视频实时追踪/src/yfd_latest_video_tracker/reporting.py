from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from youtube_feishu_dashboard.api.youtube.protocols import YouTubeReportingGateway
from youtube_feishu_dashboard.api.youtube.reporting_extraction import (
    DatedReportFile,
    ReportingReachExtractor,
    ReportingReachValue,
)
from youtube_feishu_dashboard.api.youtube.schemas import ReportingJob, ReportingReport
from youtube_feishu_dashboard.catalog.field_catalog import FieldCatalog
from youtube_feishu_dashboard.core.time import as_utc
from youtube_feishu_dashboard.db.repositories import Storage

from yfd_latest_video_tracker.api_time_fields import reporting_time_values

REACH_REPORT_TYPE_ID = "channel_reach_basic_a1"
REACH_JOB_NAME = "yfd-channel-reach-basic"
YOUTUBE_REPORTING_TIMEZONE = ZoneInfo("America/Los_Angeles")
_SAFE_FILE_COMPONENT = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True, slots=True)
class VideoReportingResult:
    video_id: str
    checked_at: datetime
    data_fetched_at: datetime | None
    data_through_date: date | None
    field_values: dict[str, ReportingReachValue]
    status: str
    api_request_count: int
    downloaded_report_count: int
    raw: dict[str, Any]

    def as_standard_values(self) -> dict[str, object]:
        return {
            **self.field_values,
            "REPORTING_FETCHED_AT": self.data_fetched_at,
            "REPORTING_DATA_THROUGH_DATE": self.data_through_date,
            **reporting_time_values(
                fetched_at=self.data_fetched_at,
                data_through_date=self.data_through_date,
            ),
        }


@dataclass(frozen=True, slots=True)
class _ReportingCycle:
    status: str
    job_id: str
    files: tuple[DatedReportFile, ...]
    downloaded_report_count: int
    raw: dict[str, Any]


class VideoReachReportingCollector:
    """维护 Reach Basic 报表任务，并按视频累计最近追踪窗口内的日报。"""

    def __init__(
        self,
        *,
        youtube: YouTubeReportingGateway,
        storage: Storage,
        catalog: FieldCatalog,
        field_ids: tuple[str, ...],
        cache_directory: Path,
        lookback_days: int,
    ) -> None:
        self.youtube = youtube
        self.storage = storage
        self.extractor = ReportingReachExtractor.compile(catalog, field_ids)
        self.cache_directory = cache_directory
        self.lookback_days = lookback_days
        self._cycle_at: datetime | None = None
        self._cycle: _ReportingCycle | None = None

    @property
    def field_ids(self) -> tuple[str, ...]:
        return self.extractor.field_ids

    def collect_video(
        self,
        *,
        video_id: str,
        published_at: datetime,
        observed_at: datetime,
    ) -> VideoReportingResult:
        checked_at = as_utc(observed_at)
        cycle, api_request_count = self._load_cycle(checked_at)
        published_date = as_utc(published_at).astimezone(YOUTUBE_REPORTING_TIMEZONE).date()
        files = tuple(
            item for item in cycle.files if item.report_date >= published_date
        )
        empty_values: dict[str, ReportingReachValue] = {
            field_id: None for field_id in self.field_ids
        }

        if not files:
            result = VideoReportingResult(
                video_id=video_id,
                checked_at=checked_at,
                data_fetched_at=None,
                data_through_date=None,
                field_values=empty_values,
                status=cycle.status,
                api_request_count=api_request_count,
                downloaded_report_count=cycle.downloaded_report_count,
                raw=cycle.raw,
            )
        else:
            extraction = self.extractor.extract_video(files, video_id=video_id)
            previous = self.latest_cached(video_id)
            data_fetched_at: datetime | None = None
            if extraction.data_through_date is not None:
                data_fetched_at = checked_at
                if (
                    previous is not None
                    and previous.data_through_date == extraction.data_through_date
                    and previous.data_fetched_at is not None
                    and cycle.downloaded_report_count == 0
                ):
                    data_fetched_at = previous.data_fetched_at
            result = VideoReportingResult(
                video_id=video_id,
                checked_at=checked_at,
                data_fetched_at=data_fetched_at,
                data_through_date=extraction.data_through_date,
                field_values=extraction.values,
                status=(
                    "available"
                    if extraction.data_through_date is not None
                    else "waiting_for_video_report_row"
                ),
                api_request_count=api_request_count,
                downloaded_report_count=cycle.downloaded_report_count,
                raw={
                    **cycle.raw,
                    "eligible_report_count": len(files),
                    "matched_row_count": extraction.matched_row_count,
                },
            )
        self._save(result)
        return result

    def latest_cached(self, video_id: str) -> VideoReportingResult | None:
        with self.storage.transaction() as repos:
            cached = repos.video_reporting.latest(video_id)
            if cached is None:
                return None
            return VideoReportingResult(
                video_id=cached.video_id,
                checked_at=as_utc(cached.checked_at),
                data_fetched_at=(
                    as_utc(cached.data_fetched_at)
                    if cached.data_fetched_at is not None
                    else None
                ),
                data_through_date=cached.data_through_date,
                field_values={
                    str(field_id): _cached_value(value)
                    for field_id, value in cached.field_values.items()
                },
                status=cached.status,
                api_request_count=0,
                downloaded_report_count=0,
                raw=dict(cached.raw_json or {}),
            )

    def _load_cycle(self, checked_at: datetime) -> tuple[_ReportingCycle, int]:
        if self._cycle_at == checked_at and self._cycle is not None:
            return self._cycle, 0

        jobs = self.youtube.list_jobs()
        job = _select_job(jobs)
        if job is None:
            created = self.youtube.create_job(REACH_REPORT_TYPE_ID, REACH_JOB_NAME)
            cycle = _ReportingCycle(
                status="job_created_waiting_for_report",
                job_id=created.job_id,
                files=(),
                downloaded_report_count=0,
                raw={"job_id": created.job_id, "report_type_id": REACH_REPORT_TYPE_ID},
            )
            self._cycle_at = checked_at
            self._cycle = cycle
            return cycle, 2

        reports = self.youtube.list_reports(job.job_id)
        cutoff = (
            checked_at.astimezone(YOUTUBE_REPORTING_TIMEZONE).date()
            - timedelta(days=self.lookback_days)
        )
        selected = [
            (report_date, report)
            for report_date, report in _latest_reports_by_date(reports).items()
            if report_date >= cutoff
        ]
        files: list[DatedReportFile] = []
        downloaded = 0
        for report_date, report in sorted(selected):
            destination = self.cache_directory / f"{_safe_name(report.report_id)}.csv"
            if not destination.is_file():
                temporary = destination.with_suffix(".csv.part")
                self.youtube.download_report(report, temporary)
                temporary.replace(destination)
                downloaded += 1
            files.append(DatedReportFile(report_date=report_date, path=destination))

        cycle = _ReportingCycle(
            status="available" if files else "waiting_for_first_report",
            job_id=job.job_id,
            files=tuple(files),
            downloaded_report_count=downloaded,
            raw={
                "job_id": job.job_id,
                "report_type_id": REACH_REPORT_TYPE_ID,
                "report_ids": [report.report_id for _, report in sorted(selected)],
            },
        )
        self._cycle_at = checked_at
        self._cycle = cycle
        return cycle, 2 + downloaded

    def _save(self, result: VideoReportingResult) -> None:
        with self.storage.transaction() as repos:
            repos.video_reporting.add_snapshot(
                video_id=result.video_id,
                checked_at=result.checked_at,
                data_fetched_at=result.data_fetched_at,
                data_through_date=result.data_through_date,
                status=result.status,
                field_values=result.field_values,
                raw_json=result.raw,
            )


def _select_job(jobs: list[ReportingJob]) -> ReportingJob | None:
    matches = [item for item in jobs if item.report_type_id == REACH_REPORT_TYPE_ID]
    if not matches:
        return None
    return sorted(matches, key=lambda item: (item.name != REACH_JOB_NAME, item.job_id))[0]


def _latest_reports_by_date(
    reports: list[ReportingReport],
) -> dict[date, ReportingReport]:
    selected: dict[date, ReportingReport] = {}
    for report in reports:
        if report.start_time is None:
            continue
        report_date = as_utc(report.start_time).astimezone(YOUTUBE_REPORTING_TIMEZONE).date()
        previous = selected.get(report_date)
        if previous is None or _report_order(report) > _report_order(previous):
            selected[report_date] = report
    return selected


def _report_order(report: ReportingReport) -> tuple[datetime, str]:
    source = report.create_time or report.end_time or report.start_time
    if source is None:
        raise ValueError(f"Reporting 报告 {report.report_id} 缺少时间信息")
    created = as_utc(source)
    return created, report.report_id


def _safe_name(value: str) -> str:
    return _SAFE_FILE_COMPONENT.sub("_", value)


def _cached_value(value: object) -> ReportingReachValue:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Reporting 本地缓存包含无效数值：{value!r}")
    return value
