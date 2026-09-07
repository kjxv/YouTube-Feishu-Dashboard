from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WINDOWS_SCRIPTS = PROJECT_ROOT / "01_启动脚本" / "Windows"
WINDOWS_OPS = PROJECT_ROOT / "05_部署与运维" / "windows"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_windows_wrapper_logs_start_end_exit_code_and_duration() -> None:
    script = read_text(WINDOWS_OPS / "Run-YfdScheduler.ps1")

    assert "[TICK_START]" in script
    assert "[TICK_END]" in script
    assert "exit_code=" in script
    assert "duration_seconds=" in script


def test_windows_manager_supports_full_diagnostics_and_timeout() -> None:
    manager = read_text(WINDOWS_OPS / "Manage-YfdScheduledTask.ps1")
    launcher = read_text(WINDOWS_SCRIPTS / "16.诊断定时任务.bat")

    assert '"Diagnose"' in manager
    assert '"Stop"' in manager
    assert "Stop-ScheduledTask" in manager
    assert "scheduler status" in manager
    assert "youtube_feishu_dashboard scheduler tick" in manager
    assert "ExecutionTimeLimit" in manager
    assert "New-TimeSpan -Minutes 45" in manager
    assert "-Action Diagnose" in launcher


def test_windows_upgrade_enables_runtime_status_before_validation() -> None:
    script = read_text(WINDOWS_SCRIPTS / "14.运行电脑升级并验收.bat")

    assert "feishu enable-runtime-status" in script
    assert "modules validate-sync latest_video_tracker" in script
    assert "modules validate-sync channel_history" in script
    assert "scheduler run-once" not in script
