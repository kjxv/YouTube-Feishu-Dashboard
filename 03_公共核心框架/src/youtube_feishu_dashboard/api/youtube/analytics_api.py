from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any

from googleapiclient.discovery import build

from youtube_feishu_dashboard.api.youtube.auth import YouTubeCredentialProvider
from youtube_feishu_dashboard.api.youtube.base import GoogleApiClientBase
from youtube_feishu_dashboard.api.youtube.schemas import AnalyticsTable


class YouTubeAnalyticsClient(GoogleApiClientBase):
    @classmethod
    def from_credentials(cls, provider: YouTubeCredentialProvider) -> YouTubeAnalyticsClient:
        return cls(
            build(
                "youtubeAnalytics",
                "v2",
                credentials=provider.credentials(),
                cache_discovery=False,
            )
        )

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
        currency: str | None = None,
        start_index: int = 1,
        max_results: int = 200,
    ) -> AnalyticsTable:
        parameters: dict[str, Any] = {
            "ids": ids,
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "metrics": ",".join(metrics),
            "startIndex": start_index,
            "maxResults": max_results,
        }
        dimensions_value = ",".join(dimensions)
        if dimensions_value:
            parameters["dimensions"] = dimensions_value
        if filters:
            parameters["filters"] = filters
        if sort:
            parameters["sort"] = sort
        if currency:
            parameters["currency"] = currency
        response = self.execute(self.service.reports().query(**parameters))
        columns = tuple(str(item["name"]) for item in response.get("columnHeaders") or [])
        rows = tuple(tuple(item) for item in response.get("rows") or [])
        return AnalyticsTable(columns=columns, rows=rows, raw=response)
