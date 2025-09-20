import asyncio
import logging
from typing import Optional, Dict, Any, Union
from datetime import timedelta
import redis.asyncio as redis
from redis.asyncio.connection import ConnectionPool
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff

from app.config import settings

logger = logging.getLogger(__name__)


class RedisService:
    """Оптимизированный сервис для работы с Redis с пулом соединений"""
    
    def __init__(self):
        self._pool: Optional[ConnectionPool] = None
        self._redis_client: Optional[redis.Redis] = None
        self._connected = False
        self._lock = asyncio.Lock()
    
    async def connect(self) -> bool:
        """Подключение к Redis с пулом соединений"""
        async with self._lock:
            if self._connected:
                return True
                
            try:
                # Создаем пул соединений с оптимизированными настройками
                self._pool = ConnectionPool.from_url(
                    settings.REDIS_URL,
                    max_connections=100,  # Максимум соединений в пуле для высоких нагрузок
                    retry_on_timeout=True,
                    retry_on_error=[redis.ConnectionError, redis.TimeoutError],
                    retry=Retry(ExponentialBackoff(), 3),  # Экспоненциальная задержка при повторах
                    health_check_interval=30,  # Проверка здоровья соединений каждые 30 сек
                    socket_keepalive=True,
                    socket_keepalive_options={},
                    socket_connect_timeout=5,
                    socket_timeout=5,
                    decode_responses=False,  # Оставляем байты для лучшей производительности
                )
                
                # Создаем клиент с пулом
                self._redis_client = redis.Redis(connection_pool=self._pool)
                
                # Проверяем подключение
                await self._redis_client.ping()
                self._connected = True
                
                logger.info("✅ Redis подключен с пулом соединений")
                return True
                
            except Exception as e:
                logger.error(f"❌ Ошибка подключения к Redis: {e}")
                self._connected = False
                return False
    
    async def disconnect(self):
        """Отключение от Redis"""
        async with self._lock:
            if self._redis_client:
                try:
                    await self._redis_client.close()
                    logger.info("Redis соединения закрыты")
                except Exception as e:
                    logger.error(f"Ошибка закрытия Redis: {e}")
                finally:
                    self._redis_client = None
                    self._pool = None
                    self._connected = False
    
    async def get_client(self) -> Optional[redis.Redis]:
        """Получение Redis клиента"""
        if not self._connected:
            await self.connect()
        return self._redis_client
    
    async def ping(self) -> bool:
        """Проверка соединения"""
        try:
            client = await self.get_client()
            if client:
                await client.ping()
                return True
        except Exception as e:
            logger.warning(f"Redis ping failed: {e}")
        return False
    
    async def get(self, key: str) -> Optional[bytes]:
        """Получение значения по ключу"""
        try:
            client = await self.get_client()
            if client:
                return await client.get(key)
        except Exception as e:
            logger.error(f"Ошибка получения {key}: {e}")
        return None
    
    async def set(
        self, 
        key: str, 
        value: Union[str, bytes], 
        expire: Optional[Union[int, timedelta]] = None
    ) -> bool:
        """Установка значения с TTL"""
        try:
            client = await self.get_client()
            if client:
                if isinstance(expire, timedelta):
                    expire = int(expire.total_seconds())
                
                await client.set(key, value, ex=expire)
                return True
        except Exception as e:
            logger.error(f"Ошибка установки {key}: {e}")
        return False
    
    async def delete(self, key: str) -> bool:
        """Удаление ключа"""
        try:
            client = await self.get_client()
            if client:
                result = await client.delete(key)
                return result > 0
        except Exception as e:
            logger.error(f"Ошибка удаления {key}: {e}")
        return False
    
    async def exists(self, key: str) -> bool:
        """Проверка существования ключа"""
        try:
            client = await self.get_client()
            if client:
                result = await client.exists(key)
                return result > 0
        except Exception as e:
            logger.error(f"Ошибка проверки существования {key}: {e}")
        return False
    
    async def expire(self, key: str, seconds: int) -> bool:
        """Установка TTL для ключа"""
        try:
            client = await self.get_client()
            if client:
                result = await client.expire(key, seconds)
                return bool(result)
        except Exception as e:
            logger.error(f"Ошибка установки TTL для {key}: {e}")
        return False
    
    async def keys(self, pattern: str = "*") -> list:
        """Получение списка ключей по паттерну"""
        try:
            client = await self.get_client()
            if client:
                return await client.keys(pattern)
        except Exception as e:
            logger.error(f"Ошибка получения ключей {pattern}: {e}")
        return []
    
    async def flushall(self) -> bool:
        """Очистка всех данных"""
        try:
            client = await self.get_client()
            if client:
                await client.flushall()
                return True
        except Exception as e:
            logger.error(f"Ошибка очистки Redis: {e}")
        return False
    
    async def increment(self, key: str, amount: int = 1) -> Optional[int]:
        """Инкремент значения"""
        try:
            client = await self.get_client()
            if client:
                return await client.incrby(key, amount)
        except Exception as e:
            logger.error(f"Ошибка инкремента {key}: {e}")
        return None
    
    async def hset(self, name: str, mapping: Dict[str, Any], expire: Optional[int] = None) -> bool:
        """Установка хеша"""
        try:
            client = await self.get_client()
            if client:
                await client.hset(name, mapping=mapping)
                if expire:
                    await client.expire(name, expire)
                return True
        except Exception as e:
            logger.error(f"Ошибка установки хеша {name}: {e}")
        return False
    
    async def hget(self, name: str, key: str) -> Optional[bytes]:
        """Получение значения из хеша"""
        try:
            client = await self.get_client()
            if client:
                return await client.hget(name, key)
        except Exception as e:
            logger.error(f"Ошибка получения из хеша {name}.{key}: {e}")
        return None
    
    async def hgetall(self, name: str) -> Dict[bytes, bytes]:
        """Получение всего хеша"""
        try:
            client = await self.get_client()
            if client:
                return await client.hgetall(name)
        except Exception as e:
            logger.error(f"Ошибка получения хеша {name}: {e}")
        return {}
    
    async def get_stats(self) -> Dict[str, Any]:
        """Получение статистики Redis"""
        try:
            client = await self.get_client()
            if client:
                info = await client.info()
                return {
                    'connected_clients': info.get('connected_clients', 0),
                    'used_memory': info.get('used_memory_human', '0B'),
                    'used_memory_peak': info.get('used_memory_peak_human', '0B'),
                    'keyspace_hits': info.get('keyspace_hits', 0),
                    'keyspace_misses': info.get('keyspace_misses', 0),
                    'total_commands_processed': info.get('total_commands_processed', 0),
                    'instantaneous_ops_per_sec': info.get('instantaneous_ops_per_sec', 0),
                }
        except Exception as e:
            logger.error(f"Ошибка получения статистики Redis: {e}")
        return {}


# Глобальный экземпляр сервиса
redis_service = RedisService()
