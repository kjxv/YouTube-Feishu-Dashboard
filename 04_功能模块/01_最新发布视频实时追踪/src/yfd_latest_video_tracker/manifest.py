from youtube_feishu_dashboard.module_contracts import ModuleManifest

MODULE_ID = "latest_video_tracker"
TASK_ID = "latest-video-tracker"

API_FIELD_IDS = (
    "VIDEO_ID",
    "VIDEO_CHANNEL_ID",
    "VIDEO_TITLE",
    "VIDEO_PUBLISHED_AT",
    "VIDEO_DURATION",
    "VIDEO_PRIVACY_STATUS",
    "VIDEO_VIEWS_PUBLIC",
    "VIDEO_LIKES_PUBLIC",
    "VIDEO_COMMENTS_PUBLIC",
)

OUTPUT_FIELD_IDS = (
    "MODULE_UNIQUE_KEY",
    "SYSTEM_OBSERVED_AT",
    "VIDEO_URL",
    "VIDEO_AGE_MINUTES",
    "VIDEO_VIEW_DELTA",
    "VIDEO_LIKE_DELTA",
    "VIDEO_COMMENT_DELTA",
    "SYSTEM_LAST_SYNCED_AT",
)

DEFAULT_FIELD_MAPPING = {
    "MODULE_UNIQUE_KEY": "快照唯一编号",
    "SYSTEM_OBSERVED_AT": "数据抓取时间",
    "VIDEO_ID": "视频唯一编号",
    "VIDEO_TITLE": "视频标题",
    "VIDEO_PUBLISHED_AT": "发布时间",
    "VIDEO_AGE_MINUTES": "发布后分钟数",
    "VIDEO_VIEWS_PUBLIC": "累计播放量",
    "VIDEO_VIEW_DELTA": "本周期新增播放量",
    "VIDEO_LIKES_PUBLIC": "累计点赞数",
    "VIDEO_LIKE_DELTA": "本周期新增点赞数",
    "VIDEO_COMMENTS_PUBLIC": "累计评论数",
    "VIDEO_COMMENT_DELTA": "本周期新增评论数",
    "SYSTEM_LAST_SYNCED_AT": "实时数据更新时间",
}

MANIFEST = ModuleManifest(
    module_id=MODULE_ID,
    version="1.0.0",
    cn_name="最新发布视频实时追踪",
    description="以真实采集时间追踪当前最新发布视频，默认持续 7 天。",
    api_field_ids=API_FIELD_IDS,
    output_field_ids=OUTPUT_FIELD_IDS,
    implemented=True,
)
