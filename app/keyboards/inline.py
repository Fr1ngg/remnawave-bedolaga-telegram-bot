from typing import List, Optional
from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime
from app.database.models import User
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings, PERIOD_PRICES, TRAFFIC_PRICES
from app.localization.loader import DEFAULT_LANGUAGE
from app.localization.texts import get_texts
from app.utils.pricing_utils import format_period_description
from app.utils.subscription_utils import get_display_subscription_link
import logging

logger = logging.getLogger(__name__)

def get_rules_keyboard(language: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=texts.RULES_ACCEPT, callback_data="rules_accept"),
            InlineKeyboardButton(text=texts.RULES_DECLINE, callback_data="rules_decline")
        ]
    ])

def get_channel_sub_keyboard(
    channel_link: str,
    language: str = DEFAULT_LANGUAGE,
) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.t("CHANNEL_SUBSCRIBE_BUTTON", "🔗 Подписаться"),
                    url=channel_link,
                )
            ],
            [
                InlineKeyboardButton(
                    text=texts.t("CHANNEL_CHECK_BUTTON", "✅ Я подписался"),
                    callback_data="sub_channel_check",
                )
            ],
        ]
    )


def get_post_registration_keyboard(language: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=texts.t("POST_REGISTRATION_TRIAL_BUTTON", "🚀 Подключиться бесплатно 🚀"),
                callback_data="trial_activate"
            )
        ],
        [InlineKeyboardButton(text=texts.t("SKIP_BUTTON", "Пропустить ➡️"), callback_data="back_to_menu")],
    ])


