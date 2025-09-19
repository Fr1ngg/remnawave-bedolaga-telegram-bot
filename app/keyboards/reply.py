from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from typing import List

from app.localization.texts import get_texts


def get_main_reply_keyboard(language: str = "ru") -> ReplyKeyboardMarkup:
    texts = get_texts(language)
    
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=texts.MENU_BALANCE),
                KeyboardButton(text=texts.MENU_SUBSCRIPTION)
            ],
            [
                KeyboardButton(text=texts.MENU_PROMOCODE),
                KeyboardButton(text=texts.MENU_REFERRALS)
            ],
            [
                KeyboardButton(text=texts.MENU_SUPPORT),
                KeyboardButton(text=texts.MENU_RULES)
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=False
    )


def get_admin_reply_keyboard(language: str = "ru") -> ReplyKeyboardMarkup:
    texts = get_texts(language)
    
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=texts.ADMIN_USERS),
                KeyboardButton(text=texts.ADMIN_SUBSCRIPTIONS)
            ],
            [
                KeyboardButton(text=texts.ADMIN_PROMOCODES),
                KeyboardButton(text=texts.ADMIN_MESSAGES)
            ],
            [
                KeyboardButton(text=texts.ADMIN_STATISTICS),
                KeyboardButton(text=texts.ADMIN_MONITORING)
            ],
            [
                KeyboardButton(text=texts.t("ADMIN_MAIN_MENU", "🏠 Главное меню"))
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=False
    )


def get_cancel_keyboard(language: str = "ru") -> ReplyKeyboardMarkup:
    texts = get_texts(language)
    
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=texts.CANCEL)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def get_confirmation_reply_keyboard(language: str = "ru") -> ReplyKeyboardMarkup:
    texts = get_texts(language)
    
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=texts.YES),
                KeyboardButton(text=texts.NO)
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def get_skip_keyboard(language: str = "ru") -> ReplyKeyboardMarkup:
    texts = get_texts(language)
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=texts.REFERRAL_CODE_SKIP)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def remove_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


def get_contact_keyboard(language: str = "ru") -> ReplyKeyboardMarkup:
    texts = get_texts(language)
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=texts.t("SEND_CONTACT_BUTTON", "📱 Отправить контакт"), request_contact=True)],
            [KeyboardButton(text=texts.CANCEL)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def get_location_keyboard(language: str = "ru") -> ReplyKeyboardMarkup:
    texts = get_texts(language)
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=texts.t("SEND_LOCATION_BUTTON", "📍 Отправить геолокацию"), request_location=True)],
            [KeyboardButton(text=texts.CANCEL)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
