"""YouTube OAuth Token 的唯一管理入口。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from youtube_feishu_dashboard.core.errors import AuthenticationError, ConfigurationError

YOUTUBE_READONLY_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
YT_ANALYTICS_READONLY_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
YT_ANALYTICS_MONETARY_SCOPE = "https://www.googleapis.com/auth/yt-analytics-monetary.readonly"
DEFAULT_SCOPES = (
    YOUTUBE_READONLY_SCOPE,
    YT_ANALYTICS_READONLY_SCOPE,
    YT_ANALYTICS_MONETARY_SCOPE,
)


class YouTubeCredentialProvider:
    def __init__(
        self,
        client_secret_file: Path,
        token_file: Path,
        *,
        scopes: tuple[str, ...] = DEFAULT_SCOPES,
    ) -> None:
        self.client_secret_file = client_secret_file
        self.token_file = token_file
        self.scopes = scopes

    def credentials(self) -> Credentials:
        if not self.token_file.is_file():
            raise AuthenticationError(
                f"未找到 YouTube Token：{self.token_file}。请先运行 yfd auth youtube。"
            )
        try:
            credentials = cast(
                Credentials,
                Credentials.from_authorized_user_file(  # type: ignore[no-untyped-call]
                    str(self.token_file), scopes=list(self.scopes)
                ),
            )
            if credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())  # type: ignore[no-untyped-call]
                self._save(credentials)
        except Exception as exc:
            raise AuthenticationError(f"读取或刷新 YouTube Token 失败：{exc}") from exc
        if not credentials.valid:
            raise AuthenticationError("YouTube Token 无效或缺少 refresh token，请重新授权。")
        return credentials

    def authorize_local(self, *, open_browser: bool = True, port: int = 0) -> Credentials:
        if not self.client_secret_file.is_file():
            raise ConfigurationError(f"未找到 OAuth 客户端文件：{self.client_secret_file}")
        flow = InstalledAppFlow.from_client_secrets_file(
            str(self.client_secret_file), scopes=list(self.scopes)
        )
        credentials = cast(
            Credentials,
            flow.run_local_server(
                host="localhost",
                port=port,
                open_browser=open_browser,
                access_type="offline",
                prompt="consent",
            ),
        )
        self._save(credentials)
        return credentials

    def token_status(self) -> dict[str, Any]:
        if not self.token_file.is_file():
            return {"exists": False, "valid": False, "path": str(self.token_file)}
        try:
            credentials = cast(
                Credentials,
                Credentials.from_authorized_user_file(  # type: ignore[no-untyped-call]
                    str(self.token_file), scopes=list(self.scopes)
                ),
            )
            return {
                "exists": True,
                "valid": bool(credentials.valid),
                "expired": bool(credentials.expired),
                "has_refresh_token": bool(credentials.refresh_token),
                "path": str(self.token_file),
            }
        except Exception as exc:
            return {
                "exists": True,
                "valid": False,
                "path": str(self.token_file),
                "error": type(exc).__name__,
            }

    def _save(self, credentials: Credentials) -> None:
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.token_file.with_suffix(self.token_file.suffix + ".tmp")
        token_json = credentials.to_json()  # type: ignore[no-untyped-call]
        temporary.write_text(cast(str, token_json), encoding="utf-8")
        if os.name != "nt":
            temporary.chmod(0o600)
        temporary.replace(self.token_file)