def get_main_menu_keyboard(
    language: str = DEFAULT_LANGUAGE,
    is_admin: bool = False,
    has_had_paid_subscription: bool = False,
    has_active_subscription: bool = False,
    subscription_is_active: bool = False,
    balance_kopeks: int = 0,
    subscription=None,
    show_resume_checkout: bool = False,
    *,
    is_moderator: bool = False,
) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    
    if settings.DEBUG:
        print(f"DEBUG KEYBOARD: language={language}, is_admin={is_admin}, has_had_paid={has_had_paid_subscription}, has_active={has_active_subscription}, sub_active={subscription_is_active}, balance={balance_kopeks}")
    
    if hasattr(texts, 'BALANCE_BUTTON') and balance_kopeks > 0:
        balance_button_text = texts.BALANCE_BUTTON.format(balance=texts.format_price(balance_kopeks))
    else:
        balance_button_text = texts.t(
            "BALANCE_BUTTON_DEFAULT",
            "💰 Баланс: {balance}",
        ).format(balance=texts.format_price(balance_kopeks))
    
    keyboard = []

    if has_active_subscription and subscription_is_active:
        connect_mode = settings.CONNECT_BUTTON_MODE
        subscription_link = get_display_subscription_link(subscription)

        def _fallback_connect_button() -> InlineKeyboardButton:
            return InlineKeyboardButton(
                text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"),
                callback_data="subscription_connect",
            )

        if connect_mode == "miniapp_subscription":
            if subscription_link:
                keyboard.append([
                    InlineKeyboardButton(
                        text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"),
                        web_app=types.WebAppInfo(url=subscription_link)
                    )
                ])
            else:
                keyboard.append([_fallback_connect_button()])
        elif connect_mode == "miniapp_custom":
            keyboard.append([
                InlineKeyboardButton(
                    text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"),
                    web_app=types.WebAppInfo(url=settings.MINIAPP_CUSTOM_URL)
                )
            ])
        elif connect_mode == "link":
            if subscription_link:
                keyboard.append([
                    InlineKeyboardButton(
                        text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"),
                        url=subscription_link
                    )
                ])
            else:
                keyboard.append([_fallback_connect_button()])
        elif connect_mode == "happ_cryptolink":
            if subscription_link:
                keyboard.append([
                    InlineKeyboardButton(
                        text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"),
                        callback_data="open_subscription_link",
                    )
                ])
            else:
                keyboard.append([_fallback_connect_button()])
        else:
            keyboard.append([_fallback_connect_button()])

        happ_row = get_happ_download_button_row(texts)
        if happ_row:
            keyboard.append(happ_row)

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
        ]
    ])

    # Server status button
    server_status_mode = settings.get_server_status_mode()
    server_status_text = texts.t("MENU_SERVER_STATUS", "📊 Статус серверов")

    if server_status_mode == "external_link":
        status_url = settings.get_server_status_external_url()
        if status_url:
            keyboard.append([
                InlineKeyboardButton(text=server_status_text, url=status_url)
            ])
    elif server_status_mode == "external_link_miniapp":
        status_url = settings.get_server_status_external_url()
        if status_url:
            keyboard.append([
                InlineKeyboardButton(
                    text=server_status_text,
                    web_app=types.WebAppInfo(url=status_url),
                )
            ])
    elif server_status_mode == "xray":
        keyboard.append([
            InlineKeyboardButton(text=server_status_text, callback_data="menu_server_status")
        ])

    # Support button is configurable (runtime via service)
    try:
        from app.services.support_settings_service import SupportSettingsService
        support_enabled = SupportSettingsService.is_support_menu_enabled()
    except Exception:
        support_enabled = settings.SUPPORT_MENU_ENABLED
    support_row = []
    if support_enabled:
        support_row.append(InlineKeyboardButton(text=texts.MENU_SUPPORT, callback_data="menu_support"))
    support_row.append(InlineKeyboardButton(text=texts.MENU_RULES, callback_data="menu_rules"))
    keyboard.append(support_row)
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
    # Moderator access (limited support panel)
    if (not is_admin) and is_moderator:
        keyboard.append([
            InlineKeyboardButton(text="🧑‍⚖️ Модерация", callback_data="moderator_panel")
        ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_happ_download_button_row(texts) -> Optional[List[InlineKeyboardButton]]:
    if not settings.is_happ_download_button_enabled():
        return None

    return [
        InlineKeyboardButton(
            text=texts.t("HAPP_DOWNLOAD_BUTTON", "⬇️ Скачать Happ"),
            callback_data="subscription_happ_download"
        )
    ]


def get_happ_cryptolink_keyboard(
    button_link: Optional[str],
    language: str = DEFAULT_LANGUAGE,
) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    buttons: List[List[InlineKeyboardButton]] = []

    if button_link:
        buttons.append([
            InlineKeyboardButton(
                text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"),
                url=button_link,
            )
        ])

    buttons.extend(
        [
            [
                InlineKeyboardButton(
                    text=texts.t("HAPP_PLATFORM_IOS", "🍎 iOS"),
                    callback_data="happ_download_ios",
                )
            ],
            [
                InlineKeyboardButton(
                    text=texts.t("HAPP_PLATFORM_ANDROID", "🤖 Android"),
                    callback_data="happ_download_android",
                )
            ],
            [
                InlineKeyboardButton(
                    text=texts.t("HAPP_PLATFORM_MACOS", "🖥️ Mac OS"),
                    callback_data="happ_download_macos",
                )
            ],
            [
                InlineKeyboardButton(
                    text=texts.t("HAPP_PLATFORM_WINDOWS", "💻 Windows"),
                    callback_data="happ_download_windows",
                )
            ],
            [
                InlineKeyboardButton(
                    text=texts.t("BACK_TO_MAIN_MENU_BUTTON", "⬅️ В главное меню"),
                    callback_data="back_to_menu",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_happ_download_platform_keyboard(language: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    buttons = [
        [InlineKeyboardButton(text=texts.t("HAPP_PLATFORM_IOS", "🍎 iOS"), callback_data="happ_download_ios")],
        [InlineKeyboardButton(text=texts.t("HAPP_PLATFORM_ANDROID", "🤖 Android"), callback_data="happ_download_android")],
        [InlineKeyboardButton(text=texts.t("HAPP_PLATFORM_MACOS", "🖥️ Mac OS"), callback_data="happ_download_macos")],
        [InlineKeyboardButton(text=texts.t("HAPP_PLATFORM_WINDOWS", "💻 Windows"), callback_data="happ_download_windows")],
        [InlineKeyboardButton(text=texts.BACK, callback_data="happ_download_close")],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_happ_download_link_keyboard(language: str, link: str) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    buttons = [
        [InlineKeyboardButton(text=texts.t("HAPP_DOWNLOAD_OPEN_LINK", "🔗 Открыть ссылку"), url=link)],
        [InlineKeyboardButton(text=texts.BACK, callback_data="happ_download_back")],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_back_keyboard(language: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")]
    ])


def get_server_status_keyboard(
    language: str,
    current_page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=texts.t("SERVER_STATUS_REFRESH", "🔄 Обновить"),
                callback_data=f"server_status_page:{current_page}",
            )
        ]
    ]

    if total_pages > 1:
        nav_row: list[InlineKeyboardButton] = []

        if current_page > 1:
            nav_row.append(
                InlineKeyboardButton(
                    text=texts.t("SERVER_STATUS_PREV_PAGE", "⬅️ Назад"),
                    callback_data=f"server_status_page:{current_page - 1}",
                )
            )

        if current_page < total_pages:
            nav_row.append(
                InlineKeyboardButton(
                    text=texts.t("SERVER_STATUS_NEXT_PAGE", "Вперед ➡️"),
                    callback_data=f"server_status_page:{current_page + 1}",
                )
            )

        if nav_row:
            keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_insufficient_balance_keyboard(
    language: str = DEFAULT_LANGUAGE,
    resume_callback: str | None = None,
    amount_kopeks: int | None = None,
) -> InlineKeyboardMarkup:

    texts = get_texts(language)
    keyboard = get_payment_methods_keyboard(amount_kopeks or 0, language)

    if resume_callback:
        keyboard.inline_keyboard.insert(
            0,
            [
                InlineKeyboardButton(
                    text=texts.RETURN_TO_SUBSCRIPTION_CHECKOUT,
                    callback_data=resume_callback,
                )
            ],
        )

    if keyboard.inline_keyboard:
        last_row = keyboard.inline_keyboard[-1]
        if (
            len(last_row) == 1
            and isinstance(last_row[0], InlineKeyboardButton)
            and last_row[0].callback_data == "menu_balance"
        ):
            keyboard.inline_keyboard[-1][0] = InlineKeyboardButton(
                text=last_row[0].text,
                callback_data="back_to_menu",
            )

    return keyboard


def get_subscription_keyboard(
    language: str = DEFAULT_LANGUAGE, 
    has_subscription: bool = False, 
    is_trial: bool = False,
    subscription=None
) -> InlineKeyboardMarkup:
    from app.config import settings 
    
    texts = get_texts(language)
    keyboard = []

    if has_subscription:
        subscription_link = get_display_subscription_link(subscription) if subscription else None
        if subscription_link:
            connect_mode = settings.CONNECT_BUTTON_MODE

            if connect_mode == "miniapp_subscription":
                keyboard.append([
                    InlineKeyboardButton(
                        text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"),
                        web_app=types.WebAppInfo(url=subscription_link)
                    )
                ])
            elif connect_mode == "miniapp_custom":
                if settings.MINIAPP_CUSTOM_URL:
                    keyboard.append([
                        InlineKeyboardButton(
                            text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"),
                            web_app=types.WebAppInfo(url=settings.MINIAPP_CUSTOM_URL)
                        )
                    ])
                else:
                    keyboard.append([
                        InlineKeyboardButton(text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"), callback_data="subscription_connect")
                    ])
            elif connect_mode == "link":
                keyboard.append([
                    InlineKeyboardButton(text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"), url=subscription_link)
                ])
            elif connect_mode == "happ_cryptolink":
                keyboard.append([
                    InlineKeyboardButton(
                        text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"),
                        callback_data="open_subscription_link",
                    )
                ])
            else:
                keyboard.append([
                    InlineKeyboardButton(text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"), callback_data="subscription_connect")
                ])
        elif settings.CONNECT_BUTTON_MODE == "miniapp_custom":
            keyboard.append([
                InlineKeyboardButton(
                    text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"),
                    web_app=types.WebAppInfo(url=settings.MINIAPP_CUSTOM_URL)
                )
            ])
        else:
            keyboard.append([
                InlineKeyboardButton(text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"), callback_data="subscription_connect")
            ])

        happ_row = get_happ_download_button_row(texts)
        if happ_row:
            keyboard.append(happ_row)

        if not is_trial:
            keyboard.append([
                InlineKeyboardButton(text=texts.MENU_EXTEND_SUBSCRIPTION, callback_data="subscription_extend")
            ])
            keyboard.append([
                InlineKeyboardButton(
                    text=texts.t("AUTOPAY_BUTTON", "💳 Автоплатеж"),
                    callback_data="subscription_autopay",
                )
            ])

        if is_trial:
            keyboard.append([
                InlineKeyboardButton(text=texts.MENU_BUY_SUBSCRIPTION, callback_data="subscription_upgrade")
            ])
        else:
            keyboard.append([
                InlineKeyboardButton(
                    text=texts.t("SUBSCRIPTION_SETTINGS_BUTTON", "⚙️ Настройки подписки"),
                    callback_data="subscription_settings",
                )
            ])
    
    keyboard.append([
        InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_payment_methods_keyboard_with_cart(
    language: str = "ru",
    amount_kopeks: int = 0,
) -> InlineKeyboardMarkup:
    keyboard = get_payment_methods_keyboard(amount_kopeks, language)
    
    # Добавляем кнопку "Очистить корзину"
    keyboard.inline_keyboard.append([
        InlineKeyboardButton(
            text="🗑️ Очистить корзину и вернуться",
            callback_data="clear_saved_cart"
        )
    ])
    
    return keyboard

def get_subscription_confirm_keyboard_with_cart(language: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✅ Подтвердить покупку",
            callback_data="subscription_confirm"
        )],
        [InlineKeyboardButton(
            text="🗑️ Очистить корзину",
            callback_data="clear_saved_cart"
        )],
        [InlineKeyboardButton(
            text="🔙 Назад",
            callback_data="back_to_menu"
        )]
    ])

def get_insufficient_balance_keyboard_with_cart(
    language: str = "ru",
    amount_kopeks: int = 0,
) -> InlineKeyboardMarkup:
    keyboard = get_insufficient_balance_keyboard(
        language,
        amount_kopeks=amount_kopeks,
    )

    keyboard.inline_keyboard.insert(
        0,
        [
            InlineKeyboardButton(
                text="🗑️ Очистить корзину и вернуться",
                callback_data="clear_saved_cart",
            )
        ],
    )

    return keyboard

def get_trial_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=texts.t("TRIAL_ACTIVATE_BUTTON", "🎁 Активировать"), callback_data="trial_activate"),
            InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")
        ]
    ])


def get_subscription_period_keyboard(language: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
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


def get_traffic_packages_keyboard(language: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
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
                text=texts.t("TRAFFIC_PACKAGES_NOT_CONFIGURED", "⚠️ Пакеты трафика не настроены"), 
                callback_data="no_traffic_packages"
            )
        ])
    
    keyboard.append([
        InlineKeyboardButton(text=texts.BACK, callback_data="subscription_config_back")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_countries_keyboard(countries: List[dict], selected: List[str], language: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
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
                text=texts.t("NO_SERVERS_AVAILABLE", "❌ Нет доступных серверов"),
                callback_data="no_servers"
            )
        ])
    
    keyboard.extend([
        [InlineKeyboardButton(text=texts.t("CONTINUE_BUTTON", "✅ Продолжить"), callback_data="countries_continue")],
        [InlineKeyboardButton(text=texts.BACK, callback_data="subscription_config_back")]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_devices_keyboard(current: int, language: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
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
        [InlineKeyboardButton(text=texts.t("CONTINUE_BUTTON", "✅ Продолжить"), callback_data="devices_continue")],
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

def get_subscription_confirm_keyboard(language: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=texts.CONFIRM, callback_data="subscription_confirm"),
            InlineKeyboardButton(text=texts.CANCEL, callback_data="subscription_cancel")
        ]
    ])


def get_balance_keyboard(language: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
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


def get_payment_methods_keyboard(amount_kopeks: int, language: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard = []

    amount_kopeks = max(0, int(amount_kopeks or 0))

    def _build_callback(method: str) -> str:
        if amount_kopeks > 0:
            return f"topup_amount|{method}|{amount_kopeks}"
        return f"topup_{method}"

    if settings.TELEGRAM_STARS_ENABLED:
        keyboard.append([
            InlineKeyboardButton(
                text=texts.t("PAYMENT_TELEGRAM_STARS", "⭐ Telegram Stars"),
                callback_data=_build_callback("stars")
            )
        ])

    if settings.is_yookassa_enabled():
        keyboard.append([
            InlineKeyboardButton(
                text=texts.t("PAYMENT_CARD_YOOKASSA", "💳 Банковская карта (YooKassa)"),
                callback_data=_build_callback("yookassa")
            )
        ])

        if settings.YOOKASSA_SBP_ENABLED:
            keyboard.append([
                InlineKeyboardButton(
                    text=texts.t("PAYMENT_SBP_YOOKASSA", "🏦 Оплатить по СБП (YooKassa)"),
                    callback_data=_build_callback("yookassa_sbp")
                )
            ])

    if settings.TRIBUTE_ENABLED:
        keyboard.append([
            InlineKeyboardButton(
                text=texts.t("PAYMENT_CARD_TRIBUTE", "💳 Банковская карта (Tribute)"),
                callback_data=_build_callback("tribute")
            )
        ])

    if settings.is_mulenpay_enabled():
        keyboard.append([
            InlineKeyboardButton(
                text=texts.t("PAYMENT_CARD_MULENPAY", "💳 Банковская карта (Mulen Pay)"),
                callback_data=_build_callback("mulenpay")
            )
        ])

    if settings.is_pal24_enabled():
        keyboard.append([
            InlineKeyboardButton(
                text=texts.t("PAYMENT_CARD_PAL24", "💳 Банковская карта (PayPalych)"),
                callback_data=_build_callback("pal24")
            )
        ])

    if settings.is_cryptobot_enabled():
        keyboard.append([
            InlineKeyboardButton(
                text=texts.t("PAYMENT_CRYPTOBOT", "🪙 Криптовалюта (CryptoBot)"),
                callback_data=_build_callback("cryptobot")
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            text=texts.t("PAYMENT_VIA_SUPPORT", "🛠️ Через поддержку"),
            callback_data="topup_support"
        )
    ])

    if len(keyboard) == 1:
        keyboard.insert(0, [
            InlineKeyboardButton(
                text=texts.t("PAYMENTS_TEMPORARILY_UNAVAILABLE", "⚠️ Способы оплаты временно недоступны"),
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
    language: str = DEFAULT_LANGUAGE
) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=texts.t("PAY_NOW_BUTTON", "💳 Оплатить"),
                url=confirmation_url
            )
        ],
        [
            InlineKeyboardButton(
                text=texts.t("CHECK_STATUS_BUTTON", "📊 Проверить статус"),
                callback_data=f"check_yookassa_status_{payment_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text=texts.t("MY_BALANCE_BUTTON", "💰 Мой баланс"),
                callback_data="menu_balance"
            )
        ]
    ])

def get_autopay_notification_keyboard(subscription_id: int, language: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=texts.t("TOPUP_BALANCE_BUTTON", "💳 Пополнить баланс"), 
                callback_data="balance_topup"
            )
        ],
        [
            InlineKeyboardButton(
                text=texts.t("MY_SUBSCRIPTION_BUTTON", "📱 Моя подписка"), 
                callback_data="menu_subscription"
            )
        ]
    ])

def get_subscription_expiring_keyboard(subscription_id: int, language: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
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
                text=texts.t("TOPUP_BALANCE_BUTTON", "💳 Пополнить баланс"), 
                callback_data="balance_topup"
            )
        ],
        [
            InlineKeyboardButton(
                text=texts.t("MY_SUBSCRIPTION_BUTTON", "📱 Моя подписка"), 
                callback_data="menu_subscription"
            )
        ]
    ])

