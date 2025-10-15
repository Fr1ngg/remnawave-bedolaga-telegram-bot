from __future__ import annotations

import logging
import math
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from typing import Any, Dict, List, Optional, Tuple, Union

from aiogram import Bot
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.user import get_user_by_telegram_id
from app.database.models import PaymentMethod, Transaction, TransactionType, User
from app.services.payment_service import PaymentService, get_wata_payment_by_link_id
from app.services.tribute_service import TributeService
from app.utils.currency_converter import currency_converter
from app.utils.telegram_webapp import TelegramWebAppAuthError, parse_webapp_init_data

from ...dependencies import get_db_session
from ...schemas.miniapp import (
    MiniAppPaymentCreateRequest,
    MiniAppPaymentCreateResponse,
    MiniAppPaymentMethod,
    MiniAppPaymentMethodsRequest,
    MiniAppPaymentMethodsResponse,
    MiniAppPaymentStatusQuery,
    MiniAppPaymentStatusRequest,
    MiniAppPaymentStatusResponse,
    MiniAppPaymentStatusResult,
)


logger = logging.getLogger(__name__)

router = APIRouter()


_CRYPTOBOT_MIN_USD = 1.0
_CRYPTOBOT_MAX_USD = 1000.0
_CRYPTOBOT_FALLBACK_RATE = 95.0

_DECIMAL_ONE_HUNDRED = Decimal(100)
_DECIMAL_CENT = Decimal("0.01")

_PAYMENT_SUCCESS_STATUSES = {
    "paid",
    "success",
    "succeeded",
    "completed",
    "captured",
    "done",
    "overpaid",
}
_PAYMENT_FAILURE_STATUSES = {
    "fail",
    "failed",
    "canceled",
    "cancelled",
    "declined",
    "expired",
    "rejected",
    "error",
    "refunded",
    "chargeback",
}


async def _resolve_user_from_init_data(
    db: AsyncSession,
    init_data: str,
) -> Tuple[User, Dict[str, Any]]:
    if not init_data:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Missing initData",
        )

    try:
        webapp_data = parse_webapp_init_data(init_data, settings.BOT_TOKEN)
    except TelegramWebAppAuthError as error:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
        ) from error

    telegram_user = webapp_data.get("user")
    if not isinstance(telegram_user, dict) or "id" not in telegram_user:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Invalid Telegram user payload",
        )

    try:
        telegram_id = int(telegram_user["id"])
    except (TypeError, ValueError):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Invalid Telegram user identifier",
        ) from None

    user = await get_user_by_telegram_id(db, telegram_id)
    if not user:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return user, webapp_data


def _normalize_amount_kopeks(
    amount_rubles: Optional[float],
    amount_kopeks: Optional[int],
) -> Optional[int]:
    if amount_kopeks is not None:
        try:
            normalized = int(amount_kopeks)
        except (TypeError, ValueError):
            return None
        return normalized if normalized >= 0 else None

    if amount_rubles is None:
        return None

    try:
        decimal_amount = Decimal(str(amount_rubles)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, ValueError):
        return None

    normalized = int((decimal_amount * 100).to_integral_value(rounding=ROUND_HALF_UP))
    return normalized if normalized >= 0 else None


def _build_balance_invoice_payload(user_id: int, amount_kopeks: int) -> str:
    suffix = uuid4().hex[:8]
    return f"balance_{user_id}_{amount_kopeks}_{suffix}"


def _current_request_timestamp() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _compute_stars_min_amount() -> Optional[int]:
    try:
        rate = Decimal(str(settings.get_stars_rate()))
    except (InvalidOperation, TypeError):
        return None

    if rate <= 0:
        return None

    return int((rate * _DECIMAL_ONE_HUNDRED).to_integral_value(rounding=ROUND_HALF_UP))


def _normalize_stars_amount(amount_kopeks: int) -> Tuple[int, int]:
    try:
        rate = Decimal(str(settings.get_stars_rate()))
    except (InvalidOperation, TypeError):
        raise ValueError("Stars rate is not configured")

    if rate <= 0:
        raise ValueError("Stars rate must be positive")

    amount_rubles = Decimal(amount_kopeks) / _DECIMAL_ONE_HUNDRED
    stars_amount = int((amount_rubles / rate).to_integral_value(rounding=ROUND_FLOOR))
    if stars_amount <= 0:
        stars_amount = 1

    normalized_rubles = (Decimal(stars_amount) * rate).quantize(
        _DECIMAL_CENT,
        rounding=ROUND_HALF_UP,
    )
    normalized_amount_kopeks = int(
        (normalized_rubles * _DECIMAL_ONE_HUNDRED).to_integral_value(
            rounding=ROUND_HALF_UP
        )
    )

    return stars_amount, normalized_amount_kopeks


async def _get_usd_to_rub_rate() -> float:
    try:
        rate = await currency_converter.get_usd_to_rub_rate()
    except Exception:
        rate = 0.0
    if not rate or rate <= 0:
        rate = _CRYPTOBOT_FALLBACK_RATE
    return float(rate)


