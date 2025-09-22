import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User, Subscription, Transaction
from app.database.crud.user import get_user_by_id

logger = logging.getLogger(__name__)


class AdminNotificationService:
    
    def __init__(self, bot: Bot):
        self.bot = bot
        self.chat_id = getattr(settings, 'ADMIN_NOTIFICATIONS_CHAT_ID', None)
        self.topic_id = getattr(settings, 'ADMIN_NOTIFICATIONS_TOPIC_ID', None)
        self.enabled = getattr(settings, 'ADMIN_NOTIFICATIONS_ENABLED', False)
    
    async def _get_referrer_info(self, db: AsyncSession, referred_by_id: Optional[int]) -> str:
        if not referred_by_id:
            return "Нет"
        
        try:
            referrer = await get_user_by_id(db, referred_by_id)
            if not referrer:
                return f"ID {referred_by_id} (не найден)"
            
            if referrer.username:
                return f"@{referrer.username} (ID: {referred_by_id})"
            else:
                return f"ID {referrer.telegram_id}"
                
        except Exception as e:
            logger.error(f"Ошибка получения данных рефера {referred_by_id}: {e}")
            return f"ID {referred_by_id}"
    
    async def send_trial_activation_notification(
        self,
        db: AsyncSession,
        user: User,
        subscription: Subscription
    ) -> bool:
        if not self._is_enabled():
            return False
        
        try:
            user_status = "🆕 Новый" if not user.has_had_paid_subscription else "🔄 Существующий"
            referrer_info = await self._get_referrer_info(db, user.referred_by_id)
            
            message = f"""🎯 <b>АКТИВАЦИЯ ТРИАЛА</b>

👤 <b>Пользователь:</b> {user.full_name}
🆔 <b>Telegram ID:</b> <code>{user.telegram_id}</code>
📱 <b>Username:</b> @{user.username or 'отсутствует'}
👥 <b>Статус:</b> {user_status}

⏰ <b>Параметры триала:</b>
📅 Период: {settings.TRIAL_DURATION_DAYS} дней
📊 Трафик: {settings.TRIAL_TRAFFIC_LIMIT_GB} ГБ
📱 Устройства: {settings.TRIAL_DEVICE_LIMIT}
🌐 Сервер: {subscription.connected_squads[0] if subscription.connected_squads else 'По умолчанию'}

📆 <b>Действует до:</b> {subscription.end_date.strftime('%d.%m.%Y %H:%M')}
🔗 <b>Реферер:</b> {referrer_info}

⏰ <i>{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</i>"""
            
            return await self._send_message(message)
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о триале: {e}")
            return False
    
    async def send_subscription_purchase_notification(
        self,
        db: AsyncSession,
        user: User,
        subscription: Subscription,
        transaction: Transaction,
        period_days: int,
        was_trial_conversion: bool = False
    ) -> bool:
        if not self._is_enabled():
            return False
        
        try:
            event_type = "🔄 КОНВЕРСИЯ ИЗ ТРИАЛА" if was_trial_conversion else "💎 ПОКУПКА ПОДПИСКИ"
            
            if was_trial_conversion:
                user_status = "🎯 Конверсия из триала"
            elif user.has_had_paid_subscription:
                user_status = "🔄 Продление/Обновление"
            else:
                user_status = "🆕 Первая покупка"
            
            servers_info = await self._get_servers_info(subscription.connected_squads)
            payment_method = self._get_payment_method_display(transaction.payment_method)
            referrer_info = await self._get_referrer_info(db, user.referred_by_id)
            
            message = f"""💎 <b>{event_type}</b>

👤 <b>Пользователь:</b> {user.full_name}
🆔 <b>Telegram ID:</b> <code>{user.telegram_id}</code>
📱 <b>Username:</b> @{user.username or 'отсутствует'}
👥 <b>Статус:</b> {user_status}

💰 <b>Платеж:</b>
💵 Сумма: {settings.format_price(transaction.amount_kopeks)}
💳 Способ: {payment_method}
🆔 ID транзакции: {transaction.id}

📱 <b>Параметры подписки:</b>
📅 Период: {period_days} дней
📊 Трафик: {self._format_traffic(subscription.traffic_limit_gb)}
📱 Устройства: {subscription.device_limit}
🌐 Серверы: {servers_info}

📆 <b>Действует до:</b> {subscription.end_date.strftime('%d.%m.%Y %H:%M')}
💰 <b>Баланс после покупки:</b> {settings.format_price(user.balance_kopeks)}
🔗 <b>Реферер:</b> {referrer_info}

⏰ <i>{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</i>"""
            
            return await self._send_message(message)
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о покупке: {e}")
            return False

    async def send_version_update_notification(
        self,
        current_version: str,
        latest_version, 
        total_updates: int
    ) -> bool:
        """Отправляет уведомление о новых обновлениях"""
        if not self._is_enabled():
            return False
        
        try:
            if latest_version.prerelease:
                update_type = "🧪 ПРЕДВАРИТЕЛЬНАЯ ВЕРСИЯ"
                type_icon = "🧪"
            elif latest_version.is_dev:
                update_type = "🔧 DEV ВЕРСИЯ"
                type_icon = "🔧"
            else:
                update_type = "📦 НОВАЯ ВЕРСИЯ"
                type_icon = "📦"
            
            description = latest_version.short_description
            if len(description) > 200:
                description = description[:197] + "..."
            
            message = f"""{type_icon} <b>{update_type} ДОСТУПНА</b>
    
    📦 <b>Текущая версия:</b> <code>{current_version}</code>
    🆕 <b>Новая версия:</b> <code>{latest_version.tag_name}</code>
    📅 <b>Дата релиза:</b> {latest_version.formatted_date}
    
    📝 <b>Описание:</b>
    {description}
    
    🔢 <b>Всего доступно обновлений:</b> {total_updates}
    🔗 <b>Репозиторий:</b> https://github.com/{getattr(self, 'repo', 'fr1ngg/remnawave-bedolaga-telegram-bot')}
    
    ℹ️ Для обновления перезапустите контейнер с новым тегом или обновите код из репозитория.
    
    ⚙️ <i>Автоматическая проверка обновлений • {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</i>"""
            
            return await self._send_message(message)
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления об обновлении: {e}")
            return False
    
    async def send_version_check_error_notification(
        self,
        error_message: str,
        current_version: str
    ) -> bool:
        if not self._is_enabled():
            return False
        
        try:
            message = f"""⚠️ <b>ОШИБКА ПРОВЕРКИ ОБНОВЛЕНИЙ</b>
    
    📦 <b>Текущая версия:</b> <code>{current_version}</code>
    ❌ <b>Ошибка:</b> {error_message}
    
    🔄 Следующая попытка через час.
    ⚙️ Проверьте доступность GitHub API и настройки сети.
    
    ⚙️ <i>Система автоматических обновлений • {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</i>"""
            
            return await self._send_message(message)
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления об ошибке проверки версий: {e}")
            return False
    
    async def send_balance_topup_notification(
        self,
        db: AsyncSession,
        user: User,
        transaction: Transaction,
        old_balance: int
    ) -> bool:
        if not self._is_enabled():
            return False
        
        try:
            topup_status = "🆕 Первое пополнение" if not user.has_made_first_topup else "🔄 Пополнение"
            payment_method = self._get_payment_method_display(transaction.payment_method)
            balance_change = user.balance_kopeks - old_balance
            referrer_info = await self._get_referrer_info(db, user.referred_by_id)
            
            message = f"""💰 <b>ПОПОЛНЕНИЕ БАЛАНСА</b>

👤 <b>Пользователь:</b> {user.full_name}
🆔 <b>Telegram ID:</b> <code>{user.telegram_id}</code>
📱 <b>Username:</b> @{user.username or 'отсутствует'}
💳 <b>Статус:</b> {topup_status}

💰 <b>Детали пополнения:</b>
💵 Сумма: {settings.format_price(transaction.amount_kopeks)}
💳 Способ: {payment_method}
🆔 ID транзакции: {transaction.id}

💰 <b>Баланс:</b>
📉 Было: {settings.format_price(old_balance)}
📈 Стало: {settings.format_price(user.balance_kopeks)}
➕ Изменение: +{settings.format_price(balance_change)}

🔗 <b>Реферер:</b> {referrer_info}
📱 <b>Подписка:</b> {self._get_subscription_status(user)}

⏰ <i>{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</i>"""
            
            return await self._send_message(message)
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о пополнении: {e}")
            return False
    
    async def send_subscription_extension_notification(
        self,
        db: AsyncSession,
        user: User,
        subscription: Subscription,
        transaction: Transaction,
        extended_days: int,
        old_end_date: datetime
    ) -> bool:
        if not self._is_enabled():
            return False
        
        try:
            payment_method = self._get_payment_method_display(transaction.payment_method)
            servers_info = await self._get_servers_info(subscription.connected_squads)
            
            message = f"""⏰ <b>ПРОДЛЕНИЕ ПОДПИСКИ</b>

👤 <b>Пользователь:</b> {user.full_name}
🆔 <b>Telegram ID:</b> <code>{user.telegram_id}</code>
📱 <b>Username:</b> @{user.username or 'отсутствует'}

💰 <b>Платеж:</b>
💵 Сумма: {settings.format_price(transaction.amount_kopeks)}
💳 Способ: {payment_method}
🆔 ID транзакции: {transaction.id}

📅 <b>Продление:</b>
➕ Добавлено дней: {extended_days}
📆 Было до: {old_end_date.strftime('%d.%m.%Y %H:%M')}
📆 Стало до: {subscription.end_date.strftime('%d.%m.%Y %H:%M')}

📱 <b>Текущие параметры:</b>
📊 Трафик: {self._format_traffic(subscription.traffic_limit_gb)}
📱 Устройства: {subscription.device_limit}
🌐 Серверы: {servers_info}

💰 <b>Баланс после операции:</b> {settings.format_price(user.balance_kopeks)}

⏰ <i>{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</i>"""
            
            return await self._send_message(message)
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о продлении: {e}")
            return False
    
    async def _send_message(self, text: str) -> bool:
        if not self.chat_id:
            logger.warning("ADMIN_NOTIFICATIONS_CHAT_ID не настроен")
            return False
        
        try:
            message_kwargs = {
                'chat_id': self.chat_id,
                'text': text,
                'parse_mode': 'HTML',
                'disable_web_page_preview': True
            }
            
            if self.topic_id:
                message_kwargs['message_thread_id'] = self.topic_id
            
            await self.bot.send_message(**message_kwargs)
            logger.info(f"Уведомление отправлено в чат {self.chat_id}")
            return True
            
        except TelegramForbiddenError:
            logger.error(f"Бот не имеет прав для отправки в чат {self.chat_id}")
            return False
        except TelegramBadRequest as e:
            logger.error(f"Ошибка отправки уведомления: {e}")
            return False
        except Exception as e:
            logger.error(f"Неожиданная ошибка при отправке уведомления: {e}")
            return False
    
    def _is_enabled(self) -> bool:
        return self.enabled and bool(self.chat_id)
    
    def _get_payment_method_display(self, payment_method: Optional[str]) -> str:
        method_names = {
            'telegram_stars': '⭐ Telegram Stars',
            'yookassa': '💳 YooKassa (карта)',
            'tribute': '💎 Tribute (карта)',
            'manual': '🛠️ Вручную (админ)',
            'balance': '💰 С баланса'
        }
        
        if not payment_method:
            return '💰 С баланса'
            
        return method_names.get(payment_method, f'💰 С баланса')
    
    def _format_traffic(self, traffic_gb: int) -> str:
        if traffic_gb == 0:
            return "∞ Безлимит"
        return f"{traffic_gb} ГБ"
    
    def _get_subscription_status(self, user: User) -> str:
        if not user.subscription:
            return "❌ Нет подписки"
        
        sub = user.subscription
        if sub.is_trial:
            return f"🎯 Триал (до {sub.end_date.strftime('%d.%m')})"
        elif sub.is_active:
            return f"✅ Активна (до {sub.end_date.strftime('%d.%m')})"
        else:
            return "❌ Неактивна"
    
    async def _get_servers_info(self, squad_uuids: list) -> str:
        if not squad_uuids:
            return "❌ Нет серверов"
        
        try:
            from app.handlers.subscription import get_servers_display_names
            servers_names = await get_servers_display_names(squad_uuids)
            return f"{len(squad_uuids)} шт. ({servers_names})"
        except Exception as e:
            logger.warning(f"Не удалось получить названия серверов: {e}")
            return f"{len(squad_uuids)} шт."


    async def send_maintenance_status_notification(
        self,
        event_type: str,
        status: str,
        details: Dict[str, Any] = None
    ) -> bool:
        if not self._is_enabled():
            return False
        
        try:
            details = details or {}
            
            if event_type == "enable":
                if details.get("auto_enabled", False):
                    icon = "⚠️"
                    title = "АВТОМАТИЧЕСКОЕ ВКЛЮЧЕНИЕ ТЕХРАБОТ"
                    alert_type = "warning"
                else:
                    icon = "🔧"
                    title = "ВКЛЮЧЕНИЕ ТЕХРАБОТ"
                    alert_type = "info"
                    
            elif event_type == "disable":
                icon = "✅"
                title = "ОТКЛЮЧЕНИЕ ТЕХРАБОТ"
                alert_type = "success"
                
            elif event_type == "api_status":
                if status == "online":
                    icon = "🟢"
                    title = "API REMNAWAVE ВОССТАНОВЛЕНО"
                    alert_type = "success"
                else:
                    icon = "🔴"
                    title = "API REMNAWAVE НЕДОСТУПНО"
                    alert_type = "error"
                    
            elif event_type == "monitoring":
                if status == "started":
                    icon = "🔍"
                    title = "МОНИТОРИНГ ЗАПУЩЕН"
                    alert_type = "info"
                else:
                    icon = "⏹️"
                    title = "МОНИТОРИНГ ОСТАНОВЛЕН"
                    alert_type = "info"
            else:
                icon = "ℹ️"
                title = "СИСТЕМА ТЕХРАБОТ"
                alert_type = "info"
            
            message_parts = [f"{icon} <b>{title}</b>", ""]
            
            if event_type == "enable":
                if details.get("reason"):
                    message_parts.append(f"📋 <b>Причина:</b> {details['reason']}")
                
                if details.get("enabled_at"):
                    enabled_at = details["enabled_at"]
                    if isinstance(enabled_at, str):
                        from datetime import datetime
                        enabled_at = datetime.fromisoformat(enabled_at)
                    message_parts.append(f"🕐 <b>Время включения:</b> {enabled_at.strftime('%d.%m.%Y %H:%M:%S')}")
                
                message_parts.append(f"🤖 <b>Автоматически:</b> {'Да' if details.get('auto_enabled', False) else 'Нет'}")
                message_parts.append("")
                message_parts.append("❗ Обычные пользователи временно не могут использовать бота.")
                
            elif event_type == "disable":
                if details.get("disabled_at"):
                    disabled_at = details["disabled_at"]
                    if isinstance(disabled_at, str):
                        from datetime import datetime
                        disabled_at = datetime.fromisoformat(disabled_at)
                    message_parts.append(f"🕐 <b>Время отключения:</b> {disabled_at.strftime('%d.%m.%Y %H:%M:%S')}")
                
                if details.get("duration"):
                    duration = details["duration"]
                    if isinstance(duration, (int, float)):
                        hours = int(duration // 3600)
                        minutes = int((duration % 3600) // 60)
                        if hours > 0:
                            duration_str = f"{hours}ч {minutes}мин"
                        else:
                            duration_str = f"{minutes}мин"
                        message_parts.append(f"⏱️ <b>Длительность:</b> {duration_str}")
                
                message_parts.append(f"🤖 <b>Было автоматическим:</b> {'Да' if details.get('was_auto', False) else 'Нет'}")
                message_parts.append("")
                message_parts.append("✅ Сервис снова доступен для пользователей.")
                
            elif event_type == "api_status":
                message_parts.append(f"🔗 <b>API URL:</b> {details.get('api_url', 'неизвестно')}")
                
                if status == "online":
                    if details.get("response_time"):
                        message_parts.append(f"⚡ <b>Время отклика:</b> {details['response_time']} сек")
                        
                    if details.get("consecutive_failures", 0) > 0:
                        message_parts.append(f"🔄 <b>Неудачных попыток было:</b> {details['consecutive_failures']}")
                        
                    message_parts.append("")
                    message_parts.append("API снова отвечает на запросы.")
                    
                else: 
                    if details.get("consecutive_failures"):
                        message_parts.append(f"🔄 <b>Попытка №:</b> {details['consecutive_failures']}")
                        
                    if details.get("error"):
                        error_msg = str(details["error"])[:100]  
                        message_parts.append(f"❌ <b>Ошибка:</b> {error_msg}")
                        
                    message_parts.append("")
                    message_parts.append("⚠️ Началась серия неудачных проверок API.")
                    
            elif event_type == "monitoring":
                if status == "started":
                    if details.get("check_interval"):
                        message_parts.append(f"🔄 <b>Интервал проверки:</b> {details['check_interval']} сек")
                        
                    if details.get("auto_enable_configured") is not None:
                        auto_enable = "Включено" if details["auto_enable_configured"] else "Отключено"
                        message_parts.append(f"🤖 <b>Автовключение:</b> {auto_enable}")
                        
                    if details.get("max_failures"):
                        message_parts.append(f"🎯 <b>Порог ошибок:</b> {details['max_failures']}")
                        
                    message_parts.append("")
                    message_parts.append("Система будет следить за доступностью API.")
                    
                else:  
                    message_parts.append("Автоматический мониторинг API остановлен.")
            
            from datetime import datetime
            message_parts.append("")
            message_parts.append(f"⏰ <i>{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</i>")
            
            message = "\n".join(message_parts)
            
            return await self._send_message(message)
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о техработах: {e}")
            return False
    
    async def send_remnawave_panel_status_notification(
        self,
        status: str,
        details: Dict[str, Any] = None
    ) -> bool:
        if not self._is_enabled():
            return False
        
        try:
            details = details or {}
            
            status_config = {
                "online": {"icon": "🟢", "title": "ПАНЕЛЬ REMNAWAVE ДОСТУПНА", "alert_type": "success"},
                "offline": {"icon": "🔴", "title": "ПАНЕЛЬ REMNAWAVE НЕДОСТУПНА", "alert_type": "error"},
                "degraded": {"icon": "🟡", "title": "ПАНЕЛЬ REMNAWAVE РАБОТАЕТ СО СБОЯМИ", "alert_type": "warning"},
                "maintenance": {"icon": "🔧", "title": "ПАНЕЛЬ REMNAWAVE НА ОБСЛУЖИВАНИИ", "alert_type": "info"}
            }
            
            config = status_config.get(status, status_config["offline"])
            
            message_parts = [
                f"{config['icon']} <b>{config['title']}</b>",
                ""
            ]
            
            if details.get("api_url"):
                message_parts.append(f"🔗 <b>URL:</b> {details['api_url']}")
                
            if details.get("response_time"):
                message_parts.append(f"⚡ <b>Время отклика:</b> {details['response_time']} сек")
                
            if details.get("last_check"):
                last_check = details["last_check"]
                if isinstance(last_check, str):
                    from datetime import datetime
                    last_check = datetime.fromisoformat(last_check)
                message_parts.append(f"🕐 <b>Последняя проверка:</b> {last_check.strftime('%H:%M:%S')}")
                
            if status == "online":
                if details.get("uptime"):
                    message_parts.append(f"⏱️ <b>Время работы:</b> {details['uptime']}")
                    
                if details.get("users_online"):
                    message_parts.append(f"👥 <b>Пользователей онлайн:</b> {details['users_online']}")
                    
                message_parts.append("")
                message_parts.append("✅ Все системы работают нормально.")
                
            elif status == "offline":
                if details.get("error"):
                    error_msg = str(details["error"])[:150]
                    message_parts.append(f"❌ <b>Ошибка:</b> {error_msg}")
                    
                if details.get("consecutive_failures"):
                    message_parts.append(f"🔄 <b>Неудачных попыток:</b> {details['consecutive_failures']}")
                    
                message_parts.append("")
                message_parts.append("⚠️ Панель недоступна. Проверьте соединение и статус сервера.")
                
            elif status == "degraded":
                if details.get("issues"):
                    issues = details["issues"]
                    if isinstance(issues, list):
                        message_parts.append("⚠️ <b>Обнаруженные проблемы:</b>")
                        for issue in issues[:3]: 
                            message_parts.append(f"   • {issue}")
                    else:
                        message_parts.append(f"⚠️ <b>Проблема:</b> {issues}")
                        
                message_parts.append("")
                message_parts.append("Панель работает, но возможны задержки или сбои.")
                
            elif status == "maintenance":
                if details.get("maintenance_reason"):
                    message_parts.append(f"🔧 <b>Причина:</b> {details['maintenance_reason']}")
                    
                if details.get("estimated_duration"):
                    message_parts.append(f"⏰ <b>Ожидаемая длительность:</b> {details['estimated_duration']}")
                    
                message_parts.append("")
                message_parts.append("Панель временно недоступна для обслуживания.")
            
            from datetime import datetime
            message_parts.append("")
            message_parts.append(f"⏰ <i>{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</i>")
            
            message = "\n".join(message_parts)
            
            return await self._send_message(message)
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о статусе панели Remnawave: {e}")
            return False


    async def send_subscription_update_notification(
        self,
        db: AsyncSession,
        user: User,
        subscription: Subscription,
        update_type: str,
        old_value: Any,
        new_value: Any,
        price_paid: int = 0
    ) -> bool:
        if not self._is_enabled():
            return False
        
        try:
            referrer_info = await self._get_referrer_info(db, user.referred_by_id)
            
            update_types = {
                "traffic": ("📊 ИЗМЕНЕНИЕ ТРАФИКА", "трафик"),
                "devices": ("📱 ИЗМЕНЕНИЕ УСТРОЙСТВ", "количество устройств"), 
                "servers": ("🌐 ИЗМЕНЕНИЕ СЕРВЕРОВ", "серверы")
            }
            
            title, param_name = update_types.get(update_type, ("⚙️ ИЗМЕНЕНИЕ ПОДПИСКИ", "параметры"))
            
            message = f"""{title}

    👤 <b>Пользователь:</b> {user.full_name}
    🆔 <b>Telegram ID:</b> <code>{user.telegram_id}</code>
    📱 <b>Username:</b> @{user.username or 'отсутствует'}

    🔧 <b>Изменение:</b>
    📋 Параметр: {param_name}"""

            if update_type == "servers":
                old_servers_info = await self._format_servers_detailed(old_value)
                new_servers_info = await self._format_servers_detailed(new_value)
                
                message += f"""
    📉 Было: {old_servers_info}
    📈 Стало: {new_servers_info}"""
            else:
                message += f"""
    📉 Было: {self._format_update_value(old_value, update_type)}
    📈 Стало: {self._format_update_value(new_value, update_type)}"""

            if price_paid > 0:
                message += f"\n💰 Доплачено: {settings.format_price(price_paid)}"
            else:
                message += f"\n💸 Бесплатно"

            message += f"""

    📅 <b>Подписка действует до:</b> {subscription.end_date.strftime('%d.%m.%Y %H:%M')}
    💰 <b>Баланс после операции:</b> {settings.format_price(user.balance_kopeks)}
    🔗 <b>Рефер:</b> {referrer_info}

    ⏰ <i>{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</i>"""
            
            return await self._send_message(message)
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления об изменении подписки: {e}")
            return False

    async def _format_servers_detailed(self, server_uuids: List[str]) -> str:
        if not server_uuids:
            return "Нет серверов"
        
        try:
            from app.handlers.subscription import get_servers_display_names
            servers_names = await get_servers_display_names(server_uuids)
            
            if servers_names and servers_names != "Нет серверов":
                return f"{len(server_uuids)} серверов ({servers_names})"
            else:
                return f"{len(server_uuids)} серверов"
                
        except Exception as e:
            logger.warning(f"Ошибка получения названий серверов для уведомления: {e}")
            return f"{len(server_uuids)} серверов"

    def _format_update_value(self, value: Any, update_type: str) -> str:
        if update_type == "traffic":
            if value == 0:
                return "♾ Безлимитный"
            return f"{value} ГБ"
        elif update_type == "devices":
            return f"{value} устройств"
        elif update_type == "servers":
            if isinstance(value, list):
                return f"{len(value)} серверов"
            return str(value)
        return str(value)

