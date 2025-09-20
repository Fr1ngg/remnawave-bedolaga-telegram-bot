import logging
from aiogram import Dispatcher, types, F
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.keyboards.inline import get_support_keyboard
from app.localization.texts import get_texts

logger = logging.getLogger(__name__)


async def show_support_info(
    callback: types.CallbackQuery,
    db_user: User
):
    """Показывает основную информацию о поддержке"""
    texts = get_texts(db_user.language)
    
    # Проверяем, настроена ли поддержка
    if not settings.is_support_configured():
        await callback.message.edit_text(
            "⚠️ <b>Поддержка временно недоступна</b>\n\n"
            "Обратитесь к администратору для настройки системы поддержки.",
            reply_markup=get_support_keyboard(db_user.language),
            parse_mode="HTML"
        )
        await callback.answer()
        return
    
    await callback.message.edit_text(
        texts.SUPPORT_INFO,
        reply_markup=get_support_keyboard(db_user.language),
        parse_mode="HTML"
    )
    await callback.answer()






def register_handlers(dp: Dispatcher):
    """Регистрирует обработчики поддержки"""
    
    # Основные обработчики
    dp.callback_query.register(
        show_support_info,
        F.data == "menu_support"
    )
    