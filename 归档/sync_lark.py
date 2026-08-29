import csv
import os
import re
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv


# ============================================================
# 读取配置
# ============================================================

load_dotenv()

APP_ID = os.getenv("LARK_APP_ID")
APP_SECRET = os.getenv("LARK_APP_SECRET")

BASE_TOKEN = os.getenv("LARK_BASE_TOKEN")

VIDEO_TABLE_ID = os.getenv(
    "LARK_VIDEO_TABLE_ID"
)

HISTORY_TABLE_ID = os.getenv(
    "LARK_HISTORY_TABLE_ID"
)
CHANNEL_TABLE_ID = os.getenv(
    "LARK_CHANNEL_TABLE_ID"
)

CHANNEL_CSV = "youtube_channel_daily.csv"

VIDEO_CSV = "youtube_videos.csv"
HISTORY_CSV = "youtube_analytics_daily.csv"

LARK_API = "https://open.larksuite.com/open-apis"

BATCH_SIZE = 100


# ============================================================
# 基础工具
# ============================================================

def get_token():

    response = requests.post(
        f"{LARK_API}/auth/v3/"
        "tenant_access_token/internal",
        json={
            "app_id": APP_ID,
            "app_secret": APP_SECRET
        },
        timeout=30
    )

    data = response.json()

    if data.get("code") != 0:
        raise RuntimeError(
            f"获取 Lark Token 失败：{data}"
        )

    return data["tenant_access_token"]


def headers(token):

    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }


def now_ms():

    return int(
        datetime.now(
            timezone.utc
        ).timestamp() * 1000
    )


def iso_to_ms(value):

    if not value:
        return None

    try:

        value = value.replace(
            "Z",
            "+00:00"
        )

        dt = datetime.fromisoformat(value)

        return int(
            dt.timestamp() * 1000
        )

    except Exception:
        return None


def date_to_ms(value):

    if not value:
        return None

    try:

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

    except Exception:
        return None


def to_int(value):

    try:
        return int(float(value))
    except Exception:
        return 0


def to_float(value):

    try:
        return float(value)
    except Exception:
        return 0.0


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

    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)

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
        yield items[i:i + size]


# ============================================================
# 获取表中已有记录
# ============================================================

def get_existing_records(
    token,
    table_id,
    key_field
):

    result = {}

    page_token = None

    while True:

        params = {
            "page_size": 500
        }

        if page_token:
            params["page_token"] = page_token

        url = (
            f"{LARK_API}/bitable/v1/"
            f"apps/{BASE_TOKEN}/"
            f"tables/{table_id}/records"
        )

        response = requests.get(
            url,
            headers=headers(token),
            params=params,
            timeout=30
        )

        data = response.json()

        if data.get("code") != 0:
            raise RuntimeError(
                f"读取记录失败：{data}"
            )

        items = (
            data.get("data", {})
            .get("items", [])
        )

        for item in items:

            fields = item.get(
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
                result[str(key)] = (
                    item["record_id"]
                )

        page_token = (
            data.get("data", {})
            .get("page_token")
        )

        has_more = (
            data.get("data", {})
            .get("has_more", False)
        )

        if not has_more:
            break

    return result


# ============================================================
# 批量新增
# ============================================================

def batch_create(
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
        f"batch_create"
    )

    for batch in chunked(
        records,
        BATCH_SIZE
    ):

        response = requests.post(
            url,
            headers=headers(token),
            json={
                "records": [
                    {
                        "fields": fields
                    }
                    for fields in batch
                ]
            },
            timeout=60
        )

        data = response.json()

        if data.get("code") != 0:
            raise RuntimeError(
                f"批量新增失败：{data}"
            )

        print(
            f"    新增 {len(batch)} 条"
        )

        time.sleep(0.2)


# ============================================================
# 批量更新
# ============================================================

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
                    item["record_id"],
                "fields":
                    item["fields"]
            })

        response = requests.post(
            url,
            headers=headers(token),
            json={
                "records": payload
            },
            timeout=60
        )

        data = response.json()

        if data.get("code") != 0:
            raise RuntimeError(
                f"批量更新失败：{data}"
            )

        print(
            f"    更新 {len(batch)} 条"
        )

        time.sleep(0.2)


