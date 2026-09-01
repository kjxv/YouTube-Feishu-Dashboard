"""应用装配层：在公共层创建客户端，并注入业务模块。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from yfd_latest_video_tracker.manifest import API_FIELD_IDS, MANIFEST
from yfd_latest_video_tracker.service import (
    LatestTrackerConfig,
    LatestVideoTrackerService,
    merge_field_mapping,
)
from yfd_latest_video_tracker.task import LatestVideoTrackingTask

from youtube_feishu_dashboard.api.feishu.client import FeishuClient
from youtube_feishu_dashboard.api.feishu.config_center import ConfigSnapshot, FeishuConfigCenter
from youtube_feishu_dashboard.api.youtube.auth import YouTubeCredentialProvider
from youtube_feishu_dashboard.api.youtube.data_api import YouTubeDataClient
from youtube_feishu_dashboard.catalog.field_catalog import FieldCatalog
from youtube_feishu_dashboard.core.errors import ConfigurationError
from youtube_feishu_dashboard.core.settings import Settings
from youtube_feishu_dashboard.db.database import Database
from youtube_feishu_dashboard.db.repositories import SqlAlchemyStorage
from youtube_feishu_dashboard.scheduler.runner import Scheduler
from youtube_feishu_dashboard.services.archive import ArchiveService
from youtube_feishu_dashboard.services.feishu_records import FeishuRecordService


@dataclass(slots=True)
class Application:
    settings: Settings
    database: Database
    storage: SqlAlchemyStorage
    catalog: FieldCatalog

    @classmethod
    def build(cls, settings: Settings | None = None) -> Application:
        resolved = settings or Settings.load()
        database = Database(resolved.sqlalchemy_url)
        return cls(
            settings=resolved,
            database=database,
            storage=SqlAlchemyStorage(database),
            catalog=FieldCatalog.load_builtin(),
        )

    def build_scheduler(self) -> Scheduler:
        settings = self.settings
        app_id = required(settings.feishu_app_id, "YFD_FEISHU_APP_ID")
        app_secret = required(
            settings.feishu_app_secret.get_secret_value() if settings.feishu_app_secret else None,
            "YFD_FEISHU_APP_SECRET",
        )
        app_token = required(settings.feishu_base_token, "YFD_FEISHU_BASE_TOKEN")
        feishu = FeishuClient(
            app_id=app_id,
            app_secret=app_secret,
            api_base_url=settings.feishu_api_base_url,
        )
        snapshot = self._load_remote_config_if_available(feishu, app_token)
        project_config = snapshot.project_config if snapshot else {}
        account_config = snapshot.account_config if snapshot else {}
        module_mappings = snapshot.module_mappings if snapshot else ()

        interval_minutes = positive_int(
            project_config.get("latest_interval_minutes"), settings.latest_interval_minutes
        )
        tracking_days = positive_int(
            project_config.get("latest_tracking_days"), settings.latest_tracking_days
        )
        mapping_overrides = {
            item.standard_field_id: item.feishu_column
            for item in module_mappings
            if item.enabled and item.module_id == MANIFEST.module_id
        }
        if snapshot is not None and not mapping_overrides:
            raise ConfigurationError("飞书配置中心没有最新视频模块的已启用字段映射。")
        mapped_table_ids = {
            item.target_table_id
            for item in module_mappings
            if item.enabled and item.module_id == MANIFEST.module_id and item.target_table_id
        }
        if len(mapped_table_ids) > 1:
            raise ConfigurationError("最新视频模块的字段映射指向了多个目标表。")
        target_table_id = next(iter(mapped_table_ids), None)
        target_table_id = (
            target_table_id
            or optional_text(account_config.get("latest_video_snapshot_table_id"))
            or optional_text(account_config.get("latest_video_table_id"))
            or settings.feishu_latest_video_snapshot_table_id
            or settings.feishu_latest_video_table_id
        )
        target_table_id = required(target_table_id, "最新视频目标 Table ID")
        channel_id = (
            optional_text(account_config.get("youtube_channel_id")) or settings.youtube_channel_id
        )

        request_plan = self.catalog.build_request_plan(MANIFEST.module_id, list(API_FIELD_IDS))
        credentials = YouTubeCredentialProvider(
            settings.youtube_client_secret_path,
            settings.youtube_token_path,
        )
        service = LatestVideoTrackerService(
            youtube=YouTubeDataClient.from_credentials(credentials),
            storage=self.storage,
            records=FeishuRecordService(feishu, self.storage, app_token),
            archive=ArchiveService(
                gateway=feishu,
                storage=self.storage,
                app_token=app_token,
                row_threshold=settings.feishu_archive_row_threshold,
            ),
            config=LatestTrackerConfig(
                channel_id=channel_id,
                tracking_days=tracking_days,
                interval_minutes=interval_minutes,
                target_table_id=target_table_id,
                field_mapping=merge_field_mapping(mapping_overrides, use_defaults=snapshot is None),
                timezone=settings.timezone,
            ),
            request_plan=request_plan,
        )
        scheduler = Scheduler(storage=self.storage)
        scheduler.register(
            LatestVideoTrackingTask(
                service,
                request_plan,
                interval_seconds=interval_minutes * 60,
                lock_ttl_seconds=settings.scheduler_lock_ttl_seconds,
            )
        )
        return scheduler

    def _load_remote_config_if_available(
        self, feishu: FeishuClient, app_token: str
    ) -> ConfigSnapshot | None:
        settings = self.settings
        table_ids = (
            settings.feishu_project_config_table_id,
            settings.feishu_account_config_table_id,
            settings.feishu_module_mapping_table_id,
        )
        if not any(table_ids):
            return None
        if not all(table_ids):
            raise ConfigurationError("飞书配置中心的三张公共表 ID 必须同时填写。")
        project_table, account_table, mapping_table = table_ids
        assert project_table and account_table and mapping_table
        return FeishuConfigCenter(
            gateway=feishu,
            storage=self.storage,
            catalog=self.catalog,
            app_token=app_token,
            project_config_table_id=project_table,
            account_config_table_id=account_table,
            module_mapping_table_id=mapping_table,
            cache_ttl_minutes=settings.config_cache_ttl_minutes,
            additional_field_ids=set(MANIFEST.output_field_ids),
        ).load()

    def close(self) -> None:
        self.database.dispose()


def required(value: str | None, label: str) -> str:
    if value is None or not str(value).strip():
        raise ConfigurationError(f"缺少配置：{label}")
    return str(value).strip()


def optional_text(value: Any) -> str | None:
    if value is None or not str(value).strip():
        return None
    return str(value).strip()


def positive_int(value: Any, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(str(value))
    except ValueError as exc:
        raise ConfigurationError(f"配置值必须是整数：{value}") from exc
    if parsed <= 0:
        raise ConfigurationError(f"配置值必须大于 0：{value}")
    return parsed
