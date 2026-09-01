"""飞书行数容量由公共服务统一评估和登记。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from youtube_feishu_dashboard.api.feishu.protocols import FeishuGateway
from youtube_feishu_dashboard.db.models import ArchiveBatch
from youtube_feishu_dashboard.db.repositories import Storage


@dataclass(frozen=True, slots=True)
class ArchiveDecision:
    status: Literal["within_capacity", "archive_required"]
    current_rows: int
    threshold: int
    batch_id: str | None = None


class ArchiveService:
    """V1 安全登记归档批次；复制、核对、删除交给后续显式执行器。"""

    def __init__(
        self,
        *,
        gateway: FeishuGateway,
        storage: Storage,
        app_token: str,
        row_threshold: int = 19000,
    ) -> None:
        self.gateway = gateway
        self.storage = storage
        self.app_token = app_token
        self.row_threshold = row_threshold

    def ensure_capacity(self, *, module_id: str, table_id: str) -> ArchiveDecision:
        current_rows = self.gateway.count_records(self.app_token, table_id)
        if current_rows < self.row_threshold:
            return ArchiveDecision("within_capacity", current_rows, self.row_threshold)
        with self.storage.transaction() as repos:
            batch = repos.archives.find_open(module_id, table_id)
            if batch is None:
                batch = repos.archives.add(
                    ArchiveBatch(
                        module_id=module_id,
                        source_table_id=table_id,
                        status="required",
                        row_count=current_rows,
                        details={
                            "reason": "feishu_row_threshold_reached",
                            "safe_phase": "plan_only_no_delete",
                        },
                    )
                )
            return ArchiveDecision(
                "archive_required",
                current_rows,
                self.row_threshold,
                batch.batch_id,
            )
