"""应用装配层：在公共层创建客户端，并注入业务模块。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from yfd_channel_history.analytics import ChannelAnalyticsCollector
from yfd_channel_history.manifest import (
    BUSINESS_TABLE_CONFIG_KEYS as CHANNEL_TABLE_CONFIG_KEYS,
)
from yfd_channel_history.manifest import (
    DEFAULT_CHANNEL_HISTORY_MAPPING,
    DEFAULT_VIDEO_HISTORY_MAPPING,
    DEFAULT_VIDEO_MAIN_MAPPING,
)
from yfd_channel_history.manifest import MANIFEST as CHANNEL_MANIFEST
from yfd_channel_history.manifest import OUTPUT_FIELD_IDS as CHANNEL_OUTPUT_FIELD_IDS
from yfd_channel_history.manifest import TASK_ID as CHANNEL_TASK_ID
from yfd_channel_history.runtime import ChannelHistoryRuntimePlan
from yfd_channel_history.service import ChannelHistoryConfig, ChannelHistoryService
from yfd_channel_history.task import ChannelHistoryDailyTask
from yfd_latest_video_tracker.analytics import VideoAnalyticsCollector
from yfd_latest_video_tracker.manifest import (
    API_FIELD_IDS,
    BUSINESS_TABLE_CONFIG_KEYS,
    DEFAULT_COMPARISON_FIELD_MAPPING,
    DEFAULT_MAIN_FIELD_MAPPING,
    DEFAULT_SNAPSHOT_FIELD_MAPPING,
    IMPLEMENTED_BUSINESS_TABLES,
    MANIFEST,
)
from yfd_latest_video_tracker.preview import LatestVideoPreviewService
from yfd_latest_video_tracker.reporting import VideoReachReportingCollector
from yfd_latest_video_tracker.service import (
    LatestTrackerConfig,
    LatestVideoTrackerService,
    TableSyncConfig,
)
from yfd_latest_video_tracker.task import LatestVideoTrackingTask

from youtube_feishu_dashboard.api.feishu.client import FeishuClient
from youtube_feishu_dashboard.api.feishu.config_center import (
    ConfigSnapshot,
    FeishuConfigCenter,
    ModuleFieldMapping,
)
from youtube_feishu_dashboard.api.youtube.analytics_api import YouTubeAnalyticsClient
from youtube_feishu_dashboard.api.youtube.auth import YouTubeCredentialProvider
from youtube_feishu_dashboard.api.youtube.data_api import YouTubeDataClient
from youtube_feishu_dashboard.api.youtube.reporting_api import YouTubeReportingClient
from youtube_feishu_dashboard.catalog.field_catalog import FieldCatalog
from youtube_feishu_dashboard.core.errors import ConfigurationError, DashboardError
from youtube_feishu_dashboard.core.settings import Settings
from youtube_feishu_dashboard.core.time import utc_now
from youtube_feishu_dashboard.db.database import Database
from youtube_feishu_dashboard.db.repositories import SqlAlchemyStorage
from youtube_feishu_dashboard.scheduler.runner import Scheduler
from youtube_feishu_dashboard.services.archive import ArchiveService
from youtube_feishu_dashboard.services.dynamic_mapping import (
    DynamicModulePlan,
    DynamicModulePlanCompiler,
)
from youtube_feishu_dashboard.services.feishu_records import FeishuRecordService
from youtube_feishu_dashboard.services.sync_validation import (
    BusinessTableSyncValidator,
    validate_field_execution_policy,
)
from youtube_feishu_dashboard.services.tracking_video_config import (
    inspect_tracking_video_selection,
    load_tracking_video_selection,
)


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
        feishu, app_token = self._build_feishu_client()
        snapshot = self._load_remote_config_if_available(feishu, app_token)
        project_config = snapshot.project_config if snapshot else {}
        account_config = snapshot.account_config if snapshot else {}
        tracking_selection = load_tracking_video_selection(account_config)

        interval_minutes = positive_int(
            project_config.get("latest_interval_minutes"), settings.latest_interval_minutes
        )
        tracking_days = positive_int(
            project_config.get("latest_tracking_days"), settings.latest_tracking_days
        )
        analytics_interval_hours = positive_int(
            project_config.get("latest_analytics_interval_hours"),
            settings.latest_analytics_interval_hours,
        )
        reporting_interval_hours = positive_int(
            project_config.get("latest_reporting_interval_hours"),
            settings.latest_reporting_interval_hours,
        )
        hourly_tracking_hours = positive_int(
            project_config.get("latest_hourly_tracking_hours"),
            settings.latest_hourly_tracking_hours,
        )
        daily_collection_hour = hour_of_day(
            project_config.get("latest_daily_collection_hour"),
            settings.latest_daily_collection_hour,
        )
        resolved_table_ids = self._latest_table_ids(account_config)
        table_ids = {
            name: required(table_id, f"{name} Table ID")
            for name, table_id in resolved_table_ids.items()
        }
        module_mappings = self._latest_mappings(snapshot, table_ids)
        runtime_catalog = self._runtime_catalog(snapshot)
        dynamic_plan = self._compile_latest_dynamic_plan(
            feishu=feishu,
            app_token=app_token,
            catalog=runtime_catalog,
            table_ids=table_ids,
            mappings=module_mappings,
        )
        channel_id = (
            optional_text(account_config.get("youtube_channel_id")) or settings.youtube_channel_id
        )

        request_plan = dynamic_plan.request_plan
        credentials = YouTubeCredentialProvider(
            settings.youtube_client_secret_path,
            settings.youtube_token_path,
        )
        analytics = (
            VideoAnalyticsCollector(
                youtube=YouTubeAnalyticsClient.from_credentials(credentials),
                storage=self.storage,
                catalog=runtime_catalog,
                field_ids=dynamic_plan.analytics_field_ids,
                channel_id=channel_id,
            )
            if dynamic_plan.analytics_field_ids
            else None
        )
        reporting = (
            VideoReachReportingCollector(
                youtube=YouTubeReportingClient.from_credentials(credentials),
                storage=self.storage,
                catalog=runtime_catalog,
                field_ids=dynamic_plan.reporting_field_ids,
                cache_directory=(
                    settings.project_root
                    / "data"
                    / "reporting"
                    / "channel_reach_basic_a1"
                ),
                lookback_days=tracking_days + 2,
            )
            if dynamic_plan.reporting_field_ids
            else None
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
                main_table=TableSyncConfig(
                    table_id=table_ids["视频追踪主表"],
                    field_mapping=dynamic_plan.require_table("视频追踪主表").field_mapping,
                    table_name="视频追踪主表",
                ),
                snapshot_table=TableSyncConfig(
                    table_id=table_ids["视频实时快照表"],
                    field_mapping=dynamic_plan.require_table("视频实时快照表").field_mapping,
                    table_name="视频实时快照表",
                ),
                comparison_table=TableSyncConfig(
                    table_id=table_ids["视频同期对比表"],
                    field_mapping=dynamic_plan.require_table("视频同期对比表").field_mapping,
                    table_name="视频同期对比表",
                ),
                timezone=settings.timezone,
                tracking_video_ids=tracking_selection.video_ids,
                analytics_interval_hours=analytics_interval_hours,
                reporting_interval_hours=reporting_interval_hours,
                hourly_tracking_hours=hourly_tracking_hours,
                daily_collection_hour=daily_collection_hour,
            ),
            request_plan=request_plan,
            dynamic_plan=dynamic_plan,
            analytics=analytics,
            reporting=reporting,
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
        if boolean_value(project_config.get("channel_history_enabled"), default=False):
            channel_table_ids = self._channel_history_table_ids(account_config)
            channel_plan = ChannelHistoryRuntimePlan.compile(
                catalog=runtime_catalog,
                table_ids=channel_table_ids,
                mappings=module_mappings,
                raw_fields_by_table_id={
                    table_id: feishu.list_fields(app_token, table_id)
                    for table_id in channel_table_ids.values()
                },
            )
            channel_service = ChannelHistoryService(
                youtube=YouTubeDataClient.from_credentials(credentials),
                analytics=ChannelAnalyticsCollector(
                    YouTubeAnalyticsClient.from_credentials(credentials)
                ),
                storage=self.storage,
                records=FeishuRecordService(feishu, self.storage, app_token),
                config=ChannelHistoryConfig(
                    channel_id=channel_id,
                    runtime_plan=channel_plan,
                    timezone=(
                        optional_text(project_config.get("channel_history_timezone"))
                        or settings.timezone
                    ),
                    ranking_window_days=positive_int(
                        project_config.get("channel_history_ranking_window_days"), 7
                    ),
                    analytics_lookback_days=7,
                ),
            )
            scheduler.register(
                ChannelHistoryDailyTask(
                    channel_service,
                    daily_collection_hour=hour_of_day(
                        project_config.get("channel_history_daily_collection_hour"), 8
                    ),
                    interval_seconds=3600,
                    lock_ttl_seconds=max(1800, settings.scheduler_lock_ttl_seconds),
                )
            )
        else:
            scheduler.disable(CHANNEL_TASK_ID)
        return scheduler

    def preview_latest_video(self, *, channel_id: str | None = None) -> dict[str, Any]:
        """只读预览手动指定的视频；不写飞书业务表或业务数据库。"""
        feishu, app_token = self._build_feishu_client()
        snapshot = self._load_remote_config_if_available(feishu, app_token)
        project_config = snapshot.project_config if snapshot else {}
        account_config = snapshot.account_config if snapshot else {}
        tracking_selection = load_tracking_video_selection(account_config)
        resolved_table_ids = self._latest_table_ids(account_config)
        table_ids = {
            name: required(table_id, f"{name} Table ID")
            for name, table_id in resolved_table_ids.items()
        }
        mappings = self._latest_mappings(snapshot, table_ids)
        dynamic_plan = self._compile_latest_dynamic_plan(
            feishu=feishu,
            app_token=app_token,
            catalog=self._runtime_catalog(snapshot),
            table_ids=table_ids,
            mappings=mappings,
        )
        credentials = YouTubeCredentialProvider(
            self.settings.youtube_client_secret_path,
            self.settings.youtube_token_path,
        )
        return LatestVideoPreviewService(
            youtube=YouTubeDataClient.from_credentials(credentials),
            request_plan=dynamic_plan.request_plan,
            channel_id=channel_id
            or optional_text(account_config.get("youtube_channel_id"))
            or self.settings.youtube_channel_id,
            tracking_days=positive_int(
                project_config.get("latest_tracking_days"), self.settings.latest_tracking_days
            ),
            tracking_video_ids=tracking_selection.video_ids,
            dynamic_plan=dynamic_plan,
        ).preview(utc_now())

    def validate_latest_video_sync(self) -> dict[str, Any]:
        """只读检查三张业务表、远端映射和当前代码实现范围。"""
        feishu, app_token = self._build_feishu_client()
        snapshot = self._load_remote_config_if_available(feishu, app_token)
        account_config = snapshot.account_config if snapshot else {}
        table_ids = self._latest_table_ids(account_config)
        complete_table_ids = {
            name: table_id for name, table_id in table_ids.items() if table_id is not None
        }
        mappings = self._latest_mappings(snapshot, complete_table_ids)
        result = BusinessTableSyncValidator(
            gateway=feishu,
            app_token=app_token,
        ).validate(
            module_id=MANIFEST.module_id,
            table_ids=table_ids,
            mappings=mappings,
            implemented_tables=set(IMPLEMENTED_BUSINESS_TABLES),
        )
        result["config_source"] = snapshot.source if snapshot else "local_environment"
        result["local_config_cache_updated"] = bool(snapshot and snapshot.source == "feishu")
        fallback_error = snapshot.fallback_error if snapshot else None
        result["config_fallback_error"] = fallback_error
        remote_config_current = not bool(
            snapshot and snapshot.source == "cache" and fallback_error
        )
        result["summary"]["remote_config_current"] = remote_config_current
        if not remote_config_current:
            result["summary"]["safe_to_run_full_three_table_sync"] = False
            fallback_reason = (
                "未能验证飞书当前配置，正在使用本地缓存："
                + str(fallback_error)
            )
            if fallback_reason not in result["summary"]["blocking_reasons"]:
                result["summary"]["blocking_reasons"].append(fallback_reason)

        runtime_catalog = self._runtime_catalog(snapshot)
        execution_report = validate_field_execution_policy(
            catalog=runtime_catalog,
            module_id=MANIFEST.module_id,
            mappings=mappings,
            module_code_field_ids=set(MANIFEST.output_field_ids),
            supported_api_sources={"data_api", "analytics_api", "reporting_api"},
        )
        result["field_execution_validation"] = execution_report
        field_execution_ready = bool(execution_report["ready"])
        result["summary"]["field_execution_ready"] = field_execution_ready
        if not field_execution_ready:
            result["summary"]["safe_to_run_full_three_table_sync"] = False
            for reason in execution_report["blocking_reasons"]:
                if reason not in result["summary"]["blocking_reasons"]:
                    result["summary"]["blocking_reasons"].append(reason)

        tracking_report = inspect_tracking_video_selection(account_config)
        result["tracking_video_config"] = tracking_report
        tracking_ready = bool(tracking_report["ready"])
        result["summary"]["tracking_video_config_ready"] = tracking_ready
        if not tracking_ready:
            result["summary"]["safe_to_run_full_three_table_sync"] = False
            tracking_error = str(tracking_report["error"])
            if tracking_error not in result["summary"]["blocking_reasons"]:
                result["summary"]["blocking_reasons"].append(tracking_error)
        try:
            if len(complete_table_ids) != len(BUSINESS_TABLE_CONFIG_KEYS):
                raise ConfigurationError("三张业务表的 Table ID 尚未全部配置。")
            dynamic_plan = self._compile_latest_dynamic_plan(
                feishu=feishu,
                app_token=app_token,
                catalog=runtime_catalog,
                table_ids=complete_table_ids,
                mappings=mappings,
            )
            result["dynamic_plan"] = dynamic_plan.as_report()
            result["summary"]["dynamic_plan_ready"] = True
        except DashboardError as exc:
            result["dynamic_plan"] = {"ready": False, "error": str(exc)}
            result["summary"]["dynamic_plan_ready"] = False
            result["summary"]["safe_to_run_full_three_table_sync"] = False
            if str(exc) not in result["summary"]["blocking_reasons"]:
                result["summary"]["blocking_reasons"].append(str(exc))
        return result

    def validate_channel_history_sync(self) -> dict[str, Any]:
        """只读检查频道每日统计的三张业务表、映射和运行参数。"""
        feishu, app_token = self._build_feishu_client()
        snapshot = self._load_remote_config_if_available(feishu, app_token)
        project_config = snapshot.project_config if snapshot else {}
        account_config = snapshot.account_config if snapshot else {}
        table_ids = self._channel_history_table_ids_optional(account_config)
        complete_table_ids = {
            name: table_id for name, table_id in table_ids.items() if table_id is not None
        }
        mappings = self._channel_history_mappings(snapshot, complete_table_ids)
        result = BusinessTableSyncValidator(
            gateway=feishu,
            app_token=app_token,
        ).validate(
            module_id=CHANNEL_MANIFEST.module_id,
            table_ids=table_ids,
            mappings=mappings,
            implemented_tables=set(CHANNEL_TABLE_CONFIG_KEYS),
        )
        result["summary"].pop("current_snapshot_sync_ready", None)
        result["config_source"] = snapshot.source if snapshot else "local_environment"
        result["local_config_cache_updated"] = bool(
            snapshot and snapshot.source == "feishu"
        )
        fallback_error = snapshot.fallback_error if snapshot else None
        result["config_fallback_error"] = fallback_error
        remote_config_current = not bool(
            snapshot and snapshot.source == "cache" and fallback_error
        )
        result["summary"]["remote_config_current"] = remote_config_current
        if not remote_config_current:
            result["summary"]["safe_to_run_full_three_table_sync"] = False
            reason = "未能验证飞书当前配置，正在使用本地缓存：" + str(
                fallback_error
            )
            if reason not in result["summary"]["blocking_reasons"]:
                result["summary"]["blocking_reasons"].append(reason)

        runtime_catalog = self._runtime_catalog(snapshot)
        execution_report = validate_field_execution_policy(
            catalog=runtime_catalog,
            module_id=CHANNEL_MANIFEST.module_id,
            mappings=mappings,
            module_code_field_ids=set(CHANNEL_MANIFEST.output_field_ids),
            supported_api_sources={"data_api", "analytics_api"},
        )
        result["field_execution_validation"] = execution_report
        result["summary"]["field_execution_ready"] = bool(execution_report["ready"])
        if not execution_report["ready"]:
            result["summary"]["safe_to_run_full_three_table_sync"] = False
            for reason in execution_report["blocking_reasons"]:
                if reason not in result["summary"]["blocking_reasons"]:
                    result["summary"]["blocking_reasons"].append(reason)

        result["runtime_config"] = {
            "module_switch_enabled": boolean_value(
                project_config.get("channel_history_enabled"), default=False
            ),
            "timezone": optional_text(project_config.get("channel_history_timezone"))
            or self.settings.timezone,
            "daily_collection_hour": hour_of_day(
                project_config.get("channel_history_daily_collection_hour"), 8
            ),
            "ranking_window_days": positive_int(
                project_config.get("channel_history_ranking_window_days"), 7
            ),
            "revenue_window_days": 28,
            "video_scope": optional_text(
                project_config.get("channel_history_video_scope")
            )
            or "long_only",
            "ranking_basis": optional_text(
                project_config.get("channel_history_ranking_basis")
            )
            or "data_api_snapshot_delta",
        }
        try:
            if len(complete_table_ids) != len(CHANNEL_TABLE_CONFIG_KEYS):
                raise ConfigurationError("频道统计的三张业务表 Table ID 尚未全部配置。")
            channel_plan = ChannelHistoryRuntimePlan.compile(
                catalog=runtime_catalog,
                table_ids=complete_table_ids,
                mappings=mappings,
                raw_fields_by_table_id={
                    table_id: feishu.list_fields(app_token, table_id)
                    for table_id in complete_table_ids.values()
                },
            )
            result["runtime_plan"] = channel_plan.as_report()
            result["summary"]["runtime_plan_ready"] = True
        except DashboardError as exc:
            result["runtime_plan"] = {"ready": False, "error": str(exc)}
            result["summary"]["runtime_plan_ready"] = False
            result["summary"]["safe_to_run_full_three_table_sync"] = False
            if str(exc) not in result["summary"]["blocking_reasons"]:
                result["summary"]["blocking_reasons"].append(str(exc))
        result["summary"]["safe_to_enable_module"] = result["summary"][
            "safe_to_run_full_three_table_sync"
        ]
        return result

    def _latest_table_ids(
        self,
        account_config: dict[str, Any],
    ) -> dict[str, str | None]:
        configured = {
            "视频追踪主表": self.settings.feishu_latest_video_main_table_id,
            "视频实时快照表": self.settings.feishu_latest_video_snapshot_table_id
            or self.settings.feishu_latest_video_table_id,
            "视频同期对比表": self.settings.feishu_latest_video_comparison_table_id,
        }
        resolved = {
            table_name: optional_text(account_config.get(config_key)) or configured[table_name]
            for table_name, config_key in BUSINESS_TABLE_CONFIG_KEYS.items()
        }
        return resolved

    def _channel_history_table_ids(self, account_config: dict[str, Any]) -> dict[str, str]:
        configured = self._channel_history_table_ids_optional(account_config)
        return {
            table_name: required(table_id, f"{table_name} Table ID")
            for table_name, table_id in configured.items()
        }

    def _channel_history_table_ids_optional(
        self, account_config: dict[str, Any]
    ) -> dict[str, str | None]:
        local = {
            "视频主表": self.settings.feishu_channel_video_main_table_id,
            "视频历史数据": self.settings.feishu_channel_video_history_table_id,
            "频道历史数据": self.settings.feishu_channel_history_table_id,
        }
        return {
            table_name: optional_text(account_config.get(config_key))
            or local[table_name]
            for table_name, config_key in CHANNEL_TABLE_CONFIG_KEYS.items()
        }

    def _channel_history_mappings(
        self,
        snapshot: ConfigSnapshot | None,
        table_ids: dict[str, str],
    ) -> tuple[ModuleFieldMapping, ...]:
        if snapshot is not None:
            return snapshot.module_mappings
        defaults = {
            "视频主表": DEFAULT_VIDEO_MAIN_MAPPING,
            "视频历史数据": DEFAULT_VIDEO_HISTORY_MAPPING,
            "频道历史数据": DEFAULT_CHANNEL_HISTORY_MAPPING,
        }
        return tuple(
            ModuleFieldMapping(
                module_id=CHANNEL_MANIFEST.module_id,
                standard_field_id=field_id,
                feishu_column=column,
                target_table_id=table_ids[table_name],
            )
            for table_name, field_mapping in defaults.items()
            if table_name in table_ids
            for field_id, column in field_mapping.items()
        )

    def _latest_mappings(
        self,
        snapshot: ConfigSnapshot | None,
        table_ids: dict[str, str],
    ) -> tuple[ModuleFieldMapping, ...]:
        if snapshot is not None:
            return snapshot.module_mappings
        defaults = {
            "视频追踪主表": DEFAULT_MAIN_FIELD_MAPPING,
            "视频实时快照表": DEFAULT_SNAPSHOT_FIELD_MAPPING,
            "视频同期对比表": DEFAULT_COMPARISON_FIELD_MAPPING,
        }
        return tuple(
            ModuleFieldMapping(
                module_id=MANIFEST.module_id,
                standard_field_id=field_id,
                feishu_column=column,
                target_table_id=table_ids[table_name],
            )
            for table_name, field_mapping in defaults.items()
            if table_name in table_ids
            for field_id, column in field_mapping.items()
        )

    def _runtime_catalog(self, snapshot: ConfigSnapshot | None) -> FieldCatalog:
        if snapshot is None or snapshot.catalog_document is None:
            return self.catalog
        return FieldCatalog(snapshot.catalog_document)

    def _compile_latest_dynamic_plan(
        self,
        *,
        feishu: FeishuClient,
        app_token: str,
        catalog: FieldCatalog,
        table_ids: dict[str, str],
        mappings: tuple[ModuleFieldMapping, ...],
    ) -> DynamicModulePlan:
        raw_fields = {
            table_id: feishu.list_fields(app_token, table_id)
            for table_id in table_ids.values()
        }
        return DynamicModulePlanCompiler(
            catalog=catalog,
            computed_field_ids=set(MANIFEST.output_field_ids),
            required_data_field_ids=API_FIELD_IDS,
            supported_api_sources={"data_api", "analytics_api", "reporting_api"},
        ).compile(
            module_id=MANIFEST.module_id,
            entity_level="video",
            table_ids=table_ids,
            mappings=mappings,
            raw_fields_by_table_id=raw_fields,
        )

    def _build_feishu_client(self) -> tuple[FeishuClient, str]:
        settings = self.settings
        app_id = required(settings.feishu_app_id, "YFD_FEISHU_APP_ID")
        app_secret = required(
            settings.feishu_app_secret.get_secret_value() if settings.feishu_app_secret else None,
            "YFD_FEISHU_APP_SECRET",
        )
        app_token = required(settings.feishu_base_token, "YFD_FEISHU_BASE_TOKEN")
        return (
            FeishuClient(
                app_id=app_id,
                app_secret=app_secret,
                api_base_url=settings.feishu_api_base_url,
            ),
            app_token,
        )

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
            api_field_table_id=settings.feishu_api_field_table_id,
            cache_ttl_minutes=settings.config_cache_ttl_minutes,
            additional_field_ids={
                *MANIFEST.output_field_ids,
                *CHANNEL_OUTPUT_FIELD_IDS,
            },
        ).load()

    def close(self) -> None:
        self.database.dispose()


def required(value: str | None, label: str) -> str:
    if value is None or not str(value).strip():
        raise ConfigurationError(f"缺少配置：{label}")
    return str(value).strip()


def boolean_value(value: object, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "on", "是", "启用"}:
        return True
    if normalized in {"false", "0", "no", "off", "否", "停用"}:
        return False
    raise ConfigurationError(f"无法识别布尔配置值：{value!r}")


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


def hour_of_day(value: Any, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(str(value))
    except ValueError as exc:
        raise ConfigurationError(f"整点小时必须是整数：{value}") from exc
    if parsed < 0 or parsed > 23:
        raise ConfigurationError(f"整点小时必须在 0 到 23 之间：{value}")
    return parsed
