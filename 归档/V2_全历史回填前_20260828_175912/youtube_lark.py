import re
import sys
import time
import httplib2
import requests

try:
    import socks
except ImportError:
    socks = None

from datetime import datetime, timedelta, timezone

from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app_config import ConfigError, YOUTUBE_SCOPES, load_config, resolve_project_path


# ============================================================
# 配置
# ============================================================

try:
    CONFIG = load_config()
except ConfigError as exc:
    print(f"❌ {exc}", file=sys.stderr)
    raise SystemExit(2) from exc

APP_ID = CONFIG["lark"]["app_id"]
APP_SECRET = CONFIG["lark"]["app_secret"]
BASE_TOKEN = CONFIG["lark"]["base_token"]

VIDEO_TABLE_ID = CONFIG["lark"]["video_table_id"]
HISTORY_TABLE_ID = CONFIG["lark"]["history_table_id"]
CHANNEL_TABLE_ID = CONFIG["lark"]["channel_table_id"]

TOKEN_FILE = str(resolve_project_path(CONFIG["youtube"]["token_file"]))

PROXY_ENABLED = CONFIG["proxy"]["enabled"]
PROXY_HOST = CONFIG["proxy"]["host"]
PROXY_PORT = CONFIG["proxy"]["port"]

LARK_API = "https://open.larksuite.com/open-apis"

BATCH_SIZE = 100

# 每日 Analytics 重新检查最近几天
# 防止 YouTube 延迟结算或后续修正数据
ANALYTICS_LOOKBACK_DAYS = CONFIG["update"]["analytics_lookback_days"]

SCOPES = YOUTUBE_SCOPES


# ============================================================
# 通用工具
# ============================================================

def now_ms():

    return int(
        datetime.now(
            timezone.utc
        ).timestamp() * 1000
    )


def date_to_ms(value):

    if not value:
        return None

    dt = datetime.strptime(
        value,
        "%Y-%m-%d"
    )

    dt = dt.replace(
        tzinfo=timezone.utc
    )

    return int(
        dt.timestamp() * 1000
    )


def iso_to_ms(value):

    if not value:
        return None

    try:

        value = value.replace(
            "Z",
            "+00:00"
        )

        dt = datetime.fromisoformat(
            value
        )

        return int(
            dt.timestamp() * 1000
        )

    except Exception:
        return None


def iso_duration_to_text(value):

    if not value:
        return ""

    match = re.match(
        r"PT"
        r"(?:(\d+)H)?"
        r"(?:(\d+)M)?"
        r"(?:(\d+)S)?",
        value
    )

    if not match:
        return value

    hours = int(
        match.group(1) or 0
    )

    minutes = int(
        match.group(2) or 0
    )

    seconds = int(
        match.group(3) or 0
    )

    if hours:

        return (
            f"{hours:02d}:"
            f"{minutes:02d}:"
            f"{seconds:02d}"
        )

    return (
        f"{minutes:02d}:"
        f"{seconds:02d}"
    )


def url_field(url, text):

    if not url:
        return None

    return {
        "link": url,
        "text": text
    }


def chunked(items, size):

    for i in range(
        0,
        len(items),
        size
    ):

        yield items[
            i:i + size
        ]


def optional_int(value):

    if value is None:
        return None

    try:
        return int(float(value))
    except Exception:
        return None


def optional_float(value):

    if value is None:
        return None

    try:
        return float(value)
    except Exception:
        return None


# ============================================================
# Google / YouTube
# ============================================================

def get_credentials():

    if not resolve_project_path(TOKEN_FILE).exists():
        raise FileNotFoundError(
            f"找不到 YouTube OAuth token：{TOKEN_FILE}。"
            "请双击 auth_youtube.bat。"
        )

    return Credentials.from_authorized_user_file(
        TOKEN_FILE,
        SCOPES
    )


def make_google_http(creds):

    if PROXY_ENABLED:

        if socks is None:
            raise RuntimeError(
                "代理功能需要 PySocks。请重新运行 install.bat 安装依赖。"
            )

        proxy_info = httplib2.ProxyInfo(
            proxy_type=socks.PROXY_TYPE_HTTP,
            proxy_host=PROXY_HOST,
            proxy_port=PROXY_PORT,
            proxy_rdns=True
        )

        http = httplib2.Http(
            proxy_info=proxy_info,
            timeout=60
        )

    else:

        http = httplib2.Http(
            timeout=60
        )

    return AuthorizedHttp(
        creds,
        http=http
    )


