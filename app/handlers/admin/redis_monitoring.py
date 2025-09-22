import logging
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from app.services.redis_health_check import redis_health_checker
from app.services.redis_service import redis_service
from app.utils.decorators import admin_required

logger = logging.getLogger(__name__)

router = Router()


@router.message(Command("redis_status"))
@admin_required
async def redis_status_handler(message: types.Message):
    """Показать статус Redis"""
    try:
        # Получаем информацию о здоровье Redis
        health_info = await redis_health_checker.check_health()
        
        # Формируем сообщение
        status_emoji = "✅" if health_info.get('is_healthy', False) else "❌"
        
        text = f"{status_emoji} **Статус Redis**\n\n"
        
        # Основная информация
        text += f"**Состояние:** {'Здоров' if health_info.get('is_healthy') else 'Проблемы'}\n"
        text += f"**Ping:** {'✅' if health_info.get('ping_ok') else '❌'}\n"
        text += f"**Клиенты:** {health_info.get('connected_clients', 0)}\n"
        text += f"**Память:** {health_info.get('memory_usage_human', '0B')}\n"
        text += f"**Пик памяти:** {health_info.get('memory_peak_human', '0B')}\n"
        text += f"**Операций/сек:** {health_info.get('ops_per_sec', 0)}\n"
        text += f"**Hit Rate:** {health_info.get('hit_rate', 0):.1f}%\n"
        text += f"**Неудач подряд:** {health_info.get('consecutive_failures', 0)}\n"
        
        # Время последней проверки
        if health_info.get('last_check'):
            text += f"**Последняя проверка:** {health_info.get('last_check')}\n"
        
        # Предупреждения
        warnings = health_info.get('warnings', [])
        if warnings:
            text += f"\n**⚠️ Предупреждения:**\n"
            for warning in warnings:
                text += f"• {warning}\n"
        
        # Ошибки
        if health_info.get('error'):
            text += f"\n**❌ Ошибка:** {health_info.get('error')}\n"
        
        # Кнопки
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Обновить", callback_data="redis_refresh"),
                InlineKeyboardButton(text="📊 Детали", callback_data="redis_details")
            ],
            [
                InlineKeyboardButton(text="🧹 Очистить кеш", callback_data="redis_clear_cache"),
                InlineKeyboardButton(text="📈 Статистика", callback_data="redis_stats")
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад в настройки", callback_data="admin_submenu_settings")
            ]
        ])
        
        await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Ошибка получения статуса Redis: {e}")
        await message.answer(f"❌ Ошибка получения статуса Redis: {e}")


@router.callback_query(lambda c: c.data == "redis_status")
@admin_required
async def redis_status_callback(callback: types.CallbackQuery):
    """Показать статус Redis через админ-панель"""
    try:
        # Получаем информацию о здоровье Redis
        health_info = await redis_health_checker.check_health()
        
        # Формируем сообщение
        status_emoji = "✅" if health_info.get('is_healthy', False) else "❌"
        
        text = f"{status_emoji} **Статус Redis**\n\n"
        
        # Основная информация
        text += f"**Состояние:** {'Здоров' if health_info.get('is_healthy') else 'Проблемы'}\n"
        text += f"**Ping:** {'✅' if health_info.get('ping_ok') else '❌'}\n"
        text += f"**Клиенты:** {health_info.get('connected_clients', 0)}\n"
        text += f"**Память:** {health_info.get('memory_usage_human', '0B')}\n"
        text += f"**Пик памяти:** {health_info.get('memory_peak_human', '0B')}\n"
        text += f"**Операций/сек:** {health_info.get('ops_per_sec', 0)}\n"
        text += f"**Hit Rate:** {health_info.get('hit_rate', 0):.1f}%\n"
        text += f"**Неудач подряд:** {health_info.get('consecutive_failures', 0)}\n"
        
        # Время последней проверки
        if health_info.get('last_check'):
            text += f"**Последняя проверка:** {health_info.get('last_check')}\n"
        
        # Предупреждения
        warnings = health_info.get('warnings', [])
        if warnings:
            text += f"\n**⚠️ Предупреждения:**\n"
            for warning in warnings:
                text += f"• {warning}\n"
        
        # Ошибки
        if health_info.get('error'):
            text += f"\n**❌ Ошибка:** {health_info.get('error')}\n"
        
        # Кнопки
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Обновить", callback_data="redis_refresh"),
                InlineKeyboardButton(text="📊 Детали", callback_data="redis_details")
            ],
            [
                InlineKeyboardButton(text="🧹 Очистить кеш", callback_data="redis_clear_cache"),
                InlineKeyboardButton(text="📈 Статистика", callback_data="redis_stats")
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад в настройки", callback_data="admin_submenu_settings")
            ]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка получения статуса Redis: {e}")
        await callback.message.edit_text(f"❌ Ошибка получения статуса Redis: {e}")
        await callback.answer()