def get_referral_keyboard(language: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    
    keyboard = [
        [
            InlineKeyboardButton(
                text=texts.t("CREATE_INVITE_BUTTON", "📝 Создать приглашение"),
                callback_data="referral_create_invite"
            )
        ],
        [
            InlineKeyboardButton(
                text=texts.t("SHOW_QR_BUTTON", "📱 Показать QR код"),
                callback_data="referral_show_qr"
            )
        ],
        [
            InlineKeyboardButton(
                text=texts.t("REFERRAL_LIST_BUTTON", "👥 Список рефералов"),
                callback_data="referral_list"
            )
        ],
        [
            InlineKeyboardButton(
                text=texts.t("REFERRAL_ANALYTICS_BUTTON", "📊 Аналитика"),
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


def get_support_keyboard(language: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    try:
        from app.services.support_settings_service import SupportSettingsService
        tickets_enabled = SupportSettingsService.is_tickets_enabled()
        contact_enabled = SupportSettingsService.is_contact_enabled()
    except Exception:
        tickets_enabled = True
        contact_enabled = True
    rows: list[list[InlineKeyboardButton]] = []
    # Tickets
    if tickets_enabled:
        rows.append([
            InlineKeyboardButton(
                text=texts.t("CREATE_TICKET_BUTTON", "🎫 Создать тикет"),
                callback_data="create_ticket"
            )
        ])
        rows.append([
            InlineKeyboardButton(
                text=texts.t("MY_TICKETS_BUTTON", "📋 Мои тикеты"),
                callback_data="my_tickets"
            )
        ])
    # Direct contact
    if contact_enabled and settings.get_support_contact_url():
        rows.append([
            InlineKeyboardButton(
                text=texts.t("CONTACT_SUPPORT_BUTTON", "💬 Связаться с поддержкой"),
                url=settings.get_support_contact_url() or "https://t.me/"
            )
        ])
    rows.append([InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_pagination_keyboard(
    current_page: int,
    total_pages: int,
    callback_prefix: str,
    language: str = DEFAULT_LANGUAGE
) -> List[List[InlineKeyboardButton]]:
    texts = get_texts(language)
    keyboard = []
    
    if total_pages > 1:
        row = []
        
        if current_page > 1:
            row.append(InlineKeyboardButton(
                text=texts.t("PAGINATION_PREV", "⬅️"),
                callback_data=f"{callback_prefix}_page_{current_page - 1}"
            ))
        
        row.append(InlineKeyboardButton(
            text=f"{current_page}/{total_pages}",
            callback_data="current_page"
        ))
        
        if current_page < total_pages:
            row.append(InlineKeyboardButton(
                text=texts.t("PAGINATION_NEXT", "➡️"),
                callback_data=f"{callback_prefix}_page_{current_page + 1}"
            ))
        
        keyboard.append(row)
    
    return keyboard

def get_confirmation_keyboard(
    confirm_data: str,
    cancel_data: str = "cancel",
    language: str = DEFAULT_LANGUAGE
) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=texts.YES, callback_data=confirm_data),
            InlineKeyboardButton(text=texts.NO, callback_data=cancel_data)
        ]
    ])


def get_autopay_keyboard(language: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=texts.t("ENABLE_BUTTON", "✅ Включить"), callback_data="autopay_enable"),
            InlineKeyboardButton(text=texts.t("DISABLE_BUTTON", "❌ Выключить"), callback_data="autopay_disable")
        ],
        [
            InlineKeyboardButton(text=texts.t("AUTOPAY_SET_DAYS_BUTTON", "⚙️ Настроить дни"), callback_data="autopay_set_days")
        ],
        [
            InlineKeyboardButton(text=texts.BACK, callback_data="menu_subscription")
        ]
    ])


