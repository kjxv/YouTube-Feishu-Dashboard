import csv
import httplib2
from datetime import date, timedelta

from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


# ============================================================
# 基础配置
# ============================================================

SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/yt-analytics-monetary.readonly",
]

TOKEN_FILE = "token.json"
VIDEO_FILE = "youtube_videos.csv"
OUTPUT_FILE = "youtube_analytics_daily.csv"

# v2rayN 本地代理
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 10808

# 第一版先读取最近 30 天
DAYS = 30


# ============================================================
# 创建 YouTube Analytics API 客户端
# ============================================================

def build_analytics():

    creds = Credentials.from_authorized_user_file(
        TOKEN_FILE,
        SCOPES
    )

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

    analytics = build(
        "youtubeAnalytics",
        "v2",
        http=authorized_http,
        cache_discovery=False
    )

    return analytics


# ============================================================
# 从 youtube_videos.csv 读取视频
# ============================================================

def load_videos():

    videos = []

    with open(
        VIDEO_FILE,
        "r",
        encoding="utf-8-sig"
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            video_id = row.get(
                "Video ID",
                ""
            ).strip()

            title = row.get(
                "视频标题",
                ""
            ).strip()

            published_at = row.get(
                "发布时间",
                ""
            ).strip()

            if video_id:

                videos.append({
                    "video_id": video_id,
                    "title": title,
                    "published_at": published_at
                })

    return videos


# ============================================================
# 获取某一个视频每天的 Analytics 数据
# ============================================================

def fetch_video_daily(
    analytics,
    video_id,
    start_date,
    end_date
):

    response = analytics.reports().query(

        ids="channel==MINE",

        startDate=str(start_date),

        endDate=str(end_date),

        metrics=(
            "views,"
            "estimatedMinutesWatched,"
            "averageViewDuration,"
            "averageViewPercentage,"
            "subscribersGained,"
            "subscribersLost,"
            "estimatedRevenue"
        ),

        # 关键修改：
        # 这里只使用 day
        dimensions="day",

        # 视频 ID 改成筛选条件
        filters=f"video=={video_id}",

        sort="day"

    ).execute()

    return response.get(
        "rows",
        []
    )


# ============================================================
# 主程序
# ============================================================

def main():

    print("")
    print("正在连接 YouTube Analytics API...")

    analytics = build_analytics()

    print("连接成功。")
    print("")

    print("正在读取视频列表...")

    videos = load_videos()

    total_videos = len(videos)

    print(
        f"共读取到 {total_videos} 个视频。"
    )

    print("")

    # Analytics 数据通常存在处理延迟
    # 所以先抓到昨天
    end_date = (
        date.today()
        - timedelta(days=1)
    )

    start_date = (
        end_date
        - timedelta(days=DAYS - 1)
    )

    print(
        f"统计开始日期：{start_date}"
    )

    print(
        f"统计结束日期：{end_date}"
    )

    print("")

    all_rows = []

    success_count = 0
    empty_count = 0
    error_count = 0

    # ========================================================
    # 循环每一个视频
    # ========================================================

    for index, video in enumerate(
        videos,
        start=1
    ):

        video_id = video[
            "video_id"
        ]

        title = video[
            "title"
        ]

        published_at = video[
            "published_at"
        ]

        print(
            f"[{index}/{total_videos}] "
            f"正在读取：{title[:40]}"
        )

        try:

            rows = fetch_video_daily(
                analytics,
                video_id,
                start_date,
                end_date
            )

            if not rows:

                empty_count += 1

                print(
                    "    没有最近30天数据"
                )

                continue

            # Analytics 返回的每一行：
            #
            # 0 日期
            # 1 views
            # 2 estimatedMinutesWatched
            # 3 averageViewDuration
            # 4 averageViewPercentage
            # 5 subscribersGained
            # 6 subscribersLost
            # 7 estimatedRevenue

            for row in rows:

                all_rows.append({
                    "日期":
                        row[0],

                    "Video ID":
                        video_id,

                    "视频标题":
                        title,

                    "发布时间":
                        published_at,

                    "当日播放量":
                        row[1],

                    "观看时长（分钟）":
                        row[2],

                    "平均观看时长（秒）":
                        row[3],

                    "平均观看百分比":
                        row[4],

                    "新增订阅":
                        row[5],

                    "取消订阅":
                        row[6],

                    "预估收入（USD）":
                        row[7]
                })

            success_count += 1

            print(
                f"    成功，"
                f"{len(rows)} 天数据"
            )

        except HttpError as e:

            error_count += 1

            print(
                "    ❌ API 错误："
                f"{e.resp.status}"
            )

        except Exception as e:

            error_count += 1

            print(
                "    ❌ 读取失败："
                f"{str(e)}"
            )

    # ========================================================
    # 保存 CSV
    # ========================================================

    print("")
    print("正在整理并保存数据...")

    # 按日期 + 视频标题排序
    all_rows.sort(
        key=lambda x: (
            x["日期"],
            x["视频标题"]
        )
    )

    fieldnames = [
        "日期",
        "Video ID",
        "视频标题",
        "发布时间",
        "当日播放量",
        "观看时长（分钟）",
        "平均观看时长（秒）",
        "平均观看百分比",
        "新增订阅",
        "取消订阅",
        "预估收入（USD）"
    ]

    with open(
        OUTPUT_FILE,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames
        )

        writer.writeheader()

        writer.writerows(
            all_rows
        )

    # ========================================================
    # 完成提示
    # ========================================================

    print("")
    print(
        "========================================"
    )

    print(
        "✅ YouTube Analytics 数据抓取完成"
    )

    print(
        "========================================"
    )

    print(
        f"视频总数：{total_videos}"
    )

    print(
        f"成功有数据：{success_count}"
    )

    print(
        f"最近30天无数据：{empty_count}"
    )

    print(
        f"读取错误：{error_count}"
    )

    print(
        f"历史数据总行数：{len(all_rows)}"
    )

    print(
        f"输出文件：{OUTPUT_FILE}"
    )

    print(
        "========================================"
    )

    print("")


if __name__ == "__main__":
    main()