from __future__ import annotations

import json

import responses
from youtube_feishu_dashboard.api.feishu.client import FeishuClient


@responses.activate
def test_feishu_client_reuses_token_and_paginates_records() -> None:
    base = "https://open.feishu.cn/open-apis"
    token_url = f"{base}/auth/v3/tenant_access_token/internal"
    records_url = f"{base}/bitable/v1/apps/base-token/tables/table-1/records"
    responses.post(
        token_url,
        json={"code": 0, "tenant_access_token": "tenant-token", "expire": 7200},
    )
    responses.get(
        records_url,
        json={
            "code": 0,
            "data": {
                "items": [{"record_id": "rec-1", "fields": {"名称": "一"}}],
                "has_more": True,
                "page_token": "next-page",
            },
        },
    )
    responses.get(
        records_url,
        json={
            "code": 0,
            "data": {
                "items": [{"record_id": "rec-2", "fields": {"名称": "二"}}],
                "has_more": False,
            },
        },
    )

    client = FeishuClient(app_id="app-id", app_secret="app-secret")
    records = client.list_records("base-token", "table-1")

    assert [item["record_id"] for item in records] == ["rec-1", "rec-2"]
    token_calls = [call for call in responses.calls if call.request.url == token_url]
    assert len(token_calls) == 1
    assert responses.calls[1].request.headers["Authorization"] == "Bearer tenant-token"


@responses.activate
def test_feishu_client_batches_create_records() -> None:
    base = "https://open.feishu.cn/open-apis"
    responses.post(
        f"{base}/auth/v3/tenant_access_token/internal",
        json={"code": 0, "tenant_access_token": "tenant-token", "expire": 7200},
    )
    responses.post(
        f"{base}/bitable/v1/apps/base-token/tables/table-1/records/batch_create",
        json={"code": 0, "data": {"records": [{"record_id": "rec-1"}]}},
    )
    client = FeishuClient(app_id="app-id", app_secret="app-secret")

    created = client.batch_create_records("base-token", "table-1", [{"视频ID": "v1"}])

    assert created == [{"record_id": "rec-1"}]
    request_body = responses.calls[1].request.body
    assert request_body is not None
    body_text = (
        request_body.decode("utf-8") if isinstance(request_body, bytes) else str(request_body)
    )
    assert json.loads(body_text)["records"][0]["fields"]["视频ID"] == "v1"


@responses.activate
def test_feishu_client_manages_tables_and_fields() -> None:
    base = "https://open.feishu.cn/open-apis"
    responses.post(
        f"{base}/auth/v3/tenant_access_token/internal",
        json={"code": 0, "tenant_access_token": "tenant-token", "expire": 7200},
    )
    tables_url = f"{base}/bitable/v1/apps/base-token/tables"
    responses.get(
        tables_url,
        json={
            "code": 0,
            "data": {"items": [{"name": "API字段字典", "table_id": "tbl-1"}]},
        },
    )
    responses.post(
        tables_url,
        json={"code": 0, "data": {"table_id": "tbl-2"}},
    )
    fields_url = f"{base}/bitable/v1/apps/base-token/tables/tbl-1/fields"
    responses.get(
        fields_url,
        json={
            "code": 0,
            "data": {"items": [{"field_name": "标准字段ID", "type": 1}]},
        },
    )
    responses.post(
        fields_url,
        json={
            "code": 0,
            "data": {"field": {"field_name": "启用", "type": 7, "field_id": "fld-1"}},
        },
    )
    responses.put(
        f"{fields_url}/fld-primary",
        json={
            "code": 0,
            "data": {"field": {"field_name": "映射名称", "type": 1, "field_id": "fld-primary"}},
        },
    )
    client = FeishuClient(app_id="app-id", app_secret="app-secret")

    assert client.list_tables("base-token")[0]["table_id"] == "tbl-1"
    created = client.create_table(
        "base-token",
        name="数据项目配置",
        fields=[{"field_name": "配置键", "type": 1}],
    )
    assert created["table_id"] == "tbl-2"
    assert client.list_fields("base-token", "tbl-1")[0]["field_name"] == "标准字段ID"
    field = client.create_field("base-token", "tbl-1", field_name="启用", field_type=7)
    assert field["field_id"] == "fld-1"
    renamed = client.update_field(
        "base-token",
        "tbl-1",
        "fld-primary",
        field_name="映射名称",
        field_type=1,
        description="【关键字段】测试说明",
    )
    assert renamed["field_name"] == "映射名称"
    update_body = responses.calls[5].request.body
    assert update_body is not None
    update_payload = json.loads(
        update_body.decode("utf-8") if isinstance(update_body, bytes) else str(update_body)
    )
    assert update_payload["description"] == {
        "text": "【关键字段】测试说明",
        "disable_sync": False,
    }

    table_body = responses.calls[2].request.body
    assert table_body is not None
    table_payload = json.loads(
        table_body.decode("utf-8") if isinstance(table_body, bytes) else str(table_body)
    )
    assert table_payload["table"]["fields"][0] == {"field_name": "配置键", "type": 1}