def get_autopay_days_keyboard(language: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard = []
    
    for days in [1, 3, 7, 14]:
        keyboard.append([
            InlineKeyboardButton(
                text=f"{days} дн{_get_days_suffix(days)}",
                callback_data=f"autopay_days_{days}"
            )
        ])
    
    keyboard.append([
        InlineKeyboardButton(text=texts.BACK, callback_data="subscription_autopay")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def _get_days_suffix(days: int) -> str:
    if days == 1:
        return "ь"
    elif 2 <= days <= 4:
        return "я"
    else:
        return "ей"



def get_extend_subscription_keyboard(language: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
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


def get_add_traffic_keyboard(language: str = DEFAULT_LANGUAGE, subscription_end_date: datetime = None) -> InlineKeyboardMarkup:
    from app.utils.pricing_utils import get_remaining_months
    from app.config import settings
    texts = get_texts(language)
    
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
                text=texts.t("NO_TRAFFIC_PACKAGES", "❌ Нет доступных пакетов"),
                callback_data="no_traffic_packages"
            )],
            [InlineKeyboardButton(
                text=texts.BACK,
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
            text=texts.BACK,
            callback_data="menu_subscription"
        )
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)
    
def get_change_devices_keyboard(current_devices: int, language: str = DEFAULT_LANGUAGE, subscription_end_date: datetime = None) -> InlineKeyboardMarkup:
    from app.utils.pricing_utils import get_remaining_months
    from app.config import settings
    texts = get_texts(language)
    
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
            text=texts.BACK,
            callback_data="subscription_settings"
        )
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_confirm_change_devices_keyboard(new_devices_count: int, price: int, language: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=texts.t("CONFIRM_CHANGE_BUTTON", "✅ Подтвердить изменение"),
                callback_data=f"confirm_change_devices_{new_devices_count}_{price}"
            )
        ],
        [
            InlineKeyboardButton(
                text=texts.CANCEL,
                callback_data="subscription_settings"
            )
        ]
    ])


