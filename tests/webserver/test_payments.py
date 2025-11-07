import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app.config import settings
from app.webserver.payments import create_payment_router


class DummyBot:
    pass


@pytest.fixture(autouse=True)
def reset_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "TRIBUTE_ENABLED", False, raising=False)
    monkeypatch.setattr(settings, "TRIBUTE_API_KEY", None, raising=False)
    monkeypatch.setattr(settings, "TRIBUTE_WEBHOOK_PATH", "/tribute", raising=False)
    monkeypatch.setattr(settings, "MULENPAY_WEBHOOK_PATH", "/mulen", raising=False)
    monkeypatch.setattr(settings, "CRYPTOBOT_ENABLED", False, raising=False)
    monkeypatch.setattr(settings, "CRYPTOBOT_API_TOKEN", None, raising=False)
    monkeypatch.setattr(settings, "CRYPTOBOT_WEBHOOK_PATH", "/cryptobot", raising=False)
    monkeypatch.setattr(settings, "CRYPTOBOT_WEBHOOK_SECRET", None, raising=False)
    monkeypatch.setattr(settings, "YOOKASSA_ENABLED", False, raising=False)
    monkeypatch.setattr(settings, "YOOKASSA_WEBHOOK_PATH", "/yookassa", raising=False)
    monkeypatch.setattr(settings, "YOOKASSA_WEBHOOK_SECRET", None, raising=False)
    monkeypatch.setattr(settings, "YOOKASSA_SHOP_ID", "shop", raising=False)
    monkeypatch.setattr(settings, "YOOKASSA_SECRET_KEY", "key", raising=False)
    monkeypatch.setattr(settings, "WEBHOOK_URL", "http://test", raising=False)


def _get_route(router, path: str, method: str = "POST"):
    for route in router.routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route
    raise AssertionError(f"Route {path} with method {method} not found")


def _build_request(
    path: str,
    body: bytes,
    headers: dict[str, str],
    client_ip: str | None = "185.71.76.1",
) -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "method": "POST",
        "path": path,
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()],
    }

    if client_ip is not None:
        scope["client"] = (client_ip, 12345)

    async def receive() -> dict:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


@pytest.mark.anyio
async def test_tribute_webhook_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "TRIBUTE_ENABLED", True, raising=False)

    process_mock = AsyncMock(return_value={"status": "ok"})

    class StubTributeService:
        def __init__(self, *_args, **_kwargs):
            pass

        async def process_webhook(self, payload: str):  # type: ignore[override]
            return await process_mock(payload)

    class StubTributeAPI:
        @staticmethod
        def verify_webhook_signature(payload: str, signature: str) -> bool:  # noqa: D401 - test stub
            return True

    monkeypatch.setattr("app.webserver.payments.TributeService", StubTributeService)
    monkeypatch.setattr("app.webserver.payments.TributeAPI", StubTributeAPI)

    router = create_payment_router(DummyBot(), SimpleNamespace())
    assert router is not None

    route = _get_route(router, settings.TRIBUTE_WEBHOOK_PATH)
    request = _build_request(
        settings.TRIBUTE_WEBHOOK_PATH,
        body=json.dumps({"event": "payment"}).encode("utf-8"),
        headers={"trbt-signature": "sig"},
    )

    response = await route.endpoint(request)

    assert response.status_code == 200
    assert json.loads(response.body.decode("utf-8"))["status"] == "ok"
    process_mock.assert_awaited_once()


@pytest.mark.anyio
async def test_yookassa_unknown_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "YOOKASSA_ENABLED", True, raising=False)

    service = SimpleNamespace(process_yookassa_webhook=AsyncMock())

    router = create_payment_router(DummyBot(), service)
    assert router is not None

    route = _get_route(router, settings.YOOKASSA_WEBHOOK_PATH)
    request = _build_request(
        settings.YOOKASSA_WEBHOOK_PATH,
        body=json.dumps({"event": "payment.succeeded"}).encode("utf-8"),
        headers={},
        client_ip=None,
    )

    response = await route.endpoint(request)

    assert response.status_code == 403
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["reason"] == "unknown_ip"
    service.process_yookassa_webhook.assert_not_awaited()


@pytest.mark.anyio
async def test_yookassa_forbidden_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "YOOKASSA_ENABLED", True, raising=False)

    service = SimpleNamespace(process_yookassa_webhook=AsyncMock())

    router = create_payment_router(DummyBot(), service)
    assert router is not None

    route = _get_route(router, settings.YOOKASSA_WEBHOOK_PATH)
    request = _build_request(
        settings.YOOKASSA_WEBHOOK_PATH,
        body=json.dumps({"event": "payment.succeeded"}).encode("utf-8"),
        headers={},
        client_ip="203.0.113.10",
    )

    response = await route.endpoint(request)

    assert response.status_code == 403
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["reason"] == "forbidden_ip"
    assert payload["ip"] == "203.0.113.10"
    service.process_yookassa_webhook.assert_not_awaited()


@pytest.mark.anyio
async def test_yookassa_allowed_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "YOOKASSA_ENABLED", True, raising=False)

    async def fake_get_db():
        yield SimpleNamespace()

    monkeypatch.setattr("app.webserver.payments.get_db", fake_get_db)

    process_mock = AsyncMock(return_value=True)
    service = SimpleNamespace(process_yookassa_webhook=process_mock)

    router = create_payment_router(DummyBot(), service)
    assert router is not None

    route = _get_route(router, settings.YOOKASSA_WEBHOOK_PATH)
    request = _build_request(
        settings.YOOKASSA_WEBHOOK_PATH,
        body=json.dumps({"event": "payment.succeeded"}).encode("utf-8"),
        headers={},
        client_ip="185.71.76.10",
    )

    response = await route.endpoint(request)

    assert response.status_code == 200
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["status"] == "ok"
    process_mock.assert_awaited_once()


@pytest.mark.anyio
async def test_cryptobot_missing_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CRYPTOBOT_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "CRYPTOBOT_API_TOKEN", "token", raising=False)
    monkeypatch.setattr(settings, "CRYPTOBOT_WEBHOOK_SECRET", "secret", raising=False)

    router = create_payment_router(DummyBot(), SimpleNamespace())
    assert router is not None

    route = _get_route(router, settings.CRYPTOBOT_WEBHOOK_PATH)
    request = _build_request(
        settings.CRYPTOBOT_WEBHOOK_PATH,
        body=json.dumps({"test": "value"}).encode("utf-8"),
        headers={},
    )

    response = await route.endpoint(request)

    assert response.status_code == 401
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["reason"] == "missing_signature"


@pytest.mark.anyio
async def test_cryptobot_invalid_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CRYPTOBOT_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "CRYPTOBOT_API_TOKEN", "token", raising=False)
    monkeypatch.setattr(settings, "CRYPTOBOT_WEBHOOK_SECRET", "secret", raising=False)

    class StubCryptoBotService:
        @staticmethod
        def verify_webhook_signature(body: str, signature: str) -> bool:  # noqa: D401 - test stub
            return False

    monkeypatch.setattr("app.external.cryptobot.CryptoBotService", StubCryptoBotService)

    router = create_payment_router(DummyBot(), SimpleNamespace())
    assert router is not None

    route = _get_route(router, settings.CRYPTOBOT_WEBHOOK_PATH)
    request = _build_request(
        settings.CRYPTOBOT_WEBHOOK_PATH,
        body=json.dumps({"test": "value"}).encode("utf-8"),
        headers={"Crypto-Pay-API-Signature": "sig"},
    )

    response = await route.endpoint(request)

    assert response.status_code == 401
