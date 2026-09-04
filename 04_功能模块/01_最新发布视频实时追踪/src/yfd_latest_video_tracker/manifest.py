from youtube_feishu_dashboard.module_contracts import ModuleManifest

MODULE_ID = "latest_video_tracker"
TASK_ID = "latest-video-tracker"

BUSINESS_TABLE_CONFIG_KEYS = {
    "视频追踪主表": "latest_video_main_table_id",
    "视频实时快照表": "latest_video_snapshot_table_id",
    "视频同期对比表": "latest_video_comparison_table_id",
}

IMPLEMENTED_BUSINESS_TABLES = tuple(BUSINESS_TABLE_CONFIG_KEYS)

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

# Analytics API 只在相应映射启用时参与查询；Data API 的小时级任务不会因此
# 每小时重复请求后台分析数据。
ANALYTICS_FIELD_IDS = (
    "ANALYTICS_ENGAGED_VIEWS",
    "ANALYTICS_WATCH_TIME_MINUTES",
    "ANALYTICS_AVG_VIEW_DURATION",
    "ANALYTICS_AVG_VIEW_PERCENT",
    "ANALYTICS_SUB_GAINED",
    "ANALYTICS_SUB_LOST",
    "ANALYTICS_SHARES",
    "ANALYTICS_EST_AD_REVENUE",
)

# 这两个沿用现有映射 ID，但实际由 Reporting API 的 Reach Basic 日报提供。
REPORTING_FIELD_IDS = (
    "ANALYTICS_IMPRESSIONS",
    "ANALYTICS_IMPRESSIONS_CTR",
)

OUTPUT_FIELD_IDS = (
    "MODULE_UNIQUE_KEY",
    "CONTENT_BATCH_ID",
    "SYSTEM_OBSERVED_AT",
    "VIDEO_URL",
    "VIDEO_TYPE",
    "VIDEO_AGE_MINUTES",
    "VIDEO_AGE_BUCKET_MINUTES",
    "VIDEO_VIEW_DELTA",
    "VIDEO_VIEW_RATE_PER_HOUR",
    "VIDEO_LIKE_DELTA",
    "VIDEO_COMMENT_DELTA",
    "TRACKING_STATUS",
    "CURRENT_TRACKING_VIDEO",
    "TRACKING_STARTED_AT",
    "TRACKING_ENDS_AT",
    "COMPARISON_RECORD_ID",
    "CURRENT_COMPARISON_BATCH",
    "COMPARISON_OBJECT",
    "COMPARISON_SAMPLE_COUNT",
    "SYSTEM_CALCULATED_AT",
    "SYSTEM_LAST_SYNCED_AT",
    "ANALYTICS_FETCHED_AT",
    "ANALYTICS_DATA_THROUGH_DATE",
    "REPORTING_FETCHED_AT",
    "REPORTING_DATA_THROUGH_DATE",
    "DATA_API_FETCHED_AT_PACIFIC",
    "DATA_API_FETCHED_AT_BEIJING",
    "DATA_API_DATA_THROUGH_AT_PACIFIC_INFERRED",
    "DATA_API_DATA_THROUGH_AT_BEIJING_INFERRED",
    "ANALYTICS_FETCHED_AT_PACIFIC",
    "ANALYTICS_FETCHED_AT_BEIJING",
    "ANALYTICS_DATA_THROUGH_AT_PACIFIC",
    "ANALYTICS_DATA_THROUGH_AT_BEIJING",
    "REPORTING_FETCHED_AT_PACIFIC",
    "REPORTING_FETCHED_AT_BEIJING",
    "REPORTING_DATA_THROUGH_AT_PACIFIC",
    "REPORTING_DATA_THROUGH_AT_BEIJING",
)

DEFAULT_MAIN_FIELD_MAPPING = {
    "VIDEO_ID": "视频唯一编号",
    "CONTENT_BATCH_ID": "内容批次编号",
    "VIDEO_TITLE": "视频标题",
    "VIDEO_URL": "视频链接",
    "VIDEO_PUBLISHED_AT": "发布时间",
    "VIDEO_DURATION": "视频时长（秒）",
    "VIDEO_TYPE": "视频类型",
    "TRACKING_STATUS": "追踪状态",
    "CURRENT_TRACKING_VIDEO": "当前追踪视频",
    "TRACKING_STARTED_AT": "追踪开始时间",
    "TRACKING_ENDS_AT": "追踪结束时间",
    "SYSTEM_OBSERVED_AT": "最新数据时间",
    "SYSTEM_LAST_SYNCED_AT": "实时数据更新时间",
    "VIDEO_AGE_MINUTES": "发布后分钟数",
    "VIDEO_VIEWS_PUBLIC": "播放量",
    "VIDEO_LIKES_PUBLIC": "点赞数",
    "VIDEO_COMMENTS_PUBLIC": "评论数",
}

DEFAULT_SNAPSHOT_FIELD_MAPPING = {
    "MODULE_UNIQUE_KEY": "快照唯一编号",
    "SYSTEM_OBSERVED_AT": "数据抓取时间",
    "VIDEO_ID": "视频唯一编号",
    "CONTENT_BATCH_ID": "内容批次编号",
    "VIDEO_TYPE": "视频类型",
    "VIDEO_TITLE": "视频标题",
    "VIDEO_PUBLISHED_AT": "发布时间",
    "VIDEO_AGE_MINUTES": "发布后分钟数",
    "VIDEO_AGE_BUCKET_MINUTES": "发布后时间档（分钟）",
    "VIDEO_VIEWS_PUBLIC": "累计播放量",
    "VIDEO_VIEW_DELTA": "本周期新增播放量",
    "VIDEO_VIEW_RATE_PER_HOUR": "每小时播放速度",
    "VIDEO_LIKES_PUBLIC": "累计点赞数",
    "VIDEO_LIKE_DELTA": "本周期新增点赞数",
    "VIDEO_COMMENTS_PUBLIC": "累计评论数",
    "VIDEO_COMMENT_DELTA": "本周期新增评论数",
    "SYSTEM_LAST_SYNCED_AT": "实时数据更新时间",
}

DEFAULT_COMPARISON_FIELD_MAPPING = {
    "COMPARISON_RECORD_ID": "对比记录编号",
    "VIDEO_ID": "当前视频唯一编号",
    "CONTENT_BATCH_ID": "当前内容批次编号",
    "CURRENT_COMPARISON_BATCH": "当前对比批次",
    "VIDEO_TYPE": "视频类型",
    "VIDEO_AGE_BUCKET_MINUTES": "发布后时间档（分钟）",
    "COMPARISON_OBJECT": "对比对象",
    "COMPARISON_SAMPLE_COUNT": "样本数量",
    "VIDEO_VIEWS_PUBLIC": "播放量",
    "VIDEO_VIEW_RATE_PER_HOUR": "每小时播放速度",
    "SYSTEM_CALCULATED_AT": "数据计算时间",
}

# 兼容早期扩展代码；新装配层按目标表分别使用上面的三个映射。
DEFAULT_FIELD_MAPPING = DEFAULT_SNAPSHOT_FIELD_MAPPING

MANIFEST = ModuleManifest(
    module_id=MODULE_ID,
    version="1.0.0",
    cn_name="最新发布视频实时追踪",
    description="以真实采集时间追踪当前最新发布视频，默认持续 7 天。",
    api_field_ids=(*API_FIELD_IDS, *ANALYTICS_FIELD_IDS, *REPORTING_FIELD_IDS),
    output_field_ids=OUTPUT_FIELD_IDS,
    implemented=True,
)
