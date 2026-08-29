"""Interactive first-run setup and legacy V1 migration."""

from __future__ import annotations

import argparse
import getpass
import json
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

from app_config import (
    CONFIG_FILE,
    DATA_DIR,
    DEFAULT_CONFIG,
    PROJECT_DIR,
    YOUTUBE_SCOPES,
    ConfigError,
    load_config,
    resolve_project_path,
    save_config,
)


LEGACY_ENV_MAP = {
    "LARK_APP_ID": "app_id",
    "LARK_APP_SECRET": "app_secret",
    "LARK_BASE_TOKEN": "base_token",
    "LARK_VIDEO_TABLE_ID": "video_table_id",
    "LARK_HISTORY_TABLE_ID": "history_table_id",
    "LARK_CHANNEL_TABLE_ID": "channel_table_id",
    "LARK_LATEST_TABLE_ID": "latest_table_id",
}


def read_legacy_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        values[key.strip()] = value
    return values


def starting_config() -> dict[str, Any]:
    if CONFIG_FILE.exists():
        return load_config(require_complete=False)

    config = deepcopy(DEFAULT_CONFIG)
    legacy = read_legacy_env(PROJECT_DIR / ".env")
    for env_key, config_key in LEGACY_ENV_MAP.items():
        if legacy.get(env_key):
            config["lark"][config_key] = legacy[env_key]
    return config


def copy_legacy_oauth_files(config: dict[str, Any]) -> list[str]:
    messages: list[str] = []
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    pairs = (
        (PROJECT_DIR / "client_secret.json", config["youtube"]["client_secret_file"]),
        (PROJECT_DIR / "token.json", config["youtube"]["token_file"]),
    )
    for source, configured_target in pairs:
        target = resolve_project_path(configured_target)
        if source.exists() and source.resolve() != target and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            messages.append(f"已迁移：{source.name} → {target.relative_to(PROJECT_DIR)}")
    return messages


def prompt_text(
    label: str,
    current: str = "",
    *,
    secret: bool = False,
    required: bool = True,
) -> str:
    while True:
        if secret:
            hint = " [已设置，回车保留]" if current else ""
            value = getpass.getpass(f"{label}{hint}：").strip()
        else:
            hint = f" [{current}]" if current else ""
            value = input(f"{label}{hint}：").strip()

        if value:
            return value
        if current:
            return current
        if not required:
            return ""
        print("  此项不能为空，请重新输入。")


def prompt_bool(label: str, current: bool) -> bool:
    suffix = "Y/n" if current else "y/N"
    while True:
        value = input(f"{label} [{suffix}]：").strip().lower()
        if not value:
            return current
        if value in {"y", "yes", "是"}:
            return True
        if value in {"n", "no", "否"}:
            return False
        print("  请输入 Y 或 N。")


def prompt_int(label: str, current: int, minimum: int = 1, maximum: int = 65535) -> int:
    while True:
        value = input(f"{label} [{current}]：").strip()
        if not value:
            return current
        try:
            number = int(value)
            if minimum <= number <= maximum:
                return number
        except ValueError:
            pass
        print(f"  请输入 {minimum}-{maximum} 的整数。")


