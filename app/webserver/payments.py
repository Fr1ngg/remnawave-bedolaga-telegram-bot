from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from typing import Iterable

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import JSONResponse

from aiogram import Bot

from app.config import settings
from app.database.database import get_db
from app.external.tribute import TributeService as TributeAPI
from app.external import yookassa_webhook as yookassa_webhook_module
from app.external.wata_webhook import WataWebhookHandler
from app.external.heleket_webhook import HeleketWebhookHandler
from app.external.pal24_client import Pal24APIError
from app.services.pal24_service import Pal24Service
from app.services.payment_service import PaymentService
from app.services.tribute_service import TributeService


logger = logging.getLogger(__name__)


def _create_cors_response() -> Response:
    return Response(
        status_code=status.HTTP_200_OK,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, trbt-signature, Crypto-Pay-API-Signature, X-MulenPay-Signature, Authorization",
        },
    )


def _extract_header(request: Request, header_names: Iterable[str]) -> str | None:
    for header_name in header_names:
        value = request.headers.get(header_name)
        if value:
            return value.strip()
    return None


def _verify_mulenpay_signature(request: Request, raw_body: bytes) -> bool:
    secret_key = settings.MULENPAY_SECRET_KEY
    display_name = settings.get_mulenpay_display_name()

    if not secret_key:
        logger.error("%s secret key is not configured", display_name)
        return False

    signature = _extract_header(
        request,
        (
            "X-MulenPay-Signature",
            "X-Mulenpay-Signature",
            "X-MULENPAY-SIGNATURE",
            "X-MulenPay-Webhook-Signature",
            "X-Mulenpay-Webhook-Signature",
            "X-MULENPAY-WEBHOOK-SIGNATURE",
            "X-Signature",
            "Signature",
            "X-MulenPay-Sign",
            "X-Mulenpay-Sign",
            "X-MULENPAY-SIGN",
            "MulenPay-Signature",
            "Mulenpay-Signature",
            "MULENPAY-SIGNATURE",
            "signature",
            "sign",
        ),
    )

    if signature:
        normalized_signature = signature
        if normalized_signature.lower().startswith("sha256="):
            normalized_signature = normalized_signature.split("=", 1)[1].strip()

        hmac_digest = hmac.new(secret_key.encode("utf-8"), raw_body, hashlib.sha256).digest()
        expected_hex = hmac_digest.hex()
        expected_base64 = base64.b64encode(hmac_digest).decode("utf-8").strip()
        expected_urlsafe = base64.urlsafe_b64encode(hmac_digest).decode("utf-8").strip()

        normalized_lower = normalized_signature.lower()
        if hmac.compare_digest(normalized_lower, expected_hex.lower()):
            return True

        normalized_no_padding = normalized_signature.rstrip("=")
        if hmac.compare_digest(normalized_no_padding, expected_base64.rstrip("=")):
            return True
        if hmac.compare_digest(normalized_no_padding, expected_urlsafe.rstrip("=")):
            return True

        logger.error("Неверная подпись %s webhook", display_name)
        return False

    authorization_header = request.headers.get("Authorization")
    if authorization_header:
        scheme, _, value = authorization_header.partition(" ")
        scheme_lower = scheme.lower()
        token = value.strip() if value else scheme.strip()

        if scheme_lower in {"bearer", "token"}:
            if hmac.compare_digest(token, secret_key):
                return True
            logger.error("Неверный %s токен %s webhook", scheme, display_name)
            return False

        if not value and hmac.compare_digest(token, secret_key):
            return True

    fallback_token = _extract_header(
        request,
        (
            "X-MulenPay-Token",
            "X-Mulenpay-Token",
            "X-Webhook-Token",
        ),
    )
    if fallback_token and hmac.compare_digest(fallback_token, secret_key):
        return True

    logger.error("Отсутствует подпись %s webhook", display_name)
    return False


async def _process_payment_service_callback(
    payment_service: PaymentService,
    payload: dict,
    method_name: str,
) -> bool:
    db_generator = get_db()
    try:
        db = await db_generator.__anext__()
    except StopAsyncIteration:  # pragma: no cover - defensive guard
        return False

    try:
        process_callback = getattr(payment_service, method_name)
        return await process_callback(db, payload)
    finally:
        try:
            await db_generator.__anext__()
        except StopAsyncIteration:
            pass


async def _parse_pal24_payload(request: Request) -> dict[str, str]:
    try:
        if request.headers.get("content-type", "").startswith("application/json"):
            data = await request.json()
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
    except json.JSONDecodeError:
        logger.debug("Pal24 webhook JSON payload не удалось распарсить")

    form = await request.form()
    if form:
        return {str(k): str(v) for k, v in form.multi_items()}

    raw_body = (await request.body()).decode("utf-8")
    if raw_body:
        try:
            data = json.loads(raw_body)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except json.JSONDecodeError:
            logger.debug("Pal24 webhook body не удалось распарсить как JSON: %s", raw_body)

    return {}


