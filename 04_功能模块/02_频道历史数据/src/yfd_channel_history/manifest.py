from youtube_feishu_dashboard.module_contracts import ModuleManifest

MANIFEST = ModuleManifest(
    module_id="channel_history",
    version="0.1.0-template",
    cn_name="频道历史数据",
    description="接口模板；后续按日抓取真实 Data/Analytics 数据，不注册调度任务。",
    api_field_ids=(
        "CHANNEL_VIEWS_PUBLIC",
        "CHANNEL_SUBSCRIBERS_PUBLIC",
        "CHANNEL_VIDEO_COUNT",
        "ANALYTICS_DAY",
        "ANALYTICS_VIEWS",
        "ANALYTICS_SUB_GAINED",
        "ANALYTICS_SUB_LOST",
    ),
    implemented=False,
)
