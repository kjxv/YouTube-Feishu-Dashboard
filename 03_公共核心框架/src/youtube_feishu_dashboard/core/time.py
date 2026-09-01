"""统一使用带时区 UTC 时间，展示时再转换到项目时区。"""

from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """兼容 SQLite 返回的无时区值，并统一转换为 UTC。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
