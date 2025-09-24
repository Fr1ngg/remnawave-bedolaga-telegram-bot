from typing import List, Optional
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from app.localization.texts import get_texts


def get_admin_main_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Юзеры/Подписки", callback_data="admin_submenu_users")],
        [InlineKeyboardButton(text="💰 Промокоды/Статистика", callback_data="admin_submenu_promo")],
        [InlineKeyboardButton(text="📨 Сообщения", callback_data="admin_submenu_communications")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="admin_submenu_settings")],
        [InlineKeyboardButton(text="🛠️ Система", callback_data="admin_submenu_system")],
        [InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")]
    ])


def get_admin_users_submenu_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=texts.ADMIN_USERS, callback_data="admin_users"),
            InlineKeyboardButton(text=texts.ADMIN_REFERRALS, callback_data="admin_referrals")
        ],
        [
            InlineKeyboardButton(text=texts.ADMIN_SUBSCRIPTIONS, callback_data="admin_subscriptions")
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")
        ]
    ])


def get_admin_promo_submenu_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=texts.ADMIN_PROMOCODES, callback_data="admin_promocodes"),
            InlineKeyboardButton(text=texts.ADMIN_STATISTICS, callback_data="admin_statistics")
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")
        ]
    ])


def get_admin_communications_submenu_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=texts.ADMIN_MESSAGES, callback_data="admin_messages")
        ],
        [
            InlineKeyboardButton(text="👋 Приветственный текст", callback_data="welcome_text_panel"),
            InlineKeyboardButton(text="📢 Сообщения в меню", callback_data="user_messages_panel")
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")
        ]
    ])


def get_admin_settings_submenu_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=texts.ADMIN_REMNAWAVE, callback_data="admin_remnawave"),
            InlineKeyboardButton(text=texts.ADMIN_MONITORING, callback_data="admin_monitoring")
        ],
        [
            InlineKeyboardButton(text=texts.ADMIN_RULES, callback_data="admin_rules"),
            InlineKeyboardButton(text="🔧 Техработы", callback_data="maintenance_panel")
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")
        ]
    ])


def get_admin_system_submenu_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📄 Обновления", callback_data="admin_updates"),
            InlineKeyboardButton(text="🗄️ Бекапы", callback_data="backup_panel")
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")
        ]
    ])


def get_admin_users_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👥 Все пользователи", callback_data="admin_users_list"),
            InlineKeyboardButton(text="🔍 Поиск", callback_data="admin_users_search")
        ],
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="admin_users_stats"),
            InlineKeyboardButton(text="🗑️ Неактивные", callback_data="admin_users_inactive")
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_submenu_users")
        ]
    ])


def get_admin_subscriptions_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📱 Все подписки", callback_data="admin_subs_list"),
            InlineKeyboardButton(text="⏰ Истекающие", callback_data="admin_subs_expiring")
        ],
        [
            InlineKeyboardButton(text="⚙️ Настройки цен", callback_data="admin_subs_pricing"),
            InlineKeyboardButton(text="🌍 Управление странами", callback_data="admin_subs_countries")
        ],
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="admin_subs_stats")
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_submenu_users")
        ]
    ])


def get_admin_promocodes_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🎫 Все промокоды", callback_data="admin_promo_list"),
            InlineKeyboardButton(text="➕ Создать", callback_data="admin_promo_create")
        ],
        [
            InlineKeyboardButton(text="📊 Общая статистика", callback_data="admin_promo_general_stats")
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_submenu_promo")
        ]
    ])


def get_promocode_management_keyboard(promo_id: int, language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"promo_edit_{promo_id}"),
            InlineKeyboardButton(text="🔄 Статус", callback_data=f"promo_toggle_{promo_id}")
        ],
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data=f"promo_stats_{promo_id}"),
            InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"promo_delete_{promo_id}")
        ],
        [
            InlineKeyboardButton(text="⬅️ К списку", callback_data="admin_promo_list")
        ]
    ])


