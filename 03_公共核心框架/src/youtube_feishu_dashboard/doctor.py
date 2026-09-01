from __future__ import annotations

import sys
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import text

from youtube_feishu_dashboard.api.feishu.client import FeishuClient
from youtube_feishu_dashboard.api.youtube.auth import YouTubeCredentialProvider
from youtube_feishu_dashboard.app import Application
from youtube_feishu_dashboard.db.migrations import migration_status


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    ok: bool
    message: str
    required: bool = True


def run_doctor(app: Application, *, online: bool = False) -> dict[str, Any]:
    settings = app.settings
    checks: list[CheckResult] = []
    checks.append(
        CheckResult(
            "Python",
            sys.version_info >= (3, 11),
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        )
    )
    try:
        with app.database.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        current, heads = migration_status(settings, app.database)
        checks.append(CheckResult("数据库连接", True, settings.safe_database_url()))
        checks.append(
            CheckResult(
                "数据库迁移",
                current in heads,
                f"current={current or 'none'}, head={','.join(heads)}",
            )
        )
    except Exception as exc:
        checks.append(CheckResult("数据库", False, str(exc)))

    checks.append(
        CheckResult(
            "OAuth 客户端文件",
            settings.youtube_client_secret_path.is_file(),
            str(settings.youtube_client_secret_path),
        )
    )
    token = YouTubeCredentialProvider(
        settings.youtube_client_secret_path, settings.youtube_token_path
    ).token_status()
    token_usable = bool(token.get("valid") or token.get("has_refresh_token"))
    checks.append(CheckResult("YouTube Token", token_usable, str(token)))

    feishu_present = bool(
        settings.feishu_app_id and settings.feishu_app_secret and settings.feishu_base_token
    )
    checks.append(
        CheckResult(
            "飞书基础配置", feishu_present, "已填写" if feishu_present else "缺少凭证或 Base Token"
        )
    )
    if online and feishu_present:
        try:
            assert (
                settings.feishu_app_id and settings.feishu_app_secret and settings.feishu_base_token
            )
            client = FeishuClient(
                app_id=settings.feishu_app_id,
                app_secret=settings.feishu_app_secret.get_secret_value(),
                api_base_url=settings.feishu_api_base_url,
            )
            result = client.check_connection(settings.feishu_base_token)
            checks.append(CheckResult("飞书在线连接", True, str(result)))
        except Exception as exc:
            checks.append(CheckResult("飞书在线连接", False, str(exc)))

    return {
        "ok": all(item.ok for item in checks if item.required),
        "checks": [asdict(item) for item in checks],
    }
