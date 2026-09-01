from __future__ import annotations

import logging
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from youtube_feishu_dashboard import __version__
from youtube_feishu_dashboard.core.errors import (
    AuthenticationError,
    ConfigurationError,
    ExternalServiceError,
    FieldCatalogError,
)
from youtube_feishu_dashboard.core.time import as_utc
from youtube_feishu_dashboard.db.repositories import Storage
from youtube_feishu_dashboard.scheduler.clock import Clock, SystemClock
from youtube_feishu_dashboard.scheduler.task import ScheduledTask, TaskContext
from youtube_feishu_dashboard.services.health import HealthService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RunOutcome:
    task_id: str
    status: Literal["success", "failed", "dry_run", "skipped_locked", "not_due"]
    run_id: str | None = None
    error: str | None = None
    counts: dict[str, int] | None = None


class Scheduler:
    def __init__(
        self,
        *,
        storage: Storage,
        clock: Clock | None = None,
        instance_id: str | None = None,
    ) -> None:
        self.storage = storage
        self.clock = clock or SystemClock()
        self.instance_id = instance_id or self._default_instance_id()
        self.health = HealthService(storage)
        self._tasks: dict[str, ScheduledTask] = {}

    def register(self, task: ScheduledTask, *, enabled: bool = True) -> None:
        if task.task_id in self._tasks:
            raise ValueError(f"重复任务 ID：{task.task_id}")
        if task.interval_seconds <= 0 or task.lock_ttl_seconds <= 0:
            raise ValueError("任务 interval_seconds 和 lock_ttl_seconds 必须大于 0。")
        self._tasks[task.task_id] = task
        now = self._now()
        with self.storage.transaction() as repos:
            repos.scheduler.ensure_job(
                task_id=task.task_id,
                module_id=task.module_id,
                interval_seconds=task.interval_seconds,
                enabled=enabled,
                first_run_at=now,
            )

    def tick(self) -> list[RunOutcome]:
        now = self._now()
        self.health.record(
            instance_id=self.instance_id,
            status="running",
            version=__version__,
            details={"mode": "tick"},
            seen_at=now,
        )
        with self.storage.transaction() as repos:
            due_ids = [job.task_id for job in repos.scheduler.due_jobs(now)]
        outcomes = [self.run_once(task_id, only_if_due=True) for task_id in due_ids]
        failed = sum(item.status == "failed" for item in outcomes)
        self.health.record(
            instance_id=self.instance_id,
            status="degraded" if failed else "healthy",
            version=__version__,
            details={"mode": "tick", "due": len(due_ids), "failed": failed},
            seen_at=self._now(),
        )
        return outcomes

    def run_once(
        self, task_id: str, *, dry_run: bool = False, only_if_due: bool = False
    ) -> RunOutcome:
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"任务未注册：{task_id}")
        started_at = self._now()
        with self.storage.transaction() as repos:
            job = repos.scheduler.get_job(task_id)
            if job is None:
                raise KeyError(f"任务未写入数据库：{task_id}")
            scheduled_for = as_utc(job.next_run_at)
            if only_if_due and scheduled_for > started_at:
                return RunOutcome(task_id, "not_due")
            attempt = job.failure_count + 1

        owner_id = f"{self.instance_id}:{uuid.uuid4()}"
        lock_key = f"task:{task_id}"
        with self.storage.transaction() as repos:
            acquired = repos.scheduler.try_acquire_lock(
                lock_key,
                owner_id,
                now=started_at,
                ttl_seconds=task.lock_ttl_seconds,
            )
        if not acquired:
            return RunOutcome(task_id, "skipped_locked")

        run_id: str | None = None
        try:
            with self.storage.transaction() as repos:
                repos.scheduler.mark_job_started(task_id, started_at)
                run = repos.scheduler.create_run(
                    task_id=task_id,
                    scheduled_for=scheduled_for if only_if_due else started_at,
                    started_at=started_at,
                    attempt=attempt,
                )
                run_id = run.run_id

            context = TaskContext(
                now=started_at,
                scheduled_for=scheduled_for if only_if_due else started_at,
                attempt=attempt,
                dry_run=dry_run,
            )
            result = task.execute(context)
            finished_at = self._now()
            with self.storage.transaction() as repos:
                repos.scheduler.finish_run(
                    run_id=run_id,
                    status="dry_run" if dry_run else "success",
                    finished_at=finished_at,
                    result_counts=result.counts,
                    details=result.details,
                )
                if not dry_run:
                    repos.scheduler.mark_job_success(
                        task_id=task_id,
                        finished_at=finished_at,
                        interval_seconds=task.interval_seconds,
                        cursor=result.cursor,
                    )
            return RunOutcome(
                task_id,
                "dry_run" if dry_run else "success",
                run_id=run_id,
                counts=result.counts,
            )
        except Exception as exc:
            finished_at = self._now()
            if isinstance(exc, ExternalServiceError):
                retryable = exc.retryable
            elif isinstance(exc, (AuthenticationError, ConfigurationError, FieldCatalogError)):
                retryable = False
            else:
                retryable = True
            failure_count = attempt
            delay = task.retry_policy.delay_seconds(failure_count, retryable=retryable)
            if run_id is not None:
                with self.storage.transaction() as repos:
                    repos.scheduler.finish_run(
                        run_id=run_id,
                        status="failed",
                        finished_at=finished_at,
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )
                    repos.scheduler.mark_job_failed(
                        task_id=task_id,
                        finished_at=finished_at,
                        retry_at=finished_at + timedelta(seconds=delay),
                    )
            logger.exception("任务执行失败", extra={"task_id": task_id, "run_id": run_id})
            return RunOutcome(task_id, "failed", run_id=run_id, error=str(exc))
        finally:
            with self.storage.transaction() as repos:
                repos.scheduler.release_lock(lock_key, owner_id)

    def _now(self) -> datetime:
        return as_utc(self.clock.now())

    @staticmethod
    def _default_instance_id() -> str:
        return f"{socket.gethostname()}:{os.getpid()}"
