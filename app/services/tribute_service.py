import logging
import json
from typing import Optional, Dict, Any
from datetime import datetime

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from app.config import settings
from app.database.database import get_db
from app.database.models import Transaction, TransactionType, PaymentMethod
from app.database.crud.transaction import (
    create_transaction, get_transaction_by_external_id, complete_transaction
)
from app.database.crud.user import get_user_by_telegram_id
from app.external.tribute import TributeService as TributeAPI
from app.services.payment_service import PaymentService
from app.services.subscription_auto_purchase_service import (
    auto_purchase_saved_cart_after_topup,
)
from app.utils.user_utils import format_referrer_info

logger = logging.getLogger(__name__)


class TributeService:
    _invoice_messages: Dict[int, Dict[str, int]] = {}
    
    def __init__(self, bot: Bot):
        self.bot = bot
        self.tribute_api = TributeAPI()

    @classmethod
    def remember_invoice_message(cls, user_id: int, chat_id: int, message_id: int) -> None:
        cls._invoice_messages[user_id] = {"chat_id": chat_id, "message_id": message_id}

    async def _cleanup_invoice_message(self, user_id: int) -> None:
        invoice_message = self._invoice_messages.pop(user_id, None)
        if not invoice_message or not getattr(self, "bot", None):
            return

        chat_id = invoice_message.get("chat_id")
        message_id = invoice_message.get("message_id")
        if not chat_id or not message_id:
            return

        try:
            await self.bot.delete_message(chat_id, message_id)
        except Exception as error:  # pragma: no cover - depends on bot rights
            logger.warning("Не удалось удалить Tribute счёт %s: %s", message_id, error)
    
    async def create_payment_link(
        self,
        user_id: int,
        amount_kopeks: int,
        description: str = "Пополнение баланса"
    ) -> Optional[str]:
        
        if not settings.TRIBUTE_ENABLED:
            logger.warning("Tribute платежи отключены")
            return None
        
        try:
            payment_url = await self.tribute_api.create_payment_link(
                user_id=user_id,
                amount_kopeks=amount_kopeks,
                description=description
            )
            
            if not payment_url:
                return None
            
            return payment_url
            
        except Exception as e:
            logger.error(f"Ошибка создания Tribute платежа: {e}")
            return None
    
    async def process_webhook(
        self,
        payload: str
    ) -> Dict[str, Any]:
        
        try:
            webhook_data = json.loads(payload)
        except json.JSONDecodeError:
            logger.error("Некорректный JSON в Tribute webhook")
            return {"status": "error", "reason": "invalid_json"}
        
        logger.info(f"Получен Tribute webhook: {json.dumps(webhook_data, ensure_ascii=False)}")
        
        processed_data = await self.tribute_api.process_webhook(webhook_data)
        if not processed_data:
            return {"status": "ignored", "reason": "invalid_data"}
        
        event_type = processed_data.get("event_type", "payment")
        status = processed_data.get("status")
        
        if event_type == "payment" and status == "paid":
            await self._handle_successful_payment(processed_data)
        elif event_type == "payment" and status == "failed":
            await self._handle_failed_payment(processed_data)
        elif event_type == "refund":
            await self._handle_refund(processed_data)
        
        return {"status": "ok", "event": event_type}
    
    async def _handle_successful_payment(self, payment_data: Dict[str, Any]):
        try:
            user_telegram_id = payment_data["user_id"] 
            amount_kopeks = payment_data["amount_kopeks"]
            payment_id = payment_data["payment_id"]
            
            logger.info(f"Обрабатываем успешный Tribute платеж: user_telegram_id={user_telegram_id}, amount={amount_kopeks}, payment_id={payment_id}")
            
            async for session in get_db():
                user = await get_user_by_telegram_id(session, user_telegram_id)
                if not user:
                    logger.error(f"Пользователь {user_telegram_id} не найден")
                    return
                
                logger.info(f"Найден пользователь {user.telegram_id}, текущий баланс: {user.balance_kopeks} коп")
                
                from app.database.crud.transaction import check_tribute_payment_duplicate
                
                duplicate_transaction = await check_tribute_payment_duplicate(
                    session, payment_id, amount_kopeks, user_telegram_id
                )
                
                if duplicate_transaction:
                    logger.warning(f"Найден дубликат платежа в течение 24ч:")
                    logger.warning(f"   Transaction ID: {duplicate_transaction.id}")
                    logger.warning(f"   Amount: {duplicate_transaction.amount_kopeks} коп")
                    logger.warning(f"   Created: {duplicate_transaction.created_at}")
                    logger.warning(f"   External ID: {duplicate_transaction.external_id}")
                    logger.warning(f"Платеж игнорирован - это дубликат свежего платежа")
                    return
                
                from app.database.crud.transaction import create_unique_tribute_transaction
                
                transaction = await create_unique_tribute_transaction(
                    db=session,
                    user_id=user.id,
                    payment_id=payment_id,
                    amount_kopeks=amount_kopeks,
                    description=f"Пополнение через Tribute: {amount_kopeks/100}₽ (ID: {payment_id})"
                )

                old_balance = user.balance_kopeks
                was_first_topup = not user.has_made_first_topup

                user.balance_kopeks += amount_kopeks
                user.updated_at = datetime.utcnow()

                promo_group = user.get_primary_promo_group()
                subscription = getattr(user, "subscription", None)
                referrer_info = format_referrer_info(user)
                topup_status = "🆕 Первое пополнение" if was_first_topup else "🔄 Пополнение"

                await session.commit()

                try:
                    from app.services.referral_service import process_referral_topup
                    await process_referral_topup(session, user.id, amount_kopeks, self.bot)
                except Exception as e:
                    logger.error(f"Ошибка обработки реферального пополнения Tribute: {e}")

                if was_first_topup and not user.has_made_first_topup:
                    user.has_made_first_topup = True
                    await session.commit()

                await session.refresh(user)

                logger.info(
                    f"✅ Баланс пользователя {user_telegram_id} обновлен: {old_balance} -> {user.balance_kopeks} коп (+{amount_kopeks})"
                )
                logger.info(f"✅ Создана транзакция ID: {transaction.id}")

                if was_first_topup:
                    logger.info(f"Отмечен первый топап для пользователя {user_telegram_id}")


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
                        db=session,
                    )
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления о Tribute пополнении: {e}")

                await self._cleanup_invoice_message(user_telegram_id)
                await self._send_success_notification(user_telegram_id, amount_kopeks)
                
                logger.info(f"🎉 Успешно обработан Tribute платеж: {amount_kopeks/100}₽ для пользователя {user_telegram_id}")
                break
                
        except Exception as e:
            logger.error(f"⌘ Ошибка обработки успешного Tribute платежа: {e}", exc_info=True)
    
    async def _handle_failed_payment(self, payment_data: Dict[str, Any]):
        
        try:
            user_id = payment_data["user_id"]
            payment_id = payment_data["payment_id"]
            
            async for session in get_db():
                transaction = await get_transaction_by_external_id(
                    session, f"donation_{payment_id}", PaymentMethod.TRIBUTE
                )
                
                if transaction:
                    transaction.description = f"{transaction.description} (платеж отклонен)"
                    await session.commit()
                
                await self._send_failure_notification(user_id)
                
                logger.info(f"Обработан неудачный Tribute платеж для пользователя {user_id}")
                break
                
        except Exception as e:
            logger.error(f"Ошибка обработки неудачного Tribute платежа: {e}")
    
    async def _handle_refund(self, refund_data: Dict[str, Any]):
        
        try:
            user_id = refund_data["user_id"]
            amount_kopeks = refund_data["amount_kopeks"]
            payment_id = refund_data["payment_id"]
            
            async for session in get_db():
                await create_transaction(
                    db=session,
                    user_id=user_id,
                    type=TransactionType.REFUND,
                    amount_kopeks=-amount_kopeks, 
                    description=f"Возврат Tribute платежа {payment_id}",
                    payment_method=PaymentMethod.TRIBUTE,
                    external_id=f"refund_{payment_id}",
                    is_completed=True
                )
                
                user = await get_user_by_telegram_id(session, user_id)
                if user and user.balance_kopeks >= amount_kopeks:
                    user.balance_kopeks -= amount_kopeks
                    await session.commit()
                
                await self._send_refund_notification(user_id, amount_kopeks)
                
                logger.info(f"Обработан возврат Tribute: {amount_kopeks/100}₽ для пользователя {user_id}")
                break
                
        except Exception as e:
            logger.error(f"Ошибка обработки возврата Tribute: {e}")
    

    async def _send_success_notification(self, user_id: int, amount_kopeks: int):

        try:
            amount_rubles = amount_kopeks / 100

            async for session in get_db():
                user = await get_user_by_telegram_id(session, user_id)
                break

            # Сначала отправляем стандартное уведомление
            payment_service = PaymentService(self.bot)
            keyboard = await payment_service.build_topup_success_keyboard(user)

            text = (
                f"✅ **Платеж успешно получен!**\n\n"
                f"💰 Сумма: {int(amount_rubles)} ₽\n"
                f"💳 Способ оплаты: Tribute\n"
                f"🎉 Средства зачислены на баланс!\n\n"
                f"⚠️ <b>Важно:</b> Пополнение баланса не активирует подписку автоматически. "
                f"Обязательно активируйте подписку отдельно!\n\n"
                f"🔄 При наличии сохранённой корзины подписки и включенной автопокупке, "
                f"подписка будет приобретена автоматически после пополнения баланса.\n\n"
                f"Спасибо за оплату! 🙏"
            )

            await self.bot.send_message(
                user_id,
                text,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )

            # Проверяем наличие сохраненной корзины для возврата к оформлению подписки
            from app.services.user_cart_service import user_cart_service
            has_saved_cart = await user_cart_service.has_user_cart(user.id)
            auto_purchase_success = False
            if has_saved_cart:
                try:
                    auto_purchase_success = await auto_purchase_saved_cart_after_topup(
                        session,
                        user,
                        bot=self.bot,
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

            if has_saved_cart and self.bot:
                # Если у пользователя есть сохраненная корзина,
                # отправляем ему уведомление с кнопкой вернуться к оформлению
                from app.localization.texts import get_texts
                from aiogram import types

                texts = get_texts(user.language)
                cart_message = texts.BALANCE_TOPUP_CART_REMINDER_DETAILED.format(
                    total_amount=settings.format_price(amount_kopeks)
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
                    chat_id=user_id,
                    text=f"✅ Баланс пополнен на {settings.format_price(amount_kopeks)}!\n\n"
                         f"⚠️ <b>Важно:</b> Пополнение баланса не активирует подписку автоматически. "
                         f"Обязательно активируйте подписку отдельно!\n\n{cart_message}",
                    reply_markup=keyboard
                )
                logger.info(
                    "Отправлено уведомление с кнопкой возврата к оформлению подписки пользователю %s",
                    user_id,
                )

        except Exception as e:
            logger.error(f"Ошибка отправки уведомления об успешном платеже: {e}")
    async def _send_failure_notification(self, user_id: int):
        
        try:
            text = (
                "⌘ **Платеж не прошел**\n\n"
                "К сожалению, ваш платеж через Tribute был отклонен.\n\n"
                "Возможные причины:\n"
                "• Недостаточно средств на карте\n"
                "• Технические проблемы банка\n"
                "• Превышен лимит операций\n\n"
                "Попробуйте еще раз или обратитесь в поддержку."
            )
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="menu_balance")],
                [InlineKeyboardButton(text="💬 Поддержка", callback_data="menu_support")]
            ])
            
            await self.bot.send_message(
                user_id,
                text,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о неудачном платеже: {e}")
    
    async def _send_refund_notification(self, user_id: int, amount_kopeks: int):
        
        try:
            amount_rubles = amount_kopeks / 100
            
            text = (
                f"🔄 **Возврат средств**\n\n"
                f"💰 Сумма возврата: {int(amount_rubles)} ₽\n"
                f"💳 Способ: Tribute\n\n"
                f"Средства будут возвращены на вашу карту в течение 3-5 рабочих дней.\n\n"
                f"Если у вас есть вопросы, обратитесь в поддержку."
            )
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💬 Поддержка", callback_data="menu_support")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_menu")]
            ])
            
            await self.bot.send_message(
                user_id,
                text,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о возврате: {e}")
    
    async def force_process_payment(
        self, 
        payment_id: str, 
        user_id: int, 
        amount_kopeks: int,
        description: str = "Принудительная обработка Tribute платежа"
    ) -> bool:
        
        try:
            logger.info(f"🔧 ПРИНУДИТЕЛЬНАЯ ОБРАБОТКА: payment_id={payment_id}, user_id={user_id}, amount={amount_kopeks}")
            
            async for session in get_db():
                user = await get_user_by_telegram_id(session, user_id)
                if not user:
                    logger.error(f"⌘ Пользователь {user_id} не найден")
                    return False
                
                external_id = f"force_donation_{payment_id}_{int(datetime.utcnow().timestamp())}"
                
                transaction = await create_transaction(
                    db=session,
                    user_id=user.id,
                    type=TransactionType.DEPOSIT,
                    amount_kopeks=amount_kopeks,
                    description=description,
                    payment_method=PaymentMethod.TRIBUTE,
                    external_id=external_id,
                    is_completed=True
                )
                
                old_balance = user.balance_kopeks
                user.balance_kopeks += amount_kopeks
                user.updated_at = datetime.utcnow()
                
                await session.commit()
                
                logger.info(f"💰 ПРИНУДИТЕЛЬНО обновлен баланс: {old_balance} -> {user.balance_kopeks} коп")
                
                await self._send_success_notification(user_id, amount_kopeks)
                
                logger.info(f"✅ Принудительно обработан платеж {payment_id}")
                return True
                
        except Exception as e:
            logger.error(f"⌘ Ошибка принудительной обработки: {e}", exc_info=True)
            return False
    
    async def get_payment_status(self, payment_id: str) -> Optional[Dict[str, Any]]:
        return await self.tribute_api.get_payment_status(payment_id)
    
    async def create_refund(
        self,
        payment_id: str,
        amount_kopeks: Optional[int] = None,
        reason: str = "Возврат по запросу"
    ) -> Optional[Dict[str, Any]]:
        return await self.tribute_api.refund_payment(payment_id, amount_kopeks, reason)
