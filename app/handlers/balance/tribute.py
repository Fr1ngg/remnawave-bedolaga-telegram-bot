import logging
from aiogram import types

from app.config import settings
from app.database.models import User
from app.keyboards.inline import get_back_keyboard
from app.localization.texts import get_texts
from app.utils.decorators import error_handler

logger = logging.getLogger(__name__)


@error_handler
async def start_tribute_payment(
    callback: types.CallbackQuery,
    db_user: User,
):
    texts = get_texts(db_user.language)

    if not settings.TRIBUTE_ENABLED:
        await callback.answer("❌ Оплата картой временно недоступна", show_alert=True)
        return

    try:
        from app.services.tribute_service import TributeService

        tribute_service = TributeService(callback.bot)
        payment_url = await tribute_service.create_payment_link(
            user_id=db_user.telegram_id,
            amount_kopeks=0,
            description="Пополнение баланса VPN",
        )

        if not payment_url:
            await callback.answer("❌ Ошибка создания платежа", show_alert=True)
            return

        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="💳 Перейти к оплате", url=payment_url)],
                [types.InlineKeyboardButton(text=texts.BACK, callback_data="balance_topup")],
            ]
        )

        await callback.message.edit_text(
            f"💳 <b>Пополнение банковской картой</b>\n\n",
            f"• Введите любую сумму от 100₽\n",
            f"• Безопасная оплата через Tribute\n",
            f"• Мгновенное зачисление на баланс\n",
            f"• Принимаем карты Visa, MasterCard, МИР\n\n",
            f"• 🚨 НЕ ОТПРАВЛЯТЬ ПЛАТЕЖ АНОНИМНО!\n\n",
            f"Нажмите кнопку для перехода к оплате:",
            reply_markup=keyboard,
            parse_mode="HTML",
        )

        TributeService.remember_invoice_message(
            db_user.telegram_id,
            callback.message.chat.id,
            callback.message.message_id,
        )

    except Exception as e:
        logger.error(f"Ошибка создания Tribute платежа: {e}")
        await callback.answer("❌ Ошибка создания платежа", show_alert=True)

    await callback.answer()
