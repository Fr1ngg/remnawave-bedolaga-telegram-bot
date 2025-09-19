from typing import List, Optional
from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime
from app.database.models import User
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings, PERIOD_PRICES, TRAFFIC_PRICES
from app.localization.texts import get_texts
from app.utils.pricing_utils import format_period_description
import logging

logger = logging.getLogger(__name__)

def get_rules_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=texts.RULES_ACCEPT, callback_data="rules_accept"),
            InlineKeyboardButton(text=texts.RULES_DECLINE, callback_data="rules_decline")
        ]
    ])

def get_channel_sub_keyboard(channel_link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔗 Подписаться", url=channel_link
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Я подписался", callback_data="sub_channel_check"
                )
            ]
        ]
    )


def get_post_registration_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🚀 Подключиться бесплатно 🚀", callback_data="trial_activate"
            )
        ],
        [InlineKeyboardButton(text="Пропустить ➡️", callback_data="back_to_menu")],
    ])


def get_main_menu_keyboard(
    language: str = "ru",
    is_admin: bool = False,
    has_had_paid_subscription: bool = False,
    has_active_subscription: bool = False,
    subscription_is_active: bool = False,
    balance_kopeks: int = 0,
    subscription=None,
    show_resume_checkout: bool = False,
) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    
    if settings.DEBUG:
        print(f"DEBUG KEYBOARD: language={language}, is_admin={is_admin}, has_had_paid={has_had_paid_subscription}, has_active={has_active_subscription}, sub_active={subscription_is_active}, balance={balance_kopeks}")
    
    if hasattr(texts, 'BALANCE_BUTTON') and balance_kopeks > 0:
        balance_button_text = texts.BALANCE_BUTTON.format(balance=texts.format_price(balance_kopeks))
    else:
        balance_button_text = f"💰 Баланс: {texts.format_price(balance_kopeks)}"
    
    keyboard = []

    if has_active_subscription and subscription_is_active:
        connect_mode = settings.CONNECT_BUTTON_MODE
        if connect_mode == "miniapp_subscription":
            keyboard.append([
                InlineKeyboardButton(
                    text="🔗 Подключиться",
                    web_app=types.WebAppInfo(url=subscription.subscription_url)
                )
            ])
        elif connect_mode == "miniapp_custom":
            keyboard.append([
                InlineKeyboardButton(
                    text="🔗 Подключиться",
                    web_app=types.WebAppInfo(url=settings.MINIAPP_CUSTOM_URL)
                )
            ])
        elif connect_mode == "link":
            keyboard.append([
                InlineKeyboardButton(
                    text="🔗 Подключиться",
                    url=subscription.subscription_url
                )
            ])
        else:
            keyboard.append([
                InlineKeyboardButton(
                    text="🔗 Подключиться",
                    callback_data="subscription_connect"
                )
            ])

        keyboard.append([
            InlineKeyboardButton(text=balance_button_text, callback_data="menu_balance"),
            InlineKeyboardButton(text=texts.MENU_SUBSCRIPTION, callback_data="menu_subscription")
        ])
    else:
        keyboard.append([
            InlineKeyboardButton(text=balance_button_text, callback_data="menu_balance")
        ])
    
    show_trial = not has_had_paid_subscription and not has_active_subscription
    
    show_buy = not has_active_subscription or not subscription_is_active
    
    subscription_buttons = []
    
    if show_trial:
        subscription_buttons.append(
            InlineKeyboardButton(text=texts.MENU_TRIAL, callback_data="menu_trial")
        )
    
    if show_buy:
        subscription_buttons.append(
            InlineKeyboardButton(text=texts.MENU_BUY_SUBSCRIPTION, callback_data="menu_buy")
        )
    
    if subscription_buttons:
        if len(subscription_buttons) == 2:
            keyboard.append(subscription_buttons)
        else:
            keyboard.append([subscription_buttons[0]])

    if show_resume_checkout:
        keyboard.append([
            InlineKeyboardButton(
                text=texts.RETURN_TO_SUBSCRIPTION_CHECKOUT,
                callback_data="subscription_resume_checkout",
            )
        ])

    keyboard.extend([
        [
            InlineKeyboardButton(text=texts.MENU_PROMOCODE, callback_data="menu_promocode"),
            InlineKeyboardButton(text=texts.MENU_REFERRALS, callback_data="menu_referrals")
        ],
        [
            InlineKeyboardButton(text=texts.MENU_SUPPORT, callback_data="menu_support"),
            InlineKeyboardButton(text=texts.MENU_RULES, callback_data="menu_rules")
        ]
    ])
    
    if settings.DEBUG:
        print(f"DEBUG KEYBOARD: is_admin={is_admin}, добавляем админ кнопку: {is_admin}")
    
    if is_admin:
        if settings.DEBUG:
            print("DEBUG KEYBOARD: Админ кнопка ДОБАВЛЕНА!")
        keyboard.append([
            InlineKeyboardButton(text=texts.MENU_ADMIN, callback_data="admin_panel")
        ])
    else:
        if settings.DEBUG:
            print("DEBUG KEYBOARD: Админ кнопка НЕ добавлена")
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_back_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")]
    ])


