"""Mixin providing integration with the WATA acquiring platform."""

from __future__ import annotations

import logging
from datetime import datetime
from importlib import import_module
import uuid
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import PaymentMethod, TransactionType
from app.utils.user_utils import format_referrer_info

logger = logging.getLogger(__name__)


class WataPaymentMixin:
    """Adds helper methods for creating and processing WATA payments."""

    async def create_wata_payment(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        amount_kopeks: int,
        description: str,
        language: str,
    ) -> Optional[Dict[str, Any]]:
        service = getattr(self, "wata_service", None)
        if not service or not service.is_configured:
            logger.error("WATA service is not initialised or configured")
            return None

        if amount_kopeks < settings.WATA_MIN_AMOUNT_KOPEKS:
            logger.warning(
                "WATA payment amount below minimum: %s < %s",
                amount_kopeks,
                settings.WATA_MIN_AMOUNT_KOPEKS,
            )
            return None

        if amount_kopeks > settings.WATA_MAX_AMOUNT_KOPEKS:
            logger.warning(
                "WATA payment amount above maximum: %s > %s",
                amount_kopeks,
                settings.WATA_MAX_AMOUNT_KOPEKS,
            )
            return None

        order_id = f"wata_{user_id}_{uuid.uuid4().hex}"
        link_type = settings.get_wata_link_type()

        try:
            response = await service.create_payment_link(
                amount_kopeks=amount_kopeks,
                currency="RUB",
                description=description[:512],
                order_id=order_id,
                link_type=link_type,
                success_redirect_url=settings.get_wata_success_redirect_url(),
                fail_redirect_url=settings.get_wata_fail_redirect_url(),
            )
        except Exception as error:  # pragma: no cover - network safety
            logger.error("Ошибка создания WATA платежа: %s", error, exc_info=True)
            return None

        if not response:
            logger.error("WATA API вернул пустой ответ при создании ссылки")
            return None

        link_id = response.get("id")
        payment_url = response.get("url")

        if not link_id or not payment_url:
            logger.error("WATA API не вернул link_id/url: %s", response)
            return None

        payment_module = import_module("app.services.payment_service")

        metadata = {
            "raw_response": response,
            "language": language,
        }

        local_payment = await payment_module.create_wata_payment(
            db,
            user_id=user_id,
            link_id=str(link_id),
            order_id=order_id,
            amount_kopeks=amount_kopeks,
            currency=response.get("currency", "RUB"),
            description=description,
            link_status=response.get("status"),
            payment_url=payment_url,
            metadata=metadata,
        )

        logger.info(
            "Создан WATA платеж для пользователя %s: %s₽, ссылка %s",
            user_id,
            amount_kopeks / 100,
            payment_url,
        )

        return {
            "local_payment_id": local_payment.id,
            "link_id": str(link_id),
            "order_id": order_id,
            "amount_kopeks": amount_kopeks,
            "payment_url": payment_url,
            "status": response.get("status"),
        }

    async def process_wata_webhook(
        self,
        db: AsyncSession,
        payload: Dict[str, Any],
    ) -> bool:
        payment_module = import_module("app.services.payment_service")

        transaction_uuid = self._extract_str(payload, "transactionId")
        payment_link_id = self._extract_str(payload, "paymentLinkId")
        order_id = self._extract_str(payload, "orderId")
        link_status = self._extract_str(payload, "paymentLinkStatus")
        transaction_status = self._extract_str(payload, "transactionStatus")

        if not any([transaction_uuid, payment_link_id, order_id]):
            logger.error("WATA webhook missing identifiers: %s", payload)
            return False

        payment = None
        if transaction_uuid:
            payment = await payment_module.get_wata_payment_by_transaction_uuid(
                db, transaction_uuid
            )
        if not payment and payment_link_id:
            payment = await payment_module.get_wata_payment_by_link_id(db, payment_link_id)
        if not payment and order_id:
            payment = await payment_module.get_wata_payment_by_order_id(db, order_id)

        if not payment:
            logger.error(
                "WATA платеж не найден (transaction=%s, link=%s, order=%s)",
                transaction_uuid,
                payment_link_id,
                order_id,
            )
            return False

        await self._sync_wata_payment_fields(
            db,
            payment,
            transaction_uuid=transaction_uuid,
            transaction_status=transaction_status,
            link_status=link_status,
            callback_payload=payload,
        )

        if transaction_status and transaction_status.lower() == "paid":
            return await self._finalize_wata_payment(
                db,
                payment,
                transaction_uuid or payment.transaction_uuid,
                payload,
            )

        logger.info(
            "Обновлен статус WATA платежа %s: %s",
            payment.id,
            transaction_status or link_status,
        )
        return True

    async def get_wata_payment_status(
        self,
        db: AsyncSession,
        local_payment_id: int,
    ) -> Optional[Dict[str, Any]]:
        payment_module = import_module("app.services.payment_service")
        payment = await payment_module.get_wata_payment_by_id(db, local_payment_id)
        if not payment:
            return None

        service = getattr(self, "wata_service", None)
        remote_status = None
        remote_payload = None

        if service:
            try:
                response = await service.find_transactions(
                    order_id=payment.order_id,
                    payment_link_id=payment.link_id,
                    max_results=1,
                )
            except Exception as error:  # pragma: no cover - network safety
                logger.error("Ошибка запроса статуса WATA: %s", error, exc_info=True)
                response = None

            if response and response.get("items"):
                item = response["items"][0]
                remote_status = item.get("status")
                remote_payload = item
                transaction_id = self._extract_str(item, "id")
                await self._sync_wata_payment_fields(
                    db,
                    payment,
                    transaction_uuid=transaction_id,
                    transaction_status=remote_status,
                    callback_payload=item,
                )
                if remote_status and remote_status.lower() == "paid":
                    await self._finalize_wata_payment(db, payment, transaction_id, item)

        return {
            "payment": payment,
            "status": payment.transaction_status or payment.link_status,
            "is_paid": payment.is_paid,
            "remote_status": remote_status,
            "remote_data": remote_payload,
        }

    async def _sync_wata_payment_fields(
        self,
        db: AsyncSession,
        payment: Any,
        *,
        transaction_uuid: Optional[str],
        transaction_status: Optional[str],
        link_status: Optional[str],
        callback_payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        updated = False
        if transaction_uuid and payment.transaction_uuid != transaction_uuid:
            payment.transaction_uuid = transaction_uuid
            updated = True
        if transaction_status and payment.transaction_status != transaction_status:
            payment.transaction_status = transaction_status
            updated = True
        if link_status and payment.link_status != link_status:
            payment.link_status = link_status
            updated = True
        if callback_payload is not None:
            payment.callback_payload = callback_payload
            updated = True

        if updated:
            payment.updated_at = datetime.utcnow()
            await db.commit()
            await db.refresh(payment)

    @staticmethod
    def _extract_str(payload: Dict[str, Any], key: str) -> Optional[str]:
        value = payload.get(key) or payload.get(key[0].lower() + key[1:])
        if value is None:
            return None
        value_str = str(value).strip()
        return value_str or None

    async def _finalize_wata_payment(
        self,
        db: AsyncSession,
        payment: Any,
        transaction_uuid: Optional[str],
        payload: Dict[str, Any],
    ) -> bool:
        if payment.is_paid:
            logger.info("WATA платеж %s уже обработан", payment.id)
            return True

        payment_module = import_module("app.services.payment_service")

        user = getattr(payment, "user", None)
        if not user or getattr(user, "id", None) is None:
            user = await payment_module.get_user_by_id(db, payment.user_id)
        if not user:
            logger.error("Пользователь не найден для WATA платежа %s", payment.id)
            return False

        if payment.transaction_id:
            payment.is_paid = True
            payment.paid_at = datetime.utcnow()
            await db.commit()
            await db.refresh(payment)
            return True

        description = settings.get_balance_payment_description(payment.amount_kopeks)
        transaction = await payment_module.create_transaction(
            db,
            user_id=payment.user_id,
            type=TransactionType.DEPOSIT,
            amount_kopeks=payment.amount_kopeks,
            description=description,
            payment_method=PaymentMethod.WATA,
            external_id=transaction_uuid or payment.link_id,
            is_completed=True,
        )

        await payment_module.link_wata_payment_to_transaction(db, payment, transaction.id)

        old_balance = user.balance_kopeks
        was_first_topup = not user.has_made_first_topup

        user.balance_kopeks += payment.amount_kopeks
        user.updated_at = datetime.utcnow()

        payment.is_paid = True
        payment.paid_at = datetime.utcnow()
        if transaction_uuid:
            payment.transaction_uuid = transaction_uuid
        payment.transaction_status = "Paid"
        payment.callback_payload = payload

        await db.commit()

        try:
            from app.services.referral_service import process_referral_topup

            await process_referral_topup(db, user.id, payment.amount_kopeks, getattr(self, "bot", None))
        except Exception as error:
            logger.error("Ошибка обработки реферального пополнения WATA: %s", error)

        if was_first_topup and not user.has_made_first_topup:
            user.has_made_first_topup = True
            await db.commit()

        await db.refresh(user)
        await db.refresh(payment)

        promo_group = getattr(user, "promo_group", None)
        subscription = getattr(user, "subscription", None)
        referrer_info = format_referrer_info(user)
        topup_status = "🆕 Первое пополнение" if was_first_topup else "🔄 Пополнение"

        if getattr(self, "bot", None):
            try:
                from app.services.admin_notification_service import AdminNotificationService

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
                logger.error("Ошибка отправки админ уведомления WATA: %s", error)

        if getattr(self, "bot", None):
            try:
                keyboard = await self.build_topup_success_keyboard(user)
                await self.bot.send_message(
                    user.telegram_id,
                    (
                        "✅ <b>Пополнение успешно!</b>\n\n"
                        f"💰 Сумма: {settings.format_price(payment.amount_kopeks)}\n"
                        "💳 Способ: WATA (карта)\n"
                        f"🆔 Транзакция: {transaction.id}\n\n"
                        "Баланс пополнен автоматически!"
                    ),
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
            except Exception as error:
                logger.error("Ошибка отправки уведомления пользователю WATA: %s", error)

        logger.info(
            "✅ Обработан WATA платеж %s для пользователя %s",
            payment.id,
            payment.user_id,
        )
        return True
