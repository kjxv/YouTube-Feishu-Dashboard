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


def test_scheduler_accepts_module_id_alias() -> None:
    assert normalize_scheduler_task_id("latest_video_tracker") == "latest-video-tracker"
    assert normalize_scheduler_task_id("latest-video-tracker") == "latest-video-tracker"
    assert normalize_scheduler_task_id("another-task") == "another-task"


def test_scheduler_has_cadence_aware_system_entry() -> None:
    args = build_parser().parse_args(
        ["scheduler", "scheduled-run", "latest-video-tracker"]
    )

    assert args.scheduler_command == "scheduled-run"
    assert args.task_id == "latest-video-tracker"
