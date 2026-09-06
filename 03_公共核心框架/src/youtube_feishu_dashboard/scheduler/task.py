from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    base_delay_seconds: int = 60
    max_delay_seconds: int = 1800
    non_retryable_delay_seconds: int = 3600

    def delay_seconds(self, failure_count: int, *, retryable: bool) -> int:
        if not retryable:
            return self.non_retryable_delay_seconds
        exponent = max(0, failure_count - 1)
        delay = self.base_delay_seconds << exponent
        return min(self.max_delay_seconds, delay)


@dataclass(frozen=True, slots=True)
class TaskContext:
    now: datetime
    scheduled_for: datetime
    attempt: int
    dry_run: bool = False
    force: bool = True
    cursor: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class TaskResult:
    counts: dict[str, int] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    cursor: dict[str, Any] | None = None


class ScheduledTask(Protocol):
    task_id: str
    module_id: str
    interval_seconds: int
    lock_ttl_seconds: int
    retry_policy: RetryPolicy

    def execute(self, context: TaskContext) -> TaskResult: ...
