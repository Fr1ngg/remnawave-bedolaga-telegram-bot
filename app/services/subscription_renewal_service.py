from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional
from uuid import uuid4

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.server_squad import get_server_ids_by_uuids
from app.database.crud.subscription import (
    add_subscription_servers,
    calculate_subscription_total_cost,
    extend_subscription,
)
from app.database.crud.transaction import create_transaction
from app.database.crud.user import subtract_user_balance
from app.database.models import PaymentMethod, Subscription, Transaction, TransactionType, User
from app.services.admin_notification_service import AdminNotificationService
from app.services.remnawave_service import RemnaWaveConfigurationError
from app.services.subscription_service import SubscriptionService
from app.utils.pricing_utils import (
    apply_percentage_discount,
    calculate_months_from_days,
    format_period_description,
    validate_pricing_calculation,
)

logger = logging.getLogger(__name__)


class SubscriptionRenewalError(Exception):
    """Base class for subscription renewal related errors."""


class SubscriptionRenewalChargeError(SubscriptionRenewalError):
    """Raised when the balance charge step fails."""


@dataclass(slots=True)
class SubscriptionRenewalPricing:
    period_days: int
    period_id: str
    months: int
    base_original_total: int
    discounted_total: int
    final_total: int
    promo_discount_value: int
    promo_discount_percent: int
    overall_discount_percent: int
    per_month: int
    server_ids: List[int]
    details: Dict[str, Any]

    def to_payload(self) -> Dict[str, Any]:
        return {
            "period_id": self.period_id,
            "period_days": self.period_days,
            "months": self.months,
            "base_original_total": self.base_original_total,
            "discounted_total": self.discounted_total,
            "final_total": self.final_total,
            "promo_discount_value": self.promo_discount_value,
            "promo_discount_percent": self.promo_discount_percent,
            "overall_discount_percent": self.overall_discount_percent,
            "per_month": self.per_month,
            "server_ids": list(self.server_ids),
            "details": dict(self.details),
        }


@dataclass(slots=True)
class SubscriptionRenewalResult:
    subscription: Subscription
    transaction: Optional[Transaction]
    total_amount_kopeks: int
    charged_from_balance_kopeks: int
    old_end_date: Optional[datetime]


@dataclass(slots=True)
class RenewalPaymentDescriptor:
    user_id: int
    subscription_id: int
    period_days: int
    total_amount_kopeks: int
    missing_amount_kopeks: int
    payload_id: str

    @property
    def balance_component_kopeks(self) -> int:
        remaining = self.total_amount_kopeks - self.missing_amount_kopeks
        return max(0, remaining)


_PAYLOAD_PREFIX = "subscription_renewal"


def build_renewal_period_id(period_days: int) -> str:
    return f"days:{period_days}"


def build_payment_descriptor(
    user_id: int,
    subscription_id: int,
    period_days: int,
    total_amount_kopeks: int,
    missing_amount_kopeks: int,
) -> RenewalPaymentDescriptor:
    return RenewalPaymentDescriptor(
        user_id=user_id,
        subscription_id=subscription_id,
        period_days=period_days,
        total_amount_kopeks=max(0, int(total_amount_kopeks)),
        missing_amount_kopeks=max(0, int(missing_amount_kopeks)),
        payload_id=uuid4().hex[:8],
    )


def encode_payment_payload(descriptor: RenewalPaymentDescriptor) -> str:
    return (
        f"{_PAYLOAD_PREFIX}|{descriptor.user_id}|{descriptor.subscription_id}|"
        f"{descriptor.period_days}|{descriptor.total_amount_kopeks}|"
        f"{descriptor.missing_amount_kopeks}|{descriptor.payload_id}"
    )


def decode_payment_payload(payload: str, expected_user_id: Optional[int] = None) -> Optional[RenewalPaymentDescriptor]:
    if not payload or not payload.startswith(f"{_PAYLOAD_PREFIX}|"):
        return None

    parts = payload.split("|")
    if len(parts) < 7:
        return None

    try:
        _, user_id_raw, subscription_raw, period_raw, total_raw, missing_raw, payload_id = parts[:7]
        user_id = int(user_id_raw)
        subscription_id = int(subscription_raw)
        period_days = int(period_raw)
        total_amount = int(total_raw)
        missing_amount = int(missing_raw)
    except (TypeError, ValueError):
        return None

    if expected_user_id is not None and user_id != expected_user_id:
        return None

    return RenewalPaymentDescriptor(
        user_id=user_id,
        subscription_id=subscription_id,
        period_days=period_days,
        total_amount_kopeks=max(0, total_amount),
        missing_amount_kopeks=max(0, missing_amount),
        payload_id=payload_id,
    )


