from __future__ import annotations

from typing import Any, Protocol


class FeishuGateway(Protocol):
    def list_records(self, app_token: str, table_id: str) -> list[dict[str, Any]]: ...

    def count_records(self, app_token: str, table_id: str) -> int: ...

    def batch_create_records(
        self, app_token: str, table_id: str, fields: list[dict[str, Any]]
    ) -> list[dict[str, Any]]: ...

    def batch_update_records(
        self, app_token: str, table_id: str, records: list[dict[str, Any]]
    ) -> list[dict[str, Any]]: ...

    def batch_delete_records(
        self, app_token: str, table_id: str, record_ids: list[str]
    ) -> None: ...


class FeishuTableAdminGateway(FeishuGateway, Protocol):
    """飞书配置中心初始化所需的表结构管理能力。"""

    def list_tables(self, app_token: str) -> list[dict[str, Any]]: ...

    def create_table(
        self,
        app_token: str,
        *,
        name: str,
        fields: list[dict[str, Any]],
        default_view_name: str = "默认视图",
    ) -> dict[str, Any]: ...

    def list_fields(self, app_token: str, table_id: str) -> list[dict[str, Any]]: ...

    def create_field(
        self,
        app_token: str,
        table_id: str,
        *,
        field_name: str,
        field_type: int,
        property: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def update_field(
        self,
        app_token: str,
        table_id: str,
        field_id: str,
        *,
        field_name: str,
        field_type: int,
        property: dict[str, Any] | None = None,
        description: str | None = None,
    ) -> dict[str, Any]: ...
