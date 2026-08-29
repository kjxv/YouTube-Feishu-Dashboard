"""Shared configuration helpers for the portable V2 project."""

from __future__ import annotations

import json
import re
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any


for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")


PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data"
LOGS_DIR = PROJECT_DIR / "logs"
CONFIG_FILE = DATA_DIR / "config.json"

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/yt-analytics-monetary.readonly",
]

DEFAULT_CONFIG: dict[str, Any] = {
    "version": 2,
    "lark": {
        "app_id": "",
        "app_secret": "",
        "base_token": "",
        "video_table_id": "",
        "history_table_id": "",
        "channel_table_id": "",
    },
    "youtube": {
        "client_secret_file": "data/client_secret.json",
        "token_file": "data/token.json",
    },
    "proxy": {
        "enabled": True,
        "host": "127.0.0.1",
        "port": 10808,
    },
    "update": {
        "analytics_lookback_days": 7,
        "current_interval_minutes": 15,
        "daily_time": "18:00",
    },
}


class ConfigError(ValueError):
    """Raised when data/config.json is absent or invalid."""


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def resolve_project_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_DIR / path
    return path.resolve()


def validate_config(config: dict[str, Any], require_complete: bool = True) -> list[str]:
    errors: list[str] = []

    for section in ("lark", "youtube", "proxy", "update"):
        if not isinstance(config.get(section), dict):
            errors.append(f"缺少或无效的配置段：{section}")

    if errors:
        return errors

    if require_complete:
        labels = {
            "app_id": "Lark App ID",
            "app_secret": "Lark App Secret",
            "base_token": "Lark Base Token",
            "video_table_id": "视频主表 Table ID",
            "history_table_id": "视频历史表 Table ID",
            "channel_table_id": "频道历史表 Table ID",
        }
        for key, label in labels.items():
            value = str(config["lark"].get(key, "")).strip()
            if not value or value.startswith("请填写"):
                errors.append(f"未填写：{label}")

    for key in ("client_secret_file", "token_file"):
        if not str(config["youtube"].get(key, "")).strip():
            errors.append(f"YouTube 路径不能为空：{key}")

    try:
        port = int(config["proxy"].get("port", 0))
        if not 1 <= port <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("代理端口必须是 1-65535 的整数")

    if bool(config["proxy"].get("enabled")) and not str(
        config["proxy"].get("host", "")
    ).strip():
        errors.append("启用代理时必须填写代理地址")

    for key, label in (
        ("analytics_lookback_days", "Analytics 回查天数"),
        ("current_interval_minutes", "current 更新间隔"),
    ):
        try:
            if int(config["update"].get(key, 0)) < 1:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"{label}必须是大于 0 的整数")

    daily_time = str(config["update"].get("daily_time", ""))
    if not re.fullmatch(r"\d{2}:\d{2}", daily_time):
        errors.append("daily_time 必须使用 HH:MM 格式，例如 18:00")
    else:
        try:
            datetime.strptime(daily_time, "%H:%M")
        except ValueError:
            errors.append("daily_time 不是有效时间")

    return errors


def load_config(
    path: Path = CONFIG_FILE,
    require_complete: bool = True,
) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(
            f"找不到配置文件：{path}。请先运行 setup.bat 或 install.bat。"
        )

    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"配置文件 JSON 格式错误：第 {exc.lineno} 行") from exc
    except OSError as exc:
        raise ConfigError(f"无法读取配置文件：{exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("配置文件最外层必须是 JSON 对象")

    config = _merge(DEFAULT_CONFIG, raw)
    config["version"] = 2
    config["proxy"]["enabled"] = bool(config["proxy"].get("enabled"))

    try:
        config["proxy"]["port"] = int(config["proxy"]["port"])
        config["update"]["analytics_lookback_days"] = int(
            config["update"]["analytics_lookback_days"]
        )
        config["update"]["current_interval_minutes"] = int(
            config["update"]["current_interval_minutes"]
        )
    except (TypeError, ValueError):
        pass

    errors = validate_config(config, require_complete=require_complete)
    if errors:
        raise ConfigError("配置无效：\n- " + "\n- ".join(errors))

    return config


def save_config(config: dict[str, Any], path: Path = CONFIG_FILE) -> None:
    merged = _merge(DEFAULT_CONFIG, config)
    merged["version"] = 2
    errors = validate_config(merged, require_complete=True)
    if errors:
        raise ConfigError("配置无效：\n- " + "\n- ".join(errors))

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(path)
