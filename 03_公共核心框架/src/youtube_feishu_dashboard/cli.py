from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import asdict
from typing import Any

from yfd_all_videos_current import MANIFEST as ALL_VIDEOS
from yfd_channel_history import MANIFEST as CHANNEL_HISTORY
from yfd_channel_history.feishu_setup import (
    Channel48HourFeishuSetup,
    ChannelHistoryFeishuSwitch,
)
from yfd_channel_history.manifest import TASK_ID as CHANNEL_HISTORY_TASK_ID
from yfd_latest_video_tracker import MANIFEST as LATEST_VIDEO
from yfd_latest_video_tracker.feishu_setup import LatestVideoMilestoneFeishuSetup
from yfd_latest_video_tracker.manifest import TASK_ID as LATEST_VIDEO_TASK_ID

from youtube_feishu_dashboard import __version__
from youtube_feishu_dashboard.api.feishu.client import FeishuClient
from youtube_feishu_dashboard.api.youtube.auth import YouTubeCredentialProvider
from youtube_feishu_dashboard.app import Application, required
from youtube_feishu_dashboard.core.errors import ConfigurationError, DashboardError
from youtube_feishu_dashboard.core.logging import configure_logging
from youtube_feishu_dashboard.core.settings import Settings
from youtube_feishu_dashboard.db.migrations import migration_status, upgrade_database
from youtube_feishu_dashboard.doctor import run_doctor
from youtube_feishu_dashboard.services.catalog_sync import sync_catalog_to_feishu
from youtube_feishu_dashboard.services.config_center_bootstrap import (
    ConfigCenterBootstrapper,
    update_dotenv,
)
from youtube_feishu_dashboard.services.config_center_critical_fields import (
    ConfigCenterCriticalFieldMarker,
)
from youtube_feishu_dashboard.services.config_center_localization import (
    ConfigCenterLocalizationService,
)
from youtube_feishu_dashboard.services.runtime_status import RuntimeStatusTableSetup
from youtube_feishu_dashboard.setup_wizard import initialize_project

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="yfd", description="YouTube 数据中心 V1")
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command")

    setup = subparsers.add_parser("setup", help="首次配置、迁移数据库并初始化字段目录")
    setup.add_argument("--interactive", action="store_true", help="交互填写新的 .env")

    auth = subparsers.add_parser("auth", help="OAuth 授权")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)
    youtube = auth_sub.add_parser("youtube", help="授权 YouTube")
    youtube.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")

    doctor = subparsers.add_parser("doctor", help="检查配置、数据库和凭证")
    doctor.add_argument("--online", action="store_true", help="额外检查飞书在线连接")

    database = subparsers.add_parser("db", help="数据库迁移")
    database_sub = database.add_subparsers(dest="db_command", required=True)
    upgrade = database_sub.add_parser("upgrade", help="迁移到指定版本")
    upgrade.add_argument("revision", nargs="?", default="head")
    database_sub.add_parser("current", help="显示当前迁移版本")

    catalog = subparsers.add_parser("catalog", help="API 字段能力目录")
    catalog_sub = catalog.add_subparsers(dest="catalog_command", required=True)
    catalog_sub.add_parser("sync", help="同步内置字段目录到主数据库")
    catalog_sub.add_parser("show", help="输出内置字段目录 JSON")
    catalog_sub.add_parser("push-feishu", help="按标准字段 ID 幂等同步到飞书字段字典表")

    feishu = subparsers.add_parser("feishu", help="飞书连接与公共配置中心")
    feishu_sub = feishu.add_subparsers(dest="feishu_command", required=True)
    bootstrap = feishu_sub.add_parser("bootstrap-config", help="幂等创建四张公共配置表")
    bootstrap.add_argument(
        "--no-write-env", action="store_true", help="创建成功后不把四个 Table ID 写回 .env"
    )
    feishu_sub.add_parser("localize-config", help="把公共配置表升级为中英文对照版")
    feishu_sub.add_parser("mark-critical-fields", help="在字段说明中标出程序关键字段")
    feishu_sub.add_parser(
        "enable-channel-48h-fields",
        help="幂等补建频道视频主表的48小时字段并启用共享映射",
    )
    feishu_sub.add_parser(
        "enable-latest-milestone-fields",
        help="幂等补建视频追踪主表的1至72小时节点字段并启用共享映射",
    )
    feishu_sub.add_parser(
        "enable-runtime-status",
        help="幂等创建系统运行状态表并把 Table ID 写入 .env",
    )
    channel_switch = feishu_sub.add_parser(
        "set-channel-history-enabled",
        help="幂等开启或关闭频道每日统计任务",
    )
    channel_switch.add_argument("enabled", choices=("true", "false"))

    scheduler = subparsers.add_parser("scheduler", help="统一调度入口")
    scheduler_sub = scheduler.add_subparsers(dest="scheduler_command", required=True)
    scheduler_sub.add_parser("tick", help="运行所有真实到期任务")
    scheduler_sub.add_parser("status", help="只读显示本地任务、运行、锁和心跳状态")
    scheduled_run = scheduler_sub.add_parser(
        "scheduled-run", help="由系统整点唤醒，并按视频分阶段规则检查"
    )
    scheduled_run.add_argument("task_id", nargs="?", default="latest-video-tracker")
    run_once = scheduler_sub.add_parser("run-once", help="立即运行一个任务")
    run_once.add_argument("task_id", nargs="?", default="latest-video-tracker")
    run_once.add_argument("--dry-run", action="store_true", help="只输出计划，不写外部数据")

    modules = subparsers.add_parser("modules", help="显示模块，或执行模块级安全检查")
    modules_sub = modules.add_subparsers(dest="modules_command")
    preview = modules_sub.add_parser("preview", help="只读预览模块将获取的真实 API 数据")
    preview.add_argument("module_id", nargs="?", default=LATEST_VIDEO.module_id)
    preview.add_argument("--channel-id", help="临时指定公开频道 ID；默认使用配置或授权账号")
    validate_sync = modules_sub.add_parser(
        "validate-sync", help="只读检查三张业务表、字段映射和当前可用状态"
    )
    validate_sync.add_argument("module_id", nargs="?", default=LATEST_VIDEO.module_id)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    settings = Settings.load()
    apply_proxy_environment(settings)
    configure_logging(settings.log_level, json_output=settings.environment == "production")
    try:
        return dispatch(args, settings)
    except DashboardError as exc:
        logger.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        print("\n已取消。")
        return 130
    except Exception:
        logger.exception("未处理的运行错误")
        return 1


