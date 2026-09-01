from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import requests

from youtube_feishu_dashboard.core.errors import AuthenticationError, ExternalServiceError


class FeishuClient:
    """企业自建应用访问多维表格的公共 HTTP 客户端。"""

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        api_base_url: str = "https://open.feishu.cn/open-apis",
        timeout_seconds: float = 30,
        session: requests.Session | None = None,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.api_base_url = api_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self._tenant_token: str | None = None
        self._token_valid_until = 0.0

    def list_records(self, app_token: str, table_id: str) -> list[dict[str, Any]]:
        return list(self.iter_records(app_token, table_id))

    def list_tables(self, app_token: str) -> list[dict[str, Any]]:
        """列出一个多维表格 Base 中的全部数据表。"""
        return list(self._iter_items(f"/bitable/v1/apps/{app_token}/tables", page_size=100))

    def create_table(
        self,
        app_token: str,
        *,
        name: str,
        fields: list[dict[str, Any]],
        default_view_name: str = "默认视图",
    ) -> dict[str, Any]:
        """创建数据表；fields 的第一项作为主字段。"""
        payload = self._request(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables",
            json={
                "table": {
                    "name": name,
                    "default_view_name": default_view_name,
                    "fields": fields,
                }
            },
        )
        return dict(payload.get("data", {}))

    def list_fields(self, app_token: str, table_id: str) -> list[dict[str, Any]]:
        """列出数据表中的全部字段。"""
        path = f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
        return list(self._iter_items(path, page_size=100))

    def create_field(
        self,
        app_token: str,
        table_id: str,
        *,
        field_name: str,
        field_type: int,
        property: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """向已有数据表追加字段；不会删除或覆盖现有字段。"""
        body: dict[str, Any] = {"field_name": field_name, "type": field_type}
        if property:
            body["property"] = property
        payload = self._request(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            json=body,
        )
        data = payload.get("data", {})
        field = data.get("field") if isinstance(data, dict) else None
        return dict(field or data)

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
    ) -> dict[str, Any]:
        """更新字段名称或属性；配置中心版本迁移时使用。"""
        body: dict[str, Any] = {"field_name": field_name, "type": field_type}
        if property:
            body["property"] = property
        if description is not None:
            body["description"] = {"text": description, "disable_sync": False}
        payload = self._request(
            "PUT",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{field_id}",
            json=body,
        )
        data = payload.get("data", {})
        field = data.get("field") if isinstance(data, dict) else None
        return dict(field or data)

    def iter_records(
        self, app_token: str, table_id: str, *, page_size: int = 500
    ) -> Iterator[dict[str, Any]]:
        page_token: str | None = None
        while True:
            parameters: dict[str, Any] = {"page_size": page_size}
            if page_token:
                parameters["page_token"] = page_token
            payload = self._request(
                "GET",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
                params=parameters,
            )
            data = payload.get("data", {})
            yield from data.get("items") or []
            page_token = data.get("page_token")
            if not data.get("has_more") or not page_token:
                break

    def count_records(self, app_token: str, table_id: str) -> int:
        payload = self._request(
            "GET",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
            params={"page_size": 1},
        )
        data = payload.get("data", {})
        if "total" in data:
            return int(data["total"])
        return len(self.list_records(app_token, table_id))

    def batch_create_records(
        self, app_token: str, table_id: str, fields: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        created: list[dict[str, Any]] = []
        for batch in self._batches(fields):
            payload = self._request(
                "POST",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
                json={"records": [{"fields": item} for item in batch]},
            )
            created.extend(payload.get("data", {}).get("records") or [])
        return created

    def batch_update_records(
        self, app_token: str, table_id: str, records: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        updated: list[dict[str, Any]] = []
        for batch in self._batches(records):
            payload = self._request(
                "POST",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_update",
                json={"records": batch},
            )
            updated.extend(payload.get("data", {}).get("records") or [])
        return updated

    def batch_delete_records(self, app_token: str, table_id: str, record_ids: list[str]) -> None:
        for batch in self._batches(record_ids):
            self._request(
                "POST",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete",
                json={"records": batch},
            )

    def check_connection(self, app_token: str) -> dict[str, Any]:
        payload = self._request("GET", f"/bitable/v1/apps/{app_token}")
        app = payload.get("data", {}).get("app", {})
        return {"app_token": app_token, "name": app.get("name"), "ok": True}

    def _iter_items(self, path: str, *, page_size: int = 100) -> Iterator[dict[str, Any]]:
        page_token: str | None = None
        while True:
            parameters: dict[str, Any] = {"page_size": page_size}
            if page_token:
                parameters["page_token"] = page_token
            payload = self._request("GET", path, params=parameters)
            data = payload.get("data", {})
            yield from data.get("items") or []
            page_token = data.get("page_token")
            if not data.get("has_more") or not page_token:
                break

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {self._get_tenant_token()}"
        try:
            response = self.session.request(
                method,
                f"{self.api_base_url}{path}",
                headers=headers,
                timeout=self.timeout_seconds,
                **kwargs,
            )
            response.raise_for_status()
            raw_payload = response.json()
        except requests.RequestException as exc:
            status = getattr(exc.response, "status_code", None)
            retryable = status in {None, 408, 429, 500, 502, 503, 504}
            raise ExternalServiceError(
                f"飞书 API 请求失败（HTTP {status or 'unknown'}）：{exc}", retryable=retryable
            ) from exc
        except ValueError as exc:
            raise ExternalServiceError("飞书 API 返回非 JSON 数据。") from exc
        if not isinstance(raw_payload, dict):
            raise ExternalServiceError("飞书 API 返回的数据不是 JSON 对象。")
        payload: dict[str, Any] = raw_payload
        if int(payload.get("code", 0)) != 0:
            code = payload.get("code")
            message = payload.get("msg") or payload.get("message") or "unknown error"
            retryable = code in {99991400, 99991401, 99991402}
            raise ExternalServiceError(
                f"飞书 API 返回错误 code={code}：{message}", retryable=retryable
            )
        return payload

    def _get_tenant_token(self) -> str:
        now = time.monotonic()
        if self._tenant_token and now < self._token_valid_until:
            return self._tenant_token
        try:
            response = self.session.post(
                f"{self.api_base_url}/auth/v3/tenant_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self.app_secret},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            raw_payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise AuthenticationError(f"获取飞书 tenant_access_token 失败：{exc}") from exc
        if not isinstance(raw_payload, dict):
            raise AuthenticationError("飞书 Token 接口返回的数据不是 JSON 对象。")
        payload: dict[str, Any] = raw_payload
        if int(payload.get("code", 0)) != 0 or not payload.get("tenant_access_token"):
            raise AuthenticationError(
                f"飞书应用凭证无效：code={payload.get('code')}，msg={payload.get('msg')}"
            )
        self._tenant_token = str(payload["tenant_access_token"])
        expire_seconds = max(60, int(payload.get("expire", 7200)) - 300)
        self._token_valid_until = now + expire_seconds
        return self._tenant_token

    @staticmethod
    def _batches(items: list[Any], size: int = 500) -> Iterator[list[Any]]:
        for start in range(0, len(items), size):
            yield items[start : start + size]
