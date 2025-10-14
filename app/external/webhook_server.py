import base64
import hashlib
import hmac
import logging
import json
from typing import Optional, Iterable

from aiohttp import web
from aiogram import Bot

from app.config import settings
from app.services.tribute_service import TributeService
from app.services.payment_service import PaymentService
from app.database.database import get_db

logger = logging.getLogger(__name__)


class WebhookServer:

    def __init__(self, bot: Bot):
        self.bot = bot
        self.app = None
        self.runner = None
        self.site = None
        self.tribute_service = TributeService(bot)
        self.payment_service = PaymentService(bot)

    async def create_app(self) -> web.Application:

        self.app = web.Application()

        self.app.router.add_post(settings.TRIBUTE_WEBHOOK_PATH, self._tribute_webhook_handler)

        if settings.is_mulenpay_enabled():
            self.app.router.add_post(settings.MULENPAY_WEBHOOK_PATH, self._mulenpay_webhook_handler)

        if settings.is_wata_enabled():
            self.app.router.add_post(settings.WATA_WEBHOOK_PATH, self._wata_webhook_handler)

        if settings.is_cryptobot_enabled():
            self.app.router.add_post(settings.CRYPTOBOT_WEBHOOK_PATH, self._cryptobot_webhook_handler)

        self.app.router.add_get('/health', self._health_check)

        self.app.router.add_options(settings.TRIBUTE_WEBHOOK_PATH, self._options_handler)
        if settings.is_mulenpay_enabled():
            self.app.router.add_options(settings.MULENPAY_WEBHOOK_PATH, self._options_handler)
        if settings.is_wata_enabled():
            self.app.router.add_options(settings.WATA_WEBHOOK_PATH, self._options_handler)
        if settings.is_cryptobot_enabled():
            self.app.router.add_options(settings.CRYPTOBOT_WEBHOOK_PATH, self._options_handler)

        logger.info(f"Webhook сервер настроен:")
        logger.info(f"  - Tribute webhook: POST {settings.TRIBUTE_WEBHOOK_PATH}")
        if settings.is_mulenpay_enabled():
            logger.info(f"  - Mulen Pay webhook: POST {settings.MULENPAY_WEBHOOK_PATH}")
        if settings.is_wata_enabled():
            logger.info(f"  - Wata webhook: POST {settings.WATA_WEBHOOK_PATH}")
        if settings.is_cryptobot_enabled():
            logger.info(f"  - CryptoBot webhook: POST {settings.CRYPTOBOT_WEBHOOK_PATH}")
        logger.info(f"  - Health check: GET /health")

        return self.app
    
    async def start(self):
        
        try:
            if not self.app:
                await self.create_app()
            
            self.runner = web.AppRunner(self.app)
            await self.runner.setup()
            
            self.site = web.TCPSite(
                self.runner,
                host=settings.TRIBUTE_WEBHOOK_HOST,
                port=settings.TRIBUTE_WEBHOOK_PORT
            )

            await self.site.start()

            logger.info(
                "Webhook сервер запущен на %s:%s",
                settings.TRIBUTE_WEBHOOK_HOST,
                settings.TRIBUTE_WEBHOOK_PORT,
            )
            logger.info(
                "Tribute webhook URL: http://%s:%s%s",
                settings.TRIBUTE_WEBHOOK_HOST,
                settings.TRIBUTE_WEBHOOK_PORT,
                settings.TRIBUTE_WEBHOOK_PATH,
            )
            if settings.is_mulenpay_enabled():
                logger.info(
                    "Mulen Pay webhook URL: http://%s:%s%s",
                    settings.TRIBUTE_WEBHOOK_HOST,
                    settings.TRIBUTE_WEBHOOK_PORT,
                    settings.MULENPAY_WEBHOOK_PATH,
                )
            if settings.is_wata_enabled():
                logger.info(
                    "Wata webhook URL: http://%s:%s%s",
                    settings.TRIBUTE_WEBHOOK_HOST,
                    settings.TRIBUTE_WEBHOOK_PORT,
                    settings.WATA_WEBHOOK_PATH,
                )
            if settings.is_cryptobot_enabled():
                logger.info(
                    "CryptoBot webhook URL: http://%s:%s%s",
                    settings.TRIBUTE_WEBHOOK_HOST,
                    settings.TRIBUTE_WEBHOOK_PORT,
                    settings.CRYPTOBOT_WEBHOOK_PATH,
                )
            
        except Exception as e:
            logger.error(f"Ошибка запуска webhook сервера: {e}")
            raise
    
    async def stop(self):
        
        try:
            if self.site:
                await self.site.stop()
                logger.info("Webhook сайт остановлен")
            
            if self.runner:
                await self.runner.cleanup()
                logger.info("Webhook runner очищен")
                
        except Exception as e:
            logger.error(f"Ошибка остановки webhook сервера: {e}")
    
    async def _options_handler(self, request: web.Request) -> web.Response:
        return web.Response(
            status=200,
            headers={
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type, trbt-signature, Crypto-Pay-API-Signature, X-MulenPay-Signature, X-Signature, Authorization',
            }
        )

    async def _mulenpay_webhook_handler(self, request: web.Request) -> web.Response:
        try:
            logger.info(f"Получен Mulen Pay webhook: {request.method} {request.path}")
            logger.info(f"MulenPay webhook headers: {dict(request.headers)}")
            raw_body = await request.read()

            if not raw_body:
                logger.warning("Пустой Mulen Pay webhook")
                return web.json_response({"status": "error", "reason": "empty_body"}, status=400)

            # Временно отключаем проверку подписи для отладки
            # TODO: Включить обратно после настройки MulenPay
            if not self._verify_mulenpay_signature(request, raw_body):
                logger.warning("MulenPay webhook signature verification failed, but processing anyway for debugging")
                # return web.json_response({"status": "error", "reason": "invalid_signature"}, status=401)

            try:
                payload = json.loads(raw_body.decode('utf-8'))
            except json.JSONDecodeError as error:
                logger.error(f"Ошибка парсинга Mulen Pay webhook: {error}")
                return web.json_response({"status": "error", "reason": "invalid_json"}, status=400)

            # Получаем соединение с БД
            db_generator = get_db()
            db = await db_generator.__anext__()

            try:
                success = await self.payment_service.process_mulenpay_callback(db, payload)
                if success:
                    return web.json_response({"status": "ok"}, status=200)
                return web.json_response({"status": "error", "reason": "processing_failed"}, status=400)
            except Exception as error:
                logger.error(f"Ошибка обработки Mulen Pay webhook: {error}", exc_info=True)
                return web.json_response({"status": "error", "reason": "internal_error"}, status=500)
            finally:
                try:
                    await db_generator.__anext__()
                except StopAsyncIteration:
                    pass

        except Exception as error:
            logger.error(f"Критическая ошибка Mulen Pay webhook: {error}", exc_info=True)
            return web.json_response({"status": "error", "reason": "internal_error", "message": str(error)}, status=500)

    async def _wata_webhook_handler(self, request: web.Request) -> web.Response:
        try:
            logger.info("Получен Wata webhook: %s %s", request.method, request.path)
            raw_body = await request.read()

            if not raw_body:
                logger.warning("Пустой Wata webhook")
                return web.json_response({"status": "error", "reason": "empty_body"}, status=400)

            signature = request.headers.get("X-Signature", "")
            wata_service = getattr(self.payment_service, "wata_service", None)

            if not wata_service:
                logger.error("Wata service не настроен, но получен webhook")
                return web.json_response({"status": "error", "reason": "service_not_configured"}, status=503)

            is_valid = False
            try:
                is_valid = await wata_service.verify_signature(raw_body, signature)
            except Exception as error:
                logger.error("Ошибка проверки подписи Wata: %s", error)

            if not is_valid:
                logger.warning("Подпись Wata webhook не прошла проверку")
                return web.json_response({"status": "error", "reason": "invalid_signature"}, status=401)

            try:
                payload = json.loads(raw_body.decode('utf-8'))
            except json.JSONDecodeError as error:
                logger.error("Ошибка парсинга Wata webhook: %s", error)
                return web.json_response({"status": "error", "reason": "invalid_json"}, status=400)

            db_generator = get_db()
            db = await db_generator.__anext__()

            try:
                success = await self.payment_service.process_wata_webhook(db, payload)
                if success:
                    return web.json_response({"status": "ok"}, status=200)
                return web.json_response({"status": "error", "reason": "processing_failed"}, status=400)
            except Exception as error:
                logger.error("Ошибка обработки Wata webhook: %s", error, exc_info=True)
                return web.json_response({"status": "error", "reason": "internal_error"}, status=500)
            finally:
                try:
                    await db_generator.__anext__()
                except StopAsyncIteration:
                    pass

        except Exception as error:
            logger.error("Критическая ошибка Wata webhook: %s", error, exc_info=True)
            return web.json_response(
                {"status": "error", "reason": "internal_error", "message": str(error)},
                status=500,
            )

    @staticmethod
    def _extract_mulenpay_header(request: web.Request, header_names: Iterable[str]) -> Optional[str]:
        for header_name in header_names:
            value = request.headers.get(header_name)
            if value:
                return value.strip()
        return None

    @staticmethod
    def _verify_mulenpay_signature(request: web.Request, raw_body: bytes) -> bool:
        secret_key = settings.MULENPAY_SECRET_KEY
        if not secret_key:
            logger.error("Mulen Pay secret key is not configured")
            return False

        # Логируем все заголовки для отладки
        logger.info("MulenPay webhook headers for signature verification:")
        for header_name, header_value in request.headers.items():
            if any(keyword in header_name.lower() for keyword in ['signature', 'sign', 'token', 'auth']):
                logger.info(f"  {header_name}: {header_value}")

        signature = WebhookServer._extract_mulenpay_header(
            request,
            (
                'X-MulenPay-Signature',
                'X-Mulenpay-Signature',
                'X-MULENPAY-SIGNATURE',
                'X-MulenPay-Webhook-Signature',
                'X-Mulenpay-Webhook-Signature',
                'X-MULENPAY-WEBHOOK-SIGNATURE',
                'X-Signature',
                'Signature',
                'X-MulenPay-Sign',
                'X-Mulenpay-Sign',
                'X-MULENPAY-SIGN',
                'MulenPay-Signature',
                'Mulenpay-Signature',
                'MULENPAY-SIGNATURE',
                'signature',
                'sign',
            )
        )
        if signature:
            normalized_signature = signature
            if normalized_signature.lower().startswith('sha256='):
                normalized_signature = normalized_signature.split('=', 1)[1].strip()

            hmac_digest = hmac.new(
                secret_key.encode('utf-8'),
                raw_body,
                hashlib.sha256,
            ).digest()
            expected_hex_signature = hmac_digest.hex()
            expected_base64_signature = base64.b64encode(hmac_digest).decode('utf-8').strip()
            expected_urlsafe_base64_signature = base64.urlsafe_b64encode(hmac_digest).decode('utf-8').strip()

            normalized_signature_lower = normalized_signature.lower()
            if hmac.compare_digest(normalized_signature_lower, expected_hex_signature.lower()):
                return True

            normalized_signature_no_padding = normalized_signature.rstrip('=')
            if hmac.compare_digest(normalized_signature_no_padding, expected_base64_signature.rstrip('=')):
                return True

            if hmac.compare_digest(normalized_signature_no_padding, expected_urlsafe_base64_signature.rstrip('=')):
                return True

            logger.error("Неверная подпись Mulen Pay webhook")
            return False

        authorization_header = request.headers.get('Authorization')
        if authorization_header:
            scheme, _, value = authorization_header.partition(' ')
            scheme_lower = scheme.lower()
            token = value.strip() if value else scheme.strip()

            if scheme_lower in ('bearer', 'token'):
                if hmac.compare_digest(token, secret_key):
                    return True

                logger.error("Неверный %s токен Mulen Pay webhook", scheme)
                return False

            if not value and hmac.compare_digest(token, secret_key):
                return True

        fallback_token = WebhookServer._extract_mulenpay_header(
            request,
            (
                'X-MulenPay-Token',
                'X-Mulenpay-Token',
                'X-Webhook-Token',
            )
        )
        if fallback_token and hmac.compare_digest(fallback_token, secret_key):
            return True

        logger.info(
            "Mulen Pay webhook headers received: %s",
            {key: value for key, value in request.headers.items() if 'authorization' not in key.lower()}
        )

        logger.error("Отсутствует подпись Mulen Pay webhook")
        return False

    async def _tribute_webhook_handler(self, request: web.Request) -> web.Response:

        try:
            logger.info(f"Получен Tribute webhook: {request.method} {request.path}")
            logger.info(f"Headers: {dict(request.headers)}")
            
            raw_body = await request.read()
            
            if not raw_body:
                logger.warning("Получен пустой webhook от Tribute")
                return web.json_response(
                    {"status": "error", "reason": "empty_body"},
                    status=400
                )
            
            payload = raw_body.decode('utf-8')
            logger.info(f"Payload: {payload}")
            
            try:
                webhook_data = json.loads(payload)
                logger.info(f"Распарсенные данные: {webhook_data}")
            except json.JSONDecodeError as e:
                logger.error(f"Ошибка парсинга JSON: {e}")
                return web.json_response(
                    {"status": "error", "reason": "invalid_json"},
                    status=400
                )
            
            signature = request.headers.get('trbt-signature')
            logger.info(f"Signature: {signature}")

            if not signature:
                logger.error("Отсутствует заголовок подписи Tribute webhook")
                return web.json_response(
                    {"status": "error", "reason": "missing_signature"},
                    status=401
                )

            if settings.TRIBUTE_API_KEY:
                from app.external.tribute import TributeService as TributeAPI
                tribute_api = TributeAPI()
                if not tribute_api.verify_webhook_signature(payload, signature):
                    logger.error("Неверная подпись Tribute webhook")
                    return web.json_response(
                        {"status": "error", "reason": "invalid_signature"},
                        status=401
                    )

            result = await self.tribute_service.process_webhook(payload)
            
            if result:
                logger.info(f"Tribute webhook обработан успешно: {result}")
                return web.json_response({"status": "ok", "result": result}, status=200)
            else:
                logger.error("Ошибка обработки Tribute webhook")
                return web.json_response(
                    {"status": "error", "reason": "processing_failed"},
                    status=400
                )
            
        except Exception as e:
            logger.error(f"Критическая ошибка обработки Tribute webhook: {e}", exc_info=True)
            return web.json_response(
                {"status": "error", "reason": "internal_error", "message": str(e)},
                status=500
            )
    
    async def _cryptobot_webhook_handler(self, request: web.Request) -> web.Response:
        
        try:
            logger.info(f"Получен CryptoBot webhook: {request.method} {request.path}")
            logger.info(f"Headers: {dict(request.headers)}")
            
            raw_body = await request.read()
            
            if not raw_body:
                logger.warning("Получен пустой CryptoBot webhook")
                return web.json_response(
                    {"status": "error", "reason": "empty_body"},
                    status=400
                )
            
            payload = raw_body.decode('utf-8')
            logger.info(f"CryptoBot Payload: {payload}")
            
            try:
                webhook_data = json.loads(payload)
                logger.info(f"CryptoBot данные: {webhook_data}")
            except json.JSONDecodeError as e:
                logger.error(f"Ошибка парсинга CryptoBot JSON: {e}")
                return web.json_response(
                    {"status": "error", "reason": "invalid_json"},
                    status=400
                )
            
            signature = request.headers.get('Crypto-Pay-API-Signature')
            logger.info(f"CryptoBot Signature: {signature}")

            if signature and settings.CRYPTOBOT_WEBHOOK_SECRET:
                from app.external.cryptobot import CryptoBotService
                cryptobot_service = CryptoBotService()
                if not cryptobot_service.verify_webhook_signature(payload, signature):
                    logger.error("Неверная подпись CryptoBot webhook")
                    return web.json_response(
                        {"status": "error", "reason": "invalid_signature"},
                        status=401
                    )

            from app.services.payment_service import PaymentService
            from app.database.database import AsyncSessionLocal
            
            payment_service = PaymentService(self.bot)
            
            async with AsyncSessionLocal() as db:
                result = await payment_service.process_cryptobot_webhook(db, webhook_data)
            
            if result:
                logger.info(f"CryptoBot webhook обработан успешно")
                return web.json_response({"status": "ok"}, status=200)
            else:
                logger.error("Ошибка обработки CryptoBot webhook")
                return web.json_response(
                    {"status": "error", "reason": "processing_failed"},
                    status=400
                )
            
        except Exception as e:
            logger.error(f"Критическая ошибка обработки CryptoBot webhook: {e}", exc_info=True)
            return web.json_response(
                {"status": "error", "reason": "internal_error", "message": str(e)},
                status=500
            )
    
    async def _health_check(self, request: web.Request) -> web.Response:
        
        return web.json_response({
            "status": "ok",
            "service": "payment-webhooks",
            "tribute_enabled": settings.TRIBUTE_ENABLED,
            "cryptobot_enabled": settings.is_cryptobot_enabled(),
            "port": settings.TRIBUTE_WEBHOOK_PORT,
            "tribute_path": settings.TRIBUTE_WEBHOOK_PATH,
            "cryptobot_path": settings.CRYPTOBOT_WEBHOOK_PATH if settings.is_cryptobot_enabled() else None
        })