def get_admin_messages_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📨 Всем пользователям", callback_data="admin_msg_all"),
            InlineKeyboardButton(text="🎯 По подпискам", callback_data="admin_msg_by_sub")
        ],
        [
            InlineKeyboardButton(text="🔍 По критериям", callback_data="admin_msg_custom"),
            InlineKeyboardButton(text="📋 История", callback_data="admin_msg_history")
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_submenu_communications")
        ]
    ])


def get_admin_monitoring_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="▶️ Запустить", callback_data="admin_mon_start"),
            InlineKeyboardButton(text="⏸️ Остановить", callback_data="admin_mon_stop")
        ],
        [
            InlineKeyboardButton(text="📊 Статус", callback_data="admin_mon_status"),
            InlineKeyboardButton(text="📋 Логи", callback_data="admin_mon_logs")
        ],
        [
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="admin_mon_settings")
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_submenu_settings")
        ]
    ])


def get_admin_remnawave_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Системная статистика", callback_data="admin_rw_system"),
            InlineKeyboardButton(text="🖥️ Управление нодами", callback_data="admin_rw_nodes")
        ],
        [
            InlineKeyboardButton(text="🔄 Синхронизация", callback_data="admin_rw_sync"),
            InlineKeyboardButton(text="🌐 Управление сквадами", callback_data="admin_rw_squads")
        ],
        [
            InlineKeyboardButton(text="📈 Трафик", callback_data="admin_rw_traffic")
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_submenu_settings")
        ]
    ])


def get_admin_statistics_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_stats_users"),
            InlineKeyboardButton(text="📱 Подписки", callback_data="admin_stats_subs")
        ],
        [
            InlineKeyboardButton(text="💰 Доходы", callback_data="admin_stats_revenue"),
            InlineKeyboardButton(text="🤝 Партнерка", callback_data="admin_stats_referrals")
        ],
        [
            InlineKeyboardButton(text="📊 Общая сводка", callback_data="admin_stats_summary")
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_submenu_promo")
        ]
    ])


def get_user_management_keyboard(user_id: int, user_status: str, language: str = "ru") -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(text="💰 Баланс", callback_data=f"admin_user_balance_{user_id}"),
            InlineKeyboardButton(text="📱 Подписка", callback_data=f"admin_user_subscription_{user_id}")
        ],
        [
            InlineKeyboardButton(text="⚙️ Настройка", callback_data=f"admin_user_servers_{user_id}"), 
            InlineKeyboardButton(text="📊 Статистика", callback_data=f"admin_user_statistics_{user_id}")
        ],
        [
            InlineKeyboardButton(text="📋 Транзакции", callback_data=f"admin_user_transactions_{user_id}")
        ]
    ]
    
    if user_status == "active":
        keyboard.append([
            InlineKeyboardButton(text="🚫 Заблокировать", callback_data=f"admin_user_block_{user_id}"),
            InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"admin_user_delete_{user_id}")
        ])
    elif user_status == "blocked":
        keyboard.append([
            InlineKeyboardButton(text="✅ Разблокировать", callback_data=f"admin_user_unblock_{user_id}"),
            InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"admin_user_delete_{user_id}")
        ])
    elif user_status == "deleted":
        keyboard.append([
            InlineKeyboardButton(text="❌ Пользователь удален", callback_data="noop")
        ])
    
    keyboard.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_users_list")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_confirmation_keyboard(
    confirm_action: str,
    cancel_action: str = "admin_panel",
    language: str = "ru"
) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=texts.YES, callback_data=confirm_action),
            InlineKeyboardButton(text=texts.NO, callback_data=cancel_action)
        ]
    ])


def get_promocode_type_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💰 Баланс", callback_data="promo_type_balance"),
            InlineKeyboardButton(text="📅 Дни подписки", callback_data="promo_type_days")
        ],
        [
            InlineKeyboardButton(text="🎁 Триал", callback_data="promo_type_trial")
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_promocodes")
        ]
    ])


