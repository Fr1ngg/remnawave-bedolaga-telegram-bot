"""
Константы для приложения Bedolaga Remnawave Bot
"""

# Лимиты для промокодов
PROMOCODE_BALANCE_MIN = 1
PROMOCODE_BALANCE_MAX = 10000  # рублей
PROMOCODE_DAYS_MIN = 1
PROMOCODE_DAYS_MAX = 3650  # дней (10 лет)
PROMOCODE_USES_MIN = 0
PROMOCODE_USES_MAX = 100000
PROMOCODE_UNLIMITED_USES = 999999

# Лимиты для транзакций
TRANSACTIONS_PER_PAGE = 10

# Лимиты для быстрого выбора сумм
QUICK_AMOUNT_BUTTONS_MAX = 6  # максимум кнопок быстрого выбора
QUICK_AMOUNT_BUTTONS_PER_ROW = 2  # кнопок в ряду

# Лимиты для серверов
SERVER_LIMIT_MIN = 1
SERVER_LIMIT_MAX = 1000

# Лимиты для устройств
DEVICE_LIMIT_MIN = 1
DEVICE_LIMIT_MAX = 20

# Лимиты для трафика (в ГБ)
TRAFFIC_LIMIT_MIN = 1
TRAFFIC_LIMIT_MAX = 10000

# Лимиты для периодов подписки (в днях)
SUBSCRIPTION_PERIOD_MIN = 1
SUBSCRIPTION_PERIOD_MAX = 3650

# Лимиты для баланса (в копейках)
BALANCE_MIN = 100  # 1 рубль
BALANCE_MAX = 10000000  # 100,000 рублей

# Сообщения об ошибках
ERROR_MESSAGES = {
    'INVALID_NUMBER': "❌ Введите корректное число",
    'INVALID_BALANCE': f"❌ Сумма должна быть от {PROMOCODE_BALANCE_MIN} до {PROMOCODE_BALANCE_MAX} рублей",
    'INVALID_DAYS': f"❌ Количество дней должно быть от {PROMOCODE_DAYS_MIN} до {PROMOCODE_DAYS_MAX}",
    'INVALID_USES': f"❌ Количество использований должно быть от {PROMOCODE_USES_MIN} до {PROMOCODE_USES_MAX}",
    'INVALID_EXPIRY': f"❌ Срок действия должен быть от 0 до {PROMOCODE_DAYS_MAX} дней",
    'INVALID_SERVER_LIMIT': f"❌ Лимит серверов должен быть от {SERVER_LIMIT_MIN} до {SERVER_LIMIT_MAX}",
    'INVALID_DEVICE_LIMIT': f"❌ Лимит устройств должен быть от {DEVICE_LIMIT_MIN} до {DEVICE_LIMIT_MAX}",
    'INVALID_TRAFFIC_LIMIT': f"❌ Лимит трафика должен быть от {TRAFFIC_LIMIT_MIN} до {TRAFFIC_LIMIT_MAX} ГБ",
    'INVALID_SUBSCRIPTION_PERIOD': f"❌ Период подписки должен быть от {SUBSCRIPTION_PERIOD_MIN} до {SUBSCRIPTION_PERIOD_MAX} дней",
    'INVALID_BALANCE_AMOUNT': f"❌ Сумма должна быть от {BALANCE_MIN//100} до {BALANCE_MAX//100} рублей",
}

# Типы промокодов
PROMOCODE_TYPES = {
    'BALANCE': 'balance',
    'DAYS': 'days', 
    'TRIAL': 'trial'
}

# Названия типов промокодов
PROMOCODE_TYPE_NAMES = {
    'balance': 'Пополнение баланса',
    'days': 'Дни подписки',
    'trial': 'Тестовая подписка'
}

# Названия типов транзакций
TRANSACTION_TYPE_NAMES = {
    'BALANCE_TOPUP': 'Пополнение баланса',
    'SUBSCRIPTION_PAYMENT': 'Оплата подписки',
    'REFERRAL_BONUS': 'Реферальный бонус',
    'PROMOCODE_BONUS': 'Бонус по промокоду',
    'ADMIN_REFUND': 'Возврат администратором',
    'ADMIN_ADD': 'Добавление администратором'
}

# Лимиты для пагинации
PAGINATION_LIMITS = {
    'USERS_PER_PAGE': 20,
    'TRANSACTIONS_PER_PAGE': 10,
    'SUBSCRIPTIONS_PER_PAGE': 15,
    'PROMOCODES_PER_PAGE': 10,
    'SERVERS_PER_PAGE': 20
}

# Лимиты для валидации
VALIDATION_LIMITS = {
    'USERNAME_MIN_LENGTH': 3,
    'USERNAME_MAX_LENGTH': 32,
    'FIRST_NAME_MIN_LENGTH': 1,
    'FIRST_NAME_MAX_LENGTH': 64,
    'LAST_NAME_MIN_LENGTH': 1,
    'LAST_NAME_MAX_LENGTH': 64,
    'MESSAGE_MAX_LENGTH': 4096,
    'CALLBACK_DATA_MAX_LENGTH': 64
}

# Коды ошибок
ERROR_CODES = {
    'VALIDATION_ERROR': 'VALIDATION_ERROR',
    'PERMISSION_DENIED': 'PERMISSION_DENIED',
    'NOT_FOUND': 'NOT_FOUND',
    'ALREADY_EXISTS': 'ALREADY_EXISTS',
    'INSUFFICIENT_FUNDS': 'INSUFFICIENT_FUNDS',
    'INVALID_STATE': 'INVALID_STATE',
    'EXTERNAL_API_ERROR': 'EXTERNAL_API_ERROR',
    'DATABASE_ERROR': 'DATABASE_ERROR'
}
