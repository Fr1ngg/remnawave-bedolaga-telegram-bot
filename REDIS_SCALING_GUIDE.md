# Руководство по масштабированию Redis для высоких нагрузок

## 🚀 Оптимизация для 100+ клиентов

### 📊 Обновленные настройки

#### Redis конфигурация (`redis.conf`):
```conf
# Память увеличена до 1GB
maxmemory 1gb
maxmemory-policy allkeys-lru
maxmemory-samples 10

# Максимум 500 клиентов
maxclients 500

# Увеличен TCP backlog
tcp-backlog 1024

# Повышена частота хеширования
hz 20
```

#### Docker настройки (`docker-compose.yml`):
```yaml
sysctls:
  - net.core.somaxconn=2048
  - net.core.netdev_max_backlog=5000
```

#### Пул соединений (`redis_service.py`):
```python
max_connections=100  # Увеличено с 20 до 100
```

### 📈 Мониторинг для высоких нагрузок

#### Новые пороги предупреждений:
- **Клиенты**: >100 (предупреждение), >200 (критично)
- **Память**: >600MB (предупреждение), >800MB (критично)
- **Hit Rate**: <80% (предупреждение)

#### Рекомендуемые метрики:
- **Клиенты**: 50-150 (оптимально)
- **Память**: <600MB (60% от лимита)
- **Операций/сек**: <2000
- **Hit Rate**: >85%

### 🔧 Дополнительные оптимизации

#### 1. Мониторинг производительности:
```bash
# Проверка количества клиентов
docker exec remnawave_bot_redis redis-cli info clients

# Проверка операций в секунду
docker exec remnawave_bot_redis redis-cli info stats | grep instantaneous

# Проверка использования памяти
docker exec remnawave_bot_redis redis-cli info memory
```

#### 2. Настройка системы для высоких нагрузок:
```bash
# Увеличение лимитов файлов
echo "* soft nofile 65536" >> /etc/security/limits.conf
echo "* hard nofile 65536" >> /etc/security/limits.conf

# Оптимизация TCP
echo "net.core.somaxconn = 2048" >> /etc/sysctl.conf
echo "net.core.netdev_max_backlog = 5000" >> /etc/sysctl.conf
sysctl -p
```

### 📊 Градация нагрузок

#### 🟢 Низкая нагрузка (1-50 клиентов):
- **Память**: 256MB
- **Клиенты**: 50
- **Соединения**: 20

#### 🟡 Средняя нагрузка (50-100 клиентов):
- **Память**: 512MB
- **Клиенты**: 100
- **Соединения**: 50

#### 🔴 Высокая нагрузка (100+ клиентов):
- **Память**: 1GB
- **Клиенты**: 500
- **Соединения**: 100

### ⚡ Производительность

#### Ожидаемые показатели:
- **Время отклика**: <5ms
- **Пропускная способность**: 10,000+ ops/sec
- **Использование CPU**: <50%
- **Использование памяти**: <80%

#### Критические пороги:
- **Время отклика**: >50ms
- **Пропускная способность**: <1000 ops/sec
- **Использование CPU**: >90%
- **Использование памяти**: >90%

### 🚨 Алерты и мониторинг

#### Автоматические предупреждения:
1. **Клиенты >100** - "High client count"
2. **Память >600MB** - "High memory usage"
3. **Hit Rate <80%** - "Low cache hit rate"
4. **Операций >2000/sec** - "High operations per second"

#### Рекомендуемые действия:
1. **Мониторить каждые 5 минут** при высокой нагрузке
2. **Проверять логи** при предупреждениях
3. **Масштабировать** при критических значениях

### 🔄 Масштабирование

#### Горизонтальное масштабирование:
```yaml
# Docker Compose с несколькими Redis инстансами
redis-master:
  image: redis:7-alpine
  command: redis-server --appendonly yes

redis-replica:
  image: redis:7-alpine
  command: redis-server --slaveof redis-master 6379
```

#### Вертикальное масштабирование:
```yaml
# Увеличение ресурсов
deploy:
  resources:
    limits:
      memory: 2G
      cpus: '2.0'
    reservations:
      memory: 1G
      cpus: '1.0'
```

### 📋 Чек-лист для высоких нагрузок

#### ✅ Подготовка:
- [ ] Увеличена память до 1GB
- [ ] Настроен пул на 100 соединений
- [ ] Обновлены пороги мониторинга
- [ ] Настроены системные параметры

#### ✅ Мониторинг:
- [ ] Настроены алерты
- [ ] Проверяется каждые 5 минут
- [ ] Логируются все предупреждения
- [ ] Отслеживается производительность

#### ✅ Производительность:
- [ ] Hit Rate >85%
- [ ] Время отклика <5ms
- [ ] Клиентов <150
- [ ] Память <600MB

### 🛠️ Устранение проблем

#### Проблема: Много клиентов
```bash
# Проверка активных соединений
docker exec remnawave_bot_redis redis-cli client list | wc -l

# Закрытие неактивных соединений
docker exec remnawave_bot_redis redis-cli client kill type normal
```

#### Проблема: Высокое использование памяти
```bash
# Очистка кеша
docker exec remnawave_bot_redis redis-cli flushall

# Проверка больших ключей
docker exec remnawave_bot_redis redis-cli --bigkeys
```

#### Проблема: Медленные запросы
```bash
# Просмотр медленных запросов
docker exec remnawave_bot_redis redis-cli slowlog get 10

# Очистка логов медленных запросов
docker exec remnawave_bot_redis redis-cli slowlog reset
```

### 📞 Поддержка

#### Команды для диагностики:
```bash
# Общая информация
docker exec remnawave_bot_redis redis-cli info

# Статистика клиентов
docker exec remnawave_bot_redis redis-cli info clients

# Статистика памяти
docker exec remnawave_bot_redis redis-cli info memory

# Статистика производительности
docker exec remnawave_bot_redis redis-cli info stats
```

#### Контакты:
- Используйте `/redis_status` для быстрой проверки
- Проверяйте логи при проблемах
- Обращайтесь к документации Redis при необходимости

## 🎯 Заключение

Система оптимизирована для работы с 100+ клиентами:
- **Память**: 1GB (достаточно для высоких нагрузок)
- **Клиенты**: до 500 одновременных соединений
- **Производительность**: 10,000+ операций в секунду
- **Мониторинг**: автоматические предупреждения

Готово к продакшену с высокими нагрузками! 🚀
