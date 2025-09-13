import logging
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import Subscription, User, SubscriptionStatus
from app.external.remnawave_api import (
    RemnaWaveAPI, RemnaWaveUser, UserStatus, 
    TrafficLimitStrategy, RemnaWaveAPIError
)
from app.database.crud.user import get_user_by_id
from app.utils.pricing_utils import (
    calculate_months_from_days,
    get_remaining_months,
    calculate_prorated_price,
    validate_pricing_calculation
)

logger = logging.getLogger(__name__)


class SubscriptionService:
    
    def __init__(self):
        auth_params = settings.get_remnawave_auth_params()
        self.api = RemnaWaveAPI(
            base_url=auth_params["base_url"],
            api_key=auth_params["api_key"],
            secret_key=auth_params["secret_key"],
            username=auth_params["username"],
            password=auth_params["password"]
        )
    
    async def create_remnawave_user(
        self, 
        db: AsyncSession, 
        subscription: Subscription
    ) -> Optional[RemnaWaveUser]:
        
        try:
            user = await get_user_by_id(db, subscription.user_id)
            if not user:
                logger.error(f"Пользователь {subscription.user_id} не найден")
                return None
            
            validation_success = await self.validate_and_clean_subscription(db, subscription, user)
            if not validation_success:
                logger.error(f"Ошибка валидации подписки для пользователя {user.telegram_id}")
                return None
            
            async with self.api as api:
                existing_users = await api.get_user_by_telegram_id(user.telegram_id)
                if existing_users:
                    logger.info(f"🔄 Найден существующий пользователь в панели для {user.telegram_id}")
                    remnawave_user = existing_users[0]
                    
                    try:
                        await api.reset_user_devices(remnawave_user.uuid)
                        logger.info(f"🔧 Сброшены HWID устройства для пользователя {user.telegram_id}")
                    except Exception as hwid_error:
                        logger.warning(f"⚠️ Не удалось сбросить HWID: {hwid_error}")
                    
                    updated_user = await api.update_user(
                        uuid=remnawave_user.uuid,
                        status=UserStatus.ACTIVE,
                        expire_at=subscription.end_date,
                        traffic_limit_bytes=self._gb_to_bytes(subscription.traffic_limit_gb),
                        traffic_limit_strategy=TrafficLimitStrategy.MONTH,
                        hwid_device_limit=subscription.device_limit,
                        active_internal_squads=subscription.connected_squads
                    )
                    
                else:
                    logger.info(f"🆕 Создаем нового пользователя в панели для {user.telegram_id}")
                    username = f"user_{user.telegram_id}"
                    updated_user = await api.create_user(
                        username=username,
                        expire_at=subscription.end_date,
                        status=UserStatus.ACTIVE,
                        traffic_limit_bytes=self._gb_to_bytes(subscription.traffic_limit_gb),
                        traffic_limit_strategy=TrafficLimitStrategy.MONTH,
                        telegram_id=user.telegram_id,
                        hwid_device_limit=subscription.device_limit,
                        description=f"Bot user: {user.full_name}",
                        active_internal_squads=subscription.connected_squads
                    )
                
                subscription.remnawave_short_uuid = updated_user.short_uuid
                subscription.subscription_url = updated_user.subscription_url 
                user.remnawave_uuid = updated_user.uuid
                
                await db.commit()
                
                logger.info(f"✅ Создан/обновлен RemnaWave пользователь для подписки {subscription.id}")
                logger.info(f"🔗 Ссылка на подписку: {updated_user.subscription_url}")
                logger.info(f"📊 Стратегия сброса трафика: MONTH") 
                return updated_user
                
        except RemnaWaveAPIError as e:
            logger.error(f"Ошибка RemnaWave API: {e}")
            return None
        except Exception as e:
            logger.error(f"Ошибка создания RemnaWave пользователя: {e}")
            return None
    
    async def update_remnawave_user(
        self,
        db: AsyncSession,
        subscription: Subscription
    ) -> Optional[RemnaWaveUser]:
        
        try:
            user = await get_user_by_id(db, subscription.user_id)
            if not user or not user.remnawave_uuid:
                logger.error(f"RemnaWave UUID не найден для пользователя {subscription.user_id}")
                return None
            
            current_time = datetime.utcnow()
            is_actually_active = (subscription.status == SubscriptionStatus.ACTIVE.value and 
                                 subscription.end_date > current_time)
            
            if (subscription.status == SubscriptionStatus.ACTIVE.value and 
                subscription.end_date <= current_time):
                
                subscription.status = SubscriptionStatus.EXPIRED.value
                subscription.updated_at = current_time
                await db.commit()
                is_actually_active = False
                logger.info(f"🔔 Статус подписки {subscription.id} автоматически изменен на 'expired'")
            
            async with self.api as api:
                updated_user = await api.update_user(
                    uuid=user.remnawave_uuid,
                    status=UserStatus.ACTIVE if is_actually_active else UserStatus.EXPIRED,
                    expire_at=subscription.end_date,
                    traffic_limit_bytes=self._gb_to_bytes(subscription.traffic_limit_gb),
                    traffic_limit_strategy=TrafficLimitStrategy.MONTH, 
                    hwid_device_limit=subscription.device_limit,
                    active_internal_squads=subscription.connected_squads
                )
                
                subscription.subscription_url = updated_user.subscription_url
                await db.commit()
                
                status_text = "активным" if is_actually_active else "истёкшим"
                logger.info(f"✅ Обновлен RemnaWave пользователь {user.remnawave_uuid} со статусом {status_text}")
                logger.info(f"📊 Стратегия сброса трафика: MONTH") 
                return updated_user
                
        except RemnaWaveAPIError as e:
            logger.error(f"Ошибка RemnaWave API: {e}")
            return None
        except Exception as e:
            logger.error(f"Ошибка обновления RemnaWave пользователя: {e}")
            return None
    
    async def disable_remnawave_user(self, user_uuid: str) -> bool:
        
        try:
            async with self.api as api:
                await api.disable_user(user_uuid)
                logger.info(f"✅ Отключен RemnaWave пользователь {user_uuid}")
                return True
                
        except Exception as e:
            logger.error(f"Ошибка отключения RemnaWave пользователя: {e}")
            return False
    
    async def revoke_subscription(
        self,
        db: AsyncSession,
        subscription: Subscription
    ) -> Optional[str]:
        
        try:
            user = await get_user_by_id(db, subscription.user_id)
            if not user or not user.remnawave_uuid:
                return None
            
            async with self.api as api:
                updated_user = await api.revoke_user_subscription(user.remnawave_uuid)
                
                subscription.remnawave_short_uuid = updated_user.short_uuid
                subscription.subscription_url = updated_user.subscription_url
                await db.commit()
                
                logger.info(f"✅ Обновлена ссылка подписки для пользователя {user.telegram_id}")
                return updated_user.subscription_url
                
        except Exception as e:
            logger.error(f"Ошибка обновления ссылки подписки: {e}")
            return None
    
    async def get_subscription_info(self, short_uuid: str) -> Optional[dict]:
        
        try:
            async with self.api as api:
                info = await api.get_subscription_info(short_uuid)
                return info
                
        except Exception as e:
            logger.error(f"Ошибка получения информации о подписке: {e}")
            return None
    
    async def sync_subscription_usage(
        self,
        db: AsyncSession,
        subscription: Subscription
    ) -> bool:
        
        try:
            user = await get_user_by_id(db, subscription.user_id)
            if not user or not user.remnawave_uuid:
                return False
            
            async with self.api as api:
                remnawave_user = await api.get_user_by_uuid(user.remnawave_uuid)
                if not remnawave_user:
                    return False
                
                used_gb = self._bytes_to_gb(remnawave_user.used_traffic_bytes)
                subscription.traffic_used_gb = used_gb
                
                await db.commit()
                
                logger.debug(f"Синхронизирован трафик для подписки {subscription.id}: {used_gb} ГБ")
                return True
                
        except Exception as e:
            logger.error(f"Ошибка синхронизации трафика: {e}")
            return False
    
    async def calculate_subscription_price(
        self,
        period_days: int,
        traffic_gb: int,
        server_squad_ids: List[int], 
        devices: int,
        db: AsyncSession 
    ) -> Tuple[int, List[int]]:
    
        from app.config import PERIOD_PRICES
        from app.database.crud.server_squad import get_server_squad_by_id
    
        if settings.MAX_DEVICES_LIMIT > 0 and devices > settings.MAX_DEVICES_LIMIT:
            raise ValueError(f"Превышен максимальный лимит устройств: {settings.MAX_DEVICES_LIMIT}")
    
        base_price = PERIOD_PRICES.get(period_days, 0)
        
        traffic_price = settings.get_traffic_price(traffic_gb)
    
        server_prices = []
        total_servers_price = 0
    
        for server_id in server_squad_ids:
            server = await get_server_squad_by_id(db, server_id)
            if server and server.is_available and not server.is_full:
                server_prices.append(server.price_kopeks)
                total_servers_price += server.price_kopeks
                logger.debug(f"Сервер {server.display_name}: {server.price_kopeks/100}₽")
            else:
                server_prices.append(0)
                logger.warning(f"Сервер ID {server_id} недоступен")
    
        devices_price = max(0, devices - settings.DEFAULT_DEVICE_LIMIT) * settings.PRICE_PER_DEVICE
        
        total_price = base_price + traffic_price + total_servers_price + devices_price
        
        logger.info(f"Расчет стоимости новой подписки:")
        logger.info(f"   Период {period_days} дней: {base_price/100}₽")
        logger.info(f"   Трафик {traffic_gb} ГБ: {traffic_price/100}₽")
        logger.info(f"   Серверы ({len(server_squad_ids)}): {total_servers_price/100}₽")
        logger.info(f"   Устройства ({devices}): {devices_price/100}₽")
        logger.info(f"   ИТОГО: {total_price/100}₽")
        
        return total_price, server_prices
    
    async def calculate_renewal_price(
        self,
        subscription: Subscription,
        period_days: int,
        db: AsyncSession
    ) -> int:
        try:
            from app.config import PERIOD_PRICES
            
            base_price = PERIOD_PRICES.get(period_days, 0)
            
            servers_price, _ = await self.get_countries_price_by_uuids(
                subscription.connected_squads, db
            )
            
            devices_price = max(0, subscription.device_limit - settings.DEFAULT_DEVICE_LIMIT) * settings.PRICE_PER_DEVICE
            
            traffic_price = settings.get_traffic_price(subscription.traffic_limit_gb)
            
            total_price = base_price + servers_price + devices_price + traffic_price
            
            logger.info(f"💰 Расчет стоимости продления для подписки {subscription.id} (по текущим ценам):")
            logger.info(f"   📅 Период {period_days} дней: {base_price/100}₽")
            logger.info(f"   🌍 Серверы ({len(subscription.connected_squads)}) по текущим ценам: {servers_price/100}₽")
            logger.info(f"   📱 Устройства ({subscription.device_limit}): {devices_price/100}₽")
            logger.info(f"   📊 Трафик ({subscription.traffic_limit_gb} ГБ): {traffic_price/100}₽")
            logger.info(f"   💎 ИТОГО: {total_price/100}₽")
            
            return total_price
            
        except Exception as e:
            logger.error(f"Ошибка расчета стоимости продления: {e}")
            from app.config import PERIOD_PRICES
            return PERIOD_PRICES.get(period_days, 0)

    async def validate_and_clean_subscription(
        self,
        db: AsyncSession,
        subscription: Subscription,
        user: User
    ) -> bool:
        try:
            needs_cleanup = False
            
            if user.remnawave_uuid:
                try:
                    async with self.api as api:
                        remnawave_user = await api.get_user_by_uuid(user.remnawave_uuid)
                        
                        if not remnawave_user:
                            logger.warning(f"⚠️ Пользователь {user.telegram_id} имеет UUID {user.remnawave_uuid}, но не найден в панели")
                            needs_cleanup = True
                        else:
                            if remnawave_user.telegram_id != user.telegram_id:
                                logger.warning(f"⚠️ Несоответствие telegram_id для пользователя {user.telegram_id}")
                                needs_cleanup = True
                except Exception as api_error:
                    logger.error(f"❌ Ошибка проверки пользователя в панели: {api_error}")
                    needs_cleanup = True
            
            if subscription.remnawave_short_uuid and not user.remnawave_uuid:
                logger.warning(f"⚠️ У подписки есть short_uuid, но у пользователя нет remnawave_uuid")
                needs_cleanup = True
                
            if needs_cleanup:
                logger.info(f"🧹 Очищаем мусорные данные подписки для пользователя {user.telegram_id}")
                
                subscription.remnawave_short_uuid = None
                subscription.subscription_url = ""
                subscription.connected_squads = []
                
                user.remnawave_uuid = None
                
                await db.commit()
                logger.info(f"✅ Мусорные данные очищены для пользователя {user.telegram_id}")
                
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка валидации подписки для пользователя {user.telegram_id}: {e}")
            await db.rollback()
            return False
    
    async def get_countries_price_by_uuids(
        self, 
        country_uuids: List[str], 
        db: AsyncSession
    ) -> Tuple[int, List[int]]:
        try:
            from app.database.crud.server_squad import get_server_squad_by_uuid
            
            total_price = 0
            prices_list = []
            
            for country_uuid in country_uuids:
                server = await get_server_squad_by_uuid(db, country_uuid)
                if server and server.is_available and not server.is_full:
                    price = server.price_kopeks
                    total_price += price
                    prices_list.append(price)
                    logger.debug(f"🏷️ Страна {server.display_name}: {price/100}₽")
                else:
                    default_price = 0  
                    total_price += default_price
                    prices_list.append(default_price)
                    logger.warning(f"⚠️ Сервер {country_uuid} недоступен, используем базовую цену: {default_price/100}₽")
            
            logger.info(f"💰 Общая стоимость стран: {total_price/100}₽")
            return total_price, prices_list
            
        except Exception as e:
            logger.error(f"Ошибка получения цен стран: {e}")
            default_prices = [0] * len(country_uuids)
            return sum(default_prices), default_prices
    
    async def _get_countries_price(self, country_uuids: List[str], db: AsyncSession) -> int:
        try:
            total_price, _ = await self.get_countries_price_by_uuids(country_uuids, db)
            return total_price
        except Exception as e:
            logger.error(f"Ошибка получения цен стран: {e}")
            return len(country_uuids) * 1000

    async def calculate_subscription_price_with_months(
        self,
        period_days: int,
        traffic_gb: int,
        server_squad_ids: List[int], 
        devices: int,
        db: AsyncSession 
    ) -> Tuple[int, List[int]]:
    
        from app.config import PERIOD_PRICES
        from app.database.crud.server_squad import get_server_squad_by_id
        
        if settings.MAX_DEVICES_LIMIT > 0 and devices > settings.MAX_DEVICES_LIMIT:
            raise ValueError(f"Превышен максимальный лимит устройств: {settings.MAX_DEVICES_LIMIT}")
        
        months_in_period = calculate_months_from_days(period_days)
        
        base_price = PERIOD_PRICES.get(period_days, 0)
        
        traffic_price_per_month = settings.get_traffic_price(traffic_gb)
        total_traffic_price = traffic_price_per_month * months_in_period
        
        server_prices = []
        total_servers_price = 0
        
        for server_id in server_squad_ids:
            server = await get_server_squad_by_id(db, server_id)
            if server and server.is_available and not server.is_full:
                server_price_per_month = server.price_kopeks
                server_price_total = server_price_per_month * months_in_period
                server_prices.append(server_price_total)
                total_servers_price += server_price_total
                logger.debug(f"Сервер {server.display_name}: {server_price_per_month/100}₽/мес x {months_in_period} мес = {server_price_total/100}₽")
            else:
                server_prices.append(0)
                logger.warning(f"Сервер ID {server_id} недоступен")
        
        additional_devices = max(0, devices - settings.DEFAULT_DEVICE_LIMIT)
        devices_price_per_month = additional_devices * settings.PRICE_PER_DEVICE
        total_devices_price = devices_price_per_month * months_in_period
        
        total_price = base_price + total_traffic_price + total_servers_price + total_devices_price
        
        logger.info(f"Расчет стоимости новой подписки на {period_days} дней ({months_in_period} мес):")
        logger.info(f"   Период {period_days} дней: {base_price/100}₽")
        logger.info(f"   Трафик {traffic_gb} ГБ: {traffic_price_per_month/100}₽/мес x {months_in_period} = {total_traffic_price/100}₽")
        logger.info(f"   Серверы ({len(server_squad_ids)}): {total_servers_price/100}₽")
        logger.info(f"   Устройства ({additional_devices}): {devices_price_per_month/100}₽/мес x {months_in_period} = {total_devices_price/100}₽")
        logger.info(f"   ИТОГО: {total_price/100}₽")
        
        return total_price, server_prices
    
    async def calculate_renewal_price_with_months(
        self,
        subscription: Subscription,
        period_days: int,
        db: AsyncSession
    ) -> int:
        try:
            from app.config import PERIOD_PRICES
            
            months_in_period = calculate_months_from_days(period_days)
            
            base_price = PERIOD_PRICES.get(period_days, 0)
            
            servers_price_per_month, _ = await self.get_countries_price_by_uuids(
                subscription.connected_squads, db
            )
            total_servers_price = servers_price_per_month * months_in_period
            
            additional_devices = max(0, subscription.device_limit - settings.DEFAULT_DEVICE_LIMIT)
            devices_price_per_month = additional_devices * settings.PRICE_PER_DEVICE
            total_devices_price = devices_price_per_month * months_in_period
            
            traffic_price_per_month = settings.get_traffic_price(subscription.traffic_limit_gb)
            total_traffic_price = traffic_price_per_month * months_in_period
            
            total_price = base_price + total_servers_price + total_devices_price + total_traffic_price
            
            logger.info(f"💰 Расчет стоимости продления подписки {subscription.id} на {period_days} дней ({months_in_period} мес):")
            logger.info(f"   📅 Период {period_days} дней: {base_price/100}₽")
            logger.info(f"   🌍 Серверы: {servers_price_per_month/100}₽/мес x {months_in_period} = {total_servers_price/100}₽")
            logger.info(f"   📱 Устройства: {devices_price_per_month/100}₽/мес x {months_in_period} = {total_devices_price/100}₽")
            logger.info(f"   📊 Трафик: {traffic_price_per_month/100}₽/мес x {months_in_period} = {total_traffic_price/100}₽")
            logger.info(f"   💎 ИТОГО: {total_price/100}₽")
            
            return total_price
            
        except Exception as e:
            logger.error(f"Ошибка расчета стоимости продления: {e}")
            from app.config import PERIOD_PRICES
            return PERIOD_PRICES.get(period_days, 0)
    
    async def calculate_addon_price_with_remaining_period(
        self,
        subscription: Subscription,
        additional_traffic_gb: int = 0,
        additional_devices: int = 0,
        additional_server_ids: List[int] = None,
        db: AsyncSession = None
    ) -> int:
        
        if additional_server_ids is None:
            additional_server_ids = []
        
        current_time = datetime.utcnow()
        months_to_pay = get_remaining_months(subscription.end_date)
        
        total_price = 0
        
        if additional_traffic_gb > 0:
            traffic_price_per_month = settings.get_traffic_price(additional_traffic_gb)
            total_price += traffic_price_per_month * months_to_pay
            logger.info(f"Трафик +{additional_traffic_gb}ГБ: {traffic_price_per_month/100}₽/мес x {months_to_pay} = {traffic_price_per_month * months_to_pay/100}₽")
        
        if additional_devices > 0:
            devices_price_per_month = additional_devices * settings.PRICE_PER_DEVICE
            total_price += devices_price_per_month * months_to_pay
            logger.info(f"Устройства +{additional_devices}: {devices_price_per_month/100}₽/мес x {months_to_pay} = {devices_price_per_month * months_to_pay/100}₽")
        
        if additional_server_ids and db:
            for server_id in additional_server_ids:
                from app.database.crud.server_squad import get_server_squad_by_id
                server = await get_server_squad_by_id(db, server_id)
                if server and server.is_available:
                    server_price_per_month = server.price_kopeks
                    server_total_price = server_price_per_month * months_to_pay
                    total_price += server_total_price
                    logger.info(f"Сервер {server.display_name}: {server_price_per_month/100}₽/мес x {months_to_pay} = {server_total_price/100}₽")
        
        logger.info(f"Итого доплата за {months_to_pay} мес: {total_price/100}₽")
        return total_price
    
    def _gb_to_bytes(self, gb: int) -> int:
        if gb == 0: 
            return 0
        return gb * 1024 * 1024 * 1024
    
    def _bytes_to_gb(self, bytes_value: int) -> float:
        if bytes_value == 0:
            return 0.0
        return bytes_value / (1024 * 1024 * 1024)