def _compute_cryptobot_limits(rate: float) -> Tuple[int, int]:
    min_kopeks = max(1, int(math.ceil(rate * _CRYPTOBOT_MIN_USD * 100)))
    max_kopeks = int(math.floor(rate * _CRYPTOBOT_MAX_USD * 100))
    if max_kopeks < min_kopeks:
        max_kopeks = min_kopeks
    return min_kopeks, max_kopeks


def _parse_client_timestamp(value: Optional[Union[str, int, float]]) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            timestamp = float(value)
        except (TypeError, ValueError):
            return None
        if timestamp > 1e12:
            timestamp /= 1000.0
        try:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.isdigit():
            return _parse_client_timestamp(int(normalized))
        for suffix in ("Z", "z"):
            if normalized.endswith(suffix):
                normalized = normalized[:-1] + "+00:00"
                break
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    return None


async def _find_recent_deposit(
    db: AsyncSession,
    *,
    user_id: int,
    payment_method: PaymentMethod,
    amount_kopeks: Optional[int],
    started_at: Optional[datetime],
    tolerance: timedelta = timedelta(minutes=5),
) -> Optional[Transaction]:
    def _transaction_matches_started_at(
        transaction: Transaction,
        reference: Optional[datetime],
    ) -> bool:
        if not reference:
            return True
        timestamp = transaction.completed_at or transaction.created_at
        if not timestamp:
            return False
        if timestamp.tzinfo:
            timestamp = timestamp.astimezone(timezone.utc).replace(tzinfo=None)
        return timestamp >= reference

    query = (
        select(Transaction)
        .where(
            Transaction.user_id == user_id,
            Transaction.type == TransactionType.DEPOSIT.value,
            Transaction.payment_method == payment_method.value,
        )
        .order_by(Transaction.created_at.desc())
        .limit(1)
    )

    if amount_kopeks is not None:
        query = query.where(Transaction.amount_kopeks == amount_kopeks)
    if started_at:
        query = query.where(Transaction.created_at >= started_at - tolerance)

    result = await db.execute(query)
    transaction = result.scalar_one_or_none()

    if not transaction:
        return None

    if not _transaction_matches_started_at(transaction, started_at):
        return None

    return transaction


def _classify_status(status: Optional[str], is_paid: bool) -> str:
    if is_paid:
        return "paid"
    normalized = (status or "").strip().lower()
    if not normalized:
        return "pending"
    if normalized in _PAYMENT_SUCCESS_STATUSES:
        return "paid"
    if normalized in _PAYMENT_FAILURE_STATUSES:
        return "failed"
    return "pending"


@router.post(
    "/payments/methods",
    response_model=MiniAppPaymentMethodsResponse,
)
async def get_payment_methods(
    payload: MiniAppPaymentMethodsRequest,
    db: AsyncSession = Depends(get_db_session),
) -> MiniAppPaymentMethodsResponse:
    _, _ = await _resolve_user_from_init_data(db, payload.init_data)

    methods: List[MiniAppPaymentMethod] = []

    if settings.TELEGRAM_STARS_ENABLED:
        stars_min_amount = _compute_stars_min_amount()
        methods.append(
            MiniAppPaymentMethod(
                id="stars",
                icon="⭐",
                requires_amount=True,
                currency="RUB",
                min_amount_kopeks=stars_min_amount,
                amount_step_kopeks=stars_min_amount,
            )
        )

    if settings.is_yookassa_enabled():
        if getattr(settings, "YOOKASSA_SBP_ENABLED", False):
            methods.append(
                MiniAppPaymentMethod(
                    id="yookassa_sbp",
                    icon="🏦",
                    requires_amount=True,
                    currency="RUB",
                    min_amount_kopeks=settings.YOOKASSA_MIN_AMOUNT_KOPEKS,
                    max_amount_kopeks=settings.YOOKASSA_MAX_AMOUNT_KOPEKS,
                )
            )

        methods.append(
            MiniAppPaymentMethod(
                id="yookassa",
                icon="💳",
                requires_amount=True,
                currency="RUB",
                min_amount_kopeks=settings.YOOKASSA_MIN_AMOUNT_KOPEKS,
                max_amount_kopeks=settings.YOOKASSA_MAX_AMOUNT_KOPEKS,
            )
        )

    if settings.is_mulenpay_enabled():
        methods.append(
            MiniAppPaymentMethod(
                id="mulenpay",
                icon="💳",
                requires_amount=True,
                currency="RUB",
                min_amount_kopeks=settings.MULENPAY_MIN_AMOUNT_KOPEKS,
                max_amount_kopeks=settings.MULENPAY_MAX_AMOUNT_KOPEKS,
            )
        )

    if settings.is_pal24_enabled():
        methods.append(
            MiniAppPaymentMethod(
                id="pal24",
                icon="🏦",
                requires_amount=True,
                currency="RUB",
                min_amount_kopeks=settings.PAL24_MIN_AMOUNT_KOPEKS,
                max_amount_kopeks=settings.PAL24_MAX_AMOUNT_KOPEKS,
            )
        )

    if settings.is_wata_enabled():
        methods.append(
            MiniAppPaymentMethod(
                id="wata",
                icon="🌊",
                requires_amount=True,
                currency="RUB",
                min_amount_kopeks=settings.WATA_MIN_AMOUNT_KOPEKS,
                max_amount_kopeks=settings.WATA_MAX_AMOUNT_KOPEKS,
            )
        )

    if settings.is_cryptobot_enabled():
        rate = await _get_usd_to_rub_rate()
        min_amount_kopeks, max_amount_kopeks = _compute_cryptobot_limits(rate)
        methods.append(
            MiniAppPaymentMethod(
                id="cryptobot",
                icon="🪙",
                requires_amount=True,
                currency="RUB",
                min_amount_kopeks=min_amount_kopeks,
                max_amount_kopeks=max_amount_kopeks,
            )
        )

    if settings.TRIBUTE_ENABLED:
        methods.append(
            MiniAppPaymentMethod(
                id="tribute",
                icon="💎",
                requires_amount=False,
                currency="RUB",
            )
        )

    order_map = {
        "stars": 1,
        "yookassa_sbp": 2,
        "yookassa": 3,
        "mulenpay": 4,
        "pal24": 5,
        "wata": 6,
        "cryptobot": 7,
        "tribute": 8,
    }
    methods.sort(key=lambda item: order_map.get(item.id, 99))

    return MiniAppPaymentMethodsResponse(methods=methods)