def get_promocode_list_keyboard(promocodes: list, page: int, total_pages: int, language: str = "ru") -> InlineKeyboardMarkup:
    keyboard = []
    
    for promo in promocodes:
        status_emoji = "✅" if promo.is_active else "❌"
        type_emoji = {"balance": "💰", "subscription_days": "📅", "trial_subscription": "🎁"}.get(promo.type, "🎫")
        
        keyboard.append([
            InlineKeyboardButton(
                text=f"{status_emoji} {type_emoji} {promo.code}",
                callback_data=f"promo_manage_{promo.id}"
            )
        ])
    
    if total_pages > 1:
        pagination_row = []
        
        if page > 1:
            pagination_row.append(
                InlineKeyboardButton(text="⬅️", callback_data=f"admin_promo_list_page_{page - 1}")
            )
        
        pagination_row.append(
            InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="current_page")
        )
        
        if page < total_pages:
            pagination_row.append(
                InlineKeyboardButton(text="➡️", callback_data=f"admin_promo_list_page_{page + 1}")
            )
        
        keyboard.append(pagination_row)
    
    keyboard.extend([
        [InlineKeyboardButton(text="➕ Создать", callback_data="admin_promo_create")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_promocodes")]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_broadcast_target_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👥 Всем", callback_data="broadcast_all"),
            InlineKeyboardButton(text="📱 С подпиской", callback_data="broadcast_active")
        ],
        [
            InlineKeyboardButton(text="🎁 Триал", callback_data="broadcast_trial"),
            InlineKeyboardButton(text="❌ Без подписки", callback_data="broadcast_no_sub")
        ],
        [
            InlineKeyboardButton(text="⏰ Истекающие", callback_data="broadcast_expiring")
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_messages")
        ]
    ])


def get_custom_criteria_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📅 Сегодня", callback_data="criteria_today"),
            InlineKeyboardButton(text="📅 За неделю", callback_data="criteria_week")
        ],
        [
            InlineKeyboardButton(text="📅 За месяц", callback_data="criteria_month"),
            InlineKeyboardButton(text="⚡ Активные сегодня", callback_data="criteria_active_today")
        ],
        [
            InlineKeyboardButton(text="💤 Неактивные 7+ дней", callback_data="criteria_inactive_week"),
            InlineKeyboardButton(text="💤 Неактивные 30+ дней", callback_data="criteria_inactive_month")
        ],
        [
            InlineKeyboardButton(text="🤝 Через рефералов", callback_data="criteria_referrals"),
            InlineKeyboardButton(text="🎫 Использовали промокоды", callback_data="criteria_promocodes")
        ],
        [
            InlineKeyboardButton(text="🎯 Прямая регистрация", callback_data="criteria_direct")
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_messages")
        ]
    ])


def get_broadcast_history_keyboard(page: int, total_pages: int, language: str = "ru") -> InlineKeyboardMarkup:
    keyboard = []
    
    if total_pages > 1:
        pagination_row = []
        
        if page > 1:
            pagination_row.append(
                InlineKeyboardButton(text="⬅️", callback_data=f"admin_msg_history_page_{page - 1}")
            )
        
        pagination_row.append(
            InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="current_page")
        )
        
        if page < total_pages:
            pagination_row.append(
                InlineKeyboardButton(text="➡️", callback_data=f"admin_msg_history_page_{page + 1}")
            )
        
        keyboard.append(pagination_row)
    
    keyboard.extend([
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_msg_history")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_messages")]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_sync_options_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(text="🔄 Полная синхронизация", callback_data="sync_all_users")],
        [InlineKeyboardButton(text="🆕 Только новые", callback_data="sync_new_users")],
        [InlineKeyboardButton(text="📈 Обновить данные", callback_data="sync_update_data")],
        [
            InlineKeyboardButton(text="🔍 Валидация", callback_data="sync_validate"),
            InlineKeyboardButton(text="🧹 Очистка", callback_data="sync_cleanup")
        ],
        [InlineKeyboardButton(text="💡 Рекомендации", callback_data="sync_recommendations")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_remnawave")]
    ]
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_sync_confirmation_keyboard(sync_type: str, language: str = "ru") -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm_{sync_type}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_rw_sync")]
    ]
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_sync_result_keyboard(sync_type: str, has_errors: bool = False, language: str = "ru") -> InlineKeyboardMarkup:
    keyboard = []
    
    if has_errors:
        keyboard.append([
            InlineKeyboardButton(text="🔄 Повторить", callback_data=f"sync_{sync_type}")
        ])
    
    if sync_type != "all_users":
        keyboard.append([
            InlineKeyboardButton(text="🔄 Полная синхронизация", callback_data="sync_all_users")
        ])
    
    keyboard.extend([
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="admin_rw_system"),
            InlineKeyboardButton(text="🔍 Валидация", callback_data="sync_validate")
        ],
        [InlineKeyboardButton(text="⬅️ К синхронизации", callback_data="admin_rw_sync")],
        [InlineKeyboardButton(text="🏠 В главное меню", callback_data="admin_remnawave")]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)



