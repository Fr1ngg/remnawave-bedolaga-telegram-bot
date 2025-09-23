import logging
from aiogram import Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.localization.texts import get_texts, get_rules
from app.keyboards.inline import get_back_keyboard

logger = logging.getLogger(__name__)


async def handle_unknown_callback(
    callback: types.CallbackQuery,
    db_user: User
):
    
    texts = get_texts(db_user.language if db_user else "ru")
    
    await callback.answer(
        texts.t(
            "UNKNOWN_CALLBACK_ALERT",
            "❓ Неизвестная команда. Попробуйте ещё раз.",
        ),
        show_alert=True,
    )
    
    logger.warning(f"Неизвестный callback: {callback.data} от пользователя {callback.from_user.id}")


async def handle_noop(
    callback: types.CallbackQuery,
    db_user: User
):
    try:
        await callback.answer()
    except Exception:
        pass


async def handle_current_page(
    callback: types.CallbackQuery,
    db_user: User
):
    try:
        await callback.answer()
    except Exception:
        pass


async def handle_cancel(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User
):
    
    texts = get_texts(db_user.language)
    
    await state.clear()
    await callback.message.edit_text(
        texts.OPERATION_CANCELLED,
        reply_markup=get_back_keyboard(db_user.language)
    )
    await callback.answer()


async def handle_unknown_message(
    message: types.Message,
    db_user: User
):

    if getattr(message, "is_topic_message", False):
        return

    texts = get_texts(db_user.language if db_user else "ru")

    await message.answer(
        texts.t(
            "UNKNOWN_COMMAND_MESSAGE",
            "❓ Не понимаю эту команду. Используйте кнопки меню.",
        ),
        reply_markup=get_back_keyboard(db_user.language if db_user else "ru"),
    )


async def show_rules(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    texts = get_texts(db_user.language)

    rules_text = await get_rules(db_user.language)
    
    await callback.message.edit_text(
        rules_text,
        reply_markup=get_back_keyboard(db_user.language)
    )
    await callback.answer()


def register_handlers(dp: Dispatcher):
    
    dp.callback_query.register(
        show_rules,
        F.data == "menu_rules"
    )

    # No-op utility handlers used in many keyboards
    dp.callback_query.register(
        handle_noop,
        F.data == "noop"
    )
    dp.callback_query.register(
        handle_current_page,
        F.data == "current_page"
    )
    
    dp.callback_query.register(
        handle_cancel,
        F.data.in_(["cancel", "subscription_cancel"])
    )

    # Самый последний: ловим любые неизвестные текстовые сообщения
    dp.message.register(handle_unknown_message)
    