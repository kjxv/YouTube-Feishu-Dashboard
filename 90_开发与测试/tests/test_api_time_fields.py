from __future__ import annotations

from datetime import UTC, date, datetime

from yfd_latest_video_tracker.api_time_fields import (
    analytics_time_values,
    data_api_time_values,
    reporting_time_values,
)


def test_data_api_fetch_and_inferred_cutoff_share_one_instant_in_two_timezones() -> None:
    values = data_api_time_values(datetime(2026, 9, 4, 8, 30, 15, tzinfo=UTC))

    assert values == {
        "DATA_API_FETCHED_AT_PACIFIC": "2026-09-04T01:30:15-07:00",
        "DATA_API_FETCHED_AT_BEIJING": "2026-09-04T16:30:15+08:00",
        "DATA_API_DATA_THROUGH_AT_PACIFIC_INFERRED": "2026-09-04T01:30:15-07:00",
        "DATA_API_DATA_THROUGH_AT_BEIJING_INFERRED": "2026-09-04T16:30:15+08:00",
    }


def test_analytics_official_pacific_day_end_is_converted_to_same_beijing_instant() -> None:
    values = analytics_time_values(
        fetched_at=datetime(2026, 9, 4, 8, 30, 15, tzinfo=UTC),
        data_through_date=date(2026, 9, 1),
    )

    assert values == {
        "ANALYTICS_FETCHED_AT_PACIFIC": "2026-09-04T01:30:15-07:00",
        "ANALYTICS_FETCHED_AT_BEIJING": "2026-09-04T16:30:15+08:00",
        "ANALYTICS_DATA_THROUGH_AT_PACIFIC": "2026-09-01T23:59:59-07:00",
        "ANALYTICS_DATA_THROUGH_AT_BEIJING": "2026-09-02T14:59:59+08:00",
    }


def test_reporting_time_fields_remain_empty_until_report_data_exists() -> None:
    values = reporting_time_values(fetched_at=None, data_through_date=None)

    assert all(value is None for value in values.values())


def test_reporting_uses_winter_pacific_offset_when_date_requires_it() -> None:
    values = reporting_time_values(
        fetched_at=datetime(2026, 12, 3, 8, 0, tzinfo=UTC),
        data_through_date=date(2026, 12, 1),
    )

    assert values["REPORTING_FETCHED_AT_PACIFIC"] == "2026-12-03T00:00:00-08:00"
    assert values["REPORTING_FETCHED_AT_BEIJING"] == "2026-12-03T16:00:00+08:00"
    assert values["REPORTING_DATA_THROUGH_AT_PACIFIC"] == (
        "2026-12-01T23:59:59-08:00"
    )
    assert values["REPORTING_DATA_THROUGH_AT_BEIJING"] == (
        "2026-12-02T15:59:59+08:00"
    )