def build_youtube():

    creds = get_credentials()

    return build(
        "youtube",
        "v3",
        http=make_google_http(
            creds
        ),
        cache_discovery=False
    )


def build_analytics():

    creds = get_credentials()

    return build(
        "youtubeAnalytics",
        "v2",
        http=make_google_http(
            creds
        ),
        cache_discovery=False
    )


# ============================================================
# 获取当前频道和视频
# ============================================================

def fetch_channel_and_videos(youtube):

    response = youtube.channels().list(
        part="statistics,contentDetails",
        mine=True
    ).execute(num_retries=2)

    channel = response[
        "items"
    ][0]

    statistics = channel[
        "statistics"
    ]

    uploads_playlist = (
        channel[
            "contentDetails"
        ][
            "relatedPlaylists"
        ][
            "uploads"
        ]
    )

    snapshot = {
        "订阅人数":
            int(
                statistics.get(
                    "subscriberCount",
                    0
                )
            ),

        "频道总播放量":
            int(
                statistics.get(
                    "viewCount",
                    0
                )
            ),

        "视频数量":
            int(
                statistics.get(
                    "videoCount",
                    0
                )
            )
    }

    video_ids = []

    page_token = None

    while True:

        response = youtube.playlistItems().list(
            part="contentDetails",
            playlistId=uploads_playlist,
            maxResults=50,
            pageToken=page_token
        ).execute(num_retries=2)

        for item in response[
            "items"
        ]:

            video_ids.append(
                item[
                    "contentDetails"
                ][
                    "videoId"
                ]
            )

        page_token = response.get(
            "nextPageToken"
        )

        if not page_token:
            break

    videos = []

    for i in range(
        0,
        len(video_ids),
        50
    ):

        batch = video_ids[
            i:i + 50
        ]

        response = youtube.videos().list(
            part="snippet,statistics,contentDetails",
            id=",".join(batch),
            maxResults=50
        ).execute(num_retries=2)

        for item in response[
            "items"
        ]:

            snippet = item[
                "snippet"
            ]

            stats = item.get(
                "statistics",
                {}
            )

            thumbnails = snippet.get(
                "thumbnails",
                {}
            )

            thumbnail = ""

            if "maxres" in thumbnails:

                thumbnail = thumbnails[
                    "maxres"
                ][
                    "url"
                ]

            elif "high" in thumbnails:

                thumbnail = thumbnails[
                    "high"
                ][
                    "url"
                ]

            elif "default" in thumbnails:

                thumbnail = thumbnails[
                    "default"
                ][
                    "url"
                ]

            video_id = item[
                "id"
            ]

            videos.append({
                "video_id":
                    video_id,

                "title":
                    snippet.get(
                        "title",
                        ""
                    ),

                "published_at":
                    snippet.get(
                        "publishedAt",
                        ""
                    ),

                "url":
                    (
                        "https://www.youtube.com/"
                        f"watch?v={video_id}"
                    ),

                "thumbnail":
                    thumbnail,

                "views":
                    int(
                        stats.get(
                            "viewCount",
                            0
                        )
                    ),

                "likes":
                    int(
                        stats.get(
                            "likeCount",
                            0
                        )
                    ),

                "comments":
                    int(
                        stats.get(
                            "commentCount",
                            0
                        )
                    ),

                "duration":
                    item[
                        "contentDetails"
                    ].get(
                        "duration",
                        ""
                    )
            })

    return snapshot, videos


# ============================================================
# Lark
# ============================================================

def get_lark_token():

    response = requests.post(
        (
            f"{LARK_API}/auth/v3/"
            "tenant_access_token/internal"
        ),
        json={
            "app_id": APP_ID,
            "app_secret": APP_SECRET
        },
        timeout=30
    )

    data = response.json()

    if data.get(
        "code"
    ) != 0:

        raise RuntimeError(
            f"Lark Token 获取失败：{data}"
        )

    return data[
        "tenant_access_token"
    ]


def lark_headers(token):

    return {
        "Authorization":
            f"Bearer {token}",

        "Content-Type":
            "application/json"
    }


