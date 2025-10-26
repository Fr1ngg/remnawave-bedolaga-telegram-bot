"""Mixin с логикой обработки платежей CryptoBot."""

from __future__ import annotations

import logging
from datetime import datetime
from importlib import import_module
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import PaymentMethod, TransactionType
from app.services.subscription_auto_purchase_service import (
    auto_purchase_saved_cart_after_topup,
    get_saved_cart_total_kopeks,
)
from app.utils.currency_converter import currency_converter
from app.utils.user_utils import format_referrer_info

logger = logging.getLogger(__name__)


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

            if not updated_payment.transaction_id:
                amount_usd = updated_payment.amount_float

                try:
                    amount_rubles = await currency_converter.usd_to_rub(amount_usd)
                    amount_kopeks = int(amount_rubles * 100)
                    conversion_rate = (
                        amount_rubles / amount_usd if amount_usd > 0 else 0
                    )
                    logger.info(
                        "Конвертация USD->RUB: $%s -> %s₽ (курс: %.2f)",
                        amount_usd,
                        amount_rubles,
                        conversion_rate,
                    )
                except Exception as error:
                    logger.warning(
                        "Ошибка конвертации валют для платежа %s, используем курс 1:1: %s",
                        invoice_id,
                        error,
                    )
                    amount_rubles = amount_usd
                    amount_kopeks = int(amount_usd * 100)
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
                        f"({updated_payment.amount} {updated_payment.asset} → {amount_rubles:.2f}₽)"
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
                            "Ошибка отправки уведомления о пополнении CryptoBot: %s",
                            error,
                        )

                if getattr(self, "bot", None):
                    try:
                        keyboard = await self.build_topup_success_keyboard(user)

                        await self.bot.send_message(
                            user.telegram_id,
                            (
                                "✅ <b>Пополнение успешно!</b>\n\n"
                                f"💰 Сумма: {settings.format_price(amount_kopeks)}\n"
                                f"🪙 Платеж: {updated_payment.amount} {updated_payment.asset}\n"
                                f"💱 Курс: 1 USD = {conversion_rate:.2f}₽\n"
                                f"🆔 Транзакция: {invoice_id[:8]}...\n\n"
                                "Баланс пополнен автоматически!"
                            ),
                            parse_mode="HTML",
                            reply_markup=keyboard,
                        )
                        logger.info(
                            "✅ Отправлено уведомление пользователю %s о пополнении на %s₽ (%s)",
                            user.telegram_id,
                            f"{amount_rubles:.2f}",
                            updated_payment.asset,
                        )
                    except Exception as error:
                        logger.error(
                            "Ошибка отправки уведомления о пополнении CryptoBot: %s",
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
                            else settings.format_price(payment.amount_kopeks)
                        )

                        cart_message = texts.BALANCE_TOPUP_CART_REMINDER_DETAILED.format(
                            total_amount=total_amount_label
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
                        logger.info(
                            "Отправлено уведомление с кнопкой возврата к оформлению подписки пользователю %s",
                            user.id,
                        )
                except Exception as e:
                    logger.error(f"Ошибка при работе с сохраненной корзиной для пользователя {user.id}: {e}", exc_info=True)

            return True

        except Exception as error:
            logger.error(
                "Ошибка обработки CryptoBot webhook: %s", error, exc_info=True
            )
            return False

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