def build_payment_metadata(descriptor: RenewalPaymentDescriptor) -> Dict[str, Any]:
    return {
        "payment_purpose": _PAYLOAD_PREFIX,
        "subscription_id": str(descriptor.subscription_id),
        "period_days": str(descriptor.period_days),
        "total_amount_kopeks": str(descriptor.total_amount_kopeks),
        "missing_amount_kopeks": str(descriptor.missing_amount_kopeks),
        "payload_id": descriptor.payload_id,
    }


def parse_payment_metadata(
    metadata: Optional[Dict[str, Any]],
    *,
    expected_user_id: Optional[int] = None,
) -> Optional[RenewalPaymentDescriptor]:
    if not metadata:
        return None

    if metadata.get("payment_purpose") != _PAYLOAD_PREFIX:
        return None

    try:
        subscription_id = int(metadata.get("subscription_id"))
        period_days = int(metadata.get("period_days"))
        total_amount = int(metadata.get("total_amount_kopeks"))
        missing_amount = int(metadata.get("missing_amount_kopeks"))
    except (TypeError, ValueError):
        return None

    payload_id = str(metadata.get("payload_id") or "")
    user_id = metadata.get("user_id")
    if user_id is not None:
        try:
            user_id_int = int(user_id)
        except (TypeError, ValueError):
            user_id_int = None
    else:
        user_id_int = None

    if expected_user_id is not None and user_id_int is not None and user_id_int != expected_user_id:
        return None

    return RenewalPaymentDescriptor(
        user_id=user_id_int or expected_user_id or 0,
        subscription_id=subscription_id,
        period_days=period_days,
        total_amount_kopeks=max(0, total_amount),
        missing_amount_kopeks=max(0, missing_amount),
        payload_id=payload_id,
    )


async def with_admin_notification_service(
    handler: Callable[[AdminNotificationService], Awaitable[Any]],
) -> None:
    if not getattr(settings, "ADMIN_NOTIFICATIONS_ENABLED", False):
        return
    if not settings.BOT_TOKEN:
        logger.debug("Skipping admin notification: bot token is not configured")
        return

    bot: Bot | None = None
    try:
        bot = Bot(token=settings.BOT_TOKEN)
        service = AdminNotificationService(bot)
        await handler(service)
    except Exception as error:  # pragma: no cover - defensive logging
        logger.error("Failed to send admin notification from renewal service: %s", error)
    finally:
        if bot is not None:
            await bot.session.close()


