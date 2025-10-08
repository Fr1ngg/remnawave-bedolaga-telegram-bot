import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from zoneinfo import ZoneInfo

from app.config import settings
from app.external.remnawave_api import (
    RemnaWaveAPI, RemnaWaveUser, RemnaWaveInternalSquad,
    RemnaWaveNode, UserStatus, TrafficLimitStrategy, RemnaWaveAPIError
)
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.crud.user import get_users_list, get_user_by_telegram_id, update_user
from app.database.crud.subscription import (
    get_subscription_by_user_id,
    update_subscription_usage,
    decrement_subscription_server_counts,
)
from app.database.models import (
    User, SubscriptionServer, Transaction, ReferralEarning, 
    PromoCodeUse, SubscriptionStatus
)

logger = logging.getLogger(__name__)


class RemnaWaveConfigurationError(Exception):
    """Raised when RemnaWave API configuration is missing."""


class RemnaWaveService:

    def __init__(self):
        auth_params = settings.get_remnawave_auth_params()
        base_url = (auth_params.get("base_url") or "").strip()
        api_key = (auth_params.get("api_key") or "").strip()

        self._config_error: Optional[str] = None

        tz_name = os.getenv("TZ", "UTC")
        try:
            self._panel_timezone = ZoneInfo(tz_name)
        except Exception:
            logger.warning(
                "⚠️ Не удалось загрузить временную зону '%s'. Используется UTC.",
                tz_name,
            )
            self._panel_timezone = ZoneInfo("UTC")

        if not base_url:
            self._config_error = "REMNAWAVE_API_URL не настроен"
        elif not api_key:
            self._config_error = "REMNAWAVE_API_KEY не настроен"

        self.api: Optional[RemnaWaveAPI]
        if self._config_error:
            self.api = None
        else:
            self.api = RemnaWaveAPI(
                base_url=base_url,
                api_key=api_key,
                secret_key=auth_params.get("secret_key"),
                username=auth_params.get("username"),
                password=auth_params.get("password")
            )

    @property
    def is_configured(self) -> bool:
        return self._config_error is None

    @property
    def configuration_error(self) -> Optional[str]:
        return self._config_error

    def _ensure_configured(self) -> None:
        if not self.is_configured or self.api is None:
            raise RemnaWaveConfigurationError(
                self._config_error or "RemnaWave API не настроен"
            )

    @asynccontextmanager
    async def get_api_client(self):
        self._ensure_configured()
        assert self.api is not None
        async with self.api as api:
            yield api

    def _now_in_panel_timezone(self) -> datetime:
        """Возвращает текущее время без часового пояса в зоне панели."""
        return datetime.now(self._panel_timezone).replace(tzinfo=None)

    def _parse_remnawave_date(self, date_str: str) -> datetime:
        if not date_str:
            return self._now_in_panel_timezone() + timedelta(days=30)

        try:

            cleaned_date = date_str.strip()

            if cleaned_date.endswith('Z'):
                cleaned_date = cleaned_date[:-1] + '+00:00'

            if '+00:00+00:00' in cleaned_date:
                cleaned_date = cleaned_date.replace('+00:00+00:00', '+00:00')

            cleaned_date = re.sub(r'(\+\d{2}:\d{2})\+\d{2}:\d{2}$', r'\1', cleaned_date)

            parsed_date = datetime.fromisoformat(cleaned_date)

            if parsed_date.tzinfo is not None:
                localized = parsed_date.astimezone(self._panel_timezone)
            else:
                localized = parsed_date.replace(tzinfo=self._panel_timezone)

            localized_naive = localized.replace(tzinfo=None)

            logger.debug(f"Успешно распарсена дата: {date_str} -> {localized_naive}")
            return localized_naive

        except Exception as e:
            logger.warning(f"⚠️ Не удалось распарсить дату '{date_str}': {e}. Используем дефолтную дату.")
            return self._now_in_panel_timezone() + timedelta(days=30)
    
    async def get_system_statistics(self) -> Dict[str, Any]:
            try:
                async with self.get_api_client() as api:
                    logger.info("Получение системной статистики RemnaWave...")
                
                    try:
                        system_stats = await api.get_system_stats()
                        logger.info(f"Системная статистика получена")
                    except Exception as e:
                        logger.error(f"Ошибка получения системной статистики: {e}")
                        system_stats = {}
                 
                    try:
                        bandwidth_stats = await api.get_bandwidth_stats()
                        logger.info(f"Статистика трафика получена")
                    except Exception as e:
                        logger.error(f"Ошибка получения статистики трафика: {e}")
                        bandwidth_stats = {}
                
                    try:
                        realtime_usage = await api.get_nodes_realtime_usage()
                        logger.info(f"Реалтайм статистика получена")
                    except Exception as e:
                        logger.error(f"Ошибка получения реалтайм статистики: {e}")
                        realtime_usage = []
                
                    try:
                        nodes_stats = await api.get_nodes_statistics()
                    except Exception as e:
                        logger.error(f"Ошибка получения статистики нод: {e}")
                        nodes_stats = {}
                
                
                    total_download = sum(node.get('downloadBytes', 0) for node in realtime_usage)
                    total_upload = sum(node.get('uploadBytes', 0) for node in realtime_usage)
                    total_realtime_traffic = total_download + total_upload
                
                    total_user_traffic = int(system_stats.get('users', {}).get('totalTrafficBytes', '0'))
                
                    nodes_weekly_data = []
                    if nodes_stats.get('lastSevenDays'):
                        nodes_by_name = {}
                        for day_data in nodes_stats['lastSevenDays']:
                            node_name = day_data['nodeName']
                            if node_name not in nodes_by_name:
                                nodes_by_name[node_name] = {
                                    'name': node_name,
                                    'total_bytes': 0,
                                    'days_data': []
                                }
                        
                            daily_bytes = int(day_data['totalBytes'])
                            nodes_by_name[node_name]['total_bytes'] += daily_bytes
                            nodes_by_name[node_name]['days_data'].append({
                                'date': day_data['date'],
                                'bytes': daily_bytes
                            })
                    
                        nodes_weekly_data = list(nodes_by_name.values())
                        nodes_weekly_data.sort(key=lambda x: x['total_bytes'], reverse=True)
                
                    result = {
                        "system": {
                            "users_online": system_stats.get('onlineStats', {}).get('onlineNow', 0),
                            "total_users": system_stats.get('users', {}).get('totalUsers', 0),
                            "active_connections": system_stats.get('onlineStats', {}).get('onlineNow', 0),
                            "nodes_online": system_stats.get('nodes', {}).get('totalOnline', 0),
                            "users_last_day": system_stats.get('onlineStats', {}).get('lastDay', 0),
                            "users_last_week": system_stats.get('onlineStats', {}).get('lastWeek', 0),
                            "users_never_online": system_stats.get('onlineStats', {}).get('neverOnline', 0),
                            "total_user_traffic": total_user_traffic
                        },
                        "users_by_status": system_stats.get('users', {}).get('statusCounts', {}),
                        "server_info": {
                            "cpu_cores": system_stats.get('cpu', {}).get('cores', 0),
                            "cpu_physical_cores": system_stats.get('cpu', {}).get('physicalCores', 0),
                            "memory_total": system_stats.get('memory', {}).get('total', 0),
                            "memory_used": system_stats.get('memory', {}).get('used', 0),
                            "memory_free": system_stats.get('memory', {}).get('free', 0),
                            "memory_available": system_stats.get('memory', {}).get('available', 0),
                            "uptime_seconds": system_stats.get('uptime', 0)
                        },
                        "bandwidth": {
                            "realtime_download": total_download,
                            "realtime_upload": total_upload,
                            "realtime_total": total_realtime_traffic
                        },
                        "traffic_periods": {
                            "last_2_days": {
                                "current": self._parse_bandwidth_string(
                                    bandwidth_stats.get('bandwidthLastTwoDays', {}).get('current', '0 B')
                                ),
                                "previous": self._parse_bandwidth_string(
                                    bandwidth_stats.get('bandwidthLastTwoDays', {}).get('previous', '0 B')
                                ),
                                "difference": bandwidth_stats.get('bandwidthLastTwoDays', {}).get('difference', '0 B')
                            },
                            "last_7_days": {
                                "current": self._parse_bandwidth_string(
                                    bandwidth_stats.get('bandwidthLastSevenDays', {}).get('current', '0 B')
                                ),
                                "previous": self._parse_bandwidth_string(
                                    bandwidth_stats.get('bandwidthLastSevenDays', {}).get('previous', '0 B')
                                ),
                                "difference": bandwidth_stats.get('bandwidthLastSevenDays', {}).get('difference', '0 B')
                            },
                            "last_30_days": {
                                "current": self._parse_bandwidth_string(
                                    bandwidth_stats.get('bandwidthLast30Days', {}).get('current', '0 B')
                                ),
                                "previous": self._parse_bandwidth_string(
                                    bandwidth_stats.get('bandwidthLast30Days', {}).get('previous', '0 B')
                                ),
                                "difference": bandwidth_stats.get('bandwidthLast30Days', {}).get('difference', '0 B')
                            },
                            "current_month": {
                                "current": self._parse_bandwidth_string(
                                    bandwidth_stats.get('bandwidthCalendarMonth', {}).get('current', '0 B')
                                ),
                                "previous": self._parse_bandwidth_string(
                                    bandwidth_stats.get('bandwidthCalendarMonth', {}).get('previous', '0 B')
                                ),
                                "difference": bandwidth_stats.get('bandwidthCalendarMonth', {}).get('difference', '0 B')
                            },
                            "current_year": {
                                "current": self._parse_bandwidth_string(
                                    bandwidth_stats.get('bandwidthCurrentYear', {}).get('current', '0 B')
                                ),
                                "previous": self._parse_bandwidth_string(
                                    bandwidth_stats.get('bandwidthCurrentYear', {}).get('previous', '0 B')
                                ),
                                "difference": bandwidth_stats.get('bandwidthCurrentYear', {}).get('difference', '0 B')
                            }
                        },
                        "nodes_realtime": realtime_usage,
                        "nodes_weekly": nodes_weekly_data,
                        "last_updated": datetime.now()
                    }
                    
                    logger.info(f"Статистика сформирована: пользователи={result['system']['total_users']}, общий трафик={total_user_traffic}")
                    return result
                
            except RemnaWaveAPIError as e:
                logger.error(f"Ошибка Remnawave API при получении статистики: {e}")
                return {"error": str(e)}
            except Exception as e:
                logger.error(f"Общая ошибка получения системной статистики: {e}")
                return {"error": f"Внутренняя ошибка сервера: {str(e)}"}

    
    def _parse_bandwidth_string(self, bandwidth_str: str) -> int:
            try:
                if not bandwidth_str or bandwidth_str == '0 B' or bandwidth_str == '0':
                    return 0
            
                bandwidth_str = bandwidth_str.replace(' ', '').upper()
            
                units = {
                    'B': 1,
                    'KB': 1024,
                    'MB': 1024 ** 2,
                    'GB': 1024 ** 3,
                    'TB': 1024 ** 4,
                    'KIB': 1024,          
                    'MIB': 1024 ** 2,
                    'GIB': 1024 ** 3,
                    'TIB': 1024 ** 4,
                    'KBPS': 1024,      
                    'MBPS': 1024 ** 2,
                    'GBPS': 1024 ** 3
                }
            
                match = re.match(r'([0-9.,]+)([A-Z]+)', bandwidth_str)
                if match:
                    value_str = match.group(1).replace(',', '.') 
                    value = float(value_str)
                    unit = match.group(2)
                
                    if unit in units:
                        result = int(value * units[unit])
                        logger.debug(f"Парсинг '{bandwidth_str}': {value} {unit} = {result} байт")
                        return result
                    else:
                        logger.warning(f"Неизвестная единица измерения: {unit}")
            
                logger.warning(f"Не удалось распарсить строку трафика: '{bandwidth_str}'")
                return 0
            
            except Exception as e:
                logger.error(f"Ошибка парсинга строки трафика '{bandwidth_str}': {e}")
                return 0
    
    async def get_all_nodes(self) -> List[Dict[str, Any]]:
        
        try:
            async with self.get_api_client() as api:
                nodes = await api.get_all_nodes()
                
                result = []
                for node in nodes:
                    result.append({
                        'uuid': node.uuid,
                        'name': node.name,
                        'address': node.address,
                        'country_code': node.country_code,
                        'is_connected': node.is_connected,
                        'is_disabled': node.is_disabled,
                        'is_node_online': node.is_node_online,
                        'is_xray_running': node.is_xray_running,
                        'users_online': node.users_online,
                        'traffic_used_bytes': node.traffic_used_bytes,
                        'traffic_limit_bytes': node.traffic_limit_bytes
                    })
                
                logger.info(f"✅ Получено {len(result)} нод из Remnawave")
                return result
                
        except Exception as e:
            logger.error(f"Ошибка получения нод из Remnawave: {e}")
            return []

    async def test_connection(self) -> bool:
        
        try:
            async with self.get_api_client() as api:
                stats = await api.get_system_stats()
                logger.info("✅ Соединение с Remnawave API работает")
                return True
                
        except Exception as e:
            logger.error(f"❌ Ошибка соединения с Remnawave API: {e}")
            return False
    
    async def get_node_details(self, node_uuid: str) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_api_client() as api:
                node = await api.get_node_by_uuid(node_uuid)
                
                if not node:
                    return None
                
                return {
                    "uuid": node.uuid,
                    "name": node.name,
                    "address": node.address,
                    "country_code": node.country_code,
                    "is_connected": node.is_connected,
                    "is_disabled": node.is_disabled,
                    "is_node_online": node.is_node_online,
                    "is_xray_running": node.is_xray_running,
                    "users_online": node.users_online or 0,
                    "traffic_used_bytes": node.traffic_used_bytes or 0,
                    "traffic_limit_bytes": node.traffic_limit_bytes or 0
                }
                
        except Exception as e:
            logger.error(f"Ошибка получения информации о ноде {node_uuid}: {e}")
            return None
    
    async def manage_node(self, node_uuid: str, action: str) -> bool:
        try:
            async with self.get_api_client() as api:
                if action == "enable":
                    await api.enable_node(node_uuid)
                elif action == "disable":
                    await api.disable_node(node_uuid)
                elif action == "restart":
                    await api.restart_node(node_uuid)
                else:
                    return False
                
                logger.info(f"✅ Действие {action} выполнено для ноды {node_uuid}")
                return True
                
        except Exception as e:
            logger.error(f"Ошибка управления нодой {node_uuid}: {e}")
            return False
    
    async def restart_all_nodes(self) -> bool:
        try:
            async with self.get_api_client() as api:
                result = await api.restart_all_nodes()
                
                if result:
                    logger.info("✅ Команда перезагрузки всех нод отправлена")
                
                return result
                
        except Exception as e:
            logger.error(f"Ошибка перезагрузки всех нод: {e}")
            return False

    async def update_squad_inbounds(self, squad_uuid: str, inbound_uuids: List[str]) -> bool:
        try:
            async with self.get_api_client() as api:
                data = {
                    'uuid': squad_uuid,
                    'inbounds': inbound_uuids
                }
                response = await api._make_request('PATCH', '/api/internal-squads', data)
                return True
        except Exception as e:
            logger.error(f"Error updating squad inbounds: {e}")
            return False
    
    async def get_all_squads(self) -> List[Dict[str, Any]]:
        
        try:
            async with self.get_api_client() as api:
                squads = await api.get_internal_squads()
                
                result = []
                for squad in squads:
                    result.append({
                        'uuid': squad.uuid,
                        'name': squad.name,
                        'members_count': squad.members_count,
                        'inbounds_count': squad.inbounds_count,
                        'inbounds': squad.inbounds
                    })
                
                logger.info(f"✅ Получено {len(result)} сквадов из Remnawave")
                return result
                
        except Exception as e:
            logger.error(f"Ошибка получения сквадов из Remnawave: {e}")
            return []
    
    async def create_squad(self, name: str, inbounds: List[str]) -> Optional[str]:
        try:
            async with self.get_api_client() as api:
                squad = await api.create_internal_squad(name, inbounds)
                
                logger.info(f"✅ Создан новый сквад: {name}")
                return squad.uuid
                
        except Exception as e:
            logger.error(f"Ошибка создания сквада {name}: {e}")
            return None
    
    async def update_squad(self, uuid: str, name: str = None, inbounds: List[str] = None) -> bool:
        try:
            async with self.get_api_client() as api:
                await api.update_internal_squad(uuid, name, inbounds)
                
                logger.info(f"✅ Обновлен сквад {uuid}")
                return True
                
        except Exception as e:
            logger.error(f"Ошибка обновления сквада {uuid}: {e}")
            return False
    
    async def delete_squad(self, uuid: str) -> bool:
        try:
            async with self.get_api_client() as api:
                result = await api.delete_internal_squad(uuid)
                
                if result:
                    logger.info(f"✅ Удален сквад {uuid}")
                
                return result
                
        except Exception as e:
            logger.error(f"Ошибка удаления сквада {uuid}: {e}")
            return False
    
    async def sync_users_from_panel(self, db: AsyncSession, sync_type: str = "all") -> Dict[str, int]:
        try:
            stats = {"created": 0, "updated": 0, "errors": 0, "deleted": 0}
            
            logger.info(f"🔄 Начинаем синхронизацию типа: {sync_type}")
            
            async with self.get_api_client() as api:
                panel_users = []
                start = 0
                size = 100 
                
                while True:
                    logger.info(f"📥 Загружаем пользователей: start={start}, size={size}")
                    
                    response = await api.get_all_users(start=start, size=size)
                    users_batch = response['users']
                    total_users = response['total']
                    
                    logger.info(f"📊 Получено {len(users_batch)} пользователей из {total_users}")
                    
                    for user_obj in users_batch:
                        user_dict = {
                            'uuid': user_obj.uuid,
                            'shortUuid': user_obj.short_uuid,
                            'username': user_obj.username,
                            'status': user_obj.status.value,
                            'telegramId': user_obj.telegram_id,
                            'expireAt': user_obj.expire_at.isoformat() + 'Z',
                            'trafficLimitBytes': user_obj.traffic_limit_bytes,
                            'usedTrafficBytes': user_obj.used_traffic_bytes,
                            'hwidDeviceLimit': user_obj.hwid_device_limit,
                            'subscriptionUrl': user_obj.subscription_url,
                            'subscriptionCryptoLink': user_obj.happ_crypto_link,
                            'activeInternalSquads': user_obj.active_internal_squads
                        }
                        panel_users.append(user_dict)
                    
                    if len(users_batch) < size:
                        break
                        
                    start += size
                    
                    if start > total_users:
                        break
                
                logger.info(f"✅ Всего загружено пользователей из панели: {len(panel_users)}")
            
            bot_users = await get_users_list(db, offset=0, limit=10000)
            bot_users_by_telegram_id = {user.telegram_id: user for user in bot_users}
            
            logger.info(f"📊 Пользователей в боте: {len(bot_users)}")
            
            panel_users_with_tg = [
                user for user in panel_users 
                if user.get('telegramId') is not None
            ]
            
            logger.info(f"📊 Пользователей в панели с Telegram ID: {len(panel_users_with_tg)}")
            
            panel_telegram_ids = set()
            
            for i, panel_user in enumerate(panel_users_with_tg):
                try:
                    telegram_id = panel_user.get('telegramId')
                    if not telegram_id:
                        continue
                    
                    panel_telegram_ids.add(telegram_id)
                    
                    if (i + 1) % 10 == 0: 
                        logger.info(f"🔄 Обрабатываем пользователя {i+1}/{len(panel_users_with_tg)}: {telegram_id}")
                    
                    db_user = bot_users_by_telegram_id.get(telegram_id)
                    
                    if not db_user:
                        if sync_type in ["new_only", "all"]:
                            logger.info(f"🆕 Создание пользователя для telegram_id {telegram_id}")
                            
                            from app.database.crud.user import create_user
                            
                            db_user = await create_user(
                                db=db,
                                telegram_id=telegram_id,
                                username=panel_user.get('username') or f"user_{telegram_id}",
                                first_name=f"Panel User {telegram_id}",
                                language="ru"
                            )
                            
                            await update_user(db, db_user, remnawave_uuid=panel_user.get('uuid'))
                            
                            await self._create_subscription_from_panel_data(db, db_user, panel_user)
                            
                            stats["created"] += 1
                            logger.info(f"✅ Создан пользователь {telegram_id} с подпиской")
                    
                    else:
                        if sync_type in ["update_only", "all"]:
                            logger.debug(f"🔄 Обновление пользователя {telegram_id}")
                            
                            if not db_user.remnawave_uuid:
                                await update_user(db, db_user, remnawave_uuid=panel_user.get('uuid'))
                            
                            await self._update_subscription_from_panel_data(db, db_user, panel_user)
                            
                            stats["updated"] += 1
                            logger.debug(f"✅ Обновлён пользователь {telegram_id}")
                            
                except Exception as user_error:
                    logger.error(f"❌ Ошибка обработки пользователя {telegram_id}: {user_error}")
                    stats["errors"] += 1
                    continue
            
            if sync_type == "all":
                logger.info("🗑️ Деактивация подписок пользователей, отсутствующих в панели...")
                
                for telegram_id, db_user in bot_users_by_telegram_id.items():
                    if telegram_id not in panel_telegram_ids and db_user.subscription:
                        try:
                            logger.info(f"🗑️ Деактивация подписки пользователя {telegram_id} (нет в панели)")
                            
                            subscription = db_user.subscription
                            
                            if db_user.remnawave_uuid:
                                try:
                                    async with self.get_api_client() as api:
                                        devices_reset = await api.reset_user_devices(db_user.remnawave_uuid)
                                        if devices_reset:
                                            logger.info(f"🔧 Сброшены HWID устройства для пользователя {telegram_id}")
                                except Exception as hwid_error:
                                    logger.error(f"❌ Ошибка сброса HWID устройств для {telegram_id}: {hwid_error}")
                            
                            try:
                                from sqlalchemy import delete
                                from app.database.models import SubscriptionServer

                                await decrement_subscription_server_counts(db, subscription)

                                await db.execute(
                                    delete(SubscriptionServer).where(
                                        SubscriptionServer.subscription_id == subscription.id
                                    )
                                )
                                logger.info(f"🗑️ Удалены серверы подписки для {telegram_id}")
                            except Exception as servers_error:
                                logger.warning(f"⚠️ Не удалось удалить серверы подписки: {servers_error}")
                            
                            from app.database.models import SubscriptionStatus
                            
                            subscription.status = SubscriptionStatus.DISABLED.value
                            subscription.is_trial = True 
                            subscription.end_date = datetime.utcnow()
                            subscription.traffic_limit_gb = 0
                            subscription.traffic_used_gb = 0.0
                            subscription.device_limit = 1
                            subscription.connected_squads = []
                            subscription.autopay_enabled = False
                            subscription.remnawave_short_uuid = None
                            subscription.subscription_url = ""
                            subscription.subscription_crypto_link = ""
                            
                            db_user.remnawave_uuid = None
                            
                            await db.commit()
                            
                            stats["deleted"] += 1
                            logger.info(f"✅ Деактивирована подписка пользователя {telegram_id} (сохранен баланс)")
                            
                        except Exception as delete_error:
                            logger.error(f"❌ Ошибка деактивации подписки {telegram_id}: {delete_error}")
                            stats["errors"] += 1
                            await db.rollback()
            
            logger.info(f"🎯 Синхронизация завершена: создано {stats['created']}, обновлено {stats['updated']}, деактивировано {stats['deleted']}, ошибок {stats['errors']}")
            return stats
        
        except Exception as e:
            logger.error(f"❌ Критическая ошибка синхронизации пользователей: {e}")
            return {"created": 0, "updated": 0, "errors": 1, "deleted": 0}

    async def _create_subscription_from_panel_data(self, db: AsyncSession, user, panel_user):
        try:
            from app.database.crud.subscription import create_subscription
            from app.database.models import SubscriptionStatus
        
            expire_at_str = panel_user.get('expireAt', '')
            expire_at = self._parse_remnawave_date(expire_at_str)
        
            panel_status = panel_user.get('status', 'ACTIVE')
            current_time = self._now_in_panel_timezone()
        
            if panel_status == 'ACTIVE' and expire_at > current_time:
                status = SubscriptionStatus.ACTIVE
            elif expire_at <= current_time:
                status = SubscriptionStatus.EXPIRED
            else:
                status = SubscriptionStatus.DISABLED
        
            traffic_limit_bytes = panel_user.get('trafficLimitBytes', 0)
            traffic_limit_gb = traffic_limit_bytes // (1024**3) if traffic_limit_bytes > 0 else 0
        
            used_traffic_bytes = panel_user.get('usedTrafficBytes', 0)
            traffic_used_gb = used_traffic_bytes / (1024**3)
        
            active_squads = panel_user.get('activeInternalSquads', [])
            squad_uuids = []
            if isinstance(active_squads, list):
                for squad in active_squads:
                    if isinstance(squad, dict) and 'uuid' in squad:
                        squad_uuids.append(squad['uuid'])
                    elif isinstance(squad, str):
                        squad_uuids.append(squad)
        
            subscription_data = {
                'user_id': user.id,
                'status': status.value,
                'is_trial': False,
                'end_date': expire_at,
                'traffic_limit_gb': traffic_limit_gb,
                'traffic_used_gb': traffic_used_gb,
                'device_limit': panel_user.get('hwidDeviceLimit', 1) or 1,
                'connected_squads': squad_uuids,
                'remnawave_short_uuid': panel_user.get('shortUuid'),
                'subscription_url': panel_user.get('subscriptionUrl', ''),
                'subscription_crypto_link': (
                    panel_user.get('subscriptionCryptoLink')
                    or (panel_user.get('happ') or {}).get('cryptoLink', '')
                )
            }
        
            subscription = await create_subscription(db, **subscription_data)
            logger.info(f"✅ Создана подписка для пользователя {user.telegram_id} до {expire_at}")
        
        except Exception as e:
            logger.error(f"❌ Ошибка создания подписки для пользователя {user.telegram_id}: {e}")
            try:
                from app.database.crud.subscription import create_subscription
                from app.database.models import SubscriptionStatus
            
                basic_subscription = await create_subscription(
                    db=db,
                    user_id=user.id,
                    status=SubscriptionStatus.ACTIVE.value,
                    is_trial=False,
                    end_date=self._now_in_panel_timezone() + timedelta(days=30),
                    traffic_limit_gb=0,
                    traffic_used_gb=0.0,
                    device_limit=1,
                    connected_squads=[],
                    remnawave_short_uuid=panel_user.get('shortUuid'),
                    subscription_url=panel_user.get('subscriptionUrl', ''),
                    subscription_crypto_link=(
                        panel_user.get('subscriptionCryptoLink')
                        or (panel_user.get('happ') or {}).get('cryptoLink', '')
                    )
                )
                logger.info(f"✅ Создана базовая подписка для пользователя {user.telegram_id}")
            except Exception as basic_error:
                logger.error(f"❌ Ошибка создания базовой подписки: {basic_error}")

    async def _update_subscription_from_panel_data(self, db: AsyncSession, user, panel_user):
        try:
            from app.database.crud.subscription import get_subscription_by_user_id
            from app.database.models import SubscriptionStatus
        
            subscription = await get_subscription_by_user_id(db, user.id)
            
            if not subscription:
                await self._create_subscription_from_panel_data(db, user, panel_user)
                return
        
            panel_status = panel_user.get('status', 'ACTIVE')
            expire_at_str = panel_user.get('expireAt', '')
            
            if expire_at_str:
                expire_at = self._parse_remnawave_date(expire_at_str)
                
                if abs((subscription.end_date - expire_at).total_seconds()) > 60: 
                    subscription.end_date = expire_at
                    logger.debug(f"Обновлена дата окончания подписки до {expire_at}")
            
            current_time = self._now_in_panel_timezone()
            if panel_status == 'ACTIVE' and subscription.end_date > current_time:
                new_status = SubscriptionStatus.ACTIVE.value
            elif subscription.end_date <= current_time:
                new_status = SubscriptionStatus.EXPIRED.value
            elif panel_status == 'DISABLED':
                new_status = SubscriptionStatus.DISABLED.value
            else:
                new_status = subscription.status 
            
            if subscription.status != new_status:
                subscription.status = new_status
                logger.debug(f"Обновлен статус подписки: {new_status}")
        
            used_traffic_bytes = panel_user.get('usedTrafficBytes', 0)
            traffic_used_gb = used_traffic_bytes / (1024**3)
        
            if abs(subscription.traffic_used_gb - traffic_used_gb) > 0.01:
                subscription.traffic_used_gb = traffic_used_gb
                logger.debug(f"Обновлен использованный трафик: {traffic_used_gb} GB")
            
            traffic_limit_bytes = panel_user.get('trafficLimitBytes', 0)
            traffic_limit_gb = traffic_limit_bytes // (1024**3) if traffic_limit_bytes > 0 else 0
            
            if subscription.traffic_limit_gb != traffic_limit_gb:
                subscription.traffic_limit_gb = traffic_limit_gb
                logger.debug(f"Обновлен лимит трафика: {traffic_limit_gb} GB")
            
            device_limit = panel_user.get('hwidDeviceLimit', 1) or 1
            if subscription.device_limit != device_limit:
                subscription.device_limit = device_limit
                logger.debug(f"Обновлен лимит устройств: {device_limit}")
        
            if not subscription.remnawave_short_uuid:
                subscription.remnawave_short_uuid = panel_user.get('shortUuid')
        
            panel_url = panel_user.get('subscriptionUrl', '')
            if not subscription.subscription_url or subscription.subscription_url != panel_url:
                subscription.subscription_url = panel_url

            panel_crypto_link = (
                panel_user.get('subscriptionCryptoLink')
                or (panel_user.get('happ') or {}).get('cryptoLink', '')
            )
            if panel_crypto_link and subscription.subscription_crypto_link != panel_crypto_link:
                subscription.subscription_crypto_link = panel_crypto_link
        
            active_squads = panel_user.get('activeInternalSquads', [])
            squad_uuids = []
            if isinstance(active_squads, list):
                for squad in active_squads:
                    if isinstance(squad, dict) and 'uuid' in squad:
                        squad_uuids.append(squad['uuid'])
                    elif isinstance(squad, str):
                        squad_uuids.append(squad)
        
            current_squads = set(subscription.connected_squads or [])
            new_squads = set(squad_uuids)
            
            if current_squads != new_squads:
                subscription.connected_squads = squad_uuids
                logger.debug(f"Обновлены подключенные сквады: {squad_uuids}")
        
            await db.commit()
            logger.debug(f"✅ Обновлена подписка для пользователя {user.telegram_id}")
        
        except Exception as e:
            logger.error(f"❌ Ошибка обновления подписки для пользователя {user.telegram_id}: {e}")
            await db.rollback()
    
    async def sync_users_to_panel(self, db: AsyncSession) -> Dict[str, int]:
        try:
            stats = {"created": 0, "updated": 0, "errors": 0}
            
            users = await get_users_list(db, offset=0, limit=10000)
            
            async with self.get_api_client() as api:
                for user in users:
                    if not user.subscription:
                        continue
                    
                    try:
                        subscription = user.subscription
                        
                        if user.remnawave_uuid:
                            await api.update_user(
                                uuid=user.remnawave_uuid,
                                status=UserStatus.ACTIVE if subscription.is_active else UserStatus.EXPIRED,
                                expire_at=subscription.end_date,
                                traffic_limit_bytes=subscription.traffic_limit_gb * (1024**3) if subscription.traffic_limit_gb > 0 else 0,
                                traffic_limit_strategy=TrafficLimitStrategy.MONTH,
                                hwid_device_limit=subscription.device_limit,
                                description=settings.format_remnawave_user_description(
                                    full_name=user.full_name,
                                    username=user.username,
                                    telegram_id=user.telegram_id
                                ),
                                active_internal_squads=subscription.connected_squads
                            )
                            stats["updated"] += 1
                        else:
                            username = f"user_{user.telegram_id}"
                            
                            new_user = await api.create_user(
                                username=username,
                                expire_at=subscription.end_date,
                                status=UserStatus.ACTIVE if subscription.is_active else UserStatus.EXPIRED,
                                traffic_limit_bytes=subscription.traffic_limit_gb * (1024**3) if subscription.traffic_limit_gb > 0 else 0,
                                traffic_limit_strategy=TrafficLimitStrategy.MONTH,
                                telegram_id=user.telegram_id,
                                hwid_device_limit=subscription.device_limit,
                                description=settings.format_remnawave_user_description(
                                    full_name=user.full_name,
                                    username=user.username,
                                    telegram_id=user.telegram_id
                                ),
                                active_internal_squads=subscription.connected_squads
                            )
                            
                            await update_user(db, user, remnawave_uuid=new_user.uuid)
                            subscription.remnawave_short_uuid = new_user.short_uuid
                            await db.commit()
                            
                            stats["created"] += 1
                            
                    except Exception as e:
                        logger.error(f"Ошибка синхронизации пользователя {user.telegram_id} в панель: {e}")
                        stats["errors"] += 1
            
            logger.info(f"✅ Синхронизация в панель завершена: создано {stats['created']}, обновлено {stats['updated']}, ошибок {stats['errors']}")
            return stats
            
        except Exception as e:
            logger.error(f"Ошибка синхронизации пользователей в панель: {e}")
            return {"created": 0, "updated": 0, "errors": 1}
    
    async def get_user_traffic_stats(self, telegram_id: int) -> Optional[Dict[str, Any]]:
        try:
            async with self.get_api_client() as api:
                users = await api.get_user_by_telegram_id(telegram_id)
                
                if not users:
                    return None
                
                user = users[0]
                
                return {
                    "used_traffic_bytes": user.used_traffic_bytes,
                    "used_traffic_gb": user.used_traffic_bytes / (1024**3),
                    "lifetime_used_traffic_bytes": user.lifetime_used_traffic_bytes,
                    "lifetime_used_traffic_gb": user.lifetime_used_traffic_bytes / (1024**3),
                    "traffic_limit_bytes": user.traffic_limit_bytes,
                    "traffic_limit_gb": user.traffic_limit_bytes / (1024**3) if user.traffic_limit_bytes > 0 else 0,
                    "subscription_url": user.subscription_url
                }
                
        except Exception as e:
            logger.error(f"Ошибка получения статистики трафика для пользователя {telegram_id}: {e}")
            return None
    
    async def test_api_connection(self) -> Dict[str, Any]:
        if not self.is_configured:
            return {
                "status": "not_configured",
                "message": self.configuration_error or "RemnaWave API не настроен",
                "api_url": settings.REMNAWAVE_API_URL,
            }
        try:
            async with self.get_api_client() as api:
                system_stats = await api.get_system_stats()

                return {
                    "status": "connected",
                    "message": "Подключение успешно",
                    "api_url": settings.REMNAWAVE_API_URL,
                    "system_info": system_stats
                }

        except RemnaWaveAPIError as e:
            return {
                "status": "error",
                "message": f"Ошибка API: {e.message}",
                "status_code": e.status_code,
                "api_url": settings.REMNAWAVE_API_URL
            }
        except RemnaWaveConfigurationError as e:
            return {
                "status": "not_configured",
                "message": str(e),
                "api_url": settings.REMNAWAVE_API_URL,
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"Ошибка подключения: {str(e)}",
                "api_url": settings.REMNAWAVE_API_URL
            }
    
    async def get_nodes_realtime_usage(self) -> List[Dict[str, Any]]:
        try:
            async with self.get_api_client() as api:
                usage_data = await api.get_nodes_realtime_usage()
                return usage_data
                
        except Exception as e:
            logger.error(f"Ошибка получения актуального использования нод: {e}")
            return []

    async def get_squad_details(self, squad_uuid: str) -> Optional[Dict]:
        try:
            async with self.get_api_client() as api:
                squad = await api.get_internal_squad_by_uuid(squad_uuid)
                if squad:
                    return {
                        'uuid': squad.uuid,
                        'name': squad.name,
                        'members_count': squad.members_count,
                        'inbounds_count': squad.inbounds_count,
                        'inbounds': squad.inbounds
                    }
                return None
        except Exception as e:
            logger.error(f"Error getting squad details: {e}")
            return None

    async def add_all_users_to_squad(self, squad_uuid: str) -> bool:
        try:
            async with self.get_api_client() as api:
                response = await api._make_request('POST', f'/api/internal-squads/{squad_uuid}/bulk-actions/add-users')
                return response.get('response', {}).get('eventSent', False)
        except Exception as e:
            logger.error(f"Error adding users to squad: {e}")
            return False

    async def remove_all_users_from_squad(self, squad_uuid: str) -> bool:
        try:
            async with self.get_api_client() as api:
                response = await api._make_request('DELETE', f'/api/internal-squads/{squad_uuid}/bulk-actions/remove-users')
                return response.get('response', {}).get('eventSent', False)
        except Exception as e:
            logger.error(f"Error removing users from squad: {e}")
            return False

    async def get_all_inbounds(self) -> List[Dict]:
        try:
            async with self.get_api_client() as api:
                response = await api._make_request('GET', '/api/config-profiles/inbounds')
                inbounds_data = response.get('response', {}).get('inbounds', [])
            
                return [
                    {
                        'uuid': inbound['uuid'],
                        'tag': inbound['tag'],
                        'type': inbound['type'],
                        'network': inbound.get('network'),
                        'security': inbound.get('security'),
                        'port': inbound.get('port')
                    }
                    for inbound in inbounds_data
                ]
        except Exception as e:
            logger.error(f"Error getting all inbounds: {e}")
            return []

    async def rename_squad(self, squad_uuid: str, new_name: str) -> bool:
        try:
            async with self.get_api_client() as api:
                data = {
                    'uuid': squad_uuid,
                    'name': new_name
                }
                response = await api._make_request('PATCH', '/api/internal-squads', data)
                return True
        except Exception as e:
            logger.error(f"Error renaming squad: {e}")
            return False

    async def get_node_user_usage_by_range(self, node_uuid: str, start_date, end_date) -> List[Dict[str, Any]]:
        try:
            async with self.get_api_client() as api:
                start_str = start_date.isoformat() + "Z"
                end_str = end_date.isoformat() + "Z"
                
                params = {
                    'start': start_str,
                    'end': end_str
                }
                
                usage_data = await api._make_request(
                    'GET', 
                    f'/api/nodes/usage/{node_uuid}/users/range',
                    params=params
                )
                
                return usage_data.get('response', [])
                
        except Exception as e:
            logger.error(f"Ошибка получения статистики использования ноды {node_uuid}: {e}")
            return []

    async def get_node_statistics(self, node_uuid: str) -> Optional[Dict[str, Any]]:
        try:
            node = await self.get_node_details(node_uuid)
            if not node:
                return None
            
            realtime_stats = await self.get_nodes_realtime_usage()
            
            node_realtime = None
            for stats in realtime_stats:
                if stats.get('nodeUuid') == node_uuid:
                    node_realtime = stats
                    break
            
            end_date = datetime.now()
            start_date = end_date - timedelta(days=7)
            
            usage_history = await self.get_node_user_usage_by_range(
                node_uuid, start_date, end_date
            )
            
            return {
                'node': node,
                'realtime': node_realtime,
                'usage_history': usage_history,
                'last_updated': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Ошибка получения статистики ноды {node_uuid}: {e}")

    async def validate_user_data_before_sync(self, panel_user) -> bool:
        try:
            if not panel_user.telegram_id:
                logger.debug(f"Нет telegram_id для пользователя {panel_user.uuid}")
                return False
            
            if not panel_user.uuid:
                logger.debug(f"Нет UUID для пользователя {panel_user.telegram_id}")
                return False
            
            if panel_user.telegram_id <= 0:
                logger.debug(f"Некорректный telegram_id: {panel_user.telegram_id}")
                return False
            
            return True
        
        except Exception as e:
            logger.error(f"Ошибка валидации данных пользователя: {e}")
            return False

    async def force_cleanup_user_data(self, db: AsyncSession, user: User) -> bool:
        try:
            logger.info(f"🗑️ ПРИНУДИТЕЛЬНАЯ полная очистка данных пользователя {user.telegram_id}")
            
            if user.remnawave_uuid:
                try:
                    async with self.get_api_client() as api:
                        devices_reset = await api.reset_user_devices(user.remnawave_uuid)
                        if devices_reset:
                            logger.info(f"🔧 Сброшены HWID устройства для {user.telegram_id}")
                except Exception as hwid_error:
                    logger.warning(f"⚠️ Ошибка сброса HWID устройств: {hwid_error}")
            
            try:
                from sqlalchemy import delete
                from app.database.models import (
                    SubscriptionServer, Transaction, ReferralEarning, 
                    PromoCodeUse, SubscriptionStatus
                )
                
                if user.subscription:
                    await decrement_subscription_server_counts(db, user.subscription)

                    await db.execute(
                        delete(SubscriptionServer).where(
                            SubscriptionServer.subscription_id == user.subscription.id
                        )
                    )
                    logger.info(f"🗑️ Удалены серверы подписки для {user.telegram_id}")
                
                await db.execute(
                    delete(Transaction).where(Transaction.user_id == user.id)
                )
                logger.info(f"🗑️ Удалены транзакции для {user.telegram_id}")
                
                await db.execute(
                    delete(ReferralEarning).where(ReferralEarning.user_id == user.id)
                )
                await db.execute(
                    delete(ReferralEarning).where(ReferralEarning.referral_id == user.id)
                )
                logger.info(f"🗑️ Удалены реферальные доходы для {user.telegram_id}")
                
                await db.execute(
                    delete(PromoCodeUse).where(PromoCodeUse.user_id == user.id)
                )
                logger.info(f"🗑️ Удалены использования промокодов для {user.telegram_id}")
                
            except Exception as records_error:
                logger.error(f"❌ Ошибка удаления связанных записей: {records_error}")
            
            try:
                
                user.balance_kopeks = 0
                user.remnawave_uuid = None
                user.has_had_paid_subscription = False
                user.used_promocodes = 0
                user.updated_at = self._now_in_panel_timezone()
                
                if user.subscription:
                    user.subscription.status = SubscriptionStatus.DISABLED.value
                    user.subscription.is_trial = True
                    user.subscription.end_date = self._now_in_panel_timezone()
                    user.subscription.traffic_limit_gb = 0
                    user.subscription.traffic_used_gb = 0.0
                    user.subscription.device_limit = 1
                    user.subscription.connected_squads = []
                    user.subscription.autopay_enabled = False
                    user.subscription.autopay_days_before = settings.DEFAULT_AUTOPAY_DAYS_BEFORE
                    user.subscription.remnawave_short_uuid = None
                    user.subscription.subscription_url = ""
                    user.subscription.subscription_crypto_link = ""
                    user.subscription.updated_at = self._now_in_panel_timezone()
                
                await db.commit()
                
                logger.info(f"✅ ПРИНУДИТЕЛЬНО очищены ВСЕ данные пользователя {user.telegram_id}")
                return True
                
            except Exception as cleanup_error:
                logger.error(f"❌ Ошибка финальной очистки пользователя: {cleanup_error}")
                await db.rollback()
                return False
        
        except Exception as e:
            logger.error(f"❌ Критическая ошибка принудительной очистки пользователя {user.telegram_id}: {e}")
            await db.rollback()
            return False

    async def cleanup_orphaned_subscriptions(self, db: AsyncSession) -> Dict[str, int]:
        try:
            stats = {"deactivated": 0, "errors": 0, "checked": 0}
        
            logger.info("🧹 Начинаем усиленную очистку неактуальных подписок...")
        
            async with self.get_api_client() as api:
                panel_users_data = await api._make_request('GET', '/api/users')
                panel_users = panel_users_data['response']['users']
        
            panel_telegram_ids = set()
            for panel_user in panel_users:
                telegram_id = panel_user.get('telegramId')
                if telegram_id:
                    panel_telegram_ids.add(telegram_id)
        
            logger.info(f"📊 Найдено {len(panel_telegram_ids)} пользователей в панели")
        
            from app.database.crud.subscription import get_all_subscriptions
            from app.database.models import SubscriptionStatus
        
            page = 1
            limit = 100
        
            while True:
                subscriptions, total_count = await get_all_subscriptions(db, page, limit)
                
                if not subscriptions:
                    break
            
                for subscription in subscriptions:
                    try:
                        stats["checked"] += 1
                        user = subscription.user
                    
                        if subscription.status == SubscriptionStatus.DISABLED.value:
                            continue
                    
                        if user.telegram_id not in panel_telegram_ids:
                            logger.info(f"🗑️ ПОЛНАЯ деактивация подписки пользователя {user.telegram_id} (отсутствует в панели)")
                            
                            cleanup_success = await self.force_cleanup_user_data(db, user)
                            
                            if cleanup_success:
                                stats["deactivated"] += 1
                            else:
                                stats["errors"] += 1
                        
                    except Exception as sub_error:
                        logger.error(f"❌ Ошибка обработки подписки {subscription.id}: {sub_error}")
                        stats["errors"] += 1
            
                page += 1
                if len(subscriptions) < limit:
                    break
        
            logger.info(f"🧹 Усиленная очистка завершена: проверено {stats['checked']}, деактивировано {stats['deactivated']}, ошибок {stats['errors']}")
            return stats
        
        except Exception as e:
            logger.error(f"❌ Критическая ошибка усиленной очистки подписок: {e}")
            return {"deactivated": 0, "errors": 1, "checked": 0}


    async def sync_subscription_statuses(self, db: AsyncSession) -> Dict[str, int]:
        try:
            stats = {"updated": 0, "errors": 0, "checked": 0}
        
            logger.info("🔄 Начинаем синхронизацию статусов подписок...")
        
            async with self.get_api_client() as api:
                panel_users_data = await api._make_request('GET', '/api/users')
                panel_users = panel_users_data['response']['users']
        
            panel_users_dict = {}
            for panel_user in panel_users:
                telegram_id = panel_user.get('telegramId')
                if telegram_id:
                    panel_users_dict[telegram_id] = panel_user
        
            logger.info(f"📊 Найдено {len(panel_users_dict)} пользователей в панели для синхронизации")
        
            from app.database.crud.subscription import get_all_subscriptions
            from app.database.models import SubscriptionStatus
        
            page = 1
            limit = 100
        
            while True:
                subscriptions, total_count = await get_all_subscriptions(db, page, limit)
            
                if not subscriptions:
                    break
            
                for subscription in subscriptions:
                    try:
                        stats["checked"] += 1
                        user = subscription.user
                    
                        panel_user = panel_users_dict.get(user.telegram_id)
                    
                        if panel_user:
                            await self._update_subscription_from_panel_data(db, user, panel_user)
                            stats["updated"] += 1
                        else:
                            if subscription.status != SubscriptionStatus.DISABLED.value:
                                logger.info(f"🗑️ Деактивируем подписку пользователя {user.telegram_id} (нет в панели)")
                            
                                from app.database.crud.subscription import deactivate_subscription
                                await deactivate_subscription(db, subscription)
                                stats["updated"] += 1
                        
                    except Exception as sub_error:
                        logger.error(f"❌ Ошибка синхронизации подписки {subscription.id}: {sub_error}")
                        stats["errors"] += 1
            
                page += 1
                if len(subscriptions) < limit:
                    break
        
            logger.info(f"🔄 Синхронизация статусов завершена: проверено {stats['checked']}, обновлено {stats['updated']}, ошибок {stats['errors']}")
            return stats
        
        except Exception as e:
            logger.error(f"❌ Критическая ошибка синхронизации статусов: {e}")
            return {"updated": 0, "errors": 1, "checked": 0}


    async def validate_and_fix_subscriptions(self, db: AsyncSession) -> Dict[str, int]:
        try:
            stats = {"fixed": 0, "errors": 0, "checked": 0, "issues_found": 0}
        
            logger.info("🔍 Начинаем валидацию подписок...")
            
            from app.database.crud.subscription import get_all_subscriptions
            from app.database.models import SubscriptionStatus
        
            page = 1
            limit = 100
        
            while True:
                subscriptions, total_count = await get_all_subscriptions(db, page, limit)
            
                if not subscriptions:
                    break
            
                for subscription in subscriptions:
                    try:
                        stats["checked"] += 1
                        user = subscription.user
                        issues_fixed = 0
                    
                        current_time = self._now_in_panel_timezone()
                        if subscription.end_date <= current_time and subscription.status == SubscriptionStatus.ACTIVE.value:
                            logger.info(f"🔧 Исправляем статус просроченной подписки {user.telegram_id}")
                            subscription.status = SubscriptionStatus.EXPIRED.value
                            issues_fixed += 1
                
                        if not subscription.remnawave_short_uuid and user.remnawave_uuid:
                            try:
                                async with self.get_api_client() as api:
                                    rw_user = await api.get_user_by_uuid(user.remnawave_uuid)
                                    if rw_user:
                                        subscription.remnawave_short_uuid = rw_user.short_uuid
                                        subscription.subscription_url = rw_user.subscription_url
                                        subscription.subscription_crypto_link = rw_user.happ_crypto_link
                                        logger.info(f"🔧 Восстановлены данные Remnawave для {user.telegram_id}")
                                        issues_fixed += 1
                            except Exception as rw_error:
                                logger.warning(f"⚠️ Не удалось получить данные Remnawave для {user.telegram_id}: {rw_error}")
                    
                        if subscription.traffic_limit_gb < 0:
                            subscription.traffic_limit_gb = 0
                            logger.info(f"🔧 Исправлен некорректный лимит трафика для {user.telegram_id}")
                            issues_fixed += 1
                    
                        if subscription.traffic_used_gb < 0:
                            subscription.traffic_used_gb = 0.0
                            logger.info(f"🔧 Исправлено некорректное использование трафика для {user.telegram_id}")
                            issues_fixed += 1
                    
                        if subscription.device_limit <= 0:
                            subscription.device_limit = 1
                            logger.info(f"🔧 Исправлен лимит устройств для {user.telegram_id}")
                            issues_fixed += 1
                    
                        if subscription.connected_squads is None:
                            subscription.connected_squads = []
                            logger.info(f"🔧 Инициализирован список сквадов для {user.telegram_id}")
                            issues_fixed += 1
                    
                        if issues_fixed > 0:
                            stats["issues_found"] += issues_fixed
                            stats["fixed"] += 1
                            await db.commit()
                        
                    except Exception as sub_error:
                        logger.error(f"❌ Ошибка валидации подписки {subscription.id}: {sub_error}")
                        stats["errors"] += 1
                        await db.rollback()
            
                page += 1
                if len(subscriptions) < limit:
                    break
        
            logger.info(f"🔍 Валидация завершена: проверено {stats['checked']}, исправлено подписок {stats['fixed']}, найдено проблем {stats['issues_found']}, ошибок {stats['errors']}")
            return stats
        
        except Exception as e:
            logger.error(f"❌ Критическая ошибка валидации: {e}")
            return {"fixed": 0, "errors": 1, "checked": 0, "issues_found": 0}


    async def get_sync_recommendations(self, db: AsyncSession) -> Dict[str, Any]:
        try:
            recommendations = {
                "should_sync": False,
                "sync_type": "none",
                "reasons": [],
                "priority": "low",
                "estimated_time": "1-2 минуты"
            }
        
            from app.database.crud.user import get_users_list
            bot_users = await get_users_list(db, offset=0, limit=10000)
        
            users_without_uuid = sum(1 for user in bot_users if not user.remnawave_uuid and user.subscription)
        
            from app.database.crud.subscription import get_expired_subscriptions
            expired_subscriptions = await get_expired_subscriptions(db)
            active_expired = sum(1 for sub in expired_subscriptions if sub.status == "active")
        
            if users_without_uuid > 10:
                recommendations["should_sync"] = True
                recommendations["sync_type"] = "all"
                recommendations["priority"] = "high"
                recommendations["reasons"].append(f"Найдено {users_without_uuid} пользователей без связи с Remnawave")
                recommendations["estimated_time"] = "3-5 минут"
        
            if active_expired > 5:
                recommendations["should_sync"] = True
                if recommendations["sync_type"] == "none":
                    recommendations["sync_type"] = "update_only"
                recommendations["priority"] = "medium" if recommendations["priority"] == "low" else recommendations["priority"]
                recommendations["reasons"].append(f"Найдено {active_expired} активных подписок с истекшим сроком")
        
            if not recommendations["should_sync"]:
                recommendations["sync_type"] = "update_only"
                recommendations["reasons"].append("Рекомендуется регулярная синхронизация данных")
                recommendations["estimated_time"] = "1-2 минуты"
        
            return recommendations
        
        except Exception as e:
            logger.error(f"❌ Ошибка получения рекомендаций: {e}")
            return {
                "should_sync": True,
                "sync_type": "all",
                "reasons": ["Ошибка анализа - рекомендуется полная синхронизация"],
                "priority": "medium",
                "estimated_time": "3-5 минут"
            }

    async def monitor_panel_status(self, bot) -> Dict[str, Any]:
        try:
            from app.utils.cache import cache
            previous_status = await cache.get("remnawave_panel_status") or "unknown"
                
            status_result = await self.check_panel_health()
            current_status = status_result.get("status", "offline")
                
            if current_status != previous_status and previous_status != "unknown":
                await self._send_status_change_notification(
                    bot, 
                    previous_status, 
                    current_status, 
                    status_result
                )
                
            await cache.set("remnawave_panel_status", current_status, expire=300)
                
            return status_result
                
        except Exception as e:
            logger.error(f"Ошибка мониторинга статуса панели Remnawave: {e}")
            return {"status": "error", "error": str(e)}
        

        
    async def _send_status_change_notification(
        self, 
        bot, 
        old_status: str, 
        new_status: str, 
        status_data: Dict[str, Any]
    ):
        try:
            from app.services.admin_notification_service import AdminNotificationService
                
            notification_service = AdminNotificationService(bot)
                
            details = {
                "api_url": status_data.get("api_url"),
                "response_time": status_data.get("response_time"),
                "last_check": status_data.get("last_check"),
                "users_online": status_data.get("users_online"),
                "nodes_online": status_data.get("nodes_online"),
                "total_nodes": status_data.get("total_nodes"),
                "old_status": old_status
            }
                
            if new_status == "offline":
                details["error"] = status_data.get("api_error")
            elif new_status == "degraded":
                issues = []
                if status_data.get("response_time", 0) > 10:
                    issues.append(f"Медленный отклик API ({status_data.get('response_time')}с)")
                if status_data.get("nodes_health") == "unhealthy":
                    issues.append(f"Проблемы с нодами ({status_data.get('nodes_online')}/{status_data.get('total_nodes')} онлайн)")
                details["issues"] = issues
                
            await notification_service.send_remnawave_panel_status_notification(
                new_status, 
                details
            )
                
            logger.info(f"Отправлено уведомление об изменении статуса панели: {old_status} -> {new_status}")
                
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления об изменении статуса: {e}")
        

        
    async def send_manual_status_notification(self, bot, status: str, message: str = ""):
        try:
            from app.services.admin_notification_service import AdminNotificationService
                
            notification_service = AdminNotificationService(bot)
                
            details = {
                "api_url": settings.REMNAWAVE_API_URL,
                "last_check": datetime.utcnow(),
                "manual_message": message
            }
                
            if status == "maintenance":
                details["maintenance_reason"] = message or "Плановое обслуживание"
                
            await notification_service.send_remnawave_panel_status_notification(status, details)
                
            logger.info(f"Отправлено ручное уведомление о статусе панели: {status}")
            return True
                
        except Exception as e:
            logger.error(f"Ошибка отправки ручного уведомления: {e}")
            return False

    async def get_panel_status_summary(self) -> Dict[str, Any]:
        try:
            status_data = await self.check_panel_health()
                
            status_descriptions = {
                "online": "🟢 Панель работает нормально",
                "offline": "🔴 Панель недоступна",
                "degraded": "🟡 Панель работает со сбоями",
                "maintenance": "🔧 Панель на обслуживании"
            }
                
            status = status_data.get("status", "offline")
                
            summary = {
                "status": status,
                "description": status_descriptions.get(status, "❓ Статус неизвестен"),
                "response_time": status_data.get("response_time", 0),
                "api_available": status_data.get("api_available", False),
                "nodes_status": f"{status_data.get('nodes_online', 0)}/{status_data.get('total_nodes', 0)} нод онлайн",
                "users_online": status_data.get("users_online", 0),
                "last_check": status_data.get("last_check"),
                "has_issues": status in ["offline", "degraded"]
            }
                
            if status == "offline":
                summary["recommendation"] = "Проверьте подключение к серверу и работоспособность панели"
            elif status == "degraded":
                summary["recommendation"] = "Рекомендуется проверить состояние нод и производительность сервера"
            else:
                summary["recommendation"] = "Все системы работают нормально"
                
            return summary
                
        except Exception as e:
            logger.error(f"Ошибка получения сводки статуса панели: {e}")
            return {
                "status": "error",
                "description": "❌ Ошибка проверки статуса",
                "response_time": 0,
                "api_available": False,
                "nodes_status": "неизвестно",
                "users_online": 0,
                "last_check": datetime.utcnow(),
                "has_issues": True,
                "recommendation": "Обратитесь к системному администратору",
                "error": str(e)
            }
        
    async def check_panel_health(self) -> Dict[str, Any]:
        try:
            start_time = datetime.utcnow()
                
            async with self.get_api_client() as api:
                try:
                    system_stats = await api.get_system_stats()
                    api_available = True
                    api_error = None
                except Exception as e:
                    api_available = False
                    api_error = str(e)
                    system_stats = {}
                    
                try:
                    nodes = await api.get_all_nodes()
                    nodes_online = sum(1 for node in nodes if node.is_connected and node.is_node_online)
                    total_nodes = len(nodes)
                    nodes_health = "healthy" if nodes_online > 0 else "unhealthy"
                except Exception:
                    nodes_online = 0
                    total_nodes = 0
                    nodes_health = "unknown"
                    
                end_time = datetime.utcnow()
                response_time = (end_time - start_time).total_seconds()
                    
                if not api_available:
                    status = "offline"
                elif response_time > 10: 
                    status = "degraded"
                elif nodes_health == "unhealthy":
                    status = "degraded"
                else:
                    status = "online"
                    
                return {
                    "status": status,
                    "api_available": api_available,
                    "api_error": api_error,
                    "response_time": round(response_time, 2),
                    "nodes_online": nodes_online,
                    "total_nodes": total_nodes,
                    "nodes_health": nodes_health,
                    "users_online": system_stats.get('onlineStats', {}).get('onlineNow', 0),
                    "total_users": system_stats.get('users', {}).get('totalUsers', 0),
                    "last_check": end_time,
                    "api_url": settings.REMNAWAVE_API_URL
                }
                
        except Exception as e:
            logger.error(f"Ошибка проверки здоровья панели: {e}")
            return {
                "status": "offline",
                "api_available": False,
                "api_error": str(e),
                "response_time": 0,
                "nodes_online": 0,
                "total_nodes": 0,
                "nodes_health": "unknown",
                "last_check": datetime.utcnow(),
                "api_url": settings.REMNAWAVE_API_URL
            }
        

