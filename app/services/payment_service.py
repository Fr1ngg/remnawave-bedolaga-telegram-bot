import logging
import hashlib
import hmac
import uuid
from decimal import Decimal, InvalidOperation
from typing import Optional, Dict, Any
from datetime import datetime
from aiogram import Bot
from aiogram.types import LabeledPrice, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.yookassa_service import YooKassaService
from app.external.telegram_stars import TelegramStarsService
from app.database.crud.yookassa import create_yookassa_payment, link_yookassa_payment_to_transaction
from app.database.crud.transaction import create_transaction
from app.database.crud.user import (
    add_user_balance,
    get_user_by_id,
    get_user_by_telegram_id,
)
from app.database.models import TransactionType, PaymentMethod
from app.external.cryptobot import CryptoBotService
from app.utils.currency_converter import currency_converter
from app.database.database import get_db
from app.localization.texts import get_texts
from app.services.subscription_checkout_service import (
    has_subscription_checkout_draft,
    should_offer_checkout_resume,
)
from app.services.mulenpay_service import MulenPayService
from app.services.pal24_service import Pal24Service, Pal24APIError
from app.database.crud.mulenpay import (
    create_mulenpay_payment,
    get_mulenpay_payment_by_local_id,
    get_mulenpay_payment_by_uuid,
    get_mulenpay_payment_by_mulen_id,
    update_mulenpay_payment_status,
    link_mulenpay_payment_to_transaction,
)
from app.database.crud.pal24 import (
    create_pal24_payment,
    get_pal24_payment_by_bill_id,
    get_pal24_payment_by_id,
    get_pal24_payment_by_order_id,
    link_pal24_payment_to_transaction,
    update_pal24_payment_status,
)

logger = logging.getLogger(__name__)