class SubscriptionRenewalService:
    """Shared helpers for subscription renewal pricing and processing."""

    async def calculate_pricing(
        self,
        db: AsyncSession,
        user: User,
        subscription: Subscription,
        period_days: int,
    ) -> SubscriptionRenewalPricing:
        connected_uuids = [str(uuid) for uuid in list(subscription.connected_squads or [])]
        server_ids: List[int] = []
        if connected_uuids:
            server_ids = await get_server_ids_by_uuids(db, connected_uuids)

        traffic_limit = subscription.traffic_limit_gb
        if traffic_limit is None:
            traffic_limit = settings.DEFAULT_TRAFFIC_LIMIT_GB

        devices_limit = subscription.device_limit
        if devices_limit is None:
            devices_limit = settings.DEFAULT_DEVICE_LIMIT

        total_cost, details = await calculate_subscription_total_cost(
            db,
            period_days,
            int(traffic_limit or 0),
            server_ids,
            int(devices_limit or 0),
            user=user,
        )

        months = details.get("months_in_period") or calculate_months_from_days(period_days)

        base_original_total = (
            details.get("base_price_original", 0)
            + details.get("traffic_price_per_month", 0) * months
            + details.get("servers_price_per_month", 0) * months
            + details.get("devices_price_per_month", 0) * months
        )

        discounted_total = total_cost

        monthly_additions = 0
        if months > 0:
            monthly_additions = (
                details.get("total_servers_price", 0) // months
                + details.get("total_devices_price", 0) // months
                + details.get("total_traffic_price", 0) // months
            )

        if not validate_pricing_calculation(
            details.get("base_price", 0),
            monthly_additions,
            months,
            discounted_total,
        ):
            logger.warning(
                "Renewal pricing validation failed for subscription %s (period %s)",
                subscription.id,
                period_days,
            )

        from app.utils.promo_offer import get_user_active_promo_discount_percent

        promo_percent = get_user_active_promo_discount_percent(user)

        final_total = discounted_total
        promo_discount_value = 0
        if promo_percent > 0 and discounted_total > 0:
            final_total, promo_discount_value = apply_percentage_discount(
                discounted_total,
                promo_percent,
            )

        overall_discount_value = max(0, base_original_total - final_total)
        overall_discount_percent = 0
        if base_original_total > 0 and overall_discount_value > 0:
            overall_discount_percent = int(
                round(overall_discount_value * 100 / base_original_total)
            )

        per_month = final_total // months if months else final_total

        return SubscriptionRenewalPricing(
            period_days=period_days,
            period_id=build_renewal_period_id(period_days),
            months=months,
            base_original_total=base_original_total,
            discounted_total=discounted_total,
            final_total=final_total,
            promo_discount_value=promo_discount_value,
            promo_discount_percent=promo_percent if promo_discount_value else 0,
            overall_discount_percent=overall_discount_percent,
            per_month=per_month,
            server_ids=list(server_ids),
            details=details,
        )

    async def finalize(
        self,
        db: AsyncSession,
        user: User,
        subscription: Subscription,
        pricing: SubscriptionRenewalPricing,
        *,
        charge_balance_amount: Optional[int] = None,
        description: Optional[str] = None,
        payment_method: Optional[PaymentMethod] = None,
    ) -> SubscriptionRenewalResult:
        final_total = int(pricing.final_total)
        if final_total < 0:
            final_total = 0

        period_days = int(pricing.period_days)
        charge_from_balance = charge_balance_amount
        if charge_from_balance is None:
            charge_from_balance = final_total
        charge_from_balance = max(0, min(charge_from_balance, final_total))

        consume_promo_offer = bool(pricing.promo_discount_value)

        description_text = description or f"Продление подписки на {period_days} дней"

        if charge_from_balance > 0 or consume_promo_offer:
            success = await subtract_user_balance(
                db,
                user,
                charge_from_balance,
                description_text,
                consume_promo_offer=consume_promo_offer,
            )
            if not success:
                raise SubscriptionRenewalChargeError("Failed to charge balance")
            await db.refresh(user)

        subscription_before = subscription
        old_end_date = subscription_before.end_date

        subscription_after = await extend_subscription(db, subscription_before, period_days)

        server_ids = pricing.server_ids or []
        server_prices_for_period = pricing.details.get("servers_individual_prices", [])
        if server_ids:
            try:
                await add_subscription_servers(
                    db,
                    subscription_after,
                    server_ids,
                    server_prices_for_period,
                )
            except Exception as error:  # pragma: no cover - defensive logging
                logger.warning(
                    "Failed to record renewal server prices for subscription %s: %s",
                    subscription_after.id,
                    error,
                )

        subscription_service = SubscriptionService()
        try:
            await subscription_service.update_remnawave_user(
                db,
                subscription_after,
                reset_traffic=settings.RESET_TRAFFIC_ON_PAYMENT,
                reset_reason="subscription renewal",
            )
        except RemnaWaveConfigurationError as error:  # pragma: no cover - configuration issues
            logger.warning("RemnaWave update skipped: %s", error)
        except Exception as error:  # pragma: no cover - defensive logging
            logger.error(
                "Failed to update RemnaWave user for subscription %s: %s",
                subscription_after.id,
                error,
            )

        transaction: Optional[Transaction] = None
        try:
            transaction = await create_transaction(
                db=db,
                user_id=user.id,
                type=TransactionType.SUBSCRIPTION_PAYMENT,
                amount_kopeks=final_total,
                description=description_text,
                payment_method=payment_method,
            )
        except Exception as error:  # pragma: no cover - defensive logging
            logger.warning(
                "Failed to create renewal transaction for subscription %s: %s",
                subscription_after.id,
                error,
            )

        await db.refresh(user)
        await db.refresh(subscription_after)

        if transaction and old_end_date and subscription_after.end_date:
            await with_admin_notification_service(
                lambda service: service.send_subscription_extension_notification(
                    db,
                    user,
                    subscription_after,
                    transaction,
                    period_days,
                    old_end_date,
                    new_end_date=subscription_after.end_date,
                    balance_after=user.balance_kopeks,
                )
            )

        return SubscriptionRenewalResult(
            subscription=subscription_after,
            transaction=transaction,
            total_amount_kopeks=final_total,
            charged_from_balance_kopeks=charge_from_balance,
            old_end_date=old_end_date,
        )

    def build_option_payload(
        self,
        pricing: SubscriptionRenewalPricing,
        *,
        language: str,
    ) -> Dict[str, Any]:
        label = format_period_description(pricing.period_days, language)
        price_label = settings.format_price(pricing.final_total)
        original_label = None
        if (
            pricing.base_original_total
            and pricing.base_original_total != pricing.final_total
        ):
            original_label = settings.format_price(pricing.base_original_total)

        per_month_label = settings.format_price(pricing.per_month)

        payload = {
            "id": pricing.period_id,
            "days": pricing.period_days,
            "months": pricing.months,
            "price_kopeks": pricing.final_total,
            "price_label": price_label,
            "original_price_kopeks": pricing.base_original_total,
            "original_price_label": original_label,
            "discount_percent": pricing.overall_discount_percent,
            "price_per_month_kopeks": pricing.per_month,
            "price_per_month_label": per_month_label,
            "title": label,
        }

        return payload


def calculate_missing_amount(balance_kopeks: int, total_kopeks: int) -> int:
    if total_kopeks <= 0:
        return 0
    if balance_kopeks <= 0:
        return total_kopeks
    return max(0, total_kopeks - min(balance_kopeks, total_kopeks))

