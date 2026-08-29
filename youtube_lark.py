import hashlib
import json
import re
import sys
import time
import unicodedata
import httplib2
import requests

try:
    import socks
except ImportError:
    socks = None

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app_config import (
    ConfigError,
    DATA_DIR,
    YOUTUBE_SCOPES,
    load_config,
    resolve_project_path,
    save_config,
)


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
LATEST_TABLE_ID = CONFIG["lark"].get("latest_table_id", "")

TOKEN_FILE = str(resolve_project_path(CONFIG["youtube"]["token_file"]))

PROXY_ENABLED = CONFIG["proxy"]["enabled"]
PROXY_HOST = CONFIG["proxy"]["host"]
PROXY_PORT = CONFIG["proxy"]["port"]

LARK_API = "https://open.larksuite.com/open-apis"

BATCH_SIZE = 100
ANALYTICS_PAGE_SIZE = 200
BACKFILL_CHUNK_DAYS = CONFIG["update"].get("backfill_chunk_days", 90)
LATEST_INTERVAL_MINUTES = CONFIG["update"].get("latest_interval_minutes", 5)
LATEST_TRACKING_HOURS = CONFIG["update"].get("latest_tracking_hours", 168)
ANALYTICS_TIMEZONE = ZoneInfo("America/Los_Angeles")
VIDEO_TYPES_FILE = DATA_DIR / "video_types.json"

VIDEO_TYPE_SHORT = "短视频"
VIDEO_TYPE_LONG = "长视频"
VIDEO_TYPE_LIVE = "直播"
VIDEO_TYPE_UNKNOWN = "未知"
VIDEO_TYPE_VALUES = (
    VIDEO_TYPE_SHORT,
    VIDEO_TYPE_LONG,
    VIDEO_TYPE_LIVE,
    VIDEO_TYPE_UNKNOWN,
)

VIDEO_TYPE_FIELD = {
    "field_name": "视频类型",
    "type": 3,
    "property": {
        "options": [
            {"name": value}
            for value in VIDEO_TYPE_VALUES
        ]
    },
}

CONTENT_RELEASE_FIELD = {
    "field_name": "内容期次",
    "type": 1,
}

LATEST_RELEASE_FIELD = {
    "field_name": "是否最新一期",
    "type": 7,
}

DASHBOARD_VIDEO_FIELDS = [
    CONTENT_RELEASE_FIELD,
    LATEST_RELEASE_FIELD,
    {
        "field_name": "净增订阅",
        "type": 2,
        "property": {"formatter": "0"},
    },
    {
        "field_name": "发布后累计播放量",
        "type": 2,
        "property": {"formatter": "0"},
    },
    {
        "field_name": "发布后累计观看时长",
        "type": 2,
        "property": {"formatter": "0.00"},
    },
    {
        "field_name": "发布后累计净增订阅",
        "type": 2,
        "property": {"formatter": "0"},
    },
    {
        "field_name": "发布后累计收入",
        "type": 2,
        "property": {"formatter": "0.00"},
    },
]

DASHBOARD_CHANNEL_FIELDS = [
    {
        "field_name": "是否最新频道快照",
        "type": 7,
    },
    {
        "field_name": "是否最新Analytics日",
        "type": 7,
    },
]

RELEASE_PAIR_WINDOW = timedelta(hours=12)

VIDEO_ANALYTICS_METRICS = (
    "views,"
    "estimatedMinutesWatched,"
    "averageViewDuration,"
    "averageViewPercentage,"
    "subscribersGained,"
    "subscribersLost,"
    "estimatedRevenue"
)

CHANNEL_ANALYTICS_METRICS = (
    "views,"
    "estimatedMinutesWatched,"
    "subscribersGained,"
    "subscribersLost,"
    "estimatedRevenue"
)

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


def iso_to_analytics_date(value):

    if not value:
        return None

    try:

        parsed = datetime.fromisoformat(
            str(value).replace(
                "Z",
                "+00:00"
            )
        )

        if parsed.tzinfo is None:
            parsed = parsed.replace(
                tzinfo=timezone.utc
            )

        return parsed.astimezone(
            ANALYTICS_TIMEZONE
        ).date()

    except Exception:
        return None


def lark_date_to_date(value):

    if value in (
        None,
        ""
    ):
        return None

    try:

        if isinstance(
            value,
            (int, float)
        ):

            return datetime.fromtimestamp(
                float(value) / 1000,
                timezone.utc
            ).date()

        return datetime.strptime(
            str(value)[:10].replace(
                "/",
                "-"
            ),
            "%Y-%m-%d"
        ).date()

    except Exception:
        return None


def query_analytics_rows(
    analytics,
    start_date,
    end_date,
    metrics,
    filters=None
):

    if start_date > end_date:
        return []

    rows = []
    start_index = 1

    while True:

        parameters = {
            "ids": "channel==MINE",
            "startDate": str(start_date),
            "endDate": str(end_date),
            "metrics": metrics,
            "dimensions": "day",
            "sort": "day",
            "maxResults": ANALYTICS_PAGE_SIZE,
            "startIndex": start_index
        }

        if filters:
            parameters["filters"] = filters

        response = analytics.reports().query(
            **parameters
        ).execute(
            num_retries=2
        )

        page = response.get(
            "rows",
            []
        )

        rows.extend(
            page
        )

        if len(page) < ANALYTICS_PAGE_SIZE:
            break

        start_index += len(page)

    return rows


def make_video_history_fields(
    video,
    row
):

    if not row:
        return None

    day = str(
        row[0]
    )

    try:
        history_date = datetime.strptime(
            day,
            "%Y-%m-%d"
        ).date()
    except ValueError:
        return None

    published_date = iso_to_analytics_date(
        video.get(
            "published_at"
        )
    )

    # YouTube can return zero-filled rows before a video's publication date
    # when a broad date range is queried. Those rows are not real history and
    # make the Lark "发布后天数" formula negative, so never store them.
    if (
        published_date
        and history_date < published_date
    ):
        return None

    values = list(row) + [None] * 8
    video_id = video[
        "video_id"
    ]

    return {
        "唯一键":
            f"{video_id}_{day}",

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

        "视频类型":
            video.get(
                "video_type",
                VIDEO_TYPE_UNKNOWN
            ),

        "内容期次":
            video.get(
                "content_release",
                ""
            ),

        "是否最新一期":
            bool(
                video.get(
                    "is_latest_release",
                    False
                )
            ),

        # YouTube Analytics 的 day 使用美国太平洋时区。直接让飞书用
        # 中国时区的发布时间做 DAYS 公式，在跨日发布时会得到 -1。
        # 由程序按 Analytics 时区计算，既保留真实首日数据又不会为负。
        "发布后天数":
            (
                history_date
                - published_date
            ).days
            if published_date
            else None,

        "当日播放量":
            optional_int(
                values[1]
            ),

        "观看时长（分钟）":
            optional_float(
                values[2]
            ),

        "平均观看时长（秒）":
            optional_float(
                values[3]
            ),

        "平均观看百分比":
            optional_float(
                values[4]
            ),

        "新增订阅":
            optional_int(
                values[5]
            ),

        "取消订阅":
            optional_int(
                values[6]
            ),

        "净增订阅":
            (
                (optional_int(values[5]) or 0)
                - (optional_int(values[6]) or 0)
            ),

        "预估收入（USD）":
            optional_float(
                values[7]
            ),

        "最后同步时间":
            now_ms()
    }


def make_channel_history_fields(row):

    if not row:
        return None

    values = list(row) + [None] * 6
    day = str(
        values[0]
    )

    try:
        datetime.strptime(
            day,
            "%Y-%m-%d"
        )
    except ValueError:
        return None

    return {
        "日期Key":
            day,

        "日期":
            date_to_ms(
                day
            ),

        "当日播放量":
            optional_int(
                values[1]
            ),

        "观看时长（分钟）":
            optional_float(
                values[2]
            ),

        "新增订阅":
            optional_int(
                values[3]
            ),

        "取消订阅":
            optional_int(
                values[4]
            ),

        "预估收入（USD）":
            optional_float(
                values[5]
            ),

        "最后同步时间":
            now_ms()
    }


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


