import logging
from aiogram import Dispatcher, types, F
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.keyboards.admin import (
    get_admin_main_keyboard,
    get_admin_users_submenu_keyboard,
    get_admin_promo_submenu_keyboard,
    get_admin_communications_submenu_keyboard,
    get_admin_settings_submenu_keyboard,
    get_admin_system_submenu_keyboard
)
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
    
    admin_text = texts.ADMIN_PANEL
    try:
        from app.services.remnawave_service import RemnaWaveService
        remnawave_service = RemnaWaveService()
        stats = await remnawave_service.get_system_statistics()
        users_online = stats.get("system", {}).get("users_online", 0)
        admin_text = admin_text.replace(
            "\n\nВыберите раздел для управления:",
            f"\n\n- 🟢 Онлайн сейчас: {users_online}\n\nВыберите раздел для управления:"
        )
    except Exception as e:
        logger.error(f"Не удалось получить статистику Remnawave для админ-панели: {e}")
    
    await callback.message.edit_text(
        admin_text,
        reply_markup=get_admin_main_keyboard(db_user.language)
    )
    await callback.answer()


@admin_required
@error_handler
async def show_users_submenu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    texts = get_texts(db_user.language)
    
    await callback.message.edit_text(
        "👥 **Управление пользователями и подписками**\n\n"
        "Выберите нужный раздел:",
        reply_markup=get_admin_users_submenu_keyboard(db_user.language),
        parse_mode="Markdown"
    )
    await callback.answer()


@admin_required
@error_handler
async def show_promo_submenu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    texts = get_texts(db_user.language)
    
    await callback.message.edit_text(
        "💰 **Промокоды и статистика**\n\n"
        "Выберите нужный раздел:",
        reply_markup=get_admin_promo_submenu_keyboard(db_user.language),
        parse_mode="Markdown"
    )
    await callback.answer()


@admin_required
@error_handler
async def show_communications_submenu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    texts = get_texts(db_user.language)
    
    await callback.message.edit_text(
        "📨 **Коммуникации**\n\n"
        "Управление рассылками и текстами интерфейса:",
        reply_markup=get_admin_communications_submenu_keyboard(db_user.language),
        parse_mode="Markdown"
    )
    await callback.answer()


@admin_required
@error_handler
async def show_settings_submenu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    texts = get_texts(db_user.language)
    
    await callback.message.edit_text(
        "⚙️ **Настройки системы**\n\n"
        "Управление Remnawave, мониторингом и другими настройками:",
        reply_markup=get_admin_settings_submenu_keyboard(db_user.language),
        parse_mode="Markdown"
    )
    await callback.answer()


@admin_required
@error_handler
async def show_system_submenu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    texts = get_texts(db_user.language)
    
    await callback.message.edit_text(
        "🛠️ **Системные функции**\n\n"
        "Обновления, резервные копии и системные операции:",
        reply_markup=get_admin_system_submenu_keyboard(db_user.language),
        parse_mode="Markdown"
    )
    await callback.answer()


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(
        show_admin_panel,
        F.data == "admin_panel"
    )
    
    dp.callback_query.register(
        show_users_submenu,
        F.data == "admin_submenu_users"
    )
    
    dp.callback_query.register(
        show_promo_submenu,
        F.data == "admin_submenu_promo"
    )
    
    dp.callback_query.register(
        show_communications_submenu,
        F.data == "admin_submenu_communications"
    )
    
    dp.callback_query.register(
        show_settings_submenu,
        F.data == "admin_submenu_settings"
    )
    
    dp.callback_query.register(
        show_system_submenu,
        F.data == "admin_submenu_system"
    )