@router.post(
    "/payments/create",
    response_model=MiniAppPaymentCreateResponse,
)
async def create_payment_link(
    payload: MiniAppPaymentCreateRequest,
    db: AsyncSession = Depends(get_db_session),
) -> MiniAppPaymentCreateResponse:
    user, _ = await _resolve_user_from_init_data(db, payload.init_data)

    method = (payload.method or "").strip().lower()
    if not method:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Payment method is required",
        )

    amount_kopeks = _normalize_amount_kopeks(
        payload.amount_rubles,
        payload.amount_kopeks,
    )

    if method == "stars":
        if not settings.TELEGRAM_STARS_ENABLED:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Payment method is unavailable")
        if amount_kopeks is None or amount_kopeks <= 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Amount must be positive")
        if not settings.BOT_TOKEN:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Bot token is not configured")

        requested_amount_kopeks = amount_kopeks
        try:
            stars_amount, amount_kopeks = _normalize_stars_amount(amount_kopeks)
        except ValueError as exc:
            logger.error("Failed to normalize Stars amount: %s", exc)
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to prepare Stars payment",
            ) from exc

        bot = Bot(token=settings.BOT_TOKEN)
        invoice_payload = _build_balance_invoice_payload(user.id, amount_kopeks)
        try:
            payment_service = PaymentService(bot)
            invoice_link = await payment_service.create_stars_invoice(
                amount_kopeks=amount_kopeks,
                description=settings.get_balance_payment_description(amount_kopeks),
                payload=invoice_payload,
                stars_amount=stars_amount,
            )
        finally:
            await bot.session.close()

        if not invoice_link:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Failed to create invoice")

        return MiniAppPaymentCreateResponse(
            method=method,
            payment_url=invoice_link,
            amount_kopeks=amount_kopeks,
            extra={
                "invoice_payload": invoice_payload,
                "requested_at": _current_request_timestamp(),
                "stars_amount": stars_amount,
                "requested_amount_kopeks": requested_amount_kopeks,
            },
        )

    if method == "yookassa_sbp":
        if not settings.is_yookassa_enabled() or not getattr(settings, "YOOKASSA_SBP_ENABLED", False):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Payment method is unavailable")
        if amount_kopeks is None or amount_kopeks <= 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Amount must be positive")
        if amount_kopeks < settings.YOOKASSA_MIN_AMOUNT_KOPEKS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Amount is below minimum")
        if amount_kopeks > settings.YOOKASSA_MAX_AMOUNT_KOPEKS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Amount exceeds maximum")

        payment_service = PaymentService()
        result = await payment_service.create_yookassa_sbp_payment(
            db=db,
            user_id=user.id,
            amount_kopeks=amount_kopeks,
            description=settings.get_balance_payment_description(amount_kopeks),
        )
        confirmation_url = result.get("confirmation_url") if result else None
        if not result or not confirmation_url:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Failed to create payment")

        extra: dict[str, Any] = {
            "local_payment_id": result.get("local_payment_id"),
            "payment_id": result.get("yookassa_payment_id"),
            "status": result.get("status"),
            "requested_at": _current_request_timestamp(),
        }
        confirmation_token = result.get("confirmation_token")
        if confirmation_token:
            extra["confirmation_token"] = confirmation_token

        return MiniAppPaymentCreateResponse(
            method=method,
            payment_url=confirmation_url,
            amount_kopeks=amount_kopeks,
            extra=extra,
        )

    if method == "yookassa":
        if not settings.is_yookassa_enabled():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Payment method is unavailable")
        if amount_kopeks is None or amount_kopeks <= 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Amount must be positive")
        if amount_kopeks < settings.YOOKASSA_MIN_AMOUNT_KOPEKS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Amount is below minimum")
        if amount_kopeks > settings.YOOKASSA_MAX_AMOUNT_KOPEKS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Amount exceeds maximum")

        payment_service = PaymentService()
        result = await payment_service.create_yookassa_payment(
            db=db,
            user_id=user.id,
            amount_kopeks=amount_kopeks,
            description=settings.get_balance_payment_description(amount_kopeks),
        )
        if not result or not result.get("confirmation_url"):
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Failed to create payment")

        return MiniAppPaymentCreateResponse(
            method=method,
            payment_url=result["confirmation_url"],
            amount_kopeks=amount_kopeks,
            extra={
                "local_payment_id": result.get("local_payment_id"),
                "payment_id": result.get("yookassa_payment_id"),
                "status": result.get("status"),
                "requested_at": _current_request_timestamp(),
            },
        )

    if method == "mulenpay":
        if not settings.is_mulenpay_enabled():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Payment method is unavailable")
        if amount_kopeks is None or amount_kopeks <= 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Amount must be positive")
        if amount_kopeks < settings.MULENPAY_MIN_AMOUNT_KOPEKS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Amount is below minimum")
        if amount_kopeks > settings.MULENPAY_MAX_AMOUNT_KOPEKS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Amount exceeds maximum")

        payment_service = PaymentService()
        result = await payment_service.create_mulenpay_payment(
            db=db,
            user_id=user.id,
            amount_kopeks=amount_kopeks,
            description=settings.get_balance_payment_description(amount_kopeks),
            language=user.language,
        )
        if not result or not result.get("payment_url"):
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Failed to create payment")

        return MiniAppPaymentCreateResponse(
            method=method,
            payment_url=result["payment_url"],
            amount_kopeks=amount_kopeks,
            extra={
                "local_payment_id": result.get("local_payment_id"),
                "payment_id": result.get("mulen_payment_id"),
                "requested_at": _current_request_timestamp(),
            },
        )

    if method == "wata":
        if not settings.is_wata_enabled():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Payment method is unavailable")
        if amount_kopeks is None or amount_kopeks <= 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Amount must be positive")
        if amount_kopeks < settings.WATA_MIN_AMOUNT_KOPEKS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Amount is below minimum")
        if amount_kopeks > settings.WATA_MAX_AMOUNT_KOPEKS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Amount exceeds maximum")

        payment_service = PaymentService()
        result = await payment_service.create_wata_payment(
            db=db,
            user_id=user.id,
            amount_kopeks=amount_kopeks,
            description=settings.get_balance_payment_description(amount_kopeks),
            language=user.language,
        )
        payment_url = result.get("payment_url") if result else None
        if not result or not payment_url:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Failed to create payment")

        return MiniAppPaymentCreateResponse(
            method=method,
            payment_url=payment_url,
            amount_kopeks=amount_kopeks,
            extra={
                "local_payment_id": result.get("local_payment_id"),
                "payment_link_id": result.get("payment_link_id"),
                "payment_id": result.get("payment_link_id"),
                "status": result.get("status"),
                "order_id": result.get("order_id"),
                "requested_at": _current_request_timestamp(),
            },
        )

    if method == "pal24":
        if not settings.is_pal24_enabled():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Payment method is unavailable")
        if amount_kopeks is None or amount_kopeks <= 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Amount must be positive")
        if amount_kopeks < settings.PAL24_MIN_AMOUNT_KOPEKS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Amount is below minimum")
        if amount_kopeks > settings.PAL24_MAX_AMOUNT_KOPEKS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Amount exceeds maximum")

        option = (payload.payment_option or "").strip().lower()
        if option not in {"card", "sbp"}:
            option = "sbp"
        provider_method = "CARD" if option == "card" else "SBP"

        payment_service = PaymentService()
        result = await payment_service.create_pal24_payment(
            db=db,
            user_id=user.id,
            amount_kopeks=amount_kopeks,
            description=settings.get_balance_payment_description(amount_kopeks),
            language=user.language or settings.DEFAULT_LANGUAGE,
            payment_method=provider_method,
        )
        if not result:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Failed to create payment")

        preferred_urls: List[Optional[str]] = []
        if option == "sbp":
            preferred_urls.append(result.get("sbp_url"))
        elif option == "card":
            preferred_urls.append(result.get("card_url"))
        preferred_urls.extend(
            [
                result.get("link_url"),
                result.get("link_page_url"),
                result.get("payment_url"),
                result.get("transfer_url"),
            ]
        )
        payment_url = next((url for url in preferred_urls if url), None)
        if not payment_url:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Failed to obtain payment url")

        return MiniAppPaymentCreateResponse(
            method=method,
            payment_url=payment_url,
            amount_kopeks=amount_kopeks,
            extra={
                "local_payment_id": result.get("local_payment_id"),
                "bill_id": result.get("bill_id"),
                "order_id": result.get("order_id"),
                "payment_method": result.get("payment_method") or provider_method,
                "sbp_url": result.get("sbp_url"),
                "card_url": result.get("card_url"),
                "link_url": result.get("link_url"),
                "link_page_url": result.get("link_page_url"),
                "transfer_url": result.get("transfer_url"),
                "selected_option": option,
                "requested_at": _current_request_timestamp(),
            },
        )

    if method == "cryptobot":
        if not settings.is_cryptobot_enabled():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Payment method is unavailable")
        if amount_kopeks is None or amount_kopeks <= 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Amount must be positive")
        rate = await _get_usd_to_rub_rate()
        min_amount_kopeks, max_amount_kopeks = _compute_cryptobot_limits(rate)
        if amount_kopeks < min_amount_kopeks:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"Amount is below minimum ({min_amount_kopeks / 100:.2f} RUB)",
            )
        if amount_kopeks > max_amount_kopeks:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"Amount exceeds maximum ({max_amount_kopeks / 100:.2f} RUB)",
            )

        try:
            amount_usd = float(
                (Decimal(amount_kopeks) / Decimal(100) / Decimal(str(rate)))
                .quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            )
        except (InvalidOperation, ValueError):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="Unable to convert amount to USD",
            )

        payment_service = PaymentService()
        result = await payment_service.create_cryptobot_payment(
            db=db,
            user_id=user.id,
            amount_usd=amount_usd,
            asset=settings.CRYPTOBOT_DEFAULT_ASSET,
            description=settings.get_balance_payment_description(amount_kopeks),
            payload=f"balance_{user.id}_{amount_kopeks}",
        )
        if not result:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Failed to create payment")

        payment_url = (
            result.get("bot_invoice_url")
            or result.get("mini_app_invoice_url")
            or result.get("web_app_invoice_url")
        )
        if not payment_url:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Failed to obtain payment url")

        return MiniAppPaymentCreateResponse(
            method=method,
            payment_url=payment_url,
            amount_kopeks=amount_kopeks,
            extra={
                "local_payment_id": result.get("local_payment_id"),
                "invoice_id": result.get("invoice_id"),
                "amount_usd": amount_usd,
                "rate": rate,
                "requested_at": _current_request_timestamp(),
            },
        )

    if method == "tribute":
        if not settings.TRIBUTE_ENABLED:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Payment method is unavailable")
        if not settings.BOT_TOKEN:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Bot token is not configured")

        bot = Bot(token=settings.BOT_TOKEN)
        try:
            tribute_service = TributeService(bot)
            payment_url = await tribute_service.create_payment_link(
                user_id=user.telegram_id,
                amount_kopeks=amount_kopeks or 0,
                description=settings.get_balance_payment_description(amount_kopeks or 0),
            )
        finally:
            await bot.session.close()

        if not payment_url:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Failed to create payment")

        return MiniAppPaymentCreateResponse(
            method=method,
            payment_url=payment_url,
            amount_kopeks=amount_kopeks,
            extra={
                "requested_at": _current_request_timestamp(),
            },
        )

    raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Unknown payment method")


