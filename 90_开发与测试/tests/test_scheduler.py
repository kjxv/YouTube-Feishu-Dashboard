from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from youtube_feishu_dashboard.core.errors import ExternalServiceError
from youtube_feishu_dashboard.core.time import as_utc
from youtube_feishu_dashboard.db.models import ScheduledJob, TaskRun
from youtube_feishu_dashboard.db.repositories import SqlAlchemyStorage
from youtube_feishu_dashboard.scheduler.runner import Scheduler
from youtube_feishu_dashboard.scheduler.task import RetryPolicy, TaskContext, TaskResult


@dataclass
class ManualClock:
    current: datetime

    def now(self) -> datetime:
        return self.current

    def advance(self, **kwargs: int) -> None:
        self.current += timedelta(**kwargs)


@dataclass
class FakeTask:
    task_id: str = "test-task"
    module_id: str = "test_module"
    interval_seconds: int = 1800
    lock_ttl_seconds: int = 900
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    calls: list[TaskContext] = field(default_factory=list)
    fail_once: bool = False

    def execute(self, context: TaskContext) -> TaskResult:
        self.calls.append(context)
        if self.fail_once:
            self.fail_once = False
            raise ExternalServiceError("模拟临时故障", retryable=True)
        return TaskResult(counts={"observations": 1}, cursor={"last": len(self.calls)})


def test_tick_recovers_from_long_offline_gap_without_fake_catch_up(
    storage: SqlAlchemyStorage,
) -> None:
    clock = ManualClock(datetime(2026, 8, 31, 0, 0, tzinfo=UTC))
    task = FakeTask()
    scheduler = Scheduler(storage=storage, clock=clock, instance_id="worker-test")
    scheduler.register(task)

    assert scheduler.tick()[0].status == "success"
    assert task.calls[0].force is False
    clock.advance(hours=3)
    recovered = scheduler.tick()

    assert recovered[0].status == "success"
    assert len(task.calls) == 2
    assert task.calls[1].now == clock.current
    with storage.transaction() as repos:
        job = repos.session.get(ScheduledJob, task.task_id)
        assert job is not None
        assert as_utc(job.next_run_at) == clock.current + timedelta(minutes=30)
        runs = list(repos.session.scalars(select(TaskRun).order_by(TaskRun.started_at)))
    assert len(runs) == 2
    assert all(run.status == "success" for run in runs)


def test_failure_is_persisted_and_retried_by_backoff(storage: SqlAlchemyStorage) -> None:
    clock = ManualClock(datetime(2026, 8, 31, 1, 0, tzinfo=UTC))
    task = FakeTask(fail_once=True)
    scheduler = Scheduler(storage=storage, clock=clock, instance_id="worker-test")
    scheduler.register(task)

    failed = scheduler.tick()[0]
    assert failed.status == "failed"
    with storage.transaction() as repos:
        job = repos.scheduler.get_job(task.task_id)
        assert job is not None
        assert job.failure_count == 1
        assert as_utc(job.next_run_at) == clock.current + timedelta(seconds=60)

    clock.advance(seconds=59)
    assert scheduler.tick() == []
    clock.advance(seconds=1)
    assert scheduler.tick()[0].status == "success"


def test_database_lock_prevents_duplicate_run(storage: SqlAlchemyStorage) -> None:
    clock = ManualClock(datetime(2026, 8, 31, 2, 0, tzinfo=UTC))
    task = FakeTask()
    scheduler = Scheduler(storage=storage, clock=clock, instance_id="worker-b")
    scheduler.register(task)
    with storage.transaction() as repos:
        assert repos.scheduler.try_acquire_lock(
            "task:test-task", "worker-a", now=clock.current, ttl_seconds=900
        )

    result = scheduler.run_once(task.task_id)

    assert result.status == "skipped_locked"
    assert task.calls == []


def test_manual_run_is_forced_but_scheduled_run_can_respect_cadence(
    storage: SqlAlchemyStorage,
) -> None:
    clock = ManualClock(datetime(2026, 8, 31, 2, 30, tzinfo=UTC))
    task = FakeTask()
    scheduler = Scheduler(storage=storage, clock=clock, instance_id="worker-test")
    scheduler.register(task)

    scheduler.run_once(task.task_id)
    scheduler.run_once(task.task_id, force=False)

    assert [context.force for context in task.calls] == [True, False]


def test_dry_run_does_not_advance_schedule(storage: SqlAlchemyStorage) -> None:
    clock = ManualClock(datetime(2026, 8, 31, 3, 0, tzinfo=UTC))
    task = FakeTask()
    scheduler = Scheduler(storage=storage, clock=clock, instance_id="worker-test")
    scheduler.register(task)
    before: datetime
    with storage.transaction() as repos:
        job = repos.scheduler.get_job(task.task_id)
        assert job is not None
        before = as_utc(job.next_run_at)

    result = scheduler.run_once(task.task_id, dry_run=True)

    assert result.status == "dry_run"
    with storage.transaction() as repos:
        job = repos.scheduler.get_job(task.task_id)
        assert job is not None
        assert as_utc(job.next_run_at) == before
