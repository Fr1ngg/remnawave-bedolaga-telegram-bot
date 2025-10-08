import logging
from aiogram import Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.states import AdminStates
from app.database.models import User
from app.keyboards.admin import get_admin_subscriptions_keyboard
from app.localization.texts import get_texts
from app.database.crud.subscription import (
    get_expiring_subscriptions, get_subscriptions_statistics, get_expired_subscriptions,
    get_all_subscriptions
)
from app.services.subscription_service import SubscriptionService
from app.utils.decorators import admin_required, error_handler
from app.utils.formatters import format_datetime, format_time_ago


def get_country_flag(country_name: str) -> str:
    flags = {
        'USA': '🇺🇸', 'United States': '🇺🇸', 'US': '🇺🇸',
        'Germany': '🇩🇪', 'DE': '🇩🇪', 'Deutschland': '🇩🇪',
        'Netherlands': '🇳🇱', 'NL': '🇳🇱', 'Holland': '🇳🇱',
        'United Kingdom': '🇬🇧', 'UK': '🇬🇧', 'GB': '🇬🇧',
        'Japan': '🇯🇵', 'JP': '🇯🇵',
        'France': '🇫🇷', 'FR': '🇫🇷',
        'Canada': '🇨🇦', 'CA': '🇨🇦',
        'Russia': '🇷🇺', 'RU': '🇷🇺',
        'Singapore': '🇸🇬', 'SG': '🇸🇬',
    }
    return flags.get(country_name, '🌍')


async def get_users_by_countries(db: AsyncSession) -> dict:
    try:
        result = await db.execute(
            select(User.preferred_location, func.count(User.id))
            .where(User.preferred_location.isnot(None))
            .group_by(User.preferred_location)
        )
        
        stats = {}
        for location, count in result.fetchall():
            if location:
                stats[location] = count
        
        return stats
    except Exception as e:
        logger.error(f"Ошибка получения статистики по странам: {e}")
        return {}

logger = logging.getLogger(__name__)