@router.post(
    "/payments/status",
    response_model=MiniAppPaymentStatusResponse,
)
async def get_payment_statuses(
    payload: MiniAppPaymentStatusRequest,
    db: AsyncSession = Depends(get_db_session),
) -> MiniAppPaymentStatusResponse:
    user, _ = await _resolve_user_from_init_data(db, payload.init_data)

    entries = payload.payments or []
    if not entries:
        return MiniAppPaymentStatusResponse(results=[])

    payment_service = PaymentService()
    results: List[MiniAppPaymentStatusResult] = []

    for entry in entries:
        result = await _resolve_payment_status_entry(
            payment_service=payment_service,
            db=db,
            user=user,
            query=entry,
        )
        if result:
            results.append(result)

    return MiniAppPaymentStatusResponse(results=results)


async def _resolve_payment_status_entry(
    *,
    payment_service: PaymentService,
    db: AsyncSession,
    user: User,
    query: MiniAppPaymentStatusQuery,
) -> MiniAppPaymentStatusResult:
    method = (query.method or "").strip().lower()
    if not method:
        return MiniAppPaymentStatusResult(
            method="",
            status="unknown",
            message="Payment method is required",
        )

    if method in {"yookassa", "yookassa_sbp"}:
        return await _resolve_yookassa_payment_status(
            db,
            user,
            query,
            method=method,
        )
    if method == "mulenpay":
        return await _resolve_mulenpay_payment_status(payment_service, db, user, query)
    if method == "wata":
        return await _resolve_wata_payment_status(payment_service, db, user, query)
    if method == "pal24":
        return await _resolve_pal24_payment_status(payment_service, db, user, query)
    if method == "cryptobot":
        return await _resolve_cryptobot_payment_status(db, user, query)
    if method == "stars":
        return await _resolve_stars_payment_status(db, user, query)
    if method == "tribute":
        return await _resolve_tribute_payment_status(db, user, query)

    return MiniAppPaymentStatusResult(
        method=method,
        status="unknown",
        message="Unsupported payment method",
    )


