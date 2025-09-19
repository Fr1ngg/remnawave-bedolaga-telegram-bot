import logging
from typing import Optional

from aiogram import Dispatcher, types
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.user import get_user_by_id
from app.localization.texts import get_texts

logger = logging.getLogger(__name__)


async def notify_saved_cart_after_topup(
    db: AsyncSession,
    bot,
    user_id: int,
    amount_kopeks: int,
    storage: Optional[BaseStorage] = None,
) -> bool:
    """Send prompt to return to saved cart after successful top-up if needed."""
    if not bot:
        return False

    user = await get_user_by_id(db, user_id)
    if not user:
        return False

    fsm_storage = storage or _resolve_storage(bot)
    if not fsm_storage:
        logger.debug("FSM storage is not available for bot %s", bot.id if bot else "<unknown>")
        return False

    try:
        key = StorageKey(bot_id=bot.id, chat_id=user.telegram_id, user_id=user.telegram_id)
        state_data = await fsm_storage.get_data(key)
        current_state = await fsm_storage.get_state(key)
    except Exception as exc:
        logger.error("Не удалось получить состояние корзины из FSM: %s", exc, exc_info=True)
        return False

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


def _resolve_storage(bot) -> Optional[BaseStorage]:
    try:
        dispatcher = Dispatcher.get_current()
    except LookupError:
        dispatcher = None

    if not dispatcher and bot:
        dispatcher = getattr(bot, "dispatcher", None)

    if dispatcher and getattr(dispatcher, "storage", None):
        return dispatcher.storage

    return None
