from typing import Dict, List, Optional

from app.config import settings


PAYMENT_METHOD_DISPLAY: Dict[Optional[str], str] = {
    None: "💰 С баланса",
    "telegram_stars": "⭐ Telegram Stars",
    "yookassa": "💳 YooKassa (карта)",
    "mulenpay": "💳 MulenPay (карта)",
    "tribute": "💳 Tribute (карта)",
    "cryptobot": "🪙 CryptoBot",
    "manual": "🛠️ Вручную (админ)",
    "balance": "💰 С баланса",
}

def get_available_payment_methods() -> List[Dict[str, str]]:
    """
    Возвращает список доступных способов оплаты с их настройками
    """
    methods = []
    
    if settings.TELEGRAM_STARS_ENABLED:
        methods.append({
            "id": "stars",
            "name": "Telegram Stars",
            "icon": "⭐",
            "description": "быстро и удобно",
            "callback": "topup_stars"
        })
    
    if settings.is_yookassa_enabled():
        methods.append({
            "id": "yookassa", 
            "name": "Банковская карта",
            "icon": "💳",
            "description": "через YooKassa",
            "callback": "topup_yookassa"
        })
    
    if settings.is_mulenpay_enabled():
        methods.append({
            "id": "mulenpay",
            "name": "Банковская карта",
            "icon": "💳",
            "description": "через MulenPay",
            "callback": "topup_mulenpay"
        })

    if settings.TRIBUTE_ENABLED:
        methods.append({
            "id": "tribute",
            "name": "Банковская карта",
            "icon": "💳",
            "description": "через Tribute",
            "callback": "topup_tribute"
        })
        
    if settings.is_cryptobot_enabled():
        methods.append({
            "id": "cryptobot",
            "name": "Криптовалюта",
            "icon": "🪙", 
            "description": "через CryptoBot",
            "callback": "topup_cryptobot"
        })
    
    # Поддержка всегда доступна
    methods.append({
        "id": "support",
        "name": "Через поддержку",
        "icon": "🛠️",
        "description": "другие способы",
        "callback": "topup_support"
    })
    
    return methods

def get_payment_methods_text() -> str:
    """
    Генерирует текст с описанием доступных способов оплаты
    """
    methods = get_available_payment_methods()
    
    if len(methods) <= 1:  # Только поддержка
        return """💳 <b>Способы пополнения баланса</b>

⚠️ В данный момент автоматические способы оплаты временно недоступны.
Обратитесь в техподдержку для пополнения баланса.

Выберите способ пополнения:"""
    
    text = "💳 <b>Способы пополнения баланса</b>\n\n"
    text += "Выберите удобный для вас способ оплаты:\n\n"
    
    for method in methods:
        text += f"{method['icon']} <b>{method['name']}</b> - {method['description']}\n"
    
    text += "\nВыберите способ пополнения:"
    
    return text

def is_payment_method_available(method_id: str) -> bool:
    """
    Проверяет, доступен ли конкретный способ оплаты
    """
    if method_id == "stars":
        return settings.TELEGRAM_STARS_ENABLED
    elif method_id == "yookassa":
        return settings.is_yookassa_enabled()
    elif method_id == "mulenpay":
        return settings.is_mulenpay_enabled()
    elif method_id == "tribute":
        return settings.TRIBUTE_ENABLED
    elif method_id == "cryptobot":
        return settings.is_cryptobot_enabled()
    elif method_id == "support":
        return True  # Поддержка всегда доступна
    else:
        return False

def get_payment_method_status() -> Dict[str, bool]:
    """
    Возвращает статус всех способов оплаты
    """
    return {
        "stars": settings.TELEGRAM_STARS_ENABLED,
        "yookassa": settings.is_yookassa_enabled(),
        "mulenpay": settings.is_mulenpay_enabled(),
        "tribute": settings.TRIBUTE_ENABLED,
        "cryptobot": settings.is_cryptobot_enabled(),
        "support": True
    }

def get_enabled_payment_methods_count() -> int:
    """
    Возвращает количество включенных способов оплаты (не считая поддержку)
    """
    count = 0
    if settings.TELEGRAM_STARS_ENABLED:
        count += 1
    if settings.is_yookassa_enabled():
        count += 1 
    if settings.is_mulenpay_enabled():
        count += 1
    if settings.TRIBUTE_ENABLED:
        count += 1
    if settings.is_cryptobot_enabled():
        count += 1
    return count


def get_payment_method_display(method_id: Optional[str]) -> str:
    """Возвращает локализованное название способа оплаты."""

    if method_id not in PAYMENT_METHOD_DISPLAY:
        return f"💰 {method_id}" if method_id else PAYMENT_METHOD_DISPLAY[None]

    return PAYMENT_METHOD_DISPLAY[method_id]
