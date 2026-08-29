import csv
import os
import httplib2

from google.oauth2.credentials import Credentials
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

TOKEN_FILE = "token.json"

PROXY_HOST = "127.0.0.1"
PROXY_PORT = 10808


# ==============================
# 创建 YouTube API 客户端
# ==============================

def get_youtube():

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

    youtube = build(
        "youtube",
        "v3",
        http=authorized_http,
        cache_discovery=False
    )

    return youtube


# ==============================
# 获取上传播放列表
# ==============================

def get_upload_playlist(youtube):

    response = youtube.channels().list(
        part="contentDetails",
        mine=True
    ).execute()

    return response["items"][0]["contentDetails"][
        "relatedPlaylists"
    ]["uploads"]


# ==============================
# 获取所有视频 ID
# ==============================

def get_all_video_ids(youtube, playlist_id):

    video_ids = []
    next_page_token = None

    while True:

        response = youtube.playlistItems().list(
            part="contentDetails",
            playlistId=playlist_id,
            maxResults=50,
            pageToken=next_page_token
        ).execute()

        for item in response["items"]:
            video_ids.append(
                item["contentDetails"]["videoId"]
            )

        next_page_token = response.get(
            "nextPageToken"
        )

        if not next_page_token:
            break

    return video_ids


# ==============================
# 获取视频详细数据
# ==============================

def get_video_details(youtube, video_ids):

    videos = []

    for i in range(0, len(video_ids), 50):

        batch = video_ids[i:i + 50]

        response = youtube.videos().list(
            part="snippet,statistics,contentDetails",
            id=",".join(batch),
            maxResults=50
        ).execute()

        for item in response["items"]:

            snippet = item["snippet"]
            statistics = item.get(
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
                ]["url"]

            elif "high" in thumbnails:
                thumbnail = thumbnails[
                    "high"
                ]["url"]

            elif "default" in thumbnails:
                thumbnail = thumbnails[
                    "default"
                ]["url"]

            video_id = item["id"]

            videos.append({
                "video_id": video_id,
                "title": snippet.get(
                    "title",
                    ""
                ),
                "published_at": snippet.get(
                    "publishedAt",
                    ""
                ),
                "url":
                    f"https://www.youtube.com/watch?v={video_id}",
                "thumbnail": thumbnail,
                "views": statistics.get(
                    "viewCount",
                    0
                ),
                "likes": statistics.get(
                    "likeCount",
                    0
                ),
                "comments": statistics.get(
                    "commentCount",
                    0
                ),
                "duration": item[
                    "contentDetails"
                ].get(
                    "duration",
                    ""
                )
            })

    return videos


# ==============================
# 保存 CSV
# ==============================

def save_csv(videos):

    filename = "youtube_videos.csv"

    fieldnames = [
        "Video ID",
        "视频标题",
        "发布时间",
        "视频链接",
        "缩略图",
        "播放量",
        "点赞数",
        "评论数",
        "视频时长"
    ]

    with open(
        filename,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames
        )

        writer.writeheader()

        for video in videos:

            writer.writerow({
                "Video ID":
                    video["video_id"],
                "视频标题":
                    video["title"],
                "发布时间":
                    video["published_at"],
                "视频链接":
                    video["url"],
                "缩略图":
                    video["thumbnail"],
                "播放量":
                    video["views"],
                "点赞数":
                    video["likes"],
                "评论数":
                    video["comments"],
                "视频时长":
                    video["duration"]
            })

    return filename


# ==============================
# 主程序
# ==============================

def main():

    print("")
    print("正在连接 YouTube API...")

    youtube = get_youtube()

    print("连接成功。")

    print("")
    print("正在获取频道上传列表...")

    playlist_id = get_upload_playlist(
        youtube
    )

    print(
        f"上传播放列表 ID："
        f"{playlist_id}"
    )

    print("")
    print("正在获取所有视频 ID...")

    video_ids = get_all_video_ids(
        youtube,
        playlist_id
    )

    print(
        f"共找到 {len(video_ids)} 个视频。"
    )

    print("")
    print("正在读取视频详细数据...")

    videos = get_video_details(
        youtube,
        video_ids
    )

    print(
        f"成功读取 {len(videos)} 个视频。"
    )

    filename = save_csv(
        videos
    )

    print("")
    print("================================")
    print("✅ 视频数据抓取完成")
    print("================================")
    print(
        f"文件：{filename}"
    )
    print(
        f"视频数量：{len(videos)}"
    )
    print("================================")
    print("")


if __name__ == "__main__":
    main()