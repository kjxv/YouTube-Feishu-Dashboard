"""add per-video YouTube Analytics cache

Revision ID: 20260903_0002
Revises: 20260831_0001
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0002"
down_revision: str | None = "20260831_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "video_analytics_snapshots",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            nullable=False,
        ),
        sa.Column("video_id", sa.String(length=32), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("requested_end_date", sa.Date(), nullable=False),
        sa.Column("data_through_date", sa.Date(), nullable=True),
        sa.Column("metric_values", sa.JSON(), nullable=False),
        sa.Column("raw_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["videos.id"],
            name=op.f("fk_video_analytics_snapshots_video_id_videos"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_video_analytics_snapshots")),
        sa.UniqueConstraint(
            "video_id",
            "fetched_at",
            name="uq_video_analytics_snapshot_fetch",
        ),
    )
    op.create_index(
        op.f("ix_video_analytics_snapshots_fetched_at"),
        "video_analytics_snapshots",
        ["fetched_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_video_analytics_snapshots_video_id"),
        "video_analytics_snapshots",
        ["video_id"],
        unique=False,
    )
    op.create_index(
        "ix_video_analytics_video_fetched",
        "video_analytics_snapshots",
        ["video_id", "fetched_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_video_analytics_video_fetched",
        table_name="video_analytics_snapshots",
    )
    op.drop_index(
        op.f("ix_video_analytics_snapshots_video_id"),
        table_name="video_analytics_snapshots",
    )
    op.drop_index(
        op.f("ix_video_analytics_snapshots_fetched_at"),
        table_name="video_analytics_snapshots",
    )
    op.drop_table("video_analytics_snapshots")
