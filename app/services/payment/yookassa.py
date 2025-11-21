"""Функции работы с YooKassa вынесены в dedicated mixin.

Такое разделение облегчает поддержку и делает очевидным, какая часть
отвечает за конкретного провайдера.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from importlib import import_module
from typing import Any, Dict, Optional, TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import PaymentMethod, TransactionType
from app.services.subscription_auto_purchase_service import (
    auto_purchase_saved_cart_after_topup,
)
from app.utils.user_utils import format_referrer_info

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.database.models import YooKassaPayment


class YooKassaPaymentMixin:
    """Mixin с операциями по созданию и подтверждению платежей YooKassa."""

    @staticmethod
    def _format_amount_value(value: Any) -> str:
        """Форматирует сумму для хранения в webhook-объекте."""

        try:
            quantized = Decimal(str(value)).quantize(Decimal("0.00"))
            return format(quantized, "f")
        except (InvalidOperation, ValueError, TypeError):
            return str(value)

    @classmethod
    def _merge_remote_yookassa_payload(
        cls,
        event_object: Dict[str, Any],
        remote_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Объединяет локальные данные вебхука с ответом API YooKassa."""

        merged: Dict[str, Any] = dict(event_object)

        status = remote_data.get("status")
        if status:
            merged["status"] = status

        if "paid" in remote_data:
            merged["paid"] = bool(remote_data.get("paid"))

        if "refundable" in remote_data:
            merged["refundable"] = bool(remote_data.get("refundable"))

        payment_method_type = remote_data.get("payment_method_type")
        if payment_method_type:
            payment_method = dict(merged.get("payment_method") or {})
            payment_method["type"] = payment_method_type
            merged["payment_method"] = payment_method

        amount_value = remote_data.get("amount_value")
        amount_currency = remote_data.get("amount_currency")
        if amount_value is not None or amount_currency:
            merged_amount = dict(merged.get("amount") or {})
            if amount_value is not None:
                merged_amount["value"] = cls._format_amount_value(amount_value)
            if amount_currency:
                merged_amount["currency"] = str(amount_currency).upper()
            merged["amount"] = merged_amount

        for datetime_field in ("captured_at", "created_at"):
            value = remote_data.get(datetime_field)
            if value:
                merged[datetime_field] = value

        metadata = remote_data.get("metadata")
        if metadata:
            try:
                merged["metadata"] = dict(metadata)  # type: ignore[arg-type]
            except TypeError:
                merged["metadata"] = metadata

        return merged

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

    async def get_yookassa_payment_status(
        self,
        db: AsyncSession,
        local_payment_id: int,
    ) -> Optional[Dict[str, Any]]:
        """Запрашивает статус платежа в YooKassa и синхронизирует локальные данные."""

        payment_module = import_module("app.services.payment_service")

        payment = await payment_module.get_yookassa_payment_by_local_id(db, local_payment_id)
        if not payment:
            return None

        remote_data: Optional[Dict[str, Any]] = None

        if getattr(self, "yookassa_service", None):
            try:
                remote_data = await self.yookassa_service.get_payment_info(  # type: ignore[union-attr]
                    payment.yookassa_payment_id
                )
            except Exception as error:  # pragma: no cover - defensive logging
                logger.error(
                    "Ошибка получения статуса YooKassa %s: %s",
                    payment.yookassa_payment_id,
                    error,
                )

        if remote_data:
            status = remote_data.get("status") or payment.status
            paid = bool(remote_data.get("paid", getattr(payment, "is_paid", False)))
            captured_raw = remote_data.get("captured_at")
            captured_at = None
            if captured_raw:
                try:
                    captured_at = datetime.fromisoformat(
                        str(captured_raw).replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                except Exception as parse_error:  # pragma: no cover - diagnostic log
                    logger.debug(
                        "Не удалось распарсить captured_at %s: %s",
                        captured_raw,
                        parse_error,
                    )
                    captured_at = None

            payment_method_type = remote_data.get("payment_method_type")

            updated_payment = await payment_module.update_yookassa_payment_status(
                db,
                payment.yookassa_payment_id,
                status=status,
                is_paid=paid,
                is_captured=paid and status == "succeeded",
                captured_at=captured_at,
                payment_method_type=payment_method_type,
            )

            if updated_payment:
                payment = updated_payment

        transaction_id = getattr(payment, "transaction_id", None)

        if (
            payment.status == "succeeded"
            and getattr(payment, "is_paid", False)
        ):
            if not transaction_id:
                try:
                    await db.refresh(payment)
                    transaction_id = getattr(payment, "transaction_id", None)
                except Exception as refresh_error:  # pragma: no cover - defensive logging
                    logger.warning(
                        "Не удалось обновить состояние платежа YooKassa %s перед повторной обработкой: %s",
                        payment.yookassa_payment_id,
                        refresh_error,
                        exc_info=True,
                    )

            if transaction_id:
                logger.info(
                    "Пропускаем повторную обработку платежа YooKassa %s: уже связан с транзакцией %s",
                    payment.yookassa_payment_id,
                    transaction_id,
                )
            else:
                try:
                    await self._process_successful_yookassa_payment(db, payment)
                except Exception as process_error:  # pragma: no cover - defensive logging
                    logger.error(
                        "Ошибка обработки успешного платежа YooKassa %s: %s",
                        payment.yookassa_payment_id,
                        process_error,
                        exc_info=True,
                    )

        return {
            "payment": payment,
            "status": payment.status,
            "is_paid": getattr(payment, "is_paid", False),
            "remote_data": remote_data,
        }

    async def _process_successful_yookassa_payment(
        self,
        db: AsyncSession,
        payment: "YooKassaPayment",
    ) -> bool:
        """Переносит успешный платёж YooKassa в транзакции и начисляет баланс пользователю."""
        try:
            from sqlalchemy import select
            payment_module = import_module("app.services.payment_service")

            # Проверяем, не обрабатывается ли уже этот платеж (защита от дублирования)
            existing_transaction = await payment_module.get_transaction_by_external_id(  # type: ignore[attr-defined]
                db,
                payment.yookassa_payment_id,
                PaymentMethod.YOOKASSA,
            )
            
            if existing_transaction:
                # Если транзакция уже существует, просто завершаем обработку
                logger.info(
                    "Платеж YooKassa %s уже был обработан транзакцией %s. Пропускаем повторную обработку.",
                    payment.yookassa_payment_id,
                    existing_transaction.id,
                )
                
                # Убедимся, что платеж связан с транзакцией
                if not getattr(payment, "transaction_id", None):
                    try:
                        linked_payment = await payment_module.link_yookassa_payment_to_transaction(  # type: ignore[attr-defined]
                            db,
                            payment.yookassa_payment_id,
                            existing_transaction.id,
                        )
                        if linked_payment:
                            payment.transaction_id = getattr(
                                linked_payment,
                                "transaction_id",
                                existing_transaction.id,
                            )
                            if hasattr(linked_payment, "transaction"):
                                payment.transaction = linked_payment.transaction
                    except Exception as link_error:  # pragma: no cover - защитный лог
                        logger.warning(
                            "Не удалось привязать платеж YooKassa %s к существующей транзакции %s: %s",
                            payment.yookassa_payment_id,
                            existing_transaction.id,
                            link_error,
                            exc_info=True,
                        )
                
                return True

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

            invoice_message = payment_metadata.get("invoice_message") or {}
            if getattr(self, "bot", None):
                chat_id = invoice_message.get("chat_id")
                message_id = invoice_message.get("message_id")
                if chat_id and message_id:
                    try:
                        await self.bot.delete_message(chat_id, message_id)
                    except Exception as delete_error:  # pragma: no cover - depends on bot rights
                        logger.warning(
                            "Не удалось удалить сообщение YooKassa %s: %s",
                            message_id,
                            delete_error,
                        )
                    else:
                        payment_metadata.pop("invoice_message", None)

            processing_completed = bool(payment_metadata.get("processing_completed"))

            transaction = None

            existing_transaction_id = getattr(payment, "transaction_id", None)
            if existing_transaction_id:
                try:
                    from app.database.crud.transaction import get_transaction_by_id

                    transaction = await get_transaction_by_id(db, existing_transaction_id)
                except Exception as fetch_error:  # pragma: no cover - диагностический лог
                    logger.warning(
                        "Не удалось получить транзакцию %s для платежа YooKassa %s: %s",
                        existing_transaction_id,
                        payment.yookassa_payment_id,
                        fetch_error,
                        exc_info=True,
                    )

                if transaction and processing_completed:
                    logger.info(
                        "Пропускаем повторную обработку платежа YooKassa %s: транзакция %s уже завершила начисление.",
                        payment.yookassa_payment_id,
                        existing_transaction_id,
                    )
                    return True

                if transaction:
                    logger.info(
                        "Транзакция %s для платежа YooKassa %s найдена, но обработка ранее не была завершена — повторяем критические шаги.",
                        existing_transaction_id,
                        payment.yookassa_payment_id,
                    )

            if transaction is None:
                existing_transaction = await payment_module.get_transaction_by_external_id(  # type: ignore[attr-defined]
                    db,
                    payment.yookassa_payment_id,
                    PaymentMethod.YOOKASSA,
                )
                
                if existing_transaction:
                    # Если транзакция уже существует, пропускаем обработку
                    logger.info(
                        "Платеж YooKassa %s уже был обработан транзакцией %s. Пропускаем повторную обработку.",
                        payment.yookassa_payment_id,
                        existing_transaction.id,
                    )
                    
                    # Убедимся, что платеж связан с транзакцией
                    if not getattr(payment, "transaction_id", None):
                        try:
                            linked_payment = await payment_module.link_yookassa_payment_to_transaction(  # type: ignore[attr-defined]
                                db,
                                payment.yookassa_payment_id,
                                existing_transaction.id,
                            )
                            if linked_payment:
                                payment.transaction_id = getattr(
                                    linked_payment,
                                    "transaction_id",
                                    existing_transaction.id,
                                )
                                if hasattr(linked_payment, "transaction"):
                                    payment.transaction = linked_payment.transaction
                        except Exception as link_error:  # pragma: no cover - защитный лог
                            logger.warning(
                                "Не удалось привязать платеж YooKassa %s к существующей транзакции %s: %s",
                                payment.yookassa_payment_id,
                                existing_transaction.id,
                                link_error,
                                exc_info=True,
                            )
                    
                    return True

            payment_description = getattr(payment, "description", "YooKassa платеж")

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

            if transaction is None:
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

            if not getattr(payment, "transaction_id", None):
                linked_payment = await payment_module.link_yookassa_payment_to_transaction(
                    db,
                    payment.yookassa_payment_id,
                    transaction.id,
                )

                if linked_payment:
                    payment.transaction_id = getattr(linked_payment, "transaction_id", transaction.id)
                    if hasattr(linked_payment, "transaction"):
                        payment.transaction = linked_payment.transaction

            critical_flow_completed = False
            processing_marked = False

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

                    # Обновляем пользователя с нужными связями, чтобы избежать проблем с ленивой загрузкой
                    from app.database.crud.user import get_user_by_id
                    from sqlalchemy.orm import selectinload
                    from app.database.models import User, Subscription as SubscriptionModel
                    
                    # Загружаем пользователя с подпиской и промо-группой
                    full_user_result = await db.execute(
                        select(User)
                        .options(selectinload(User.subscription))
                        .options(selectinload(User.user_promo_groups))
                        .where(User.id == user.id)
                    )
                    full_user = full_user_result.scalar_one_or_none()

                    # Используем обновленные данные или исходные, если не удалось обновить
                    subscription = full_user.subscription if full_user else getattr(user, "subscription", None)
                    promo_group = full_user.get_primary_promo_group() if full_user else (user.get_primary_promo_group() if hasattr(user, 'get_primary_promo_group') else None)
                    
                    # Используем full_user для форматирования реферальной информации, чтобы избежать проблем с ленивой загрузкой
                    user_for_referrer = full_user if full_user else user
                    referrer_info = format_referrer_info(user_for_referrer)
                    topup_status = (
                        "🆕 Первое пополнение" if was_first_topup else "🔄 Пополнение"
                    )

                    payment_metadata = await self._mark_yookassa_payment_processing_completed(
                        db,
                        payment,
                        payment_metadata,
                        commit=False,
                    )
                    processing_marked = True

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
                            
                            # Обновляем пользователя, чтобы избежать проблем с ленивой загрузкой
                            from app.database.crud.user import get_user_by_id
                            refreshed_user = await get_user_by_id(db, user.id)
                            
                            await notification_service.send_balance_topup_notification(
                                refreshed_user or user,
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
                        logger.info(
                            "Результат проверки корзины для пользователя %s: %s",
                            user.id,
                            has_saved_cart,
                        )

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
                                            callback_data="return_to_saved_cart",
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
                                text=f"✅ Баланс пополнен на {settings.format_price(payment.amount_kopeks)}!\n\n"
                                     f"⚠️ <b>Важно:</b> Пополнение баланса не активирует подписку автоматически. "
                                     f"Обязательно активируйте подписку отдельно!\n\n"
                                     f"🔄 При наличии сохранённой корзины подписки и включенной автопокупке, "
                                     f"подписка будет приобретена автоматически после пополнения баланса.\n\n{cart_message}",
                                reply_markup=keyboard,
                            )
                            logger.info(
                                f"Отправлено уведомление с кнопкой возврата к оформлению подписки пользователю {user.id}"
                            )
                        else:
                            logger.info(
                                "У пользователя %s нет сохраненной корзины, бот недоступен или покупка уже выполнена",
                                user.id,
                            )
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
                                    
                                    # Обновляем пользователя с нужными связями, чтобы избежать проблем с ленивой загрузкой
                                    from sqlalchemy import select
                                    from sqlalchemy.orm import selectinload
                                    from app.database.models import User, Subscription as SubscriptionModel
                                    
                                    # Загружаем пользователя с подпиской и промо-группой
                                    full_user_result = await db.execute(
                                        select(User)
                                        .options(selectinload(User.subscription))
                                        .options(selectinload(User.user_promo_groups))
                                        .where(User.id == user.id)
                                    )
                                    full_user = full_user_result.scalar_one_or_none()
                                    
                                    # Загружаем подписку отдельно, если нужно
                                    subscription_result = await db.execute(
                                        select(SubscriptionModel)
                                        .where(SubscriptionModel.user_id == user.id)
                                    )
                                    subscription_db = subscription_result.scalar_one_or_none()
                                    
                                    await notification_service.send_subscription_purchase_notification(
                                        db,
                                        full_user or user,
                                        subscription_db or subscription,
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

                    if not processing_marked:
                        payment_metadata = await self._mark_yookassa_payment_processing_completed(
                            db,
                            payment,
                            payment_metadata,
                            commit=True,
                        )
                        processing_marked = True

                critical_flow_completed = True
            else:
                logger.warning(
                    "Пользователь %s для платежа YooKassa %s не найден — начисление баланса невозможно",
                    payment.user_id,
                    payment.yookassa_payment_id,
                )

            if critical_flow_completed and not processing_marked:
                payment_metadata = await self._mark_yookassa_payment_processing_completed(
                    db,
                    payment,
                    payment_metadata,
                    commit=True,
                )

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

    async def _mark_yookassa_payment_processing_completed(
        self,
        db: AsyncSession,
        payment: "YooKassaPayment",
        payment_metadata: Dict[str, Any],
        *,
        commit: bool = False,
    ) -> Dict[str, Any]:
        """Отмечает платёж как полностью обработанный, чтобы избежать повторного начисления."""

        if payment_metadata.get("processing_completed"):
            return payment_metadata

        updated_metadata = dict(payment_metadata)
        updated_metadata["processing_completed"] = True

        try:
            from sqlalchemy import update
            from app.database.models import YooKassaPayment as YooKassaPaymentModel

            await db.execute(
                update(YooKassaPaymentModel)
                .where(YooKassaPaymentModel.id == payment.id)
                .values(metadata_json=updated_metadata, updated_at=datetime.utcnow())
            )
            if commit:
                await db.commit()
            else:
                await db.flush()
            payment.metadata_json = updated_metadata
        except Exception as mark_error:  # pragma: no cover - защитный лог
            logger.warning(
                "Не удалось отметить платеж YooKassa %s как завершенный: %s",
                payment.yookassa_payment_id,
                mark_error,
                exc_info=True,
            )

        return updated_metadata

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

        remote_data: Optional[Dict[str, Any]] = None
        if getattr(self, "yookassa_service", None):
            try:
                remote_data = await self.yookassa_service.get_payment_info(  # type: ignore[union-attr]
                    yookassa_payment_id
                )
            except Exception as error:  # pragma: no cover - диагностический лог
                logger.warning(
                    "Не удалось запросить актуальный статус платежа YooKassa %s: %s",
                    yookassa_payment_id,
                    error,
                    exc_info=True,
                )

        if remote_data:
            previous_status = event_object.get("status")
            event_object = self._merge_remote_yookassa_payload(event_object, remote_data)
            if previous_status and event_object.get("status") != previous_status:
                logger.info(
                    "Статус платежа YooKassa %s скорректирован по данным API: %s → %s",
                    yookassa_payment_id,
                    previous_status,
                    event_object.get("status"),
                )
            event["object"] = event_object

        payment_module = import_module("app.services.payment_service")

        payment = await payment_module.get_yookassa_payment_by_id(db, yookassa_payment_id)
        if not payment:
            logger.warning(
                "Локальный платеж для YooKassa id %s не найден", yookassa_payment_id
            )
            payment = await self._restore_missing_yookassa_payment(db, event_object)

            if not payment:
                logger.error(
                    "Не удалось восстановить локальную запись платежа YooKassa %s",
                    yookassa_payment_id,
                )
                return False

        payment.status = event_object.get("status", payment.status)
        payment.confirmation_url = self._extract_confirmation_url(event_object)

        payment.payment_method_type = (
            (event_object.get("payment_method") or {}).get("type")
            or payment.payment_method_type
        )
        payment.refundable = event_object.get("refundable", getattr(payment, "refundable", False))

        current_paid = bool(getattr(payment, "is_paid", getattr(payment, "paid", False)))
        payment.is_paid = bool(event_object.get("paid", current_paid))

        captured_at_raw = event_object.get("captured_at")
        if captured_at_raw:
            try:
                payment.captured_at = datetime.fromisoformat(
                    captured_at_raw.replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except Exception as error:
                logger.debug(
                    "Не удалось распарсить captured_at=%s: %s",
                    captured_at_raw,
                    error,
                )

        await db.commit()
        await db.refresh(payment)

        if payment.status == "succeeded" and payment.is_paid:
            return await self._process_successful_yookassa_payment(db, payment)

        logger.info(
            "Webhook YooKassa обновил платеж %s до статуса %s",
            yookassa_payment_id,
            payment.status,
        )
        return True

    async def _restore_missing_yookassa_payment(
        self,
        db: AsyncSession,
        event_object: Dict[str, Any],
    ) -> Optional["YooKassaPayment"]:
        """Создает локальную запись платежа на основе данных webhook, если она отсутствует."""

        yookassa_payment_id = event_object.get("id")
        if not yookassa_payment_id:
            return None

        metadata = self._normalise_yookassa_metadata(event_object.get("metadata"))
        user_id_raw = metadata.get("user_id") or metadata.get("userId")

        if user_id_raw is None:
            logger.error(
                "Webhook YooKassa %s не содержит user_id в metadata. Невозможно восстановить платеж.",
                yookassa_payment_id,
            )
            return None

        try:
            user_id = int(user_id_raw)
        except (TypeError, ValueError):
            logger.error(
                "Webhook YooKassa %s содержит некорректный user_id=%s",
                yookassa_payment_id,
                user_id_raw,
            )
            return None

        amount_info = event_object.get("amount") or {}
        amount_value = amount_info.get("value")
        currency = (amount_info.get("currency") or "RUB").upper()

        if amount_value is None:
            logger.error(
                "Webhook YooKassa %s не содержит сумму платежа",
                yookassa_payment_id,
            )
            return None

        try:
            amount_kopeks = int((Decimal(str(amount_value)) * 100).quantize(Decimal("1")))
        except (InvalidOperation, ValueError) as error:
            logger.error(
                "Некорректная сумма в webhook YooKassa %s: %s (%s)",
                yookassa_payment_id,
                amount_value,
                error,
            )
            return None

        description = event_object.get("description") or metadata.get("description") or "YooKassa платеж"
        payment_method_type = (event_object.get("payment_method") or {}).get("type")

        yookassa_created_at = None
        created_at_raw = event_object.get("created_at")
        if created_at_raw:
            try:
                yookassa_created_at = datetime.fromisoformat(
                    created_at_raw.replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except Exception as error:  # pragma: no cover - диагностический лог
                logger.debug(
                    "Не удалось распарсить created_at=%s для YooKassa %s: %s",
                    created_at_raw,
                    yookassa_payment_id,
                    error,
                )

        payment_module = import_module("app.services.payment_service")

        local_payment = await payment_module.create_yookassa_payment(
            db=db,
            user_id=user_id,
            yookassa_payment_id=yookassa_payment_id,
            amount_kopeks=amount_kopeks,
            currency=currency,
            description=description,
            status=event_object.get("status", "pending"),
            confirmation_url=self._extract_confirmation_url(event_object),
            metadata_json=metadata,
            payment_method_type=payment_method_type,
            yookassa_created_at=yookassa_created_at,
            test_mode=bool(event_object.get("test") or event_object.get("test_mode")),
        )

        if not local_payment:
            return None

        await payment_module.update_yookassa_payment_status(
            db=db,
            yookassa_payment_id=yookassa_payment_id,
            status=event_object.get("status", local_payment.status),
            is_paid=bool(event_object.get("paid")),
            is_captured=event_object.get("status") == "succeeded",
            captured_at=self._parse_datetime(event_object.get("captured_at")),
            payment_method_type=payment_method_type,
        )

        return await payment_module.get_yookassa_payment_by_id(db, yookassa_payment_id)

    @staticmethod
    def _normalise_yookassa_metadata(metadata: Any) -> Dict[str, Any]:
        if isinstance(metadata, dict):
            return metadata

        if isinstance(metadata, list):
            normalised: Dict[str, Any] = {}
            for item in metadata:
                key = item.get("key") if isinstance(item, dict) else None
                if key:
                    normalised[key] = item.get("value")
            return normalised

        if isinstance(metadata, str):
            try:
                import json

                parsed = json.loads(metadata)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                logger.debug("Не удалось распарсить metadata webhook YooKassa: %s", metadata)

        return {}

    @staticmethod
    def _extract_confirmation_url(event_object: Dict[str, Any]) -> Optional[str]:
        if "confirmation_url" in event_object:
            return event_object.get("confirmation_url")

        confirmation = event_object.get("confirmation")
        if isinstance(confirmation, dict):
            return confirmation.get("confirmation_url") or confirmation.get("return_url")

        return None

    @staticmethod
    def _parse_datetime(raw_value: Optional[str]) -> Optional[datetime]:
        if not raw_value:
            return None

        try:
            return datetime.fromisoformat(raw_value.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            return None
