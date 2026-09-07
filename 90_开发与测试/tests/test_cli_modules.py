from youtube_feishu_dashboard.cli import build_parser, normalize_scheduler_task_id


def test_modules_preview_parser_defaults_to_latest_video() -> None:
    args = build_parser().parse_args(["modules", "preview"])

    assert args.command == "modules"
    assert args.modules_command == "preview"
    assert args.module_id == "latest_video_tracker"


def test_modules_validate_sync_parser_accepts_module_id() -> None:
    args = build_parser().parse_args(
        ["modules", "validate-sync", "latest_video_tracker"]
    )

    assert args.modules_command == "validate-sync"
    assert args.module_id == "latest_video_tracker"


def test_modules_validate_sync_parser_accepts_channel_history() -> None:
    args = build_parser().parse_args(
        ["modules", "validate-sync", "channel_history"]
    )

    assert args.modules_command == "validate-sync"
    assert args.module_id == "channel_history"


def test_scheduler_accepts_module_id_alias() -> None:
    assert normalize_scheduler_task_id("latest_video_tracker") == "latest-video-tracker"
    assert normalize_scheduler_task_id("latest-video-tracker") == "latest-video-tracker"
    assert normalize_scheduler_task_id("channel_history") == "channel-history-daily"
    assert normalize_scheduler_task_id("channel-history-daily") == "channel-history-daily"
    assert normalize_scheduler_task_id("another-task") == "another-task"


def test_scheduler_has_cadence_aware_system_entry() -> None:
    args = build_parser().parse_args(
        ["scheduler", "scheduled-run", "latest-video-tracker"]
    )

    assert args.scheduler_command == "scheduled-run"
    assert args.task_id == "latest-video-tracker"


def test_feishu_has_channel_48h_setup_entry() -> None:
    args = build_parser().parse_args(["feishu", "enable-channel-48h-fields"])

    assert args.command == "feishu"
    assert args.feishu_command == "enable-channel-48h-fields"


def test_feishu_has_channel_history_switch_entry() -> None:
    args = build_parser().parse_args(
        ["feishu", "set-channel-history-enabled", "true"]
    )

    assert args.feishu_command == "set-channel-history-enabled"
    assert args.enabled == "true"


def test_feishu_has_runtime_status_setup_entry() -> None:
    args = build_parser().parse_args(["feishu", "enable-runtime-status"])

    assert args.feishu_command == "enable-runtime-status"


def test_scheduler_has_local_status_entry() -> None:
    args = build_parser().parse_args(["scheduler", "status"])

    assert args.scheduler_command == "status"