def get_reset_traffic_confirm_keyboard(price_kopeks: int, language: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
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
            InlineKeyboardButton(
                text=texts.t("PENDING_CANCEL_BUTTON", "⌛ Отмена"),
                callback_data="menu_subscription",
            )
        ]
    ])

def get_manage_countries_keyboard(
    countries: List[dict],
    selected: List[str],
    current_subscription_countries: List[str],
    language: str = DEFAULT_LANGUAGE,
    subscription_end_date: datetime = None
) -> InlineKeyboardMarkup:
    from app.utils.pricing_utils import get_remaining_months

    texts = get_texts(language)

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
            text=texts.BACK,
            callback_data="menu_subscription"
        )
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_device_selection_keyboard(language: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    from app.config import settings
    texts = get_texts(language)
    
    keyboard = [
        [
            InlineKeyboardButton(text=texts.t("DEVICE_GUIDE_IOS", "📱 iOS (iPhone/iPad)"), callback_data="device_guide_ios"),
            InlineKeyboardButton(text=texts.t("DEVICE_GUIDE_ANDROID", "🤖 Android"), callback_data="device_guide_android")
        ],
        [
            InlineKeyboardButton(text=texts.t("DEVICE_GUIDE_WINDOWS", "💻 Windows"), callback_data="device_guide_windows"),
            InlineKeyboardButton(text=texts.t("DEVICE_GUIDE_MAC", "🎯 macOS"), callback_data="device_guide_mac")
        ],
        [
            InlineKeyboardButton(text=texts.t("DEVICE_GUIDE_ANDROID_TV", "📺 Android TV"), callback_data="device_guide_tv")
        ]
    ]
    
    if settings.CONNECT_BUTTON_MODE == "guide":
        keyboard.append([
            InlineKeyboardButton(text=texts.t("SHOW_SUBSCRIPTION_LINK", "📋 Показать ссылку подписки"), callback_data="open_subscription_link")
        ])
    
    keyboard.append([
        InlineKeyboardButton(text=texts.BACK, callback_data="menu_subscription")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_connection_guide_keyboard(
    subscription_url: str, 
    app: dict, 
    language: str = DEFAULT_LANGUAGE
) -> InlineKeyboardMarkup:
    from app.handlers.subscription import create_deep_link
    texts = get_texts(language)
    
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
    
    if settings.is_happ_cryptolink_mode():
        copy_button = InlineKeyboardButton(
            text=texts.t("COPY_SUBSCRIPTION_LINK", "📋 Скопировать ссылку подписки"),
            callback_data="open_subscription_link",
        )
    else:
        copy_button = InlineKeyboardButton(
            text=texts.t("COPY_SUBSCRIPTION_LINK", "📋 Скопировать ссылку подписки"),
            url=subscription_url,
        )

    keyboard.append([copy_button])
    
    keyboard.extend([
        [
            InlineKeyboardButton(text=texts.t("CHOOSE_ANOTHER_DEVICE", "📱 Выбрать другое устройство"), callback_data="subscription_connect")
        ],
        [
            InlineKeyboardButton(text=texts.t("BACK_TO_SUBSCRIPTION", "⬅️ К подписке"), callback_data="menu_subscription")
        ]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_app_selection_keyboard(
    device_type: str, 
    apps: list, 
    language: str = DEFAULT_LANGUAGE
) -> InlineKeyboardMarkup:
    texts = get_texts(language)
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
            InlineKeyboardButton(text=texts.t("CHOOSE_ANOTHER_DEVICE", "📱 Выбрать другое устройство"), callback_data="subscription_connect")
        ],
        [
            InlineKeyboardButton(text=texts.t("BACK_TO_SUBSCRIPTION", "⬅️ К подписке"), callback_data="menu_subscription")
        ]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_specific_app_keyboard(
    subscription_url: str,
    app: dict,
    device_type: str,
    language: str = DEFAULT_LANGUAGE
) -> InlineKeyboardMarkup:
    from app.handlers.subscription import create_deep_link
    texts = get_texts(language)
    
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
    
    if settings.is_happ_cryptolink_mode():
        copy_button = InlineKeyboardButton(
            text=texts.t("COPY_SUBSCRIPTION_LINK", "📋 Скопировать ссылку подписки"),
            callback_data="open_subscription_link",
        )
    else:
        copy_button = InlineKeyboardButton(
            text=texts.t("COPY_SUBSCRIPTION_LINK", "📋 Скопировать ссылку подписки"),
            url=subscription_url,
        )

    keyboard.append([copy_button])
    
    if 'additionalAfterAddSubscriptionStep' in app and 'buttons' in app['additionalAfterAddSubscriptionStep']:
        for button in app['additionalAfterAddSubscriptionStep']['buttons']:
            button_text = button['buttonText'].get(language, button['buttonText']['en'])
            keyboard.append([
                InlineKeyboardButton(text=button_text, url=button['buttonLink'])
            ])
    
    keyboard.extend([
        [
            InlineKeyboardButton(text=texts.t("OTHER_APPS_BUTTON", "📋 Другие приложения"), callback_data=f"app_list_{device_type}")
        ],
        [
            InlineKeyboardButton(text=texts.t("CHOOSE_ANOTHER_DEVICE", "📱 Выбрать другое устройство"), callback_data="subscription_connect")
        ],
        [
            InlineKeyboardButton(text=texts.t("BACK_TO_SUBSCRIPTION", "⬅️ К подписке"), callback_data="menu_subscription")
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
        InlineKeyboardButton(text=texts.BACK, callback_data="menu_subscription")
    ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_cryptobot_payment_keyboard(
    payment_id: str,
    local_payment_id: int,
    amount_usd: float,
    asset: str,
    bot_invoice_url: str,
    language: str = DEFAULT_LANGUAGE
) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=texts.t("PAY_WITH_COINS_BUTTON", "🪙 Оплатить"),
                url=bot_invoice_url
            )
        ],
        [
            InlineKeyboardButton(
                text=texts.t("CHECK_STATUS_BUTTON", "📊 Проверить статус"),
                callback_data=f"check_cryptobot_{local_payment_id}"
            )
        ],
        [
            InlineKeyboardButton(
                text=texts.t("MY_BALANCE_BUTTON", "💰 Мой баланс"),
                callback_data="menu_balance"
            )
        ]
    ])

def get_devices_management_keyboard(
    devices: List[dict], 
    pagination,
    language: str = DEFAULT_LANGUAGE
) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    
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
                    text=texts.t("PAGINATION_PREV", "⬅️"),
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
                    text=texts.t("PAGINATION_NEXT", "➡️"),
                    callback_data=f"devices_page_{pagination.next_page}"
                )
            )
        
        keyboard.append(nav_row)
    
    keyboard.append([
        InlineKeyboardButton(
            text=texts.t("RESET_ALL_DEVICES_BUTTON", "🔄 Сбросить все устройства"),
            callback_data="reset_all_devices"
        )
    ])
    
    keyboard.append([
        InlineKeyboardButton(
            text=texts.BACK,
            callback_data="subscription_settings"
        )
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_updated_subscription_settings_keyboard(language: str = DEFAULT_LANGUAGE, show_countries_management: bool = True) -> InlineKeyboardMarkup:
    from app.config import settings
    
    texts = get_texts(language)
    keyboard = []
    
    if show_countries_management:
        keyboard.append([
            InlineKeyboardButton(text=texts.t("ADD_COUNTRIES_BUTTON", "🌐 Добавить страны"), callback_data="subscription_add_countries")
        ])

    keyboard.extend([
        [
            InlineKeyboardButton(text=texts.t("CHANGE_DEVICES_BUTTON", "📱 Изменить устройства"), callback_data="subscription_change_devices") 
        ],
        [
            InlineKeyboardButton(text=texts.t("MANAGE_DEVICES_BUTTON", "🔧 Управление устройствами"), callback_data="subscription_manage_devices")
        ]
    ])

    if settings.is_traffic_selectable():
        keyboard.insert(-2, [
            InlineKeyboardButton(text=texts.t("SWITCH_TRAFFIC_BUTTON", "🔄 Переключить трафик"), callback_data="subscription_switch_traffic")
        ])
        keyboard.insert(-2, [
            InlineKeyboardButton(text=texts.t("RESET_TRAFFIC_BUTTON", "🔄 Сбросить трафик"), callback_data="subscription_reset_traffic")
        ])
    
    keyboard.append([
        InlineKeyboardButton(text=texts.BACK, callback_data="menu_subscription")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_device_reset_confirm_keyboard(device_info: str, device_index: int, page: int, language: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=texts.t("RESET_DEVICE_CONFIRM_BUTTON", "✅ Да, сбросить это устройство"), 
                callback_data=f"confirm_reset_device_{device_index}_{page}"
            )
        ],
        [
            InlineKeyboardButton(
                text=texts.CANCEL, 
                callback_data=f"devices_page_{page}"
            )
        ]
    ])


def get_device_management_help_keyboard(language: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=texts.t("DEVICE_CONNECTION_HELP", "❓ Как подключить устройство заново?"),
                callback_data="device_connection_help"
            )
        ],
        [
            InlineKeyboardButton(
                text=texts.t("MANAGE_DEVICES_BUTTON", "🔧 Управление устройствами"),
                callback_data="subscription_manage_devices"
            )
        ],
        [
            InlineKeyboardButton(
                text=texts.t("BACK_TO_SUBSCRIPTION", "⬅️ К подписке"),
                callback_data="menu_subscription"
            )
        ]
    ])


