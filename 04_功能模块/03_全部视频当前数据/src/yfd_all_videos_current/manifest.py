from youtube_feishu_dashboard.module_contracts import ModuleManifest

MANIFEST = ModuleManifest(
    module_id="all_videos_current",
    version="0.1.0-template",
    cn_name="全部视频当前数据",
    description="接口模板；后续分页扫描上传列表并更新当前统计，不注册调度任务。",
    api_field_ids=(
        "VIDEO_ID",
        "VIDEO_TITLE",
        "VIDEO_PUBLISHED_AT",
        "VIDEO_VIEWS_PUBLIC",
        "VIDEO_LIKES_PUBLIC",
        "VIDEO_COMMENTS_PUBLIC",
    ),
    implemented=False,
)