def iso_duration_to_seconds(value):

    if not value:
        return 0

    match = re.fullmatch(
        r"P(?:(\d+)D)?T?"
        r"(?:(\d+)H)?"
        r"(?:(\d+)M)?"
        r"(?:(\d+)S)?",
        str(value)
    )

    if not match:
        return 0

    days = int(match.group(1) or 0)
    hours = int(match.group(2) or 0)
    minutes = int(match.group(3) or 0)
    seconds = int(match.group(4) or 0)

    return (
        days * 86400
        + hours * 3600
        + minutes * 60
        + seconds
    )


def normalize_video_type(value):

    normalized = re.sub(
        r"[^a-z]",
        "",
        str(value or "").lower()
    )

    mapping = {
        "shorts": VIDEO_TYPE_SHORT,
        "videoondemand": VIDEO_TYPE_LONG,
        "livestream": VIDEO_TYPE_LIVE,
        "story": VIDEO_TYPE_UNKNOWN,
        "unspecified": VIDEO_TYPE_UNKNOWN,
    }

    return mapping.get(
        normalized,
        value if value in VIDEO_TYPE_VALUES else VIDEO_TYPE_UNKNOWN
    )


def parse_published_datetime(value):

    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(
            str(value).replace(
                "Z",
                "+00:00"
            )
        )

        if parsed.tzinfo is None:
            parsed = parsed.replace(
                tzinfo=timezone.utc
            )

        return parsed.astimezone(
            timezone.utc
        )

    except Exception:
        return None


def normalize_release_title(value):

    normalized = unicodedata.normalize(
        "NFKC",
        str(value or "")
    ).casefold()

    # 同一期长、短视频标题偶尔只相差开头 emoji、空格或标点。
    # 只保留字母和数字，避免这些展示字符影响配对。
    return "".join(
        character
        for character in normalized
        if character.isalnum()
    )


def assign_content_releases(videos):

    eligible = []

    for video in videos:

        video["content_release"] = ""
        video["is_latest_release"] = False

        video_type = normalize_video_type(
            video.get("video_type")
        )
        published_at = parse_published_datetime(
            video.get("published_at")
        )
        title_key = normalize_release_title(
            video.get("title")
        )

        if (
            video_type not in (
                VIDEO_TYPE_LONG,
                VIDEO_TYPE_SHORT
            )
            or not published_at
            or not title_key
        ):
            continue

        eligible.append((
            published_at,
            title_key,
            video_type,
            video
        ))

    eligible.sort(
        key=lambda item: (
            item[0],
            item[3].get("video_id", "")
        )
    )

    releases = []

    for published_at, title_key, video_type, video in eligible:

        selected = None

        for release in reversed(releases):

            if release["title_key"] != title_key:
                continue

            if (
                published_at - release["last_published_at"]
                > RELEASE_PAIR_WINDOW
            ):
                continue

            if video_type in release["video_types"]:
                continue

            selected = release
            break

        if selected is None:
            selected = {
                "title_key": title_key,
                "first_published_at": published_at,
                "last_published_at": published_at,
                "video_types": set(),
                "videos": [],
            }
            releases.append(selected)

        selected["last_published_at"] = max(
            selected["last_published_at"],
            published_at
        )
        selected["video_types"].add(video_type)
        selected["videos"].append(video)

    if not releases:
        return None

    for release in releases:

        representative = next(
            (
                video
                for video in release["videos"]
                if normalize_video_type(
                    video.get("video_type")
                ) == VIDEO_TYPE_LONG
            ),
            release["videos"][0]
        )
        display_time = release[
            "first_published_at"
        ].astimezone(
            ANALYTICS_TIMEZONE
        ).strftime(
            "%Y-%m-%d %H:%M"
        )
        release["label"] = (
            f"{display_time}｜"
            f"{str(representative.get('title', ''))[:120]}"
        )

        for video in release["videos"]:
            video["content_release"] = release["label"]

    latest_release = max(
        releases,
        key=lambda item: item["last_published_at"]
    )

    for video in latest_release["videos"]:
        video["is_latest_release"] = True

    return latest_release["label"]


def infer_video_type(video):

    duration_seconds = iso_duration_to_seconds(
        video.get("duration")
    )
    aspect_ratio = optional_float(
        video.get("aspect_ratio")
    )

    # YouTube 对普通频道的现行 Shorts 规则：2024-10-15 起上传、
    # 方形或竖屏、时长不超过 3 分钟。Analytics 尚未结算的新视频
    # 暂时按这套官方规则判断，之后会由 creatorContentType 覆盖。
    published_date = iso_to_analytics_date(
        video.get("published_at")
    )

    if (
        published_date
        and published_date >= date(2024, 10, 15)
        and 0 < duration_seconds <= 180
        and aspect_ratio is not None
        and aspect_ratio <= 1.0
    ):
        return VIDEO_TYPE_SHORT

    if duration_seconds > 0:
        return VIDEO_TYPE_LONG

    if video.get("has_live_streaming_details"):
        return VIDEO_TYPE_LIVE

    return VIDEO_TYPE_UNKNOWN


def load_video_type_cache():

    if not VIDEO_TYPES_FILE.exists():
        return {}

    try:
        payload = json.loads(
            VIDEO_TYPES_FILE.read_text(
                encoding="utf-8-sig"
            )
        )
    except Exception:
        return {}

    videos = payload.get("videos", {})

    if not isinstance(videos, dict):
        return {}

    return videos


def fetch_official_video_types(
    analytics,
    videos,
    end_date=None
):

    if not videos:
        return {}

    if end_date is None:
        end_date = (
            datetime.now(
                ANALYTICS_TIMEZONE
            ).date()
            - timedelta(days=1)
        )

    candidates = {}

    # video 过滤器单次最多支持 500 个 ID。必须带 video 过滤器；
    # 当前 API 不接受无过滤器的 video,creatorContentType 组合。
    for batch in chunked(videos, 500):

        published_dates = [
            iso_to_analytics_date(
                video.get("published_at")
            )
            for video in batch
        ]
        published_dates = [
            value
            for value in published_dates
            if value
        ]

        if not published_dates:
            continue

        start_date = min(published_dates)

        if start_date > end_date:
            continue

        filters = "video==" + ",".join(
            video["video_id"]
            for video in batch
        )
        start_index = 1

        while True:

            response = analytics.reports().query(
                ids="channel==MINE",
                startDate=str(start_date),
                endDate=str(end_date),
                metrics="views",
                dimensions="video,creatorContentType",
                filters=filters,
                sort="-views",
                maxResults=ANALYTICS_PAGE_SIZE,
                startIndex=start_index
            ).execute(num_retries=2)

            rows = response.get("rows", [])

            for row in rows:

                values = list(row) + [None] * 3
                video_id = str(values[0] or "")
                video_type = normalize_video_type(
                    values[1]
                )
                views = optional_int(values[2]) or 0

                if not video_id:
                    continue

                current = candidates.get(video_id)

                if (
                    not current
                    or views > current[1]
                ):
                    candidates[video_id] = (
                        video_type,
                        views
                    )

            if len(rows) < ANALYTICS_PAGE_SIZE:
                break

            start_index += len(rows)

    return {
        video_id: value[0]
        for video_id, value in candidates.items()
    }


