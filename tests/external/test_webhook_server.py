"""Тестирование хендлеров WebhookServer без запуска реального сервера."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Tuple
import sys
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import make_mocked_request
from aiohttp import web

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import settings  # noqa: E402
from app.external.webhook_server import WebhookServer  # noqa: E402


class DummyBot:
    async def send_message(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover - уведомления не проверяем
        return None


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def webhook_server(monkeypatch: pytest.MonkeyPatch) -> Tuple[WebhookServer, AsyncMock, AsyncMock]:
    monkeypatch.setattr(settings, "TRIBUTE_WEBHOOK_PATH", "/tribute", raising=False)
    monkeypatch.setattr(settings, "MULENPAY_WEBHOOK_PATH", "/mulen", raising=False)
    monkeypatch.setattr(settings, "CRYPTOBOT_WEBHOOK_PATH", "/cryptobot", raising=False)
    monkeypatch.setattr(settings, "MULENPAY_SECRET_KEY", "mulen-secret", raising=False)
    monkeypatch.setattr(settings, "CRYPTOBOT_WEBHOOK_SECRET", "", raising=False)
    monkeypatch.setattr(type(settings), "is_mulenpay_enabled", lambda self: True, raising=False)
    monkeypatch.setattr(type(settings), "is_cryptobot_enabled", lambda self: True, raising=False)

    server = WebhookServer(DummyBot())

    tribute_mock = AsyncMock()
    tribute_mock.process_webhook = AsyncMock(return_value={"status": "ok"})
    server.tribute_service = tribute_mock

    payment_mock = AsyncMock()
    payment_mock.process_mulenpay_callback = AsyncMock(return_value=True)
    payment_mock.process_cryptobot_webhook = AsyncMock(return_value=True)
    monkeypatch.setattr("app.external.webhook_server.PaymentService", lambda *args, **kwargs: payment_mock)
    monkeypatch.setattr("app.services.payment_service.PaymentService", lambda *args, **kwargs: payment_mock)

    # В актуальной реализации WebhookServer инициализирует PaymentService в __init__,
    # поэтому принудительно подменяем инстанс на mock, чтобы избежать обращения к
    # реальному сервису и зависимости от интерфейса настоящей БД в тестах.
    server.payment_service = payment_mock

    server._verify_mulenpay_signature = lambda request, raw: True  # type: ignore[attr-defined]

    class DummyDB:
        async def commit(self) -> None:  # pragma: no cover - не проверяем транзакции
            return None

    async def fake_get_db():
        yield DummyDB()

    monkeypatch.setattr("app.external.webhook_server.get_db", fake_get_db)

    class DummySessionManager:
        def __init__(self) -> None:
            self.session = DummyDB()

        async def __aenter__(self) -> DummyDB:
            return self.session

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setattr("app.database.database.AsyncSessionLocal", lambda: DummySessionManager())

    return server, tribute_mock, payment_mock


def _mock_request(method: str, path: str, body: dict[str, Any], headers: dict[str, str] | None = None) -> AsyncMock:
    request = AsyncMock(spec=web.Request)
    request.method = method
    request.path = path
    request.headers = headers or {}
    request.read.return_value = json.dumps(body).encode("utf-8")
    return request


@pytest.mark.anyio("asyncio")
async def test_health_endpoint(webhook_server: Tuple[WebhookServer, AsyncMock, AsyncMock]) -> None:
    server, _, _ = webhook_server
    request = make_mocked_request("GET", "/health")
    response = await server._health_check(request)
    assert response.status == 200
    data = json.loads(response.text)
    assert data["status"] == "ok"
    assert data["service"] == "payment-webhooks"


@pytest.mark.anyio("asyncio")
async def test_tribute_webhook_success(monkeypatch: pytest.MonkeyPatch, webhook_server: Tuple[WebhookServer, AsyncMock, AsyncMock]) -> None:
    server, tribute_mock, _ = webhook_server
    monkeypatch.setattr(settings, "TRIBUTE_API_KEY", "key", raising=False)

    class FakeTributeAPI:
        def verify_webhook_signature(self, payload: str, signature: str) -> bool:
            return True

    monkeypatch.setattr("app.external.tribute.TributeService", FakeTributeAPI)

    request = _mock_request("POST", "/tribute", {"event_type": "payment", "status": "paid"}, headers={"trbt-signature": "sig"})
    response = await server._tribute_webhook_handler(request)
    assert response.status == 200
    assert tribute_mock.process_webhook.await_count == 1


@pytest.mark.anyio("asyncio")
async def test_mulenpay_webhook_success(webhook_server: Tuple[WebhookServer, AsyncMock, AsyncMock]) -> None:
    server, _, payment_mock = webhook_server
    request = _mock_request("POST", "/mulen", {"uuid": "uuid", "payment_status": "success"})
    response = await server._mulenpay_webhook_handler(request)
    assert response.status == 200
    payment_mock.process_mulenpay_callback.assert_awaited_once()


@pytest.mark.anyio("asyncio")
async def test_cryptobot_webhook_success(webhook_server: Tuple[WebhookServer, AsyncMock, AsyncMock]) -> None:
    server, _, payment_mock = webhook_server
    request = _mock_request(
        "POST",
        "/cryptobot",
        {"update_type": "invoice_paid", "payload": {"invoice_id": 1}},
    )
    response = await server._cryptobot_webhook_handler(request)
    assert response.status == 200
    payment_mock.process_cryptobot_webhook.assert_awaited_once()
