from __future__ import annotations

from youtube_feishu_dashboard.catalog.field_catalog import ApiRequestPlan
from youtube_feishu_dashboard.scheduler.task import RetryPolicy, TaskContext, TaskResult

from yfd_latest_video_tracker.manifest import MODULE_ID, TASK_ID
from yfd_latest_video_tracker.service import LatestVideoTrackerService


class LatestVideoTrackingTask:
    task_id = TASK_ID
    module_id = MODULE_ID

    def __init__(
        self,
        service: LatestVideoTrackerService,
        request_plan: ApiRequestPlan,
        *,
        interval_seconds: int = 1800,
        lock_ttl_seconds: int = 900,
    ) -> None:
        self.service = service
        self.request_plan = request_plan
        self.interval_seconds = interval_seconds
        self.lock_ttl_seconds = lock_ttl_seconds
        self.retry_policy = RetryPolicy(
            base_delay_seconds=60,
            max_delay_seconds=min(1800, interval_seconds),
            non_retryable_delay_seconds=interval_seconds,
        )

    def execute(self, context: TaskContext) -> TaskResult:
        if context.dry_run:
            tracking_video_ids = list(self.service.config.tracking_video_ids)
            return TaskResult(
                details={
                    "dry_run": True,
                    "tracking_video_config": {
                        "video_ids": tracking_video_ids,
                        "video_count": len(tracking_video_ids),
                    },
                    "request_plan": {
                        "field_ids": self.request_plan.field_ids,
                        "data_api_parts": self.request_plan.data_api_parts,
                        "required_scopes": self.request_plan.required_scopes,
                    },
                }
            )
        if context.force:
            counts, details = self.service.track_automatic(context.now)
        else:
            counts, details = self.service.track_scheduled(context.now)
        return TaskResult(counts=counts, details=details)
