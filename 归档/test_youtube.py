import os
import httplib2

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build


# ==============================
# 基础设置
# ==============================

SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/yt-analytics-monetary.readonly",
]

CLIENT_SECRET_FILE = "client_secret.json"
TOKEN_FILE = "token.json"


# ==============================
# v2rayN 本地代理
# ==============================

PROXY_HOST = "127.0.0.1"
PROXY_PORT = 10808


# ==============================
# 获取 Google OAuth 凭证
# ==============================

def get_credentials():

    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(
            TOKEN_FILE,
            SCOPES
        )

    if not creds or not creds.valid:

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())

        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                CLIENT_SECRET_FILE,
                SCOPES
            )

            creds = flow.run_local_server(
                port=0,
                access_type="offline",
                prompt="consent"
            )

        with open(TOKEN_FILE, "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return creds


# ==============================
# 主程序
# ==============================

def main():

    print("正在读取 OAuth 凭证...")

    creds = get_credentials()

    print("OAuth 凭证读取成功。")
    print("正在通过 v2rayN 连接 YouTube API...")
    print(f"代理地址：http://{PROXY_HOST}:{PROXY_PORT}")

    # 强制 httplib2 走 v2rayN
    proxy_info = httplib2.ProxyInfo(
        proxy_type=httplib2.socks.PROXY_TYPE_HTTP,
        proxy_host=PROXY_HOST,
        proxy_port=PROXY_PORT,
        proxy_rdns=True
    )

    http = httplib2.Http(
        proxy_info=proxy_info,
        timeout=30
    )

    authorized_http = AuthorizedHttp(
        creds,
        http=http
    )

    youtube = build(
        "youtube",
        "v3",
        http=authorized_http,
        cache_discovery=False
    )

    print("YouTube API 客户端创建成功。")
    print("正在读取频道信息...")

    response = youtube.channels().list(
        part="snippet,statistics,contentDetails",
        mine=True
    ).execute()

    if not response.get("items"):
        print("")
        print("没有找到 YouTube 频道。")
        return

    channel = response["items"][0]

    snippet = channel["snippet"]
    statistics = channel["statistics"]

    print("")
    print("======================================")
    print("✅ YouTube API 连接成功")
    print("======================================")

    print(f"频道名称：{snippet.get('title')}")
    print(f"频道 ID：{channel.get('id')}")
    print(
        f"订阅人数："
        f"{statistics.get('subscriberCount', '未提供')}"
    )
    print(
        f"频道总播放量："
        f"{statistics.get('viewCount', '未提供')}"
    )
    print(
        f"视频数量："
        f"{statistics.get('videoCount', '未提供')}"
    )

    print("======================================")
    print("")


if __name__ == "__main__":
    main()