def get_all_records(
    token,
    table_id
):

    records = []

    page_token = None

    while True:

        params = {
            "page_size": 500
        }

        if page_token:

            params[
                "page_token"
            ] = page_token

        url = (
            f"{LARK_API}/bitable/v1/"
            f"apps/{BASE_TOKEN}/"
            f"tables/{table_id}/records"
        )

        response = requests.get(
            url,
            headers=lark_headers(
                token
            ),
            params=params,
            timeout=60
        )

        data = response.json()

        if data.get(
            "code"
        ) != 0:

            raise RuntimeError(
                f"Lark 读取记录失败：{data}"
            )

        records.extend(
            data.get(
                "data",
                {}
            ).get(
                "items",
                []
            )
        )

        has_more = (
            data.get(
                "data",
                {}
            ).get(
                "has_more",
                False
            )
        )

        page_token = (
            data.get(
                "data",
                {}
            ).get(
                "page_token"
            )
        )

        if not has_more:
            break

    return records


def make_record_map(
    records,
    key_field
):

    result = {}

    for record in records:

        fields = record.get(
            "fields",
            {}
        )

        key = fields.get(
            key_field
        )

        if key not in (
            None,
            ""
        ):

            result[
                str(key)
            ] = record

    return result


def batch_create(
    token,
    table_id,
    fields_list
):

    if not fields_list:
        return

    url = (
        f"{LARK_API}/bitable/v1/"
        f"apps/{BASE_TOKEN}/"
        f"tables/{table_id}/records/"
        f"batch_create"
    )

    for batch in chunked(
        fields_list,
        BATCH_SIZE
    ):

        response = requests.post(
            url,
            headers=lark_headers(
                token
            ),
            json={
                "records": [
                    {
                        "fields": fields
                    }
                    for fields
                    in batch
                ]
            },
            timeout=60
        )

        data = response.json()

        if data.get(
            "code"
        ) != 0:

            raise RuntimeError(
                f"Lark 新增失败：{data}"
            )

        print(
            f"    新增 {len(batch)} 条"
        )

        time.sleep(0.2)


def batch_update(
    token,
    table_id,
    records
):

    if not records:
        return

    url = (
        f"{LARK_API}/bitable/v1/"
        f"apps/{BASE_TOKEN}/"
        f"tables/{table_id}/records/"
        f"batch_update"
    )

    for batch in chunked(
        records,
        BATCH_SIZE
    ):

        payload = []

        for item in batch:

            payload.append({
                "record_id":
                    item[
                        "record_id"
                    ],

                "fields":
                    item[
                        "fields"
                    ]
            })

        response = requests.post(
            url,
            headers=lark_headers(
                token
            ),
            json={
                "records":
                    payload
            },
            timeout=60
        )

        data = response.json()

        if data.get(
            "code"
        ) != 0:

            raise RuntimeError(
                f"Lark 更新失败：{data}"
            )

        print(
            f"    更新 {len(batch)} 条"
        )

        time.sleep(0.2)


# ============================================================
# CURRENT
# 当前数据模式
# ============================================================