async def _resolve_yookassa_payment_status(
    db: AsyncSession,
    user: User,
    query: MiniAppPaymentStatusQuery,
    *,
    method: str = "yookassa",
) -> MiniAppPaymentStatusResult:
    from app.database.crud.yookassa import (
        get_yookassa_payment_by_id,
        get_yookassa_payment_by_local_id,
    )

    payment = None
    if query.local_payment_id:
        payment = await get_yookassa_payment_by_local_id(db, query.local_payment_id)
    if not payment and query.payment_id:
        payment = await get_yookassa_payment_by_id(db, query.payment_id)

    if not payment or payment.user_id != user.id:
        return MiniAppPaymentStatusResult(
            method=method,
            status="pending",
            is_paid=False,
            amount_kopeks=query.amount_kopeks,
            message="Payment not found",
            extra={
                "local_payment_id": query.local_payment_id,
                "payment_id": query.payment_id,
                "invoice_id": query.payment_id,
                "payload": query.payload,
                "started_at": query.started_at,
            },
        )

    succeeded = bool(payment.is_paid and (payment.status or "").lower() == "succeeded")
    status_value = _classify_status(payment.status, succeeded)
    completed_at = payment.captured_at or payment.updated_at or payment.created_at

    return MiniAppPaymentStatusResult(
        method=method,
        status=status_value,
        is_paid=status_value == "paid",
        amount_kopeks=payment.amount_kopeks,
        currency=payment.currency,
        completed_at=completed_at,
        transaction_id=payment.transaction_id,
        external_id=payment.yookassa_payment_id,
        extra={
            "status": payment.status,
            "is_paid": payment.is_paid,
            "local_payment_id": payment.id,
            "payment_id": payment.yookassa_payment_id,
            "invoice_id": payment.yookassa_payment_id,
            "payload": query.payload,
            "started_at": query.started_at,
        },
    )


