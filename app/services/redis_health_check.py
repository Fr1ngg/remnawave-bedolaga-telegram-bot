import asyncio
import logging
from typing import Dict, Any
from datetime import datetime

from app.services.redis_service import redis_service

logger = logging.getLogger(__name__)


class RedisHealthChecker:
    """Сервис для мониторинга здоровья Redis"""
    
    def __init__(self):
        self._last_check = None
        self._is_healthy = False
        self._check_interval = 30  # секунд
        self._consecutive_failures = 0
        self._max_failures = 3
    
    async def check_health(self) -> Dict[str, Any]:
        """Проверка здоровья Redis"""
        start_time = datetime.now()
        
        try:
            # Проверяем ping
            ping_ok = await redis_service.ping()
            
            if not ping_ok:
                raise Exception("Redis ping failed")
            
            # Получаем статистику
            stats = await redis_service.get_stats()
            
            # Проверяем использование памяти
            memory_usage = self._parse_memory_usage(stats.get('used_memory', '0B'))
            memory_peak = self._parse_memory_usage(stats.get('used_memory_peak', '0B'))
            
            # Проверяем количество подключенных клиентов
            connected_clients = stats.get('connected_clients', 0)
            
            # Проверяем производительность
            ops_per_sec = stats.get('instantaneous_ops_per_sec', 0)
            
            # Определяем здоровье системы
            is_healthy = (
                ping_ok and
                connected_clients < 200 and  # Максимум 200 клиентов для высоких нагрузок
                memory_usage < 800 * 1024 * 1024 and  # Максимум 800MB (80% от 1GB)
                self._consecutive_failures < self._max_failures
            )
            
            if is_healthy:
                self._consecutive_failures = 0
            else:
                self._consecutive_failures += 1
            
            self._is_healthy = is_healthy
            self._last_check = start_time
            
            check_duration = (datetime.now() - start_time).total_seconds()
            
            health_info = {
                'is_healthy': is_healthy,
                'ping_ok': ping_ok,
                'connected_clients': connected_clients,
                'memory_usage_bytes': memory_usage,
                'memory_usage_human': stats.get('used_memory', '0B'),
                'memory_peak_bytes': memory_peak,
                'memory_peak_human': stats.get('used_memory_peak', '0B'),
                'ops_per_sec': ops_per_sec,
                'keyspace_hits': stats.get('keyspace_hits', 0),
                'keyspace_misses': stats.get('keyspace_misses', 0),
                'hit_rate': self._calculate_hit_rate(stats),
                'consecutive_failures': self._consecutive_failures,
                'check_duration_ms': check_duration * 1000,
                'last_check': self._last_check.isoformat(),
                'warnings': self._get_warnings(stats, memory_usage, connected_clients)
            }
            
            if is_healthy:
                logger.debug(f"Redis health check passed: {check_duration:.3f}s")
            else:
                logger.warning(f"Redis health check failed: {health_info}")
            
            return health_info
            
        except Exception as e:
            self._consecutive_failures += 1
            self._is_healthy = False
            self._last_check = start_time
            
            check_duration = (datetime.now() - start_time).total_seconds()
            
            logger.error(f"Redis health check error: {e}")
            
            return {
                'is_healthy': False,
                'ping_ok': False,
                'error': str(e),
                'consecutive_failures': self._consecutive_failures,
                'check_duration_ms': check_duration * 1000,
                'last_check': self._last_check.isoformat(),
                'warnings': ['Redis connection failed']
            }
    
    def _parse_memory_usage(self, memory_str: str) -> int:
        """Парсинг строки использования памяти в байты"""
        if not memory_str:
            return 0
        
        memory_str = memory_str.upper().replace('B', '')
        
        multipliers = {
            'K': 1024,
            'M': 1024 * 1024,
            'G': 1024 * 1024 * 1024
        }
        
        for suffix, multiplier in multipliers.items():
            if memory_str.endswith(suffix):
                try:
                    return int(float(memory_str[:-1]) * multiplier)
                except ValueError:
                    pass
        
        try:
            return int(memory_str)
        except ValueError:
            return 0
    
    def _calculate_hit_rate(self, stats: Dict[str, Any]) -> float:
        """Расчет hit rate для кеша"""
        hits = stats.get('keyspace_hits', 0)
        misses = stats.get('keyspace_misses', 0)
        total = hits + misses
        
        if total == 0:
            return 0.0
        
        return (hits / total) * 100
    
    def _get_warnings(self, stats: Dict[str, Any], memory_usage: int, connected_clients: int) -> list:
        """Получение предупреждений о состоянии Redis"""
        warnings = []
        
        # Проверка использования памяти
        if memory_usage > 600 * 1024 * 1024:  # 600MB (60% от 1GB)
            warnings.append(f"High memory usage: {stats.get('used_memory', '0B')}")
        
        # Проверка количества клиентов
        if connected_clients > 100:
            warnings.append(f"High client count: {connected_clients}")
        
        # Проверка hit rate
        hit_rate = self._calculate_hit_rate(stats)
        if hit_rate < 80 and hit_rate > 0:
            warnings.append(f"Low cache hit rate: {hit_rate:.1f}%")
        
        # Проверка производительности
        ops_per_sec = stats.get('instantaneous_ops_per_sec', 0)
        if ops_per_sec > 1000:
            warnings.append(f"High operations per second: {ops_per_sec}")
        
        return warnings
    
    @property
    def is_healthy(self) -> bool:
        """Текущее состояние здоровья Redis"""
        return self._is_healthy
    
    @property
    def last_check(self) -> datetime:
        """Время последней проверки"""
        return self._last_check
    
    @property
    def consecutive_failures(self) -> int:
        """Количество последовательных неудач"""
        return self._consecutive_failures


# Глобальный экземпляр health checker
redis_health_checker = RedisHealthChecker()
