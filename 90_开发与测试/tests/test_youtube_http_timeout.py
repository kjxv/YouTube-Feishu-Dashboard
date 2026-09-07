from __future__ import annotations

from typing import Any, cast

import pytest
from google.oauth2.credentials import Credentials
from httplib2 import HttpLib2Error
from youtube_feishu_dashboard.api.youtube import auth
from youtube_feishu_dashboard.api.youtube.base import GoogleApiClientBase
from youtube_feishu_dashboard.core.errors import ExternalServiceError


def test_authorized_http_uses_explicit_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_http(*, timeout: float) -> object:
        captured["timeout"] = timeout
        return "transport"

    def fake_authorized_http(credentials: Credentials, *, http: object) -> object:
        captured["credentials"] = credentials
        captured["http"] = http
        return "authorized"

    credentials = cast(Credentials, object())
    monkeypatch.setattr(auth, "Http", fake_http)
    monkeypatch.setattr(auth, "AuthorizedHttp", fake_authorized_http)

    result = auth.build_authorized_http(credentials, timeout_seconds=12.5)

    assert result == "authorized"
    assert captured == {
        "timeout": 12.5,
        "credentials": credentials,
        "http": "transport",
    }


def test_authorized_http_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="必须大于 0"):
        auth.build_authorized_http(cast(Credentials, object()), timeout_seconds=0)


def test_google_network_timeout_is_wrapped_as_retryable_error() -> None:
    class TimedOutRequest:
        @staticmethod
        def execute() -> dict[str, Any]:
            raise HttpLib2Error("timed out")

    with pytest.raises(ExternalServiceError) as captured:
        GoogleApiClientBase.execute(TimedOutRequest())

    assert captured.value.retryable is True
    assert "超时或中断" in str(captured.value)
