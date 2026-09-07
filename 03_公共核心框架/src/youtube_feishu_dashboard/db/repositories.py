"""统一 Repository/Storage 接口与 SQLAlchemy 实现。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Protocol, cast

from sqlalchemy import delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from youtube_feishu_dashboard.core.time import as_utc, utc_now
from youtube_feishu_dashboard.db.database import Database
from youtube_feishu_dashboard.db.models import (
    ApiFieldCapability,
    ArchiveBatch,
    Channel,
    ConfigCache,
    FeishuRecordBinding,
    ScheduledJob,
    SystemHeartbeat,
    TaskLock,
    TaskRun,
    Video,
    VideoAnalyticsSnapshot,
    VideoReportingSnapshot,
    VideoSnapshot,
)


class Storage(Protocol):
    def transaction(self) -> AbstractContextManager[RepositorySet]: ...


@dataclass(frozen=True, slots=True)
class ComparableSnapshot:
    """用于同期对比的历史视频快照只读投影。"""

    video_id: str
    published_at: datetime
    observed_at: datetime
    view_count: int | None


class ChannelRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(
        self,
        channel_id: str,
        *,
        title: str | None,
        uploads_playlist_id: str | None,
        timezone: str,
        raw_json: dict[str, Any] | None = None,
    ) -> Channel:
        channel = self.session.get(Channel, channel_id) or Channel(id=channel_id)
        channel.title = title
        channel.uploads_playlist_id = uploads_playlist_id
        channel.timezone = timezone
        channel.raw_json = raw_json
        self.session.add(channel)
        self.session.flush()
        return channel

    def get(self, channel_id: str) -> Channel | None:
        return self.session.get(Channel, channel_id)


class VideoRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, values: dict[str, Any]) -> Video:
        video_id = str(values["id"])
        video = self.session.get(Video, video_id)
        if video is None:
            video = Video(**values)
        else:
            for key, value in values.items():
                if key != "id":
                    setattr(video, key, value)
        self.session.add(video)
        self.session.flush()
        return video

    def get(self, video_id: str) -> Video | None:
        return self.session.get(Video, video_id)

    def add_snapshot(
        self,
        *,
        video_id: str,
        observed_at: datetime,
        view_count: int | None,
        like_count: int | None,
        comment_count: int | None,
        raw_json: dict[str, Any] | None,
        source: str = "youtube_data_api",
    ) -> VideoSnapshot:
        existing = self.session.scalar(
            select(VideoSnapshot).where(
                VideoSnapshot.video_id == video_id,
                VideoSnapshot.observed_at == observed_at,
            )
        )
        if existing is not None:
            return existing
        snapshot = VideoSnapshot(
            video_id=video_id,
            observed_at=observed_at,
            view_count=view_count,
            like_count=like_count,
            comment_count=comment_count,
            raw_json=raw_json,
            source=source,
        )
        self.session.add(snapshot)
        self.session.flush()
        return snapshot

    def latest_snapshot_before(self, video_id: str, observed_at: datetime) -> VideoSnapshot | None:
        statement = (
            select(VideoSnapshot)
            .where(
                VideoSnapshot.video_id == video_id,
                VideoSnapshot.observed_at < observed_at,
            )
            .order_by(VideoSnapshot.observed_at.desc())
            .limit(1)
        )
        return self.session.scalar(statement)

    def latest_snapshot_at_or_before(
        self,
        video_id: str,
        observed_at: datetime,
        *,
        source: str | None = None,
    ) -> VideoSnapshot | None:
        conditions = [
            VideoSnapshot.video_id == video_id,
            VideoSnapshot.observed_at <= observed_at,
        ]
        if source is not None:
            conditions.append(VideoSnapshot.source == source)
        statement = (
            select(VideoSnapshot)
            .where(*conditions)
            .order_by(VideoSnapshot.observed_at.desc())
            .limit(1)
        )
        return self.session.scalar(statement)

    def latest_snapshot(self, video_id: str) -> VideoSnapshot | None:
        statement = (
            select(VideoSnapshot)
            .where(VideoSnapshot.video_id == video_id)
            .order_by(VideoSnapshot.observed_at.desc())
            .limit(1)
        )
        return self.session.scalar(statement)

    def nearest_snapshot_to(
        self,
        video_id: str,
        target_at: datetime,
        *,
        tolerance_minutes: int,
    ) -> VideoSnapshot | None:
        """返回目标时刻附近最近的真实 Data API 快照；等距时优先目标之后。"""

        if tolerance_minutes < 0:
            raise ValueError("tolerance_minutes 不能为负数。")
        target = as_utc(target_at)
        tolerance = timedelta(minutes=tolerance_minutes)
        snapshots = list(
            self.session.scalars(
                select(VideoSnapshot)
                .where(
                    VideoSnapshot.video_id == video_id,
                    VideoSnapshot.observed_at >= target - tolerance,
                    VideoSnapshot.observed_at <= target + tolerance,
                    VideoSnapshot.view_count.is_not(None),
                )
                .order_by(VideoSnapshot.observed_at.asc())
            )
        )
        if not snapshots:
            return None

        def selection_key(item: VideoSnapshot) -> tuple[float, bool]:
            observed = as_utc(item.observed_at)
            return (abs((observed - target).total_seconds()), observed < target)

        return min(snapshots, key=selection_key)

    def list_published_since(self, channel_id: str, cutoff: datetime) -> list[Video]:
        statement = (
            select(Video)
            .where(Video.channel_id == channel_id, Video.published_at >= cutoff)
            .order_by(Video.published_at.desc())
        )
        return list(self.session.scalars(statement))

    def list_comparison_snapshots(
        self,
        *,
        channel_id: str,
        exclude_video_id: str,
        video_type: str,
    ) -> list[ComparableSnapshot]:
        """列出同频道、同类型的历史快照，由模块按发布后时间档选样本。"""
        statement = (
            select(
                Video.id,
                Video.published_at,
                VideoSnapshot.observed_at,
                VideoSnapshot.view_count,
            )
            .join(VideoSnapshot, VideoSnapshot.video_id == Video.id)
            .where(
                Video.channel_id == channel_id,
                Video.id != exclude_video_id,
                Video.video_type == video_type,
            )
            .order_by(Video.published_at.desc(), VideoSnapshot.observed_at.asc())
        )
        return [
            ComparableSnapshot(
                video_id=str(row[0]),
                published_at=cast(datetime, row[1]),
                observed_at=cast(datetime, row[2]),
                view_count=cast(int | None, row[3]),
            )
            for row in self.session.execute(statement)
        ]


class VideoAnalyticsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add_snapshot(
        self,
        *,
        video_id: str,
        fetched_at: datetime,
        start_date: date,
        requested_end_date: date,
        data_through_date: date | None,
        metric_values: dict[str, int | float | None],
        raw_json: dict[str, Any] | None,
    ) -> VideoAnalyticsSnapshot:
        existing = self.session.scalar(
            select(VideoAnalyticsSnapshot).where(
                VideoAnalyticsSnapshot.video_id == video_id,
                VideoAnalyticsSnapshot.fetched_at == fetched_at,
            )
        )
        if existing is not None:
            return existing
        snapshot = VideoAnalyticsSnapshot(
            video_id=video_id,
            fetched_at=fetched_at,
            start_date=start_date,
            requested_end_date=requested_end_date,
            data_through_date=data_through_date,
            metric_values=metric_values,
            raw_json=raw_json,
        )
        self.session.add(snapshot)
        self.session.flush()
        return snapshot

    def latest(self, video_id: str) -> VideoAnalyticsSnapshot | None:
        statement = (
            select(VideoAnalyticsSnapshot)
            .where(VideoAnalyticsSnapshot.video_id == video_id)
            .order_by(VideoAnalyticsSnapshot.fetched_at.desc())
            .limit(1)
        )
        return self.session.scalar(statement)


class VideoReportingRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add_snapshot(
        self,
        *,
        video_id: str,
        checked_at: datetime,
        data_fetched_at: datetime | None,
        data_through_date: date | None,
        status: str,
        field_values: dict[str, int | float | None],
        raw_json: dict[str, Any] | None,
    ) -> VideoReportingSnapshot:
        existing = self.session.scalar(
            select(VideoReportingSnapshot).where(
                VideoReportingSnapshot.video_id == video_id,
                VideoReportingSnapshot.checked_at == checked_at,
            )
        )
        if existing is not None:
            return existing
        snapshot = VideoReportingSnapshot(
            video_id=video_id,
            checked_at=checked_at,
            data_fetched_at=data_fetched_at,
            data_through_date=data_through_date,
            status=status,
            field_values=field_values,
            raw_json=raw_json,
        )
        self.session.add(snapshot)
        self.session.flush()
        return snapshot

    def latest(self, video_id: str) -> VideoReportingSnapshot | None:
        statement = (
            select(VideoReportingSnapshot)
            .where(VideoReportingSnapshot.video_id == video_id)
            .order_by(VideoReportingSnapshot.checked_at.desc())
            .limit(1)
        )
        return self.session.scalar(statement)


class CatalogRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def replace_catalog(self, version: str, fields: list[dict[str, Any]]) -> None:
        incoming_ids = {str(item["standard_field_id"]) for item in fields}
        if incoming_ids:
            self.session.execute(
                delete(ApiFieldCapability).where(
                    ApiFieldCapability.standard_field_id.not_in(incoming_ids)
                )
            )
        for item in fields:
            field_id = str(item["standard_field_id"])
            model = self.session.get(ApiFieldCapability, field_id) or ApiFieldCapability(
                standard_field_id=field_id
            )
            for key, value in item.items():
                if key != "standard_field_id":
                    setattr(model, key, value)
            model.catalog_version = version
            self.session.add(model)


class ConfigCacheRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, cache_key: str) -> ConfigCache | None:
        return self.session.get(ConfigCache, cache_key)

    def put(
        self,
        *,
        cache_key: str,
        version_hash: str,
        payload: dict[str, Any],
        fetched_at: datetime,
        valid_until: datetime,
        source: str = "feishu",
        last_error: str | None = None,
    ) -> ConfigCache:
        cached = self.session.get(ConfigCache, cache_key) or ConfigCache(cache_key=cache_key)
        cached.version_hash = version_hash
        cached.payload = payload
        cached.fetched_at = fetched_at
        cached.valid_until = valid_until
        cached.source = source
        cached.last_error = last_error
        self.session.add(cached)
        return cached


class SchedulerRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def ensure_job(
        self,
        *,
        task_id: str,
        module_id: str,
        interval_seconds: int,
        enabled: bool = True,
        first_run_at: datetime | None = None,
    ) -> ScheduledJob:
        job = self.session.get(ScheduledJob, task_id)
        if job is None:
            job = ScheduledJob(
                task_id=task_id,
                module_id=module_id,
                interval_seconds=interval_seconds,
                enabled=enabled,
                next_run_at=first_run_at or utc_now(),
            )
        else:
            job.module_id = module_id
            job.interval_seconds = interval_seconds
            job.enabled = enabled
        self.session.add(job)
        self.session.flush()
        return job

    def get_job(self, task_id: str) -> ScheduledJob | None:
        return self.session.get(ScheduledJob, task_id)

    def list_jobs(self) -> list[ScheduledJob]:
        statement = select(ScheduledJob).order_by(ScheduledJob.task_id)
        return list(self.session.scalars(statement))

    def list_recent_runs(self, *, limit: int = 20) -> list[TaskRun]:
        if limit <= 0:
            raise ValueError("limit 必须大于 0。")
        statement = select(TaskRun).order_by(TaskRun.started_at.desc()).limit(limit)
        return list(self.session.scalars(statement))

    def list_locks(self) -> list[TaskLock]:
        statement = select(TaskLock).order_by(TaskLock.expires_at)
        return list(self.session.scalars(statement))

    def set_enabled_if_exists(self, task_id: str, enabled: bool) -> None:
        job = self.session.get(ScheduledJob, task_id)
        if job is not None:
            job.enabled = enabled
            self.session.add(job)

    def due_jobs(self, now: datetime) -> list[ScheduledJob]:
        statement = (
            select(ScheduledJob)
            .where(ScheduledJob.enabled.is_(True), ScheduledJob.next_run_at <= now)
            .order_by(ScheduledJob.next_run_at)
        )
        return list(self.session.scalars(statement))

    def try_acquire_lock(
        self, lock_key: str, owner_id: str, *, now: datetime, ttl_seconds: int
    ) -> bool:
        expires_at = now + timedelta(seconds=ttl_seconds)
        updated = cast(
            CursorResult[Any],
            self.session.execute(
                update(TaskLock)
                .where(TaskLock.lock_key == lock_key, TaskLock.expires_at <= now)
                .values(owner_id=owner_id, acquired_at=now, expires_at=expires_at)
            ),
        )
        if updated.rowcount:
            return True
        if self.session.get(TaskLock, lock_key) is not None:
            return False
        try:
            with self.session.begin_nested():
                self.session.add(
                    TaskLock(
                        lock_key=lock_key,
                        owner_id=owner_id,
                        acquired_at=now,
                        expires_at=expires_at,
                    )
                )
                self.session.flush()
        except IntegrityError:
            return False
        return True

    def release_lock(self, lock_key: str, owner_id: str) -> None:
        self.session.execute(
            delete(TaskLock).where(TaskLock.lock_key == lock_key, TaskLock.owner_id == owner_id)
        )

    def create_run(
        self, *, task_id: str, scheduled_for: datetime, started_at: datetime, attempt: int
    ) -> TaskRun:
        run = TaskRun(
            task_id=task_id,
            status="running",
            scheduled_for=scheduled_for,
            started_at=started_at,
            attempt=attempt,
        )
        self.session.add(run)
        self.session.flush()
        return run

    def mark_job_started(self, task_id: str, started_at: datetime) -> None:
        job = self.session.get(ScheduledJob, task_id)
        if job is None:
            raise KeyError(task_id)
        job.last_attempt_at = started_at

    def mark_job_success(
        self,
        *,
        task_id: str,
        finished_at: datetime,
        interval_seconds: int,
        cursor: dict[str, Any] | None,
    ) -> None:
        job = self.session.get(ScheduledJob, task_id)
        if job is None:
            raise KeyError(task_id)
        job.last_success_at = finished_at
        job.failure_count = 0
        job.next_run_at = finished_at + timedelta(seconds=interval_seconds)
        if cursor is not None:
            job.cursor = cursor

    def mark_job_failed(self, *, task_id: str, finished_at: datetime, retry_at: datetime) -> None:
        job = self.session.get(ScheduledJob, task_id)
        if job is None:
            raise KeyError(task_id)
        job.failure_count += 1
        job.next_run_at = retry_at
        job.updated_at = finished_at

    def finish_run(
        self,
        *,
        run_id: str,
        status: str,
        finished_at: datetime,
        result_counts: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        run = self.session.get(TaskRun, run_id)
        if run is None:
            raise KeyError(run_id)
        run.status = status
        run.finished_at = finished_at
        run.result_counts = result_counts
        run.details = details
        run.error_type = error_type
        run.error_message = error_message[:4000] if error_message else None


class HealthRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def heartbeat(
        self,
        *,
        instance_id: str,
        status: str,
        version: str,
        seen_at: datetime,
        details: dict[str, Any] | None = None,
    ) -> SystemHeartbeat:
        item = self.session.get(SystemHeartbeat, instance_id) or SystemHeartbeat(
            instance_id=instance_id
        )
        item.status = status
        item.version = version
        item.last_seen_at = seen_at
        item.details = details
        self.session.add(item)
        return item

    def get(self, instance_id: str) -> SystemHeartbeat | None:
        return self.session.get(SystemHeartbeat, instance_id)

    def list_recent(self, *, limit: int = 20) -> list[SystemHeartbeat]:
        if limit <= 0:
            raise ValueError("limit 必须大于 0。")
        statement = (
            select(SystemHeartbeat)
            .order_by(SystemHeartbeat.last_seen_at.desc())
            .limit(limit)
        )
        return list(self.session.scalars(statement))


class ArchiveRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, batch: ArchiveBatch) -> ArchiveBatch:
        self.session.add(batch)
        self.session.flush()
        return batch

    def find_open(self, module_id: str, source_table_id: str) -> ArchiveBatch | None:
        statement = select(ArchiveBatch).where(
            ArchiveBatch.module_id == module_id,
            ArchiveBatch.source_table_id == source_table_id,
            ArchiveBatch.status.in_(("required", "running")),
        )
        return self.session.scalar(statement)


class BindingRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, table_id: str, entity_type: str, entity_key: str) -> FeishuRecordBinding | None:
        statement = select(FeishuRecordBinding).where(
            FeishuRecordBinding.table_id == table_id,
            FeishuRecordBinding.entity_type == entity_type,
            FeishuRecordBinding.entity_key == entity_key,
        )
        return self.session.scalar(statement)

    def list_for_table(self, table_id: str) -> list[FeishuRecordBinding]:
        statement = select(FeishuRecordBinding).where(
            FeishuRecordBinding.table_id == table_id
        )
        return list(self.session.scalars(statement))

    def upsert(
        self,
        *,
        table_id: str,
        entity_type: str,
        entity_key: str,
        record_id: str,
        payload_hash: str | None,
    ) -> FeishuRecordBinding:
        binding = self.get(table_id, entity_type, entity_key)
        if binding is None:
            binding = FeishuRecordBinding(
                table_id=table_id,
                entity_type=entity_type,
                entity_key=entity_key,
                record_id=record_id,
            )
        binding.record_id = record_id
        binding.last_payload_hash = payload_hash
        binding.synced_at = utc_now()
        self.session.add(binding)
        return binding


@dataclass(slots=True)
class RepositorySet:
    channels: ChannelRepository
    videos: VideoRepository
    video_analytics: VideoAnalyticsRepository
    video_reporting: VideoReportingRepository
    catalog: CatalogRepository
    config_cache: ConfigCacheRepository
    scheduler: SchedulerRepository
    health: HealthRepository
    archives: ArchiveRepository
    bindings: BindingRepository
    session: Session


class SqlAlchemyStorage:
    def __init__(self, database: Database) -> None:
        self.database = database

    @contextmanager
    def transaction(self) -> Iterator[RepositorySet]:
        with self.database.session() as session:
            yield RepositorySet(
                channels=ChannelRepository(session),
                videos=VideoRepository(session),
                video_analytics=VideoAnalyticsRepository(session),
                video_reporting=VideoReportingRepository(session),
                catalog=CatalogRepository(session),
                config_cache=ConfigCacheRepository(session),
                scheduler=SchedulerRepository(session),
                health=HealthRepository(session),
                archives=ArchiveRepository(session),
                bindings=BindingRepository(session),
                session=session,
            )