# ==================== TICKET KEYBOARDS ====================

def get_ticket_cancel_keyboard(language: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=texts.t("CANCEL_TICKET_CREATION", "❌ Отменить создание тикета"),
                callback_data="cancel_ticket_creation"
            )
        ]
    ])


def get_my_tickets_keyboard(
    tickets: List[dict],
    current_page: int = 1,
    total_pages: int = 1,
    language: str = DEFAULT_LANGUAGE,
    page_prefix: str = "my_tickets_page_"
) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard = []
    
    for ticket in tickets:
        status_emoji = ticket.get('status_emoji', '❓')
        # Override status emoji for closed tickets in admin list
        if ticket.get('is_closed', False):
            status_emoji = '✅'
        title = ticket.get('title', 'Без названия')[:25]
        button_text = f"{status_emoji} #{ticket['id']} {title}"
        
        keyboard.append([
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"view_ticket_{ticket['id']}"
            )
        ])
    
    # Пагинация
    if total_pages > 1:
        nav_row = []
        
        if current_page > 1:
            nav_row.append(
                InlineKeyboardButton(
                    text=texts.t("PAGINATION_PREV", "⬅️"),
                    callback_data=f"{page_prefix}{current_page - 1}"
                )
            )
        
        nav_row.append(
            InlineKeyboardButton(
                text=f"{current_page}/{total_pages}",
                callback_data="current_page"
            )
        )
        
        if current_page < total_pages:
            nav_row.append(
                InlineKeyboardButton(
                    text=texts.t("PAGINATION_NEXT", "➡️"),
                    callback_data=f"{page_prefix}{current_page + 1}"
                )
            )
        
        keyboard.append(nav_row)
    
    keyboard.append([
        InlineKeyboardButton(text=texts.BACK, callback_data="menu_support")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_ticket_view_keyboard(
    ticket_id: int,
    is_closed: bool = False,
    language: str = DEFAULT_LANGUAGE
) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard = []
    
    if not is_closed:
        keyboard.append([
            InlineKeyboardButton(
                text=texts.t("REPLY_TO_TICKET", "💬 Ответить"),
                callback_data=f"reply_ticket_{ticket_id}"
            )
        ])
    
    if not is_closed:
        keyboard.append([
            InlineKeyboardButton(
                text=texts.t("CLOSE_TICKET", "🔒 Закрыть тикет"),
                callback_data=f"close_ticket_{ticket_id}"
            )
        ])
    
    keyboard.append([
        InlineKeyboardButton(text=texts.BACK, callback_data="my_tickets")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_ticket_reply_cancel_keyboard(language: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=texts.t("CANCEL_REPLY", "❌ Отменить ответ"),
                callback_data="cancel_ticket_reply"
            )
        ]
    ])


# ==================== ADMIN TICKET KEYBOARDS ====================

def get_admin_tickets_keyboard(
    tickets: List[dict],
    current_page: int = 1,
    total_pages: int = 1,
    language: str = DEFAULT_LANGUAGE,
    scope: str = "all",
    *,
    back_callback: str = "admin_submenu_support"
) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard = []
    
    # Разделяем открытые/закрытые
    open_rows = []
    closed_rows = []
    for ticket in tickets:
        status_emoji = ticket.get('status_emoji', '❓')
        if ticket.get('is_closed', False):
            status_emoji = '✅'
        user_name = ticket.get('user_name', 'Unknown')[:15]
        title = ticket.get('title', 'Без названия')[:20]
        locked_emoji = ticket.get('locked_emoji', '')
        button_text = f"{status_emoji} #{ticket['id']} {locked_emoji} {user_name}: {title}".replace("  ", " ")
        row = [InlineKeyboardButton(text=button_text, callback_data=f"admin_view_ticket_{ticket['id']}")]
        if ticket.get('is_closed', False):
            closed_rows.append(row)
        else:
            open_rows.append(row)

    # Scope switcher
    switch_row = []
    switch_row.append(InlineKeyboardButton(text=texts.t("OPEN_TICKETS", "🔴 Открытые"), callback_data="admin_tickets_scope_open"))
    switch_row.append(InlineKeyboardButton(text=texts.t("CLOSED_TICKETS", "🟢 Закрытые"), callback_data="admin_tickets_scope_closed"))
    keyboard.append(switch_row)

    if open_rows and scope in ("all", "open"):
        keyboard.append([InlineKeyboardButton(text=texts.t("OPEN_TICKETS_HEADER", "Открытые тикеты"), callback_data="noop")])
        keyboard.extend(open_rows)
    if closed_rows and scope in ("all", "closed"):
        keyboard.append([InlineKeyboardButton(text=texts.t("CLOSED_TICKETS_HEADER", "Закрытые тикеты"), callback_data="noop")])
        keyboard.extend(closed_rows)
    
    # Пагинация
    if total_pages > 1:
        nav_row = []
        
        if current_page > 1:
            nav_row.append(
                InlineKeyboardButton(
                    text=texts.t("PAGINATION_PREV", "⬅️"),
                    callback_data=f"admin_tickets_page_{scope}_{current_page - 1}"
                )
            )
        
        nav_row.append(
            InlineKeyboardButton(
                text=f"{current_page}/{total_pages}",
                callback_data="current_page"
            )
        )
        
        if current_page < total_pages:
            nav_row.append(
                InlineKeyboardButton(
                    text=texts.t("PAGINATION_NEXT", "➡️"),
                    callback_data=f"admin_tickets_page_{scope}_{current_page + 1}"
                )
            )
        
        keyboard.append(nav_row)
    
    keyboard.append([
        InlineKeyboardButton(text=texts.BACK, callback_data=back_callback)
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_admin_ticket_view_keyboard(
    ticket_id: int,
    is_closed: bool = False,
    language: str = DEFAULT_LANGUAGE,
    *,
    is_user_blocked: bool = False
) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard = []
    
    if not is_closed:
        keyboard.append([
            InlineKeyboardButton(
                text=texts.t("REPLY_TO_TICKET", "💬 Ответить"),
                callback_data=f"admin_reply_ticket_{ticket_id}"
            )
        ])
    
    if not is_closed:
        keyboard.append([
            InlineKeyboardButton(
                text=texts.t("CLOSE_TICKET", "🔒 Закрыть тикет"),
                callback_data=f"admin_close_ticket_{ticket_id}"
            )
        ])
    
    # Блок-контролы: когда не заблокирован — показать два варианта, когда заблокирован — только "Разблокировать"
    if is_user_blocked:
        keyboard.append([
            InlineKeyboardButton(text=texts.t("UNBLOCK", "✅ Разблокировать"), callback_data=f"admin_unblock_user_ticket_{ticket_id}")
        ])
    else:
        keyboard.append([
            InlineKeyboardButton(text=texts.t("BLOCK_FOREVER", "🚫 Заблокировать"), callback_data=f"admin_block_user_perm_ticket_{ticket_id}"),
            InlineKeyboardButton(text=texts.t("BLOCK_BY_TIME", "⏳ Блок по времени"), callback_data=f"admin_block_user_ticket_{ticket_id}")
        ])
    
    keyboard.append([
        InlineKeyboardButton(text=texts.BACK, callback_data="admin_tickets")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_admin_ticket_reply_cancel_keyboard(language: str = DEFAULT_LANGUAGE) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=texts.t("CANCEL_REPLY", "❌ Отменить ответ"),
                callback_data="cancel_admin_ticket_reply"
            )
        ]
    ])
