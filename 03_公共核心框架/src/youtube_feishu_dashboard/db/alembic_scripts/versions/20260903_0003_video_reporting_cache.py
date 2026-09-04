"""add per-video YouTube Reporting reach cache

Revision ID: 20260903_0003
Revises: 20260903_0002
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0003"
down_revision: str | None = "20260903_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "video_reporting_snapshots",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            nullable=False,
        ),
        sa.Column("video_id", sa.String(length=32), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_through_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("field_values", sa.JSON(), nullable=False),
        sa.Column("raw_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["videos.id"],
            name=op.f("fk_video_reporting_snapshots_video_id_videos"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_video_reporting_snapshots")),
        sa.UniqueConstraint(
            "video_id",
            "checked_at",
            name="uq_video_reporting_snapshot_check",
        ),
    )
    op.create_index(
        op.f("ix_video_reporting_snapshots_checked_at"),
        "video_reporting_snapshots",
        ["checked_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_video_reporting_snapshots_video_id"),
        "video_reporting_snapshots",
        ["video_id"],
        unique=False,
    )
    op.create_index(
        "ix_video_reporting_video_checked",
        "video_reporting_snapshots",
        ["video_id", "checked_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_video_reporting_video_checked",
        table_name="video_reporting_snapshots",
    )
    op.drop_index(
        op.f("ix_video_reporting_snapshots_video_id"),
        table_name="video_reporting_snapshots",
    )
    op.drop_index(
        op.f("ix_video_reporting_snapshots_checked_at"),
        table_name="video_reporting_snapshots",
    )
    op.drop_table("video_reporting_snapshots")
