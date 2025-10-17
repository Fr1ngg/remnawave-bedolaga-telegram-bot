from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import aiohttp
from aiohttp import web
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.config import settings
from app.database.database import get_db
from app.services.payment_service import PaymentService

logger = logging.getLogger(__name__)


class WataPublicKeyProvider:
    """Loads and caches the WATA public key used for webhook signature validation."""

    def __init__(self, *, cache_seconds: Optional[int] = None) -> None:
        self._cache_seconds = cache_seconds or int(settings.WATA_PUBLIC_KEY_CACHE_SECONDS)
        self._cached_key: Optional[str] = None
        self._expires_at: Optional[datetime] = None
        self._lock = asyncio.Lock()

    async def get_public_key(self) -> Optional[str]:
        """Returns a cached public key or fetches a new one from WATA."""

        now = datetime.utcnow()
        if self._cached_key and self._expires_at and now < self._expires_at:
            return self._cached_key

        async with self._lock:
            now = datetime.utcnow()
            if self._cached_key and self._expires_at and now < self._expires_at:
                return self._cached_key

            key = await self._fetch_public_key()
            if key:
                self._cached_key = key
                if self._cache_seconds > 0:
                    self._expires_at = datetime.utcnow() + timedelta(seconds=self._cache_seconds)
                else:
                    self._expires_at = None
                logger.debug("Получен и закеширован публичный ключ WATA")
                return self._cached_key

            if self._cached_key:
                logger.warning("Используем ранее закешированный публичный ключ WATA")
                return self._cached_key

            logger.error("Публичный ключ WATA недоступен")
            return None

    async def _fetch_public_key(self) -> Optional[str]:
        url = settings.WATA_PUBLIC_KEY_URL or f"{settings.WATA_BASE_URL.rstrip('/')}/public-key"
        timeout = aiohttp.ClientTimeout(total=settings.WATA_REQUEST_TIMEOUT)

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    text = await response.text()
                    if response.status >= 400:
                        logger.error(
                            "Ошибка получения публичного ключа WATA %s: %s",
                            response.status,
                            text,
                        )
                        return None

                    try:
                        payload = await response.json()
                    except aiohttp.ContentTypeError:
                        logger.error("Ответ WATA public-key не является JSON: %s", text)
                        return None

            if isinstance(payload, dict):
                value = payload.get("value")
                if value:
                    return value
                logger.error("Ответ WATA public-key не содержит ключ: %s", payload)
            else:
                logger.error("Неожиданный формат ответа WATA public-key: %s", payload)
        except Exception as error:
            logger.error("Ошибка запроса публичного ключа WATA: %s", error)

        return None


class WataWebhookHandler:
    """Processes webhook callbacks coming from WATA."""

    def __init__(
        self,
        payment_service: PaymentService,
        *,
        public_key_provider: Optional[WataPublicKeyProvider] = None,
    ) -> None:
        self.payment_service = payment_service
        self.public_key_provider = public_key_provider or WataPublicKeyProvider()

    async def _verify_signature(self, raw_body: str, signature: str) -> bool:
        signature = (signature or "").strip()
        if not signature:
            logger.error("WATA webhook без подписи")
            return False

        public_key_pem = await self.public_key_provider.get_public_key()
        if not public_key_pem:
            logger.error("Публичный ключ WATA отсутствует, проверка подписи невозможна")
            return False

        try:
            signature_bytes = base64.b64decode(signature)
        except (ValueError, TypeError):
            logger.error("Некорректная подпись WATA (не Base64)")
            return False

        try:
            public_key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
        except ValueError as error:
            logger.error("Ошибка загрузки публичного ключа WATA: %s", error)
            return False

        try:
            public_key.verify(
                signature_bytes,
                raw_body.encode("utf-8"),
                padding.PKCS1v15(),
                hashes.SHA512(),
            )
            return True
        except InvalidSignature:
            logger.warning("Подпись WATA webhook не прошла проверку")
            return False
        except Exception as error:
            logger.error("Ошибка проверки подписи WATA: %s", error)
            return False

    async def handle_webhook(self, request: web.Request) -> web.Response:
        if not settings.is_wata_enabled():
            logger.warning("Получен WATA webhook, но сервис отключен")
            return web.json_response({"status": "error", "reason": "wata_disabled"}, status=503)

        raw_body = await request.text()
        if not raw_body:
            logger.warning("Получен пустой WATA webhook")
            return web.json_response({"status": "error", "reason": "empty_body"}, status=400)

        signature = request.headers.get("X-Signature")
        if not await self._verify_signature(raw_body, signature or ""):
            return web.json_response({"status": "error", "reason": "invalid_signature"}, status=401)

        try:
            payload: Dict[str, Any] = json.loads(raw_body)
        except json.JSONDecodeError:
            logger.error("Некорректный JSON WATA webhook")
            return web.json_response({"status": "error", "reason": "invalid_json"}, status=400)

        logger.info(
            "Получен WATA webhook: order_id=%s, status=%s",
            payload.get("orderId"),
            payload.get("transactionStatus"),
        )

        processed: Optional[bool] = None
        async for db in get_db():
            processed = await self.payment_service.process_wata_webhook(db, payload)

        if processed:
            return web.json_response({"status": "ok"}, status=200)

        return web.json_response({"status": "error", "reason": "not_processed"}, status=400)

    async def health_check(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "status": "ok",
                "service": "wata_webhook",
                "enabled": settings.is_wata_enabled(),
                "path": settings.WATA_WEBHOOK_PATH,
            }
        )

    async def options_handler(self, _: web.Request) -> web.Response:
        return web.Response(
            status=200,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, X-Signature",
            },
        )


def create_wata_webhook_app(payment_service: PaymentService) -> web.Application:
    app = web.Application()
    handler = WataWebhookHandler(payment_service)

    app.router.add_post(settings.WATA_WEBHOOK_PATH, handler.handle_webhook)
    app.router.add_get(settings.WATA_WEBHOOK_PATH, handler.health_check)
    app.router.add_options(settings.WATA_WEBHOOK_PATH, handler.options_handler)
    app.router.add_get("/health", handler.health_check)

    logger.info(
        "Настроен WATA webhook endpoint на %s",
        settings.WATA_WEBHOOK_PATH,
    )

    return app


async def start_wata_webhook_server(payment_service: PaymentService) -> None:
    if not settings.is_wata_enabled():
        logger.info("WATA отключен, webhook сервер не запускается")
        return

    app = create_wata_webhook_app(payment_service)
    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        host=settings.WATA_WEBHOOK_HOST,
        port=settings.WATA_WEBHOOK_PORT,
    )

    try:
        await site.start()
        logger.info(
            "WATA webhook сервер запущен на %s:%s",
            settings.WATA_WEBHOOK_HOST,
            settings.WATA_WEBHOOK_PORT,
        )
        logger.info(
            "WATA webhook URL: http://%s:%s%s",
            settings.WATA_WEBHOOK_HOST,
            settings.WATA_WEBHOOK_PORT,
            settings.WATA_WEBHOOK_PATH,
        )

        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        logger.info("WATA webhook сервер остановлен по запросу")
    finally:
        await site.stop()
        await runner.cleanup()
        logger.info("WATA webhook сервер корректно остановлен")
