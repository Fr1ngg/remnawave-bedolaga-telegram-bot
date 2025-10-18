"""Функции работы с YooKassa вынесены в dedicated mixin.

Такое разделение облегчает поддержку и делает очевидным, какая часть
отвечает за конкретного провайдера.
"""

from __future__ import annotations

import logging
from datetime import datetime
from importlib import import_module
from typing import Any, Dict, Optional, TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import PaymentMethod, TransactionType
from app.utils.user_utils import format_referrer_info

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.database.models import YooKassaPayment


class YooKassaPaymentMixin:
    """Mixin с операциями по созданию и подтверждению платежей YooKassa."""

    async def create_yookassa_payment(
        self,
        db: AsyncSession,
        user_id: int,
        amount_kopeks: int,
        description: str,
        receipt_email: Optional[str] = None,
        receipt_phone: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Создаёт обычный платёж в YooKassa и сохраняет локальную запись."""
        if not getattr(self, "yookassa_service", None):
            logger.error("YooKassa сервис не инициализирован")
            return None

        payment_module = import_module("app.services.payment_service")

        try:
            amount_rubles = amount_kopeks / 100

            payment_metadata = metadata.copy() if metadata else {}
            payment_metadata.update(
                {
                    "user_id": str(user_id),
                    "amount_kopeks": str(amount_kopeks),
                    "type": "balance_topup",
                }
            )

            yookassa_response = await self.yookassa_service.create_payment(
                amount=amount_rubles,
                currency="RUB",
                description=description,
                metadata=payment_metadata,
                receipt_email=receipt_email,
                receipt_phone=receipt_phone,
            )

            if not yookassa_response or yookassa_response.get("error"):
                logger.error(
                    "Ошибка создания платежа YooKassa: %s", yookassa_response
                )
                return None

            yookassa_created_at: Optional[datetime] = None
            if yookassa_response.get("created_at"):
                try:
                    dt_with_tz = datetime.fromisoformat(
                        yookassa_response["created_at"].replace("Z", "+00:00")
                    )
                    yookassa_created_at = dt_with_tz.replace(tzinfo=None)
                except Exception as error:
                    logger.warning("Не удалось распарсить created_at: %s", error)
                    yookassa_created_at = None

            local_payment = await payment_module.create_yookassa_payment(
                db=db,
                user_id=user_id,
                yookassa_payment_id=yookassa_response["id"],
                amount_kopeks=amount_kopeks,
                currency="RUB",
                description=description,
                status=yookassa_response["status"],
                confirmation_url=yookassa_response.get("confirmation_url"),
                metadata_json=payment_metadata,
                payment_method_type=None,
                yookassa_created_at=yookassa_created_at,
                test_mode=yookassa_response.get("test_mode", False),
            )

            logger.info(
                "Создан платеж YooKassa %s на %s₽ для пользователя %s",
                yookassa_response["id"],
                amount_rubles,
                user_id,
            )

            return {
                "local_payment_id": local_payment.id,
                "yookassa_payment_id": yookassa_response["id"],
                "confirmation_url": yookassa_response.get("confirmation_url"),
                "amount_kopeks": amount_kopeks,
                "amount_rubles": amount_rubles,
                "status": yookassa_response["status"],
                "created_at": local_payment.created_at,
            }

        except Exception as error:
            logger.error("Ошибка создания платежа YooKassa: %s", error)
            return None

    async def create_yookassa_sbp_payment(
        self,
        db: AsyncSession,
        user_id: int,
        amount_kopeks: int,
        description: str,
        receipt_email: Optional[str] = None,
        receipt_phone: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Создаёт платёж по СБП через YooKassa."""
        if not getattr(self, "yookassa_service", None):
            logger.error("YooKassa сервис не инициализирован")
            return None

        payment_module = import_module("app.services.payment_service")

        try:
            amount_rubles = amount_kopeks / 100

            payment_metadata = metadata.copy() if metadata else {}
            payment_metadata.update(
                {
                    "user_id": str(user_id),
                    "amount_kopeks": str(amount_kopeks),
                    "type": "balance_topup_sbp",
                }
            )

            yookassa_response = (
                await self.yookassa_service.create_sbp_payment(
                    amount=amount_rubles,
                    currency="RUB",
                    description=description,
                    metadata=payment_metadata,
                    receipt_email=receipt_email,
                    receipt_phone=receipt_phone,
                )
            )

            if not yookassa_response or yookassa_response.get("error"):
                logger.error(
                    "Ошибка создания платежа YooKassa СБП: %s",
                    yookassa_response,
                )
                return None

            local_payment = await payment_module.create_yookassa_payment(
                db=db,
                user_id=user_id,
                yookassa_payment_id=yookassa_response["id"],
                amount_kopeks=amount_kopeks,
                currency="RUB",
                description=description,
                status=yookassa_response["status"],
                confirmation_url=yookassa_response.get("confirmation_url"),  # Используем confirmation URL
                metadata_json=payment_metadata,
                payment_method_type="bank_card",
                yookassa_created_at=None,
                test_mode=yookassa_response.get("test_mode", False),
            )

            logger.info(
                "Создан платеж YooKassa СБП %s на %s₽ для пользователя %s",
                yookassa_response["id"],
                amount_rubles,
                user_id,
            )

            confirmation_token = (
                yookassa_response.get("confirmation", {}) or {}
            ).get("confirmation_token")

            return {
                "local_payment_id": local_payment.id,
                "yookassa_payment_id": yookassa_response["id"],
                "confirmation_url": yookassa_response.get("confirmation_url"),  # URL для подтверждения
                "qr_confirmation_data": yookassa_response.get("qr_confirmation_data"),   # Данные для QR-кода
                "confirmation_token": confirmation_token,
                "amount_kopeks": amount_kopeks,
                "amount_rubles": amount_rubles,
                "status": yookassa_response["status"],
                "created_at": local_payment.created_at,
            }

        except Exception as error:
            logger.error("Ошибка создания платежа YooKassa СБП: %s", error)
            return None

    async def _process_successful_yookassa_payment(
        self,
        db: AsyncSession,
        payment: "YooKassaPayment",
    ) -> bool:
        """Переносит успешный платёж YooKassa в транзакции и начисляет баланс пользователю."""
        try:
            payment_module = import_module("app.services.payment_service")

            payment_description = getattr(payment, "description", "YooKassa платеж")

            payment_metadata: Dict[str, Any] = {}
            try:
                if hasattr(payment, "metadata_json") and payment.metadata_json:
                    import json

                    if isinstance(payment.metadata_json, str):
                        payment_metadata = json.loads(payment.metadata_json)
                    elif isinstance(payment.metadata_json, dict):
                        payment_metadata = payment.metadata_json
                    logger.info(f"Метаданные платежа: {payment_metadata}")
            except Exception as parse_error:
                logger.error(f"Ошибка парсинга метаданных платежа: {parse_error}")

            payment_purpose = payment_metadata.get("payment_purpose", "")
            is_simple_subscription = payment_purpose == "simple_subscription_purchase"

            transaction_type = (
                TransactionType.SUBSCRIPTION_PAYMENT
                if is_simple_subscription
                else TransactionType.DEPOSIT
            )
            transaction_description = (
                f"Оплата подписки через YooKassa: {payment_description}"
                if is_simple_subscription
                else f"Пополнение через YooKassa: {payment_description}"
            )

            transaction = await payment_module.create_transaction(
                db=db,
                user_id=payment.user_id,
                type=transaction_type,
                amount_kopeks=payment.amount_kopeks,
                description=transaction_description,
                payment_method=PaymentMethod.YOOKASSA,
                external_id=payment.yookassa_payment_id,
                is_completed=True,
            )

            await payment_module.link_yookassa_payment_to_transaction(
                db,
                payment.yookassa_payment_id,
                transaction.id,
            )

            user = await payment_module.get_user_by_id(db, payment.user_id)
            if user:
                if is_simple_subscription:
                    logger.info(
                        "YooKassa платеж %s обработан как покупка подписки. Баланс пользователя %s не изменяется.",
                        payment.yookassa_payment_id,
                        user.id,
                    )
                else:
                    old_balance = getattr(user, "balance_kopeks", 0)
                    was_first_topup = not getattr(user, "has_made_first_topup", False)

                    user.balance_kopeks += payment.amount_kopeks
                    user.updated_at = datetime.utcnow()

                    promo_group = getattr(user, "promo_group", None)
                    subscription = getattr(user, "subscription", None)
                    referrer_info = format_referrer_info(user)
                    topup_status = (
                        "🆕 Первое пополнение" if was_first_topup else "🔄 Пополнение"
                    )

                    await db.commit()

                    try:
                        from app.services.referral_service import process_referral_topup

                        await process_referral_topup(
                            db,
                            user.id,
                            payment.amount_kopeks,
                            getattr(self, "bot", None),
                        )
                    except Exception as error:
                        logger.error(
                            "Ошибка обработки реферального пополнения YooKassa: %s",
                            error,
                        )

                    if was_first_topup and not getattr(user, "has_made_first_topup", False):
                        user.has_made_first_topup = True
                        await db.commit()

                    await db.refresh(user)

                    # Отправляем уведомления админам
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
                            logger.info("Уведомление админам о пополнении отправлено успешно")
                        except Exception as error:
                            logger.error(
                                "Ошибка отправки уведомления админам о YooKassa пополнении: %s",
                                error,
                                exc_info=True,  # Добавляем полный стек вызовов для отладки
                            )

                    # Отправляем уведомление пользователю
                    if getattr(self, "bot", None):
                        try:
                            # Передаем только простые данные, чтобы избежать проблем с ленивой загрузкой
                            await self._send_payment_success_notification(
                                user.telegram_id,
                                payment.amount_kopeks,
                                user=None,  # Передаем None, чтобы _ensure_user_snapshot загрузил данные сам
                                db=db,
                                payment_method_title="Банковская карта (YooKassa)",
                            )
                            logger.info("Уведомление пользователю о платеже отправлено успешно")
                        except Exception as error:
                            logger.error(
                                "Ошибка отправки уведомления о платеже: %s",
                                error,
                                exc_info=True,  # Добавляем полный стек вызовов для отладки
                            )

                    # Проверяем наличие сохраненной корзины для возврата к оформлению подписки
                    # ВАЖНО: этот код должен выполняться даже при ошибках в уведомлениях
                    logger.info(f"Проверяем наличие сохраненной корзины для пользователя {user.id}")
                    from app.services.user_cart_service import user_cart_service
                    try:
                        has_saved_cart = await user_cart_service.has_user_cart(user.id)
                        logger.info(f"Результат проверки корзины для пользователя {user.id}: {has_saved_cart}")
                        if has_saved_cart and getattr(self, "bot", None):
                            # Если у пользователя есть сохраненная корзина,
                            # отправляем ему уведомление с кнопкой вернуться к оформлению
                            from app.localization.texts import get_texts
                            from aiogram import types

                            texts = get_texts(user.language)
                            cart_message = texts.BALANCE_TOPUP_CART_REMINDER_DETAILED.format(
                                total_amount=settings.format_price(payment.amount_kopeks)
                            )

                            # Создаем клавиатуру с кнопками
                            keyboard = types.InlineKeyboardMarkup(
                                inline_keyboard=[
                                    [
                                        types.InlineKeyboardButton(
                                            text=texts.RETURN_TO_SUBSCRIPTION_CHECKOUT,
                                            callback_data="subscription_resume_checkout",
                                        )
                                    ],
                                    [
                                        types.InlineKeyboardButton(
                                            text="💰 Мой баланс",
                                            callback_data="menu_balance",
                                        )
                                    ],
                                    [
                                        types.InlineKeyboardButton(
                                            text="🏠 Главное меню",
                                            callback_data="back_to_menu",
                                        )
                                    ],
                                ]
                            )

                            await self.bot.send_message(
                                chat_id=user.telegram_id,
                                text=f"✅ Баланс пополнен на {settings.format_price(payment.amount_kopeks)}!\n\n{cart_message}",
                                reply_markup=keyboard,
                            )
                            logger.info(
                                f"Отправлено уведомление с кнопкой возврата к оформлению подписки пользователю {user.id}"
                            )
                        else:
                            logger.info(f"У пользователя {user.id} нет сохраненной корзины или бот недоступен")
                    except Exception as e:
                        logger.error(
                            f"Критическая ошибка при работе с сохраненной корзиной для пользователя {user.id}: {e}",
                            exc_info=True,
                        )

                if is_simple_subscription:
                    logger.info(f"Обнаружен платеж простой покупки подписки для пользователя {user.id}")
                    try:
                        # Активируем подписку
                        from app.services.subscription_service import SubscriptionService
                        subscription_service = SubscriptionService()
                        
                        # Получаем параметры подписки из метаданных
                        subscription_period = int(payment_metadata.get("subscription_period", 30))
                        order_id = payment_metadata.get("order_id")
                        
                        logger.info(f"Активация подписки: период={subscription_period} дней, заказ={order_id}")
                        
                        # Активируем pending подписку пользователя
                        from app.database.crud.subscription import activate_pending_subscription
                        subscription = await activate_pending_subscription(
                            db=db,
                            user_id=user.id,
                            period_days=subscription_period
                        )
                        
                        if subscription:
                            logger.info(f"Подписка успешно активирована для пользователя {user.id}")

                            # Обновляем данные подписки в RemnaWave, чтобы получить актуальные ссылки
                            try:
                                remnawave_user = await subscription_service.create_remnawave_user(db, subscription)
                                if remnawave_user:
                                    await db.refresh(subscription)
                            except Exception as sync_error:
                                logger.error(
                                    "Ошибка синхронизации подписки с RemnaWave для пользователя %s: %s",
                                    user.id,
                                    sync_error,
                                    exc_info=True,
                                )
                            
                            # Отправляем уведомление пользователю об активации подписки
                            if getattr(self, "bot", None):
                                from app.localization.texts import get_texts
                                from aiogram import types
                                
                                texts = get_texts(user.language)
                                
                                success_message = (
                                    f"✅ <b>Подписка успешно активирована!</b>\n\n"
                                    f"📅 Период: {subscription_period} дней\n"
                                    f"📱 Устройства: 1\n"
                                    f"📊 Трафик: Безлимит\n"
                                    f"💳 Оплата: {settings.format_price(payment.amount_kopeks)} (YooKassa)\n\n"
                                    f"🔗 Для подключения перейдите в раздел 'Моя подписка'"
                                )
                                
                                keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
                                    [types.InlineKeyboardButton(text="📱 Моя подписка", callback_data="menu_subscription")],
                                    [types.InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_menu")]
                                ])
                                
                                await self.bot.send_message(
                                    chat_id=user.telegram_id,
                                    text=success_message,
                                    reply_markup=keyboard,
                                    parse_mode="HTML"
                                )

                            if getattr(self, "bot", None):
                                try:
                                    from app.services.admin_notification_service import (
                                        AdminNotificationService,
                                    )

                                    notification_service = AdminNotificationService(self.bot)
                                    await notification_service.send_subscription_purchase_notification(
                                        db,
                                        user,
                                        subscription,
                                        transaction,
                                        subscription_period,
                                        was_trial_conversion=False,
                                    )
                                except Exception as admin_error:
                                    logger.error(
                                        "Ошибка отправки уведомления админам о покупке подписки через YooKassa: %s",
                                        admin_error,
                                        exc_info=True,
                                    )
                        else:
                            logger.error(f"Ошибка активации подписки для пользователя {user.id}")
                    except Exception as e:
                        logger.error(f"Ошибка активации подписки для пользователя {user.id}: {e}", exc_info=True)

            if is_simple_subscription:
                logger.info(
                    "Успешно обработан платеж YooKassa %s как покупка подписки: пользователь %s, сумма %s₽",
                    payment.yookassa_payment_id,
                    payment.user_id,
                    payment.amount_kopeks / 100,
                )
            else:
                logger.info(
                    "Успешно обработан платеж YooKassa %s: пользователь %s пополнил баланс на %s₽",
                    payment.yookassa_payment_id,
                    payment.user_id,
                    payment.amount_kopeks / 100,
                )

            return True

        except Exception as error:
            logger.error(
                "Ошибка обработки успешного платежа YooKassa %s: %s",
                payment.yookassa_payment_id,
                error,
            )
            return False

    async def process_yookassa_webhook(
        self,
        db: AsyncSession,
        event: Dict[str, Any],
    ) -> bool:
        """Обрабатывает входящий webhook YooKassa и синхронизирует состояние платежа."""
        event_object = event.get("object", {})
        yookassa_payment_id = event_object.get("id")

        if not yookassa_payment_id:
            logger.warning("Webhook без payment id: %s", event)
            return False

        payment_module = import_module("app.services.payment_service")

        payment = await payment_module.get_yookassa_payment_by_id(db, yookassa_payment_id)
        if not payment:
            logger.warning(
                "Локальный платеж для YooKassa id %s не найден", yookassa_payment_id
            )
            return False

        payment.status = event_object.get("status", payment.status)
        payment.confirmation_url = event_object.get("confirmation_url")

        current_paid = getattr(payment, "paid", False)
        payment.paid = event_object.get("paid", current_paid)

        await db.commit()
        await db.refresh(payment)

        if payment.status == "succeeded" and payment.paid:
            return await self._process_successful_yookassa_payment(db, payment)

        logger.info(
            "Webhook YooKassa обновил платеж %s до статуса %s",
            yookassa_payment_id,
            payment.status,
        )
        return True
