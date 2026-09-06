from __future__ import annotations

from zoneinfo import ZoneInfo

from youtube_feishu_dashboard.scheduler.task import RetryPolicy, TaskContext, TaskResult

from yfd_channel_history.manifest import MODULE_ID, TASK_ID
from yfd_channel_history.service import ChannelHistoryService


class ChannelHistoryDailyTask:
    task_id = TASK_ID
    module_id = MODULE_ID

    def __init__(
        self,
        service: ChannelHistoryService,
        *,
        daily_collection_hour: int = 8,
        interval_seconds: int = 3600,
        lock_ttl_seconds: int = 1800,
    ) -> None:
        self.service = service
        self.daily_collection_hour = daily_collection_hour
        self.interval_seconds = interval_seconds
        self.lock_ttl_seconds = lock_ttl_seconds
        self.retry_policy = RetryPolicy(
            base_delay_seconds=300,
            max_delay_seconds=3600,
            non_retryable_delay_seconds=interval_seconds,
        )

    def execute(self, context: TaskContext) -> TaskResult:
        local_now = context.now.astimezone(ZoneInfo(self.service.config.timezone))
        today = local_now.date().isoformat()
        previous_cursor = dict(context.cursor or {})
        if context.dry_run:
            return TaskResult(
                details={
                    "dry_run": True,
                    "external_requests_made": False,
                    "collection_date_beijing": today,
                    "daily_collection_hour": self.daily_collection_hour,
                },
                cursor=previous_cursor or None,
            )
        if not context.force and local_now.hour < self.daily_collection_hour:
            return TaskResult(
                details={"skipped": True, "reason": "before_daily_collection_hour"},
                cursor=previous_cursor or None,
            )
        if not context.force and previous_cursor.get("last_collection_date") == today:
            return TaskResult(
                details={"skipped": True, "reason": "already_collected_today"},
                cursor=previous_cursor,
            )
        counts, details = self.service.collect(context.now)
        previous_cursor["last_collection_date"] = today
        return TaskResult(counts=counts, details=details, cursor=previous_cursor)