def get_period_selection_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📅 Сегодня", callback_data="period_today"),
            InlineKeyboardButton(text="📅 Вчера", callback_data="period_yesterday")
        ],
        [
            InlineKeyboardButton(text="📅 Неделя", callback_data="period_week"),
            InlineKeyboardButton(text="📅 Месяц", callback_data="period_month")
        ],
        [
            InlineKeyboardButton(text="📅 Все время", callback_data="period_all")
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_statistics")
        ]
    ])


def get_node_management_keyboard(node_uuid: str, language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="▶️ Включить", callback_data=f"node_enable_{node_uuid}"),
            InlineKeyboardButton(text="⏸️ Отключить", callback_data=f"node_disable_{node_uuid}")
        ],
        [
            InlineKeyboardButton(text="🔄 Перезагрузить", callback_data=f"node_restart_{node_uuid}"),
            InlineKeyboardButton(text="📊 Статистика", callback_data=f"node_stats_{node_uuid}")
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_rw_nodes")
        ]
    ])

def get_squad_management_keyboard(squad_uuid: str, language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👥 Добавить всех пользователей", callback_data=f"squad_add_users_{squad_uuid}"),
        ],
        [
            InlineKeyboardButton(text="❌ Удалить всех пользователей", callback_data=f"squad_remove_users_{squad_uuid}"),
        ],
        [
            InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"squad_edit_{squad_uuid}"),
            InlineKeyboardButton(text="🗑️ Удалить сквад", callback_data=f"squad_delete_{squad_uuid}")
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_rw_squads")
        ]
    ])

def get_squad_edit_keyboard(squad_uuid: str, language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔧 Изменить инбаунды", callback_data=f"squad_edit_inbounds_{squad_uuid}"),
        ],
        [
            InlineKeyboardButton(text="✏️ Переименовать", callback_data=f"squad_rename_{squad_uuid}"),
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад к сквадам", callback_data=f"admin_squad_manage_{squad_uuid}")
        ]
    ])

def get_monitoring_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="▶️ Запустить", callback_data="admin_mon_start"),
            InlineKeyboardButton(text="⏹️ Остановить", callback_data="admin_mon_stop")
        ],
        [
            InlineKeyboardButton(text="🔄 Принудительная проверка", callback_data="admin_mon_force_check"),
            InlineKeyboardButton(text="📋 Логи", callback_data="admin_mon_logs")
        ],
        [
            InlineKeyboardButton(text="🧪 Тест уведомлений", callback_data="admin_mon_test_notifications"),
            InlineKeyboardButton(text="📊 Статистика", callback_data="admin_mon_statistics")
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="admin_panel")
        ]
    ])

def get_monitoring_logs_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_mon_logs"),
            InlineKeyboardButton(text="🗑️ Очистить старые", callback_data="admin_mon_clear_logs")
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_monitoring")
        ]
    ])

