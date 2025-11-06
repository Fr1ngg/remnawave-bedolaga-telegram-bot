import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.config import settings
from app.webserver.telegram import (
    TelegramWebhookProcessor,
    create_telegram_router,
)


@pytest.fixture(autouse=True)
def reset_webhook_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "WEBHOOK_PATH", "/telegram-webhook", raising=False)
    monkeypatch.setattr(settings, "WEBHOOK_SECRET_TOKEN", "", raising=False)
    monkeypatch.setattr(settings, "WEBHOOK_URL", None, raising=False)
    monkeypatch.setattr(settings, "BOT_RUN_MODE", "webhook", raising=False)
    monkeypatch.setattr(settings, "WEBHOOK_MAX_QUEUE_SIZE", 8, raising=False)
    monkeypatch.setattr(settings, "WEBHOOK_WORKERS", 1, raising=False)
    monkeypatch.setattr(settings, "WEBHOOK_ENQUEUE_TIMEOUT", 0.0, raising=False)
    monkeypatch.setattr(settings, "WEBHOOK_WORKER_SHUTDOWN_TIMEOUT", 1.0, raising=False)


def _get_route(router, path: str, method: str = "POST"):
    for route in router.routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route
    raise AssertionError(f"Route {path} with method {method} not found")


def _build_request(path: str, body: bytes, headers: dict[str, str] | None = None) -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "method": "POST",
        "path": path,
        "headers": [
            (k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (headers or {}).items()
        ],
    }

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def _webhook_path() -> str:
    return settings.get_telegram_webhook_path()


@pytest.mark.anyio
async def test_webhook_without_secret() -> None:
    bot = AsyncMock()
    dispatcher = AsyncMock()
    dispatcher.feed_update = AsyncMock()

    sample_update = {
        "update_id": 123,
        "message": {
            "message_id": 10,
            "date": 1715700000,
            "chat": {"id": 456, "type": "private"},
            "text": "ping",
        },
    }

    router = create_telegram_router(bot, dispatcher)
    path = _webhook_path()
    route = _get_route(router, path)
    request = _build_request(path, json.dumps(sample_update).encode("utf-8"))

    response = await route.endpoint(request)

    assert response.status_code == 200
    dispatcher.feed_update.assert_awaited_once()
    args, _kwargs = dispatcher.feed_update.await_args
    assert args[0] is bot


@pytest.mark.anyio
async def test_webhook_with_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = AsyncMock()
    dispatcher = AsyncMock()
    dispatcher.feed_update = AsyncMock()

    monkeypatch.setattr(settings, "WEBHOOK_SECRET_TOKEN", "super-secret", raising=False)

    sample_update = {
        "update_id": 321,
        "message": {
            "message_id": 20,
            "date": 1715700000,
            "chat": {"id": 789, "type": "private"},
            "text": "pong",
        },
    }

    router = create_telegram_router(bot, dispatcher)
    path = _webhook_path()
    route = _get_route(router, path)
    request = _build_request(
        path,
        json.dumps(sample_update).encode("utf-8"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "super-secret"},
    )

    response = await route.endpoint(request)

    assert response.status_code == 200
    dispatcher.feed_update.assert_awaited_once()