# ============================================================
# 视频主表
# ============================================================

def sync_video_table(token):

    print("")
    print("================================")
    print("开始同步：视频主表")
    print("================================")

    existing = get_existing_records(
        token,
        VIDEO_TABLE_ID,
        "Video ID"
    )

    print(
        f"Lark 已有视频："
        f"{len(existing)}"
    )

    creates = []
    updates = []

    with open(
        VIDEO_CSV,
        "r",
        encoding="utf-8-sig"
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            video_id = (
                row.get(
                    "Video ID",
                    ""
                ).strip()
            )

            if not video_id:
                continue

            fields = {
                "Video ID":
                    video_id,

                "视频标题":
                    row.get(
                        "视频标题",
                        ""
                    ),

                "发布时间":
                    iso_to_ms(
                        row.get(
                            "发布时间",
                            ""
                        )
                    ),

                "视频链接":
                    url_field(
                        row.get(
                            "视频链接",
                            ""
                        ),
                        "打开视频"
                    ),

                "缩略图URL":
                    url_field(
                        row.get(
                            "缩略图",
                            ""
                        ),
                        "查看缩略图"
                    ),

                "当前播放量":
                    to_int(
                        row.get(
                            "播放量"
                        )
                    ),

                "点赞数":
                    to_int(
                        row.get(
                            "点赞数"
                        )
                    ),

                "评论数":
                    to_int(
                        row.get(
                            "评论数"
                        )
                    ),

                "视频时长":
                    iso_duration_to_text(
                        row.get(
                            "视频时长",
                            ""
                        )
                    ),

                "最后同步时间":
                    now_ms()
            }

            # 删除 None
            fields = {
                k: v
                for k, v
                in fields.items()
                if v is not None
            }

            if video_id in existing:

                updates.append({
                    "record_id":
                        existing[video_id],
                    "fields":
                        fields
                })

            else:

                creates.append(
                    fields
                )

    print(
        f"需要新增：{len(creates)}"
    )

    print(
        f"需要更新：{len(updates)}"
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

    print("✅ 视频主表同步完成")


# ============================================================
# 视频历史数据
# ============================================================

def sync_history_table(token):

    print("")
    print("================================")
    print("开始同步：视频历史数据")
    print("================================")

    existing = get_existing_records(
        token,
        HISTORY_TABLE_ID,
        "唯一键"
    )

    print(
        f"Lark 已有历史记录："
        f"{len(existing)}"
    )

    creates = []
    updates = []

    with open(
        HISTORY_CSV,
        "r",
        encoding="utf-8-sig"
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            video_id = (
                row.get(
                    "Video ID",
                    ""
                ).strip()
            )

            day = (
                row.get(
                    "日期",
                    ""
                ).strip()
            )

            if not video_id or not day:
                continue

            unique_key = (
                f"{video_id}_{day}"
            )

            fields = {

                "唯一键":
                    unique_key,

                "日期":
                    date_to_ms(day),

                "Video ID":
                    video_id,

                "视频标题":
                    row.get(
                        "视频标题",
                        ""
                    ),

                "发布时间":
                    iso_to_ms(
                        row.get(
                            "发布时间",
                            ""
                        )
                    ),

                "当日播放量":
                    to_int(
                        row.get(
                            "当日播放量"
                        )
                    ),

                "观看时长（分钟）":
                    to_float(
                        row.get(
                            "观看时长（分钟）"
                        )
                    ),

                "平均观看时长（秒）":
                    to_float(
                        row.get(
                            "平均观看时长（秒）"
                        )
                    ),

                "平均观看百分比":
                    to_float(
                        row.get(
                            "平均观看百分比"
                        )
                    ),

                "新增订阅":
                    to_int(
                        row.get(
                            "新增订阅"
                        )
                    ),

                "取消订阅":
                    to_int(
                        row.get(
                            "取消订阅"
                        )
                    ),

                "预估收入（USD）":
                    to_float(
                        row.get(
                            "预估收入（USD）"
                        )
                    ),

                "最后同步时间":
                    now_ms()
            }

            # 注意：
            # “发布后天数”是 Lark 公式字段
            # 所以 Python 不写这个字段

            fields = {
                k: v
                for k, v
                in fields.items()
                if v is not None
            }

            if unique_key in existing:

                updates.append({
                    "record_id":
                        existing[unique_key],
                    "fields":
                        fields
                })

            else:

                creates.append(
                    fields
                )

    print(
        f"需要新增：{len(creates)}"
    )

    print(
        f"需要更新：{len(updates)}"
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

    print("✅ 视频历史数据同步完成")


# ============================================================
# 主程序
# ============================================================
def sync_channel_table(token):

    print("")
    print("================================")
    print("开始同步：频道历史数据")
    print("================================")

    existing = get_existing_records(
        token,
        CHANNEL_TABLE_ID,
        "日期Key"
    )

    print(
        f"Lark 已有频道历史记录："
        f"{len(existing)}"
    )

    creates = []
    updates = []

    def optional_int(value):

        if value is None:
            return None

        value = str(value).strip()

        if value == "":
            return None

        try:
            return int(float(value))
        except Exception:
            return None

    def optional_float(value):

        if value is None:
            return None

        value = str(value).strip()

        if value == "":
            return None

        try:
            return float(value)
        except Exception:
            return None

    with open(
        CHANNEL_CSV,
        "r",
        encoding="utf-8-sig"
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            day = str(
                row.get(
                    "日期Key",
                    ""
                )
            ).strip()

            if not day:
                continue

            fields = {
                "日期Key":
                    day,

                "日期":
                    date_to_ms(
                        row.get(
                            "日期",
                            ""
                        )
                    ),

                "订阅人数":
                    optional_int(
                        row.get(
                            "订阅人数"
                        )
                    ),

                "订阅快照变化":
                    optional_int(
                        row.get(
                            "订阅快照变化"
                        )
                    ),

                "频道总播放量":
                    optional_int(
                        row.get(
                            "频道总播放量"
                        )
                    ),

                "视频数量":
                    optional_int(
                        row.get(
                            "视频数量"
                        )
                    ),

                "当日播放量":
                    optional_int(
                        row.get(
                            "当日播放量"
                        )
                    ),

                "观看时长（分钟）":
                    optional_float(
                        row.get(
                            "观看时长（分钟）"
                        )
                    ),

                "新增订阅":
                    optional_int(
                        row.get(
                            "新增订阅"
                        )
                    ),

                "取消订阅":
                    optional_int(
                        row.get(
                            "取消订阅"
                        )
                    ),

                "预估收入（USD）":
                    optional_float(
                        row.get(
                            "预估收入（USD）"
                        )
                    ),

                "最后同步时间":
                    now_ms()
            }

            # 删掉空值
            # 不把历史缺失数据错误写成 0
            fields = {
                k: v
                for k, v
                in fields.items()
                if v is not None
            }

            # 净增订阅是 Lark 公式字段
            # Python 不写

            if day in existing:

                updates.append({
                    "record_id":
                        existing[day],
                    "fields":
                        fields
                })

            else:

                creates.append(
                    fields
                )

    print(
        f"需要新增：{len(creates)}"
    )

    print(
        f"需要更新：{len(updates)}"
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

    print("✅ 频道历史数据同步完成")
    
def main():

    print("")
    print("正在连接 Lark...")

    token = get_token()

    print("✅ Lark Token 获取成功")

    sync_video_table(token)

    sync_history_table(token)

    sync_channel_table(token)

    print("")
    print("================================")
    print("✅ 全部同步完成")
    print("================================")
    print("")


if __name__ == "__main__":
    main()