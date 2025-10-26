"""Логика Telegram Stars вынесена в отдельный mixin.

Методы здесь отвечают только за работу с звёздами, что позволяет держать
основной сервис компактным и облегчает тестирование конкретных сценариев.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from typing import Optional

from aiogram.types import LabeledPrice
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.transaction import create_transaction
from app.database.crud.user import get_user_by_id
from app.database.models import PaymentMethod, TransactionType
from app.external.telegram_stars import TelegramStarsService
from app.services.subscription_auto_purchase_service import (
    auto_purchase_saved_cart_after_topup,
    get_saved_cart_total_kopeks,
)
from app.utils.user_utils import format_referrer_info

logger = logging.getLogger(__name__)


class TelegramStarsMixin:
    """Mixin с операциями создания и обработки платежей через Telegram Stars."""

    async def create_stars_invoice(
        self,
        amount_kopeks: int,
        description: str,
        payload: Optional[str] = None,
        *,
        stars_amount: Optional[int] = None,
    ) -> str:
        """Создаёт invoice в Telegram Stars, автоматически рассчитывая количество звёзд."""
        if not self.bot or not getattr(self, "stars_service", None):
            raise ValueError("Bot instance required for Stars payments")

        try:
            amount_rubles = Decimal(amount_kopeks) / Decimal(100)

            # Если количество звёзд не задано, вычисляем его из курса.
            if stars_amount is None:
                rate = Decimal(str(settings.get_stars_rate()))
                if rate <= 0:
                    raise ValueError("Stars rate must be positive")

                normalized_stars = (amount_rubles / rate).to_integral_value(
                    rounding=ROUND_FLOOR
                )
                stars_amount = int(normalized_stars) or 1

            if stars_amount <= 0:
                raise ValueError("Stars amount must be positive")

            invoice_link = await self.bot.create_invoice_link(
                title="Пополнение баланса VPN",
                description=f"{description} (≈{stars_amount} ⭐)",
                payload=payload or f"balance_topup_{amount_kopeks}",
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice(label="Пополнение", amount=stars_amount)],
            )

            logger.info(
                "Создан Stars invoice на %s звезд (~%s)",
                stars_amount,
                settings.format_price(amount_kopeks),
            )
            return invoice_link

        except Exception as error:
            logger.error("Ошибка создания Stars invoice: %s", error)
            raise

    async def process_stars_payment(
        self,
        db: AsyncSession,
        user_id: int,
        stars_amount: int,
        payload: str,
        telegram_payment_charge_id: str,
    ) -> bool:
        """Финализирует платеж, пришедший из Telegram Stars, и обновляет баланс пользователя."""
        del payload  # payload пока не используется, но оставляем аргумент для совместимости.
        try:
            rubles_amount = TelegramStarsService.calculate_rubles_from_stars(
                stars_amount
            )
            amount_kopeks = int(
                (rubles_amount * Decimal(100)).to_integral_value(
                    rounding=ROUND_HALF_UP
                )
            )

            transaction = await create_transaction(
                db=db,
                user_id=user_id,
                type=TransactionType.DEPOSIT,
                amount_kopeks=amount_kopeks,
                description=f"Пополнение через Telegram Stars ({stars_amount} ⭐)",
                payment_method=PaymentMethod.TELEGRAM_STARS,
                external_id=telegram_payment_charge_id,
                is_completed=True,
            )

            user = await get_user_by_id(db, user_id)
            if not user:
                logger.error(
                    "Пользователь с ID %s не найден при обработке Stars платежа",
                    user_id,
                )
                return False

            # Запоминаем старые значения, чтобы корректно построить уведомления.
            old_balance = user.balance_kopeks
            was_first_topup = not user.has_made_first_topup

            # Обновляем баланс в БД.
            user.balance_kopeks += amount_kopeks
            user.updated_at = datetime.utcnow()

            promo_group = getattr(user, "promo_group", None)
            subscription = getattr(user, "subscription", None)
            referrer_info = format_referrer_info(user)
            topup_status = (
                "🆕 Первое пополнение" if was_first_topup else "🔄 Пополнение"
            )

            await db.commit()

            description_for_referral = (
                f"Пополнение Stars: {settings.format_price(amount_kopeks)} ({stars_amount} ⭐)"
            )
            logger.info(
                "🔍 Проверка реферальной логики для описания: '%s'",
                description_for_referral,
            )

            lower_description = description_for_referral.lower()
            contains_allowed_keywords = any(
                word in lower_description
                for word in ["пополнение", "stars", "yookassa", "topup"]
            )
            contains_forbidden_keywords = any(
                word in lower_description for word in ["комиссия", "бонус"]
            )
            allow_referral = contains_allowed_keywords and not contains_forbidden_keywords

            if allow_referral:
                logger.info(
                    "🔞 Вызов process_referral_topup для пользователя %s",
                    user_id,
                )
                try:
                    from app.services.referral_service import process_referral_topup

                    await process_referral_topup(
                        db, user_id, amount_kopeks, getattr(self, "bot", None)
                    )
                except Exception as error:
                    logger.error(
                        "Ошибка обработки реферального пополнения: %s", error
                    )
            else:
                logger.info(
                    "❌ Описание '%s' не подходит для реферальной логики",
                    description_for_referral,
                )

            if was_first_topup and not user.has_made_first_topup:
                user.has_made_first_topup = True
                await db.commit()

            await db.refresh(user)

            logger.info(
                "💰 Баланс пользователя %s изменен: %s → %s (Δ +%s)",
                user.telegram_id,
                old_balance,
                user.balance_kopeks,
                amount_kopeks,
            )

            if getattr(self, "bot", None):
                try:
                    from app.services.admin_notification_service import (
                        AdminNotificationService,
                    )

                    notification_service = AdminNotificationService(self.bot)
                    await notification_service.send_balance_topup_notification(
                        user,
                        transaction,
                        old_balance,
                        topup_status=topup_status,
                        referrer_info=referrer_info,
                        subscription=subscription,
                        promo_group=promo_group,
                        db=db,
                    )
                except Exception as error:
                    logger.error(
                        "Ошибка отправки уведомления о пополнении Stars: %s",
                        error,
                        exc_info=True
                    )

            if getattr(self, "bot", None):
                try:
                    keyboard = await self.build_topup_success_keyboard(user)

                    await self.bot.send_message(
                        user.telegram_id,
                        (
                            "✅ <b>Пополнение успешно!</b>\n\n"
                            f"⭐ Звезд: {stars_amount}\n"
                            f"💰 Сумма: {settings.format_price(amount_kopeks)}\n"
                            "🦊 Способ: Telegram Stars\n"
                            f"🆔 Транзакция: {telegram_payment_charge_id[:8]}...\n\n"
                            "Баланс пополнен автоматически!"
                        ),
                        parse_mode="HTML",
                        reply_markup=keyboard,
                    )
                    logger.info(
                        "✅ Отправлено уведомление пользователю %s о пополнении на %s",
                        user.telegram_id,
                        settings.format_price(amount_kopeks),
                    )
                except Exception as error:
                    logger.error(
                        "Ошибка отправки уведомления о пополнении Stars: %s",
                        error,
                    )

            # Проверяем наличие сохраненной корзины для возврата к оформлению подписки
            try:
                from app.services.user_cart_service import user_cart_service
                from aiogram import types
                has_saved_cart = await user_cart_service.has_user_cart(user.id)
                auto_purchase_success = False
                if has_saved_cart:
                    try:
                        auto_purchase_success = await auto_purchase_saved_cart_after_topup(
                            db,
                            user,
                            bot=getattr(self, "bot", None),
                        )
                    except Exception as auto_error:
                        logger.error(
                            "Ошибка автоматической покупки подписки для пользователя %s: %s",
                            user.id,
                            auto_error,
                            exc_info=True,
                        )

                    if auto_purchase_success:
                        has_saved_cart = False

                if has_saved_cart and getattr(self, "bot", None):
                    # Если у пользователя есть сохраненная корзина,
                    # отправляем ему уведомление с кнопкой вернуться к оформлению
                    from app.localization.texts import get_texts

                    texts = get_texts(user.language)
                    cart_data = await user_cart_service.get_user_cart(user.id)
                    cart_total = get_saved_cart_total_kopeks(
                        cart_data, getattr(user, "balance_kopeks", None)
                    )

                    total_amount_label = (
                        texts.format_price(cart_total)
                        if cart_total
                        else settings.format_price(amount_kopeks)
                    )

                    cart_message = texts.t(
                        "BALANCE_TOPUP_CART_REMINDER_DETAILED",
                        "🛒 У вас есть неоформленный заказ.\n\n"
                        "Вы можете продолжить оформление с теми же параметрами."
                    ).format(total_amount=total_amount_label)

                    # Создаем клавиатуру с кнопками
                    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
                        [types.InlineKeyboardButton(
                            text=texts.RETURN_TO_SUBSCRIPTION_CHECKOUT,
                            callback_data="subscription_resume_checkout"
                        )],
                        [types.InlineKeyboardButton(
                            text="💰 Мой баланс",
                            callback_data="menu_balance"
                        )],
                        [types.InlineKeyboardButton(
                            text="🏠 Главное меню",
                            callback_data="back_to_menu"
                        )]
                    ])
                    
                    await self.bot.send_message(
                        chat_id=user.telegram_id,
                        text=f"✅ Баланс пополнен на {settings.format_price(amount_kopeks)}!\n\n{cart_message}",
                        reply_markup=keyboard
                    )
                    logger.info(f"Отправлено уведомление с кнопкой возврата к оформлению подписки пользователю {user.id}")
            except Exception as e:
                logger.error(f"Ошибка при работе с сохраненной корзиной для пользователя {user.id}: {e}", exc_info=True)

            logger.info(
                "✅ Обработан Stars платеж: пользователь %s, %s звезд → %s",
                user_id,
                stars_amount,
                settings.format_price(amount_kopeks),
            )
            return True

        except Exception as error:
            logger.error("Ошибка обработки Stars платежа: %s", error, exc_info=True)
            return False