@pytest.mark.anyio
async def test_webhook_secret_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = AsyncMock()
    dispatcher = AsyncMock()
    dispatcher.feed_update = AsyncMock()

    monkeypatch.setattr(settings, "WEBHOOK_SECRET_TOKEN", "expected", raising=False)

    router = create_telegram_router(bot, dispatcher)
    path = _webhook_path()
    route = _get_route(router, path)
    request = _build_request(
        path,
        json.dumps({"update_id": 1}).encode("utf-8"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )

    with pytest.raises(HTTPException) as exc:
        await route.endpoint(request)

    assert exc.value.status_code == 401
    dispatcher.feed_update.assert_not_called()


@pytest.mark.anyio
async def test_webhook_invalid_payload() -> None:
    bot = AsyncMock()
    dispatcher = AsyncMock()
    dispatcher.feed_update = AsyncMock()

    router = create_telegram_router(bot, dispatcher)
    path = _webhook_path()
    route = _get_route(router, path)
    request = _build_request(path, b"not-json")

    with pytest.raises(HTTPException) as exc:
        await route.endpoint(request)

    assert exc.value.status_code == 400
    dispatcher.feed_update.assert_not_called()


@pytest.mark.anyio
async def test_webhook_invalid_content_type() -> None:
    bot = AsyncMock()
    dispatcher = AsyncMock()
    dispatcher.feed_update = AsyncMock()

    sample_update = {
        "update_id": 123,
        "message": {
            "message_id": 10,
            "date": 1715700000,
            "chat": {"id": 456, "type": "private"},
            "text": "ping",
        },
    }

    router = create_telegram_router(bot, dispatcher)
    path = _webhook_path()
    route = _get_route(router, path)
    request = _build_request(
        path,
        json.dumps(sample_update).encode("utf-8"),
        headers={"Content-Type": "text/plain"},
    )

    with pytest.raises(HTTPException) as exc:
        await route.endpoint(request)

    assert exc.value.status_code == 415
    dispatcher.feed_update.assert_not_called()


@pytest.mark.anyio
async def test_webhook_uses_processor() -> None:
    bot = AsyncMock()
    dispatcher = AsyncMock()
    dispatcher.feed_update = AsyncMock()

    processor = TelegramWebhookProcessor(
        bot=bot,
        dispatcher=dispatcher,
        queue_maxsize=1,
        worker_count=0,
        enqueue_timeout=0.0,
        shutdown_timeout=1.0,
    )
    await processor.start()

    sample_update = {
        "update_id": 999,
        "message": {
            "message_id": 77,
            "date": 1715700000,
            "chat": {"id": 111, "type": "private"},
            "text": "processor",
        },
    }

    router = create_telegram_router(bot, dispatcher, processor=processor)
    path = _webhook_path()
    route = _get_route(router, path)
    request = _build_request(path, json.dumps(sample_update).encode("utf-8"))

    response = await route.endpoint(request)

    assert response.status_code == 200
    dispatcher.feed_update.assert_not_awaited()
    assert processor.is_running

    await processor.stop()


@pytest.mark.anyio
async def test_webhook_processor_overloaded() -> None:
    bot = AsyncMock()
    dispatcher = AsyncMock()
    dispatcher.feed_update = AsyncMock()

    processor = TelegramWebhookProcessor(
        bot=bot,
        dispatcher=dispatcher,
        queue_maxsize=1,
        worker_count=0,
        enqueue_timeout=0.0,
        shutdown_timeout=1.0,
    )
    await processor.start()

    router = create_telegram_router(bot, dispatcher, processor=processor)
    path = _webhook_path()
    route = _get_route(router, path)

    request_payload = json.dumps({"update_id": 1}).encode("utf-8")
    request = _build_request(path, request_payload)
    await route.endpoint(request)

    with pytest.raises(HTTPException) as exc:
        await route.endpoint(request)

    assert exc.value.status_code == 503
    assert exc.value.detail == "webhook_queue_full"
    dispatcher.feed_update.assert_not_called()

    await processor.stop()


@pytest.mark.anyio
async def test_webhook_processor_not_running() -> None:
    bot = AsyncMock()
    dispatcher = AsyncMock()
    dispatcher.feed_update = AsyncMock()

    processor = TelegramWebhookProcessor(
        bot=bot,
        dispatcher=dispatcher,
        queue_maxsize=1,
        worker_count=1,
        enqueue_timeout=0.0,
        shutdown_timeout=1.0,
    )

    router = create_telegram_router(bot, dispatcher, processor=processor)
    path = _webhook_path()
    route = _get_route(router, path)
    request = _build_request(path, json.dumps({"update_id": 5}).encode("utf-8"))

    with pytest.raises(HTTPException) as exc:
        await route.endpoint(request)

    assert exc.value.status_code == 503
    assert exc.value.detail == "webhook_processor_unavailable"
    dispatcher.feed_update.assert_not_called()


@pytest.mark.anyio
async def test_webhook_path_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = AsyncMock()
    dispatcher = AsyncMock()
    dispatcher.feed_update = AsyncMock()

    monkeypatch.setattr(settings, "WEBHOOK_PATH", "  telegram/webhook ", raising=False)

    router = create_telegram_router(bot, dispatcher)
    normalized_path = settings.get_telegram_webhook_path()

    assert normalized_path == "/telegram/webhook"
    route = _get_route(router, normalized_path)

    request = _build_request(normalized_path, json.dumps({"update_id": 7}).encode("utf-8"))
    response = await route.endpoint(request)

    assert response.status_code == 200
    dispatcher.feed_update.assert_awaited_once()


@pytest.mark.anyio
async def test_health_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = AsyncMock()
    dispatcher = AsyncMock()
    dispatcher.feed_update = AsyncMock()

    monkeypatch.setattr(settings, "WEBHOOK_URL", "https://example.com", raising=False)
    monkeypatch.setattr(settings, "WEBHOOK_PATH", "/custom", raising=False)
    monkeypatch.setattr(settings, "WEBHOOK_MAX_QUEUE_SIZE", 42, raising=False)
    monkeypatch.setattr(settings, "WEBHOOK_WORKERS", 2, raising=False)

    router = create_telegram_router(bot, dispatcher)
    route = _get_route(router, "/health/telegram-webhook", method="GET")

    response = await route.endpoint()

    assert response.status_code == 200
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["status"] == "ok"
    assert payload["mode"] == settings.get_bot_run_mode()
    assert payload["path"] == "/custom"
    assert payload["webhook_configured"] is True
    assert payload["queue_maxsize"] == 42
    assert payload["workers"] == 2