def get_insufficient_balance_keyboard(
    language: str = "ru",
    resume_callback: str | None = None,
) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=texts.GO_TO_BALANCE_TOP_UP,
                callback_data="balance_topup",
            )
        ]
    ]

    if resume_callback:
        keyboard.append([
            InlineKeyboardButton(
                text=texts.RETURN_TO_SUBSCRIPTION_CHECKOUT,
                callback_data=resume_callback,
            )
        ])

    keyboard.append([InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_subscription_keyboard(
    language: str = "ru", 
    has_subscription: bool = False, 
    is_trial: bool = False,
    subscription=None
) -> InlineKeyboardMarkup:
    from app.config import settings 
    
    texts = get_texts(language)
    keyboard = []

    if has_subscription:
        if subscription and subscription.subscription_url:
            connect_mode = settings.CONNECT_BUTTON_MODE
            
            if connect_mode == "miniapp_subscription":
                keyboard.append([
                    InlineKeyboardButton(
                        text="🔗 Подключиться",
                        web_app=types.WebAppInfo(url=subscription.subscription_url)
                    )
                ])
            elif connect_mode == "miniapp_custom":
                if settings.MINIAPP_CUSTOM_URL:
                    keyboard.append([
                        InlineKeyboardButton(
                            text="🔗 Подключиться",
                            web_app=types.WebAppInfo(url=settings.MINIAPP_CUSTOM_URL)
                        )
                    ])
                else:
                    keyboard.append([
                        InlineKeyboardButton(text="🔗 Подключиться", callback_data="subscription_connect")
                    ])
            elif connect_mode == "link":
                keyboard.append([
                    InlineKeyboardButton(text="🔗 Подключиться", url=subscription.subscription_url)
                ])
            else:
                keyboard.append([
                    InlineKeyboardButton(text="🔗 Подключиться", callback_data="subscription_connect")
                ])

        if not is_trial:
            keyboard.append([
                InlineKeyboardButton(text=texts.MENU_EXTEND_SUBSCRIPTION, callback_data="subscription_extend")
            ])
            keyboard.append([
                InlineKeyboardButton(text="💳 Автоплатеж", callback_data="subscription_autopay")
            ])

        if is_trial:
            keyboard.append([
                InlineKeyboardButton(text=texts.MENU_BUY_SUBSCRIPTION, callback_data="subscription_upgrade")
            ])
        else:
            keyboard.append([
                InlineKeyboardButton(text="⚙️ Настройки подписки", callback_data="subscription_settings")
            ])
    
    keyboard.append([
        InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_trial_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🎁 Активировать", callback_data="trial_activate"),
            InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")
        ]
    ])


def get_subscription_period_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard = []
    
    available_periods = settings.get_available_subscription_periods()
    
    period_texts = {
        14: texts.PERIOD_14_DAYS,
        30: texts.PERIOD_30_DAYS,
        60: texts.PERIOD_60_DAYS,
        90: texts.PERIOD_90_DAYS,
        180: texts.PERIOD_180_DAYS,
        360: texts.PERIOD_360_DAYS
    }
    
    for days in available_periods:
        if days in period_texts:
            keyboard.append([
                InlineKeyboardButton(
                    text=period_texts[days], 
                    callback_data=f"period_{days}"
                )
            ])
    
    keyboard.append([
        InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_traffic_packages_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    import logging
    logger = logging.getLogger(__name__)
    
    from app.config import settings
    
    if settings.is_traffic_fixed():
        return get_back_keyboard(language)
    
    logger.info(f"🔍 RAW CONFIG: '{settings.TRAFFIC_PACKAGES_CONFIG}'")
    
    all_packages = settings.get_traffic_packages()
    logger.info(f"🔍 ALL PACKAGES: {all_packages}")
    
    enabled_packages = [pkg for pkg in all_packages if pkg['enabled']]
    disabled_packages = [pkg for pkg in all_packages if not pkg['enabled']]
    
    logger.info(f"🔍 ENABLED: {len(enabled_packages)} packages")
    logger.info(f"🔍 DISABLED: {len(disabled_packages)} packages")
    
    for pkg in disabled_packages:
        logger.info(f"🔍 DISABLED PACKAGE: {pkg['gb']}GB = {pkg['price']} kopeks, enabled={pkg['enabled']}")
    
    texts = get_texts(language)
    keyboard = []
    
    traffic_packages = settings.get_traffic_packages()
    
    for package in traffic_packages:
        gb = package["gb"]
        price = package["price"]  
        enabled = package["enabled"]
        
        if not enabled:
            continue
        
        if gb == 0:
            text = f"♾️ Безлимит - {settings.format_price(package['price'])}"
        else:
            text = f"📊 {gb} ГБ - {settings.format_price(package['price'])}"
        
        keyboard.append([
            InlineKeyboardButton(text=text, callback_data=f"traffic_{gb}")
        ])

    if not keyboard:
        keyboard.append([
            InlineKeyboardButton(
                text="⚠️ Пакеты трафика не настроены", 
                callback_data="no_traffic_packages"
            )
        ])
    
    keyboard.append([
        InlineKeyboardButton(text=texts.BACK, callback_data="subscription_config_back")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_countries_keyboard(countries: List[dict], selected: List[str], language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard = []
    
    for country in countries:
        if not country.get('is_available', True):
            continue 
            
        emoji = "✅" if country['uuid'] in selected else "⚪"
        
        if country['price_kopeks'] > 0:
            price_text = f" (+{texts.format_price(country['price_kopeks'])})"
        else:
            price_text = " (Бесплатно)"
        
        keyboard.append([
            InlineKeyboardButton(
                text=f"{emoji} {country['name']}{price_text}",
                callback_data=f"country_{country['uuid']}"
            )
        ])
    
    if not keyboard:
        keyboard.append([
            InlineKeyboardButton(
                text="❌ Нет доступных серверов",
                callback_data="no_servers"
            )
        ])
    
    keyboard.extend([
        [InlineKeyboardButton(text="✅ Продолжить", callback_data="countries_continue")],
        [InlineKeyboardButton(text=texts.BACK, callback_data="subscription_config_back")]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_devices_keyboard(current: int, language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard = []
    
    start_devices = settings.DEFAULT_DEVICE_LIMIT
    max_devices = settings.MAX_DEVICES_LIMIT if settings.MAX_DEVICES_LIMIT > 0 else 50
    end_devices = min(max_devices + 1, start_devices + 10)
    
    buttons = []
    
    for devices in range(start_devices, end_devices): 
        price = max(0, devices - settings.DEFAULT_DEVICE_LIMIT) * settings.PRICE_PER_DEVICE
        price_text = f" (+{texts.format_price(price)})" if price > 0 else " (вкл.)"
        emoji = "✅" if devices == current else "⚪"
        
        button_text = f"{emoji} {devices}{price_text}"
        
        buttons.append(
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"devices_{devices}"
            )
        )
    
    for i in range(0, len(buttons), 2):
        if i + 1 < len(buttons):
            keyboard.append([buttons[i], buttons[i + 1]])
        else:
            keyboard.append([buttons[i]])
    
    keyboard.extend([
        [InlineKeyboardButton(text="✅ Продолжить", callback_data="devices_continue")],
        [InlineKeyboardButton(text=texts.BACK, callback_data="subscription_config_back")]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def _get_device_declension(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "устройство"
    elif count % 10 in [2, 3, 4] and count % 100 not in [12, 13, 14]:
        return "устройства"
    else:
        return "устройств"

def get_subscription_confirm_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=texts.CONFIRM, callback_data="subscription_confirm"),
            InlineKeyboardButton(text=texts.CANCEL, callback_data="subscription_cancel")
        ]
    ])


def get_balance_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    
    keyboard = [
        [
            InlineKeyboardButton(text=texts.BALANCE_HISTORY, callback_data="balance_history"),
            InlineKeyboardButton(text=texts.BALANCE_TOP_UP, callback_data="balance_topup")
        ],
        [
            InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")
        ]
    ]
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_payment_methods_keyboard(amount_kopeks: int, language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard = []
    
    
    if settings.TELEGRAM_STARS_ENABLED:
        keyboard.append([
            InlineKeyboardButton(
                text="⭐ Telegram Stars", 
                callback_data="topup_stars"
            )
        ])
    
    if settings.is_yookassa_enabled():
        keyboard.append([
            InlineKeyboardButton(
                text="💳 Банковская карта (YooKassa)",
                callback_data="topup_yookassa"
            )
        ])

        if settings.YOOKASSA_SBP_ENABLED:
            keyboard.append([
                InlineKeyboardButton(
                    text="🏦 Оплатить по СБП (YooKassa)",
                    callback_data="topup_yookassa_sbp"
                )
            ])

    if settings.is_mulenpay_enabled():
        keyboard.append([
            InlineKeyboardButton(
                text="💳 Банковская карта (MulenPay)",
                callback_data="topup_mulenpay"
            )
        ])

    if settings.TRIBUTE_ENABLED:
        keyboard.append([
            InlineKeyboardButton(
                text="💳 Банковская карта (Tribute)",
                callback_data="topup_tribute"
            )
        ])

    if settings.is_cryptobot_enabled():
        keyboard.append([
            InlineKeyboardButton(
                text="🪙 Криптовалюта (CryptoBot)", 
                callback_data="topup_cryptobot"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            text="🛠️ Через поддержку", 
            callback_data="topup_support"
        )
    ])
    
    if len(keyboard) == 1:  
        keyboard.insert(0, [
            InlineKeyboardButton(
                text="⚠️ Способы оплаты временно недоступны",
                callback_data="payment_methods_unavailable"
            )
        ])
    
    keyboard.append([
        InlineKeyboardButton(text=texts.BACK, callback_data="menu_balance")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_yookassa_payment_keyboard(
    payment_id: str, 
    amount_kopeks: int, 
    confirmation_url: str,
    language: str = "ru"
) -> InlineKeyboardMarkup:
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="💳 Оплатить",
                url=confirmation_url
            )
        ],
        [
            InlineKeyboardButton(
                text="📊 Проверить статус",
                callback_data=f"check_yookassa_status_{payment_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text="💰 Мой баланс",
                callback_data="menu_balance"
            )
        ]
    ])

def get_autopay_notification_keyboard(subscription_id: int, language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="💳 Пополнить баланс", 
                callback_data="balance_topup"
            )
        ],
        [
            InlineKeyboardButton(
                text="📱 Моя подписка", 
                callback_data="menu_subscription"
            )
        ]
    ])

def get_subscription_expiring_keyboard(subscription_id: int, language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=texts.MENU_EXTEND_SUBSCRIPTION, 
                callback_data="subscription_extend"
            )
        ],
        [
            InlineKeyboardButton(
                text="💳 Пополнить баланс", 
                callback_data="balance_topup"
            )
        ],
        [
            InlineKeyboardButton(
                text="📱 Моя подписка", 
                callback_data="menu_subscription"
            )
        ]
    ])

