import base64
import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from app.config import settings
from app.external.yookassa_webhook import (
    create_yookassa_webhook_app,
    resolve_yookassa_ip,
)


ALLOWED_IP = "185.71.76.10"


class DummyDB:
    async def close(self) -> None:  # pragma: no cover - simple stub
        pass


def _generate_signature(body: str, secret: str) -> str:
    payment_id = "test-payment"
    timestamp = "2024-01-01T00:00:00.000Z"
    payload = f"{payment_id}.{timestamp}.{body}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return f"v1 {payment_id} {timestamp} {base64.b64encode(digest).decode('utf-8')}"


@pytest.fixture(autouse=True)
def configure_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "YOOKASSA_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "YOOKASSA_SHOP_ID", "shop", raising=False)
    monkeypatch.setattr(settings, "YOOKASSA_SECRET_KEY", "key", raising=False)
    monkeypatch.setattr(settings, "YOOKASSA_WEBHOOK_SECRET", "secret", raising=False)
    monkeypatch.setattr(settings, "YOOKASSA_WEBHOOK_PATH", "/yookassa-webhook", raising=False)


def _build_headers(**overrides: str) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Forwarded-For": ALLOWED_IP,
    }
    headers.update(overrides)
    return headers


@pytest.mark.parametrize(
    ("remote", "expected"),
    (
        ("185.71.76.10", "185.71.76.10"),
        ("8.8.8.8", "8.8.8.8"),
        ("10.0.0.5", "185.71.76.10"),
        (None, "185.71.76.10"),
    ),
)
def test_resolve_yookassa_ip_trust_rules(remote: str | None, expected: str) -> None:
    candidates = [ALLOWED_IP]
    ip_object = resolve_yookassa_ip(candidates, remote=remote)

    assert ip_object is not None
    assert str(ip_object) == expected


def test_resolve_yookassa_ip_prefers_last_forwarded_candidate() -> None:
    candidates = ["185.71.76.10", "8.8.8.8"]

    ip_object = resolve_yookassa_ip(candidates, remote="10.0.0.5")

    assert ip_object is not None
    assert str(ip_object) == "8.8.8.8"


def test_resolve_yookassa_ip_accepts_allowed_last_forwarded_candidate() -> None:
    candidates = ["8.8.8.8", ALLOWED_IP]

    ip_object = resolve_yookassa_ip(candidates, remote="10.0.0.5")

    assert ip_object is not None
    assert str(ip_object) == ALLOWED_IP


def test_resolve_yookassa_ip_returns_none_when_no_candidates() -> None:
    assert resolve_yookassa_ip([], remote=None) is None


async def _post_webhook(client: TestClient, payload: dict, **headers: str) -> web.Response:
    body = json.dumps(payload, ensure_ascii=False)
    return await client.post(
        settings.YOOKASSA_WEBHOOK_PATH,
        data=body.encode("utf-8"),
        headers=_build_headers(**headers),
    )


def _patch_get_db(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_db():
        yield DummyDB()

    monkeypatch.setattr("app.external.yookassa_webhook.get_db", fake_get_db)


@pytest.mark.asyncio
async def test_handle_webhook_missing_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get_db(monkeypatch)

    service = SimpleNamespace(process_yookassa_webhook=AsyncMock())

    app = create_yookassa_webhook_app(service)
    async with TestClient(TestServer(app)) as client:
        response = await _post_webhook(client, {"event": "payment.succeeded"})
        status = response.status
        body = await response.text()

    assert status == 401
    assert body == "Missing signature"
    service.process_yookassa_webhook.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_webhook_invalid_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get_db(monkeypatch)

    service = SimpleNamespace(process_yookassa_webhook=AsyncMock())

    app = create_yookassa_webhook_app(service)
    async with TestClient(TestServer(app)) as client:
        response = await _post_webhook(
            client,
            {"event": "payment.succeeded"},
            Signature="v1 test-payment 2024-01-01T00:00:00.000Z invalid",
        )
        status = response.status
        body = await response.text()

    assert status == 401
    assert body == "Invalid signature"
    service.process_yookassa_webhook.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_webhook_valid_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get_db(monkeypatch)

    process_mock = AsyncMock(return_value=True)
    service = SimpleNamespace(process_yookassa_webhook=process_mock)

    app = create_yookassa_webhook_app(service)
    async with TestClient(TestServer(app)) as client:
        payload = {"event": "payment.succeeded"}
        body = json.dumps(payload, ensure_ascii=False)
        signature = _generate_signature(body, settings.YOOKASSA_WEBHOOK_SECRET)
        response = await client.post(
            settings.YOOKASSA_WEBHOOK_PATH,
            data=body.encode("utf-8"),
            headers=_build_headers(Signature=signature),
        )
        status = response.status
        text = await response.text()

    assert status == 200
    assert text == "OK"
    process_mock.assert_awaited_once()
