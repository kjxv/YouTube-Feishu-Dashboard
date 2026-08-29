import csv
import httplib2
from datetime import date, datetime, timedelta

from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build


SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/yt-analytics-monetary.readonly",
]

TOKEN_FILE = "token.json"
SUBSCRIBER_FILE = "subscriber_history.csv"
OUTPUT_FILE = "youtube_channel_daily.csv"

PROXY_HOST = "127.0.0.1"
PROXY_PORT = 10808


def build_clients():

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

    def make_http():

        http = httplib2.Http(
            proxy_info=proxy_info,
            timeout=30
        )

        return AuthorizedHttp(
            creds,
            http=http
        )

    youtube = build(
        "youtube",
        "v3",
        http=make_http(),
        cache_discovery=False
    )

    analytics = build(
        "youtubeAnalytics",
        "v2",
        http=make_http(),
        cache_discovery=False
    )

    return youtube, analytics


def parse_old_date(value):

    if value is None:
        return None

    try:
        value = str(value).strip()

        if not value:
            return None

        # 兼容：
        # 20260828
        # 20260828.0
        # 2.0260828e+07
        number = int(float(value))

        value = str(number)

        if len(value) != 8:
            return None

        dt = datetime.strptime(
            value,
            "%Y%m%d"
        )

        return dt.strftime(
            "%Y-%m-%d"
        )

    except Exception:
        return None


def load_subscriber_history():

    result = {}

    with open(
        SUBSCRIBER_FILE,
        "r",
        encoding="utf-8-sig"
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            day = parse_old_date(
                row.get("日期")
            )

            if not day:
                continue

            try:
                subscribers = int(
                    float(row.get("订阅数量", 0))
                )
            except Exception:
                subscribers = None

            try:
                manual_change = int(
                    float(row.get("今日增加", 0))
                )
            except Exception:
                manual_change = None

            result[day] = {
                "订阅人数": subscribers,
                "订阅快照变化": manual_change
            }

    return result


def get_current_snapshot(youtube):

    response = youtube.channels().list(
        part="statistics",
        mine=True
    ).execute()

    statistics = (
        response["items"][0]["statistics"]
    )

    return {
        "订阅人数": int(
            statistics.get(
                "subscriberCount",
                0
            )
        ),

        "频道总播放量": int(
            statistics.get(
                "viewCount",
                0
            )
        ),

        "视频数量": int(
            statistics.get(
                "videoCount",
                0
            )
        )
    }


def get_analytics(
    analytics,
    start_date,
    end_date
):

    response = analytics.reports().query(

        ids="channel==MINE",

        startDate=start_date,

        endDate=end_date,

        metrics=(
            "views,"
            "estimatedMinutesWatched,"
            "subscribersGained,"
            "subscribersLost,"
            "estimatedRevenue"
        ),

        dimensions="day",

        sort="day"

    ).execute()

    result = {}

    for row in response.get(
        "rows",
        []
    ):

        result[row[0]] = {
            "当日播放量": row[1],
            "观看时长（分钟）": row[2],
            "新增订阅": row[3],
            "取消订阅": row[4],
            "预估收入（USD）": row[5]
        }

    return result


def main():

    print("")
    print("正在读取历史订阅数据...")

    subscriber_history = (
        load_subscriber_history()
    )

    print(
        f"历史订阅记录："
        f"{len(subscriber_history)} 条"
    )

    if not subscriber_history:
        print("❌ 没有读取到历史订阅数据")
        return

    first_date = min(
        subscriber_history.keys()
    )

    print(
        f"最早订阅记录：{first_date}"
    )

    print("")
    print("正在连接 YouTube...")

    youtube, analytics = (
        build_clients()
    )

    print("✅ YouTube 连接成功")

    snapshot = (
        get_current_snapshot(
            youtube
        )
    )

    today = date.today().strftime(
        "%Y-%m-%d"
    )

    print("")
    print(
        f"当前订阅人数："
        f"{snapshot['订阅人数']}"
    )

    print(
        f"当前频道总播放量："
        f"{snapshot['频道总播放量']}"
    )

    print(
        f"当前公开视频数量："
        f"{snapshot['视频数量']}"
    )

    analytics_end = (
        date.today()
        - timedelta(days=1)
    ).strftime("%Y-%m-%d")

    print("")
    print(
        f"读取 Analytics："
        f"{first_date} ~ "
        f"{analytics_end}"
    )

    analytics_data = get_analytics(
        analytics,
        first_date,
        analytics_end
    )

    print(
        f"Analytics 日数据："
        f"{len(analytics_data)} 条"
    )

    # 日期并集
    all_dates = set(
        subscriber_history.keys()
    )

    all_dates.update(
        analytics_data.keys()
    )

    all_dates.add(today)

    output = []

    for day in sorted(all_dates):

        subscriber = (
            subscriber_history.get(
                day,
                {}
            )
        )

        analytic = (
            analytics_data.get(
                day,
                {}
            )
        )

        row = {
            "日期Key": day,
            "日期": day,

            "订阅人数":
                subscriber.get(
                    "订阅人数",
                    ""
                ),

            "订阅快照变化":
                subscriber.get(
                    "订阅快照变化",
                    ""
                ),

            "频道总播放量": "",
            "视频数量": "",

            "当日播放量":
                analytic.get(
                    "当日播放量",
                    ""
                ),

            "观看时长（分钟）":
                analytic.get(
                    "观看时长（分钟）",
                    ""
                ),

            "新增订阅":
                analytic.get(
                    "新增订阅",
                    ""
                ),

            "取消订阅":
                analytic.get(
                    "取消订阅",
                    ""
                ),

            "预估收入（USD）":
                analytic.get(
                    "预估收入（USD）",
                    ""
                )
        }

        # 今天使用最新 API 快照覆盖
        if day == today:

            row["订阅人数"] = (
                snapshot["订阅人数"]
            )

            row["频道总播放量"] = (
                snapshot[
                    "频道总播放量"
                ]
            )

            row["视频数量"] = (
                snapshot["视频数量"]
            )

        output.append(row)

    fieldnames = [
        "日期Key",
        "日期",
        "订阅人数",
        "订阅快照变化",
        "频道总播放量",
        "视频数量",
        "当日播放量",
        "观看时长（分钟）",
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
        writer.writerows(output)

    print("")
    print(
        "================================"
    )

    print(
        "✅ 频道历史数据合并完成"
    )

    print(
        "================================"
    )

    print(
        f"最终记录：{len(output)} 条"
    )

    print(
        f"输出文件：{OUTPUT_FILE}"
    )

    print(
        "================================"
    )


if __name__ == "__main__":
    main()