def get_referral_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    
    keyboard = [
        [
            InlineKeyboardButton(
                text="📝 Создать приглашение",
                callback_data="referral_create_invite"
            )
        ],
        [
            InlineKeyboardButton(
                text="📱 Показать QR код",
                callback_data="referral_show_qr"
            )
        ],
        [
            InlineKeyboardButton(
                text="👥 Список рефералов",
                callback_data="referral_list"
            )
        ],
        [
            InlineKeyboardButton(
                text="📊 Аналитика",
                callback_data="referral_analytics"
            )
        ],
        [
            InlineKeyboardButton(
                text=texts.BACK,
                callback_data="back_to_menu" 
            )
        ]
    ]
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_support_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=texts.CONTACT_SUPPORT,
                url=settings.get_support_contact_url() or "https://t.me/"
            )
        ],
        [
            InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")
        ]
    ])


def get_pagination_keyboard(
    current_page: int,
    total_pages: int,
    callback_prefix: str,
    language: str = "ru"
) -> List[List[InlineKeyboardButton]]:
    keyboard = []
    
    if total_pages > 1:
        row = []
        
        if current_page > 1:
            row.append(InlineKeyboardButton(
                text="⬅️",
                callback_data=f"{callback_prefix}_page_{current_page - 1}"
            ))
        
        row.append(InlineKeyboardButton(
            text=f"{current_page}/{total_pages}",
            callback_data="current_page"
        ))
        
        if current_page < total_pages:
            row.append(InlineKeyboardButton(
                text="➡️",
                callback_data=f"{callback_prefix}_page_{current_page + 1}"
            ))
        
        keyboard.append(row)
    
    return keyboard

