"""Create or replace the Windows Scheduled Tasks."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

from app_config import ConfigError, PROJECT_DIR, load_config


def create_task(name: str, schedule_args: list[str], bat_file: str, dry_run: bool) -> bool:
    comspec = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")
    bat_path = str((PROJECT_DIR / bat_file).resolve())
    action = f'"{comspec}" /d /c ""{bat_path}" --scheduled"'
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    schtasks = system_root / "System32" / "schtasks.exe"
    command = [
        str(schtasks),
        "/Create",
        "/TN",
        name,
        "/TR",
        action,
        "/RL",
        "LIMITED",
        "/F",
        *schedule_args,
    ]
    if dry_run:
        print(f"[预检] {name}：{schedule_args} → {bat_file} --scheduled")
        return True

    result = subprocess.run(command, text=True, capture_output=True, encoding="utf-8", errors="replace")
    if result.returncode == 0:
        print(f"✅ 已创建/更新任务：{name}")
        return True
    message = (result.stderr or result.stdout).strip()
    print(f"❌ 任务创建失败：{name}（{message}）")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="安装 Windows 自动同步任务")
    parser.add_argument("--dry-run", action="store_true", help="只检查参数，不创建任务")
    args = parser.parse_args()

    if os.name != "nt":
        print("❌ install_tasks 仅支持 Windows。")
        return 1
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"❌ {exc}")
        return 1

    identity = config["lark"]["app_id"] + "|" + config["lark"]["base_token"]
    suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8].upper()
    current_name = f"YouTube-Lark-Current-{suffix}"
    daily_name = f"YouTube-Lark-Daily-{suffix}"
    latest_name = f"YouTube-Lark-Latest-{suffix}"
    interval = str(config["update"]["current_interval_minutes"])
    latest_interval = str(config["update"]["latest_interval_minutes"])
    daily_time = config["update"]["daily_time"]

    print(f"项目目录：{PROJECT_DIR}")
    print(
        f"current：每 {interval} 分钟；"
        f"latest：每 {latest_interval} 分钟；"
        f"daily：每天 {daily_time}"
    )
    current_ok = create_task(
        current_name,
        ["/SC", "MINUTE", "/MO", interval],
        "run_current.bat",
        args.dry_run,
    )
    daily_ok = create_task(
        daily_name,
        ["/SC", "DAILY", "/ST", daily_time],
        "run_daily.bat",
        args.dry_run,
    )
    latest_ok = create_task(
        latest_name,
        ["/SC", "MINUTE", "/MO", latest_interval],
        "run_latest.bat",
        args.dry_run,
    )
    if current_ok and daily_ok and latest_ok:
        if args.dry_run:
            print("✅ 定时任务参数预检通过，未修改系统任务。")
        else:
            print("✅ 定时任务安装完成。移动项目后请重新运行 install_tasks.bat。")
        return 0
    print("如遇权限错误，请右键 install_tasks.bat，选择“以管理员身份运行”。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
