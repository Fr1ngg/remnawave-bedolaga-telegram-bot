"""Automatic subscription purchase from a saved cart after balance top-up."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.localization.texts import get_texts
from app.services.admin_notification_service import AdminNotificationService
from app.services.subscription_checkout_service import clear_subscription_checkout_draft
from app.services.subscription_purchase_service import (
    MiniAppSubscriptionPurchaseService,
    PurchaseOptionsContext,
    PurchasePricingResult,
    PurchaseSelection,
    PurchaseValidationError,
    PurchaseBalanceError,
    SubscriptionPurchaseService,
)
from app.services.user_cart_service import user_cart_service
from app.utils.pricing_utils import format_period_description

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AutoPurchaseContext:
    """Aggregated data prepared for automatic checkout processing."""

    context: PurchaseOptionsContext
    pricing: PurchasePricingResult
    selection: PurchaseSelection


async def _prepare_auto_purchase(
    db: AsyncSession,
    user: User,
    cart_data: dict,
) -> Optional[AutoPurchaseContext]:
    """Builds purchase context and pricing for a saved cart."""

    period_days = int(cart_data.get("period_days") or 0)
    if period_days <= 0:
        logger.info(
            "🔁 Автопокупка: у пользователя %s нет корректного периода в сохранённой корзине",
            user.telegram_id,
        )
        return None

    miniapp_service = MiniAppSubscriptionPurchaseService()
    context = await miniapp_service.build_options(db, user)

    period_config = context.period_map.get(f"days:{period_days}")
    if not period_config:
        logger.warning(
            "🔁 Автопокупка: период %s дней недоступен для пользователя %s",
            period_days,
            user.telegram_id,
        )
        return None

    traffic_value = cart_data.get("traffic_gb")
    if traffic_value is None:
        traffic_value = (
            period_config.traffic.current_value
            if period_config.traffic.current_value is not None
            else period_config.traffic.default_value or 0
        )
    else:
        traffic_value = int(traffic_value)

    devices = int(cart_data.get("devices") or period_config.devices.current or 1)
    servers = list(cart_data.get("countries") or [])
    if not servers:
        servers = list(period_config.servers.default_selection)

    selection = PurchaseSelection(
        period=period_config,
        traffic_value=traffic_value,
        servers=servers,
        devices=devices,
    )

    pricing = await miniapp_service.calculate_pricing(db, context, selection)
    return AutoPurchaseContext(context=context, pricing=pricing, selection=selection)


def get_saved_cart_total_kopeks(
    cart_data: Optional[dict],
    user_balance_kopeks: Optional[int],
) -> int:
    """Extracts the expected total purchase amount from saved cart data.

    The cart stores the final total in ``total_price``. If it is absent (for
    example, because the cart was saved at the moment of insufficient balance),
    we approximate the target sum as the current balance plus the recorded
    ``missing_amount``. Any parsing errors are treated as zero to avoid
    propagating exceptions during notification flows.
    """

    if not cart_data:
        return 0

    try:
        total = int(cart_data.get("total_price") or 0)
    except (TypeError, ValueError):
        total = 0

    if total > 0:
        return total

    try:
        missing = int(cart_data.get("missing_amount") or 0)
    except (TypeError, ValueError):
        missing = 0

    if missing <= 0 or user_balance_kopeks is None:
        return 0

    try:
        balance = int(user_balance_kopeks)
    except (TypeError, ValueError):
        balance = 0

    estimated_total = balance + missing
    return estimated_total if estimated_total > 0 else 0


async def auto_purchase_saved_cart_after_topup(
    db: AsyncSession,
    user: User,
    *,
    bot: Optional[Bot] = None,
) -> bool:
    """Attempts to automatically purchase a subscription from a saved cart."""

    if not settings.is_auto_purchase_after_topup_enabled():
        return False

    if not user or not getattr(user, "id", None):
        return False

    cart_data = await user_cart_service.get_user_cart(user.id)
    if not cart_data:
        return False

    logger.info(
        "🔁 Автопокупка: обнаружена сохранённая корзина у пользователя %s", user.telegram_id
    )

    try:
        prepared = await _prepare_auto_purchase(db, user, cart_data)
    except PurchaseValidationError as error:
        logger.error(
            "❌ Автопокупка: ошибка валидации корзины пользователя %s: %s",
            user.telegram_id,
            error,
        )
        return False
    except Exception as error:  # pragma: no cover - defensive logging
        logger.error(
            "❌ Автопокупка: непредвиденная ошибка при подготовке корзины %s: %s",
            user.telegram_id,
            error,
            exc_info=True,
        )
        return False

    if prepared is None:
        return False

    pricing = prepared.pricing
    selection = prepared.selection

    if pricing.final_total <= 0:
        logger.warning(
            "❌ Автопокупка: итоговая сумма для пользователя %s некорректна (%s)",
            user.telegram_id,
            pricing.final_total,
        )
        return False

    if user.balance_kopeks < pricing.final_total:
        logger.info(
            "🔁 Автопокупка: у пользователя %s недостаточно средств (%s < %s)",
            user.telegram_id,
            user.balance_kopeks,
            pricing.final_total,
        )
        return False

    purchase_service = SubscriptionPurchaseService()

    try:
        purchase_result = await purchase_service.submit_purchase(
            db,
            prepared.context,
            pricing,
        )
    except PurchaseBalanceError:
        logger.info(
            "🔁 Автопокупка: баланс пользователя %s изменился и стал недостаточным",
            user.telegram_id,
        )
        return False
    except PurchaseValidationError as error:
        logger.error(
            "❌ Автопокупка: не удалось подтвердить корзину пользователя %s: %s",
            user.telegram_id,
            error,
        )
        return False
    except Exception as error:  # pragma: no cover - defensive logging
        logger.error(
            "❌ Автопокупка: ошибка оформления подписки для пользователя %s: %s",
            user.telegram_id,
            error,
            exc_info=True,
        )
        return False

    await user_cart_service.delete_user_cart(user.id)
    await clear_subscription_checkout_draft(user.id)

    subscription = purchase_result.get("subscription")
    transaction = purchase_result.get("transaction")
    was_trial_conversion = purchase_result.get("was_trial_conversion", False)
    texts = get_texts(getattr(user, "language", "ru"))

    if bot:
        try:
            notification_service = AdminNotificationService(bot)
            await notification_service.send_subscription_purchase_notification(
                db,
                user,
                subscription,
                transaction,
                selection.period.days,
                was_trial_conversion,
            )
        except Exception as error:  # pragma: no cover - defensive logging
            logger.error(
                "⚠️ Автопокупка: не удалось отправить уведомление админам (%s): %s",
                user.telegram_id,
                error,
            )

        try:
            period_label = format_period_description(
                selection.period.days,
                getattr(user, "language", "ru"),
            )
            auto_message = texts.t(
                "AUTO_PURCHASE_SUBSCRIPTION_SUCCESS",
                "✅ Subscription purchased automatically after balance top-up ({period}).",
            ).format(period=period_label)

            hint_message = texts.t(
                "AUTO_PURCHASE_SUBSCRIPTION_HINT",
                "Open the ‘My subscription’ section to access your link.",
            )

            purchase_message = purchase_result.get("message", "")
            full_message = "\n\n".join(
                part.strip()
                for part in [auto_message, purchase_message, hint_message]
                if part and part.strip()
            )

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=texts.t("MY_SUBSCRIPTION_BUTTON", "📱 My subscription"),
                            callback_data="menu_subscription",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text=texts.t("BACK_TO_MAIN_MENU_BUTTON", "🏠 Main menu"),
                            callback_data="back_to_menu",
                        )
                    ],
                ]
            )

            await bot.send_message(
                chat_id=user.telegram_id,
                text=full_message,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        except Exception as error:  # pragma: no cover - defensive logging
            logger.error(
                "⚠️ Автопокупка: не удалось уведомить пользователя %s: %s",
                user.telegram_id,
                error,
            )

    logger.info(
        "✅ Автопокупка: подписка на %s дней оформлена для пользователя %s",
        selection.period.days,
        user.telegram_id,
    )

    return True


__all__ = ["auto_purchase_saved_cart_after_topup", "get_saved_cart_total_kopeks"]