def dispatch(args: argparse.Namespace, settings: Settings) -> int:
    if args.command == "setup":
        print_json(initialize_project(settings, interactive=args.interactive))
        print("下一步：放置 OAuth 客户端文件，运行 yfd auth youtube，再运行 yfd doctor。")
        return 0
    if args.command == "auth":
        provider = YouTubeCredentialProvider(
            settings.youtube_client_secret_path, settings.youtube_token_path
        )
        provider.authorize_local(open_browser=not args.no_browser)
        print("YouTube 授权完成，Token 已安全保存到 secrets 目录。")
        return 0
    if args.command == "db":
        if args.db_command == "upgrade":
            upgrade_database(settings, args.revision)
            print(f"数据库已迁移到 {args.revision}。")
            return 0
        app = Application.build(settings)
        try:
            current, heads = migration_status(settings, app.database)
            print_json({"current": current, "heads": heads})
        finally:
            app.close()
        return 0

    app = Application.build(settings)
    try:
        if args.command == "doctor":
            result = run_doctor(app, online=args.online)
            print_json(result)
            return 0 if result["ok"] else 2
        if args.command == "catalog":
            if args.catalog_command == "sync":
                app.catalog.sync_to_storage(app.storage)
                print(f"字段目录 {app.catalog.document.catalog_version} 已同步。")
            elif args.catalog_command == "show":
                print(app.catalog.to_json())
            else:
                app_id = required(settings.feishu_app_id, "YFD_FEISHU_APP_ID")
                app_secret = required(
                    settings.feishu_app_secret.get_secret_value()
                    if settings.feishu_app_secret
                    else None,
                    "YFD_FEISHU_APP_SECRET",
                )
                app_token = required(settings.feishu_base_token, "YFD_FEISHU_BASE_TOKEN")
                table_id = required(
                    settings.feishu_api_field_table_id, "YFD_FEISHU_API_FIELD_TABLE_ID"
                )
                result = sync_catalog_to_feishu(
                    gateway=FeishuClient(
                        app_id=app_id,
                        app_secret=app_secret,
                        api_base_url=settings.feishu_api_base_url,
                    ),
                    app_token=app_token,
                    table_id=table_id,
                    catalog=app.catalog,
                )
                print_json(result)
            return 0
        if args.command == "feishu":
            app_id = required(settings.feishu_app_id, "YFD_FEISHU_APP_ID")
            app_secret = required(
                settings.feishu_app_secret.get_secret_value()
                if settings.feishu_app_secret
                else None,
                "YFD_FEISHU_APP_SECRET",
            )
            app_token = required(settings.feishu_base_token, "YFD_FEISHU_BASE_TOKEN")
            client = FeishuClient(
                app_id=app_id,
                app_secret=app_secret,
                api_base_url=settings.feishu_api_base_url,
            )
            if args.feishu_command == "enable-runtime-status":
                runtime_setup_result = RuntimeStatusTableSetup(
                    gateway=client,
                    app_token=app_token,
                    env_file=settings.project_root / ".env",
                ).ensure()
                print_json(asdict(runtime_setup_result))
                return 0
            if args.feishu_command == "set-channel-history-enabled":
                switch_result = ChannelHistoryFeishuSwitch(
                    gateway=client,
                    app_token=app_token,
                    project_config_table_id=required(
                        settings.feishu_project_config_table_id,
                        "YFD_FEISHU_PROJECT_CONFIG_TABLE_ID",
                    ),
                ).set_enabled(args.enabled == "true")
                print_json(asdict(switch_result))
                return 0
            if args.feishu_command == "enable-channel-48h-fields":
                snapshot = app._load_remote_config_if_available(client, app_token)
                if snapshot is None or snapshot.source != "feishu":
                    raise ConfigurationError(
                        "未能读取飞书当前配置，禁止使用本地缓存执行字段升级。"
                    )
                account_config = snapshot.account_config if snapshot else {}
                channel_table_ids = app._channel_history_table_ids(account_config)
                setup = Channel48HourFeishuSetup(
                    gateway=client,
                    app_token=app_token,
                    video_main_table_id=channel_table_ids["视频主表"],
                    mapping_table_id=required(
                        settings.feishu_module_mapping_table_id,
                        "YFD_FEISHU_MODULE_MAPPING_TABLE_ID",
                    ),
                )
                setup.validate()
                catalog_result = sync_catalog_to_feishu(
                    gateway=client,
                    app_token=app_token,
                    table_id=required(
                        settings.feishu_api_field_table_id,
                        "YFD_FEISHU_API_FIELD_TABLE_ID",
                    ),
                    catalog=app.catalog,
                )
                setup_result = setup.apply()
                print_json(
                    {
                        "catalog_sync": catalog_result,
                        "channel_48h_setup": asdict(setup_result),
                    }
                )
                return 0
            business_tables = resolve_latest_video_business_tables(client, app_token, settings)
            if args.feishu_command == "enable-latest-milestone-fields":
                milestone_setup = LatestVideoMilestoneFeishuSetup(
                    gateway=client,
                    app_token=app_token,
                    video_main_table_id=business_tables["视频追踪主表"],
                    mapping_table_id=required(
                        settings.feishu_module_mapping_table_id,
                        "YFD_FEISHU_MODULE_MAPPING_TABLE_ID",
                    ),
                )
                milestone_setup.validate()
                catalog_result = sync_catalog_to_feishu(
                    gateway=client,
                    app_token=app_token,
                    table_id=required(
                        settings.feishu_api_field_table_id,
                        "YFD_FEISHU_API_FIELD_TABLE_ID",
                    ),
                    catalog=app.catalog,
                )
                milestone_result = milestone_setup.apply()
                print_json(
                    {
                        "catalog_sync": catalog_result,
                        "latest_milestone_setup": asdict(milestone_result),
                    }
                )
                return 0
            if args.feishu_command == "mark-critical-fields":
                marker_result = ConfigCenterCriticalFieldMarker(
                    gateway=client,
                    app_token=app_token,
                    table_ids={
                        "API字段字典": required(
                            settings.feishu_api_field_table_id,
                            "YFD_FEISHU_API_FIELD_TABLE_ID",
                        ),
                        "模块字段需求与映射": required(
                            settings.feishu_module_mapping_table_id,
                            "YFD_FEISHU_MODULE_MAPPING_TABLE_ID",
                        ),
                        "数据项目配置": required(
                            settings.feishu_project_config_table_id,
                            "YFD_FEISHU_PROJECT_CONFIG_TABLE_ID",
                        ),
                        "账号非敏感配置": required(
                            settings.feishu_account_config_table_id,
                            "YFD_FEISHU_ACCOUNT_CONFIG_TABLE_ID",
                        ),
                    },
                    backup_file=settings.project_root
                    / "runtime"
                    / "backups"
                    / "feishu_fields_before_critical_marking.json",
                ).mark()
                print_json(asdict(marker_result))
                return 0
            if args.feishu_command == "localize-config":
                localization_result = ConfigCenterLocalizationService(
                    gateway=client,
                    app_token=app_token,
                    catalog=app.catalog,
                    mapping_table_id=required(
                        settings.feishu_module_mapping_table_id,
                        "YFD_FEISHU_MODULE_MAPPING_TABLE_ID",
                    ),
                    project_config_table_id=required(
                        settings.feishu_project_config_table_id,
                        "YFD_FEISHU_PROJECT_CONFIG_TABLE_ID",
                    ),
                    account_config_table_id=required(
                        settings.feishu_account_config_table_id,
                        "YFD_FEISHU_ACCOUNT_CONFIG_TABLE_ID",
                    ),
                    business_tables=business_tables,
                    backup_file=settings.project_root
                    / "runtime"
                    / "backups"
                    / "feishu_config_before_localization.json",
                ).upgrade()
                update_dotenv(
                    settings.project_root / ".env",
                    {
                        "YFD_FEISHU_LATEST_VIDEO_MAIN_TABLE_ID": business_tables["视频追踪主表"],
                        "YFD_FEISHU_LATEST_VIDEO_SNAPSHOT_TABLE_ID": business_tables[
                            "视频实时快照表"
                        ],
                        "YFD_FEISHU_LATEST_VIDEO_COMPARISON_TABLE_ID": business_tables[
                            "视频同期对比表"
                        ],
                    },
                )
                bootstrap_result = ConfigCenterBootstrapper(
                    gateway=client,
                    app_token=app_token,
                    catalog=app.catalog,
                    env_file=settings.project_root / ".env",
                    latest_video_main_table_id=business_tables["视频追踪主表"],
                    latest_video_snapshot_table_id=business_tables["视频实时快照表"],
                    latest_video_comparison_table_id=business_tables["视频同期对比表"],
                    youtube_channel_id=settings.youtube_channel_id,
                ).bootstrap(write_env=True)
                print_json(
                    {
                        "localization": asdict(localization_result),
                        "bootstrap_verification": asdict(bootstrap_result),
                    }
                )
                return 0
            bootstrap_result = ConfigCenterBootstrapper(
                gateway=client,
                app_token=app_token,
                catalog=app.catalog,
                env_file=settings.project_root / ".env",
                latest_video_main_table_id=business_tables["视频追踪主表"],
                latest_video_snapshot_table_id=business_tables["视频实时快照表"],
                latest_video_comparison_table_id=business_tables["视频同期对比表"],
                youtube_channel_id=settings.youtube_channel_id,
            ).bootstrap(write_env=not args.no_write_env)
            print_json(asdict(bootstrap_result))
            return 0
        if args.command == "modules":
            if args.modules_command == "preview":
                ensure_latest_video_module(args.module_id)
                print_json(app.preview_latest_video(channel_id=args.channel_id))
                return 0
            if args.modules_command == "validate-sync":
                if args.module_id in {
                    CHANNEL_HISTORY.module_id,
                    CHANNEL_HISTORY_TASK_ID,
                }:
                    result = app.validate_channel_history_sync()
                else:
                    ensure_latest_video_module(args.module_id)
                    result = app.validate_latest_video_sync()
                print_json(result)
                return 0 if result["summary"]["safe_to_run_full_three_table_sync"] else 2
            print_json(
                [manifest_to_dict(item) for item in (LATEST_VIDEO, CHANNEL_HISTORY, ALL_VIDEOS)]
            )
            return 0
        if args.command == "scheduler":
            task_id = (
                normalize_scheduler_task_id(args.task_id)
                if args.scheduler_command in {"run-once", "scheduled-run"}
                else None
            )
            if args.scheduler_command == "run-once" and args.dry_run:
                if task_id == CHANNEL_HISTORY_TASK_ID:
                    print_json(
                        {
                            "task_id": task_id,
                            "module_id": CHANNEL_HISTORY.module_id,
                            "dry_run": True,
                            "external_requests_made": False,
                            "external_writes_made": False,
                            "defaults": {
                                "timezone": settings.timezone,
                                "daily_collection_hour": 8,
                                "ranking_window_days": 7,
                                "video_scope": "long_only",
                                "ranking_basis": "data_api_snapshot_delta",
                            },
                            "note": (
                                "只检查频道每日任务入口；不读取 YouTube，"
                                "不写飞书业务表。"
                            ),
                        }
                    )
                    return 0
                plan = app.catalog.build_request_plan(
                    LATEST_VIDEO.module_id, list(LATEST_VIDEO.api_field_ids)
                )
                missing_bootstrap = [
                    name
                    for name, present in (
                        ("YFD_FEISHU_APP_ID", bool(settings.feishu_app_id)),
                        ("YFD_FEISHU_APP_SECRET", bool(settings.feishu_app_secret)),
                        ("YFD_FEISHU_BASE_TOKEN", bool(settings.feishu_base_token)),
                        (
                            "YFD_FEISHU_LATEST_VIDEO_TABLE_ID 或飞书映射目标表",
                            bool(settings.feishu_latest_video_table_id),
                        ),
                        ("YouTube OAuth Token", settings.youtube_token_path.is_file()),
                    )
                    if not present
                ]
                print_json(
                    {
                        "task_id": task_id,
                        "dry_run": True,
                        "external_requests_made": False,
                        "request_plan": asdict(plan),
                        "defaults": {
                            "interval_minutes": settings.latest_interval_minutes,
                            "analytics_interval_hours": (
                                settings.latest_analytics_interval_hours
                            ),
                            "reporting_interval_hours": (
                                settings.latest_reporting_interval_hours
                            ),
                            "tracking_days": settings.latest_tracking_days,
                            "hourly_tracking_hours": settings.latest_hourly_tracking_hours,
                            "daily_collection_hour": settings.latest_daily_collection_hour,
                        },
                        "missing_bootstrap_configuration": missing_bootstrap,
                    }
                )
                return 0
            if args.scheduler_command == "status":
                print_json(app.scheduler_status())
                return 0
            if args.scheduler_command == "tick":
                outcomes = app.run_scheduler_tick()
            elif args.scheduler_command == "scheduled-run":
                scheduler = app.build_scheduler()
                outcomes = [
                    scheduler.run_once(
                        required(task_id, "调度任务 ID"),
                        force=False,
                    )
                ]
            else:
                scheduler = app.build_scheduler()
                outcomes = [
                    scheduler.run_once(
                        required(task_id, "调度任务 ID"), dry_run=args.dry_run
                    )
                ]
            print_json([asdict(outcome) for outcome in outcomes])
            return 0 if all(item.status != "failed" for item in outcomes) else 2
    finally:
        app.close()
    raise ValueError(f"无法识别的命令：{args.command}")