def resolve_video_types(
    videos,
    analytics=None,
    refresh=False
):

    cache = load_video_type_cache()
    official = {}

    if analytics and refresh:
        official = fetch_official_video_types(
            analytics,
            videos
        )

    resolved = {}
    cache_changed = False

    for video in videos:

        video_id = video["video_id"]
        cached = cache.get(video_id, {})
        cached_type = normalize_video_type(
            cached.get("type")
            if isinstance(cached, dict)
            else cached
        )

        if video_id in official:
            video_type = official[video_id]
            source = "youtubeAnalytics.creatorContentType"
        else:
            inferred = infer_video_type(video)

            if (
                not refresh
                and cached_type != VIDEO_TYPE_UNKNOWN
            ):
                video_type = cached_type
                source = (
                    cached.get("source", "cache")
                    if isinstance(cached, dict)
                    else "cache"
                )
            elif inferred != VIDEO_TYPE_UNKNOWN:
                video_type = inferred
                source = "youtubeData.fallback"
            else:
                video_type = cached_type
                source = (
                    cached.get("source", "unknown")
                    if isinstance(cached, dict)
                    else "unknown"
                )

        video["video_type"] = video_type
        resolved[video_id] = video_type

        if (
            isinstance(cached, dict)
            and cached.get("type") == video_type
            and cached.get("source") == source
        ):
            cache_entry = cached
        else:
            cache_entry = {
                "type": video_type,
                "source": source,
                "updated_at": datetime.now().isoformat(
                    timespec="seconds"
                )
            }

        if cache.get(video_id) != cache_entry:
            cache[video_id] = cache_entry
            cache_changed = True

    if cache_changed:
        save_json_atomic(
            VIDEO_TYPES_FILE,
            {
                "version": 1,
                "updated_at": datetime.now().isoformat(
                    timespec="seconds"
                ),
                "videos": cache
            }
        )

    return resolved


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

def video_file_aspect_ratio(item):

    streams = (
        item.get("fileDetails", {})
        .get("videoStreams", [])
    )

    for stream in streams:

        aspect_ratio = optional_float(
            stream.get("aspectRatio")
        )

        if aspect_ratio and aspect_ratio > 0:
            return aspect_ratio

        width = optional_int(
            stream.get("widthPixels")
        )
        height = optional_int(
            stream.get("heightPixels")
        )

        if width and height:
            return width / height

    return None

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
            part=(
                "snippet,statistics,contentDetails,"
                "fileDetails,liveStreamingDetails"
            ),
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
                    ),

                "aspect_ratio":
                    video_file_aspect_ratio(
                        item
                    ),

                "has_live_streaming_details":
                    bool(
                        item.get(
                            "liveStreamingDetails"
                        )
                    )
            })

    return snapshot, videos


def fetch_latest_video(youtube):

    response = youtube.channels().list(
        part="contentDetails",
        mine=True
    ).execute(num_retries=2)

    items = response.get("items", [])

    if not items:
        raise RuntimeError("当前 OAuth 账号没有可访问的 YouTube 频道")

    uploads_playlist = (
        items[0]
        .get("contentDetails", {})
        .get("relatedPlaylists", {})
        .get("uploads")
    )

    if not uploads_playlist:
        raise RuntimeError("无法读取频道的上传视频列表")

    response = youtube.playlistItems().list(
        part="contentDetails",
        playlistId=uploads_playlist,
        maxResults=10
    ).execute(num_retries=2)

    video_ids = [
        item.get("contentDetails", {}).get("videoId")
        for item in response.get("items", [])
    ]
    video_ids = [video_id for video_id in video_ids if video_id]

    if not video_ids:
        raise RuntimeError("频道上传列表中没有可追踪的视频")

    response = youtube.videos().list(
        part=(
            "snippet,statistics,contentDetails,"
            "fileDetails,liveStreamingDetails"
        ),
        id=",".join(video_ids),
        maxResults=len(video_ids)
    ).execute(num_retries=2)

    candidates = []

    for item in response.get("items", []):
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        published_at = snippet.get("publishedAt", "")

        if not published_at:
            continue

        candidates.append({
            "video_id": item.get("id", ""),
            "title": snippet.get("title", ""),
            "published_at": published_at,
            "url": (
                "https://www.youtube.com/watch?v="
                + item.get("id", "")
            ),
            "views": int(stats.get("viewCount", 0)),
            "likes": int(stats.get("likeCount", 0)),
            "comments": int(stats.get("commentCount", 0)),
            "duration": (
                item.get("contentDetails", {})
                .get("duration", "")
            ),
            "aspect_ratio": video_file_aspect_ratio(item),
            "has_live_streaming_details": bool(
                item.get("liveStreamingDetails")
            ),
        })

    if not candidates:
        raise RuntimeError("没有找到可读取统计数据的最新视频")

    return max(
        candidates,
        key=lambda video: video["published_at"]
    )


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


def sync_video_type_records(
    token,
    table_id,
    records,
    video_types
):

    updates = []

    for record in records:

        fields = record.get("fields", {})
        video_id = str(fields.get("Video ID", ""))
        video_type = video_types.get(video_id)

        if not video_type:
            continue

        raw_current = fields.get("视频类型")
        current = normalize_video_type(raw_current)

        if raw_current not in (None, "") and current == video_type:
            continue

        updates.append({
            "record_id": record["record_id"],
            "fields": {
                "视频类型": video_type
            }
        })

    batch_update(
        token,
        table_id,
        updates
    )

    return len(updates)


def batch_delete(
    token,
    table_id,
    record_ids
):

    if not record_ids:
        return

    url = (
        f"{LARK_API}/bitable/v1/"
        f"apps/{BASE_TOKEN}/"
        f"tables/{table_id}/records/"
        f"batch_delete"
    )

    for batch in chunked(
        record_ids,
        BATCH_SIZE
    ):

        response = requests.post(
            url,
            headers=lark_headers(
                token
            ),
            json={
                "records":
                    list(batch)
            },
            timeout=60
        )

        data = response.json()

        if data.get(
            "code"
        ) != 0:

            raise RuntimeError(
                f"Lark 删除失败：{data}"
            )

        print(
            f"    删除 {len(batch)} 条无效记录"
        )

        time.sleep(0.2)


def checkbox_value(value):

    if isinstance(value, bool):
        return value

    return str(value or "").strip().lower() in (
        "1",
        "true",
        "yes"
    )


def lark_value_matches(current, desired):

    if isinstance(desired, bool):
        return checkbox_value(current) == desired

    if isinstance(desired, (int, float)):
        try:
            return abs(
                float(current) - float(desired)
            ) < 0.000001
        except Exception:
            return False

    return str(current or "") == str(desired or "")


def changed_fields(record, desired):

    current = record.get("fields", {})

    return {
        field_name: value
        for field_name, value in desired.items()
        if not lark_value_matches(
            current.get(field_name),
            value
        )
    }


def sync_video_main_records(
    token,
    videos
):

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

        video_id = video["video_id"]
        fields = {
            "视频类型": video.get(
                "video_type",
                VIDEO_TYPE_UNKNOWN
            ),
            "内容期次": video.get(
                "content_release",
                ""
            ),
            "是否最新一期": bool(
                video.get(
                    "is_latest_release",
                    False
                )
            ),
            "Video ID": video_id,
            "视频标题": video["title"],
            "发布时间": iso_to_ms(
                video["published_at"]
            ),
            "视频链接": url_field(
                video["url"],
                "打开视频"
            ),
            "缩略图URL": url_field(
                video["thumbnail"],
                "查看缩略图"
            ),
            "当前播放量": video["views"],
            "点赞数": video["likes"],
            "评论数": video["comments"],
            "视频时长": iso_duration_to_text(
                video["duration"]
            ),
            "最后同步时间": now_ms()
        }
        fields = {
            key: value
            for key, value in fields.items()
            if value is not None
        }

        if video_id in existing:
            updates.append({
                "record_id": existing[video_id]["record_id"],
                "fields": fields
            })
        else:
            creates.append(fields)

    print(f"新增：{len(creates)}")
    print(f"更新：{len(updates)}")
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


