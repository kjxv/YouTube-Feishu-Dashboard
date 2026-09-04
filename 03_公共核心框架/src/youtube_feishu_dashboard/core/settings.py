"""环境变量与 `.env` 配置；敏感值不会进入版本化配置。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from youtube_feishu_dashboard.core.paths import discover_project_root, resolve_from_root


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="YFD_",
        env_file=None,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    project_root: Path = Field(default_factory=discover_project_root, exclude=True)
    environment: Literal["development", "production", "test"] = "development"
    timezone: str = "Asia/Shanghai"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    database_url: str = "sqlite:///data/yfd.db"

    youtube_client_secret_file: Path = Path("secrets/youtube_client_secret.json")
    youtube_token_file: Path = Path("secrets/youtube_token.json")
    youtube_channel_id: str | None = None

    feishu_app_id: str | None = None
    feishu_app_secret: SecretStr | None = None
    feishu_api_base_url: str = "https://open.feishu.cn/open-apis"
    feishu_base_token: str | None = None
    feishu_api_field_table_id: str | None = None
    feishu_module_mapping_table_id: str | None = None
    feishu_project_config_table_id: str | None = None
    feishu_account_config_table_id: str | None = None
    feishu_latest_video_table_id: str | None = None
    feishu_latest_video_main_table_id: str | None = None
    feishu_latest_video_snapshot_table_id: str | None = None
    feishu_latest_video_comparison_table_id: str | None = None

    latest_interval_minutes: int = 60
    latest_analytics_interval_hours: int = 24
    latest_reporting_interval_hours: int = 24
    latest_tracking_days: int = 7
    scheduler_lock_ttl_seconds: int = 900
    config_cache_ttl_minutes: int = 1440
    feishu_archive_row_threshold: int = 19000
    https_proxy: str | None = None

    @field_validator(
        "latest_interval_minutes",
        "latest_analytics_interval_hours",
        "latest_reporting_interval_hours",
        "latest_tracking_days",
        "scheduler_lock_ttl_seconds",
        "config_cache_ttl_minutes",
        "feishu_archive_row_threshold",
    )
    @classmethod
    def must_be_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("必须大于 0")
        return value

    @field_validator(
        "youtube_channel_id",
        "feishu_app_id",
        "feishu_base_token",
        "feishu_api_field_table_id",
        "feishu_module_mapping_table_id",
        "feishu_project_config_table_id",
        "feishu_account_config_table_id",
        "feishu_latest_video_table_id",
        "feishu_latest_video_main_table_id",
        "feishu_latest_video_snapshot_table_id",
        "feishu_latest_video_comparison_table_id",
        "https_proxy",
        mode="before",
    )
    @classmethod
    def empty_string_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def youtube_client_secret_path(self) -> Path:
        return resolve_from_root(self.youtube_client_secret_file, self.project_root)

    @property
    def youtube_token_path(self) -> Path:
        return resolve_from_root(self.youtube_token_file, self.project_root)

    @property
    def sqlalchemy_url(self) -> str:
        """把相对 SQLite 路径稳定解析到仓库根目录，其他数据库 URL 原样返回。"""
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix) or self.database_url.startswith("sqlite:////"):
            return self.database_url
        relative = self.database_url[len(prefix) :]
        if relative == ":memory:":
            return self.database_url
        absolute = resolve_from_root(Path(relative), self.project_root)
        return f"sqlite:///{absolute.as_posix()}"

    def safe_database_url(self) -> str:
        """用于日志的脱敏数据库 URL。"""
        parts = urlsplit(self.sqlalchemy_url)
        if not parts.password:
            return self.sqlalchemy_url
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        username = parts.username or ""
        netloc = f"{username}:***@{host}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))

    @classmethod
    def load(cls, project_root: Path | None = None) -> Settings:
        root = (project_root or discover_project_root()).resolve()
        return cls(_env_file=root / ".env", project_root=root)  # type: ignore[call-arg]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.load()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
