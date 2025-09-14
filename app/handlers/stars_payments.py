import logging
from aiogram import Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.services.payment_service import PaymentService
from app.external.telegram_stars import TelegramStarsService
from app.database.crud.user import get_user_by_telegram_id
from app.localization.texts import get_texts

logger = logging.getLogger(__name__)


async def handle_pre_checkout_query(query: types.PreCheckoutQuery):
    try:
        logger.info(f"📋 Pre-checkout query от {query.from_user.id}: {query.total_amount} XTR, payload: {query.invoice_payload}")
        
        if not query.invoice_payload or not query.invoice_payload.startswith("balance_"):
            logger.warning(f"Невалидный payload: {query.invoice_payload}")
            await query.answer(
                ok=False,
                error_message="Ошибка валидации платежа. Попробуйте еще раз."
            )
            return
        
        try:
            from app.database.database import get_db
            async for db in get_db():
                user = await get_user_by_telegram_id(db, query.from_user.id)
                if not user:
                    logger.warning(f"Пользователь {query.from_user.id} не найден в БД")
                    await query.answer(
                        ok=False,
                        error_message="Пользователь не найден. Обратитесь в поддержку."
                    )
                    return
                break 
        except Exception as db_error:
            logger.error(f"Ошибка подключения к БД в pre_checkout_query: {db_error}")
            await query.answer(
                ok=False,
                error_message="Техническая ошибка. Попробуйте позже."
            )
            return
        
        await query.answer(ok=True)
        logger.info(f"✅ Pre-checkout одобрен для пользователя {query.from_user.id}")
        
    except Exception as e:
        logger.error(f"Ошибка в pre_checkout_query: {e}", exc_info=True)
        await query.answer(
            ok=False,
            error_message="Техническая ошибка. Попробуйте позже."
        )


async def handle_successful_payment(
    message: types.Message,
    db: AsyncSession,
    **kwargs
):
    try:
        payment = message.successful_payment
        user_id = message.from_user.id
        
        logger.info(
            f"💳 Успешный Stars платеж от {user_id}: "
            f"{payment.total_amount} XTR, "
            f"payload: {payment.invoice_payload}, "
            f"charge_id: {payment.telegram_payment_charge_id}"
        )
        
        user = await get_user_by_telegram_id(db, user_id)
        if not user:
            logger.error(f"Пользователь {user_id} не найден при обработке Stars платежа")
            await message.answer(
                "❌ Ошибка: пользователь не найден. Обратитесь в поддержку."
            )
            return
        
        payment_service = PaymentService(message.bot)
        success = await payment_service.process_stars_payment(
            db=db,
            user_id=user.id,
            stars_amount=payment.total_amount,
            payload=payment.invoice_payload,
            telegram_payment_charge_id=payment.telegram_payment_charge_id
        )
        
        if success:
            rubles_amount = TelegramStarsService.calculate_rubles_from_stars(payment.total_amount)

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

            await message.answer(
                f"🎉 <b>Платеж успешно обработан!</b>\n\n"
                f"⭐ Потрачено звезд: {payment.total_amount}\n"
                f"💰 Зачислено на баланс: {int(rubles_amount)} ₽\n"
                f"🆔 ID транзакции: {payment.telegram_payment_charge_id[:8]}...\n\n"
                f"Спасибо за пополнение! 🚀",
                parse_mode="HTML",
                reply_markup=keyboard,
            )

            logger.info(
                f"✅ Stars платеж успешно обработан: "
                f"пользователь {user.id}, {payment.total_amount} звезд → {int(rubles_amount)}₽"
            )
        else:
            logger.error(f"Ошибка обработки Stars платежа для пользователя {user.id}")
            await message.answer(
                "❌ Произошла ошибка при зачислении средств. "
                "Обратитесь в поддержку, платеж будет проверен вручную."
            )
        
    except Exception as e:
        logger.error(f"Ошибка в successful_payment: {e}", exc_info=True)
        await message.answer(
            "❌ Техническая ошибка при обработке платежа. "
            "Обратитесь в поддержку для решения проблемы."
        )


def register_stars_handlers(dp: Dispatcher):
    
    dp.pre_checkout_query.register(
        handle_pre_checkout_query,
        F.currency == "XTR" 
    )
    
    dp.message.register(
        handle_successful_payment,
        F.successful_payment
    )
    
    logger.info("🌟 Зарегистрированы обработчики Telegram Stars платежей")