def get_monitoring_logs_navigation_keyboard(
    current_page: int, 
    total_pages: int,
    has_logs: bool = True
) -> InlineKeyboardMarkup:
    keyboard = []
    
    if total_pages > 1:
        nav_row = []
        
        if current_page > 1:
            nav_row.append(InlineKeyboardButton(
                text="⬅️", 
                callback_data=f"admin_mon_logs_page_{current_page - 1}"
            ))
        
        nav_row.append(InlineKeyboardButton(
            text=f"{current_page}/{total_pages}", 
            callback_data="current_page_info"
        ))
        
        if current_page < total_pages:
            nav_row.append(InlineKeyboardButton(
                text="➡️", 
                callback_data=f"admin_mon_logs_page_{current_page + 1}"
            ))
        
        keyboard.append(nav_row)
    
    management_row = []
    
    if has_logs:
        management_row.extend([
            InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_mon_logs"),
            InlineKeyboardButton(text="🗑️ Очистить", callback_data="admin_mon_clear_logs")
        ])
    else:
        management_row.append(
            InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_mon_logs")
        )
    
    keyboard.append(management_row)
    
    keyboard.append([
        InlineKeyboardButton(text="⬅️ Назад к мониторингу", callback_data="admin_monitoring")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_log_detail_keyboard(log_id: int, current_page: int = 1) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🗑️ Удалить этот лог", 
                callback_data=f"admin_mon_delete_log_{log_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text="⬅️ К списку логов", 
                callback_data=f"admin_mon_logs_page_{current_page}"
            )
        ]
    ])


def get_monitoring_clear_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, очистить", callback_data="admin_mon_clear_logs_confirm"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="admin_mon_logs")
        ],
        [
            InlineKeyboardButton(text="🗑️ Очистить ВСЕ логи", callback_data="admin_mon_clear_all_logs")
        ]
    ])

def get_monitoring_status_keyboard(
    is_running: bool,
    last_check_ago_minutes: int = 0
) -> InlineKeyboardMarkup:
    keyboard = []
    
    control_row = []
    if is_running:
        control_row.extend([
            InlineKeyboardButton(text="⏹️ Остановить", callback_data="admin_mon_stop"),
            InlineKeyboardButton(text="🔄 Перезапустить", callback_data="admin_mon_restart")
        ])
    else:
        control_row.append(
            InlineKeyboardButton(text="▶️ Запустить", callback_data="admin_mon_start")
        )
    
    keyboard.append(control_row)
    
    monitoring_row = []
    
    if not is_running or last_check_ago_minutes > 10:
        monitoring_row.append(
            InlineKeyboardButton(
                text="⚡ Срочная проверка", 
                callback_data="admin_mon_force_check"
            )
        )
    else:
        monitoring_row.append(
            InlineKeyboardButton(
                text="🔄 Проверить сейчас", 
                callback_data="admin_mon_force_check"
            )
        )
    
    keyboard.append(monitoring_row)
    
    info_row = [
        InlineKeyboardButton(text="📋 Логи", callback_data="admin_mon_logs"),
        InlineKeyboardButton(text="📊 Статистика", callback_data="admin_mon_statistics")
    ]
    keyboard.append(info_row)
    
    test_row = [
        InlineKeyboardButton(text="🧪 Тест уведомлений", callback_data="admin_mon_test_notifications")
    ]
    keyboard.append(test_row)
    
    keyboard.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_submenu_settings")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_monitoring_settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⏱️ Интервал проверки", callback_data="admin_mon_set_interval"),
            InlineKeyboardButton(text="🔔 Уведомления", callback_data="admin_mon_toggle_notifications")
        ],
        [
            InlineKeyboardButton(text="💳 Настройки автооплаты", callback_data="admin_mon_autopay_settings"),
            InlineKeyboardButton(text="🧹 Автоочистка логов", callback_data="admin_mon_auto_cleanup")
        ],
        [
            InlineKeyboardButton(text="⬅️ К мониторингу", callback_data="admin_monitoring")
        ]
    ])


def get_log_type_filter_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Успешные", callback_data="admin_mon_logs_filter_success"),
            InlineKeyboardButton(text="❌ Ошибки", callback_data="admin_mon_logs_filter_error")
        ],
        [
            InlineKeyboardButton(text="🔄 Циклы мониторинга", callback_data="admin_mon_logs_filter_cycle"),
            InlineKeyboardButton(text="💳 Автооплаты", callback_data="admin_mon_logs_filter_autopay")
        ],
        [
            InlineKeyboardButton(text="📋 Все логи", callback_data="admin_mon_logs"),
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_monitoring")
        ]
    ])

def get_admin_servers_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📋 Список серверов", callback_data="admin_servers_list"),
            InlineKeyboardButton(text="🔄 Синхронизация", callback_data="admin_servers_sync")
        ],
        [
            InlineKeyboardButton(text="➕ Добавить сервер", callback_data="admin_servers_add"),
            InlineKeyboardButton(text="📊 Статистика", callback_data="admin_servers_stats")
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_subscriptions")
        ]
    ])