def get_confirmation_keyboard(
    confirm_data: str,
    cancel_data: str = "cancel",
    language: str = "ru"
) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=texts.YES, callback_data=confirm_data),
            InlineKeyboardButton(text=texts.NO, callback_data=cancel_data)
        ]
    ])


def get_autopay_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Включить", callback_data="autopay_enable"),
            InlineKeyboardButton(text="❌ Выключить", callback_data="autopay_disable")
        ],
        [
            InlineKeyboardButton(text="⚙️ Настроить дни", callback_data="autopay_set_days")
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_subscription")
        ]
    ])


def get_autopay_days_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    keyboard = []
    
    for days in [1, 3, 7, 14]:
        keyboard.append([
            InlineKeyboardButton(
                text=f"{days} дн{_get_days_suffix(days)}",
                callback_data=f"autopay_days_{days}"
            )
        ])
    
    keyboard.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data="subscription_autopay")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def _get_days_suffix(days: int) -> str:
    if days == 1:
        return "ь"
    elif 2 <= days <= 4:
        return "я"
    else:
        return "ей"



def get_extend_subscription_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard = []
    
    periods = [
        (14, f"📅 14 дней - {settings.format_price(settings.PRICE_14_DAYS)}"),
        (30, f"📅 30 дней - {settings.format_price(settings.PRICE_30_DAYS)}"),
        (60, f"📅 60 дней - {settings.format_price(settings.PRICE_60_DAYS)}"),
        (90, f"📅 90 дней - {settings.format_price(settings.PRICE_90_DAYS)}")
    ]
    
    for days, text in periods:
        keyboard.append([
            InlineKeyboardButton(text=text, callback_data=f"extend_period_{days}")
        ])
    
    keyboard.append([
        InlineKeyboardButton(text=texts.BACK, callback_data="menu_subscription")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_add_traffic_keyboard(language: str = "ru", subscription_end_date: datetime = None) -> InlineKeyboardMarkup:
    from app.utils.pricing_utils import get_remaining_months
    from app.config import settings
    
    months_multiplier = 1
    period_text = ""
    if subscription_end_date:
        months_multiplier = get_remaining_months(subscription_end_date)
        if months_multiplier > 1:
            period_text = f" (за {months_multiplier} мес)"
    
    packages = settings.get_traffic_packages()
    enabled_packages = [pkg for pkg in packages if pkg['enabled']]
    
    if not enabled_packages:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="❌ Нет доступных пакетов" if language == "ru" else "❌ No packages available",
                callback_data="no_traffic_packages"
            )],
            [InlineKeyboardButton(
                text="⬅️ Назад" if language == "ru" else "⬅️ Back",
                callback_data="menu_subscription"
            )]
        ])
    
    buttons = []
    
    for package in enabled_packages:
        gb = package['gb']
        price_per_month = package['price']
        total_price = price_per_month * months_multiplier
        
        if gb == 0:
            if language == "ru":
                text = f"♾️ Безлимитный трафик - {total_price//100} ₽{period_text}"
            else:
                text = f"♾️ Unlimited traffic - {total_price//100} ₽{period_text}"
        else:
            if language == "ru":
                text = f"📊 +{gb} ГБ трафика - {total_price//100} ₽{period_text}"
            else:
                text = f"📊 +{gb} GB traffic - {total_price//100} ₽{period_text}"
        
        buttons.append([
            InlineKeyboardButton(text=text, callback_data=f"add_traffic_{gb}")
        ])
    
    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Назад" if language == "ru" else "⬅️ Back",
            callback_data="menu_subscription"
        )
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)
    
