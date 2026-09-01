from __future__ import annotations

import getpass
import os
from pathlib import Path

from youtube_feishu_dashboard.catalog.field_catalog import FieldCatalog
from youtube_feishu_dashboard.core.settings import Settings
from youtube_feishu_dashboard.db.database import Database
from youtube_feishu_dashboard.db.migrations import upgrade_database
from youtube_feishu_dashboard.db.repositories import SqlAlchemyStorage


def initialize_project(settings: Settings, *, interactive: bool = False) -> dict[str, object]:
    root = settings.project_root
    for relative in ("data", "logs", "runtime", "secrets"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    env_path = root / ".env"
    created_env = False
    if not env_path.exists():
        values = collect_answers() if interactive else {}
        write_env_from_example(root / ".env.example", env_path, values)
        created_env = True

    effective = Settings.load(root)
    upgrade_database(effective)
    database = Database(effective.sqlalchemy_url)
    try:
        FieldCatalog.load_builtin().sync_to_storage(SqlAlchemyStorage(database))
    finally:
        database.dispose()
    return {
        "project_root": str(root),
        "env_created": created_env,
        "database": effective.safe_database_url(),
        "catalog_synced": True,
    }


def collect_answers() -> dict[str, str]:
    print("\n首次配置向导：留空表示稍后手工编辑 .env。输入内容只写入本机 .env。\n")
    answers = {
        "YFD_TIMEZONE": prompt("时区", "Asia/Shanghai"),
        "YFD_YOUTUBE_CHANNEL_ID": prompt("YouTube 频道 ID（可留空自动识别）", ""),
        "YFD_FEISHU_APP_ID": prompt("飞书 App ID", ""),
        "YFD_FEISHU_APP_SECRET": getpass.getpass("飞书 App Secret（输入不可见，可留空）：").strip(),
        "YFD_FEISHU_BASE_TOKEN": prompt("飞书 Base Token", ""),
        "YFD_FEISHU_API_FIELD_TABLE_ID": prompt("API 字段字典 Table ID", ""),
        "YFD_FEISHU_MODULE_MAPPING_TABLE_ID": prompt("模块字段映射 Table ID", ""),
        "YFD_FEISHU_PROJECT_CONFIG_TABLE_ID": prompt("数据项目配置 Table ID", ""),
        "YFD_FEISHU_ACCOUNT_CONFIG_TABLE_ID": prompt("账号非敏感配置 Table ID", ""),
        "YFD_FEISHU_LATEST_VIDEO_TABLE_ID": prompt("最新视频实时追踪 Table ID", ""),
    }
    return answers


def prompt(label: str, default: str) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}：").strip()
    return value or default


def write_env_from_example(example: Path, destination: Path, values: dict[str, str]) -> None:
    lines = example.read_text(encoding="utf-8").splitlines()
    rendered: list[str] = []
    replaced: set[str] = set()
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            rendered.append(line)
            continue
        key = line.split("=", 1)[0]
        if key in values:
            rendered.append(f"{key}={values[key]}")
            replaced.add(key)
        else:
            rendered.append(line)
    for key in sorted(values.keys() - replaced):
        rendered.append(f"{key}={values[key]}")
    temporary = destination.with_suffix(".tmp")
    temporary.write_text("\n".join(rendered) + "\n", encoding="utf-8")
    if os.name != "nt":
        temporary.chmod(0o600)
    temporary.replace(destination)