@router.callback_query(lambda c: c.data == "redis_refresh")
@admin_required
async def redis_refresh_callback(callback: types.CallbackQuery):
    """Обновить статус Redis"""
    await callback.answer("Обновление...")
    
    # Получаем новую информацию
    health_info = await redis_health_checker.check_health()
    
    status_emoji = "✅" if health_info.get('is_healthy', False) else "❌"
    
    text = f"{status_emoji} **Статус Redis** (обновлено)\n\n"
    text += f"**Состояние:** {'Здоров' if health_info.get('is_healthy') else 'Проблемы'}\n"
    text += f"**Ping:** {'✅' if health_info.get('ping_ok') else '❌'}\n"
    text += f"**Клиенты:** {health_info.get('connected_clients', 0)}\n"
    text += f"**Память:** {health_info.get('memory_usage_human', '0B')}\n"
    text += f"**Операций/сек:** {health_info.get('ops_per_sec', 0)}\n"
    text += f"**Hit Rate:** {health_info.get('hit_rate', 0):.1f}%\n"
    
    # Кнопки
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Обновить", callback_data="redis_refresh"),
                InlineKeyboardButton(text="📊 Детали", callback_data="redis_details")
            ],
            [
                InlineKeyboardButton(text="🧹 Очистить кеш", callback_data="redis_clear_cache"),
                InlineKeyboardButton(text="📈 Статистика", callback_data="redis_stats")
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад в настройки", callback_data="admin_submenu_settings")
            ]
        ])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")


@router.callback_query(lambda c: c.data == "redis_details")
@admin_required
async def redis_details_callback(callback: types.CallbackQuery):
    """Детальная информация о Redis"""
    await callback.answer()
    
    try:
        # Получаем детальную статистику
        stats = await redis_service.get_stats()
        health_info = await redis_health_checker.check_health()
        
        text = "📊 **Детальная информация Redis**\n\n"
        
        # Общая информация
        text += "**Общее:**\n"
        text += f"• Подключенных клиентов: {stats.get('connected_clients', 0)}\n"
        text += f"• Использовано памяти: {stats.get('used_memory', '0B')}\n"
        text += f"• Пик памяти: {stats.get('used_memory_peak', '0B')}\n"
        text += f"• Операций в секунду: {stats.get('instantaneous_ops_per_sec', 0)}\n\n"
        
        # Кеш статистика
        text += "**Кеш:**\n"
        text += f"• Попаданий: {stats.get('keyspace_hits', 0)}\n"
        text += f"• Промахов: {stats.get('keyspace_misses', 0)}\n"
        text += f"• Hit Rate: {health_info.get('hit_rate', 0):.1f}%\n\n"
        
        # Производительность
        text += "**Производительность:**\n"
        text += f"• Всего команд: {stats.get('total_commands_processed', 0)}\n"
        text += f"• Время проверки: {health_info.get('check_duration_ms', 0):.1f}ms\n"
        text += f"• Неудач подряд: {health_info.get('consecutive_failures', 0)}\n"
        
        # Предупреждения
        warnings = health_info.get('warnings', [])
        if warnings:
            text += f"\n**⚠️ Предупреждения:**\n"
            for warning in warnings:
                text += f"• {warning}\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="redis_status")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Ошибка получения детальной информации Redis: {e}")
        await callback.message.edit_text(f"❌ Ошибка: {e}")


@router.callback_query(lambda c: c.data == "redis_clear_cache")
@admin_required
async def redis_clear_cache_callback(callback: types.CallbackQuery):
    """Очистка кеша Redis"""
    await callback.answer("Очистка кеша...")
    
    try:
        # Очищаем кеш
        result = await redis_service.flushall()
        
        if result:
            text = "✅ **Кеш Redis очищен**\n\n"
            text += "Все данные из Redis были удалены."
        else:
            text = "❌ **Ошибка очистки кеша**\n\n"
            text += "Не удалось очистить кеш Redis."
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="redis_status")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Ошибка очистки кеша Redis: {e}")
        await callback.message.edit_text(f"❌ Ошибка очистки кеша: {e}")


@router.callback_query(lambda c: c.data == "redis_stats")
@admin_required
async def redis_stats_callback(callback: types.CallbackQuery):
    """Статистика Redis"""
    await callback.answer()
    
    try:
        # Получаем статистику
        stats = await redis_service.get_stats()
        
        text = "📈 **Статистика Redis**\n\n"
        
        # Основные метрики
        text += "**Память:**\n"
        text += f"• Использовано: {stats.get('used_memory', '0B')}\n"
        text += f"• Пик: {stats.get('used_memory_peak', '0B')}\n\n"
        
        text += "**Подключения:**\n"
        text += f"• Клиентов: {stats.get('connected_clients', 0)}\n\n"
        
        text += "**Производительность:**\n"
        text += f"• Операций/сек: {stats.get('instantaneous_ops_per_sec', 0)}\n"
        text += f"• Всего команд: {stats.get('total_commands_processed', 0)}\n\n"
        
        text += "**Кеш:**\n"
        hits = stats.get('keyspace_hits', 0)
        misses = stats.get('keyspace_misses', 0)
        total = hits + misses
        hit_rate = (hits / total * 100) if total > 0 else 0
        
        text += f"• Попаданий: {hits}\n"
        text += f"• Промахов: {misses}\n"
        text += f"• Hit Rate: {hit_rate:.1f}%\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="redis_status")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Ошибка получения статистики Redis: {e}")
        await callback.message.edit_text(f"❌ Ошибка получения статистики: {e}")


@router.callback_query(lambda c: c.data == "admin_submenu_settings")
@admin_required
async def back_to_settings_callback(callback: types.CallbackQuery):
    """Возврат в настройки"""
    from app.handlers.admin.main import show_settings_submenu
    await show_settings_submenu(callback, None, None)


def register_handlers(dp):
    """Регистрация обработчиков"""
    dp.include_router(router)
