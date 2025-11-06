from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from aiogram import Bot, Dispatcher
from aiogram.types import Update

from app.config import settings


logger = logging.getLogger(__name__)


class TelegramWebhookProcessorError(RuntimeError):
    """Базовое исключение очереди Telegram webhook."""


class TelegramWebhookProcessorNotRunningError(TelegramWebhookProcessorError):
    """Очередь ещё не запущена или уже остановлена."""


class TelegramWebhookOverloadedError(TelegramWebhookProcessorError):
    """Очередь переполнена и не успевает обрабатывать новые обновления."""


class TelegramWebhookProcessor:
    """Асинхронная очередь обработки Telegram webhook-ов."""

    def __init__(
        self,
        *,
        bot: Bot,
        dispatcher: Dispatcher,
        queue_maxsize: int,
        worker_count: int,
        enqueue_timeout: float,
        shutdown_timeout: float,
    ) -> None:
        self._bot = bot
        self._dispatcher = dispatcher
        self._queue_maxsize = max(1, queue_maxsize)
        self._worker_count = max(0, worker_count)
        self._enqueue_timeout = max(0.0, enqueue_timeout)
        self._shutdown_timeout = max(1.0, shutdown_timeout)
        self._queue: asyncio.Queue[Update | object] = asyncio.Queue(maxsize=self._queue_maxsize)
        self._workers: list[asyncio.Task[None]] = []
        self._running = False
        self._stop_sentinel: object = object()
        self._lifecycle_lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._running:
                return

            self._running = True
            self._queue = asyncio.Queue(maxsize=self._queue_maxsize)
            self._workers.clear()

            for index in range(self._worker_count):
                task = asyncio.create_task(
                    self._worker_loop(index),
                    name=f"telegram-webhook-worker-{index}",
                )
                self._workers.append(task)

            if self._worker_count:
                logger.info(
                    "🚀 Telegram webhook processor запущен: %s воркеров, очередь %s", 
                    self._worker_count,
                    self._queue_maxsize,
                )
            else:
                logger.warning(
                    "Telegram webhook processor запущен без воркеров — обновления не будут обрабатываться"
                )

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            if not self._running:
                return

            self._running = False

            if self._worker_count > 0:
                try:
                    await asyncio.wait_for(self._queue.join(), timeout=self._shutdown_timeout)
                except asyncio.TimeoutError:
                    logger.warning(
                        "⏱️ Не удалось дождаться завершения очереди Telegram webhook за %s секунд",
                        self._shutdown_timeout,
                    )
            else:
                drained = 0
                while not self._queue.empty():
                    try:
                        self._queue.get_nowait()
                    except asyncio.QueueEmpty:  # pragma: no cover - гонка состояния
                        break
                    else:
                        drained += 1
                        self._queue.task_done()
                if drained:
                    logger.warning(
                        "Очередь Telegram webhook остановлена без воркеров, потеряно %s обновлений",
                        drained,
                    )

            for _ in range(len(self._workers)):
                try:
                    self._queue.put_nowait(self._stop_sentinel)
                except asyncio.QueueFull:
                    # Очередь переполнена, подождём пока освободится место
                    await self._queue.put(self._stop_sentinel)

            if self._workers:
                await asyncio.gather(*self._workers, return_exceptions=True)
            self._workers.clear()
            logger.info("🛑 Telegram webhook processor остановлен")

    async def enqueue(self, update: Update) -> None:
        if not self._running:
            raise TelegramWebhookProcessorNotRunningError

        try:
            if self._enqueue_timeout <= 0:
                self._queue.put_nowait(update)
            else:
                await asyncio.wait_for(self._queue.put(update), timeout=self._enqueue_timeout)
        except asyncio.QueueFull as error:  # pragma: no cover - защитный сценарий
            raise TelegramWebhookOverloadedError from error
        except asyncio.TimeoutError as error:
            raise TelegramWebhookOverloadedError from error

    async def wait_until_drained(self, timeout: float | None = None) -> None:
        if not self._running or self._worker_count == 0:
            return
        if timeout is None:
            await self._queue.join()
            return
        await asyncio.wait_for(self._queue.join(), timeout=timeout)

    async def _worker_loop(self, worker_id: int) -> None:
        try:
            while True:
                try:
                    item = await self._queue.get()
                except asyncio.CancelledError:  # pragma: no cover - остановка приложения
                    logger.debug("Worker %s cancelled", worker_id)
                    raise

                if item is self._stop_sentinel:
                    self._queue.task_done()
                    break

                update = item
                try:
                    await self._dispatcher.feed_update(self._bot, update)  # type: ignore[arg-type]
                except asyncio.CancelledError:  # pragma: no cover - остановка приложения
                    logger.debug("Worker %s cancelled during processing", worker_id)
                    raise
                except Exception as error:  # pragma: no cover - логируем сбой обработчика
                    logger.exception("Ошибка обработки Telegram update в worker %s: %s", worker_id, error)
                finally:
                    self._queue.task_done()
        finally:
            logger.debug("Worker %s завершён", worker_id)


async def _dispatch_update(
    update: Update,
    *,
    dispatcher: Dispatcher,
    bot: Bot,
    processor: TelegramWebhookProcessor | None,
) -> None:
    if processor is not None:
        try:
            await processor.enqueue(update)
        except TelegramWebhookOverloadedError as error:
            logger.warning("Очередь Telegram webhook переполнена: %s", error)
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="webhook_queue_full") from error
        except TelegramWebhookProcessorNotRunningError as error:
            logger.error("Telegram webhook processor неактивен: %s", error)
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="webhook_processor_unavailable") from error
        return

    await dispatcher.feed_update(bot, update)


def create_telegram_router(
    bot: Bot,
    dispatcher: Dispatcher,
    *,
    processor: TelegramWebhookProcessor | None = None,
) -> APIRouter:
    router = APIRouter()
    webhook_path = settings.get_telegram_webhook_path()
    secret_token = settings.WEBHOOK_SECRET_TOKEN

    @router.post(webhook_path)
    async def telegram_webhook(request: Request) -> JSONResponse:
        if secret_token:
            header_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
            if header_token != secret_token:
                logger.warning("Получен Telegram webhook с неверным секретом")
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_secret_token")

        content_type = request.headers.get("content-type", "")
        if content_type and "application/json" not in content_type.lower():
            raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="invalid_content_type")

        try:
            payload: Any = await request.json()
        except Exception as error:  # pragma: no cover - defensive logging
            logger.error("Ошибка чтения Telegram webhook: %s", error)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_payload") from error

        try:
            update = Update.model_validate(payload)
        except Exception as error:  # pragma: no cover - defensive logging
            logger.error("Ошибка валидации Telegram update: %s", error)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_update") from error

        await _dispatch_update(update, dispatcher=dispatcher, bot=bot, processor=processor)
        return JSONResponse({"status": "ok"})

    @router.get("/health/telegram-webhook")
    async def telegram_webhook_health() -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "mode": settings.get_bot_run_mode(),
                "path": webhook_path,
                "webhook_configured": bool(settings.get_telegram_webhook_url()),
                "queue_maxsize": settings.get_webhook_queue_maxsize(),
                "workers": settings.get_webhook_worker_count(),
            }
        )

    return router
