from __future__ import annotations

from pathlib import Path
from typing import Any

from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from youtube_feishu_dashboard.api.youtube.auth import YouTubeCredentialProvider
from youtube_feishu_dashboard.api.youtube.base import GoogleApiClientBase
from youtube_feishu_dashboard.api.youtube.schemas import (
    ReportingJob,
    ReportingReport,
    parse_api_datetime,
)
from youtube_feishu_dashboard.core.errors import ExternalServiceError


class YouTubeReportingClient(GoogleApiClientBase):
    def __init__(self, service: Any, credentials: Credentials) -> None:
        super().__init__(service)
        self.credentials = credentials

    @classmethod
    def from_credentials(cls, provider: YouTubeCredentialProvider) -> YouTubeReportingClient:
        credentials = provider.credentials()
        service = build(
            "youtubereporting",
            "v1",
            credentials=credentials,
            cache_discovery=False,
        )
        return cls(service, credentials)

    def list_report_types(self) -> list[dict[str, Any]]:
        response = self.execute(self.service.reportTypes().list())
        return list(response.get("reportTypes") or [])

    def list_jobs(self) -> list[ReportingJob]:
        response = self.execute(self.service.jobs().list())
        return [self._parse_job(item) for item in response.get("jobs") or []]

    def create_job(self, report_type_id: str, name: str) -> ReportingJob:
        response = self.execute(
            self.service.jobs().create(body={"reportTypeId": report_type_id, "name": name})
        )
        return self._parse_job(response)

    def list_reports(self, job_id: str) -> list[ReportingReport]:
        results: list[ReportingReport] = []
        page_token: str | None = None
        while True:
            parameters: dict[str, Any] = {"jobId": job_id}
            if page_token:
                parameters["pageToken"] = page_token
            response = self.execute(self.service.jobs().reports().list(**parameters))
            results.extend(self._parse_report(item) for item in response.get("reports") or [])
            page_token = response.get("nextPageToken")
            if not page_token:
                return results

    def download_report(self, report: ReportingReport, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        session = AuthorizedSession(self.credentials)  # type: ignore[no-untyped-call]
        try:
            response = session.get(report.download_url, timeout=120)
            response.raise_for_status()
            destination.write_bytes(response.content)
        except Exception as exc:
            raise ExternalServiceError(f"下载 Reporting 报告失败：{exc}") from exc
        return destination

    @staticmethod
    def _parse_job(item: dict[str, Any]) -> ReportingJob:
        return ReportingJob(
            job_id=str(item["id"]),
            report_type_id=str(item["reportTypeId"]),
            name=str(item.get("name", "")),
            raw=item,
        )

    @staticmethod
    def _parse_report(item: dict[str, Any]) -> ReportingReport:
        return ReportingReport(
            report_id=str(item["id"]),
            job_id=str(item["jobId"]),
            start_time=parse_api_datetime(item.get("startTime")),
            end_time=parse_api_datetime(item.get("endTime")),
            download_url=str(item["downloadUrl"]),
            raw=item,
        )