class PaymentService:
    
    def __init__(self, bot: Optional[Bot] = None):
        self.bot = bot
        self.yookassa_service = YooKassaService() if settings.is_yookassa_enabled() else None
        self.stars_service = TelegramStarsService(bot) if bot else None
        self.cryptobot_service = CryptoBotService() if settings.is_cryptobot_enabled() else None
        self.mulenpay_service = MulenPayService() if settings.is_mulenpay_enabled() else None
        self.pal24_service = Pal24Service() if settings.is_pal24_enabled() else None

    async def build_topup_success_keyboard(self, user) -> InlineKeyboardMarkup:
        texts = get_texts(user.language if user else "ru")

        has_active_subscription = (
            user
            and user.subscription
            and not user.subscription.is_trial
            and user.subscription.is_active
        )

        first_button = InlineKeyboardButton(
            text=(
                texts.MENU_EXTEND_SUBSCRIPTION
                if has_active_subscription
                else texts.MENU_BUY_SUBSCRIPTION
            ),
            callback_data=(
                "subscription_extend" if has_active_subscription else "menu_buy"
            ),
        )

        keyboard_rows: list[list[InlineKeyboardButton]] = [[first_button]]

        if user:
            draft_exists = await has_subscription_checkout_draft(user.id)
            if should_offer_checkout_resume(user, draft_exists):
                keyboard_rows.append([
                    InlineKeyboardButton(
                        text=texts.RETURN_TO_SUBSCRIPTION_CHECKOUT,
                        callback_data="subscription_resume_checkout",
                    )
                ])

        keyboard_rows.append([
            InlineKeyboardButton(text="💰 Мой баланс", callback_data="menu_balance")
        ])
        keyboard_rows.append([
            InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_menu")
        ])

        return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

    async def create_stars_invoice(
        self,
        amount_kopeks: int,
        description: str,
        payload: Optional[str] = None
    ) -> str:
        
        if not self.bot or not self.stars_service:
            raise ValueError("Bot instance required for Stars payments")
        
        try:
            amount_rubles = amount_kopeks / 100
            stars_amount = TelegramStarsService.calculate_stars_from_rubles(amount_rubles)
            
            invoice_link = await self.bot.create_invoice_link(
                title="Пополнение баланса VPN",
                description=f"{description} (≈{stars_amount} ⭐)",
                payload=payload or f"balance_topup_{amount_kopeks}",
                provider_token="", 
                currency="XTR", 
                prices=[LabeledPrice(label="Пополнение", amount=stars_amount)]
            )
            
            logger.info(f"Создан Stars invoice на {stars_amount} звезд (~{int(amount_rubles)}₽)")
            return invoice_link
            
        except Exception as e:
            logger.error(f"Ошибка создания Stars invoice: {e}")
            raise
    
    async def process_stars_payment(
        self,
        db: AsyncSession,
        user_id: int,
        stars_amount: int,
        payload: str,
        telegram_payment_charge_id: str
    ) -> bool:
        try:
            rubles_amount = TelegramStarsService.calculate_rubles_from_stars(stars_amount)
            amount_kopeks = int(rubles_amount * 100)
            
            transaction = await create_transaction(
                db=db,
                user_id=user_id,
                type=TransactionType.DEPOSIT,
                amount_kopeks=amount_kopeks,
                description=f"Пополнение через Telegram Stars ({stars_amount} ⭐)",
                payment_method=PaymentMethod.TELEGRAM_STARS,
                external_id=telegram_payment_charge_id,
                is_completed=True
            )
            
            user = await get_user_by_id(db, user_id)
            if user:
                old_balance = user.balance_kopeks
                
                user.balance_kopeks += amount_kopeks
                user.updated_at = datetime.utcnow()
                
                await db.commit()
                await db.refresh(user)
                
                logger.info(f"💰 Баланс пользователя {user.telegram_id} изменен: {old_balance} → {user.balance_kopeks} (изменение: +{amount_kopeks})")
                
                description_for_referral = f"Пополнение Stars: {int(rubles_amount)}₽ ({stars_amount} ⭐)"
                logger.info(f"🔍 Проверка реферальной логики для описания: '{description_for_referral}'")
                
                if any(word in description_for_referral.lower() for word in ["пополнение", "stars", "yookassa", "topup"]) and not any(word in description_for_referral.lower() for word in ["комиссия", "бонус"]):
                    logger.info(f"🔞 Вызов process_referral_topup для пользователя {user_id}")
                    try:
                        from app.services.referral_service import process_referral_topup
                        await process_referral_topup(db, user_id, amount_kopeks, self.bot)
                    except Exception as e:
                        logger.error(f"Ошибка обработки реферального пополнения: {e}")
                else:
                    logger.info(f"❌ Описание '{description_for_referral}' не подходит для реферальной логики")
                
                if self.bot:
                    try:
                        from app.services.admin_notification_service import AdminNotificationService
                        notification_service = AdminNotificationService(self.bot)
                        await notification_service.send_balance_topup_notification(
                            db, user, transaction, old_balance
                        )
                    except Exception as e:
                        logger.error(f"Ошибка отправки уведомления о пополнении Stars: {e}")
                
                if self.bot:
                    try:
                        keyboard = await self.build_topup_success_keyboard(user)

                        await self.bot.send_message(
                            user.telegram_id,
                            f"✅ <b>Пополнение успешно!</b>\n\n"
                            f"⭐ Звезд: {stars_amount}\n"
                            f"💰 Сумма: {settings.format_price(amount_kopeks)}\n"
                            f"🦊 Способ: Telegram Stars\n"
                            f"🆔 Транзакция: {telegram_payment_charge_id[:8]}...\n\n"
                            f"Баланс пополнен автоматически!",
                            parse_mode="HTML",
                            reply_markup=keyboard,
                        )
                        logger.info(
                            f"✅ Отправлено уведомление пользователю {user.telegram_id} о пополнении на {int(rubles_amount)}₽"
                        )
                    except Exception as e:
                        logger.error(f"Ошибка отправки уведомления о пополнении Stars: {e}")
                
                logger.info(
                    f"✅ Обработан Stars платеж: пользователь {user_id}, "
                    f"{stars_amount} звезд → {int(rubles_amount)}₽"
                )
                return True
            else:
                logger.error(f"Пользователь с ID {user_id} не найден при обработке Stars платежа")
                return False
                
        except Exception as e:
            logger.error(f"Ошибка обработки Stars платежа: {e}", exc_info=True)
            return False
    
    async def create_yookassa_payment(
        self,
        db: AsyncSession,
        user_id: int,
        amount_kopeks: int,
        description: str,
        receipt_email: Optional[str] = None,
        receipt_phone: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        
        if not self.yookassa_service:
            logger.error("YooKassa сервис не инициализирован")
            return None
        
        try:
            amount_rubles = amount_kopeks / 100
            
            payment_metadata = metadata or {}
            payment_metadata.update({
                "user_id": str(user_id),
                "amount_kopeks": str(amount_kopeks),
                "type": "balance_topup"
            })
            
            yookassa_response = await self.yookassa_service.create_payment(
                amount=amount_rubles,
                currency="RUB",
                description=description,
                metadata=payment_metadata,
                receipt_email=receipt_email,
                receipt_phone=receipt_phone
            )
            
            if not yookassa_response or yookassa_response.get("error"):
                logger.error(f"Ошибка создания платежа YooKassa: {yookassa_response}")
                return None
            
            yookassa_created_at = None
            if yookassa_response.get("created_at"):
                try:
                    dt_with_tz = datetime.fromisoformat(
                        yookassa_response["created_at"].replace('Z', '+00:00')
                    )
                    yookassa_created_at = dt_with_tz.replace(tzinfo=None)
                except Exception as e:
                    logger.warning(f"Не удалось парсить created_at: {e}")
                    yookassa_created_at = None
            
            local_payment = await create_yookassa_payment(
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
                test_mode=yookassa_response.get("test_mode", False)
            )
            
            logger.info(f"Создан платеж YooKassa {yookassa_response['id']} на {amount_rubles}₽ для пользователя {user_id}")
            
            return {
                "local_payment_id": local_payment.id,
                "yookassa_payment_id": yookassa_response["id"],
                "confirmation_url": yookassa_response.get("confirmation_url"),
                "amount_kopeks": amount_kopeks,
                "amount_rubles": amount_rubles,
                "status": yookassa_response["status"],
                "created_at": local_payment.created_at
            }
            
        except Exception as e:
            logger.error(f"Ошибка создания платежа YooKassa: {e}")
            return None

    async def create_yookassa_sbp_payment(
        self,
        db: AsyncSession,
        user_id: int,
        amount_kopeks: int,
        description: str,
        receipt_email: Optional[str] = None,
        receipt_phone: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        
        if not self.yookassa_service:
            logger.error("YooKassa сервис не инициализирован")
            return None
        
        try:
            amount_rubles = amount_kopeks / 100
            
            payment_metadata = metadata or {}
            payment_metadata.update({
                "user_id": str(user_id),
                "amount_kopeks": str(amount_kopeks),
                "type": "balance_topup_sbp" 
            })
            
            yookassa_response = await self.yookassa_service.create_sbp_payment(
                amount=amount_rubles,
                currency="RUB",
                description=description,
                metadata=payment_metadata,
                receipt_email=receipt_email,
                receipt_phone=receipt_phone
            )
            
            if not yookassa_response or yookassa_response.get("error"):
                logger.error(f"Ошибка создания платежа YooKassa СБП: {yookassa_response}")
                return None
            
            yookassa_created_at = None
            if yookassa_response.get("created_at"):
                try:
                    dt_with_tz = datetime.fromisoformat(
                        yookassa_response["created_at"].replace('Z', '+00:00')
                    )
                    yookassa_created_at = dt_with_tz.replace(tzinfo=None)
                except Exception as e:
                    logger.warning(f"Не удалось парсить created_at: {e}")
                    yookassa_created_at = None
            
            confirmation_token = None
            if yookassa_response.get("confirmation"):
                confirmation_token = yookassa_response["confirmation"].get("confirmation_token")
            
            if confirmation_token:
                payment_metadata["confirmation_token"] = confirmation_token
            
            local_payment = await create_yookassa_payment(
                db=db,
                user_id=user_id,
                yookassa_payment_id=yookassa_response["id"],
                amount_kopeks=amount_kopeks,
                currency="RUB",
                description=description,
                status=yookassa_response["status"],
                confirmation_url=yookassa_response.get("confirmation_url"),
                metadata_json=payment_metadata,
                payment_method_type="bank_card",  
                yookassa_created_at=yookassa_created_at, 
                test_mode=yookassa_response.get("test_mode", False)
            )
            
            logger.info(f"Создан платеж YooKassa СБП {yookassa_response['id']} на {amount_rubles}₽ для пользователя {user_id}")
            
            return {
                "local_payment_id": local_payment.id,
                "yookassa_payment_id": yookassa_response["id"],
                "confirmation_url": yookassa_response.get("confirmation_url"),
                "confirmation_token": confirmation_token,
                "amount_kopeks": amount_kopeks,
                "amount_rubles": amount_rubles,
                "status": yookassa_response["status"],
                "created_at": local_payment.created_at
            }
            
        except Exception as e:
            logger.error(f"Ошибка создания платежа YooKassa СБП: {e}")
            return None
    
    async def process_yookassa_webhook(self, db: AsyncSession, webhook_data: dict) -> bool:
        try:
            from app.database.crud.yookassa import (
                get_yookassa_payment_by_id, 
                update_yookassa_payment_status,
                link_yookassa_payment_to_transaction
            )
            from app.database.crud.transaction import create_transaction
            from app.database.models import TransactionType, PaymentMethod
            
            payment_object = webhook_data.get("object", {})
            yookassa_payment_id = payment_object.get("id")
            status = payment_object.get("status")
            paid = payment_object.get("paid", False)
            
            if not yookassa_payment_id:
                logger.error("Webhook без ID платежа")
                return False
            
            payment = await get_yookassa_payment_by_id(db, yookassa_payment_id)
            if not payment:
                logger.error(f"Платеж не найден в БД: {yookassa_payment_id}")
                return False
            
            captured_at = None
            if status == "succeeded":
                captured_at = datetime.utcnow() 
            
            updated_payment = await update_yookassa_payment_status(
                db, 
                yookassa_payment_id, 
                status, 
                is_paid=paid,
                is_captured=(status == "succeeded"),
                captured_at=captured_at,
                payment_method_type=payment_object.get("payment_method", {}).get("type")
            )
            
            if status == "succeeded" and paid and not updated_payment.transaction_id:
                transaction = await create_transaction(
                    db,
                    user_id=updated_payment.user_id,
                    type=TransactionType.DEPOSIT, 
                    amount_kopeks=updated_payment.amount_kopeks,
                    description=f"Пополнение через YooKassa ({yookassa_payment_id[:8]}...)",
                    payment_method=PaymentMethod.YOOKASSA,
                    external_id=yookassa_payment_id,
                    is_completed=True
                )
                
                await link_yookassa_payment_to_transaction(
                    db, yookassa_payment_id, transaction.id
                )
                
                user = await get_user_by_id(db, updated_payment.user_id)
                if user:
                    old_balance = user.balance_kopeks
                    
                    user.balance_kopeks += updated_payment.amount_kopeks
                    user.updated_at = datetime.utcnow()
                    
                    await db.commit()
                    await db.refresh(user)
                    
                    try:
                        from app.services.referral_service import process_referral_topup
                        await process_referral_topup(db, user.id, updated_payment.amount_kopeks, self.bot)
                    except Exception as e:
                        logger.error(f"Ошибка обработки реферального пополнения YooKassa: {e}")
                    
                    if self.bot:
                        try:
                            from app.services.admin_notification_service import AdminNotificationService
                            notification_service = AdminNotificationService(self.bot)
                            await notification_service.send_balance_topup_notification(
                                db, user, transaction, old_balance
                            )
                        except Exception as e:
                            logger.error(f"Ошибка отправки уведомления о пополнении YooKassa: {e}")
                    
                    if self.bot:
                        try:
                            keyboard = await self.build_topup_success_keyboard(user)

                            await self.bot.send_message(
                                user.telegram_id,
                                f"✅ <b>Пополнение успешно!</b>\n\n"
                                f"💰 Сумма: {settings.format_price(updated_payment.amount_kopeks)}\n"
                                f"🦊 Способ: Банковская карта\n"
                                f"🆔 Транзакция: {yookassa_payment_id[:8]}...\n\n"
                                f"Баланс пополнен автоматически!",
                                parse_mode="HTML",
                                reply_markup=keyboard,
                            )
                            logger.info(
                                f"✅ Отправлено уведомление пользователю {user.telegram_id} о пополнении на {updated_payment.amount_kopeks//100}₽"
                            )
                        except Exception as e:
                            logger.error(f"Ошибка отправки уведомления о пополнении: {e}")
                else:
                    logger.error(f"Пользователь с ID {updated_payment.user_id} не найден при пополнении баланса")
                    return False
            
            return True
            
        except Exception as e:
            logger.error(f"Ошибка обработки YooKassa webhook: {e}", exc_info=True)
            return False
    
    async def _process_successful_yookassa_payment(
        self,
        db: AsyncSession,
        payment: "YooKassaPayment"
    ) -> bool:
        
        try:
            transaction = await create_transaction(
                db=db,
                user_id=payment.user_id,
                transaction_type=TransactionType.DEPOSIT,
                amount_kopeks=payment.amount_kopeks,
                description=f"Пополнение через YooKassa: {payment.description}",
                payment_method=PaymentMethod.YOOKASSA,
                external_id=payment.yookassa_payment_id,
                is_completed=True
            )
            
            await link_yookassa_payment_to_transaction(
                db=db,
                yookassa_payment_id=payment.yookassa_payment_id,
                transaction_id=transaction.id
            )
            
            user = await get_user_by_id(db, payment.user_id)
            if user:
                await add_user_balance(db, user, payment.amount_kopeks, f"Пополнение YooKassa: {payment.amount_kopeks//100}₽")
            
            logger.info(f"Успешно обработан платеж YooKassa {payment.yookassa_payment_id}: "
                       f"пользователь {payment.user_id} получил {payment.amount_kopeks/100}₽")
            
            if self.bot and user:
                try:
                    await self._send_payment_success_notification(
                        user.telegram_id, 
                        payment.amount_kopeks
                    )
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления о платеже: {e}")
            
            return True
            
        except Exception as e:
            logger.error(f"Ошибка обработки успешного платежа YooKassa {payment.yookassa_payment_id}: {e}")
            return False
    
    async def _send_payment_success_notification(
        self,
        telegram_id: int,
        amount_kopeks: int
    ) -> None:

        if not self.bot:
            return

        try:
            async for db in get_db():
                user = await get_user_by_telegram_id(db, telegram_id)
                break

            keyboard = await self.build_topup_success_keyboard(user)

            message = (
                f"✅ <b>Платеж успешно завершен!</b>\n\n"
                f"💰 Сумма: {settings.format_price(amount_kopeks)}\n"
                f"💳 Способ: Банковская карта (YooKassa)\n\n"
                f"Средства зачислены на ваш баланс!"
            )

            await self.bot.send_message(
                chat_id=telegram_id,
                text=message,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления пользователю {telegram_id}: {e}")
    
    async def create_tribute_payment(
        self,
        amount_kopeks: int,
        user_id: int,
        description: str
    ) -> str:
        
        if not settings.TRIBUTE_ENABLED:
            raise ValueError("Tribute payments are disabled")
        
        try:
            payment_data = {
                "amount": amount_kopeks,
                "currency": "RUB",
                "description": description,
                "user_id": user_id,
                "callback_url": f"{settings.WEBHOOK_URL}/tribute/callback"
            }
            
            payment_url = f"https://tribute.ru/pay?amount={amount_kopeks}&user={user_id}"
            
            logger.info(f"Создан Tribute платеж на {amount_kopeks/100}₽ для пользователя {user_id}")
            return payment_url
            
        except Exception as e:
            logger.error(f"Ошибка создания Tribute платежа: {e}")
            raise
    
    def verify_tribute_webhook(
        self,
        data: dict,
        signature: str
    ) -> bool:
        
        if not settings.TRIBUTE_API_KEY:
            return False

        try:
            message = str(data).encode()
            expected_signature = hmac.new(
                settings.TRIBUTE_API_KEY.encode(),
                message,
                hashlib.sha256
            ).hexdigest()

            return hmac.compare_digest(signature, expected_signature)
            
        except Exception as e:
            logger.error(f"Ошибка проверки Tribute webhook: {e}")
            return False
    
    async def process_successful_payment(
        self,
        payment_id: str,
        amount_kopeks: int,
        user_id: int,
        payment_method: str
    ) -> bool:
        
        try:
            logger.info(f"Обработан успешный платеж: {payment_id}, {amount_kopeks/100}₽, {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка обработки платежа: {e}")
            return False

    async def create_cryptobot_payment(
        self,
        db: AsyncSession,
        user_id: int,
        amount_usd: float,
        asset: str = "USDT",
        description: str = "Пополнение баланса",
        payload: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:

        if not self.cryptobot_service:
            logger.error("CryptoBot сервис не инициализирован")
            return None
        
        try:
            amount_str = f"{amount_usd:.2f}"
            
            invoice_data = await self.cryptobot_service.create_invoice(
                amount=amount_str,
                asset=asset,
                description=description,
                payload=payload or f"balance_topup_{user_id}_{int(amount_usd * 100)}",
                expires_in=settings.get_cryptobot_invoice_expires_seconds()
            )
            
            if not invoice_data:
                logger.error("Ошибка создания CryptoBot invoice")
                return None
            
            from app.database.crud.cryptobot import create_cryptobot_payment
            
            local_payment = await create_cryptobot_payment(
                db=db,
                user_id=user_id,
                invoice_id=str(invoice_data['invoice_id']),
                amount=amount_str,
                asset=asset,
                status="active",
                description=description,
                payload=payload,
                bot_invoice_url=invoice_data.get('bot_invoice_url'),
                mini_app_invoice_url=invoice_data.get('mini_app_invoice_url'),
                web_app_invoice_url=invoice_data.get('web_app_invoice_url')
            )
            
            logger.info(f"Создан CryptoBot платеж {invoice_data['invoice_id']} на {amount_str} {asset} для пользователя {user_id}")
            
            return {
                "local_payment_id": local_payment.id,
                "invoice_id": str(invoice_data['invoice_id']),
                "amount": amount_str,
                "asset": asset,
                "bot_invoice_url": invoice_data.get('bot_invoice_url'),
                "mini_app_invoice_url": invoice_data.get('mini_app_invoice_url'),
                "web_app_invoice_url": invoice_data.get('web_app_invoice_url'),
                "status": "active",
                "created_at": local_payment.created_at.isoformat() if local_payment.created_at else None
            }
            
        except Exception as e:
            logger.error(f"Ошибка создания CryptoBot платежа: {e}")
            return None

    async def create_mulenpay_payment(
        self,
        db: AsyncSession,
        user_id: int,
        amount_kopeks: int,
        description: str,
        language: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:

        if not self.mulenpay_service:
            logger.error("MulenPay сервис не инициализирован")
            return None

        if amount_kopeks < settings.MULENPAY_MIN_AMOUNT_KOPEKS:
            logger.warning(
                "Сумма MulenPay меньше минимальной: %s < %s",
                amount_kopeks,
                settings.MULENPAY_MIN_AMOUNT_KOPEKS,
            )
            return None

        if amount_kopeks > settings.MULENPAY_MAX_AMOUNT_KOPEKS:
            logger.warning(
                "Сумма MulenPay больше максимальной: %s > %s",
                amount_kopeks,
                settings.MULENPAY_MAX_AMOUNT_KOPEKS,
            )
            return None

        try:
            payment_uuid = f"mulen_{user_id}_{uuid.uuid4().hex}"
            amount_rubles = amount_kopeks / 100

            items = [
                {
                    "description": description[:128],
                    "quantity": 1,
                    "price": round(amount_rubles, 2),
                    "vat_code": settings.MULENPAY_VAT_CODE,
                    "payment_subject": settings.MULENPAY_PAYMENT_SUBJECT,
                    "payment_mode": settings.MULENPAY_PAYMENT_MODE,
                }
            ]

            response = await self.mulenpay_service.create_payment(
                amount_kopeks=amount_kopeks,
                description=description,
                uuid=payment_uuid,
                items=items,
                language=language or settings.MULENPAY_LANGUAGE,
                website_url=settings.WEBHOOK_URL,
            )

            if not response:
                logger.error("Ошибка создания MulenPay платежа")
                return None

            mulen_payment_id = response.get("id")
            payment_url = response.get("paymentUrl")

            metadata = {
                "user_id": user_id,
                "amount_kopeks": amount_kopeks,
                "description": description,
            }

            local_payment = await create_mulenpay_payment(
                db=db,
                user_id=user_id,
                amount_kopeks=amount_kopeks,
                uuid=payment_uuid,
                description=description,
                payment_url=payment_url,
                mulen_payment_id=mulen_payment_id,
                currency="RUB",
                status="created",
                metadata=metadata,
            )

            logger.info(
                "Создан MulenPay платеж %s на %s₽ для пользователя %s",
                mulen_payment_id,
                amount_rubles,
                user_id,
            )

            return {
                "local_payment_id": local_payment.id,
                "mulen_payment_id": mulen_payment_id,
                "payment_url": payment_url,
                "amount_kopeks": amount_kopeks,
                "uuid": payment_uuid,
                "status": "created",
            }

        except Exception as e:
            logger.error(f"Ошибка создания MulenPay платежа: {e}")
            return None

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
    ) -> Optional[Dict[str, Any]]:

        if not self.pal24_service or not self.pal24_service.is_configured:
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

        try:
            response = await self.pal24_service.create_bill(
                amount_kopeks=amount_kopeks,
                user_id=user_id,
                order_id=order_id,
                description=description,
                ttl_seconds=ttl_seconds,
                custom_payload=custom_payload,
                payer_email=payer_email,
                payment_method="SBP",
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

        payment = await create_pal24_payment(
            db,
            user_id=user_id,
            bill_id=bill_id,
            order_id=order_id,
            amount_kopeks=amount_kopeks,
            description=description,
            status=response.get("status", "NEW"),
            type_=response.get("type", "normal"),
            currency=response.get("currency", "RUB"),
            link_url=primary_link,
            link_page_url=secondary_link,
            ttl=ttl_seconds,
            metadata={
                "raw_response": response,
                "language": language,
                **({"links": metadata_links} if metadata_links else {}),
            },
        )

        payment_info = {
            "bill_id": bill_id,
            "order_id": order_id,
            "link_url": primary_link,
            "link_page_url": secondary_link,
            "local_payment_id": payment.id,
            "amount_kopeks": amount_kopeks,
            "sbp_url": transfer_url or primary_link,
            "card_url": card_url,
            "transfer_url": transfer_url,
        }

        logger.info(
            "Создан Pal24 счет %s для пользователя %s на сумму %s",
            bill_id,
            user_id,
            settings.format_price(amount_kopeks),
        )

        return payment_info

    async def process_mulenpay_callback(self, db: AsyncSession, callback_data: dict) -> bool:
        try:
            uuid_value = callback_data.get("uuid")
            payment_status = (callback_data.get("payment_status") or "").lower()
            mulen_payment_id_raw = callback_data.get("id")
            mulen_payment_id_int: Optional[int] = None
            if mulen_payment_id_raw is not None:
                try:
                    mulen_payment_id_int = int(mulen_payment_id_raw)
                except (TypeError, ValueError):
                    mulen_payment_id_int = None
            amount_value = callback_data.get("amount")

            if not uuid_value and mulen_payment_id_raw is None:
                logger.error("MulenPay callback без uuid и id")
                return False

            payment = None
            if uuid_value:
                payment = await get_mulenpay_payment_by_uuid(db, uuid_value)

            if not payment and mulen_payment_id_int is not None:
                payment = await get_mulenpay_payment_by_mulen_id(db, mulen_payment_id_int)

            if not payment:
                logger.error(
                    "MulenPay платеж не найден (uuid=%s, id=%s)",
                    uuid_value,
                    mulen_payment_id_raw,
                )
                return False

            if payment.transaction_id and payment.is_paid:
                logger.info("MulenPay платеж %s уже обработан", payment.uuid)
                return True

            paid_at = datetime.utcnow()

            if payment_status == "success":
                try:
                    amount_kopeks = int(Decimal(str(amount_value)) * 100)
                except (InvalidOperation, TypeError):
                    amount_kopeks = payment.amount_kopeks
                    logger.warning(
                        "Не удалось распарсить сумму MulenPay, используем значение из БД: %s",
                        amount_value,
                    )

                if amount_kopeks != payment.amount_kopeks:
                    logger.warning(
                        "Несовпадение суммы MulenPay: callback=%s, ожидаемо=%s",
                        amount_kopeks,
                        payment.amount_kopeks,
                    )

                transaction = await create_transaction(
                    db,
                    user_id=payment.user_id,
                    type=TransactionType.DEPOSIT,
                    amount_kopeks=payment.amount_kopeks,
                    description=f"Пополнение через Mulen Pay ({mulen_payment_id_raw})",
                    payment_method=PaymentMethod.MULENPAY,
                    external_id=(
                        str(mulen_payment_id_int)
                        if mulen_payment_id_int is not None
                        else payment.uuid
                    ),
                    is_completed=True,
                )

                await link_mulenpay_payment_to_transaction(
                    db,
                    payment=payment,
                    transaction_id=transaction.id,
                )

                user = await get_user_by_id(db, payment.user_id)
                if not user:
                    logger.error("Пользователь %s не найден для MulenPay платежа", payment.user_id)
                    return False

                old_balance = user.balance_kopeks
                user.balance_kopeks += payment.amount_kopeks
                user.updated_at = datetime.utcnow()

                await db.commit()
                await db.refresh(user)

                try:
                    from app.services.referral_service import process_referral_topup

                    await process_referral_topup(db, user.id, payment.amount_kopeks, self.bot)
                except Exception as referral_error:
                    logger.error(
                        "Ошибка обработки реферального пополнения MulenPay: %s",
                        referral_error,
                    )

                await update_mulenpay_payment_status(
                    db,
                    payment=payment,
                    status="success",
                    is_paid=True,
                    paid_at=paid_at,
                    callback_payload=callback_data,
                    mulen_payment_id=mulen_payment_id_int,
                )

                if self.bot:
                    try:
                        from app.services.admin_notification_service import AdminNotificationService

                        notification_service = AdminNotificationService(self.bot)
                        await notification_service.send_balance_topup_notification(
                            db,
                            user,
                            transaction,
                            old_balance,
                        )
                    except Exception as notify_error:
                        logger.error(
                            "Ошибка отправки админ уведомления MulenPay: %s",
                            notify_error,
                        )

                if self.bot:
                    try:
                        keyboard = await self.build_topup_success_keyboard(user)
                        await self.bot.send_message(
                            user.telegram_id,
                            (
                                "✅ <b>Пополнение успешно!</b>\n\n"
                                f"💰 Сумма: {settings.format_price(payment.amount_kopeks)}\n"
                                "🦊 Способ: Mulen Pay\n"
                                f"🆔 Транзакция: {transaction.id}\n\n"
                                "Баланс пополнен автоматически!"
                            ),
                            parse_mode="HTML",
                            reply_markup=keyboard,
                        )
                    except Exception as user_notify_error:
                        logger.error(
                            "Ошибка отправки уведомления пользователю MulenPay: %s",
                            user_notify_error,
                        )

                logger.info(
                    "✅ Обработан MulenPay платеж %s для пользователя %s",
                    payment.uuid,
                    payment.user_id,
                )
                return True

            if payment_status == "cancel":
                await update_mulenpay_payment_status(
                    db,
                    payment=payment,
                    status="canceled",
                    callback_payload=callback_data,
                    mulen_payment_id=mulen_payment_id_int,
                )
                logger.info("MulenPay платеж %s отменен", payment.uuid)
                return True

            await update_mulenpay_payment_status(
                db,
                payment=payment,
                status=payment_status or "unknown",
                callback_payload=callback_data,
                mulen_payment_id=mulen_payment_id_int,
            )
            logger.info(
                "Получен MulenPay callback со статусом %s для платежа %s",
                payment_status,
                payment.uuid,
            )
            return True

        except Exception as error:
            logger.error(f"Ошибка обработки MulenPay callback: {error}", exc_info=True)
            return False

    async def process_pal24_postback(self, db: AsyncSession, payload: Dict[str, Any]) -> bool:

        if not self.pal24_service or not self.pal24_service.is_configured:
            logger.error("Pal24 сервис не инициализирован")
            return False

        try:
            order_id_raw = payload.get("InvId")
            order_id = str(order_id_raw) if order_id_raw is not None else None
            if not order_id:
                logger.error("Pal24 postback без InvId")
                return False

            payment = await get_pal24_payment_by_order_id(db, order_id)
            if not payment:
                bill_id = payload.get("BillId")
                if bill_id:
                    payment = await get_pal24_payment_by_bill_id(db, str(bill_id))

            if not payment:
                logger.error("Pal24 платеж не найден для order_id=%s", order_id)
                return False

            if payment.transaction_id and payment.is_paid:
                logger.info("Pal24 платеж %s уже обработан", payment.bill_id)
                return True

            status = str(payload.get("Status", "UNKNOWN")).upper()
            payment_id = payload.get("TrsId")
            balance_amount = payload.get("BalanceAmount")
            balance_currency = payload.get("BalanceCurrency")
            payer_account = payload.get("AccountNumber")
            payment_method = payload.get("AccountType")

            try:
                amount_kopeks = Pal24Service.convert_to_kopeks(str(payload.get("OutSum")))
            except Exception:
                logger.warning("Не удалось распарсить сумму Pal24, используем сохраненное значение")
                amount_kopeks = payment.amount_kopeks

            if amount_kopeks != payment.amount_kopeks:
                logger.warning(
                    "Несовпадение суммы Pal24: callback=%s, ожидаемо=%s",
                    amount_kopeks,
                    payment.amount_kopeks,
                )

            is_success = status in Pal24Service.BILL_SUCCESS_STATES
            is_failed = status in Pal24Service.BILL_FAILED_STATES

            await update_pal24_payment_status(
                db,
                payment,
                status=status,
                is_active=not is_failed,
                is_paid=is_success,
                payment_id=str(payment_id) if payment_id else None,
                payment_status=status,
                payment_method=str(payment_method) if payment_method else None,
                balance_amount=str(balance_amount) if balance_amount is not None else None,
                balance_currency=str(balance_currency) if balance_currency is not None else None,
                payer_account=str(payer_account) if payer_account is not None else None,
                callback_payload=payload,
            )

            if not is_success:
                logger.info(
                    "Получен Pal24 статус %s для платежа %s (успех=%s)",
                    status,
                    payment.bill_id,
                    is_success,
                )
                return True

            user = await get_user_by_id(db, payment.user_id)
            if not user:
                logger.error("Пользователь %s не найден для Pal24 платежа", payment.user_id)
                return False

            transaction = await create_transaction(
                db=db,
                user_id=payment.user_id,
                type=TransactionType.DEPOSIT,
                amount_kopeks=payment.amount_kopeks,
                description=f"Пополнение через Pal24 ({payment_id})",
                payment_method=PaymentMethod.PAL24,
                external_id=str(payment_id) if payment_id else payment.bill_id,
                is_completed=True,
            )

            await link_pal24_payment_to_transaction(db, payment, transaction.id)

            old_balance = user.balance_kopeks
            user.balance_kopeks += payment.amount_kopeks
            user.updated_at = datetime.utcnow()
            await db.commit()
            await db.refresh(user)

            try:
                from app.services.referral_service import process_referral_topup

                await process_referral_topup(db, user.id, payment.amount_kopeks, self.bot)
            except Exception as referral_error:
                logger.error("Ошибка обработки реферального пополнения Pal24: %s", referral_error)

            if self.bot:
                try:
                    from app.services.admin_notification_service import AdminNotificationService

                    notification_service = AdminNotificationService(self.bot)
                    await notification_service.send_balance_topup_notification(
                        db,
                        user,
                        transaction,
                        old_balance,
                    )
                except Exception as notify_error:
                    logger.error("Ошибка отправки админ уведомления Pal24: %s", notify_error)

            if self.bot:
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
                except Exception as user_notify_error:
                    logger.error("Ошибка отправки уведомления пользователю Pal24: %s", user_notify_error)

            logger.info(
                "✅ Обработан Pal24 платеж %s для пользователя %s",
                payment.bill_id,
                payment.user_id,
            )

            return True

        except Exception as error:
            logger.error("Ошибка обработки Pal24 postback: %s", error, exc_info=True)
            return False

    @staticmethod
    def _map_mulenpay_status(status_code: Optional[int]) -> str:
        mapping = {
            0: "created",
            1: "processing",
            2: "canceled",
            3: "success",
            4: "error",
            5: "hold",
            6: "hold",
        }
        return mapping.get(status_code, "unknown")

    async def get_mulenpay_payment_status(
        self,
        db: AsyncSession,
        local_payment_id: int,
    ) -> Optional[Dict[str, Any]]:
        try:
            payment = await get_mulenpay_payment_by_local_id(db, local_payment_id)
            if not payment:
                return None

            remote_status_code = None
            remote_data = None

            if (
                self.mulenpay_service
                and payment.mulen_payment_id is not None
            ):
                response = await self.mulenpay_service.get_payment(payment.mulen_payment_id)
                if response and response.get("success"):
                    remote_data = response.get("payment")
                    if isinstance(remote_data, dict):
                        remote_status_code = remote_data.get("status")
                        mapped_status = self._map_mulenpay_status(remote_status_code)

                        if mapped_status == "success" and not payment.is_paid:
                            await self.process_mulenpay_callback(
                                db,
                                {
                                    "uuid": payment.uuid,
                                    "payment_status": "success",
                                    "id": remote_data.get("id"),
                                    "amount": remote_data.get("amount"),
                                },
                            )
                            payment = await get_mulenpay_payment_by_local_id(
                                db, local_payment_id
                            )
                        elif mapped_status and mapped_status != payment.status:
                            await update_mulenpay_payment_status(
                                db,
                                payment=payment,
                                status=mapped_status,
                                mulen_payment_id=remote_data.get("id"),
                            )
                            payment = await get_mulenpay_payment_by_local_id(
                                db, local_payment_id
                            )

            return {
                "payment": payment,
                "status": payment.status,
                "is_paid": payment.is_paid,
                "remote_status_code": remote_status_code,
                "remote_data": remote_data,
            }

        except Exception as error:
            logger.error(f"Ошибка получения статуса MulenPay: {error}", exc_info=True)
            return None

    async def get_pal24_payment_status(
        self,
        db: AsyncSession,
        local_payment_id: int,
    ) -> Optional[Dict[str, Any]]:
        try:
            payment = await get_pal24_payment_by_id(db, local_payment_id)
            if not payment:
                return None

            remote_status = None
            remote_data = None

            if self.pal24_service and payment.bill_id:
                try:
                    response = await self.pal24_service.get_bill_status(payment.bill_id)
                    remote_data = response
                    remote_status = (
                        response.get("status")
                        or response.get("bill", {}).get("status")
                    )

                    if remote_status and remote_status != payment.status:
                        await update_pal24_payment_status(
                            db,
                            payment,
                            status=str(remote_status).upper(),
                        )
                        payment = await get_pal24_payment_by_id(db, local_payment_id)
                except Pal24APIError as error:
                    logger.error("Ошибка Pal24 API при получении статуса: %s", error)

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

    async def process_cryptobot_webhook(self, db: AsyncSession, webhook_data: dict) -> bool:
        try:
            from app.database.crud.cryptobot import (
                get_cryptobot_payment_by_invoice_id,
                update_cryptobot_payment_status,
                link_cryptobot_payment_to_transaction
            )
            from app.database.crud.transaction import create_transaction
            from app.database.models import TransactionType, PaymentMethod
            
            update_type = webhook_data.get("update_type")
            
            if update_type != "invoice_paid":
                logger.info(f"Пропуск CryptoBot webhook с типом: {update_type}")
                return True
            
            payload = webhook_data.get("payload", {})
            invoice_id = str(payload.get("invoice_id"))
            status = "paid"
            
            if not invoice_id:
                logger.error("CryptoBot webhook без invoice_id")
                return False
            
            payment = await get_cryptobot_payment_by_invoice_id(db, invoice_id)
            if not payment:
                logger.error(f"CryptoBot платеж не найден в БД: {invoice_id}")
                return False
            
            if payment.status == "paid":
                logger.info(f"CryptoBot платеж {invoice_id} уже обработан")
                return True
            
            paid_at_str = payload.get("paid_at")
            paid_at = None
            if paid_at_str:
                try:
                    paid_at = datetime.fromisoformat(paid_at_str.replace('Z', '+00:00')).replace(tzinfo=None)
                except:
                    paid_at = datetime.utcnow()
            else:
                paid_at = datetime.utcnow()
            
            updated_payment = await update_cryptobot_payment_status(
                db, invoice_id, status, paid_at
            )
            
            if not updated_payment.transaction_id:
                amount_usd = updated_payment.amount_float
                
                try:
                    amount_rubles = await currency_converter.usd_to_rub(amount_usd)
                    amount_kopeks = int(amount_rubles * 100)
                    conversion_rate = amount_rubles / amount_usd if amount_usd > 0 else 0
                    logger.info(f"Конвертация USD->RUB: ${amount_usd} -> {amount_rubles}₽ (курс: {conversion_rate:.2f})")
                except Exception as e:
                    logger.warning(f"Ошибка конвертации валют для платежа {invoice_id}, используем курс 1:1: {e}")
                    amount_rubles = amount_usd
                    amount_kopeks = int(amount_usd * 100)
                    conversion_rate = 1.0
                
                if amount_kopeks <= 0:
                    logger.error(f"Некорректная сумма после конвертации: {amount_kopeks} копеек для платежа {invoice_id}")
                    return False
                
                transaction = await create_transaction(
                    db,
                    user_id=updated_payment.user_id,
                    type=TransactionType.DEPOSIT,
                    amount_kopeks=amount_kopeks,
                    description=f"Пополнение через CryptoBot ({updated_payment.amount} {updated_payment.asset} → {amount_rubles:.2f}₽)",
                    payment_method=PaymentMethod.CRYPTOBOT,
                    external_id=invoice_id,
                    is_completed=True
                )
                
                await link_cryptobot_payment_to_transaction(
                    db, invoice_id, transaction.id
                )
                
                user = await get_user_by_id(db, updated_payment.user_id)
                if user:
                    old_balance = user.balance_kopeks
                    
                    user.balance_kopeks += amount_kopeks
                    user.updated_at = datetime.utcnow()
                    
                    await db.commit()
                    await db.refresh(user)
                    
                    try:
                        from app.services.referral_service import process_referral_topup
                        await process_referral_topup(db, user.id, amount_kopeks, self.bot)
                    except Exception as e:
                        logger.error(f"Ошибка обработки реферального пополнения CryptoBot: {e}")
                    
                    if self.bot:
                        try:
                            from app.services.admin_notification_service import AdminNotificationService
                            notification_service = AdminNotificationService(self.bot)
                            await notification_service.send_balance_topup_notification(
                                db, user, transaction, old_balance
                            )
                        except Exception as e:
                            logger.error(f"Ошибка отправки уведомления о пополнении CryptoBot: {e}")
                    
                    if self.bot:
                        try:
                            keyboard = await self.build_topup_success_keyboard(user)

                            await self.bot.send_message(
                                user.telegram_id,
                                f"✅ <b>Пополнение успешно!</b>\n\n"
                                f"💰 Сумма: {settings.format_price(amount_kopeks)}\n"
                                f"🪙 Платеж: {updated_payment.amount} {updated_payment.asset}\n"
                                f"💱 Курс: 1 USD = {conversion_rate:.2f}₽\n"
                                f"🆔 Транзакция: {invoice_id[:8]}...\n\n"
                                f"Баланс пополнен автоматически!",
                                parse_mode="HTML",
                                reply_markup=keyboard,
                            )
                            logger.info(f"✅ Отправлено уведомление пользователю {user.telegram_id} о пополнении на {amount_rubles:.2f}₽ ({updated_payment.asset})")
                        except Exception as e:
                            logger.error(f"Ошибка отправки уведомления о пополнении CryptoBot: {e}")
                else:
                    logger.error(f"Пользователь с ID {updated_payment.user_id} не найден при пополнении баланса")
                    return False
            
            return True
            
        except Exception as e:
            logger.error(f"Ошибка обработки CryptoBot webhook: {e}", exc_info=True)
            return False
