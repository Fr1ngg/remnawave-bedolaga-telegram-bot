import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from dataclasses import dataclass

from app.config import settings
from app.external.remnawave_api import RemnaWaveAPI, test_api_connection
from app.utils.cache import cache

logger = logging.getLogger(__name__)


@dataclass
class MaintenanceStatus:
    is_active: bool
    enabled_at: Optional[datetime] = None
    last_check: Optional[datetime] = None
    reason: Optional[str] = None
    auto_enabled: bool = False
    api_status: bool = True
    consecutive_failures: int = 0


class MaintenanceService:
    
    def __init__(self):
        self._status = MaintenanceStatus(is_active=False)
        self._check_task: Optional[asyncio.Task] = None
        self._is_checking = False
        self._max_consecutive_failures = 3
        self._bot = None 
        self._last_notification_sent = None 
        
    def set_bot(self, bot):
        self._bot = bot
        logger.info("Бот установлен для maintenance_service")
    
    @property
    def status(self) -> MaintenanceStatus:
        return self._status
    
    def is_maintenance_active(self) -> bool:
        return self._status.is_active
    
    def get_maintenance_message(self) -> str:
        if self._status.auto_enabled:
            return f"""
🔧 Технические работы!

Сервис временно недоступен из-за проблем с подключением к серверам.

⏰ Мы работаем над восстановлением. Попробуйте через несколько минут.

🔄 Последняя проверка: {self._status.last_check.strftime('%H:%M:%S') if self._status.last_check else 'неизвестно'}
"""
        else:
            return settings.get_maintenance_message()
    
    async def _send_admin_notification(self, message: str, alert_type: str = "info"):
        if not self._bot:
            logger.warning("Бот не установлен, уведомления не могут быть отправлены")
            return False
        
        try:
            from app.services.admin_notification_service import AdminNotificationService
            
            notification_service = AdminNotificationService(self._bot)
            
            if not notification_service._is_enabled():
                logger.debug("Уведомления администраторов отключены")
                return False
            
            emoji_map = {
                "error": "🚨",
                "warning": "⚠️", 
                "success": "✅",
                "info": "ℹ️"
            }
            emoji = emoji_map.get(alert_type, "ℹ️")
            
            formatted_message = f"{emoji} <b>ТЕХНИЧЕСКИЕ РАБОТЫ</b>\n\n{message}\n\n⏰ <i>{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</i>"
            
            return await notification_service._send_message(formatted_message)
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления через AdminNotificationService: {e}")
            return False
    
    async def _notify_admins(self, message: str, alert_type: str = "info"):
        if not self._bot:
            logger.warning("Бот не установлен, уведомления не могут быть отправлены")
            return
        
        notification_sent = await self._send_admin_notification(message, alert_type)
        
        if notification_sent:
            logger.info("Уведомление успешно отправлено через AdminNotificationService")
            return
        
        logger.info("Отправляем уведомление напрямую администраторам")
        
        cache_key = f"maintenance_notification_{alert_type}"
        if await cache.get(cache_key):
            return
        
        admin_ids = settings.get_admin_ids()
        if not admin_ids:
            logger.warning("Список администраторов пуст")
            return
        
        emoji_map = {
            "error": "🚨",
            "warning": "⚠️", 
            "success": "✅",
            "info": "ℹ️"
        }
        emoji = emoji_map.get(alert_type, "ℹ️")
        
        formatted_message = f"{emoji} <b>Maintenance Service</b>\n\n{message}"
        
        success_count = 0
        for admin_id in admin_ids:
            try:
                await self._bot.send_message(
                    chat_id=admin_id,
                    text=formatted_message,
                    parse_mode="HTML"
                )
                success_count += 1
                await asyncio.sleep(0.1) 
                
            except Exception as e:
                logger.error(f"Ошибка отправки уведомления админу {admin_id}: {e}")
        
        if success_count > 0:
            logger.info(f"Уведомление отправлено {success_count} администраторам")
            await cache.set(cache_key, True, expire=300)
        else:
            logger.error("Не удалось отправить уведомления ни одному администратору")
    
    async def enable_maintenance(self, reason: Optional[str] = None, auto: bool = False) -> bool:
        try:
            if self._status.is_active:
                logger.warning("Режим техработ уже включен")
                return True
            
            self._status.is_active = True
            self._status.enabled_at = datetime.utcnow()
            self._status.reason = reason or ("Автоматическое включение" if auto else "Включено администратором")
            self._status.auto_enabled = auto
            
            await self._save_status_to_cache()
            
            notification_msg = f"""Режим технических работ ВКЛЮЧЕН

📋 <b>Причина:</b> {self._status.reason}
🤖 <b>Автоматически:</b> {'Да' if auto else 'Нет'}
🕐 <b>Время:</b> {self._status.enabled_at.strftime('%d.%m.%Y %H:%M:%S')}

Обычные пользователи временно не смогут использовать бота."""
            
            await self._notify_admins(notification_msg, "warning" if auto else "info")
            
            logger.warning(f"🔧 Режим техработ ВКЛЮЧЕН. Причина: {self._status.reason}")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка включения режима техработ: {e}")
            return False
    
    async def disable_maintenance(self) -> bool:
        try:
            if not self._status.is_active:
                logger.info("Режим техработ уже выключен")
                return True
            
            was_auto = self._status.auto_enabled
            duration = None
            if self._status.enabled_at:
                duration = datetime.utcnow() - self._status.enabled_at
            
            self._status.is_active = False
            self._status.enabled_at = None
            self._status.reason = None
            self._status.auto_enabled = False
            self._status.consecutive_failures = 0
            
            await self._save_status_to_cache()
            
            duration_str = ""
            if duration:
                hours = int(duration.total_seconds() // 3600)
                minutes = int((duration.total_seconds() % 3600) // 60)
                if hours > 0:
                    duration_str = f"\n⏱️ <b>Длительность:</b> {hours}ч {minutes}мин"
                else:
                    duration_str = f"\n⏱️ <b>Длительность:</b> {minutes}мин"
            
            notification_msg = f"""Режим технических работ ВЫКЛЮЧЕН

🤖 <b>Автоматически:</b> {'Да' if was_auto else 'Нет'}
🕐 <b>Время:</b> {datetime.utcnow().strftime('%d.%m.%Y %H:%M:%S')}
{duration_str}

Сервис снова доступен для пользователей."""
            
            await self._notify_admins(notification_msg, "success")
            
            logger.info("✅ Режим техработ ВЫКЛЮЧЕН")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка выключения режима техработ: {e}")
            return False
    
    async def start_monitoring(self) -> bool:
        try:
            if self._check_task and not self._check_task.done():
                logger.warning("Мониторинг уже запущен")
                return True
            
            await self._load_status_from_cache()
            
            self._check_task = asyncio.create_task(self._monitoring_loop())
            logger.info(f"🔄 Запущен мониторинг API Remnawave (интервал: {settings.get_maintenance_check_interval()}с)")
            
            await self._notify_admins(f"""Мониторинг технических работ запущен

🔄 <b>Интервал проверки:</b> {settings.get_maintenance_check_interval()} секунд
🤖 <b>Автовключение:</b> {'Включено' if settings.is_maintenance_auto_enable() else 'Отключено'}
🎯 <b>Порог ошибок:</b> {self._max_consecutive_failures}

Система будет следить за доступностью API.""", "info")
            
            return True
            
        except Exception as e:
            logger.error(f"Ошибка запуска мониторинга: {e}")
            return False
    
    async def stop_monitoring(self) -> bool:
        try:
            if self._check_task and not self._check_task.done():
                self._check_task.cancel()
                try:
                    await self._check_task
                except asyncio.CancelledError:
                    pass
            
            await self._notify_admins("Мониторинг технических работ остановлен", "info")
            logger.info("ℹ️ Мониторинг API остановлен")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка остановки мониторинга: {e}")
            return False
    
    async def check_api_status(self) -> bool:
        try:
            if self._is_checking:
                return self._status.api_status
            
            self._is_checking = True
            self._status.last_check = datetime.utcnow()
            
            auth_params = settings.get_remnawave_auth_params()
            api = RemnaWaveAPI(
                base_url=auth_params["base_url"],
                api_key=auth_params["api_key"],
                secret_key=auth_params["secret_key"],
                username=auth_params["username"],
                password=auth_params["password"]
            )
            
            async with api:
                is_connected = await test_api_connection(api)
                
                if is_connected:
                    if not self._status.api_status:
                        await self._notify_admins(f"""API Remnawave восстановлено!

✅ <b>Статус:</b> Доступно
🕐 <b>Время восстановления:</b> {self._status.last_check.strftime('%H:%M:%S')}
🔄 <b>Неудачных попыток было:</b> {self._status.consecutive_failures}

API снова отвечает на запросы.""", "success")
                    
                    self._status.api_status = True
                    self._status.consecutive_failures = 0
                    
                    if self._status.is_active and self._status.auto_enabled:
                        await self.disable_maintenance()
                        logger.info("✅ API восстановился, режим техработ автоматически отключен")
                    
                    return True
                else:
                    was_available = self._status.api_status
                    self._status.api_status = False
                    self._status.consecutive_failures += 1
                    
                    if was_available:
                        await self._notify_admins(f"""API Remnawave недоступно!

❌ <b>Статус:</b> Недоступно
🕐 <b>Время обнаружения:</b> {self._status.last_check.strftime('%H:%M:%S')}
🔄 <b>Попытка:</b> {self._status.consecutive_failures}

Началась серия неудачных проверок API.""", "error")
                    
                    if (self._status.consecutive_failures >= self._max_consecutive_failures and
                        not self._status.is_active and
                        settings.is_maintenance_auto_enable()):
                        
                        await self.enable_maintenance(
                            reason=f"Автоматическое включение после {self._status.consecutive_failures} неудачных проверок API",
                            auto=True
                        )
                    
                    return False
                    
        except Exception as e:
            logger.error(f"Ошибка проверки API: {e}")
            
            if self._status.api_status:
                await self._notify_admins(f"""Ошибка при проверке API Remnawave

❌ <b>Ошибка:</b> {str(e)}
🕐 <b>Время:</b> {datetime.utcnow().strftime('%H:%M:%S')}

Не удалось выполнить проверку доступности API.""", "error")
            
            self._status.api_status = False
            self._status.consecutive_failures += 1
            return False
        finally:
            self._is_checking = False
            await self._save_status_to_cache()
    
    async def _monitoring_loop(self):
        while True:
            try:
                await self.check_api_status()
                await asyncio.sleep(settings.get_maintenance_check_interval())
                
            except asyncio.CancelledError:
                logger.info("Мониторинг отменен")
                break
            except Exception as e:
                logger.error(f"Ошибка в цикле мониторинга: {e}")
                await asyncio.sleep(30) 
    
    async def _save_status_to_cache(self):
        try:
            status_data = {
                "is_active": self._status.is_active,
                "enabled_at": self._status.enabled_at.isoformat() if self._status.enabled_at else None,
                "reason": self._status.reason,
                "auto_enabled": self._status.auto_enabled,
                "consecutive_failures": self._status.consecutive_failures,
                "last_check": self._status.last_check.isoformat() if self._status.last_check else None
            }
            
            await cache.set("maintenance_status", status_data, expire=3600)
            
        except Exception as e:
            logger.error(f"Ошибка сохранения состояния в кеш: {e}")
    
    async def _load_status_from_cache(self):
        try:
            status_data = await cache.get("maintenance_status")
            if not status_data:
                return
            
            self._status.is_active = status_data.get("is_active", False)
            self._status.reason = status_data.get("reason")
            self._status.auto_enabled = status_data.get("auto_enabled", False)
            self._status.consecutive_failures = status_data.get("consecutive_failures", 0)
            
            if status_data.get("enabled_at"):
                self._status.enabled_at = datetime.fromisoformat(status_data["enabled_at"])
            
            if status_data.get("last_check"):
                self._status.last_check = datetime.fromisoformat(status_data["last_check"])
            
            logger.info(f"🔥 Состояние техработ загружено из кеша: активен={self._status.is_active}")
            
        except Exception as e:
            logger.error(f"Ошибка загрузки состояния из кеша: {e}")
    
    def get_status_info(self) -> Dict[str, Any]:
        return {
            "is_active": self._status.is_active,
            "enabled_at": self._status.enabled_at,
            "last_check": self._status.last_check,
            "reason": self._status.reason,
            "auto_enabled": self._status.auto_enabled,
            "api_status": self._status.api_status,
            "consecutive_failures": self._status.consecutive_failures,
            "monitoring_active": self._check_task is not None and not self._check_task.done(),
            "auto_enable_configured": settings.is_maintenance_auto_enable(),
            "check_interval": settings.get_maintenance_check_interval(),
            "bot_connected": self._bot is not None
        }
    
    async def force_api_check(self) -> Dict[str, Any]:
        start_time = datetime.utcnow()
        
        try:
            api_status = await self.check_api_status()
            end_time = datetime.utcnow()
            response_time = (end_time - start_time).total_seconds()
            
            return {
                "success": True,
                "api_available": api_status,
                "response_time": round(response_time, 2),
                "checked_at": end_time,
                "consecutive_failures": self._status.consecutive_failures
            }
            
        except Exception as e:
            end_time = datetime.utcnow()
            response_time = (end_time - start_time).total_seconds()
            
            return {
                "success": False,
                "api_available": False,
                "error": str(e),
                "response_time": round(response_time, 2),
                "checked_at": end_time,
                "consecutive_failures": self._status.consecutive_failures
            }
    
    async def send_remnawave_status_notification(self, status: str, details: str = "") -> bool:
        try:
            status_emojis = {
                "online": "🟢",
                "offline": "🔴", 
                "warning": "🟡",
                "error": "⚠️"
            }
            
            emoji = status_emojis.get(status, "ℹ️")
            
            message = f"""Статус панели Remnawave изменился

{emoji} <b>Статус:</b> {status.upper()}
🔗 <b>URL:</b> {settings.REMNAWAVE_API_URL}
{details}"""
            
            alert_type = "error" if status in ["offline", "error"] else "info"
            await self._notify_admins(message, alert_type)
            
            logger.info(f"Отправлено уведомление о статусе Remnawave: {status}")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о статусе Remnawave: {e}")
            return False


maintenance_service = MaintenanceService()
