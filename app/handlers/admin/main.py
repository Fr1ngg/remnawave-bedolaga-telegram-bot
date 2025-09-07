import logging
from aiogram import Dispatcher, types, F
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.keyboards.admin import get_admin_main_keyboard
from app.localization.texts import get_texts
from app.utils.decorators import admin_required, error_handler
from app.services.version_service import version_service

logger = logging.getLogger(__name__)


@admin_required
@error_handler
async def show_admin_panel(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    texts = get_texts(db_user.language)
    
    # Добавляем информацию о версии
    version_info = version_service.get_version_info()
    version_display = version_service.get_version_display()
    
    admin_text = texts.ADMIN_PANEL
    admin_text += f"\n\n🤖 <b>Версия:</b> {version_display}"
    
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