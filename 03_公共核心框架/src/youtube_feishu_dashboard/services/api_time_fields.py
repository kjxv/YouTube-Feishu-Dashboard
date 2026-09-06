"""Data/Analytics/Reporting API 的统一时间口径与展示字段。"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from youtube_feishu_dashboard.core.time import as_utc

PACIFIC_TIMEZONE = ZoneInfo("America/Los_Angeles")
BEIJING_TIMEZONE = ZoneInfo("Asia/Shanghai")

DATA_API_TIME_FIELD_IDS = (
    "DATA_API_FETCHED_AT_PACIFIC",
    "DATA_API_FETCHED_AT_BEIJING",
    "DATA_API_DATA_THROUGH_AT_PACIFIC_INFERRED",
    "DATA_API_DATA_THROUGH_AT_BEIJING_INFERRED",
)

ANALYTICS_TIME_FIELD_IDS = (
    "ANALYTICS_FETCHED_AT_PACIFIC",
    "ANALYTICS_FETCHED_AT_BEIJING",
    "ANALYTICS_DATA_THROUGH_AT_PACIFIC",
    "ANALYTICS_DATA_THROUGH_AT_BEIJING",
)

REPORTING_TIME_FIELD_IDS = (
    "REPORTING_FETCHED_AT_PACIFIC",
    "REPORTING_FETCHED_AT_BEIJING",
    "REPORTING_DATA_THROUGH_AT_PACIFIC",
    "REPORTING_DATA_THROUGH_AT_BEIJING",
)


def data_api_time_values(fetched_at: datetime) -> dict[str, str]:
    """Data API 没有官方统计截止时间，因此以获取时刻明确标为推定。"""

    pacific = zoned_text(fetched_at, PACIFIC_TIMEZONE)
    beijing = zoned_text(fetched_at, BEIJING_TIMEZONE)
    return {
        "DATA_API_FETCHED_AT_PACIFIC": pacific,
        "DATA_API_FETCHED_AT_BEIJING": beijing,
        "DATA_API_DATA_THROUGH_AT_PACIFIC_INFERRED": pacific,
        "DATA_API_DATA_THROUGH_AT_BEIJING_INFERRED": beijing,
    }


def analytics_time_values(
    *, fetched_at: datetime, data_through_date: date | None
) -> dict[str, str | None]:
    values: dict[str, str | None] = {
        "ANALYTICS_FETCHED_AT_PACIFIC": zoned_text(fetched_at, PACIFIC_TIMEZONE),
        "ANALYTICS_FETCHED_AT_BEIJING": zoned_text(fetched_at, BEIJING_TIMEZONE),
    }
    values.update(
        official_reporting_day_end_values(
            prefix="ANALYTICS", data_through_date=data_through_date
        )
    )
    return values


def reporting_time_values(
    *, fetched_at: datetime | None, data_through_date: date | None
) -> dict[str, str | None]:
    values: dict[str, str | None] = {
        "REPORTING_FETCHED_AT_PACIFIC": (
            zoned_text(fetched_at, PACIFIC_TIMEZONE) if fetched_at is not None else None
        ),
        "REPORTING_FETCHED_AT_BEIJING": (
            zoned_text(fetched_at, BEIJING_TIMEZONE) if fetched_at is not None else None
        ),
    }
    values.update(
        official_reporting_day_end_values(
            prefix="REPORTING", data_through_date=data_through_date
        )
    )
    return values


def official_reporting_day_end_values(
    *, prefix: str, data_through_date: date | None
) -> dict[str, str | None]:
    pacific_field = f"{prefix}_DATA_THROUGH_AT_PACIFIC"
    beijing_field = f"{prefix}_DATA_THROUGH_AT_BEIJING"
    if data_through_date is None:
        return {pacific_field: None, beijing_field: None}
    pacific_day_end = datetime.combine(
        data_through_date, time(23, 59, 59), tzinfo=PACIFIC_TIMEZONE
    )
    return {
        pacific_field: zoned_text(pacific_day_end, PACIFIC_TIMEZONE),
        beijing_field: zoned_text(pacific_day_end, BEIJING_TIMEZONE),
    }


def zoned_text(value: datetime, timezone: ZoneInfo) -> str:
    return as_utc(value).astimezone(timezone).isoformat(timespec="seconds")