def run_current():

    print("")
    print("================================")
    print("CURRENT：更新当前 YouTube 数据")
    print("================================")

    print("正在连接 YouTube...")

    youtube = build_youtube()

    snapshot, videos = (
        fetch_channel_and_videos(
            youtube
        )
    )

    print(
        f"✅ 获取视频：{len(videos)} 个"
    )

    print(
        f"当前订阅："
        f"{snapshot['订阅人数']}"
    )

    print(
        f"当前总播放："
        f"{snapshot['频道总播放量']}"
    )

    print("")
    print("正在连接 Lark...")

    token = get_lark_token()

    print("✅ Lark 连接成功")


    # --------------------------------------------------------
    # 视频主表
    # --------------------------------------------------------

    print("")
    print("更新：视频主表")

    records = get_all_records(
        token,
        VIDEO_TABLE_ID
    )

    existing = make_record_map(
        records,
        "Video ID"
    )

    creates = []
    updates = []

    for video in videos:

        video_id = video[
            "video_id"
        ]

        fields = {
            "Video ID":
                video_id,

            "视频标题":
                video["title"],

            "发布时间":
                iso_to_ms(
                    video[
                        "published_at"
                    ]
                ),

            "视频链接":
                url_field(
                    video["url"],
                    "打开视频"
                ),

            "缩略图URL":
                url_field(
                    video[
                        "thumbnail"
                    ],
                    "查看缩略图"
                ),

            "当前播放量":
                video["views"],

            "点赞数":
                video["likes"],

            "评论数":
                video["comments"],

            "视频时长":
                iso_duration_to_text(
                    video[
                        "duration"
                    ]
                ),

            "最后同步时间":
                now_ms()
        }

        fields = {
            k: v
            for k, v
            in fields.items()
            if v is not None
        }

        if video_id in existing:

            updates.append({
                "record_id":
                    existing[
                        video_id
                    ][
                        "record_id"
                    ],

                "fields":
                    fields
            })

        else:

            creates.append(
                fields
            )

    print(
        f"新增：{len(creates)}"
    )

    print(
        f"更新：{len(updates)}"
    )

    batch_create(
        token,
        VIDEO_TABLE_ID,
        creates
    )

    batch_update(
        token,
        VIDEO_TABLE_ID,
        updates
    )


    # --------------------------------------------------------
    # 频道当前快照
    # --------------------------------------------------------

    print("")
    print("更新：频道当前快照")

    channel_records = (
        get_all_records(
            token,
            CHANNEL_TABLE_ID
        )
    )

    channel_map = (
        make_record_map(
            channel_records,
            "日期Key"
        )
    )

    # 延续你以前手动统计的日期习惯：
    # 使用电脑本地日期
    today_date = (
        datetime.now().date()
    )

    today = today_date.strftime(
        "%Y-%m-%d"
    )

    yesterday = (
        today_date
        - timedelta(days=1)
    ).strftime(
        "%Y-%m-%d"
    )

    previous_subscribers = None

    if yesterday in channel_map:

        previous_subscribers = (
            channel_map[
                yesterday
            ].get(
                "fields",
                {}
            ).get(
                "订阅人数"
            )
        )

    snapshot_change = None

    if previous_subscribers not in (
        None,
        ""
    ):

        try:

            snapshot_change = (
                snapshot[
                    "订阅人数"
                ]
                - int(
                    float(
                        previous_subscribers
                    )
                )
            )

        except Exception:
            snapshot_change = None

    fields = {
        "日期Key":
            today,

        "日期":
            date_to_ms(
                today
            ),

        "订阅人数":
            snapshot[
                "订阅人数"
            ],

        "频道总播放量":
            snapshot[
                "频道总播放量"
            ],

        "视频数量":
            snapshot[
                "视频数量"
            ],

        "最后同步时间":
            now_ms()
    }

    if snapshot_change is not None:

        fields[
            "订阅快照变化"
        ] = snapshot_change

    if today in channel_map:

        batch_update(
            token,
            CHANNEL_TABLE_ID,
            [{
                "record_id":
                    channel_map[
                        today
                    ][
                        "record_id"
                    ],

                "fields":
                    fields
            }]
        )

        print(
            f"✅ 更新频道快照：{today}"
        )

    else:

        batch_create(
            token,
            CHANNEL_TABLE_ID,
            [fields]
        )

        print(
            f"✅ 新建频道快照：{today}"
        )

    print("")
    print(
        "✅ CURRENT 更新完成"
    )


# ============================================================
# DAILY
# Analytics 日数据模式
# ============================================================

