from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from youtube_feishu_dashboard.core.time import as_utc


@dataclass(frozen=True, slots=True)
class VideoCadenceState:
    published_at: datetime
    last_data_at: datetime | None = None
    last_analytics_at: datetime | None = None
    last_reporting_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class VideoCadenceDecision:
    data_api_due: bool
    analytics_api_due: bool
    reporting_api_due: bool
    data_reason: str

    @property
    def any_due(self) -> bool:
        return self.data_api_due or self.analytics_api_due or self.reporting_api_due


@dataclass(frozen=True, slots=True)
class TrackingCadencePolicy:
    """按视频发布时间决定三个数据源是否到期。

    Windows 只需在每个整点唤醒一次。这里负责避免重复采集，并在电脑离线后
    只做一次真实补采，不为错过的小时伪造历史记录。
    """

    timezone: str = "Asia/Shanghai"
    hourly_tracking_hours: int = 72
    daily_collection_hour: int = 8
    tracking_days: int = 28
    reporting_interval_hours: int = 6
    milestone_days: tuple[int, ...] = (7, 14, 28)

    def decide(self, state: VideoCadenceState, now: datetime) -> VideoCadenceDecision:
        current = as_utc(now)
        published = as_utc(state.published_at)
        if current < published:
            return VideoCadenceDecision(False, False, False, "not_published")

        end_at = published + timedelta(days=self.tracking_days)
        data_due, data_reason = self._data_due(
            published_at=published,
            last_data_at=state.last_data_at,
            now=current,
            end_at=end_at,
        )
        source_window_open = current <= end_at or data_due
        analytics_due = source_window_open and self._daily_due(
            state.last_analytics_at,
            current,
            published,
        )
        reporting_due = source_window_open and self._periodic_due(
            state.last_reporting_at,
            current,
            published,
            interval_hours=self.reporting_interval_hours,
        )
        return VideoCadenceDecision(
            data_api_due=data_due,
            analytics_api_due=analytics_due,
            reporting_api_due=reporting_due,
            data_reason=data_reason,
        )

    def _data_due(
        self,
        *,
        published_at: datetime,
        last_data_at: datetime | None,
        now: datetime,
        end_at: datetime,
    ) -> tuple[bool, str]:
        age = now - published_at
        if age <= timedelta(hours=self.hourly_tracking_hours):
            current_hour = self._local(now).replace(minute=0, second=0, microsecond=0)
            if last_data_at is None or self._local(last_data_at) < current_hour:
                return True, "hourly_first_72_hours"
            return False, "hour_already_collected"

        missed_milestones = [
            published_at + timedelta(days=day)
            for day in self.milestone_days
            if day <= self.tracking_days
            and published_at + timedelta(days=day) <= now
            and (last_data_at is None or as_utc(last_data_at) < published_at + timedelta(days=day))
        ]
        if missed_milestones and (now <= end_at or last_data_at is not None):
            return True, f"milestone_day_{self._milestone_day(published_at, missed_milestones[-1])}"

        if now <= end_at and self._daily_due(last_data_at, now, published_at):
            return True, "daily_08_beijing"

        if now > end_at and last_data_at is not None and as_utc(last_data_at) < end_at:
            return True, f"milestone_day_{self.tracking_days}_after_resume"
        return False, "tracking_complete" if now > end_at else "daily_already_collected"

    def _daily_due(
        self,
        last_at: datetime | None,
        now: datetime,
        published_at: datetime,
    ) -> bool:
        local_now = self._local(now)
        target = local_now.replace(
            hour=self.daily_collection_hour,
            minute=0,
            second=0,
            microsecond=0,
        )
        if target > local_now:
            target -= timedelta(days=1)
        published_local = self._local(published_at)
        if last_at is None:
            return True
        return target >= published_local and self._local(last_at) < target

    def _periodic_due(
        self,
        last_at: datetime | None,
        now: datetime,
        published_at: datetime,
        *,
        interval_hours: int,
    ) -> bool:
        if last_at is None:
            return True
        local_now = self._local(now)
        anchor = local_now.replace(
            hour=self.daily_collection_hour,
            minute=0,
            second=0,
            microsecond=0,
        )
        interval = timedelta(hours=interval_hours)
        while anchor > local_now:
            anchor -= interval
        elapsed = local_now - anchor
        anchor += interval * int(elapsed // interval)
        return anchor >= self._local(published_at) and self._local(last_at) < anchor

    def _local(self, value: datetime) -> datetime:
        return as_utc(value).astimezone(ZoneInfo(self.timezone))

    @staticmethod
    def _milestone_day(published_at: datetime, milestone: datetime) -> int:
        return int((milestone - published_at).total_seconds() // 86400)
