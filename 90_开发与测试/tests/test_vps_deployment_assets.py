from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LINUX_SCRIPTS = PROJECT_ROOT / "01_启动脚本" / "Linux_VPS"


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    return raw.decode("utf-8")


def test_vps_channel_entrypoints_use_the_registered_tasks() -> None:
    validate = read_text(LINUX_SCRIPTS / "只读检查_频道数据.sh")
    run_once = read_text(LINUX_SCRIPTS / "运行一次_频道数据.sh")

    assert validate.startswith("#!/usr/bin/env bash\nset -euo pipefail\n")
    assert "modules validate-sync channel_history" in validate
    assert run_once.startswith("#!/usr/bin/env bash\nset -euo pipefail\n")
    assert "scheduler run-once channel-history-daily" in run_once


def test_vps_upgrade_script_is_validation_only() -> None:
    script = read_text(LINUX_SCRIPTS / "升级并验收.sh")

    assert "pip install --upgrade ." in script
    assert "db upgrade head" in script
    assert "doctor --online" in script
    assert "feishu enable-runtime-status" in script
    assert "feishu enable-channel-48h-fields" in script
    assert "modules validate-sync latest_video_tracker" in script
    assert "modules validate-sync channel_history" in script
    assert "scheduler run-once" not in script
    assert "set-channel-history-enabled" not in script


def test_vps_systemd_manager_installs_one_non_overlapping_timer() -> None:
    script = read_text(LINUX_SCRIPTS / "自动任务管理.sh")

    assert "ExecStart=${PYTHON} -m youtube_feishu_dashboard scheduler tick" in script
    assert "OnUnitInactiveSec=5min" in script
    assert "Persistent=true" in script
    assert 'systemctl enable --now "${TIMER_NAME}"' in script
    assert "cron" not in script.lower()

    service = read_text(
        PROJECT_ROOT / "05_部署与运维" / "systemd" / "yfd-tick.service.example"
    )
    timer = read_text(
        PROJECT_ROOT / "05_部署与运维" / "systemd" / "yfd-tick.timer.example"
    )
    assert "NoNewPrivileges=true" in service
    assert "PrivateTmp=true" in service
    assert "OnUnitInactiveSec=5min" in timer
    assert "OnUnitActiveSec" not in timer


def test_vps_sqlite_backup_uses_online_backup_and_integrity_check() -> None:
    script = read_text(LINUX_SCRIPTS / "备份SQLite数据库.sh")

    assert "source.backup(target)" in script
    assert "PRAGMA integrity_check" in script
    assert 'url.drivername.startswith("sqlite")' in script
    assert "runtime/backups/vps" in script
