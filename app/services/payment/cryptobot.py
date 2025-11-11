"""Mixin с логикой обработки платежей CryptoBot."""

from __future__ import annotations
import logging
import math
from dataclasses import dataclass
from datetime import datetime
from importlib import import_module
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.database import AsyncSessionLocal
from app.database.models import PaymentMethod, TransactionType
from app.services.subscription_auto_purchase_service import (
    auto_purchase_saved_cart_after_topup,
)
from app.services.subscription_renewal_service import (
    SubscriptionRenewalChargeError,
    SubscriptionRenewalPricing,
    SubscriptionRenewalService,
    RenewalPaymentDescriptor,
    build_renewal_period_id,
    decode_payment_payload,
)
from app.utils.currency_converter import currency_converter
from app.utils.user_utils import format_referrer_info

logger = logging.getLogger(__name__)


renewal_service = SubscriptionRenewalService()


@dataclass(slots=True)
class _AdminNotificationContext:
    user_id: int
    transaction_id: int
    old_balance: int
    topup_status: str
    referrer_info: str


@dataclass(slots=True)
class _UserNotificationPayload:
    telegram_id: int
    text: str
    parse_mode: Optional[str]
    reply_markup: Any
    amount_rubles: float
    asset: str


@dataclass(slots=True)
class _SavedCartNotificationPayload:
    telegram_id: int
    text: str
    reply_markup: Any
    user_id: int