async def _resolve_mulenpay_payment_status(
    payment_service: PaymentService,
    db: AsyncSession,
    user: User,
    query: MiniAppPaymentStatusQuery,
) -> MiniAppPaymentStatusResult:
    if not query.local_payment_id:
        return MiniAppPaymentStatusResult(
            method="mulenpay",
            status="pending",
            is_paid=False,
            amount_kopeks=query.amount_kopeks,
            message="Missing payment identifier",
            extra={
                "local_payment_id": query.local_payment_id,
                "bill_id": query.invoice_id,
                "order_id": None,
                "payload": query.payload,
                "started_at": query.started_at,
            },
        )

    status_info = await payment_service.get_mulenpay_payment_status(
        db,
        query.local_payment_id,
    )
    payment = status_info.get("payment") if status_info else None

    if not payment or payment.user_id != user.id:
        return MiniAppPaymentStatusResult(
            method="mulenpay",
            status="pending",
            is_paid=False,
            amount_kopeks=query.amount_kopeks,
            message="Payment not found",
            extra={
                "local_payment_id": query.local_payment_id,
                "bill_id": query.invoice_id,
                "order_id": None,
                "payload": query.payload,
                "started_at": query.started_at,
            },
        )

    status_raw = status_info.get("status") or payment.status
    is_paid = bool(payment.is_paid)
    status_value = _classify_status(status_raw, is_paid)
    completed_at = payment.paid_at or payment.updated_at or payment.created_at
    message = None
    if status_value == "failed":
        remote_status = status_info.get("remote_status") or status_raw
        if remote_status:
            message = f"Status: {remote_status}"

    return MiniAppPaymentStatusResult(
        method="mulenpay",
        status=status_value,
        is_paid=status_value == "paid",
        amount_kopeks=payment.amount_kopeks,
        currency=payment.currency,
        completed_at=completed_at,
        transaction_id=payment.transaction_id,
        external_id=payment.payment_link_id,
        message=message,
        extra={
            "status": payment.status,
            "remote_status": status_info.get("remote_status"),
            "local_payment_id": payment.id,
            "bill_id": payment.bill_id,
            "order_id": payment.order_id,
            "payment_method": getattr(payment, "payment_method", None),
            "payload": query.payload,
            "started_at": query.started_at,
        },
    )


