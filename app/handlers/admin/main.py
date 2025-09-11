import logging
from aiogram import Dispatcher, types, F
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.keyboards.admin import get_admin_main_keyboard
from app.localization.texts import get_texts
from app.utils.decorators import admin_required, error_handler

logger = logging.getLogger(__name__)


@admin_required
@error_handler
async def show_admin_panel(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    texts = get_texts(db_user.language)
    
    # Получаем статистику онлайн пользователей
    try:
        from app.services.remnawave_service import RemnaWaveService
        remnawave_service = RemnaWaveService()
        stats = await remnawave_service.get_system_statistics()
        
        if "error" not in stats:
            users_online = stats.get("system", {}).get("users_online", 0)
            admin_text = f"""⚙️ <b>Административная панель</b>
- 🟢 Онлайн сейчас: {users_online}

Выберите раздел для управления:"""
        else:
            admin_text = texts.ADMIN_PANEL
    except Exception as e:
        logger.error(f"Ошибка получения статистики онлайн пользователей: {e}")
        admin_text = texts.ADMIN_PANEL
    
    await callback.message.edit_text(
        admin_text,
        reply_markup=get_admin_main_keyboard(db_user.language)
    )
    await callback.answer()


def register_handlers(dp: Dispatcher):
    
    dp.callback_query.register(
        show_admin_panel,
        F.data == "admin_panel"
    )