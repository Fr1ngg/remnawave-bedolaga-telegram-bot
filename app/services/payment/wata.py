"""Mixin для интеграции с платёжным шлюзом WATA."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from decimal import Decimal
from importlib import import_module
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import PaymentMethod, TransactionType
from app.utils.user_utils import format_referrer_info

logger = logging.getLogger(__name__)


class WataPaymentMixin:
    """Функциональность создания платежных ссылок и обработки уведомлений WATA."""

    async def create_wata_payment(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        amount_kopeks: int,
        description: str,
        language: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        service = getattr(self, "wata_service", None)
        if not service or not service.is_configured:
            logger.error("Wata сервис не инициализирован")
            return None

        if amount_kopeks < settings.WATA_MIN_AMOUNT_KOPEKS:
            logger.warning(
                "Сумма Wata меньше минимальной: %s < %s",
                amount_kopeks,
                settings.WATA_MIN_AMOUNT_KOPEKS,
            )
            return None

        if amount_kopeks > settings.WATA_MAX_AMOUNT_KOPEKS:
            logger.warning(
                "Сумма Wata больше максимальной: %s > %s",
                amount_kopeks,
                settings.WATA_MAX_AMOUNT_KOPEKS,
            )
            return None

        payment_module = import_module("app.services.payment_service")
        order_id = f"wata_{user_id}_{uuid.uuid4().hex}"

        metadata: Dict[str, Any] = {
            "user_id": user_id,
            "amount_kopeks": amount_kopeks,
            "language": language or "ru",
        }

        response = await service.create_payment_link(
            amount_kopeks=amount_kopeks,
            description=description,
            order_id=order_id,
        )

        if not response:
            logger.error("Wata API не вернул ответ при создании ссылки")
            return None

        link_id = response.get("id") or response.get("paymentLinkId")
        payment_url = response.get("url")
        status = response.get("status") or "OPENED"
        currency = response.get("currency") or settings.WATA_DEFAULT_CURRENCY or "RUB"
        type_ = response.get("type")

        if not link_id or not payment_url:
            logger.error("Некорректный ответ создания Wata ссылки: %s", response)
            return None

        metadata["raw_response"] = response

        payment = await payment_module.create_wata_payment(
            db,
            user_id=user_id,
            payment_link_id=str(link_id),
            order_id=response.get("orderId") or order_id,
            amount_kopeks=amount_kopeks,
            currency=str(currency),
            description=response.get("description") or description,
            status=str(status).upper(),
            url=payment_url,
            type_=type_,
            metadata=metadata,
        )

        logger.info(
            "Создан Wata платёж %s для пользователя %s (%s₽)",
            payment.payment_link_id,
            user_id,
            amount_kopeks / 100,
        )

        return {
            "local_payment_id": payment.id,
            "payment_link_id": payment.payment_link_id,
            "payment_url": payment.url,
            "status": payment.status,
            "order_id": payment.order_id,
            "amount_kopeks": payment.amount_kopeks,
        }

    async def process_wata_webhook(
        self,
        db: AsyncSession,
        payload: Dict[str, Any],
    ) -> bool:
        try:
            payment_module = import_module("app.services.payment_service")

            transaction_uuid = (payload.get("transactionId") or "").strip()
            payment_link_id = (payload.get("paymentLinkId") or "").strip()
            order_id = (payload.get("orderId") or "").strip()
            transaction_status = (payload.get("transactionStatus") or "").strip()
            transaction_type = (payload.get("transactionType") or "").strip()
            error_code = payload.get("errorCode")
            error_description = payload.get("errorDescription")
            payment_time_raw = payload.get("paymentTime")
            amount_value = payload.get("amount")
            currency = payload.get("currency") or settings.WATA_DEFAULT_CURRENCY or "RUB"

            payment = None
            if transaction_uuid:
                payment = await payment_module.get_wata_payment_by_transaction_uuid(
                    db, transaction_uuid
                )

            if not payment and payment_link_id:
                payment = await payment_module.get_wata_payment_by_link_id(
                    db, payment_link_id
                )

            if not payment and order_id:
                payment = await payment_module.get_wata_payment_by_order_id(db, order_id)

            if not payment:
                logger.error(
                    "Wata платеж не найден (transaction=%s, link=%s, order=%s)",
                    transaction_uuid,
                    payment_link_id,
                    order_id,
                )
                return False

            paid_at: Optional[datetime] = None
            if isinstance(payment_time_raw, str) and payment_time_raw:
                try:
                    normalized = payment_time_raw.replace("Z", "+00:00")
                    paid_at = datetime.fromisoformat(normalized)
                except ValueError:
                    paid_at = None

            callback_amount_kopeks: Optional[int] = None
            if amount_value is not None:
                try:
                    callback_amount_kopeks = int(Decimal(str(amount_value)) * 100)
                except (ValueError, ArithmeticError):
                    callback_amount_kopeks = None

            if (
                callback_amount_kopeks is not None
                and callback_amount_kopeks != payment.amount_kopeks
            ):
                logger.warning(
                    "Сумма из webhook Wata (%s) не совпадает с ожидаемой (%s) для платежа %s",
                    callback_amount_kopeks,
                    payment.amount_kopeks,
                    payment.payment_link_id,
                )

            status_to_set = payment.status
            if transaction_status:
                lowered_status = transaction_status.lower()
                if lowered_status == "paid":
                    status_to_set = "CLOSED"
                elif lowered_status in {"declined", "canceled"}:
                    status_to_set = "CLOSED"

            await payment_module.update_wata_payment_status(
                db,
                payment,
                status=status_to_set,
                is_paid=transaction_status.lower() == "paid",
                paid_at=paid_at,
                transaction_uuid=transaction_uuid or payment.transaction_uuid,
                transaction_status=transaction_status or payment.transaction_status,
                transaction_type=transaction_type or payment.transaction_type,
                error_code=error_code,
                error_description=error_description,
                payment_time=paid_at,
                metadata=payment.metadata_json,
                callback_payload=payload,
            )

            payment = await payment_module.get_wata_payment_by_id(db, payment.id)

            if payment.is_paid and payment.transaction_id:
                logger.info(
                    "Wata платеж %s уже обработан, пропускаем повторный webhook",
                    payment.payment_link_id,
                )
                return True

            if transaction_status.lower() != "paid":
                logger.info(
                    "Получен Wata webhook со статусом %s для платежа %s",
                    transaction_status,
                    payment.payment_link_id,
                )
                return True

            transaction_description = (
                payload.get("orderDescription")
                or payment.description
                or settings.get_balance_payment_description(payment.amount_kopeks)
            )

            transaction = await payment_module.create_transaction(
                db,
                user_id=payment.user_id,
                type=TransactionType.DEPOSIT,
                amount_kopeks=payment.amount_kopeks,
                description=f"Пополнение через Wata: {transaction_description}",
                payment_method=PaymentMethod.WATA,
                external_id=transaction_uuid or payment.payment_link_id,
                is_completed=True,
            )

            await payment_module.link_wata_payment_to_transaction(
                db,
                payment,
                transaction_id=transaction.id,
            )

            user = await payment_module.get_user_by_id(db, payment.user_id)
            if not user:
                logger.error(
                    "Пользователь %s не найден при обработке Wata", payment.user_id
                )
                return False

            old_balance = user.balance_kopeks
            was_first_topup = not user.has_made_first_topup

            await payment_module.add_user_balance(
                db,
                user,
                payment.amount_kopeks,
                f"Пополнение Wata: {payment.amount_kopeks // 100}₽",
            )

            if was_first_topup and not user.has_made_first_topup:
                user.has_made_first_topup = True
                await db.commit()
            await db.refresh(user)

            promo_group = getattr(user, "promo_group", None)
            subscription = getattr(user, "subscription", None)
            referrer_info = format_referrer_info(user)
            topup_status = "🆕 Первое пополнение" if was_first_topup else "🔄 Пополнение"

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
                        "Ошибка отправки уведомления о пополнении Wata: %s",
                        error,
                    )

            if getattr(self, "bot", None):
                try:
                    keyboard = await self.build_topup_success_keyboard(user)
                    await self.bot.send_message(
                        user.telegram_id,
                        (
                            "✅ <b>Пополнение успешно!</b>\n\n"
                            f"💰 Сумма: {settings.format_price(payment.amount_kopeks)}\n"
                            "💳 Способ: Wata\n"
                            f"🆔 Транзакция: {transaction.id}\n\n"
                            "Баланс пополнен автоматически!"
                        ),
                        parse_mode="HTML",
                        reply_markup=keyboard,
                    )
                except Exception as error:
                    logger.error(
                        "Ошибка отправки уведомления пользователю о Wata пополнении: %s",
                        error,
                    )

            logger.info(
                "✅ Обработан Wata платеж %s для пользователя %s",
                payment.payment_link_id,
                payment.user_id,
            )
            return True

        except Exception as error:
            logger.error("Ошибка обработки Wata webhook: %s", error, exc_info=True)
            return False

    async def get_wata_payment_status(
        self,
        db: AsyncSession,
        local_payment_id: int,
    ) -> Optional[Dict[str, Any]]:
        try:
            payment_module = import_module("app.services.payment_service")
            payment = await payment_module.get_wata_payment_by_id(db, local_payment_id)
            if not payment:
                return None

            remote_link: Optional[Dict[str, Any]] = None
            remote_transaction: Optional[Dict[str, Any]] = None

            service = getattr(self, "wata_service", None)
            if service:
                if payment.payment_link_id:
                    remote_link = await service.get_payment_link(payment.payment_link_id)
                    if remote_link:
                        await payment_module.update_wata_payment_status(
                            db,
                            payment,
                            status=(remote_link.get("status") or payment.status),
                            url=remote_link.get("url") or payment.url,
                        )
                        payment = await payment_module.get_wata_payment_by_id(
                            db, local_payment_id
                        )

                if payment.transaction_uuid:
                    remote_transaction = await service.get_transaction(
                        payment.transaction_uuid
                    )
                    if remote_transaction:
                        await payment_module.update_wata_payment_status(
                            db,
                            payment,
                            transaction_status=remote_transaction.get("status")
                            or payment.transaction_status,
                            error_code=remote_transaction.get("errorCode"),
                            error_description=remote_transaction.get("errorDescription"),
                        )
                        payment = await payment_module.get_wata_payment_by_id(
                            db, local_payment_id
                        )

            return {
                "payment": payment,
                "remote_link": remote_link,
                "remote_transaction": remote_transaction,
            }
        except Exception as error:
            logger.error("Ошибка получения статуса Wata: %s", error, exc_info=True)
            return None