def valid_client_secret(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, "文件不存在"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return False, "不是有效的 JSON 文件"
    if not isinstance(data, dict) or not (data.get("installed") or data.get("web")):
        return False, "缺少 Google OAuth installed/web 配置"
    return True, "格式正常"


def valid_token(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, "文件不存在"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return False, "不是有效的 JSON 文件"

    required = {"token", "refresh_token", "client_id", "client_secret"}
    if not required.issubset(data):
        return False, "OAuth 凭据不完整"
    scopes = set(data.get("scopes") or [])
    if scopes and not set(YOUTUBE_SCOPES).issubset(scopes):
        return False, "授权范围不足，需要重新授权"
    return True, "凭据文件存在（联网状态请双击 doctor.bat 检查）"


def show_oauth_status(config: dict[str, Any], offer_authorize: bool) -> None:
    client_path = resolve_project_path(config["youtube"]["client_secret_file"])
    token_path = resolve_project_path(config["youtube"]["token_file"])
    client_ok, client_message = valid_client_secret(client_path)
    token_ok, token_message = valid_token(token_path)

    if client_ok and token_ok:
        try:
            client_data = json.loads(client_path.read_text(encoding="utf-8-sig"))
            client_section = client_data.get("installed") or client_data.get("web") or {}
            token_data = json.loads(token_path.read_text(encoding="utf-8-sig"))
            if client_section.get("client_id") != token_data.get("client_id"):
                token_ok = False
                token_message = "与当前 client_secret.json 不匹配，需要重新授权"
        except (OSError, json.JSONDecodeError):
            pass

    print("\nYouTube OAuth 文件检查")
    print(f"  client_secret.json：{'正常' if client_ok else '需要处理'}（{client_message}）")
    print(f"  token.json：{'正常' if token_ok else '需要处理'}（{token_message}）")

    if not client_ok:
        print("\n请把 Google Cloud 下载的 OAuth 客户端文件放到：")
        print(f"  {client_path}")
        print("然后重新运行 setup.bat，或双击 auth_youtube.bat")
        return

    if not token_ok:
        print("\n需要浏览器登录 YouTube 运营账号并授权。")
        if offer_authorize and prompt_bool("现在开始 YouTube 授权吗？", True):
            subprocess.run([sys.executable, str(PROJECT_DIR / "auth_youtube.py")], check=False)
        else:
            print("稍后双击 auth_youtube.bat")
    elif offer_authorize and prompt_bool("是否更换 YouTube 运营账号并重新授权？", False):
        subprocess.run([sys.executable, str(PROJECT_DIR / "auth_youtube.py")], check=False)


def migrate_existing() -> int:
    try:
        config = starting_config()
        save_config(config)
    except ConfigError as exc:
        print(f"❌ 无法迁移：{exc}")
        return 1

    print(f"✅ 已生成：{CONFIG_FILE}")
    for message in copy_legacy_oauth_files(config):
        print(f"✅ {message}")
    show_oauth_status(config, offer_authorize=False)
    return 0


def interactive_setup() -> int:
    print("=" * 52)
    print(" YouTube → Lark Dashboard V2 初始化")
    print("=" * 52)
    print("直接回车会保留方括号中的现有值；App Secret 不会回显。\n")

    try:
        config = starting_config()
    except ConfigError as exc:
        print(f"❌ {exc}")
        return 1

    lark = config["lark"]
    previous_base_token = str(lark.get("base_token", ""))
    lark["app_id"] = prompt_text("Lark App ID", str(lark.get("app_id", "")))
    lark["app_secret"] = prompt_text(
        "Lark App Secret", str(lark.get("app_secret", "")), secret=True
    )
    lark["base_token"] = prompt_text("Lark Base Token", str(lark.get("base_token", "")))
    if previous_base_token and lark["base_token"] != previous_base_token:
        lark["latest_table_id"] = ""
        print("检测到 Base Token 已更换：实时追踪表将在新 Base 中自动创建。")
    lark["video_table_id"] = prompt_text(
        "视频主表 Table ID", str(lark.get("video_table_id", ""))
    )
    lark["history_table_id"] = prompt_text(
        "视频历史表 Table ID", str(lark.get("history_table_id", ""))
    )
    lark["channel_table_id"] = prompt_text(
        "频道历史表 Table ID", str(lark.get("channel_table_id", ""))
    )
    print("实时追踪表可留空；输入 AUTO 可在当前 Base 中重新自动创建。")
    latest_table_id = prompt_text(
        "实时追踪表 Table ID", str(lark.get("latest_table_id", "")), required=False
    )
    lark["latest_table_id"] = (
        "" if latest_table_id.strip().upper() == "AUTO" else latest_table_id
    )

    youtube = config["youtube"]
    youtube["client_secret_file"] = prompt_text(
        "OAuth client_secret 路径", str(youtube.get("client_secret_file", ""))
    )
    youtube["token_file"] = prompt_text(
        "OAuth token 路径", str(youtube.get("token_file", ""))
    )

    proxy = config["proxy"]
    proxy["enabled"] = prompt_bool("YouTube 是否使用代理？", bool(proxy.get("enabled")))
    if proxy["enabled"]:
        proxy["host"] = prompt_text("代理地址", str(proxy.get("host", "127.0.0.1")))
        proxy["port"] = prompt_int("代理端口", int(proxy.get("port", 10808)))

    update = config["update"]
    update["analytics_lookback_days"] = prompt_int(
        "Analytics 回查天数", int(update.get("analytics_lookback_days", 7)), 1, 365
    )
    update["backfill_chunk_days"] = prompt_int(
        "全历史回填分段天数", int(update.get("backfill_chunk_days", 90)), 7, 365
    )
    update["current_interval_minutes"] = prompt_int(
        "current 更新间隔（分钟）", int(update.get("current_interval_minutes", 15)), 1, 1440
    )
    update["latest_interval_minutes"] = prompt_int(
        "最新视频追踪间隔（分钟）", int(update.get("latest_interval_minutes", 5)), 1, 1440
    )
    update["latest_tracking_hours"] = prompt_int(
        "每条最新视频持续追踪小时数", int(update.get("latest_tracking_hours", 168)), 1, 8760
    )
    while True:
        daily_time = prompt_text("daily 每日运行时间（HH:MM）", str(update.get("daily_time", "18:00")))
        try:
            from datetime import datetime

            datetime.strptime(daily_time, "%H:%M")
            update["daily_time"] = daily_time
            break
        except ValueError:
            print("  时间格式无效，例如应填写 18:00。")

    try:
        save_config(config)
    except ConfigError as exc:
        print(f"\n❌ 保存失败：{exc}")
        return 1

    print(f"\n✅ 配置已保存：{CONFIG_FILE}")
    for message in copy_legacy_oauth_files(config):
        print(f"✅ {message}")
    show_oauth_status(config, offer_authorize=True)

    print("\n下一步：")
    print("  1. 双击 doctor.bat 做完整检查")
    print("  2. 双击 run_current.bat、run_daily.bat 和 run_latest.bat 测试同步")
    print("  3. 以当前 Windows 用户运行 install_tasks.bat 安装定时任务")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化 YouTube-Lark Dashboard V2")
    parser.add_argument(
        "--migrate-existing",
        action="store_true",
        help="从根目录 .env/client_secret.json/token.json 非交互迁移",
    )
    args = parser.parse_args()
    if args.migrate_existing:
        return migrate_existing()
    return interactive_setup()


if __name__ == "__main__":
    raise SystemExit(main())
