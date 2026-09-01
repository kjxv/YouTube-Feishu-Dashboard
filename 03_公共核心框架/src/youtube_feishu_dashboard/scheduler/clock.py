from __future__ import annotations

from datetime import datetime
from typing import Protocol

from youtube_feishu_dashboard.core.time import utc_now


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return utc_now()
