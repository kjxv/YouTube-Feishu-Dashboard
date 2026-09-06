from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from googleapiclient.discovery import build

from youtube_feishu_dashboard.api.youtube.auth import YouTubeCredentialProvider
from youtube_feishu_dashboard.api.youtube.base import GoogleApiClientBase
from youtube_feishu_dashboard.api.youtube.schemas import (
    ChannelResource,
    VideoResource,
    optional_int,
    parse_api_datetime,
)
from youtube_feishu_dashboard.core.errors import ExternalServiceError


class YouTubeDataClient(GoogleApiClientBase):
    @classmethod
    def from_credentials(cls, provider: YouTubeCredentialProvider) -> YouTubeDataClient:
        return cls(
            build(
                "youtube",
                "v3",
                credentials=provider.credentials(),
                cache_discovery=False,
            )
        )

    def get_channel(self, channel_id: str | None = None) -> ChannelResource:
        parameters: dict[str, Any] = {
            "part": "id,snippet,contentDetails,statistics"
        }
        if channel_id:
            parameters["id"] = channel_id
        else:
            parameters["mine"] = True
        response = self.execute(self.service.channels().list(**parameters))
        items = response.get("items") or []
        if not items:
            raise ExternalServiceError("没有找到可访问的 YouTube 频道。", retryable=False)
        item = items[0]
        playlist_id = item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
        if not playlist_id:
            raise ExternalServiceError("频道响应缺少 uploads 播放列表。", retryable=False)
        statistics = item.get("statistics", {})
        return ChannelResource(
            channel_id=str(item["id"]),
            title=str(item.get("snippet", {}).get("title", "")),
            uploads_playlist_id=str(playlist_id),
            raw=item,
            view_count=optional_int(statistics.get("viewCount")),
            subscriber_count=optional_int(statistics.get("subscriberCount")),
            video_count=optional_int(statistics.get("videoCount")),
            hidden_subscriber_count=(
                bool(statistics.get("hiddenSubscriberCount"))
                if "hiddenSubscriberCount" in statistics
                else None
            ),
        )

    def list_upload_video_ids(
        self,
        uploads_playlist_id: str,
        *,
        published_after: datetime | None = None,
        max_pages: int = 20,
    ) -> list[str]:
        video_ids: list[str] = []
        page_token: str | None = None
        stop = False
        for _ in range(max_pages):
            parameters: dict[str, Any] = {
                "part": "contentDetails,snippet",
                "playlistId": uploads_playlist_id,
                "maxResults": 50,
            }
            if page_token:
                parameters["pageToken"] = page_token
            response = self.execute(self.service.playlistItems().list(**parameters))
            for item in response.get("items") or []:
                published_at = parse_api_datetime(
                    item.get("contentDetails", {}).get("videoPublishedAt")
                    or item.get("snippet", {}).get("publishedAt")
                )
                if published_after and published_at and published_at < published_after:
                    stop = True
                    break
                video_id = item.get("contentDetails", {}).get("videoId")
                if video_id:
                    video_ids.append(str(video_id))
            page_token = response.get("nextPageToken")
            if stop or not page_token:
                break
        return list(dict.fromkeys(video_ids))

    def list_videos(
        self, video_ids: Iterable[str], *, parts: Iterable[str] | None = None
    ) -> list[VideoResource]:
        unique_ids = list(dict.fromkeys(str(item) for item in video_ids if item))
        requested_parts = tuple(parts or ("snippet", "contentDetails", "status", "statistics"))
        resources: list[VideoResource] = []
        for start in range(0, len(unique_ids), 50):
            batch = unique_ids[start : start + 50]
            response = self.execute(
                self.service.videos().list(part=",".join(requested_parts), id=",".join(batch))
            )
            resources.extend(self._parse_video(item) for item in response.get("items") or [])
        return resources

    @staticmethod
    def _parse_video(item: dict[str, Any]) -> VideoResource:
        snippet = item.get("snippet", {})
        statistics = item.get("statistics", {})
        published_at = parse_api_datetime(snippet.get("publishedAt"))
        if published_at is None:
            raise ExternalServiceError(
                f"视频 {item.get('id', '<unknown>')} 缺少发布时间。", retryable=False
            )
        return VideoResource(
            video_id=str(item["id"]),
            channel_id=str(snippet.get("channelId", "")),
            title=str(snippet.get("title", "")),
            published_at=published_at,
            duration=item.get("contentDetails", {}).get("duration"),
            privacy_status=item.get("status", {}).get("privacyStatus"),
            view_count=optional_int(statistics.get("viewCount")),
            like_count=optional_int(statistics.get("likeCount")),
            comment_count=optional_int(statistics.get("commentCount")),
            raw=item,
        )