def run_daily():

    print("")
    print("================================")
    print("DAILY：更新 YouTube Analytics")
    print("================================")

    youtube = build_youtube()

    analytics = build_analytics()

    _, videos = fetch_channel_and_videos(
        youtube
    )

    token = get_lark_token()

    end_date = (
        datetime.now().date()
        - timedelta(days=1)
    )

    start_date = (
        end_date
        - timedelta(
            days=(
                ANALYTICS_LOOKBACK_DAYS
                - 1
            )
        )
    )

    print(
        f"重新检查："
        f"{start_date} ~ {end_date}"
    )

    print(
        f"视频数量：{len(videos)}"
    )


    # --------------------------------------------------------
    # 视频历史数据
    # --------------------------------------------------------

    print("")
    print(
        "读取视频历史表..."
    )

    records = get_all_records(
        token,
        HISTORY_TABLE_ID
    )

    existing = make_record_map(
        records,
        "唯一键"
    )

    creates = []
    updates = []

    total_videos = len(
        videos
    )

    for index, video in enumerate(
        videos,
        start=1
    ):

        video_id = video[
            "video_id"
        ]

        print(
            f"[{index}/{total_videos}] "
            f"{video['title'][:35]}"
        )

        try:

            response = analytics.reports().query(

                ids="channel==MINE",

                startDate=str(
                    start_date
                ),

                endDate=str(
                    end_date
                ),

                metrics=(
                    "views,"
                    "estimatedMinutesWatched,"
                    "averageViewDuration,"
                    "averageViewPercentage,"
                    "subscribersGained,"
                    "subscribersLost,"
                    "estimatedRevenue"
                ),

                dimensions="day",

                filters=(
                    f"video=={video_id}"
                ),

                sort="day"

            ).execute(num_retries=2)

            rows = response.get(
                "rows",
                []
            )

            for row in rows:

                day = row[0]

                unique_key = (
                    f"{video_id}_{day}"
                )

                fields = {
                    "唯一键":
                        unique_key,

                    "日期":
                        date_to_ms(
                            day
                        ),

                    "Video ID":
                        video_id,

                    "视频标题":
                        video[
                            "title"
                        ],

                    "发布时间":
                        iso_to_ms(
                            video[
                                "published_at"
                            ]
                        ),

                    "当日播放量":
                        optional_int(
                            row[1]
                        ),

                    "观看时长（分钟）":
                        optional_float(
                            row[2]
                        ),

                    "平均观看时长（秒）":
                        optional_float(
                            row[3]
                        ),

                    "平均观看百分比":
                        optional_float(
                            row[4]
                        ),

                    "新增订阅":
                        optional_int(
                            row[5]
                        ),

                    "取消订阅":
                        optional_int(
                            row[6]
                        ),

                    "预估收入（USD）":
                        optional_float(
                            row[7]
                        ),

                    "最后同步时间":
                        now_ms()
                }

                fields = {
                    k: v
                    for k, v
                    in fields.items()
                    if v is not None
                }

                if unique_key in existing:

                    updates.append({
                        "record_id":
                            existing[
                                unique_key
                            ][
                                "record_id"
                            ],

                        "fields":
                            fields
                    })

                else:

                    creates.append(
                        fields
                    )

        except HttpError as e:

            print(
                f"    ❌ API错误："
                f"{e.resp.status}"
            )

    print("")
    print(
        f"视频历史新增："
        f"{len(creates)}"
    )

    print(
        f"视频历史更新："
        f"{len(updates)}"
    )

    batch_create(
        token,
        HISTORY_TABLE_ID,
        creates
    )

    batch_update(
        token,
        HISTORY_TABLE_ID,
        updates
    )


    # --------------------------------------------------------
    # 频道 Analytics
    # --------------------------------------------------------

    print("")
    print(
        "更新频道 Analytics..."
    )

    response = analytics.reports().query(

        ids="channel==MINE",

        startDate=str(
            start_date
        ),

        endDate=str(
            end_date
        ),

        metrics=(
            "views,"
            "estimatedMinutesWatched,"
            "subscribersGained,"
            "subscribersLost,"
            "estimatedRevenue"
        ),

        dimensions="day",

        sort="day"

    ).execute(num_retries=2)

    channel_records = (
        get_all_records(
            token,
            CHANNEL_TABLE_ID
        )
    )

    channel_map = (
        make_record_map(
            channel_records,
            "日期Key"
        )
    )

    creates = []
    updates = []

    for row in response.get(
        "rows",
        []
    ):

        day = row[0]

        fields = {
            "日期Key":
                day,

            "日期":
                date_to_ms(
                    day
                ),

            "当日播放量":
                optional_int(
                    row[1]
                ),

            "观看时长（分钟）":
                optional_float(
                    row[2]
                ),

            "新增订阅":
                optional_int(
                    row[3]
                ),

            "取消订阅":
                optional_int(
                    row[4]
                ),

            "预估收入（USD）":
                optional_float(
                    row[5]
                ),

            "最后同步时间":
                now_ms()
        }

        fields = {
            k: v
            for k, v
            in fields.items()
            if v is not None
        }

        if day in channel_map:

            updates.append({
                "record_id":
                    channel_map[
                        day
                    ][
                        "record_id"
                    ],

                "fields":
                    fields
            })

        else:

            creates.append(
                fields
            )

    print(
        f"频道历史新增："
        f"{len(creates)}"
    )

    print(
        f"频道历史更新："
        f"{len(updates)}"
    )

    batch_create(
        token,
        CHANNEL_TABLE_ID,
        creates
    )

    batch_update(
        token,
        CHANNEL_TABLE_ID,
        updates
    )

    print("")
    print(
        "✅ DAILY 更新完成"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if len(
        sys.argv
    ) < 2:

        print("")
        print(
            "请选择运行模式："
        )

        print(
            "python youtube_lark.py current"
        )

        print(
            "python youtube_lark.py daily"
        )

        return

    mode = (
        sys.argv[1]
        .lower()
        .strip()
    )

    if mode == "current":

        run_current()

    elif mode == "daily":

        run_daily()

    else:

        print(
            f"未知模式：{mode}"
        )


if __name__ == "__main__":
    main()