def get_server_edit_keyboard(server_id: int, is_available: bool, language: str = "ru") -> InlineKeyboardMarkup:
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✏️ Название", callback_data=f"admin_server_edit_name_{server_id}"),
            InlineKeyboardButton(text="💰 Цена", callback_data=f"admin_server_edit_price_{server_id}")
        ],
        [
            InlineKeyboardButton(text="🌍 Страна", callback_data=f"admin_server_edit_country_{server_id}"),
            InlineKeyboardButton(text="👥 Лимит", callback_data=f"admin_server_edit_limit_{server_id}")
        ],
        [
            InlineKeyboardButton(text="📝 Описание", callback_data=f"admin_server_edit_desc_{server_id}")
        ],
        [
            InlineKeyboardButton(
                text="❌ Отключить" if is_available else "✅ Включить",
                callback_data=f"admin_server_toggle_{server_id}"
            )
        ],
        [
            InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"admin_server_delete_{server_id}"),
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_servers_list")
        ]
    ])


def get_admin_pagination_keyboard(
    current_page: int,
    total_pages: int,
    callback_prefix: str,
    back_callback: str = "admin_panel",
    language: str = "ru"
) -> InlineKeyboardMarkup:
    keyboard = []
    
    if total_pages > 1:
        row = []
        
        if current_page > 1:
            row.append(InlineKeyboardButton(
                text="⬅️",
                callback_data=f"{callback_prefix}_page_{current_page - 1}"
            ))
        
        row.append(InlineKeyboardButton(
            text=f"{current_page}/{total_pages}",
            callback_data="current_page"
        ))
        
        if current_page < total_pages:
            row.append(InlineKeyboardButton(
                text="➡️",
                callback_data=f"{callback_prefix}_page_{current_page + 1}"
            ))
        
        keyboard.append(row)
    
    keyboard.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback)
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_maintenance_keyboard(
    language: str, 
    is_maintenance_active: bool, 
    is_monitoring_active: bool,
    panel_has_issues: bool = False
) -> InlineKeyboardMarkup:
    keyboard = []
    
    if is_maintenance_active:
        keyboard.append([
            InlineKeyboardButton(
                text="🟢 Выключить техработы", 
                callback_data="maintenance_toggle"
            )
        ])
    else:
        keyboard.append([
            InlineKeyboardButton(
                text="🔧 Включить техработы", 
                callback_data="maintenance_toggle"
            )
        ])
    
    if is_monitoring_active:
        keyboard.append([
            InlineKeyboardButton(
                text="⏹️ Остановить мониторинг", 
                callback_data="maintenance_monitoring"
            )
        ])
    else:
        keyboard.append([
            InlineKeyboardButton(
                text="▶️ Запустить мониторинг", 
                callback_data="maintenance_monitoring"
            )
        ])
    
    keyboard.append([
        InlineKeyboardButton(
            text="🔍 Проверить API", 
            callback_data="maintenance_check_api"
        ),
        InlineKeyboardButton(
            text="🌐 Статус панели" + ("⚠️" if panel_has_issues else ""), 
            callback_data="maintenance_check_panel"
        )
    ])
    
    keyboard.append([
        InlineKeyboardButton(
            text="📢 Отправить уведомление", 
            callback_data="maintenance_manual_notify"
        )
    ])
    
    keyboard.append([
        InlineKeyboardButton(
            text="🔄 Обновить", 
            callback_data="maintenance_panel"
        ),
        InlineKeyboardButton(
            text="⬅️ Назад", 
            callback_data="admin_submenu_settings"
        )
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_sync_simplified_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(text="🔄 Полная синхронизация", callback_data="sync_all_users")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_remnawave")]
    ]
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_welcome_text_keyboard(language: str = "ru", is_enabled: bool = True) -> InlineKeyboardMarkup:
    
    toggle_text = "🔴 Отключить" if is_enabled else "🟢 Включить"
    toggle_callback = "toggle_welcome_text"
    
    keyboard = [
        [
            InlineKeyboardButton(text=toggle_text, callback_data=toggle_callback)
        ],
        [
            InlineKeyboardButton(text="📝 Изменить текст", callback_data="edit_welcome_text"),
            InlineKeyboardButton(text="👁️ Показать текущий", callback_data="show_welcome_text")
        ],
        [
            InlineKeyboardButton(text="👁️ Предпросмотр", callback_data="preview_welcome_text"),
            InlineKeyboardButton(text="🔄 Сбросить", callback_data="reset_welcome_text")
        ],
        [
            InlineKeyboardButton(text="🏷️ HTML форматирование", callback_data="show_formatting_help"),
            InlineKeyboardButton(text="💡 Плейсхолдеры", callback_data="show_placeholders_help")
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_submenu_communications")
        ]
    ]
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

DEFAULT_BROADCAST_BUTTONS = ("home",)

BROADCAST_BUTTONS = {
    "balance": {"text": "💰 Пополнить баланс", "callback": "balance_topup"},
    "referrals": {"text": "🤝 Партнерка", "callback": "menu_referrals"},
    "promocode": {"text": "🎫 Промокод", "callback": "menu_promocode"},
    "connect": {"text": "🔗 Подключиться", "callback": "subscription_connect"},
    "subscription": {"text": "📱 Подписка", "callback": "menu_subscription"},
    "support": {"text": "🛠️ Техподдержка", "callback": "menu_support"},
    "home": {"text": "🏠 На главную", "callback": "back_to_menu"},
}

BROADCAST_BUTTON_ROWS: tuple[tuple[str, ...], ...] = (
    ("balance", "referrals"),
    ("promocode", "connect"),
    ("subscription", "support"),
    ("home",),
)

BROADCAST_BUTTON_LABELS = {key: value["text"] for key, value in BROADCAST_BUTTONS.items()}


def get_message_buttons_selector_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return get_updated_message_buttons_selector_keyboard_with_media(list(DEFAULT_BROADCAST_BUTTONS), False, language)

def get_broadcast_media_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📷 Добавить фото", callback_data="add_media_photo"),
            InlineKeyboardButton(text="🎥 Добавить видео", callback_data="add_media_video")
        ],
        [
            InlineKeyboardButton(text="📄 Добавить документ", callback_data="add_media_document"),
            InlineKeyboardButton(text="⏭️ Пропустить медиа", callback_data="skip_media")
        ],
        [
            InlineKeyboardButton(text="❌ Отмена", callback_data="admin_messages")
        ]
    ])