def get_change_devices_keyboard(current_devices: int, language: str = "ru", subscription_end_date: datetime = None) -> InlineKeyboardMarkup:
    from app.utils.pricing_utils import get_remaining_months
    from app.config import settings
    
    months_multiplier = 1
    period_text = ""
    if subscription_end_date:
        months_multiplier = get_remaining_months(subscription_end_date)
        if months_multiplier > 1:
            period_text = f" (за {months_multiplier} мес)"
    
    device_price_per_month = settings.PRICE_PER_DEVICE
    
    buttons = []
    
    min_devices = 1 
    max_devices = settings.MAX_DEVICES_LIMIT if settings.MAX_DEVICES_LIMIT > 0 else 20
    
    start_range = max(1, min(current_devices - 3, max_devices - 6))
    end_range = min(max_devices + 1, max(current_devices + 4, 7))
    
    for devices_count in range(start_range, end_range):
        if devices_count == current_devices:
            emoji = "✅"
            action_text = " (текущее)"
            price_text = ""
        elif devices_count > current_devices:
            emoji = "➕"
            additional_devices = devices_count - current_devices
            
            current_chargeable = max(0, current_devices - settings.DEFAULT_DEVICE_LIMIT)
            new_chargeable = max(0, devices_count - settings.DEFAULT_DEVICE_LIMIT)
            chargeable_devices = new_chargeable - current_chargeable
            
            if chargeable_devices > 0:
                price_per_month = chargeable_devices * device_price_per_month
                total_price = price_per_month * months_multiplier
                price_text = f" (+{total_price//100}₽{period_text})"
                action_text = ""
            else:
                price_text = " (бесплатно)"
                action_text = ""
        else:
            emoji = "➖"
            action_text = ""
            price_text = " (без возврата)"
        
        button_text = f"{emoji} {devices_count} устр.{action_text}{price_text}"
        
        buttons.append([
            InlineKeyboardButton(text=button_text, callback_data=f"change_devices_{devices_count}")
        ])
    
    if current_devices < start_range or current_devices >= end_range:
        current_button = f"✅ {current_devices} устр. (текущее)"
        buttons.insert(0, [
            InlineKeyboardButton(text=current_button, callback_data=f"change_devices_{current_devices}")
        ])
    
    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Назад" if language == "ru" else "⬅️ Back",
            callback_data="subscription_settings"
        )
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_confirm_change_devices_keyboard(new_devices_count: int, price: int, language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Подтвердить изменение",
                callback_data=f"confirm_change_devices_{new_devices_count}_{price}"
            )
        ],
        [
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data="subscription_settings"
            )
        ]
    ])