class CryptoBotPaymentMixin:
    """Mixin, отвечающий за генерацию инвойсов CryptoBot и обработку webhook."""

    async def create_cryptobot_payment(
        self,
        db: AsyncSession,
        user_id: int,
        amount_usd: float,
        asset: str = "USDT",
        description: str = "Пополнение баланса",
        payload: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Создаёт invoice в CryptoBot и сохраняет локальную запись."""
        if not getattr(self, "cryptobot_service", None):
            logger.error("CryptoBot сервис не инициализирован")
            return None

        try:
            amount_str = f"{amount_usd:.2f}"

            invoice_data = await self.cryptobot_service.create_invoice(
                amount=amount_str,
                asset=asset,
                description=description,
                payload=payload or f"balance_topup_{user_id}_{int(amount_usd * 100)}",
                expires_in=settings.get_cryptobot_invoice_expires_seconds(),
            )

            if not invoice_data:
                logger.error("Ошибка создания CryptoBot invoice")
                return None

            cryptobot_crud = import_module("app.database.crud.cryptobot")

            local_payment = await cryptobot_crud.create_cryptobot_payment(
                db=db,
                user_id=user_id,
                invoice_id=str(invoice_data["invoice_id"]),
                amount=amount_str,
                asset=asset,
                status="active",
                description=description,
                payload=payload,
                bot_invoice_url=invoice_data.get("bot_invoice_url"),
                mini_app_invoice_url=invoice_data.get("mini_app_invoice_url"),
                web_app_invoice_url=invoice_data.get("web_app_invoice_url"),
            )

            logger.info(
                "Создан CryptoBot платеж %s на %s %s для пользователя %s",
                invoice_data["invoice_id"],
                amount_str,
                asset,
                user_id,
            )

            return {
                "local_payment_id": local_payment.id,
                "invoice_id": str(invoice_data["invoice_id"]),
                "amount": amount_str,
                "asset": asset,
                "bot_invoice_url": invoice_data.get("bot_invoice_url"),
                "mini_app_invoice_url": invoice_data.get("mini_app_invoice_url"),
                "web_app_invoice_url": invoice_data.get("web_app_invoice_url"),
                "status": "active",
                "created_at": (
                    local_payment.created_at.isoformat()
                    if local_payment.created_at
                    else None
                ),
            }

        except Exception as error:
            logger.error("Ошибка создания CryptoBot платежа: %s", error)
            return None

    async def process_cryptobot_webhook(
        self,
        db: AsyncSession,
        webhook_data: Dict[str, Any],
    ) -> bool:
        """Обрабатывает webhook от CryptoBot и начисляет средства пользователю."""
        try:
            update_type = webhook_data.get("update_type")

            if update_type != "invoice_paid":
                logger.info("Пропуск CryptoBot webhook с типом: %s", update_type)
                return True

            payload = webhook_data.get("payload", {})
            invoice_id = str(payload.get("invoice_id"))
            status = "paid"

            if not invoice_id:
                logger.error("CryptoBot webhook без invoice_id")
                return False

            cryptobot_crud = import_module("app.database.crud.cryptobot")
            payment = await cryptobot_crud.get_cryptobot_payment_by_invoice_id(
                db, invoice_id
            )
            if not payment:
                logger.error("CryptoBot платеж не найден в БД: %s", invoice_id)
                return False

            if payment.status == "paid":
                logger.info("CryptoBot платеж %s уже обработан", invoice_id)
                return True

            paid_at_str = payload.get("paid_at")
            if paid_at_str:
                try:
                    paid_at = datetime.fromisoformat(
                        paid_at_str.replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                except Exception:
                    paid_at = datetime.utcnow()
            else:
                paid_at = datetime.utcnow()

            updated_payment = await cryptobot_crud.update_cryptobot_payment_status(
                db, invoice_id, status, paid_at
            )

            descriptor = decode_payment_payload(
                getattr(updated_payment, "payload", "") or "",
                expected_user_id=updated_payment.user_id,
            )
            if descriptor:
                renewal_handled = await self._process_subscription_renewal_payment(
                    db,
                    updated_payment,
                    descriptor,
                    cryptobot_crud,
                )
                if renewal_handled:
                    return True

            if not updated_payment.transaction_id:
                amount_usd = updated_payment.amount_float

                try:
                    amount_rubles = await currency_converter.usd_to_rub(amount_usd)
                    amount_rubles_rounded = math.ceil(amount_rubles)
                    amount_kopeks = int(amount_rubles_rounded * 100)
                    conversion_rate = (
                        amount_rubles / amount_usd if amount_usd > 0 else 0
                    )
                    logger.info(
                        "Конвертация USD->RUB: $%s -> %s₽ (округлено до %s₽, курс: %.2f)",
                        amount_usd,
                        amount_rubles,
                        amount_rubles_rounded,
                        conversion_rate,
                    )
                except Exception as error:
                    logger.warning(
                        "Ошибка конвертации валют для платежа %s, используем курс 1:1: %s",
                        invoice_id,
                        error,
                    )
                    amount_rubles = amount_usd
                    amount_rubles_rounded = math.ceil(amount_rubles)
                    amount_kopeks = int(amount_rubles_rounded * 100)
                    conversion_rate = 1.0

                if amount_kopeks <= 0:
                    logger.error(
                        "Некорректная сумма после конвертации: %s копеек для платежа %s",
                        amount_kopeks,
                        invoice_id,
                    )
                    return False

                payment_service_module = import_module("app.services.payment_service")
                transaction = await payment_service_module.create_transaction(
                    db,
                    user_id=updated_payment.user_id,
                    type=TransactionType.DEPOSIT,
                    amount_kopeks=amount_kopeks,
                    description=(
                        "Пополнение через CryptoBot "
                        f"({updated_payment.amount} {updated_payment.asset} → {amount_rubles_rounded:.2f}₽)"
                    ),
                    payment_method=PaymentMethod.CRYPTOBOT,
                    external_id=invoice_id,
                    is_completed=True,
                )

                await cryptobot_crud.link_cryptobot_payment_to_transaction(
                    db, invoice_id, transaction.id
                )

                get_user_by_id = payment_service_module.get_user_by_id
                user = await get_user_by_id(db, updated_payment.user_id)
                if not user:
                    logger.error(
                        "Пользователь с ID %s не найден при пополнении баланса",
                        updated_payment.user_id,
                    )
                    return False

                old_balance = user.balance_kopeks
                was_first_topup = not user.has_made_first_topup

                user.balance_kopeks += amount_kopeks
                user.updated_at = datetime.utcnow()

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
                        amount_kopeks,
                        getattr(self, "bot", None),
                    )
                except Exception as error:
                    logger.error(
                        "Ошибка обработки реферального пополнения CryptoBot: %s",
                        error,
                    )

                if was_first_topup and not user.has_made_first_topup:
                    user.has_made_first_topup = True
                    await db.commit()

                await db.refresh(user)

                admin_notification: Optional[_AdminNotificationContext] = None
                user_notification: Optional[_UserNotificationPayload] = None
                saved_cart_notification: Optional[_SavedCartNotificationPayload] = None

                bot_instance = getattr(self, "bot", None)
                if bot_instance:
                    admin_notification = _AdminNotificationContext(
                        user_id=user.id,
                        transaction_id=transaction.id,
                        old_balance=old_balance,
                        topup_status=topup_status,
                        referrer_info=referrer_info,
                    )

                    try:
                        keyboard = await self.build_topup_success_keyboard(user)
                        message_text = (
                            "✅ <b>Пополнение успешно!</b>\n\n"
                            f"💰 Сумма: {settings.format_price(amount_kopeks)}\n"
                            f"🪙 Платеж: {updated_payment.amount} {updated_payment.asset}\n"
                            f"💱 Курс: 1 USD = {conversion_rate:.2f}₽\n"
                            f"🆔 Транзакция: {invoice_id[:8]}...\n\n"
                            "Баланс пополнен автоматически!"
                        )
                        user_notification = _UserNotificationPayload(
                            telegram_id=user.telegram_id,
                            text=message_text,
                            parse_mode="HTML",
                            reply_markup=keyboard,
                            amount_rubles=amount_rubles_rounded,
                            asset=updated_payment.asset,
                        )
                    except Exception as error:
                        logger.error(
                            "Ошибка подготовки уведомления о пополнении CryptoBot: %s",
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
                                bot=bot_instance,
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

                    if has_saved_cart and bot_instance:
                        from app.localization.texts import get_texts

                        texts = get_texts(user.language)
                        cart_message = texts.BALANCE_TOPUP_CART_REMINDER_DETAILED.format(
                            total_amount=settings.format_price(payment.amount_kopeks)
                        )

                        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
                            [types.InlineKeyboardButton(
                                text=texts.RETURN_TO_SUBSCRIPTION_CHECKOUT,
                                callback_data="return_to_saved_cart"
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

                        saved_cart_notification = _SavedCartNotificationPayload(
                            telegram_id=user.telegram_id,
                            text=(
                                f"✅ Баланс пополнен на {settings.format_price(payment.amount_kopeks)}!\n\n"
                                f"⚠️ <b>Важно:</b> Пополнение баланса не активирует подписку автоматически. "
                                f"Обязательно активируйте подписку отдельно!\n\n"
                                f"🔄 При наличии сохранённой корзины подписки и включенной автопокупке, "
                                f"подписка будет приобретена автоматически после пополнения баланса.\n\n{cart_message}"
                            ),
                            reply_markup=keyboard,
                            user_id=user.id,
                        )
                except Exception as error:
                    logger.error(
                        "Ошибка при работе с сохраненной корзиной для пользователя %s: %s",
                        user.id,
                        error,
                        exc_info=True,
                    )

                if admin_notification:
                    await self._deliver_admin_topup_notification(admin_notification)

                if user_notification and bot_instance:
                    await self._deliver_user_topup_notification(user_notification)

                if saved_cart_notification and bot_instance:
                    await self._deliver_saved_cart_reminder(saved_cart_notification)

            return True

        except Exception as error:
            logger.error(
                "Ошибка обработки CryptoBot webhook: %s", error, exc_info=True
            )
            return False

    async def _process_subscription_renewal_payment(
        self,
        db: AsyncSession,
        payment: Any,
        descriptor: RenewalPaymentDescriptor,
        cryptobot_crud: Any,
    ) -> bool:
        try:
            payment_service_module = import_module("app.services.payment_service")
            user = await payment_service_module.get_user_by_id(db, payment.user_id)
        except Exception as error:
            logger.error(
                "Не удалось загрузить пользователя %s для продления через CryptoBot: %s",
                getattr(payment, "user_id", None),
                error,
            )
            return False

        if not user:
            logger.error(
                "Пользователь %s не найден при обработке продления через CryptoBot",
                getattr(payment, "user_id", None),
            )
            return False

        subscription = getattr(user, "subscription", None)
        if not subscription or subscription.id != descriptor.subscription_id:
            logger.warning(
                "Продление через CryptoBot отклонено: подписка %s не совпадает с ожидаемой %s",
                getattr(subscription, "id", None),
                descriptor.subscription_id,
            )
            return False

        pricing_model: Optional[SubscriptionRenewalPricing] = None
        if descriptor.pricing_snapshot:
            try:
                pricing_model = SubscriptionRenewalPricing.from_payload(
                    descriptor.pricing_snapshot
                )
            except Exception as error:
                logger.warning(
                    "Не удалось восстановить сохраненную стоимость продления из payload %s: %s",
                    payment.invoice_id,
                    error,
                )

        if pricing_model is None:
            try:
                pricing_model = await renewal_service.calculate_pricing(
                    db,
                    user,
                    subscription,
                    descriptor.period_days,
                )
            except Exception as error:
                logger.error(
                    "Не удалось пересчитать стоимость продления для CryptoBot %s: %s",
                    payment.invoice_id,
                    error,
                )
                return False

            if pricing_model.final_total != descriptor.total_amount_kopeks:
                logger.warning(
                    "Сумма продления через CryptoBot %s изменилась (ожидалось %s, получено %s)",
                    payment.invoice_id,
                    descriptor.total_amount_kopeks,
                    pricing_model.final_total,
                )
                pricing_model.final_total = descriptor.total_amount_kopeks
                pricing_model.per_month = (
                    descriptor.total_amount_kopeks // pricing_model.months
                    if pricing_model.months
                    else descriptor.total_amount_kopeks
                )

        pricing_model.period_days = descriptor.period_days
        pricing_model.period_id = build_renewal_period_id(descriptor.period_days)

        required_balance = max(
            0,
            min(
                pricing_model.final_total,
                descriptor.balance_component_kopeks,
            ),
        )

        current_balance = getattr(user, "balance_kopeks", 0)
        if current_balance < required_balance:
            logger.warning(
                "Недостаточно средств на балансе пользователя %s для завершения продления: нужно %s, доступно %s",
                user.id,
                required_balance,
                current_balance,
            )
            return False

        description = f"Продление подписки на {descriptor.period_days} дней"

        try:
            result = await renewal_service.finalize(
                db,
                user,
                subscription,
                pricing_model,
                charge_balance_amount=required_balance,
                description=description,
                payment_method=PaymentMethod.CRYPTOBOT,
            )
        except SubscriptionRenewalChargeError as error:
            logger.error(
                "Списание баланса не выполнено при продлении через CryptoBot %s: %s",
                payment.invoice_id,
                error,
            )
            return False
        except Exception as error:
            logger.error(
                "Ошибка завершения продления через CryptoBot %s: %s",
                payment.invoice_id,
                error,
                exc_info=True,
            )
            return False

        transaction = result.transaction
        if transaction:
            try:
                await cryptobot_crud.link_cryptobot_payment_to_transaction(
                    db,
                    payment.invoice_id,
                    transaction.id,
                )
            except Exception as error:
                logger.warning(
                    "Не удалось связать платеж CryptoBot %s с транзакцией %s: %s",
                    payment.invoice_id,
                    transaction.id,
                    error,
                )

        external_amount_label = settings.format_price(descriptor.missing_amount_kopeks)
        balance_amount_label = settings.format_price(required_balance)

        logger.info(
            "Подписка %s продлена через CryptoBot invoice %s (внешний платеж %s, списано с баланса %s)",
            subscription.id,
            payment.invoice_id,
            external_amount_label,
            balance_amount_label,
        )

        return True

    async def _deliver_admin_topup_notification(
        self, context: _AdminNotificationContext
    ) -> None:
        bot_instance = getattr(self, "bot", None)
        if not bot_instance:
            return

        try:
            from app.services.admin_notification_service import AdminNotificationService
            from app.database.crud.user import get_user_by_id
            from app.database.crud.transaction import get_transaction_by_id
        except Exception as error:
            logger.error(
                "Не удалось импортировать зависимости для админ-уведомления CryptoBot: %s",
                error,
                exc_info=True,
            )
            return

        async with AsyncSessionLocal() as session:
            try:
                user = await get_user_by_id(session, context.user_id)
                transaction = await get_transaction_by_id(session, context.transaction_id)
            except Exception as error:
                logger.error(
                    "Ошибка загрузки данных для админ-уведомления CryptoBot: %s",
                    error,
                    exc_info=True,
                )
                await session.rollback()
                return

            if not user or not transaction:
                logger.warning(
                    "Пропущена отправка админ-уведомления CryptoBot: user=%s transaction=%s",
                    bool(user),
                    bool(transaction),
                )
                return

            notification_service = AdminNotificationService(bot_instance)
            try:
                await notification_service.send_balance_topup_notification(
                    user,
                    transaction,
                    context.old_balance,
                    topup_status=context.topup_status,
                    referrer_info=context.referrer_info,
                    subscription=getattr(user, "subscription", None),
                    promo_group=getattr(user, "promo_group", None),
                    db=session,
                )
            except Exception as error:
                logger.error(
                    "Ошибка отправки админ-уведомления о пополнении CryptoBot: %s",
                    error,
                    exc_info=True,
                )

    async def _deliver_user_topup_notification(
        self, payload: _UserNotificationPayload
    ) -> None:
        bot_instance = getattr(self, "bot", None)
        if not bot_instance:
            return

        try:
            await bot_instance.send_message(
                payload.telegram_id,
                payload.text,
                parse_mode=payload.parse_mode,
                reply_markup=payload.reply_markup,
            )
            logger.info(
                "✅ Отправлено уведомление пользователю %s о пополнении на %s₽ (%s)",
                payload.telegram_id,
                f"{payload.amount_rubles:.2f}",
                payload.asset,
            )
        except Exception as error:
            logger.error(
                "Ошибка отправки уведомления о пополнении CryptoBot: %s",
                error,
            )

    async def _deliver_saved_cart_reminder(
        self, payload: _SavedCartNotificationPayload
    ) -> None:
        bot_instance = getattr(self, "bot", None)
        if not bot_instance:
            return

        try:
            await bot_instance.send_message(
                chat_id=payload.telegram_id,
                text=payload.text,
                reply_markup=payload.reply_markup,
            )
            logger.info(
                "Отправлено уведомление с кнопкой возврата к оформлению подписки пользователю %s",
                payload.user_id,
            )
        except Exception as error:
            logger.error(
                "Ошибка отправки уведомления о сохраненной корзине для пользователя %s: %s",
                payload.user_id,
                error,
                exc_info=True,
            )

    async def get_cryptobot_payment_status(
        self,
        db: AsyncSession,
        local_payment_id: int,
    ) -> Optional[Dict[str, Any]]:
        """Запрашивает актуальный статус CryptoBot invoice и синхронизирует его."""

        cryptobot_crud = import_module("app.database.crud.cryptobot")
        payment = await cryptobot_crud.get_cryptobot_payment_by_id(db, local_payment_id)
        if not payment:
            logger.warning("CryptoBot платеж %s не найден", local_payment_id)
            return None

        if not self.cryptobot_service:
            logger.warning("CryptoBot сервис не инициализирован для ручной проверки")
            return {"payment": payment}

        invoice_id = payment.invoice_id
        try:
            invoices = await self.cryptobot_service.get_invoices(
                invoice_ids=[invoice_id]
            )
        except Exception as error:  # pragma: no cover - network errors
            logger.error(
                "Ошибка запроса статуса CryptoBot invoice %s: %s",
                invoice_id,
                error,
            )
            return {"payment": payment}

        remote_invoice: Optional[Dict[str, Any]] = None
        if invoices:
            for item in invoices:
                if str(item.get("invoice_id")) == str(invoice_id):
                    remote_invoice = item
                    break

        if not remote_invoice:
            logger.info(
                "CryptoBot invoice %s не найден через API при ручной проверке",
                invoice_id,
            )
            refreshed = await cryptobot_crud.get_cryptobot_payment_by_id(db, local_payment_id)
            return {"payment": refreshed or payment}

        status = (remote_invoice.get("status") or "").lower()
        paid_at_str = remote_invoice.get("paid_at")
        paid_at = None
        if paid_at_str:
            try:
                paid_at = datetime.fromisoformat(paid_at_str.replace("Z", "+00:00")).replace(
                    tzinfo=None
                )
            except Exception:  # pragma: no cover - defensive parsing
                paid_at = None

        if status == "paid":
            webhook_payload = {
                "update_type": "invoice_paid",
                "payload": {
                    "invoice_id": remote_invoice.get("invoice_id") or invoice_id,
                    "amount": remote_invoice.get("amount") or payment.amount,
                    "asset": remote_invoice.get("asset") or payment.asset,
                    "paid_at": paid_at_str,
                    "payload": remote_invoice.get("payload") or payment.payload,
                },
            }
            await self.process_cryptobot_webhook(db, webhook_payload)
        else:
            if status and status != (payment.status or "").lower():
                await cryptobot_crud.update_cryptobot_payment_status(
                    db,
                    invoice_id,
                    status,
                    paid_at,
                )

        refreshed = await cryptobot_crud.get_cryptobot_payment_by_id(db, local_payment_id)
        return {"payment": refreshed or payment}
