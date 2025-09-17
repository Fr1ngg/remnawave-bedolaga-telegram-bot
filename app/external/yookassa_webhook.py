import asyncio
import logging
import json
import hashlib
import hmac
import base64
from typing import Optional, Dict, Any

import uvicorn
from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.payment_service import PaymentService
from app.database.database import get_db

logger = logging.getLogger(__name__)


class YooKassaWebhookHandler:

    @staticmethod
    def verify_webhook_signature(body: str, signature: str, secret: str) -> bool:
        try:
            signature_parts = signature.strip().split(' ')
            
            if len(signature_parts) < 4:
                logger.error(f"Неверный формат подписи YooKassa: {signature}")
                return False
            
            version = signature_parts[0] 
            payment_id = signature_parts[1] 
            timestamp = signature_parts[2]  
            received_signature = signature_parts[3]
            
            if version != "v1":
                logger.error(f"Неподдерживаемая версия подписи: {version}")
                return False
            
            logger.info(f"Проверка подписи v1 для платежа {payment_id}, timestamp: {timestamp}")
            
            
            expected_signature_1 = hmac.new(
                secret.encode('utf-8'),
                body.encode('utf-8'),
                hashlib.sha256
            ).digest()
            expected_signature_1_b64 = base64.b64encode(expected_signature_1).decode('utf-8')
            
            signed_payload_2 = f"{payment_id}.{timestamp}.{body}"
            expected_signature_2 = hmac.new(
                secret.encode('utf-8'),
                signed_payload_2.encode('utf-8'),
                hashlib.sha256
            ).digest()
            expected_signature_2_b64 = base64.b64encode(expected_signature_2).decode('utf-8')
            
            signed_payload_3 = f"{timestamp}.{body}"
            expected_signature_3 = hmac.new(
                secret.encode('utf-8'),
                signed_payload_3.encode('utf-8'),
                hashlib.sha256
            ).digest()
            expected_signature_3_b64 = base64.b64encode(expected_signature_3).decode('utf-8')
            
            logger.debug(f"Получена подпись: {received_signature}")
            logger.debug(f"Ожидаемая подпись (вариант 1): {expected_signature_1_b64}")
            logger.debug(f"Ожидаемая подпись (вариант 2): {expected_signature_2_b64}")
            logger.debug(f"Ожидаемая подпись (вариант 3): {expected_signature_3_b64}")
            
            is_valid = (
                hmac.compare_digest(received_signature, expected_signature_1_b64) or
                hmac.compare_digest(received_signature, expected_signature_2_b64) or  
                hmac.compare_digest(received_signature, expected_signature_3_b64)
            )
            
            if is_valid:
                logger.info("✅ Подпись YooKassa webhook проверена успешно")
            else:
                logger.warning("⚠️ Подпись YooKassa webhook не совпадает ни с одним вариантом")
            
            return is_valid
            
        except Exception as e:
            logger.error(f"Ошибка проверки подписи YooKassa: {e}")
            return False
    
    def __init__(self, payment_service: PaymentService):
        self.payment_service = payment_service
    
    async def handle_webhook(self, request: web.Request) -> web.Response:
        
        try:
            logger.info(f"📥 Получен YooKassa webhook: {request.method} {request.path}")
            logger.info(f"📋 Headers: {dict(request.headers)}")
            
            body = await request.text()
            
            if not body:
                logger.warning("⚠️ Получен пустой webhook от YooKassa")
                return web.Response(status=400, text="Empty body")
            
            logger.info(f"📄 Body: {body}")
            
            signature = request.headers.get('Signature') or request.headers.get('X-YooKassa-Signature')
            
            if settings.YOOKASSA_WEBHOOK_SECRET and signature:
                logger.info(f"🔐 Получена подпись: {signature}")
                
                if not YooKassaWebhookHandler.verify_webhook_signature(body, signature, settings.YOOKASSA_WEBHOOK_SECRET):
                    logger.warning("❌ Подпись не совпала, но продолжаем обработку (режим отладки)")
                else:
                    logger.info("✅ Подпись webhook проверена успешно")
                    
            elif settings.YOOKASSA_WEBHOOK_SECRET and not signature:
                logger.warning("⚠️ Webhook без подписи, но секрет настроен")
                
            elif signature and not settings.YOOKASSA_WEBHOOK_SECRET:
                logger.info("ℹ️ Подпись получена, но проверка отключена (YOOKASSA_WEBHOOK_SECRET не настроен)")
                
            else:
                logger.info("ℹ️ Проверка подписи отключена")
            
            try:
                webhook_data = json.loads(body)
            except json.JSONDecodeError as e:
                logger.error(f"❌ Ошибка парсинга JSON webhook YooKassa: {e}")
                return web.Response(status=400, text="Invalid JSON")
            
            logger.info(f"📊 Обработка webhook YooKassa: {webhook_data.get('event', 'unknown_event')}")
            logger.debug(f"🔍 Полные данные webhook: {webhook_data}")
            
            event_type = webhook_data.get("event")
            if not event_type:
                logger.warning("⚠️ Webhook YooKassa без типа события")
                return web.Response(status=400, text="No event type")
            
            if event_type not in ["payment.succeeded", "payment.waiting_for_capture"]:
                logger.info(f"ℹ️ Игнорируем событие YooKassa: {event_type}")
                return web.Response(status=200, text="OK")
            
            async for db in get_db():
                try:
                    success = await self.payment_service.process_yookassa_webhook(db, webhook_data)
                    
                    if success:
                        logger.info(f"✅ Успешно обработан webhook YooKassa: {event_type}")
                        return web.Response(status=200, text="OK")
                    else:
                        logger.error(f"❌ Ошибка обработки webhook YooKassa: {event_type}")
                        return web.Response(status=500, text="Processing error")
                        
                finally:
                    await db.close()
        
        except Exception as e:
            logger.error(f"❌ Критическая ошибка обработки webhook YooKassa: {e}", exc_info=True)
            return web.Response(status=500, text="Internal server error")
    
    def setup_routes(self, app: web.Application) -> None:
        
        webhook_path = settings.YOOKASSA_WEBHOOK_PATH
        app.router.add_post(webhook_path, self.handle_webhook)
        app.router.add_get(webhook_path, self._get_handler) 
        app.router.add_options(webhook_path, self._options_handler) 
        
        logger.info(f"✅ Настроен YooKassa webhook на пути: POST {webhook_path}")
    
    async def _get_handler(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "message": "YooKassa webhook endpoint is working",
            "method": "GET",
            "path": request.path,
            "note": "Use POST method for actual webhooks"
        })
    
    async def _options_handler(self, request: web.Request) -> web.Response:
        return web.Response(
            status=200,
            headers={
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type, X-YooKassa-Signature',
            }
        )


