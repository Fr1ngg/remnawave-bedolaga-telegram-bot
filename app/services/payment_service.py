import logging
import hashlib
import hmac
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
from app.services.cart_topup_service import notify_saved_cart_after_topup

logger = logging.getLogger(__name__)


class PaymentService:
    
    def __init__(self, bot: Optional[Bot] = None):
        self.bot = bot
        self.yookassa_service = YooKassaService() if settings.is_yookassa_enabled() else None
        self.stars_service = TelegramStarsService(bot) if bot else None
        self.cryptobot_service = CryptoBotService() if settings.is_cryptobot_enabled() else None
    
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
                        user_language = user.language if user else "ru"
                        texts = get_texts(user_language)
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

                        keyboard = InlineKeyboardMarkup(
                            inline_keyboard=[
                                [first_button],
                                [InlineKeyboardButton(text="💰 Мой баланс", callback_data="menu_balance")],
                                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_menu")],
                            ]
                        )

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
                        await notify_saved_cart_after_topup(
                            db=db,
                            bot=self.bot,
                            user_id=user.id,
                            amount_kopeks=amount_kopeks,
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
                            user_language = user.language if user else "ru"
                            texts = get_texts(user_language)
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

                            keyboard = InlineKeyboardMarkup(
                                inline_keyboard=[
                                    [first_button],
                                    [InlineKeyboardButton(text="💰 Мой баланс", callback_data="menu_balance")],
                                    [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_menu")],
                                ]
                            )

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
                            await notify_saved_cart_after_topup(
                                db=db,
                                bot=self.bot,
                                user_id=user.id,
                                amount_kopeks=updated_payment.amount_kopeks,
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

            user_language = user.language if user else "ru"
            texts = get_texts(user_language)
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

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [first_button],
                    [InlineKeyboardButton(text="💰 Мой баланс", callback_data="menu_balance")],
                    [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_menu")],
                ]
            )

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
            if user:
                await notify_saved_cart_after_topup(
                    db=db,
                    bot=self.bot,
                    user_id=user.id,
                    amount_kopeks=amount_kopeks,
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
                            user_language = user.language if user else "ru"
                            texts = get_texts(user_language)
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

                            keyboard = InlineKeyboardMarkup(
                                inline_keyboard=[
                                    [first_button],
                                    [InlineKeyboardButton(text="💰 Мой баланс", callback_data="menu_balance")],
                                    [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_menu")],
                                ]
                            )

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
                            await notify_saved_cart_after_topup(
                                db=db,
                                bot=self.bot,
                                user_id=user.id,
                                amount_kopeks=amount_kopeks,
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