async def _resolve_wata_payment_status(
    payment_service: PaymentService,
    db: AsyncSession,
    user: User,
    query: MiniAppPaymentStatusQuery,
) -> MiniAppPaymentStatusResult:
    async def _maybe_get_payment_by_link_id(link_id: Optional[str | int]):
        if not link_id:
            return None
        try:
            return await get_wata_payment_by_link_id(db, link_id)
        except AttributeError:
            # Tests may call the resolver with ``db=None``. In that case the real
            # CRUD helper would try to access ``db.execute`` on ``None``. Swallow
            # the error so that monkeypatched helpers (used in tests) can still
            # be invoked while production code keeps the original behaviour when
            # a real session is supplied.
            if db is None:
                return None
            raise

    local_id = query.local_payment_id
    payment_link_id = query.payment_link_id or query.payment_id or query.invoice_id
    fallback_payment = None

    if not local_id and payment_link_id:
        fallback_payment = await _maybe_get_payment_by_link_id(payment_link_id)
        if fallback_payment:
            local_id = fallback_payment.id

    if not local_id:
        return MiniAppPaymentStatusResult(
            method="wata",
            status="pending",
            is_paid=False,
            amount_kopeks=query.amount_kopeks,
            message="Missing payment identifier",
            extra={
                "local_payment_id": query.local_payment_id,
                "payment_link_id": payment_link_id,
                "payment_id": query.payment_id,
                "invoice_id": query.invoice_id,
                "payload": query.payload,
                "started_at": query.started_at,
            },
        )

    status_info = await payment_service.get_wata_payment_status(db, local_id)
    payment = (status_info or {}).get("payment") or fallback_payment

    if not payment or payment.user_id != user.id:
        return MiniAppPaymentStatusResult(
            method="wata",
            status="pending",
            is_paid=False,
            amount_kopeks=query.amount_kopeks,
            message="Payment not found",
            extra={
                "local_payment_id": local_id,
                "payment_link_id": (payment_link_id or getattr(payment, "payment_link_id", None)),
                "payment_id": query.payment_id,
                "invoice_id": query.invoice_id,
                "payload": query.payload,
                "started_at": query.started_at,
            },
        )

    remote_link = (status_info or {}).get("remote_link") if status_info else None
    transaction_payload = (status_info or {}).get("transaction") if status_info else None
    status_raw = (status_info or {}).get("status") or getattr(payment, "status", None)
    is_paid_flag = bool((status_info or {}).get("is_paid") or getattr(payment, "is_paid", False))
    status_value = _classify_status(status_raw, is_paid_flag)
    completed_at = (
        getattr(payment, "paid_at", None)
        or getattr(payment, "updated_at", None)
        or getattr(payment, "created_at", None)
    )

    message = None
    if status_value == "failed":
        message = (
            (transaction_payload or {}).get("errorDescription")
            or (transaction_payload or {}).get("errorCode")
            or (remote_link or {}).get("status")
        )

    extra: Dict[str, Any] = {
        "local_payment_id": payment.id,
        "payment_link_id": payment.payment_link_id,
        "payment_id": payment.payment_link_id,
        "status": status_raw,
        "is_paid": getattr(payment, "is_paid", False),
        "order_id": getattr(payment, "order_id", None),
        "payload": query.payload,
        "started_at": query.started_at,
    }
    if remote_link:
        extra["remote_link"] = remote_link
    if transaction_payload:
        extra["transaction"] = transaction_payload

    return MiniAppPaymentStatusResult(
        method="wata",
        status=status_value,
        is_paid=status_value == "paid",
        amount_kopeks=payment.amount_kopeks,
        currency=payment.currency,
        completed_at=completed_at,
        transaction_id=payment.transaction_id,
        external_id=payment.payment_link_id,
        message=message,
        extra=extra,
    )


async def _resolve_pal24_payment_status(
    payment_service: PaymentService,
    db: AsyncSession,
    user: User,
    query: MiniAppPaymentStatusQuery,
) -> MiniAppPaymentStatusResult:
    from app.database.crud.pal24 import get_pal24_payment_by_bill_id

    local_id = query.local_payment_id
    if not local_id and query.invoice_id:
        payment_by_bill = await get_pal24_payment_by_bill_id(db, query.invoice_id)
        if payment_by_bill and payment_by_bill.user_id == user.id:
            local_id = payment_by_bill.id

    if not local_id:
        return MiniAppPaymentStatusResult(
            method="pal24",
            status="pending",
            is_paid=False,
            amount_kopeks=query.amount_kopeks,
            message="Missing payment identifier",
            extra={
                "local_payment_id": query.local_payment_id,
                "bill_id": query.invoice_id,
                "order_id": None,
                "payload": query.payload,
                "started_at": query.started_at,
            },
        )

    status_info = await payment_service.get_pal24_payment_status(db, local_id)
    payment = status_info.get("payment") if status_info else None

    if not payment or payment.user_id != user.id:
        return MiniAppPaymentStatusResult(
            method="pal24",
            status="pending",
            is_paid=False,
            amount_kopeks=query.amount_kopeks,
            message="Payment not found",
            extra={
                "local_payment_id": local_id,
                "bill_id": query.invoice_id,
                "order_id": None,
                "payload": query.payload,
                "started_at": query.started_at,
            },
        )

    status_raw = status_info.get("status") or payment.status
    is_paid = bool(payment.is_paid)
    status_value = _classify_status(status_raw, is_paid)
    completed_at = payment.paid_at or payment.updated_at or payment.created_at
    message = None
    if status_value == "failed":
        remote_status = status_info.get("remote_status") or status_raw
        if remote_status:
            message = f"Status: {remote_status}"

    return MiniAppPaymentStatusResult(
        method="pal24",
        status=status_value,
        is_paid=status_value == "paid",
        amount_kopeks=payment.amount_kopeks,
        currency=payment.currency,
        completed_at=completed_at,
        transaction_id=payment.transaction_id,
        external_id=payment.bill_id,
        message=message,
        extra={
            "status": payment.status,
            "remote_status": status_info.get("remote_status"),
            "local_payment_id": payment.id,
            "bill_id": payment.bill_id,
            "order_id": payment.order_id,
            "payment_method": getattr(payment, "payment_method", None),
            "payload": query.payload,
            "started_at": query.started_at,
        },
    )


