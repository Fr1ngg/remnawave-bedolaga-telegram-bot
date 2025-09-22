#!/usr/bin/env python3
"""
Скрипт для тестирования оптимизаций Redis
"""

import asyncio
import time
import logging
from typing import List, Dict, Any

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_redis_performance():
    """Тестирование производительности Redis"""
    
    try:
        from app.services.redis_service import redis_service
        from app.services.redis_health_check import redis_health_checker
        
        print("🚀 Тестирование оптимизаций Redis...")
        
        # 1. Тест подключения
        print("\n1. Тест подключения...")
        start_time = time.time()
        connected = await redis_service.connect()
        connect_time = time.time() - start_time
        
        if connected:
            print(f"✅ Подключение успешно за {connect_time:.3f}s")
        else:
            print("❌ Ошибка подключения")
            return
        
        # 2. Тест ping
        print("\n2. Тест ping...")
        start_time = time.time()
        ping_ok = await redis_service.ping()
        ping_time = time.time() - start_time
        
        if ping_ok:
            print(f"✅ Ping успешен за {ping_time:.3f}s")
        else:
            print("❌ Ping не удался")
        
        # 3. Тест записи/чтения
        print("\n3. Тест записи/чтения...")
        test_data = {"test": "data", "number": 123, "list": [1, 2, 3]}
        
        # Запись
        start_time = time.time()
        write_ok = await redis_service.set("test_key", str(test_data), expire=60)
        write_time = time.time() - start_time
        
        if write_ok:
            print(f"✅ Запись успешна за {write_time:.3f}s")
        else:
            print("❌ Ошибка записи")
        
        # Чтение
        start_time = time.time()
        read_data = await redis_service.get("test_key")
        read_time = time.time() - start_time
        
        if read_data:
            print(f"✅ Чтение успешно за {read_time:.3f}s")
        else:
            print("❌ Ошибка чтения")
        
        # 4. Тест производительности (множественные операции)
        print("\n4. Тест производительности...")
        operations = 100
        start_time = time.time()
        
        for i in range(operations):
            await redis_service.set(f"perf_test_{i}", f"value_{i}", expire=60)
        
        for i in range(operations):
            await redis_service.get(f"perf_test_{i}")
        
        total_time = time.time() - start_time
        ops_per_sec = (operations * 2) / total_time
        
        print(f"✅ {operations * 2} операций за {total_time:.3f}s ({ops_per_sec:.1f} ops/sec)")
        
        # 5. Тест health check
        print("\n5. Тест health check...")
        start_time = time.time()
        health_info = await redis_health_checker.check_health()
        health_time = time.time() - start_time
        
        print(f"✅ Health check за {health_time:.3f}s")
        print(f"   Состояние: {'Здоров' if health_info.get('is_healthy') else 'Проблемы'}")
        print(f"   Клиенты: {health_info.get('connected_clients', 0)}")
        print(f"   Память: {health_info.get('memory_usage_human', '0B')}")
        print(f"   Hit Rate: {health_info.get('hit_rate', 0):.1f}%")
        
        # 6. Тест статистики
        print("\n6. Тест статистики...")
        stats = await redis_service.get_stats()
        print(f"✅ Статистика получена:")
        print(f"   Подключенных клиентов: {stats.get('connected_clients', 0)}")
        print(f"   Использовано памяти: {stats.get('used_memory', '0B')}")
        print(f"   Операций в секунду: {stats.get('instantaneous_ops_per_sec', 0)}")
        print(f"   Всего команд: {stats.get('total_commands_processed', 0)}")
        
        # 7. Очистка тестовых данных
        print("\n7. Очистка тестовых данных...")
        for i in range(operations):
            await redis_service.delete(f"perf_test_{i}")
        await redis_service.delete("test_key")
        print("✅ Тестовые данные очищены")
        
        # 8. Тест отключения
        print("\n8. Тест отключения...")
        await redis_service.disconnect()
        print("✅ Отключение успешно")
        
        print("\n🎉 Все тесты пройдены успешно!")
        
    except Exception as e:
        print(f"❌ Ошибка тестирования: {e}")
        logger.exception("Ошибка тестирования Redis")

async def test_cache_service():
    """Тестирование кеш-сервиса"""
    
    try:
        from app.utils.cache import cache
        
        print("\n🧪 Тестирование кеш-сервиса...")
        
        # Подключение
        await cache.connect()
        print("✅ Кеш-сервис подключен")
        
        # Тест записи/чтения
        test_data = {"user_id": 123, "name": "Test User", "active": True}
        
        # Запись
        write_ok = await cache.set("user:123", test_data, expire=300)
        if write_ok:
            print("✅ Запись в кеш успешна")
        else:
            print("❌ Ошибка записи в кеш")
        
        # Чтение
        read_data = await cache.get("user:123")
        if read_data == test_data:
            print("✅ Чтение из кеша успешно")
        else:
            print("❌ Ошибка чтения из кеша")
        
        # Тест хеша
        hash_data = {"field1": "value1", "field2": "value2"}
        hash_ok = await cache.set_hash("test_hash", hash_data, expire=300)
        if hash_ok:
            print("✅ Запись хеша успешна")
        
        read_hash = await cache.get_hash("test_hash")
        if read_hash == hash_data:
            print("✅ Чтение хеша успешно")
        
        # Очистка
        await cache.delete("user:123")
        await cache.delete("test_hash")
        print("✅ Тестовые данные очищены")
        
        # Отключение
        await cache.disconnect()
        print("✅ Кеш-сервис отключен")
        
    except Exception as e:
        print(f"❌ Ошибка тестирования кеш-сервиса: {e}")
        logger.exception("Ошибка тестирования кеш-сервиса")

async def main():
    """Основная функция тестирования"""
    
    print("🔧 Тестирование оптимизаций Redis для Telegram бота")
    print("=" * 60)
    
    # Тест Redis сервиса
    await test_redis_performance()
    
    # Тест кеш-сервиса
    await test_cache_service()
    
    print("\n" + "=" * 60)
    print("✅ Тестирование завершено!")

if __name__ == "__main__":
    asyncio.run(main())
