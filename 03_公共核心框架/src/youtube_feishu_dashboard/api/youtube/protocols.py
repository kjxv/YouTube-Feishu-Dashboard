from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol

from youtube_feishu_dashboard.api.youtube.schemas import (
    AnalyticsTable,
    ChannelResource,
    ReportingJob,
    ReportingReport,
    VideoResource,
)


class YouTubeDataGateway(Protocol):
    def get_channel(self, channel_id: str | None = None) -> ChannelResource: ...

    def list_upload_video_ids(
        self,
        uploads_playlist_id: str,
        *,
        published_after: datetime | None = None,
        max_pages: int = 20,
    ) -> list[str]: ...

    def list_videos(
        self, video_ids: Iterable[str], *, parts: Iterable[str] | None = None
    ) -> list[VideoResource]: ...


class YouTubeAnalyticsGateway(Protocol):
    def query(
        self,
        *,
        start_date: date,
        end_date: date,
        metrics: Iterable[str],
        dimensions: Iterable[str] = (),
        filters: str | None = None,
        ids: str = "channel==MINE",
        sort: str | None = None,
        start_index: int = 1,
        max_results: int = 200,
    ) -> AnalyticsTable: ...


class YouTubeReportingGateway(Protocol):
    def list_report_types(self) -> list[dict[str, Any]]: ...

    def list_jobs(self) -> list[ReportingJob]: ...

    def create_job(self, report_type_id: str, name: str) -> ReportingJob: ...

    def list_reports(self, job_id: str) -> list[ReportingReport]: ...

    def download_report(self, report: ReportingReport, destination: Path) -> Path: ...