def create_payment_router(bot: Bot, payment_service: PaymentService) -> APIRouter | None:
    router = APIRouter()
    routes_registered = False

    if settings.TRIBUTE_ENABLED:
        tribute_service = TributeService(bot)
        tribute_api = TributeAPI()

        @router.options(settings.TRIBUTE_WEBHOOK_PATH)
        async def tribute_options() -> Response:
            return _create_cors_response()

        @router.post(settings.TRIBUTE_WEBHOOK_PATH)
        async def tribute_webhook(request: Request) -> JSONResponse:
            raw_body = await request.body()
            if not raw_body:
                return JSONResponse({"status": "error", "reason": "empty_body"}, status_code=status.HTTP_400_BAD_REQUEST)

            payload = raw_body.decode("utf-8")

            signature = request.headers.get("trbt-signature")
            if not signature:
                return JSONResponse(
                    {"status": "error", "reason": "missing_signature"},
                    status_code=status.HTTP_401_UNAUTHORIZED,
                )

            if settings.TRIBUTE_API_KEY and not tribute_api.verify_webhook_signature(payload, signature):
                return JSONResponse(
                    {"status": "error", "reason": "invalid_signature"},
                    status_code=status.HTTP_401_UNAUTHORIZED,
                )

            try:
                json.loads(payload)
            except json.JSONDecodeError:
                return JSONResponse(
                    {"status": "error", "reason": "invalid_json"},
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            result = await tribute_service.process_webhook(payload)
            if result:
                return JSONResponse({"status": "ok", "result": result})

            return JSONResponse(
                {"status": "error", "reason": "processing_failed"},
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        routes_registered = True

    if settings.is_mulenpay_enabled():

        @router.options(settings.MULENPAY_WEBHOOK_PATH)
        async def mulenpay_options() -> Response:
            return _create_cors_response()

        @router.post(settings.MULENPAY_WEBHOOK_PATH)
        async def mulenpay_webhook(request: Request) -> JSONResponse:
            raw_body = await request.body()
            if not raw_body:
                return JSONResponse({"status": "error", "reason": "empty_body"}, status_code=status.HTTP_400_BAD_REQUEST)

            if not _verify_mulenpay_signature(request, raw_body):
                return JSONResponse(
                    {"status": "error", "reason": "invalid_signature"},
                    status_code=status.HTTP_401_UNAUTHORIZED,
                )

            try:
                payload = json.loads(raw_body.decode("utf-8"))
            except json.JSONDecodeError:
                return JSONResponse(
                    {"status": "error", "reason": "invalid_json"},
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            success = await _process_payment_service_callback(
                payment_service,
                payload,
                "process_mulenpay_callback",
            )
            if success:
                return JSONResponse({"status": "ok"})

            return JSONResponse(
                {"status": "error", "reason": "processing_failed"},
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        routes_registered = True

    if settings.is_cryptobot_enabled():

        @router.options(settings.CRYPTOBOT_WEBHOOK_PATH)
        async def cryptobot_options() -> Response:
            return _create_cors_response()

        @router.post(settings.CRYPTOBOT_WEBHOOK_PATH)
        async def cryptobot_webhook(request: Request) -> JSONResponse:
            raw_body = await request.body()
            if not raw_body:
                return JSONResponse({"status": "error", "reason": "empty_body"}, status_code=status.HTTP_400_BAD_REQUEST)

            payload_text = raw_body.decode("utf-8")
            try:
                payload = json.loads(payload_text)
            except json.JSONDecodeError:
                return JSONResponse(
                    {"status": "error", "reason": "invalid_json"},
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            signature = request.headers.get("Crypto-Pay-API-Signature")
            secret = settings.CRYPTOBOT_WEBHOOK_SECRET
            if secret:
                if not signature:
                    return JSONResponse(
                        {"status": "error", "reason": "missing_signature"},
                        status_code=status.HTTP_401_UNAUTHORIZED,
                    )

                from app.external.cryptobot import CryptoBotService

                if not CryptoBotService().verify_webhook_signature(payload_text, signature):
                    return JSONResponse(
                        {"status": "error", "reason": "invalid_signature"},
                        status_code=status.HTTP_401_UNAUTHORIZED,
                    )

            success = await _process_payment_service_callback(
                payment_service,
                payload,
                "process_cryptobot_webhook",
            )
            if success:
                return JSONResponse({"status": "ok"})

            return JSONResponse(
                {"status": "error", "reason": "processing_failed"},
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        routes_registered = True

    if settings.is_yookassa_enabled():

        @router.options(settings.YOOKASSA_WEBHOOK_PATH)
        async def yookassa_options() -> Response:
            return Response(
                status_code=status.HTTP_200_OK,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type, X-YooKassa-Signature, Signature",
                },
            )

        @router.get(settings.YOOKASSA_WEBHOOK_PATH)
        async def yookassa_health() -> JSONResponse:
            return JSONResponse(
                {
                    "status": "ok",
                    "service": "yookassa_webhook",
                    "enabled": settings.is_yookassa_enabled(),
                }
            )

        @router.post(settings.YOOKASSA_WEBHOOK_PATH)
        async def yookassa_webhook(request: Request) -> JSONResponse:
            header_ip_candidates = yookassa_webhook_module.collect_yookassa_ip_candidates(
                request.headers.get("X-Forwarded-For"),
                request.headers.get("X-Real-IP"),
            )
            remote_ip = request.client.host if request.client else None
            client_ip = yookassa_webhook_module.resolve_yookassa_ip(
                header_ip_candidates,
                remote=remote_ip,
            )

            if client_ip is None:
                return JSONResponse(
                    {
                        "status": "error",
                        "reason": "unknown_ip",
                        "candidates": header_ip_candidates + ([remote_ip] if remote_ip else []),
                    },
                    status_code=status.HTTP_403_FORBIDDEN,
                )

            if not yookassa_webhook_module.is_yookassa_ip_allowed(client_ip):
                return JSONResponse(
                    {
                        "status": "error",
                        "reason": "forbidden_ip",
                        "ip": str(client_ip),
                    },
                    status_code=status.HTTP_403_FORBIDDEN,
                )

            body_bytes = await request.body()
            if not body_bytes:
                return JSONResponse({"status": "error", "reason": "empty_body"}, status_code=status.HTTP_400_BAD_REQUEST)

            body = body_bytes.decode("utf-8")

            try:
                webhook_data = json.loads(body)
            except json.JSONDecodeError:
                return JSONResponse(
                    {"status": "error", "reason": "invalid_json"},
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            event_type = webhook_data.get("event")
            if not event_type:
                return JSONResponse(
                    {"status": "error", "reason": "missing_event"},
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            if event_type not in {"payment.succeeded", "payment.waiting_for_capture"}:
                return JSONResponse({"status": "ok", "ignored": event_type})

            success = await _process_payment_service_callback(
                payment_service,
                webhook_data,
                "process_yookassa_webhook",
            )
            if success:
                return JSONResponse({"status": "ok"})

            return JSONResponse(
                {"status": "error", "reason": "processing_failed"},
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        routes_registered = True

    if settings.is_wata_enabled():
        wata_handler = WataWebhookHandler(payment_service)

        @router.options(settings.WATA_WEBHOOK_PATH)
        async def wata_options() -> Response:
            return Response(
                status_code=status.HTTP_200_OK,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type, X-Signature",
                },
            )

        @router.get(settings.WATA_WEBHOOK_PATH)
        async def wata_health() -> JSONResponse:
            return JSONResponse(
                {
                    "status": "ok",
                    "service": "wata_webhook",
                    "enabled": settings.is_wata_enabled(),
                }
            )

        @router.post(settings.WATA_WEBHOOK_PATH)
        async def wata_webhook(request: Request) -> JSONResponse:
            raw_body = await request.body()
            if not raw_body:
                return JSONResponse({"status": "error", "reason": "empty_body"}, status_code=status.HTTP_400_BAD_REQUEST)

            signature = request.headers.get("X-Signature") or ""
            if not await wata_handler._verify_signature(raw_body.decode("utf-8"), signature):  # type: ignore[attr-defined]
                return JSONResponse(
                    {"status": "error", "reason": "invalid_signature"},
                    status_code=status.HTTP_401_UNAUTHORIZED,
                )

            try:
                payload = json.loads(raw_body.decode("utf-8"))
            except json.JSONDecodeError:
                return JSONResponse(
                    {"status": "error", "reason": "invalid_json"},
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            success = await _process_payment_service_callback(
                payment_service,
                payload,
                "process_wata_webhook",
            )
            if success:
                return JSONResponse({"status": "ok"})

            return JSONResponse(
                {"status": "error", "reason": "not_processed"},
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        routes_registered = True

    if settings.is_heleket_enabled():
        heleket_handler = HeleketWebhookHandler(payment_service)

        @router.options(settings.HELEKET_WEBHOOK_PATH)
        async def heleket_options() -> Response:
            return Response(
                status_code=status.HTTP_200_OK,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type, Authorization",
                },
            )

        @router.get(settings.HELEKET_WEBHOOK_PATH)
        async def heleket_health() -> JSONResponse:
            return JSONResponse(
                {
                    "status": "ok",
                    "service": "heleket_webhook",
                    "enabled": settings.is_heleket_enabled(),
                }
            )

        @router.post(settings.HELEKET_WEBHOOK_PATH)
        async def heleket_webhook(request: Request) -> JSONResponse:
            try:
                payload = await request.json()
            except json.JSONDecodeError:
                return JSONResponse(
                    {"status": "error", "reason": "invalid_json"},
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            if not heleket_handler.service.verify_webhook_signature(payload):
                return JSONResponse(
                    {"status": "error", "reason": "invalid_signature"},
                    status_code=status.HTTP_401_UNAUTHORIZED,
                )

            success = await _process_payment_service_callback(
                payment_service,
                payload,
                "process_heleket_webhook",
            )
            if success:
                return JSONResponse({"status": "ok"})

            return JSONResponse(
                {"status": "error", "reason": "not_processed"},
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        routes_registered = True

    if settings.is_pal24_enabled():
        pal24_service = Pal24Service()

        @router.options(settings.PAL24_WEBHOOK_PATH)
        async def pal24_options() -> Response:
            return Response(
                status_code=status.HTTP_200_OK,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type",
                },
            )

        @router.get(settings.PAL24_WEBHOOK_PATH)
        async def pal24_health() -> JSONResponse:
            return JSONResponse(
                {
                    "status": "ok",
                    "service": "pal24_webhook",
                    "enabled": settings.is_pal24_enabled(),
                }
            )

        @router.post(settings.PAL24_WEBHOOK_PATH)
        async def pal24_webhook(request: Request) -> JSONResponse:
            if not pal24_service.is_configured:
                return JSONResponse(
                    {"status": "error", "reason": "service_not_configured"},
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

            payload = await _parse_pal24_payload(request)
            if not payload:
                return JSONResponse(
                    {"status": "error", "reason": "empty_payload"},
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            try:
                parsed_payload = pal24_service.parse_postback(payload)
            except Pal24APIError as error:
                return JSONResponse(
                    {"status": "error", "reason": str(error)},
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            success = await _process_payment_service_callback(
                payment_service,
                parsed_payload,
                "process_pal24_postback",
            )
            if success:
                return JSONResponse({"status": "ok"})

            return JSONResponse(
                {"status": "error", "reason": "not_processed"},
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        routes_registered = True

    if settings.is_platega_enabled():

        @router.get(settings.PLATEGA_WEBHOOK_PATH)
        async def platega_health() -> JSONResponse:
            return JSONResponse(
                {
                    "status": "ok",
                    "service": "platega_webhook",
                    "enabled": settings.is_platega_enabled(),
                }
            )

        @router.post(settings.PLATEGA_WEBHOOK_PATH)
        async def platega_webhook(request: Request) -> JSONResponse:
            merchant_id = request.headers.get("X-MerchantId", "")
            secret = request.headers.get("X-Secret", "")
            if (
                merchant_id != (settings.PLATEGA_MERCHANT_ID or "")
                or secret != (settings.PLATEGA_SECRET or "")
            ):
                return JSONResponse(
                    {"status": "error", "reason": "unauthorized"},
                    status_code=status.HTTP_401_UNAUTHORIZED,
                )

            try:
                payload = await request.json()
            except json.JSONDecodeError:
                return JSONResponse(
                    {"status": "error", "reason": "invalid_json"},
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            success = await _process_payment_service_callback(
                payment_service,
                payload,
                "process_platega_webhook",
            )
            if success:
                return JSONResponse({"status": "ok"})

            return JSONResponse(
                {"status": "error", "reason": "not_processed"},
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        routes_registered = True

    if routes_registered:
        @router.get("/health/payment-webhooks")
        async def payment_webhooks_health() -> JSONResponse:
            return JSONResponse(
                {
                    "status": "ok",
                    "tribute_enabled": settings.TRIBUTE_ENABLED,
                    "mulenpay_enabled": settings.is_mulenpay_enabled(),
                    "cryptobot_enabled": settings.is_cryptobot_enabled(),
                    "yookassa_enabled": settings.is_yookassa_enabled(),
                    "wata_enabled": settings.is_wata_enabled(),
                    "heleket_enabled": settings.is_heleket_enabled(),
                    "pal24_enabled": settings.is_pal24_enabled(),
                    "platega_enabled": settings.is_platega_enabled(),
                }
            )

    return router if routes_registered else None
