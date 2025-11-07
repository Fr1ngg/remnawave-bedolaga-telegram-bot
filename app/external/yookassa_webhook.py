import asyncio
import logging
import json
import hashlib
import hmac
import base64
from ipaddress import (
    IPv4Address,
    IPv4Network,
    IPv6Address,
    IPv6Network,
    ip_address,
    ip_network,
)
from typing import Iterable, Optional, Dict, Any, List, Union, Tuple
from aiohttp import web

from app.config import settings
from app.services.payment_service import PaymentService
from app.database.database import get_db

logger = logging.getLogger(__name__)


IPAddress = Union[IPv4Address, IPv6Address]
IPNetwork = Union[IPv4Network, IPv6Network]

YOOKASSA_ALLOWED_IP_NETWORKS: tuple[IPNetwork, ...] = (
    ip_network("185.71.76.0/27"),
    ip_network("185.71.77.0/27"),
    ip_network("77.75.153.0/25"),
    ip_network("77.75.154.128/25"),
    ip_network("77.75.156.11/32"),
    ip_network("77.75.156.35/32"),
    ip_network("2a02:5180::/32"),
)


def collect_yookassa_ip_candidates(*values: Optional[str]) -> List[str]:
    candidates: List[str] = []
    for value in values:
        if not value:
            continue
        for part in value.split(","):
            normalized = part.strip()
            if normalized:
                candidates.append(normalized)
    return candidates


def _parse_candidate_ip(candidate: str) -> Optional[IPAddress]:
    value = candidate.strip()
    if not value:
        return None

    if value.startswith("[") and "]" in value:
        value = value[1:value.index("]")]

    if "%" in value:
        value = value.split("%", 1)[0]

    if value.count(":") == 1 and "." in value:
        host, _, port = value.rpartition(":")
        if port.isdigit():
            value = host

    try:
        return ip_address(value)
    except ValueError:
        return None


def _should_trust_forwarded_headers(remote_ip: Optional[IPAddress]) -> bool:
    if remote_ip is None:
        return True

    return any(
        getattr(remote_ip, attribute)
        for attribute in ("is_private", "is_loopback", "is_link_local", "is_reserved")
    )


_TRUSTED_PROXY_NETWORKS_CACHE: Tuple[str, Tuple[IPNetwork, ...]] = ("", ())


def _get_trusted_proxy_networks() -> Tuple[IPNetwork, ...]:
    global _TRUSTED_PROXY_NETWORKS_CACHE

    raw_value = getattr(settings, "YOOKASSA_TRUSTED_PROXY_NETWORKS", "") or ""
    cached_raw, cached_networks = _TRUSTED_PROXY_NETWORKS_CACHE

    if raw_value == cached_raw:
        return cached_networks

    networks: List[IPNetwork] = []
    for part in raw_value.split(","):
        candidate = part.strip()
        if not candidate:
            continue

        try:
            networks.append(ip_network(candidate, strict=False))
        except ValueError:
            logger.warning("Неверная сеть доверенного прокси YooKassa: %s", candidate)

    cached_networks = tuple(networks)
    _TRUSTED_PROXY_NETWORKS_CACHE = (raw_value, cached_networks)
    return cached_networks


def _is_trusted_proxy_ip(ip_object: IPAddress) -> bool:
    if any(
        getattr(ip_object, attribute)
        for attribute in ("is_private", "is_loopback", "is_link_local", "is_reserved")
    ):
        return True

    return any(ip_object in network for network in _get_trusted_proxy_networks())


def resolve_yookassa_ip(
    candidates: Iterable[str],
    *,
    remote: Optional[str] = None,
) -> Optional[IPAddress]:
    remote_ip = _parse_candidate_ip(remote) if remote else None

    if remote_ip is not None and remote_ip.is_global:
        return remote_ip

    candidate_list = list(candidates)

    if _should_trust_forwarded_headers(remote_ip):
        last_hop = remote_ip
        for candidate in reversed(candidate_list):
            ip_object = _parse_candidate_ip(candidate)
            if ip_object is not None:
                if last_hop is None or _is_trusted_proxy_ip(last_hop):
                    if _is_trusted_proxy_ip(ip_object):
                        last_hop = ip_object
                        continue
                    return ip_object
                break

        if last_hop is not None and not _is_trusted_proxy_ip(last_hop):
            return last_hop

    return remote_ip if remote_ip is not None else next(
        (ip for ip in (_parse_candidate_ip(value) for value in candidate_list) if ip is not None),
        None,
    )


def is_yookassa_ip_allowed(ip_object: IPAddress) -> bool:
    return any(ip_object in network for network in YOOKASSA_ALLOWED_IP_NETWORKS)


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

            header_ip_candidates = collect_yookassa_ip_candidates(
                request.headers.get("X-Forwarded-For"),
                request.headers.get("X-Real-IP"),
            )
            client_ip = resolve_yookassa_ip(
                header_ip_candidates,
                remote=request.remote,
            )

            if client_ip is None:
                logger.warning(
                    "🚫 Не удалось определить IP-адрес отправителя YooKassa webhook. Кандидаты: %s",
                    header_ip_candidates + ([request.remote] if request.remote else []),
                )
                return web.Response(status=403, text="Forbidden")

            if not is_yookassa_ip_allowed(client_ip):
                logger.warning(
                    "🚫 YooKassa webhook отклонён: IP %s не входит в доверенные диапазоны (%s)",
                    client_ip,
                    ", ".join(str(network) for network in YOOKASSA_ALLOWED_IP_NETWORKS),
                )
                return web.Response(status=403, text="Forbidden")

            logger.info("🌐 IP-адрес YooKassa подтверждён: %s", client_ip)

            body = await request.text()

            if not body:
                logger.warning("⚠️ Получен пустой webhook от YooKassa")
                return web.Response(status=400, text="Empty body")

            logger.info(f"📄 Body: {body}")

            signature = request.headers.get('Signature') or request.headers.get('X-YooKassa-Signature')

            if settings.YOOKASSA_WEBHOOK_SECRET:
                if not signature:
                    logger.warning("⚠️ Webhook без подписи, но секрет настроен")
                    return web.Response(status=401, text="Missing signature")

                logger.info(f"🔐 Получена подпись: {signature}")

                if not YooKassaWebhookHandler.verify_webhook_signature(
                    body,
                    signature,
                    settings.YOOKASSA_WEBHOOK_SECRET,
                ):
                    logger.warning("❌ Неверная подпись YooKassa webhook")
                    return web.Response(status=401, text="Invalid signature")
            elif signature:
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
            host=settings.YOOKASSA_WEBHOOK_HOST,
            port=settings.YOOKASSA_WEBHOOK_PORT
        )
        
        await site.start()
        
        logger.info(
            "✅ YooKassa webhook сервер запущен на %s:%s",
            settings.YOOKASSA_WEBHOOK_HOST,
            settings.YOOKASSA_WEBHOOK_PORT,
        )
        logger.info(
            "🎯 YooKassa webhook URL: http://%s:%s%s",
            settings.YOOKASSA_WEBHOOK_HOST,
            settings.YOOKASSA_WEBHOOK_PORT,
            settings.YOOKASSA_WEBHOOK_PATH,
        )
        
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
