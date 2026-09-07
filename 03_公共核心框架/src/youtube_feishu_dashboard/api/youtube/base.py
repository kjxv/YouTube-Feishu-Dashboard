from __future__ import annotations

from typing import Any

from googleapiclient.errors import HttpError
from httplib2 import HttpLib2Error

from youtube_feishu_dashboard.core.errors import ExternalServiceError


class GoogleApiClientBase:
    def __init__(self, service: Any) -> None:
        self.service = service

    @staticmethod
    def execute(request: Any) -> dict[str, Any]:
        try:
            result = request.execute()
        except HttpError as exc:
            status = getattr(exc.resp, "status", None)
            retryable = status in {408, 429, 500, 502, 503, 504}
            raise ExternalServiceError(
                f"YouTube API 请求失败（HTTP {status or 'unknown'}）：{exc.reason}",
                retryable=retryable,
            ) from exc
        except (HttpLib2Error, TimeoutError, OSError) as exc:
            raise ExternalServiceError(
                f"YouTube API 网络请求超时或中断：{type(exc).__name__}",
                retryable=True,
            ) from exc
        if not isinstance(result, dict):
            raise ExternalServiceError("YouTube API 返回了无法识别的数据格式。", retryable=False)
        return result
