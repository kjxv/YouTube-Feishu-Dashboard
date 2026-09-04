from __future__ import annotations

from datetime import UTC, datetime, timedelta

from yfd_latest_video_tracker.cadence import TrackingCadencePolicy, VideoCadenceState

PUBLISHED = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)  # 北京时间 20:00


def decide(
    now: datetime,
    *,
    data: datetime | None = None,
    analytics: datetime | None = None,
    reporting: datetime | None = None,
):
    return TrackingCadencePolicy().decide(
        VideoCadenceState(
            published_at=PUBLISHED,
            last_data_at=data,
            last_analytics_at=analytics,
            last_reporting_at=reporting,
        ),
        now,
    )


def test_first_72_hours_collect_once_per_beijing_clock_hour() -> None:
    first = PUBLISHED + timedelta(minutes=5)
    same_hour = PUBLISHED + timedelta(minutes=50)
    next_hour = PUBLISHED + timedelta(hours=1)

    assert decide(first).data_api_due is True
    assert decide(same_hour, data=first).data_api_due is False
    assert decide(next_hour, data=first).data_api_due is True


def test_after_72_hours_collects_daily_at_08_beijing() -> None:
    last = datetime(2026, 9, 4, 0, 1, tzinfo=UTC)  # 北京时间 08:01
    before_next_target = datetime(2026, 9, 4, 23, 0, tzinfo=UTC)  # 次日 07:00
    next_target = datetime(2026, 9, 5, 0, 0, tzinfo=UTC)  # 次日 08:00

    assert decide(before_next_target, data=last).data_api_due is False
    result = decide(next_target, data=last)
    assert result.data_api_due is True
    assert result.data_reason == "daily_08_beijing"


def test_publication_anniversary_adds_7_14_28_day_samples() -> None:
    morning_day_7 = PUBLISHED + timedelta(days=7, hours=-12)
    day_7 = PUBLISHED + timedelta(days=7)
    day_14 = PUBLISHED + timedelta(days=14)
    day_28 = PUBLISHED + timedelta(days=28)

    assert decide(day_7, data=morning_day_7).data_reason == "milestone_day_7"
    assert decide(day_14, data=day_7).data_reason == "milestone_day_14"
    assert decide(day_28, data=day_14).data_reason == "milestone_day_28"


def test_after_28_days_only_one_real_final_catch_up_is_allowed() -> None:
    day_27 = PUBLISHED + timedelta(days=27)
    resumed = PUBLISHED + timedelta(days=29)
    final_result = decide(resumed, data=day_27)

    assert final_result.data_api_due is True
    assert "milestone_day_28" in final_result.data_reason
    assert decide(resumed + timedelta(hours=1), data=resumed).data_api_due is False
    assert decide(resumed, data=None).data_api_due is False


def test_analytics_uses_daily_08_and_reporting_uses_six_hour_slots() -> None:
    at_08 = datetime(2026, 9, 3, 0, 0, tzinfo=UTC)
    at_14 = datetime(2026, 9, 3, 6, 0, tzinfo=UTC)
    before_08 = at_08 - timedelta(hours=1)
    previous_08 = at_08 - timedelta(days=1)
    same_day_02 = at_08 - timedelta(hours=6)

    before = decide(before_08, analytics=previous_08, reporting=same_day_02)
    assert before.analytics_api_due is False
    assert before.reporting_api_due is False

    morning = decide(at_08, analytics=previous_08, reporting=same_day_02)
    assert morning.analytics_api_due is True
    assert morning.reporting_api_due is True

    afternoon = decide(at_14, analytics=at_08, reporting=at_08)
    assert afternoon.analytics_api_due is False
    assert afternoon.reporting_api_due is True
