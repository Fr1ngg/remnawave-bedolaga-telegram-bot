import logging
from aiogram import types
from aiogram.fsm.storage.base import StorageKey
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.user import get_user_by_id
from app.localization.texts import get_texts

logger = logging.getLogger(__name__)


async def notify_saved_cart_after_topup(
    db: AsyncSession,
    bot,
    user_id: int,
    amount_kopeks: int,
) -> bool:
    """Send prompt to return to saved cart after successful top-up if needed."""
    if not bot:
        return False

    user = await get_user_by_id(db, user_id)
    if not user:
        return False

    try:
        from app.bot import dp

        storage = dp.storage
        key = StorageKey(bot_id=bot.id, chat_id=user.telegram_id, user_id=user.telegram_id)

        state_data = await storage.get_data(key)
        current_state = await storage.get_state(key)

        if current_state != "SubscriptionStates:cart_saved_for_topup":
            return False

        if not state_data.get("saved_cart"):
            return False

        texts = get_texts(user.language)
        total_price = state_data.get("total_price", 0)

        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="🛒 Вернуться к оформлению подписки",
                        callback_data="return_to_saved_cart",
                    )
                ],
                [
                    types.InlineKeyboardButton(
                        text="💰 Мой баланс",
                        callback_data="menu_balance",
                    )
                ],
                [
                    types.InlineKeyboardButton(
                        text="🏠 Главное меню",
                        callback_data="back_to_menu",
                    )
                ],
            ]
        )

        success_text = (
            f"✅ Баланс пополнен на {texts.format_price(amount_kopeks)}!\n\n"
            f"💰 Текущий баланс: {texts.format_price(user.balance_kopeks)}\n\n"
            f"🛒 У вас есть сохраненная корзина подписки\n"
            f"Стоимость: {texts.format_price(total_price)}\n\n"
            f"Хотите продолжить оформление?"
        )

        await bot.send_message(
            chat_id=user.telegram_id,
            text=success_text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )

        return True

    except Exception as exc:
        logger.error(
            "Ошибка обработки успешного пополнения с корзиной: %s",
            exc,
        )
        return False