def sync_channel_snapshot_record(
    token,
    snapshot
):

    print("")
    print("更新：频道当前快照")

    channel_records = get_all_records(
        token,
        CHANNEL_TABLE_ID
    )
    channel_map = make_record_map(
        channel_records,
        "日期Key"
    )
    today_date = datetime.now().date()
    today = today_date.strftime("%Y-%m-%d")
    yesterday = (
        today_date - timedelta(days=1)
    ).strftime("%Y-%m-%d")
    previous_subscribers = None

    if yesterday in channel_map:
        previous_subscribers = channel_map[
            yesterday
        ].get("fields", {}).get("订阅人数")

    snapshot_change = None

    if previous_subscribers not in (None, ""):
        try:
            snapshot_change = (
                snapshot["订阅人数"]
                - int(float(previous_subscribers))
            )
        except Exception:
            snapshot_change = None

    fields = {
        "日期Key": today,
        "日期": date_to_ms(today),
        "订阅人数": snapshot["订阅人数"],
        "频道总播放量": snapshot["频道总播放量"],
        "视频数量": snapshot["视频数量"],
        "最后同步时间": now_ms()
    }

    if snapshot_change is not None:
        fields["订阅快照变化"] = snapshot_change

    if today in channel_map:
        batch_update(
            token,
            CHANNEL_TABLE_ID,
            [{
                "record_id": channel_map[today]["record_id"],
                "fields": fields
            }]
        )
        print(f"✅ 更新频道快照：{today}")
    else:
        batch_create(
            token,
            CHANNEL_TABLE_ID,
            [fields]
        )
        print(f"✅ 新建频道快照：{today}")


def sync_dashboard_video_helpers(
    token,
    videos
):

    assign_content_releases(videos)
    video_map = {
        video["video_id"]: video
        for video in videos
    }
    history_records = get_all_records(
        token,
        HISTORY_TABLE_ID
    )
    history_by_video = {}

    for record in history_records:
        video_id = str(
            record.get("fields", {}).get("Video ID", "")
        )
        if video_id:
            history_by_video.setdefault(
                video_id,
                []
            ).append(record)

    history_updates = []
    latest_totals = {}

    for video_id, records in history_by_video.items():

        records.sort(
            key=lambda record: (
                lark_date_to_date(
                    record.get("fields", {}).get("日期")
                ) or date.min,
                record.get("record_id", "")
            )
        )
        cumulative_views = 0
        cumulative_watch = 0.0
        cumulative_net_subscribers = 0
        cumulative_revenue = 0.0
        video = video_map.get(video_id, {})
        content_release = video.get(
            "content_release",
            ""
        )
        is_latest = bool(
            video.get(
                "is_latest_release",
                False
            )
        )

        for record in records:

            fields = record.get("fields", {})
            post_publish_days = optional_int(
                fields.get("发布后天数")
            )

            if (
                post_publish_days is not None
                and post_publish_days < 0
            ):
                raise RuntimeError(
                    "视频历史表仍存在负数发布后天数："
                    f"{video_id} / {post_publish_days}"
                )

            net_subscribers = (
                (optional_int(fields.get("新增订阅")) or 0)
                - (optional_int(fields.get("取消订阅")) or 0)
            )
            cumulative_views += (
                optional_int(fields.get("当日播放量")) or 0
            )
            cumulative_watch += (
                optional_float(
                    fields.get("观看时长（分钟）")
                ) or 0.0
            )
            cumulative_net_subscribers += net_subscribers
            cumulative_revenue += (
                optional_float(
                    fields.get("预估收入（USD）")
                ) or 0.0
            )
            desired = {
                "内容期次": content_release,
                "是否最新一期": is_latest,
                "净增订阅": net_subscribers,
                "发布后累计播放量": cumulative_views,
                "发布后累计观看时长": round(
                    cumulative_watch,
                    6
                ),
                "发布后累计净增订阅": (
                    cumulative_net_subscribers
                ),
                "发布后累计收入": round(
                    cumulative_revenue,
                    6
                ),
            }
            changed = changed_fields(
                record,
                desired
            )

            if changed:
                history_updates.append({
                    "record_id": record["record_id"],
                    "fields": changed
                })

            latest_totals[video_id] = desired

    batch_update(
        token,
        HISTORY_TABLE_ID,
        history_updates
    )

    main_records = get_all_records(
        token,
        VIDEO_TABLE_ID
    )
    main_updates = []

    for record in main_records:

        fields = record.get("fields", {})
        video_id = str(fields.get("Video ID", ""))
        video = video_map.get(video_id)

        if not video:
            desired = {
                "是否最新一期": False
            }
        else:
            totals = latest_totals.get(video_id, {})
            cumulative_net = optional_int(
                totals.get("发布后累计净增订阅")
            ) or 0
            desired = {
                "内容期次": video.get(
                    "content_release",
                    ""
                ),
                "是否最新一期": bool(
                    video.get(
                        "is_latest_release",
                        False
                    )
                ),
                # 主表没有日粒度，净增订阅表示截至最新 Analytics 日
                # 的发布后累计净增订阅。
                "净增订阅": cumulative_net,
                "发布后累计播放量": optional_int(
                    totals.get("发布后累计播放量")
                ) or 0,
                "发布后累计观看时长": optional_float(
                    totals.get("发布后累计观看时长")
                ) or 0.0,
                "发布后累计净增订阅": cumulative_net,
                "发布后累计收入": optional_float(
                    totals.get("发布后累计收入")
                ) or 0.0,
            }

        changed = changed_fields(
            record,
            desired
        )

        if changed:
            main_updates.append({
                "record_id": record["record_id"],
                "fields": changed
            })

    batch_update(
        token,
        VIDEO_TABLE_ID,
        main_updates
    )
    print(
        "仪表盘辅助字段："
        f"视频历史更新 {len(history_updates)}，"
        f"视频主表更新 {len(main_updates)}"
    )

    return {
        "history_updates": len(history_updates),
        "main_updates": len(main_updates),
    }