def get_media_confirm_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Использовать это медиа", callback_data="confirm_media"),
            InlineKeyboardButton(text="🔄 Заменить медиа", callback_data="replace_media")
        ],
        [
            InlineKeyboardButton(text="⏭️ Без медиа", callback_data="skip_media"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="admin_messages")
        ]
    ])

def get_updated_message_buttons_selector_keyboard_with_media(selected_buttons: list, has_media: bool = False, language: str = "ru") -> InlineKeyboardMarkup:
    selected_buttons = selected_buttons or []

    keyboard: list[list[InlineKeyboardButton]] = []

    for row in BROADCAST_BUTTON_ROWS:
        row_buttons: list[InlineKeyboardButton] = []
        for button_key in row:
            button_config = BROADCAST_BUTTONS[button_key]
            base_text = button_config["text"]
            if button_key in selected_buttons:
                if " " in base_text:
                    toggle_text = f"✅ {base_text.split(' ', 1)[1]}"
                else:
                    toggle_text = f"✅ {base_text}"
            else:
                toggle_text = base_text
            row_buttons.append(
                InlineKeyboardButton(text=toggle_text, callback_data=f"btn_{button_key}")
            )
        if row_buttons:
            keyboard.append(row_buttons)

    if has_media:
        keyboard.append([
            InlineKeyboardButton(text="🖼️ Изменить медиа", callback_data="change_media")
        ])

    keyboard.extend([
        [
            InlineKeyboardButton(text="✅ Продолжить", callback_data="buttons_confirm")
        ],
        [
            InlineKeyboardButton(text="❌ Отмена", callback_data="admin_messages")
        ]
    ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)
