# Документация: Единый модуль расчета стоимости тарифа

## Обзор

Мы создали единый модуль `app/utils/pricing_calculator.py` для централизованного расчета стоимости подписки, который учитывает:

- Стоимость периода
- Стоимость трафика
- Стоимость серверов
- Стоимость устройств
- Скидки от промо-групп
- Прорейтед расчеты

## Структура модуля

### Основные функции:

1. `calculate_subscription_total_cost(db, period_days, traffic_gb, server_squad_ids, devices, user=None, promo_group=None)`
   - Основная функция для расчета полной стоимости подписки
   - Возвращает объект `PricingResult` с деталями

2. `calculate_period_price(period_days, user=None, promo_group=None)`
   - Расчет только стоимости периода

3. `calculate_traffic_price(traffic_gb, period_days, user=None, promo_group=None)`
   - Расчет только стоимости трафика

4. `calculate_servers_price(db, server_squad_ids, period_days, user=None, promo_group=None)`
   - Расчет только стоимости серверов

5. `calculate_devices_price(devices, period_days, user=None, promo_group=None)`
   - Расчет только стоимости устройств

## Использование

### Пример использования основной функции:

```python
from app.utils.pricing_calculator import calculate_subscription_total_cost

pricing_result = await calculate_subscription_total_cost(
    db,
    period_days=30,
    traffic_gb=100,  # 100GB
    server_squad_ids=[1, 2],
    devices=3,
    user=user  # может быть None
)

total_price = pricing_result.total_price  # Общая цена в копейках
details = pricing_result.details  # Детали расчета

print(f"Итоговая цена: {total_price/100}₽")
print(f"Скидка: {details.total_discount/100}₽")
```

### Доступные детали расчета:

Объект `PricingDetails` содержит следующие поля:

- `base_price_original` - оригинальная цена периода
- `base_price_discounted` - цена периода со скидкой
- `base_discount_percent` - процент скидки на период
- `total_traffic_price` - общая цена трафика
- `total_servers_price` - общая цена серверов
- `total_devices_price` - общая цена устройств
- `total_cost` - общая стоимость
- и другие...

## Интеграция

Мы интегрировали новый модуль в существующую систему:

- Функция `calculate_subscription_total_cost` в `app/database/crud/subscription.py` теперь использует новый модуль
- Новый модуль экспортируется через `app/utils/__init__.py`
- Все вспомогательные функции доступны в системе

## Преимущества

1. **Единая точка расчета** - вся логика расчета цен теперь в одном месте
2. **Легкость поддержки** - изменения в логике цен теперь происходят только в одном месте
3. **Детализация** - подробная информация о каждом компоненте цены
4. **Гибкость** - возможность расчета отдельных компонентов
5. **Обратная совместимость** - сохранен прежний интерфейс для существующего кода