def sync_dashboard_channel_helpers(token):

    records = get_all_records(
        token,
        CHANNEL_TABLE_ID
    )
    dated_records = [
        (
            lark_date_to_date(
                record.get("fields", {}).get("日期")
            ),
            record
        )
        for record in records
    ]
    dated_records = [
        item
        for item in dated_records
        if item[0]
    ]

    snapshot_records = [
        item
        for item in dated_records
        if any(
            item[1].get("fields", {}).get(field_name)
            not in (None, "")
            for field_name in (
                "订阅人数",
                "频道总播放量",
                "视频数量"
            )
        )
    ]
    analytics_records = [
        item
        for item in dated_records
        if any(
            item[1].get("fields", {}).get(field_name)
            not in (None, "")
            for field_name in (
                "当日播放量",
                "观看时长（分钟）",
                "新增订阅",
                "取消订阅",
                "预估收入（USD）"
            )
        )
    ]
    latest_snapshot_id = (
        max(snapshot_records, key=lambda item: item[0])[1]["record_id"]
        if snapshot_records
        else None
    )
    latest_analytics_id = (
        max(analytics_records, key=lambda item: item[0])[1]["record_id"]
        if analytics_records
        else None
    )
    updates = []

    for record in records:
        desired = {
            "是否最新频道快照": (
                record.get("record_id") == latest_snapshot_id
            ),
            "是否最新Analytics日": (
                record.get("record_id") == latest_analytics_id
            ),
        }
        changed = changed_fields(
            record,
            desired
        )

        if changed:
            updates.append({
                "record_id": record["record_id"],
                "fields": changed
            })

    batch_update(
        token,
        CHANNEL_TABLE_ID,
        updates
    )
    print(
        f"频道最新标记更新：{len(updates)} 条"
    )

    return len(updates)


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

    resolve_video_types(videos)
    latest_release = assign_content_releases(
        videos
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

    ensure_video_type_fields(token)

    ensure_dashboard_fields(token)

    print("✅ Lark 连接成功")

    if latest_release:
        print(
            f"最新内容期次：{latest_release}"
        )

    sync_video_main_records(
        token,
        videos
    )

    sync_channel_snapshot_record(
        token,
        snapshot
    )

    sync_dashboard_video_helpers(
        token,
        videos
    )

    sync_dashboard_channel_helpers(
        token
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

    snapshot, videos = fetch_channel_and_videos(
        youtube
    )

    resolve_video_types(
        videos,
        analytics=analytics,
        refresh=True
    )

    latest_release = assign_content_releases(
        videos
    )

    token = get_lark_token()

    ensure_video_type_fields(token)

    ensure_dashboard_fields(token)

    ensure_post_publish_days_number_field(
        token
    )

    if latest_release:
        print(
            f"最新内容期次：{latest_release}"
        )

    # 每日任务同时刷新当前统计，仪表盘不再依赖额外的实时任务。
    sync_video_main_records(
        token,
        videos
    )

    sync_channel_snapshot_record(
        token,
        snapshot
    )

    end_date = (
        datetime.now(
            ANALYTICS_TIMEZONE
        ).date()
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
    skipped_before_publish = 0

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

            published_date = iso_to_analytics_date(
                video.get(
                    "published_at"
                )
            )

            video_start_date = start_date

            if (
                published_date
                and published_date > video_start_date
            ):

                video_start_date = published_date

            rows = query_analytics_rows(
                analytics,
                video_start_date,
                end_date,
                VIDEO_ANALYTICS_METRICS,
                filters=f"video=={video_id}"
            )

            for row in rows:

                fields = make_video_history_fields(
                    video,
                    row
                )

                if not fields:

                    skipped_before_publish += 1
                    continue

                fields = {
                    k: v
                    for k, v
                    in fields.items()
                    if v is not None
                }

                unique_key = fields[
                    "唯一键"
                ]

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

    if skipped_before_publish:

        print(
            f"已跳过发布前无效行："
            f"{skipped_before_publish}"
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

    rows = query_analytics_rows(
        analytics,
        start_date,
        end_date,
        CHANNEL_ANALYTICS_METRICS
    )

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

    for row in rows:

        fields = make_channel_history_fields(
            row
        )

        if not fields:
            continue

        fields = {
            k: v
            for k, v
            in fields.items()
            if v is not None
        }

        day = fields[
            "日期Key"
        ]

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
    print("更新仪表盘辅助字段...")

    sync_dashboard_video_helpers(
        token,
        videos
    )

    sync_dashboard_channel_helpers(
        token
    )

    print("")
    print(
        "✅ DAILY 更新完成"
    )


# ============================================================
# BACKFILL
# 从频道创建日开始的一次性全历史回填
# ============================================================

BACKFILL_CHECKPOINT_FILE = (
    DATA_DIR / "backfill_checkpoint.json"
)


def save_json_atomic(
    path,
    payload
):

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temporary = path.with_suffix(
        path.suffix + ".tmp"
    )

    temporary.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            default=str
        ) + "\n",
        encoding="utf-8"
    )

    temporary.replace(
        path
    )


def load_backfill_checkpoint(
    identity,
    start_date,
    end_date
):

    checkpoint = None

    if BACKFILL_CHECKPOINT_FILE.exists():

        try:
            checkpoint = json.loads(
                BACKFILL_CHECKPOINT_FILE.read_text(
                    encoding="utf-8-sig"
                )
            )
        except Exception:
            checkpoint = None

    if (
        not isinstance(
            checkpoint,
            dict
        )
        or checkpoint.get(
            "identity"
        ) != identity
        or checkpoint.get(
            "start_date"
        ) != str(start_date)
        or checkpoint.get(
            "end_date"
        ) != str(end_date)
    ):

        checkpoint = {
            "version": 1,
            "identity": identity,
            "start_date": str(start_date),
            "end_date": str(end_date),
            "completed_video_ids": [],
            "channel_completed_through": None,
            "status": "running",
            "created_at": datetime.now().isoformat(
                timespec="seconds"
            )
        }

        save_json_atomic(
            BACKFILL_CHECKPOINT_FILE,
            checkpoint
        )

    return checkpoint


def find_invalid_video_history(
    records,
    videos
):

    publish_dates = {
        video["video_id"]:
            iso_to_analytics_date(
                video.get(
                    "published_at"
                )
            )
        for video in videos
    }

    invalid = []

    for record in records:

        fields = record.get(
            "fields",
            {}
        )

        video_id = str(
            fields.get(
                "Video ID",
                ""
            )
        )

        published_date = publish_dates.get(
            video_id
        )

        history_date = lark_date_to_date(
            fields.get(
                "日期"
            )
        )

        if (
            published_date
            and history_date
            and history_date < published_date
        ):

            invalid.append(
                record
            )

    return invalid


def get_table_fields(
    token,
    table_id
):

    url = (
        f"{LARK_API}/bitable/v1/"
        f"apps/{BASE_TOKEN}/"
        f"tables/{table_id}/fields"
    )

    response = requests.get(
        url,
        headers=lark_headers(
            token
        ),
        params={
            "page_size": 100
        },
        timeout=60
    )

    data = response.json()

    if data.get(
        "code"
    ) != 0:

        raise RuntimeError(
            f"Lark 读取字段失败：{data}"
        )

    return data.get(
        "data",
        {}
    ).get(
        "items",
        []
    )


LATEST_TABLE_NAME = "最新视频实时追踪"

LATEST_TABLE_FIELDS = [
    {
        "field_name": "采集时间",
        "type": 5,
        "property": {
            "date_formatter": "yyyy-MM-dd HH:mm",
            "auto_fill": False
        }
    },
    {"field_name": "Video ID", "type": 1},
    VIDEO_TYPE_FIELD,
    {"field_name": "视频标题", "type": 1},
    {
        "field_name": "发布时间",
        "type": 5,
        "property": {
            "date_formatter": "yyyy-MM-dd HH:mm",
            "auto_fill": False
        }
    },
    {
        "field_name": "发布后分钟数",
        "type": 2,
        "property": {"formatter": "0"}
    },
    {
        "field_name": "当前播放量",
        "type": 2,
        "property": {"formatter": "0"}
    },
    {
        "field_name": "播放增量",
        "type": 2,
        "property": {"formatter": "0"}
    },
    {
        "field_name": "当前点赞数",
        "type": 2,
        "property": {"formatter": "0"}
    },
    {
        "field_name": "点赞增量",
        "type": 2,
        "property": {"formatter": "0"}
    },
    {
        "field_name": "当前评论数",
        "type": 2,
        "property": {"formatter": "0"}
    },
    {
        "field_name": "评论增量",
        "type": 2,
        "property": {"formatter": "0"}
    },
    {"field_name": "视频链接", "type": 15},
    {
        "field_name": "最后同步时间",
        "type": 5,
        "property": {
            "date_formatter": "yyyy-MM-dd HH:mm",
            "auto_fill": False
        }
    },
]


def save_latest_table_id(table_id):

    config = load_config()
    config["lark"]["latest_table_id"] = table_id
    save_config(config)


def find_table_by_name(token, table_name):

    url = (
        f"{LARK_API}/bitable/v1/"
        f"apps/{BASE_TOKEN}/tables"
    )
    page_token = None

    while True:

        params = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token

        response = requests.get(
            url,
            headers=lark_headers(token),
            params=params,
            timeout=60
        )
        data = response.json()

        if data.get("code") != 0:
            raise RuntimeError(f"Lark 读取数据表失败：{data}")

        payload = data.get("data", {})
        for table in payload.get("items", []):
            if table.get("name") == table_name:
                return table.get("table_id")

        if not payload.get("has_more"):
            return None

        page_token = payload.get("page_token")


def create_latest_tracking_table(token):

    url = (
        f"{LARK_API}/bitable/v1/"
        f"apps/{BASE_TOKEN}/tables"
    )
    response = requests.post(
        url,
        headers=lark_headers(token),
        json={
            "table": {
                "name": LATEST_TABLE_NAME,
                "default_view_name": "表格",
                "fields": [
                    {
                        "field_name": "唯一键",
                        "type": 1
                    }
                ]
            }
        },
        timeout=60
    )
    data = response.json()

    if data.get("code") != 0:
        raise RuntimeError(f"Lark 创建实时追踪表失败：{data}")

    payload = data.get("data", {})
    table_id = payload.get("table_id")
    if not table_id and isinstance(payload.get("table"), dict):
        table_id = payload["table"].get("table_id")

    if not table_id:
        raise RuntimeError(f"Lark 创建表成功但未返回 table_id：{data}")

    print(f"✅ 已创建多维表格：{LATEST_TABLE_NAME}（{table_id}）")
    return table_id


def update_lark_field(token, table_id, field_id, definition):

    url = (
        f"{LARK_API}/bitable/v1/"
        f"apps/{BASE_TOKEN}/tables/{table_id}/fields/{field_id}"
    )
    response = requests.put(
        url,
        headers=lark_headers(token),
        json=definition,
        timeout=60
    )
    data = response.json()

    if data.get("code") != 0:
        raise RuntimeError(f"Lark 更新字段失败：{data}")


def create_lark_field(token, table_id, definition):

    url = (
        f"{LARK_API}/bitable/v1/"
        f"apps/{BASE_TOKEN}/tables/{table_id}/fields"
    )
    response = requests.post(
        url,
        headers=lark_headers(token),
        json=definition,
        timeout=60
    )
    data = response.json()

    if data.get("code") != 0:
        raise RuntimeError(
            f"Lark 创建字段 {definition['field_name']} 失败：{data}"
        )


def ensure_video_type_field(
    token,
    table_id,
    table_label
):

    if not table_id:
        return

    fields = get_table_fields(
        token,
        table_id
    )
    current = next(
        (
            field
            for field in fields
            if field.get("field_name") == "视频类型"
        ),
        None
    )

    if current:
        if current.get("type") not in (1, 3):
            raise RuntimeError(
                f"{table_label}的视频类型字段必须是文本或单选字段；"
                f"当前类型：{current.get('type')}"
            )
        return

    create_lark_field(
        token,
        table_id,
        VIDEO_TYPE_FIELD
    )
    print(f"✅ {table_label}已创建字段：视频类型")


def ensure_video_type_fields(token):

    ensure_video_type_field(
        token,
        VIDEO_TABLE_ID,
        "视频主表"
    )
    ensure_video_type_field(
        token,
        HISTORY_TABLE_ID,
        "视频历史表"
    )

    if LATEST_TABLE_ID:
        ensure_video_type_field(
            token,
            LATEST_TABLE_ID,
            "最新视频实时追踪表"
        )


def ensure_fields(
    token,
    table_id,
    table_label,
    definitions
):

    fields = get_table_fields(
        token,
        table_id
    )
    fields_by_name = {
        field.get("field_name"): field
        for field in fields
    }

    for definition in definitions:

        field_name = definition["field_name"]
        current = fields_by_name.get(field_name)

        if current:
            if current.get("type") != definition["type"]:
                raise RuntimeError(
                    f"{table_label}字段类型不符：{field_name}；"
                    f"期望 {definition['type']}，"
                    f"当前 {current.get('type')}"
                )
            continue

        create_lark_field(
            token,
            table_id,
            definition
        )
        print(
            f"✅ {table_label}已创建字段：{field_name}"
        )


def ensure_dashboard_fields(token):

    ensure_fields(
        token,
        VIDEO_TABLE_ID,
        "视频主表",
        DASHBOARD_VIDEO_FIELDS
    )
    ensure_fields(
        token,
        HISTORY_TABLE_ID,
        "视频历史表",
        DASHBOARD_VIDEO_FIELDS
    )
    ensure_fields(
        token,
        CHANNEL_TABLE_ID,
        "频道历史表",
        DASHBOARD_CHANNEL_FIELDS
    )


def ensure_latest_tracking_table(token):

    table_id = str(LATEST_TABLE_ID or "").strip()

    if table_id:
        try:
            fields = get_table_fields(token, table_id)
        except Exception:
            table_id = ""
            fields = []
    else:
        fields = []

    if not table_id:
        table_id = find_table_by_name(token, LATEST_TABLE_NAME)

        if table_id:
            print(f"✅ 已找到现有多维表格：{LATEST_TABLE_NAME}")
        else:
            table_id = create_latest_tracking_table(token)

        save_latest_table_id(table_id)
        fields = get_table_fields(token, table_id)

    primary = next(
        (field for field in fields if field.get("is_primary")),
        None
    )

    if not primary:
        raise RuntimeError("实时追踪表没有主字段")

    if primary.get("field_name") != "唯一键":
        update_lark_field(
            token,
            table_id,
            primary["field_id"],
            {"field_name": "唯一键", "type": 1}
        )
        print("✅ 已设置实时追踪表主字段：唯一键")

    fields = get_table_fields(token, table_id)
    fields_by_name = {
        field.get("field_name"): field
        for field in fields
    }

    for definition in LATEST_TABLE_FIELDS:

        current = fields_by_name.get(definition["field_name"])

        if current:
            if current.get("type") != definition["type"]:
                raise RuntimeError(
                    f"实时追踪表字段类型不符：{definition['field_name']}"
                )
            continue

        create_lark_field(token, table_id, definition)
        print(f"✅ 已创建字段：{definition['field_name']}")

    return table_id


def lark_datetime_to_ms(value):

    if isinstance(value, (int, float)):
        return int(value)

    if not value:
        return 0

    try:
        parsed = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1000)
    except Exception:
        return 0


def run_latest():

    print("")
    print("================================")
    print("LATEST：最新视频近实时追踪")
    print("================================")

    youtube = build_youtube()
    latest = fetch_latest_video(youtube)
    resolve_video_types([latest])
    token = get_lark_token()
    table_id = ensure_latest_tracking_table(token)

    published = datetime.fromisoformat(
        latest["published_at"].replace("Z", "+00:00")
    )
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)

    current_time = datetime.now(timezone.utc)
    age_minutes = max(
        0,
        int((current_time - published).total_seconds() // 60)
    )
    tracking_minutes = int(LATEST_TRACKING_HOURS) * 60

    print(f"最新视频：{latest['title']}")
    print(f"Video ID：{latest['video_id']}")
    print(f"发布后：{age_minutes} 分钟")

    if age_minutes > tracking_minutes:
        print(
            f"ℹ️ 最新视频已超过 {LATEST_TRACKING_HOURS} 小时追踪期，"
            "等待下一条新视频。"
        )
        print("✅ LATEST 检查完成（本次无需写入）")
        return

    interval_seconds = int(LATEST_INTERVAL_MINUTES) * 60
    bucket_seconds = (
        int(current_time.timestamp())
        // interval_seconds
        * interval_seconds
    )
    bucket_time = datetime.fromtimestamp(
        bucket_seconds,
        timezone.utc
    )
    bucket_ms = bucket_seconds * 1000
    key_time = bucket_time.astimezone(
        ZoneInfo("Asia/Shanghai")
    ).strftime("%Y-%m-%d_%H-%M")
    unique_key = f"{latest['video_id']}_{key_time}"

    records = get_all_records(token, table_id)
    existing = make_record_map(records, "唯一键")
    previous = None
    previous_time = -1

    for record in records:
        fields = record.get("fields", {})
        if str(fields.get("Video ID", "")) != latest["video_id"]:
            continue
        if str(fields.get("唯一键", "")) == unique_key:
            continue
        captured_ms = lark_datetime_to_ms(fields.get("采集时间"))
        if captured_ms <= bucket_ms and captured_ms > previous_time:
            previous = fields
            previous_time = captured_ms

    previous_views = optional_int(
        previous.get("当前播放量") if previous else None
    )
    previous_likes = optional_int(
        previous.get("当前点赞数") if previous else None
    )
    previous_comments = optional_int(
        previous.get("当前评论数") if previous else None
    )

    fields = {
        "视频类型": latest.get(
            "video_type",
            VIDEO_TYPE_UNKNOWN
        ),
        "唯一键": unique_key,
        "采集时间": bucket_ms,
        "Video ID": latest["video_id"],
        "视频标题": latest["title"],
        "发布时间": iso_to_ms(latest["published_at"]),
        "发布后分钟数": age_minutes,
        "当前播放量": latest["views"],
        "播放增量": (
            latest["views"] - previous_views
            if previous_views is not None else 0
        ),
        "当前点赞数": latest["likes"],
        "点赞增量": (
            latest["likes"] - previous_likes
            if previous_likes is not None else 0
        ),
        "当前评论数": latest["comments"],
        "评论增量": (
            latest["comments"] - previous_comments
            if previous_comments is not None else 0
        ),
        "视频链接": url_field(latest["url"], "打开视频"),
        "最后同步时间": now_ms(),
    }

    current_record = existing.get(unique_key)
    if current_record:
        batch_update(
            token,
            table_id,
            [{
                "record_id": current_record["record_id"],
                "fields": fields
            }]
        )
        action = "更新"
    else:
        batch_create(token, table_id, [fields])
        action = "新增"

    print(
        f"{action}追踪快照：播放 {latest['views']}，"
        f"点赞 {latest['likes']}，评论 {latest['comments']}"
    )
    print("✅ LATEST 追踪完成")


def ensure_post_publish_days_number_field(
    token
):

    fields = get_table_fields(
        token,
        HISTORY_TABLE_ID
    )

    field = next(
        (
            item
            for item in fields
            if item.get(
                "field_name"
            ) == "发布后天数"
        ),
        None
    )

    if not field:
        raise RuntimeError(
            "视频历史表缺少字段：发布后天数"
        )

    if field.get(
        "type"
    ) == 2:

        return field[
            "field_id"
        ]

    if field.get(
        "type"
    ) != 20:

        raise RuntimeError(
            "发布后天数必须是数字字段；"
            f"当前字段类型：{field.get('type')}"
        )

    backup_path = (
        DATA_DIR
        / "backups"
        / (
            "post_publish_days_field_"
            + datetime.now().strftime(
                "%Y%m%d_%H%M%S"
            )
            + ".json"
        )
    )

    save_json_atomic(
        backup_path,
        {
            "reason": (
                "将易受时区影响的 DAYS 公式字段迁移为"
                "由 Python 按 YouTube Analytics 时区计算的数字字段"
            ),
            "created_at": datetime.now().isoformat(
                timespec="seconds"
            ),
            "field": field
        }
    )

    url = (
        f"{LARK_API}/bitable/v1/"
        f"apps/{BASE_TOKEN}/"
        f"tables/{HISTORY_TABLE_ID}/fields/"
        f"{field['field_id']}"
    )

    response = requests.put(
        url,
        headers=lark_headers(
            token
        ),
        json={
            "field_name": "发布后天数",
            "type": 2,
            "property": {
                "formatter": "0"
            }
        },
        timeout=60
    )

    data = response.json()

    if data.get(
        "code"
    ) != 0:

        raise RuntimeError(
            "无法把发布后天数迁移为数字字段："
            f"{data}"
        )

    print(
        "✅ 发布后天数已从公式字段迁移为 Analytics 时区数字字段"
    )

    print(
        f"✅ 原字段配置备份：{backup_path}"
    )

    return field[
        "field_id"
    ]

def repair_post_publish_days(
    token,
    records,
    videos
):

    publish_dates = {
        video["video_id"]:
            iso_to_analytics_date(
                video.get(
                    "published_at"
                )
            )
        for video in videos
    }

    updates = []
    invalid = 0

    for record in records:

        fields = record.get(
            "fields",
            {}
        )

        video_id = str(
            fields.get(
                "Video ID",
                ""
            )
        )

        published_date = publish_dates.get(
            video_id
        )

        history_date = lark_date_to_date(
            fields.get(
                "日期"
            )
        )

        if not (
            published_date
            and history_date
        ):
            continue

        days = (
            history_date
            - published_date
        ).days

        if days < 0:

            invalid += 1
            continue

        updates.append({
            "record_id":
                record[
                    "record_id"
                ],
            "fields": {
                "发布后天数":
                    days
            }
        })

    if invalid:
        raise RuntimeError(
            "修复发布后天数前仍有发布前记录："
            f"{invalid} 条"
        )

    print(
        f"按 YouTube Analytics 时区重算发布后天数："
        f"{len(updates)} 条"
    )

    batch_update(
        token,
        HISTORY_TABLE_ID,
        updates
    )

    return len(
        updates
    )


def backup_and_delete_invalid_history(
    token,
    records,
    videos
):

    invalid = find_invalid_video_history(
        records,
        videos
    )

    if not invalid:

        print(
            "✅ 没有发现发布日期之前的无效视频历史记录"
        )
        return set()

    backup_path = (
        DATA_DIR
        / "backups"
        / (
            "invalid_video_history_"
            + datetime.now().strftime(
                "%Y%m%d_%H%M%S"
            )
            + ".json"
        )
    )

    save_json_atomic(
        backup_path,
        {
            "reason": "历史日期早于视频在 YouTube Analytics 时区中的发布日期",
            "created_at": datetime.now().isoformat(
                timespec="seconds"
            ),
            "count": len(
                invalid
            ),
            "records": invalid
        }
    )

    print(
        f"⚠️ 发现发布前无效记录：{len(invalid)} 条"
    )

    print(
        f"✅ 删除前备份：{backup_path}"
    )

    record_ids = [
        record[
            "record_id"
        ]
        for record in invalid
        if record.get(
            "record_id"
        )
    ]

    batch_delete(
        token,
        HISTORY_TABLE_ID,
        record_ids
    )

    return set(
        record_ids
    )


def iter_date_chunks(
    start_date,
    end_date,
    chunk_days=BACKFILL_CHUNK_DAYS
):

    current = start_date

    while current <= end_date:

        chunk_end = min(
            end_date,
            current + timedelta(
                days=chunk_days - 1
            )
        )

        yield current, chunk_end

        current = chunk_end + timedelta(
            days=1
        )


def run_backfill():

    print("")
    print("================================")
    print("BACKFILL：回填频道创建以来的完整历史")
    print("================================")
    print("该任务只补齐/更新历史记录，不删除有效历史数据。")
    print("中断后再次运行 run_backfill.bat 会从检查点继续。")

    youtube = build_youtube()
    analytics = build_analytics()

    channel_response = youtube.channels().list(
        part="snippet",
        mine=True
    ).execute(
        num_retries=2
    )

    channel_items = channel_response.get(
        "items",
        []
    )

    if not channel_items:
        raise RuntimeError(
            "YouTube 未返回当前频道。"
        )

    channel_created = iso_to_analytics_date(
        channel_items[0]
        .get(
            "snippet",
            {}
        )
        .get(
            "publishedAt"
        )
    )

    if not channel_created:
        raise RuntimeError(
            "无法识别频道创建日期。"
        )

    _, videos = fetch_channel_and_videos(
        youtube
    )

    resolve_video_types(
        videos,
        analytics=analytics,
        refresh=True
    )

    assign_content_releases(
        videos
    )

    videos.sort(
        key=lambda item: (
            iso_to_analytics_date(
                item.get(
                    "published_at"
                )
            )
            or date.max,
            item[
                "video_id"
            ]
        )
    )

    end_date = (
        datetime.now(
            ANALYTICS_TIMEZONE
        ).date()
        - timedelta(
            days=1
        )
    )

    identity = hashlib.sha256(
        (
            f"{APP_ID}|{BASE_TOKEN}|"
            f"{HISTORY_TABLE_ID}|{CHANNEL_TABLE_ID}"
        ).encode(
            "utf-8"
        )
    ).hexdigest()[:16]

    checkpoint = load_backfill_checkpoint(
        identity,
        channel_created,
        end_date
    )

    completed_video_ids = set(
        checkpoint.get(
            "completed_video_ids",
            []
        )
    )

    expected_rows = 0

    for video in videos:

        published_date = iso_to_analytics_date(
            video.get(
                "published_at"
            )
        )

        if (
            published_date
            and published_date <= end_date
        ):

            expected_rows += (
                end_date
                - max(
                    channel_created,
                    published_date
                )
            ).days + 1

    print(
        f"频道创建日期：{channel_created}"
    )

    print(
        f"请求结束日期：{end_date}"
    )

    print(
        f"视频数量：{len(videos)}"
    )

    print(
        f"理论最大视频日记录：{expected_rows}"
    )

    if completed_video_ids:

        print(
            f"检查点已完成视频：{len(completed_video_ids)}"
        )

    token = get_lark_token()

    ensure_video_type_fields(token)

    ensure_dashboard_fields(token)

    ensure_post_publish_days_number_field(
        token
    )

    print("")
    print("读取并检查现有视频历史表...")

    history_records = get_all_records(
        token,
        HISTORY_TABLE_ID
    )

    deleted_ids = backup_and_delete_invalid_history(
        token,
        history_records,
        videos
    )

    valid_records = [
        record
        for record in history_records
        if record.get(
            "record_id"
        ) not in deleted_ids
    ]

    existing = make_record_map(
        valid_records,
        "唯一键"
    )

    total_created = 0
    total_updated = 0
    total_rows = 0

    for index, video in enumerate(
        videos,
        start=1
    ):

        video_id = video[
            "video_id"
        ]

        if video_id in completed_video_ids:
            continue

        published_date = iso_to_analytics_date(
            video.get(
                "published_at"
            )
        )

        if not published_date:

            print(
                f"[{index}/{len(videos)}] "
                f"跳过：无法识别发布时间 {video_id}"
            )
            continue

        video_start = max(
            channel_created,
            published_date
        )

        print("")
        print(
            f"[{index}/{len(videos)}] "
            f"{video['title'][:42]}"
        )
        print(
            f"    {video_start} ~ {end_date}"
        )

        video_created = 0
        video_updated = 0
        video_rows = 0

        if video_start <= end_date:

            for chunk_start, chunk_end in iter_date_chunks(
                video_start,
                end_date
            ):

                rows = query_analytics_rows(
                    analytics,
                    chunk_start,
                    chunk_end,
                    VIDEO_ANALYTICS_METRICS,
                    filters=f"video=={video_id}"
                )

                creates = []
                updates = []

                for row in rows:

                    fields = make_video_history_fields(
                        video,
                        row
                    )

                    if not fields:
                        continue

                    fields = {
                        key: value
                        for key, value in fields.items()
                        if value is not None
                    }

                    unique_key = fields[
                        "唯一键"
                    ]

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

                video_created += len(
                    creates
                )
                video_updated += len(
                    updates
                )
                video_rows += len(
                    creates
                ) + len(
                    updates
                )

        completed_video_ids.add(
            video_id
        )

        checkpoint[
            "completed_video_ids"
        ] = sorted(
            completed_video_ids
        )

        checkpoint[
            "updated_at"
        ] = datetime.now().isoformat(
            timespec="seconds"
        )

        save_json_atomic(
            BACKFILL_CHECKPOINT_FILE,
            checkpoint
        )

        total_created += video_created
        total_updated += video_updated
        total_rows += video_rows

        print(
            f"    完成：新增 {video_created}，"
            f"更新 {video_updated}"
        )

    print("")
    print("回填频道 Analytics 历史...")

    channel_records = get_all_records(
        token,
        CHANNEL_TABLE_ID
    )

    channel_map = make_record_map(
        channel_records,
        "日期Key"
    )

    channel_start = channel_created
    completed_through = checkpoint.get(
        "channel_completed_through"
    )

    if completed_through:

        try:
            channel_start = max(
                channel_start,
                datetime.strptime(
                    completed_through,
                    "%Y-%m-%d"
                ).date() + timedelta(
                    days=1
                )
            )
        except ValueError:
            pass

    channel_created_count = 0
    channel_updated_count = 0

    for chunk_start, chunk_end in iter_date_chunks(
        channel_start,
        end_date
    ):

        rows = query_analytics_rows(
            analytics,
            chunk_start,
            chunk_end,
            CHANNEL_ANALYTICS_METRICS
        )

        creates = []
        updates = []

        for row in rows:

            fields = make_channel_history_fields(
                row
            )

            if not fields:
                continue

            fields = {
                key: value
                for key, value in fields.items()
                if value is not None
            }

            day = fields[
                "日期Key"
            ]

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

        channel_created_count += len(
            creates
        )
        channel_updated_count += len(
            updates
        )

        checkpoint[
            "channel_completed_through"
        ] = str(
            chunk_end
        )

        checkpoint[
            "updated_at"
        ] = datetime.now().isoformat(
            timespec="seconds"
        )

        save_json_atomic(
            BACKFILL_CHECKPOINT_FILE,
            checkpoint
        )

    print("")
    print("复核视频历史表...")

    final_records = get_all_records(
        token,
        HISTORY_TABLE_ID
    )

    invalid_after = find_invalid_video_history(
        final_records,
        videos
    )

    if invalid_after:
        raise RuntimeError(
            "回填后仍发现发布前无效记录："
            f"{len(invalid_after)} 条"
        )

    repaired_days = repair_post_publish_days(
        token,
        final_records,
        videos
    )

    repaired_types = sync_video_type_records(
        token,
        HISTORY_TABLE_ID,
        final_records,
        {
            video["video_id"]: video.get(
                "video_type",
                VIDEO_TYPE_UNKNOWN
            )
            for video in videos
        }
    )

    dashboard_video_updates = sync_dashboard_video_helpers(
        token,
        videos
    )

    dashboard_channel_updates = sync_dashboard_channel_helpers(
        token
    )

    checkpoint[
        "status"
    ] = "complete"

    checkpoint[
        "completed_at"
    ] = datetime.now().isoformat(
        timespec="seconds"
    )

    checkpoint[
        "final_history_record_count"
    ] = len(
        final_records
    )

    save_json_atomic(
        BACKFILL_CHECKPOINT_FILE,
        checkpoint
    )

    print("")
    print(
        f"视频历史本次处理：{total_rows} 条"
    )
    print(
        f"视频历史新增：{total_created}"
    )
    print(
        f"视频历史更新：{total_updated}"
    )
    print(
        f"频道历史新增：{channel_created_count}"
    )
    print(
        f"频道历史更新：{channel_updated_count}"
    )
    print(
        f"最终视频历史记录：{len(final_records)}"
    )
    print(
        f"发布后天数已重算：{repaired_days}"
    )
    print(
        f"视频类型已更新：{repaired_types}"
    )
    print(
        "仪表盘辅助字段已更新："
        f"视频历史 {dashboard_video_updates['history_updates']}，"
        f"视频主表 {dashboard_video_updates['main_updates']}，"
        f"频道历史 {dashboard_channel_updates}"
    )
    print(
        "发布前无效记录：0"
    )
    print("")
    print(
        "✅ BACKFILL 全历史回填完成"
    )


def run_video_types():

    print("")
    print("================================")
    print("TYPES：同步全部视频类型")
    print("================================")

    youtube = build_youtube()
    analytics = build_analytics()
    _, videos = fetch_channel_and_videos(youtube)
    video_types = resolve_video_types(
        videos,
        analytics=analytics,
        refresh=True
    )

    token = get_lark_token()
    ensure_video_type_fields(token)
    ensure_dashboard_fields(token)

    targets = [
        ("视频主表", VIDEO_TABLE_ID),
        ("视频历史表", HISTORY_TABLE_ID),
    ]

    if LATEST_TABLE_ID:
        targets.append(
            ("最新视频实时追踪表", LATEST_TABLE_ID)
        )

    total_updated = 0

    for label, table_id in targets:
        print("")
        print(f"同步：{label}")
        records = get_all_records(
            token,
            table_id
        )
        updated = sync_video_type_records(
            token,
            table_id,
            records,
            video_types
        )
        total_updated += updated
        print(
            f"{label}：检查 {len(records)} 条，"
            f"更新 {updated} 条"
        )

    counts = {
        value: sum(
            1
            for current in video_types.values()
            if current == value
        )
        for value in VIDEO_TYPE_VALUES
    }

    print("")
    print(
        "分类结果："
        + "，".join(
            f"{label} {count}"
            for label, count in counts.items()
        )
    )

    sync_dashboard_video_helpers(
        token,
        videos
    )

    print(f"三张视频表合计更新：{total_updated} 条")
    print("✅ TYPES 视频类型同步完成")


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

        print(
            "python youtube_lark.py backfill"
        )

        print(
            "python youtube_lark.py latest"
        )

        print(
            "python youtube_lark.py types"
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

    elif mode == "backfill":

        run_backfill()

    elif mode == "latest":

        run_latest()

    elif mode == "types":

        run_video_types()

    else:

        print(
            f"未知模式：{mode}"
        )


if __name__ == "__main__":
    main()