def get_reset_traffic_confirm_keyboard(price_kopeks: int, language: str = "ru") -> InlineKeyboardMarkup:
    from app.config import settings
    
    if settings.is_traffic_fixed():
        return get_back_keyboard(language)
    
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"✅ Сбросить за {settings.format_price(price_kopeks)}", 
                callback_data="confirm_reset_traffic"
            )
        ],
        [
            InlineKeyboardButton(text="⌛ Отмена", callback_data="menu_subscription")
        ]
    ])

def get_manage_countries_keyboard(
    countries: List[dict], 
    selected: List[str], 
    current_subscription_countries: List[str],
    language: str = "ru",
    subscription_end_date: datetime = None
) -> InlineKeyboardMarkup:
    from app.utils.pricing_utils import get_remaining_months
    
    months_multiplier = 1
    if subscription_end_date:
        months_multiplier = get_remaining_months(subscription_end_date)
        logger.info(f"🔍 Расчет для управления странами: осталось {months_multiplier} месяцев до {subscription_end_date}")
    
    buttons = []
    total_cost = 0
    
    for country in countries:
        uuid = country['uuid']
        name = country['name']
        price_per_month = country['price_kopeks']
        
        if uuid in current_subscription_countries:
            if uuid in selected:
                icon = "✅"
            else:
                icon = "➖"
        else:
            if uuid in selected:
                icon = "➕"
                total_cost += price_per_month * months_multiplier
            else:
                icon = "⚪"
        
        if uuid not in current_subscription_countries and uuid in selected:
            total_price = price_per_month * months_multiplier
            if months_multiplier > 1:
                price_text = f" ({price_per_month//100}₽/мес × {months_multiplier} = {total_price//100}₽)"
                logger.info(f"🔍 Сервер {name}: {price_per_month/100}₽/мес × {months_multiplier} мес = {total_price/100}₽")
            else:
                price_text = f" ({total_price//100}₽)"
            display_name = f"{icon} {name}{price_text}"
        else:
            display_name = f"{icon} {name}"
        
        buttons.append([
            InlineKeyboardButton(
                text=display_name,
                callback_data=f"country_manage_{uuid}"
            )
        ])
    
    if total_cost > 0:
        apply_text = f"✅ Применить изменения ({total_cost//100} ₽)"
        logger.info(f"🔍 Общая стоимость новых серверов: {total_cost/100}₽")
    else:
        apply_text = "✅ Применить изменения"
    
    buttons.append([
        InlineKeyboardButton(text=apply_text, callback_data="countries_apply")
    ])
    
    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Назад" if language == "ru" else "⬅️ Back",
            callback_data="menu_subscription"
        )
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_device_selection_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    from app.config import settings
    
    keyboard = [
        [
            InlineKeyboardButton(text="📱 iOS (iPhone/iPad)", callback_data="device_guide_ios"),
            InlineKeyboardButton(text="🤖 Android", callback_data="device_guide_android")
        ],
        [
            InlineKeyboardButton(text="💻 Windows", callback_data="device_guide_windows"),
            InlineKeyboardButton(text="🎯 macOS", callback_data="device_guide_mac")
        ],
        [
            InlineKeyboardButton(text="📺 Android TV", callback_data="device_guide_tv")
        ]
    ]
    
    if settings.CONNECT_BUTTON_MODE == "guide":
        keyboard.append([
            InlineKeyboardButton(text="📋 Показать ссылку подписки", callback_data="open_subscription_link")
        ])
    
    keyboard.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_subscription")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_connection_guide_keyboard(
    subscription_url: str, 
    app: dict, 
    language: str = "ru"
) -> InlineKeyboardMarkup:
    from app.handlers.subscription import create_deep_link
    
    keyboard = []
    
    if 'installationStep' in app and 'buttons' in app['installationStep']:
        app_buttons = []
        for button in app['installationStep']['buttons']:
            button_text = button['buttonText'].get(language, button['buttonText']['en'])
            app_buttons.append(
                InlineKeyboardButton(text=f"📥 {button_text}", url=button['buttonLink'])
            )
            if len(app_buttons) == 2:
                keyboard.append(app_buttons)
                app_buttons = []
        
        if app_buttons:
            keyboard.append(app_buttons)
    
    keyboard.append([
        InlineKeyboardButton(text="📋 Скопировать ссылку подписки", url=subscription_url)
    ])
    
    keyboard.extend([
        [
            InlineKeyboardButton(text="📱 Выбрать другое устройство", callback_data="subscription_connect")
        ],
        [
            InlineKeyboardButton(text="⬅️ К подписке", callback_data="menu_subscription")
        ]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_app_selection_keyboard(
    device_type: str, 
    apps: list, 
    language: str = "ru"
) -> InlineKeyboardMarkup:
    keyboard = []
    
    for app in apps:
        app_name = app['name']
        if app.get('isFeatured', False):
            app_name = f"⭐ {app_name}"
        
        keyboard.append([
            InlineKeyboardButton(
                text=app_name, 
                callback_data=f"app_{device_type}_{app['id']}"
            )
        ])
    
    keyboard.extend([
        [
            InlineKeyboardButton(text="📱 Выбрать другое устройство", callback_data="subscription_connect")
        ],
        [
            InlineKeyboardButton(text="⬅️ К подписке", callback_data="menu_subscription")
        ]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_specific_app_keyboard(
    subscription_url: str,
    app: dict,
    device_type: str,
    language: str = "ru"
) -> InlineKeyboardMarkup:
    from app.handlers.subscription import create_deep_link
    
    keyboard = []
    
    if 'installationStep' in app and 'buttons' in app['installationStep']:
        app_buttons = []
        for button in app['installationStep']['buttons']:
            button_text = button['buttonText'].get(language, button['buttonText']['en'])
            app_buttons.append(
                InlineKeyboardButton(text=f"📥 {button_text}", url=button['buttonLink'])
            )
            if len(app_buttons) == 2:
                keyboard.append(app_buttons)
                app_buttons = []
        
        if app_buttons:
            keyboard.append(app_buttons)
    
    keyboard.append([
        InlineKeyboardButton(text="📋 Скопировать ссылку подписки", url=subscription_url)
    ])
    
    if 'additionalAfterAddSubscriptionStep' in app and 'buttons' in app['additionalAfterAddSubscriptionStep']:
        for button in app['additionalAfterAddSubscriptionStep']['buttons']:
            button_text = button['buttonText'].get(language, button['buttonText']['en'])
            keyboard.append([
                InlineKeyboardButton(text=button_text, url=button['buttonLink'])
            ])
    
    keyboard.extend([
        [
            InlineKeyboardButton(text="📋 Другие приложения", callback_data=f"app_list_{device_type}")
        ],
        [
            InlineKeyboardButton(text="📱 Выбрать другое устройство", callback_data="subscription_connect")
        ],
        [
            InlineKeyboardButton(text="⬅️ К подписке", callback_data="menu_subscription")
        ]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_extend_subscription_keyboard_with_prices(language: str, prices: dict) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard = []

    available_periods = settings.get_available_renewal_periods()

    for days in available_periods:
        if days in prices:
            period_display = format_period_description(days, language)
            keyboard.append([
                InlineKeyboardButton(
                    text=f"📅 {period_display} - {texts.format_price(prices[days])}",
                    callback_data=f"extend_period_{days}"
                )
            ])

    keyboard.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_subscription")
    ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_cryptobot_payment_keyboard(
    payment_id: str,
    local_payment_id: int,
    amount_usd: float,
    asset: str,
    bot_invoice_url: str,
    language: str = "ru"
) -> InlineKeyboardMarkup:
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🪙 Оплатить",
                url=bot_invoice_url
            )
        ],
        [
            InlineKeyboardButton(
                text="📊 Проверить статус",
                callback_data=f"check_cryptobot_{local_payment_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text="💰 Мой баланс",
                callback_data="menu_balance"
            )
        ]
    ])

def get_devices_management_keyboard(
    devices: List[dict], 
    pagination,
    language: str = "ru"
) -> InlineKeyboardMarkup:
    
    keyboard = []
    
    for i, device in enumerate(devices):
        platform = device.get('platform', 'Unknown')
        device_model = device.get('deviceModel', 'Unknown')
        device_info = f"{platform} - {device_model}"
        
        if len(device_info) > 25:
            device_info = device_info[:22] + "..."
        
        keyboard.append([
            InlineKeyboardButton(
                text=f"🔄 {device_info}",
                callback_data=f"reset_device_{i}_{pagination.page}"
            )
        ])
    
    if pagination.total_pages > 1:
        nav_row = []
        
        if pagination.has_prev:
            nav_row.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"devices_page_{pagination.prev_page}"
                )
            )
        
        nav_row.append(
            InlineKeyboardButton(
                text=f"{pagination.page}/{pagination.total_pages}",
                callback_data="current_page"
            )
        )
        
        if pagination.has_next:
            nav_row.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"devices_page_{pagination.next_page}"
                )
            )
        
        keyboard.append(nav_row)
    
    keyboard.append([
        InlineKeyboardButton(
            text="🔄 Сбросить все устройства",
            callback_data="reset_all_devices"
        )
    ])
    
    keyboard.append([
        InlineKeyboardButton(
            text="⬅️ Назад" if language == "ru" else "⬅️ Back",
            callback_data="subscription_settings"
        )
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_updated_subscription_settings_keyboard(language: str = "ru", show_countries_management: bool = True) -> InlineKeyboardMarkup:
    from app.config import settings
    
    texts = get_texts(language)
    keyboard = []
    
    if show_countries_management:
        keyboard.append([
            InlineKeyboardButton(text="🌐 Добавить страны", callback_data="subscription_add_countries")
        ])
    
    keyboard.extend([
        [
            InlineKeyboardButton(text="📱 Изменить устройства", callback_data="subscription_change_devices") 
        ],
        [
            InlineKeyboardButton(text="🔧 Управление устройствами", callback_data="subscription_manage_devices")
        ]
    ])
    
    if settings.is_traffic_selectable():
        keyboard.insert(-2, [
            InlineKeyboardButton(text="🔄 Переключить трафик", callback_data="subscription_switch_traffic")
        ])
        keyboard.insert(-2, [
            InlineKeyboardButton(text="🔄 Сбросить трафик", callback_data="subscription_reset_traffic")
        ])
    
    keyboard.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_subscription")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_device_reset_confirm_keyboard(device_info: str, device_index: int, page: int, language: str = "ru") -> InlineKeyboardMarkup:
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Да, сбросить это устройство", 
                callback_data=f"confirm_reset_device_{device_index}_{page}"
            )
        ],
        [
            InlineKeyboardButton(
                text="❌ Отмена", 
                callback_data=f"devices_page_{page}"
            )
        ]
    ])


def get_device_management_help_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="❓ Как подключить устройство заново?",
                callback_data="device_connection_help"
            )
        ],
        [
            InlineKeyboardButton(
                text="🔧 Управление устройствами",
                callback_data="subscription_manage_devices"
            )
        ],
        [
            InlineKeyboardButton(
                text="⬅️ К подписке",
                callback_data="menu_subscription"
            )
        ]
    ])