@admin_required
@error_handler
async def show_subscriptions_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    stats = await get_subscriptions_statistics(db)
    
    text = f"""
📱 <b>Управление подписками</b>

📊 <b>Статистика:</b>
- Всего: {stats['total_subscriptions']}
- Активных: {stats['active_subscriptions']}
- Платных: {stats['paid_subscriptions']}
- Триальных: {stats['trial_subscriptions']}

📈 <b>Продажи:</b>
- Сегодня: {stats['purchased_today']}
- За неделю: {stats['purchased_week']}
- За месяц: {stats['purchased_month']}

Выберите действие:
"""
    
    keyboard = [
        [
            types.InlineKeyboardButton(text="📋 Список подписок", callback_data="admin_subs_list"),
            types.InlineKeyboardButton(text="⏰ Истекающие", callback_data="admin_subs_expiring")
        ],
        [
            types.InlineKeyboardButton(text="📊 Статистика", callback_data="admin_subs_stats"),
            types.InlineKeyboardButton(text="🌍 География", callback_data="admin_subs_countries")
        ],
        [
            types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")
        ]
    ]
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@admin_required
@error_handler
async def show_subscriptions_list(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    page: int = 1
):
    
    subscriptions, total_count = await get_all_subscriptions(db, page=page, limit=10)
    total_pages = (total_count + 9) // 10 
    
    if not subscriptions:
        text = "📱 <b>Список подписок</b>\n\n❌ Подписки не найдены."
    else:
        text = f"📱 <b>Список подписок</b>\n\n"
        text += f"📊 Всего: {total_count} | Страница: {page}/{total_pages}\n\n"
        
        for i, sub in enumerate(subscriptions, 1 + (page - 1) * 10):
            user_info = f"ID{sub.user.telegram_id}" if sub.user else "Неизвестно"
            sub_type = "🎁" if sub.is_trial else "💎"
            status = "✅ Активна" if sub.is_active else "❌ Неактивна"
            
            text += f"{i}. {sub_type} {user_info}\n"
            text += f"   {status} | До: {format_datetime(sub.end_date)}\n"
            if sub.device_limit > 0:
                text += f"   📱 Устройств: {sub.device_limit}\n"
            text += "\n"
    
    keyboard = []
    
    if total_pages > 1:
        nav_row = []
        if page > 1:
            nav_row.append(types.InlineKeyboardButton(
                text="⬅️", callback_data=f"admin_subs_list_page_{page-1}"
            ))
        
        nav_row.append(types.InlineKeyboardButton(
            text=f"{page}/{total_pages}", callback_data="current_page"
        ))
        
        if page < total_pages:
            nav_row.append(types.InlineKeyboardButton(
                text="➡️", callback_data=f"admin_subs_list_page_{page+1}"
            ))
        
        keyboard.append(nav_row)
    
    keyboard.extend([
        [types.InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_subs_list")],
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_subscriptions")]
    ])
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@admin_required
@error_handler
async def show_expiring_subscriptions(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    expiring_3d = await get_expiring_subscriptions(db, 3)
    expiring_1d = await get_expiring_subscriptions(db, 1)
    expired = await get_expired_subscriptions(db)
    
    text = f"""
⏰ <b>Истекающие подписки</b>

📊 <b>Статистика:</b>
- Истекают через 3 дня: {len(expiring_3d)}
- Истекают завтра: {len(expiring_1d)}
- Уже истекли: {len(expired)}

<b>Истекают через 3 дня:</b>
"""
    
    for sub in expiring_3d[:5]:
        user_info = f"ID{sub.user.telegram_id}" if sub.user else "Неизвестно"
        sub_type = "🎁" if sub.is_trial else "💎"
        text += f"{sub_type} {user_info} - {format_datetime(sub.end_date)}\n"
    
    if len(expiring_3d) > 5:
        text += f"... и еще {len(expiring_3d) - 5}\n"
    
    text += f"\n<b>Истекают завтра:</b>\n"
    for sub in expiring_1d[:5]:
        user_info = f"ID{sub.user.telegram_id}" if sub.user else "Неизвестно"
        sub_type = "🎁" if sub.is_trial else "💎"
        text += f"{sub_type} {user_info} - {format_datetime(sub.end_date)}\n"
    
    if len(expiring_1d) > 5:
        text += f"... и еще {len(expiring_1d) - 5}\n"
    
    keyboard = [
        [types.InlineKeyboardButton(text="📨 Отправить напоминания", callback_data="admin_send_expiry_reminders")],
        [types.InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_subs_expiring")],
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_subscriptions")]
    ]
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@admin_required
@error_handler
async def show_subscriptions_stats(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    stats = await get_subscriptions_statistics(db)
    
    expiring_3d = await get_expiring_subscriptions(db, 3)
    expiring_7d = await get_expiring_subscriptions(db, 7)
    expired = await get_expired_subscriptions(db)
    
    text = f"""
📊 <b>Детальная статистика подписок</b>

<b>📱 Общая информация:</b>
• Всего подписок: {stats['total_subscriptions']}
• Активных: {stats['active_subscriptions']}
• Неактивных: {stats['total_subscriptions'] - stats['active_subscriptions']}

<b>💎 По типам:</b>
• Платных: {stats['paid_subscriptions']}
• Триальных: {stats['trial_subscriptions']}

<b>📈 Продажи:</b>
• Сегодня: {stats['purchased_today']}
• За неделю: {stats['purchased_week']}  
• За месяц: {stats['purchased_month']}

<b>⏰ Истечение:</b>
• Истекают через 3 дня: {len(expiring_3d)}
• Истекают через 7 дней: {len(expiring_7d)}
• Уже истекли: {len(expired)}

<b>💰 Конверсия:</b>
• Из триала в платную: {stats.get('trial_to_paid_conversion', 0)}%
• Продлений: {stats.get('renewals_count', 0)}
"""
    
    keyboard = [
       # [
       #     types.InlineKeyboardButton(text="📊 Экспорт данных", callback_data="admin_subs_export"),
       #     types.InlineKeyboardButton(text="📈 Графики", callback_data="admin_subs_charts")
       # ],
       # [types.InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_subs_stats")],
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_subscriptions")]
    ]
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@admin_required
@error_handler
async def show_countries_management(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    try:
        from app.services.remnawave_service import RemnaWaveService
        remnawave_service = RemnaWaveService()
        
        nodes_data = await remnawave_service.get_all_nodes()
        squads_data = await remnawave_service.get_all_squads() 
        
        text = "🌍 <b>Управление странами</b>\n\n"
        
        if nodes_data:
            text += "<b>Доступные серверы:</b>\n"
            countries = {}
            
            for node in nodes_data:
                country_code = node.get('country_code', 'XX')  
                country_name = country_code
                
                if country_name not in countries:
                    countries[country_name] = []
                countries[country_name].append(node)
            
            for country, nodes in countries.items():
                active_nodes = len([n for n in nodes if n.get('is_connected') and n.get('is_node_online')])
                total_nodes = len(nodes)
                
                country_flag = get_country_flag(country)
                text += f"{country_flag} {country}: {active_nodes}/{total_nodes} серверов\n"
                
                total_users_online = sum(n.get('users_online', 0) or 0 for n in nodes)
                if total_users_online > 0:
                    text += f"   👥 Пользователей онлайн: {total_users_online}\n"
        else:
            text += "❌ Не удалось загрузить данные о серверах\n"
        
        if squads_data:
            text += f"\n<b>Всего сквадов:</b> {len(squads_data)}\n"
            
            total_members = sum(squad.get('members_count', 0) for squad in squads_data)
            text += f"<b>Участников в сквадах:</b> {total_members}\n"
            
            text += "\n<b>Сквады:</b>\n"
            for squad in squads_data[:5]: 
                name = squad.get('name', 'Неизвестно')
                members = squad.get('members_count', 0)
                inbounds = squad.get('inbounds_count', 0)
                text += f"• {name}: {members} участников, {inbounds} inbound(s)\n"
            
            if len(squads_data) > 5:
                text += f"... и еще {len(squads_data) - 5} сквадов\n"
        
        user_stats = await get_users_by_countries(db)
        if user_stats:
            text += "\n<b>Пользователи по регионам:</b>\n"
            for country, count in user_stats.items():
                country_flag = get_country_flag(country)
                text += f"{country_flag} {country}: {count} пользователей\n"
        
    except Exception as e:
        logger.error(f"Ошибка получения данных о странах: {e}")
        text = f"""
🌍 <b>Управление странами</b>

❌ <b>Ошибка загрузки данных</b>
Не удалось получить информацию о серверах.

Проверьте подключение к RemnaWave API.

<b>Детали ошибки:</b> {str(e)}
"""
    
    keyboard = [
        [
            types.InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_subs_countries")
        ],
        [
            types.InlineKeyboardButton(text="📊 Статистика нод", callback_data="admin_rw_nodes"),
            types.InlineKeyboardButton(text="🔧 Сквады", callback_data="admin_rw_squads")
        ],
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_subscriptions")]
    ]
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@admin_required
@error_handler
async def send_expiry_reminders(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    await callback.message.edit_text(
        "📨 Отправка напоминаний...\n\nПодождите, это может занять время.",
        reply_markup=None
    )
    
    expiring_subs = await get_expiring_subscriptions(db, 1)
    sent_count = 0
    
    for subscription in expiring_subs:
        if subscription.user:
            try:
                user = subscription.user
                days_left = max(1, subscription.days_left)
                
                reminder_text = f"""
⚠️ <b>Подписка истекает!</b>

Ваша подписка истекает через {days_left} день(а).

Не забудьте продлить подписку, чтобы не потерять доступ к серверам.

💎 Продлить подписку можно в главном меню.
"""
                
                await callback.bot.send_message(
                    chat_id=user.telegram_id,
                    text=reminder_text
                )
                sent_count += 1
                
            except Exception as e:
                logger.error(f"Ошибка отправки напоминания пользователю {subscription.user_id}: {e}")
    
    await callback.message.edit_text(
        f"✅ Напоминания отправлены: {sent_count} из {len(expiring_subs)}",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_subs_expiring")]
        ])
    )
    await callback.answer()


@admin_required
@error_handler  
async def handle_subscriptions_pagination(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    page = int(callback.data.split('_')[-1])
    await show_subscriptions_list(callback, db_user, db, page)


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_subscriptions_menu, F.data == "admin_subscriptions")
    dp.callback_query.register(show_subscriptions_list, F.data == "admin_subs_list")
    dp.callback_query.register(show_expiring_subscriptions, F.data == "admin_subs_expiring")
    dp.callback_query.register(show_subscriptions_stats, F.data == "admin_subs_stats")
    dp.callback_query.register(show_countries_management, F.data == "admin_subs_countries")
    dp.callback_query.register(send_expiry_reminders, F.data == "admin_send_expiry_reminders")
    
    dp.callback_query.register(
        handle_subscriptions_pagination, 
        F.data.startswith("admin_subs_list_page_")
    )
