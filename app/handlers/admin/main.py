import logging
from aiogram import Dispatcher, types, F
from aiogram.filters import Command
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
from app.database.crud.rules import clear_all_rules, get_rules_statistics
from app.localization.texts import clear_rules_cache

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
        system_stats = stats.get("system", {})
        users_online = system_stats.get("users_online", 0)
        users_today = system_stats.get("users_last_day", 0)
        users_week = system_stats.get("users_last_week", 0)
        admin_text = admin_text.replace(
            "\n\nВыберите раздел для управления:",
            (
                f"\n\n- 🟢 Онлайн сейчас: {users_online}"
                f"\n- 📅 Онлайн сегодня: {users_today}"
                f"\n- 🗓️ На этой неделе: {users_week}"
                "\n\nВыберите раздел для управления:"
            ),
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



@admin_required
@error_handler
async def clear_rules_command(
    message: types.Message,
    db_user: User,
    db: AsyncSession
):
    try:
        stats = await get_rules_statistics(db)
        
        if stats['total_active'] == 0:
            await message.reply(
                "ℹ️ <b>Правила уже очищены</b>\n\n"
                "В системе нет активных правил. Используются стандартные правила по умолчанию."
            )
            return
        
        success = await clear_all_rules(db, db_user.language)
        
        if success:
            clear_rules_cache()
            
            await message.reply(
                f"✅ <b>Правила успешно очищены!</b>\n\n"
                f"📊 <b>Статистика:</b>\n"
                f"• Очищено правил: {stats['total_active']}\n"
                f"• Язык: {db_user.language}\n"
                f"• Выполнил: {db_user.full_name}\n\n"
                f"Теперь используются стандартные правила по умолчанию."
            )
            
            logger.info(f"Правила очищены командой администратором {db_user.telegram_id} ({db_user.full_name})")
        else:
            await message.reply(
                "⚠️ <b>Нет правил для очистки</b>\n\n"
                "Активные правила не найдены."
            )
            
    except Exception as e:
        logger.error(f"Ошибка при очистке правил командой: {e}")
        await message.reply(
            "❌ <b>Ошибка при очистке правил</b>\n\n"
            f"Произошла ошибка: {str(e)}\n"
            "Попробуйте через админ-панель или повторите позже."
        )


@admin_required
@error_handler
async def rules_stats_command(
    message: types.Message,
    db_user: User,
    db: AsyncSession
):
    try:
        stats = await get_rules_statistics(db)
        
        if 'error' in stats:
            await message.reply(f"❌ Ошибка получения статистики: {stats['error']}")
            return
        
        text = f"📊 <b>Статистика правил сервиса</b>\n\n"
        text += f"📋 <b>Общая информация:</b>\n"
        text += f"• Активных правил: {stats['total_active']}\n"
        text += f"• Всего в истории: {stats['total_all_time']}\n"
        text += f"• Поддерживаемых языков: {stats['total_languages']}\n\n"
        
        if stats['languages']:
            text += f"🌐 <b>По языкам:</b>\n"
            for lang, lang_stats in stats['languages'].items():
                text += f"• <code>{lang}</code>: {lang_stats['active_count']} правил, "
                text += f"{lang_stats['content_length']} символов\n"
                if lang_stats['last_updated']:
                    text += f"  Обновлено: {lang_stats['last_updated'].strftime('%d.%m.%Y %H:%M')}\n"
        else:
            text += "ℹ️ Активных правил нет - используются правила по умолчанию"
        
        await message.reply(text)
        
    except Exception as e:
        logger.error(f"Ошибка при получении статистики правил: {e}")
        await message.reply(
            f"❌ <b>Ошибка получения статистики</b>\n\n"
            f"Произошла ошибка: {str(e)}"
        )


@admin_required
@error_handler
async def admin_commands_help(
    message: types.Message,
    db_user: User,
    db: AsyncSession
):
    help_text = """
🔧 <b>Доступные админские команды:</b>

<b>📋 Управление правилами:</b>
• <code>/clear_rules</code> - очистить все правила
• <code>/rules_stats</code> - статистика правил

<b>ℹ️ Справка:</b>
• <code>/admin_help</code> - это сообщение

<b>📱 Панель управления:</b>
Используйте кнопку "Админ панель" в главном меню для полного доступа ко всем функциям.

<b>⚠️ Важно:</b>
Все команды логируются и требуют админских прав.
"""
    
    await message.reply(help_text)


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
    
    dp.message.register(
        clear_rules_command,
        Command("clear_rules")
    )
    
    dp.message.register(
        rules_stats_command,
        Command("rules_stats")
    )
    
    dp.message.register(
        admin_commands_help,
        Command("admin_help")
    )