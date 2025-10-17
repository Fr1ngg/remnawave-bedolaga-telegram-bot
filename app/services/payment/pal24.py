"""Mixin для интеграции с PayPalych (Pal24)."""

from __future__ import annotations

import logging
from datetime import datetime
from importlib import import_module
import uuid
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import PaymentMethod, TransactionType
from app.services.pal24_service import Pal24APIError
from app.utils.user_utils import format_referrer_info

logger = logging.getLogger(__name__)


class Pal24PaymentMixin:
    """Mixin с созданием счетов Pal24, обработкой postback и запросом статуса."""

    async def create_pal24_payment(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        amount_kopeks: int,
        description: str,
        language: str,
        ttl_seconds: Optional[int] = None,
        payer_email: Optional[str] = None,
        payment_method: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Создаёт счёт в Pal24 и сохраняет локальную запись."""
        service = getattr(self, "pal24_service", None)
        if not service or not service.is_configured:
            logger.error("Pal24 сервис не инициализирован")
            return None

        if amount_kopeks < settings.PAL24_MIN_AMOUNT_KOPEKS:
            logger.warning(
                "Сумма Pal24 меньше минимальной: %s < %s",
                amount_kopeks,
                settings.PAL24_MIN_AMOUNT_KOPEKS,
            )
            return None

        if amount_kopeks > settings.PAL24_MAX_AMOUNT_KOPEKS:
            logger.warning(
                "Сумма Pal24 больше максимальной: %s > %s",
                amount_kopeks,
                settings.PAL24_MAX_AMOUNT_KOPEKS,
            )
            return None

        order_id = f"pal24_{user_id}_{uuid.uuid4().hex}"

        custom_payload = {
            "user_id": user_id,
            "amount_kopeks": amount_kopeks,
            "language": language,
        }

        normalized_payment_method = self._normalize_payment_method(payment_method)
        api_payment_method = self._map_api_payment_method(normalized_payment_method)

        payment_module = import_module("app.services.payment_service")

        try:
            response = await service.create_bill(
                amount_kopeks=amount_kopeks,
                user_id=user_id,
                order_id=order_id,
                description=description,
                ttl_seconds=ttl_seconds,
                custom_payload=custom_payload,
                payer_email=payer_email,
                payment_method=api_payment_method,
            )
        except Pal24APIError as error:
            logger.error("Ошибка Pal24 API при создании счета: %s", error)
            return None

        if not response.get("success", True):
            logger.error("Pal24 вернул ошибку при создании счета: %s", response)
            return None

        bill_id = response.get("bill_id")
        if not bill_id:
            logger.error("Pal24 не вернул bill_id: %s", response)
            return None

        def _pick_url(*keys: str) -> Optional[str]:
            for key in keys:
                value = response.get(key)
                if value:
                    return str(value)
            return None

        transfer_url = _pick_url(
            "transfer_url",
            "transferUrl",
            "transfer_link",
            "transferLink",
            "transfer",
            "sbp_url",
            "sbpUrl",
            "sbp_link",
            "sbpLink",
        )
        card_url = _pick_url(
            "link_url",
            "linkUrl",
            "link",
            "card_url",
            "cardUrl",
            "card_link",
            "cardLink",
            "payment_url",
            "paymentUrl",
            "url",
        )
        link_page_url = _pick_url(
            "link_page_url",
            "linkPageUrl",
            "page_url",
            "pageUrl",
        )

        primary_link = transfer_url or link_page_url or card_url
        secondary_link = link_page_url or card_url or transfer_url

        metadata_links = {
            key: value
            for key, value in {
                "sbp": transfer_url,
                "card": card_url,
                "page": link_page_url,
            }.items()
            if value
        }

        metadata_payload = {
            "user_id": user_id,
            "amount_kopeks": amount_kopeks,
            "description": description,
            "links": metadata_links,
            "raw_response": response,
            "selected_method": normalized_payment_method,
        }

        payment = await payment_module.create_pal24_payment(
            db,
            user_id=user_id,
            bill_id=bill_id,
            amount_kopeks=amount_kopeks,
            description=description,
            status=response.get("status", "NEW"),
            type_=response.get("type", "normal"),
            currency=response.get("currency", "RUB"),
            link_url=transfer_url or card_url,
            link_page_url=link_page_url or primary_link,
            order_id=order_id,
            ttl=ttl_seconds,
            metadata=metadata_payload,
        )

        logger.info(
            "Создан Pal24 счет %s для пользователя %s (%s₽)",
            bill_id,
            user_id,
            amount_kopeks / 100,
        )

        payment_status = getattr(payment, "status", response.get("status", "NEW"))

        return {
            "local_payment_id": payment.id,
            "bill_id": bill_id,
            "order_id": order_id,
            "amount_kopeks": amount_kopeks,
            "primary_url": primary_link,
            "secondary_url": secondary_link,
            "link_url": transfer_url,
            "card_url": card_url,
            "payment_method": normalized_payment_method,
            "metadata_links": metadata_links,
            "status": payment_status,
            "sbp_url": transfer_url,
            "transfer_url": transfer_url,
            "link_page_url": link_page_url,
            "payment_url": primary_link,
        }

    async def process_pal24_postback(
        self,
        db: AsyncSession,
        postback: Dict[str, Any],
    ) -> bool:
        """Обрабатывает postback от Pal24 и начисляет баланс при успехе."""
        try:
            payment_module = import_module("app.services.payment_service")

            def _first_non_empty(*values: Optional[str]) -> Optional[str]:
                for value in values:
                    if value:
                        return value
                return None

            payment_id = _first_non_empty(
                postback.get("id"),
                postback.get("TrsId"),
                postback.get("TrsID"),
            )
            bill_id = _first_non_empty(
                postback.get("bill_id"),
                postback.get("billId"),
                postback.get("BillId"),
                postback.get("BillID"),
            )
            order_id = _first_non_empty(
                postback.get("order_id"),
                postback.get("orderId"),
                postback.get("InvId"),
                postback.get("InvID"),
            )
            status = (postback.get("status") or postback.get("Status") or "").upper()

            if not bill_id and not order_id:
                logger.error("Pal24 postback без идентификаторов: %s", postback)
                return False

            payment = None
            if bill_id:
                payment = await payment_module.get_pal24_payment_by_bill_id(db, bill_id)
            if not payment and order_id:
                payment = await payment_module.get_pal24_payment_by_order_id(db, order_id)

            if not payment:
                logger.error("Pal24 платеж не найден: %s / %s", bill_id, order_id)
                return False

            if payment.is_paid:
                logger.info("Pal24 платеж %s уже обработан", payment.bill_id)
                return True

            if status in {"PAID", "SUCCESS", "OVERPAID"}:
                user = await payment_module.get_user_by_id(db, payment.user_id)
                if not user:
                    logger.error(
                        "Пользователь %s не найден для Pal24 платежа",
                        payment.user_id,
                    )
                    return False

                await payment_module.update_pal24_payment_status(
                    db,
                    payment,
                    status=status,
                    is_paid=True,
                    paid_at=datetime.utcnow(),
                    postback_payload=postback,
                    payment_id=payment_id,
                    payment_status=postback.get("Status") or status,
                    payment_method=(
                        postback.get("payment_method")
                        or postback.get("PaymentMethod")
                        or (payment.metadata_json or {}).get("selected_method")
                        or getattr(payment, "payment_method", None)
                    ),
                )

                if payment.transaction_id:
                    logger.info(
                        "Для Pal24 платежа %s уже создана транзакция",
                        payment.bill_id,
                    )
                    return True

                transaction = await payment_module.create_transaction(
                    db,
                    user_id=payment.user_id,
                    type=TransactionType.DEPOSIT,
                    amount_kopeks=payment.amount_kopeks,
                    description=f"Пополнение через Pal24 ({payment_id})",
                    payment_method=PaymentMethod.PAL24,
                    external_id=str(payment_id) if payment_id else payment.bill_id,
                    is_completed=True,
                )

                await payment_module.link_pal24_payment_to_transaction(db, payment, transaction.id)

                old_balance = user.balance_kopeks
                was_first_topup = not user.has_made_first_topup

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
                        db, user.id, payment.amount_kopeks, getattr(self, "bot", None)
                    )
                except Exception as error:
                    logger.error(
                        "Ошибка обработки реферального пополнения Pal24: %s",
                        error,
                    )

                if was_first_topup and not user.has_made_first_topup:
                    user.has_made_first_topup = True
                    await db.commit()

                await db.refresh(user)

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
                            "Ошибка отправки админ уведомления Pal24: %s", error
                        )

                if getattr(self, "bot", None):
                    try:
                        keyboard = await self.build_topup_success_keyboard(user)
                        await self.bot.send_message(
                            user.telegram_id,
                            (
                                "✅ <b>Пополнение успешно!</b>\n\n"
                                f"💰 Сумма: {settings.format_price(payment.amount_kopeks)}\n"
                                "🦊 Способ: PayPalych\n"
                                f"🆔 Транзакция: {transaction.id}\n\n"
                                "Баланс пополнен автоматически!"
                            ),
                            parse_mode="HTML",
                            reply_markup=keyboard,
                        )
                    except Exception as error:
                        logger.error(
                            "Ошибка отправки уведомления пользователю Pal24: %s",
                            error,
                        )

                # Проверяем наличие сохраненной корзины для возврата к оформлению подписки
                try:
                    from app.services.user_cart_service import user_cart_service
                    from aiogram import types
                    has_saved_cart = await user_cart_service.has_user_cart(user.id)
                    if has_saved_cart and getattr(self, "bot", None):
                        # Если у пользователя есть сохраненная корзина, 
                        # отправляем ему уведомление с кнопкой вернуться к оформлению
                        from app.localization.texts import get_texts
                        
                        texts = get_texts(user.language)
                        cart_message = texts.t(
                            "BALANCE_TOPUP_CART_REMINDER_DETAILED",
                            "🛒 У вас есть неоформленный заказ.\n\n"
                            "Вы можете продолжить оформление с теми же параметрами."
                        )
                        
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
                            text=f"✅ Баланс пополнен на {settings.format_price(payment.amount_kopeks)}!\n\n{cart_message}",
                            reply_markup=keyboard
                        )
                        logger.info(f"Отправлено уведомление с кнопкой возврата к оформлению подписки пользователю {user.id}")
                except Exception as e:
                    logger.error(f"Ошибка при работе с сохраненной корзиной для пользователя {user.id}: {e}", exc_info=True)

                logger.info(
                    "✅ Обработан Pal24 платеж %s для пользователя %s",
                    payment.bill_id,
                    payment.user_id,
                )

                return True

            await payment_module.update_pal24_payment_status(
                db,
                payment,
                status=status or "UNKNOWN",
                is_paid=False,
                postback_payload=postback,
                payment_id=payment_id,
                payment_status=postback.get("Status") or status,
                payment_method=(
                    postback.get("payment_method")
                    or postback.get("PaymentMethod")
                    or getattr(payment, "payment_method", None)
                ),
            )
            logger.info(
                "Обновили Pal24 платеж %s до статуса %s",
                payment.bill_id,
                status,
            )
            return True

        except Exception as error:
            logger.error("Ошибка обработки Pal24 postback: %s", error, exc_info=True)
            return False

    async def get_pal24_payment_status(
        self,
        db: AsyncSession,
        local_payment_id: int,
    ) -> Optional[Dict[str, Any]]:
        """Запрашивает актуальный статус платежа у Pal24 и синхронизирует локальную запись."""
        try:
            payment_module = import_module("app.services.payment_service")

            payment = await payment_module.get_pal24_payment_by_id(db, local_payment_id)
            if not payment:
                return None

            remote_status = None
            remote_data = None

            service = getattr(self, "pal24_service", None)
            if service and payment.bill_id:
                try:
                    response = await service.get_bill_status(payment.bill_id)
                    remote_data = response
                    remote_status = response.get("status") or response.get(
                        "bill", {}
                    ).get("status")

                    if remote_status:
                        normalized_remote = str(remote_status).upper()
                        if normalized_remote != payment.status:
                            update_kwargs: Dict[str, Any] = {
                                "status": normalized_remote,
                                "payment_status": remote_status,
                            }
                            if normalized_remote in getattr(
                                service, "BILL_SUCCESS_STATES", {"SUCCESS"}
                            ):
                                update_kwargs["is_paid"] = True
                                if not payment.paid_at:
                                    update_kwargs["paid_at"] = datetime.utcnow()
                            elif normalized_remote in getattr(
                                service, "BILL_FAILED_STATES", {"FAIL"}
                            ):
                                update_kwargs["is_paid"] = False

                            await payment_module.update_pal24_payment_status(
                                db,
                                payment,
                                **update_kwargs,
                            )
                        payment = await payment_module.get_pal24_payment_by_id(
                            db, local_payment_id
                        )
                except Pal24APIError as error:
                    logger.error(
                        "Ошибка Pal24 API при получении статуса: %s", error
                    )

            return {
                "payment": payment,
                "status": payment.status,
                "is_paid": payment.is_paid,
                "remote_status": remote_status,
                "remote_data": remote_data,
            }

        except Exception as error:
            logger.error("Ошибка получения статуса Pal24: %s", error, exc_info=True)
            return None

    @staticmethod
    def _normalize_payment_method(payment_method: Optional[str]) -> str:
        mapping = {
            "sbp": "sbp",
            "fast": "sbp",
            "fastpay": "sbp",
            "fast_payment": "sbp",
            "card": "card",
            "bank_card": "card",
            "bankcard": "card",
            "bank-card": "card",
        }

        if not payment_method:
            return "sbp"

        normalized = payment_method.strip().lower()
        return mapping.get(normalized, "sbp")

    @staticmethod
    def _map_api_payment_method(normalized_payment_method: str) -> Optional[str]:
        """Преобразует нормализованный метод оплаты в значение для Pal24 API."""

        api_mapping = {
            "sbp": "SBP",
            "card": "BANK_CARD",
        }

        return api_mapping.get(normalized_payment_method)
