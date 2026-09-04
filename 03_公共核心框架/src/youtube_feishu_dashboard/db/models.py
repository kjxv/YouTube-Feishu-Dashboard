"""全部模块共用的数据模型。"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from youtube_feishu_dashboard.core.time import utc_now
from youtube_feishu_dashboard.db.base import Base


def uuid4_str() -> str:
    return str(uuid.uuid4())


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str | None] = mapped_column(String(255))
    uploads_playlist_id: Mapped[str | None] = mapped_column(String(64))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    videos: Mapped[list[Video]] = relationship(back_populates="channel")


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    privacy_status: Mapped[str | None] = mapped_column(String(32))
    video_type: Mapped[str | None] = mapped_column(String(32))
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    channel: Mapped[Channel] = relationship(back_populates="videos")
    snapshots: Mapped[list[VideoSnapshot]] = relationship(back_populates="video")

    __table_args__ = (Index("ix_videos_channel_published", "channel_id", "published_at"),)


class VideoSnapshot(Base):
    __tablename__ = "video_snapshots"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source: Mapped[str] = mapped_column(String(32), default="youtube_data_api")
    view_count: Mapped[int | None] = mapped_column(BigInteger)
    like_count: Mapped[int | None] = mapped_column(BigInteger)
    comment_count: Mapped[int | None] = mapped_column(BigInteger)
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    video: Mapped[Video] = relationship(back_populates="snapshots")

    __table_args__ = (
        UniqueConstraint("video_id", "observed_at", name="uq_video_snapshot_observation"),
        Index("ix_snapshots_video_observed", "video_id", "observed_at"),
    )


class VideoAnalyticsSnapshot(Base):
    """按视频保存 Analytics API 的低频汇总结果，供高频 Data API 任务复用。"""

    __tablename__ = "video_analytics_snapshots"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    requested_end_date: Mapped[date] = mapped_column(Date)
    data_through_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    metric_values: Mapped[dict[str, Any]] = mapped_column(JSON)
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint(
            "video_id",
            "fetched_at",
            name="uq_video_analytics_snapshot_fetch",
        ),
        Index(
            "ix_video_analytics_video_fetched",
            "video_id",
            "fetched_at",
        ),
    )


class VideoReportingSnapshot(Base):
    """按视频保存 Reporting API Reach 汇总及最近检查状态。"""

    __tablename__ = "video_reporting_snapshots"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"), index=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    data_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    data_through_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    field_values: Mapped[dict[str, Any]] = mapped_column(JSON)
    raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint(
            "video_id",
            "checked_at",
            name="uq_video_reporting_snapshot_check",
        ),
        Index(
            "ix_video_reporting_video_checked",
            "video_id",
            "checked_at",
        ),
    )


class ApiFieldCapability(Base):
    __tablename__ = "api_field_capabilities"

    standard_field_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    catalog_version: Mapped[str] = mapped_column(String(32))
    cn_name: Mapped[str] = mapped_column(String(255))
    api_source: Mapped[str] = mapped_column(String(32))
    official_field: Mapped[str] = mapped_column(String(255))
    data_type: Mapped[str] = mapped_column(String(32))
    unit: Mapped[str | None] = mapped_column(String(32))
    entity_level: Mapped[str] = mapped_column(String(64))
    query_group: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ConfigCache(Base):
    __tablename__ = "config_cache"

    cache_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    version_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(32), default="feishu")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_error: Mapped[str | None] = mapped_column(Text)


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"

    task_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    module_id: Mapped[str] = mapped_column(String(100), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    interval_seconds: Mapped[int] = mapped_column(Integer)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    cursor: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class TaskLock(Base):
    __tablename__ = "task_locks"

    lock_key: Mapped[str] = mapped_column(String(150), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(150), index=True)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class TaskRun(Base):
    __tablename__ = "task_runs"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    task_id: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    result_counts: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_type: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class SystemHeartbeat(Base):
    __tablename__ = "system_heartbeats"

    instance_id: Mapped[str] = mapped_column(String(150), primary_key=True)
    status: Mapped[str] = mapped_column(String(32))
    version: Mapped[str] = mapped_column(String(32))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class ArchiveBatch(Base):
    __tablename__ = "archive_batches"

    batch_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    module_id: Mapped[str] = mapped_column(String(100), index=True)
    source_table_id: Mapped[str] = mapped_column(String(100))
    target_table_id: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(32), index=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    cutoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class FeishuRecordBinding(Base):
    __tablename__ = "feishu_record_bindings"

    binding_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
    table_id: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_key: Mapped[str] = mapped_column(String(255))
    record_id: Mapped[str] = mapped_column(String(100))
    last_payload_hash: Mapped[str | None] = mapped_column(String(64))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("table_id", "entity_type", "entity_key", name="uq_feishu_binding_entity"),
    )
