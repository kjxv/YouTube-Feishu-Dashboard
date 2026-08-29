"""Authorize a YouTube account and save its refreshable OAuth token."""

from __future__ import annotations

import argparse
import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

from app_config import (
    CONFIG_FILE,
    YOUTUBE_SCOPES,
    ConfigError,
    load_config,
    resolve_project_path,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="重新授权 YouTube 运营账号")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="不自动打开浏览器，只显示授权链接",
    )
    args = parser.parse_args()

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"❌ {exc}")
        print(f"请先运行 setup.bat 生成 {CONFIG_FILE}")
        return 1

    client_path = resolve_project_path(config["youtube"]["client_secret_file"])
    token_path = resolve_project_path(config["youtube"]["token_file"])
    if not client_path.exists():
        print(f"❌ 找不到 OAuth 客户端文件：{client_path}")
        print("请从 Google Cloud 下载桌面应用 OAuth JSON，并放到上述位置。")
        return 1

    old_proxy = {name: os.environ.get(name) for name in ("HTTP_PROXY", "HTTPS_PROXY")}
    if config["proxy"]["enabled"]:
        proxy_url = f"http://{config['proxy']['host']}:{config['proxy']['port']}"
        os.environ["HTTP_PROXY"] = proxy_url
        os.environ["HTTPS_PROXY"] = proxy_url
        print(f"使用代理：{config['proxy']['host']}:{config['proxy']['port']}")

    print("即将打开浏览器。请登录需要同步的 YouTube 运营账号并同意授权。")
    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(client_path),
            scopes=YOUTUBE_SCOPES,
        )
        credentials = flow.run_local_server(
            host="localhost",
            port=0,
            open_browser=not args.no_browser,
            prompt="consent",
            authorization_prompt_message="请在浏览器完成 YouTube 授权：{url}",
            success_message="YouTube 授权成功，可以关闭此页面。",
        )
    except Exception as exc:
        print(f"❌ YouTube 授权失败：{exc}")
        return 1
    finally:
        for name, value in old_proxy.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    token_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = token_path.with_suffix(token_path.suffix + ".tmp")
    temp_path.write_text(credentials.to_json() + "\n", encoding="utf-8")
    temp_path.replace(token_path)
    print(f"✅ YouTube 授权已保存：{token_path}")
    print("建议现在双击 doctor.bat")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
