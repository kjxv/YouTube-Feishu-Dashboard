"""统一 Repository/Storage 接口与 SQLAlchemy 实现。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol, cast

from sqlalchemy import delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from youtube_feishu_dashboard.core.time import utc_now
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
    VideoSnapshot,
)


class Storage(Protocol):
    def transaction(self) -> AbstractContextManager[RepositorySet]: ...


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

    def list_published_since(self, channel_id: str, cutoff: datetime) -> list[Video]:
        statement = (
            select(Video)
            .where(Video.channel_id == channel_id, Video.published_at >= cutoff)
            .order_by(Video.published_at.desc())
        )
        return list(self.session.scalars(statement))


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
                catalog=CatalogRepository(session),
                config_cache=ConfigCacheRepository(session),
                scheduler=SchedulerRepository(session),
                health=HealthRepository(session),
                archives=ArchiveRepository(session),
                bindings=BindingRepository(session),
                session=session,
            )