def create_yookassa_webhook_app(payment_service: PaymentService) -> web.Application:
    
    app = web.Application()
    
    webhook_handler = YooKassaWebhookHandler(payment_service)
    webhook_handler.setup_routes(app)
    
    async def health_check(request):
        return web.json_response({
            "status": "ok", 
            "service": "yookassa_webhook",
            "port": settings.YOOKASSA_WEBHOOK_PORT,
            "path": settings.YOOKASSA_WEBHOOK_PATH,
            "enabled": settings.is_yookassa_enabled()
        })
    
    app.router.add_get("/health", health_check)
    
    return app


async def start_yookassa_webhook_server(payment_service: PaymentService) -> None:
    
    if not settings.is_yookassa_enabled():
        logger.info("ℹ️ YooKassa отключена, webhook сервер не запускается")
        return
    
    try:
        app = create_yookassa_webhook_app(payment_service)
        
        runner = web.AppRunner(app)
        await runner.setup()
        
        site = web.TCPSite(
            runner, 
            host='0.0.0.0',  
            port=settings.YOOKASSA_WEBHOOK_PORT
        )
        
        await site.start()
        
        logger.info(f"✅ YooKassa webhook сервер запущен на порту {settings.YOOKASSA_WEBHOOK_PORT}")
        logger.info(f"🎯 YooKassa webhook URL: http://0.0.0.0:{settings.YOOKASSA_WEBHOOK_PORT}{settings.YOOKASSA_WEBHOOK_PATH}")
        
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("🛑 YooKassa webhook сервер получил сигнал остановки")
        finally:
            await site.stop()
            await runner.cleanup()
            logger.info("✅ YooKassa webhook сервер остановлен")
        
    except Exception as e:
        logger.error(f"❌ Ошибка запуска YooKassa webhook сервера: {e}", exc_info=True)
        raise
