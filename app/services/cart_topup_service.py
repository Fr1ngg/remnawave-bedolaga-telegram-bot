import logging
from typing import Optional

from aiogram import Dispatcher, types
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.user import get_user_by_id
from app.localization.texts import get_texts
from app.states import SubscriptionStates

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

    if not state_data.get("saved_cart"):
        logger.debug("У пользователя %s нет сохраненной корзины", user.telegram_id)
        return False

    if state_data.get("return_to_cart") is False:
        logger.debug("Повторное уведомление о корзине для пользователя %s не требуется", user.telegram_id)
        return False

    if current_state not in (None, SubscriptionStates.cart_saved_for_topup.state):
        logger.debug(
            "Текущее состояние %s пользователя %s не соответствует сохраненной корзине",
            current_state,
            user.telegram_id,
        )
        return False

    texts = get_texts(user.language)
    total_price = state_data.get("total_price", 0)
    has_enough_balance = user.balance_kopeks >= total_price > 0

    balance_text = texts.format_price(user.balance_kopeks)
    total_text = texts.format_price(total_price) if total_price else None

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

    success_parts = [
        f"✅ Баланс пополнен на {texts.format_price(amount_kopeks)}!",
        "",
        f"💰 Текущий баланс: {balance_text}",
    ]

    if total_price:
        success_parts.extend(
            [
                "",
                "🛒 У вас есть сохраненная корзина подписки",
                f"Стоимость: {total_text}",
            ]
        )

    if has_enough_balance:
        success_parts.extend([
            "",
            "🎯 Теперь средств достаточно, можно продолжить оформление.",
        ])
    elif total_price:
        missing_amount = total_price - user.balance_kopeks
        if missing_amount > 0:
            success_parts.extend(
                [
                    "",
                    "⚠️ Пока средств недостаточно для оформления.",
                    f"Не хватает: {texts.format_price(missing_amount)}",
                    "Пополните баланс еще или вернитесь к корзине, чтобы изменить параметры.",
                ]
            )

    success_parts.extend([
        "",
        "Хотите продолжить оформление?",
    ])

    success_text = "\n".join(success_parts)

    await bot.send_message(
        chat_id=user.telegram_id,
        text=success_text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )

    try:
        await fsm_storage.update_data(key, return_to_cart=False)
    except Exception as exc:
        logger.warning(
            "Не удалось обновить флаг возврата к корзине для пользователя %s: %s",
            user.telegram_id,
            exc,
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