def manifest_to_dict(manifest: Any) -> dict[str, Any]:
    return {
        "module_id": manifest.module_id,
        "version": manifest.version,
        "cn_name": manifest.cn_name,
        "implemented": manifest.implemented,
        "api_field_ids": manifest.api_field_ids,
    }


def ensure_latest_video_module(module_id: str) -> None:
    accepted = {LATEST_VIDEO.module_id, LATEST_VIDEO_TASK_ID}
    if module_id not in accepted:
        raise DashboardError(
            f"模块 {module_id} 尚未实现安全预览或同步检查；当前仅支持 {LATEST_VIDEO.module_id}。"
        )


def normalize_scheduler_task_id(task_id: str) -> str:
    """让用户入口同时接受模块 ID 和调度任务 ID。"""
    if task_id in {LATEST_VIDEO.module_id, LATEST_VIDEO_TASK_ID}:
        return LATEST_VIDEO_TASK_ID
    if task_id in {CHANNEL_HISTORY.module_id, "channel-history-daily"}:
        return "channel-history-daily"
    return task_id


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def apply_proxy_environment(settings: Settings) -> None:
    if not settings.https_proxy:
        return
    os.environ.setdefault("HTTPS_PROXY", settings.https_proxy)
    os.environ.setdefault("HTTP_PROXY", settings.https_proxy)
    current_no_proxy = os.environ.get("NO_PROXY", "")
    entries = {item.strip() for item in current_no_proxy.split(",") if item.strip()}
    entries.update({"localhost", "127.0.0.1"})
    os.environ["NO_PROXY"] = ",".join(sorted(entries))


def resolve_latest_video_business_tables(
    client: FeishuClient, app_token: str, settings: Settings
) -> dict[str, str]:
    """优先使用明确配置；缺少时按精确中文表名从同一 Base 发现。"""
    configured = {
        "视频追踪主表": settings.feishu_latest_video_main_table_id,
        "视频实时快照表": settings.feishu_latest_video_snapshot_table_id
        or settings.feishu_latest_video_table_id,
        "视频同期对比表": settings.feishu_latest_video_comparison_table_id,
    }
    discovered = {
        str(item.get("name")): str(item.get("table_id"))
        for item in client.list_tables(app_token)
        if item.get("name") and item.get("table_id")
    }
    return {
        name: required(table_id or discovered.get(name), f"飞书业务表：{name}")
        for name, table_id in configured.items()
    }


if __name__ == "__main__":
    raise SystemExit(main())
