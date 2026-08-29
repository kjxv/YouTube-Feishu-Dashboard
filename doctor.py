"""Read-only diagnostics for the local V2 installation."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import socket
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

from app_config import ConfigError, YOUTUBE_SCOPES, load_config, resolve_project_path


class Doctor:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.warnings = 0

    def ok(self, message: str) -> None:
        self.passed += 1
        print(f"✅ {message}")

    def fail(self, message: str) -> None:
        self.failed += 1
        print(f"❌ {message}")

    def warn(self, message: str) -> None:
        self.warnings += 1
        print(f"⚠️  {message}")

    def summary(self) -> int:
        print("\n" + "=" * 52)
        print(f"检查完成：通过 {self.passed}，失败 {self.failed}，提醒 {self.warnings}")
        if self.failed:
            print("请先处理失败项，再运行 current/daily/latest。")
            return 1
        print("环境可用，可以运行 current/daily/latest。")
        return 0


def check_json_file(path: Path, predicate: Callable[[dict], bool]) -> tuple[bool, str]:
    if not path.exists():
        return False, "文件不存在"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"JSON 无效：{exc}"
    if not isinstance(data, dict) or not predicate(data):
        return False, "内容不完整"
    return True, ""


def module_available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ModuleNotFoundError, AttributeError, ValueError):
        return False


def main() -> int:
    doctor = Doctor()
    print("=" * 52)
    print(" YouTube → Lark Dashboard V2 环境诊断")
    print("=" * 52)

    if sys.version_info >= (3, 9):
        doctor.ok(f"Python {sys.version.split()[0]}")
    else:
        doctor.fail(f"Python 版本过低：{sys.version.split()[0]}（需要 3.9+）")

    project_python = resolve_project_path(".venv/Scripts/python.exe")
    if project_python.exists() and Path(sys.executable).resolve() != project_python:
        doctor.warn("当前使用的是系统 Python，不是项目 .venv；请改为双击 doctor.bat")

    dependencies = {
        "requests": "requests",
        "httplib2": "httplib2",
        "PySocks": "socks",
        "google-auth": "google.oauth2.credentials",
        "google-auth-httplib2": "google_auth_httplib2",
        "google-auth-oauthlib": "google_auth_oauthlib.flow",
        "google-api-python-client": "googleapiclient.discovery",
        "tzdata": "tzdata",
    }
    missing: list[str] = []
    for package, module in dependencies.items():
        if not module_available(module):
            missing.append(package)
    if missing:
        doctor.fail("缺少 Python 依赖：" + ", ".join(missing))
        print("  请运行 install.bat。")
    else:
        doctor.ok("Python 依赖完整")

    try:
        config = load_config()
        doctor.ok("data/config.json 格式和必填项正常")
    except ConfigError as exc:
        doctor.fail(str(exc))
        return doctor.summary()

    client_path = resolve_project_path(config["youtube"]["client_secret_file"])
    client_ok, client_error = check_json_file(
        client_path, lambda data: bool(data.get("installed") or data.get("web"))
    )
    if client_ok:
        doctor.ok(f"OAuth 客户端文件正常：{client_path.name}")
    else:
        doctor.fail(f"OAuth 客户端文件异常：{client_error}")

    token_path = resolve_project_path(config["youtube"]["token_file"])
    token_ok, token_error = check_json_file(
        token_path,
        lambda data: bool(data.get("refresh_token"))
        and set(YOUTUBE_SCOPES).issubset(set(data.get("scopes") or YOUTUBE_SCOPES)),
    )
    if token_ok:
        doctor.ok(f"OAuth token 文件正常：{token_path.name}")
    else:
        doctor.fail(f"OAuth token 异常：{token_error}；请双击 auth_youtube.bat")

    if client_ok and token_ok:
        client_data = json.loads(client_path.read_text(encoding="utf-8-sig"))
        client_section = client_data.get("installed") or client_data.get("web") or {}
        token_data = json.loads(token_path.read_text(encoding="utf-8-sig"))
        if client_section.get("client_id") == token_data.get("client_id"):
            doctor.ok("OAuth client_secret 与 token 属于同一客户端")
        else:
            doctor.fail("OAuth client_secret 与 token 不匹配，请双击 auth_youtube.bat")
            token_ok = False

    if config["proxy"]["enabled"]:
        host = config["proxy"]["host"]
        port = config["proxy"]["port"]
        try:
            with socket.create_connection((host, port), timeout=3):
                pass
            doctor.ok(f"代理端口可连接：{host}:{port}")
        except OSError as exc:
            doctor.fail(f"代理不可连接：{host}:{port}（{exc}）")
    else:
        doctor.ok("代理未启用，将直接连接 YouTube")

    if missing:
        return doctor.summary()

    import requests

    lark_api = "https://open.larksuite.com/open-apis"
    lark_token = ""
    try:
        response = requests.post(
            f"{lark_api}/auth/v3/tenant_access_token/internal",
            json={
                "app_id": config["lark"]["app_id"],
                "app_secret": config["lark"]["app_secret"],
            },
            timeout=30,
        )
        data = response.json()
        if data.get("code") != 0:
            raise RuntimeError(data.get("msg") or f"Lark code={data.get('code')}")
        lark_token = data["tenant_access_token"]
        doctor.ok("Lark App 凭据可获取 tenant_access_token")
    except Exception as exc:
        doctor.fail(f"Lark App 连接失败：{exc}")

    if lark_token:
        headers = {"Authorization": f"Bearer {lark_token}"}
        tables = [
            ("视频主表", config["lark"]["video_table_id"]),
            ("视频历史表", config["lark"]["history_table_id"]),
            ("频道历史表", config["lark"]["channel_table_id"]),
        ]
        latest_table_id = str(config["lark"].get("latest_table_id", "")).strip()
        if latest_table_id:
            tables.append(("最新视频实时追踪表", latest_table_id))
        else:
            doctor.warn("实时追踪表尚未创建；首次运行 run_latest.bat 会自动创建")
        for label, table_id in tables:
            try:
                response = requests.get(
                    f"{lark_api}/bitable/v1/apps/{config['lark']['base_token']}"
                    f"/tables/{table_id}/records",
                    headers=headers,
                    params={"page_size": 1},
                    timeout=30,
                )
                data = response.json()
                if data.get("code") != 0:
                    raise RuntimeError(data.get("msg") or f"Lark code={data.get('code')}")
                doctor.ok(f"Lark {label}可访问")
            except Exception as exc:
                doctor.fail(f"Lark {label}不可访问：{exc}")

    if token_ok:
        try:
            from youtube_lark import build_youtube

            youtube = build_youtube()
            response = youtube.channels().list(part="id,snippet", mine=True).execute(
                num_retries=2
            )
            items = response.get("items", [])
            if not items:
                raise RuntimeError("当前 OAuth 账号没有可访问的 YouTube 频道")
            title = items[0].get("snippet", {}).get("title", "未命名频道")
            doctor.ok(f"YouTube Data API 可用（频道：{title}）")
        except Exception as exc:
            doctor.fail(f"YouTube OAuth/Data API 失败：{exc}")

        try:
            from youtube_lark import build_analytics

            yesterday = str(date.today() - timedelta(days=1))
            analytics = build_analytics()
            analytics.reports().query(
                ids="channel==MINE",
                startDate=yesterday,
                endDate=yesterday,
                metrics="views",
            ).execute(num_retries=2)
            doctor.ok("YouTube Analytics API 可用")
        except Exception as exc:
            doctor.fail(f"YouTube Analytics API 失败：{exc}")

    return doctor.summary()


if __name__ == "__main__":
    raise SystemExit(main())