async def _resolve_cryptobot_payment_status(
    db: AsyncSession,
    user: User,
    query: MiniAppPaymentStatusQuery,
) -> MiniAppPaymentStatusResult:
    from app.database.crud.cryptobot import (
        get_cryptobot_payment_by_id,
        get_cryptobot_payment_by_invoice_id,
    )

    payment = None
    if query.local_payment_id:
        payment = await get_cryptobot_payment_by_id(db, query.local_payment_id)
    if not payment and query.invoice_id:
        payment = await get_cryptobot_payment_by_invoice_id(db, query.invoice_id)

    if not payment or payment.user_id != user.id:
        return MiniAppPaymentStatusResult(
            method="cryptobot",
            status="pending",
            is_paid=False,
            amount_kopeks=query.amount_kopeks,
            message="Payment not found",
            extra={
                "local_payment_id": query.local_payment_id,
                "invoice_id": query.invoice_id,
                "payment_id": query.payment_id,
                "payload": query.payload,
                "started_at": query.started_at,
            },
        )

    status_raw = payment.status
    is_paid = bool(payment.is_paid)
    status_value = _classify_status(status_raw, is_paid)
    completed_at = payment.paid_at or payment.updated_at or payment.created_at
    amount_kopeks = payment.amount_rub_kopeks or query.amount_kopeks

    return MiniAppPaymentStatusResult(
        method="cryptobot",
        status=status_value,
        is_paid=status_value == "paid",
        amount_kopeks=amount_kopeks,
        currency=payment.asset,
        completed_at=completed_at,
        transaction_id=payment.transaction_id,
        external_id=payment.invoice_id,
        extra={
            "status": payment.status,
            "asset": payment.asset,
            "local_payment_id": payment.id,
            "invoice_id": payment.invoice_id,
            "payload": query.payload,
            "started_at": query.started_at,
        },
    )


async def _resolve_stars_payment_status(
    db: AsyncSession,
    user: User,
    query: MiniAppPaymentStatusQuery,
) -> MiniAppPaymentStatusResult:
    started_at = _parse_client_timestamp(query.started_at)
    transaction = await _find_recent_deposit(
        db,
        user_id=user.id,
        payment_method=PaymentMethod.TELEGRAM_STARS,
        amount_kopeks=query.amount_kopeks,
        started_at=started_at,
    )

    if not transaction:
        return MiniAppPaymentStatusResult(
            method="stars",
            status="pending",
            is_paid=False,
            amount_kopeks=query.amount_kopeks,
            message="Waiting for confirmation",
            extra={
                "payload": query.payload,
                "started_at": query.started_at,
            },
        )

    return MiniAppPaymentStatusResult(
        method="stars",
        status="paid",
        is_paid=True,
        amount_kopeks=transaction.amount_kopeks,
        currency="RUB",
        completed_at=transaction.completed_at or transaction.created_at,
        transaction_id=transaction.id,
        external_id=transaction.external_id,
        extra={
            "payload": query.payload,
            "started_at": query.started_at,
        },
    )


async def _resolve_tribute_payment_status(
    db: AsyncSession,
    user: User,
    query: MiniAppPaymentStatusQuery,
) -> MiniAppPaymentStatusResult:
    started_at = _parse_client_timestamp(query.started_at)
    transaction = await _find_recent_deposit(
        db,
        user_id=user.id,
        payment_method=PaymentMethod.TRIBUTE,
        amount_kopeks=query.amount_kopeks,
        started_at=started_at,
    )

    if not transaction:
        return MiniAppPaymentStatusResult(
            method="tribute",
            status="pending",
            is_paid=False,
            amount_kopeks=query.amount_kopeks,
            message="Waiting for confirmation",
            extra={
                "payload": query.payload,
                "started_at": query.started_at,
            },
        )

    return MiniAppPaymentStatusResult(
        method="tribute",
        status="paid",
        is_paid=True,
        amount_kopeks=transaction.amount_kopeks,
        currency="RUB",
        completed_at=transaction.completed_at or transaction.created_at,
        transaction_id=transaction.id,
        external_id=transaction.external_id,
        extra={
            "payload": query.payload,
            "started_at": query.started_at,
        },
    )


__all__ = ["router"]
