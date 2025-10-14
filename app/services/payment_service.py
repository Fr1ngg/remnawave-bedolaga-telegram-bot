"""Агрегирующий сервис, собирающий все платёжные модули."""

from __future__ import annotations

import logging
from importlib import import_module
from typing import Optional

from aiogram import Bot

from app.config import settings
from app.utils.currency_converter import currency_converter  # noqa: F401
from app.external.cryptobot import CryptoBotService
from app.external.telegram_stars import TelegramStarsService
from app.services.mulenpay_service import MulenPayService
from app.services.pal24_service import Pal24Service
from app.services.payment import (
    CryptoBotPaymentMixin,
    MulenPayPaymentMixin,
    Pal24PaymentMixin,
    PaymentCommonMixin,
    TelegramStarsMixin,
    TributePaymentMixin,
    YooKassaPaymentMixin,
    WataPaymentMixin,
)
from app.services.yookassa_service import YooKassaService
from app.services.wata_service import WataService

logger = logging.getLogger(__name__)


# --- Совместимость: экспортируем функции, которые активно мокаются в тестах ---


async def create_yookassa_payment(*args, **kwargs):
    yk_crud = import_module("app.database.crud.yookassa")
    return await yk_crud.create_yookassa_payment(*args, **kwargs)


async def link_yookassa_payment_to_transaction(*args, **kwargs):
    yk_crud = import_module("app.database.crud.yookassa")
    return await yk_crud.link_yookassa_payment_to_transaction(*args, **kwargs)


async def get_yookassa_payment_by_id(*args, **kwargs):
    yk_crud = import_module("app.database.crud.yookassa")
    return await yk_crud.get_yookassa_payment_by_id(*args, **kwargs)


async def create_transaction(*args, **kwargs):
    transaction_crud = import_module("app.database.crud.transaction")
    return await transaction_crud.create_transaction(*args, **kwargs)


async def add_user_balance(*args, **kwargs):
    user_crud = import_module("app.database.crud.user")
    return await user_crud.add_user_balance(*args, **kwargs)


async def get_user_by_id(*args, **kwargs):
    user_crud = import_module("app.database.crud.user")
    return await user_crud.get_user_by_id(*args, **kwargs)


async def get_user_by_telegram_id(*args, **kwargs):
    user_crud = import_module("app.database.crud.user")
    return await user_crud.get_user_by_telegram_id(*args, **kwargs)


async def create_mulenpay_payment(*args, **kwargs):
    mulenpay_crud = import_module("app.database.crud.mulenpay")
    return await mulenpay_crud.create_mulenpay_payment(*args, **kwargs)


async def get_mulenpay_payment_by_uuid(*args, **kwargs):
    mulenpay_crud = import_module("app.database.crud.mulenpay")
    return await mulenpay_crud.get_mulenpay_payment_by_uuid(*args, **kwargs)


async def get_mulenpay_payment_by_mulen_id(*args, **kwargs):
    mulenpay_crud = import_module("app.database.crud.mulenpay")
    return await mulenpay_crud.get_mulenpay_payment_by_mulen_id(*args, **kwargs)


async def get_mulenpay_payment_by_local_id(*args, **kwargs):
    mulenpay_crud = import_module("app.database.crud.mulenpay")
    return await mulenpay_crud.get_mulenpay_payment_by_local_id(*args, **kwargs)


async def update_mulenpay_payment_status(*args, **kwargs):
    mulenpay_crud = import_module("app.database.crud.mulenpay")
    return await mulenpay_crud.update_mulenpay_payment_status(*args, **kwargs)


async def link_mulenpay_payment_to_transaction(*args, **kwargs):
    mulenpay_crud = import_module("app.database.crud.mulenpay")
    return await mulenpay_crud.link_mulenpay_payment_to_transaction(*args, **kwargs)


async def create_pal24_payment(*args, **kwargs):
    pal_crud = import_module("app.database.crud.pal24")
    return await pal_crud.create_pal24_payment(*args, **kwargs)


async def get_pal24_payment_by_bill_id(*args, **kwargs):
    pal_crud = import_module("app.database.crud.pal24")
    return await pal_crud.get_pal24_payment_by_bill_id(*args, **kwargs)


async def get_pal24_payment_by_order_id(*args, **kwargs):
    pal_crud = import_module("app.database.crud.pal24")
    return await pal_crud.get_pal24_payment_by_order_id(*args, **kwargs)


async def get_pal24_payment_by_id(*args, **kwargs):
    pal_crud = import_module("app.database.crud.pal24")
    return await pal_crud.get_pal24_payment_by_id(*args, **kwargs)


async def update_pal24_payment_status(*args, **kwargs):
    pal_crud = import_module("app.database.crud.pal24")
    return await pal_crud.update_pal24_payment_status(*args, **kwargs)


async def link_pal24_payment_to_transaction(*args, **kwargs):
    pal_crud = import_module("app.database.crud.pal24")
    return await pal_crud.link_pal24_payment_to_transaction(*args, **kwargs)


async def create_wata_payment(*args, **kwargs):
    wata_crud = import_module("app.database.crud.wata")
    return await wata_crud.create_wata_payment(*args, **kwargs)


async def get_wata_payment_by_id(*args, **kwargs):
    wata_crud = import_module("app.database.crud.wata")
    return await wata_crud.get_wata_payment_by_id(*args, **kwargs)


async def get_wata_payment_by_link_id(*args, **kwargs):
    wata_crud = import_module("app.database.crud.wata")
    return await wata_crud.get_wata_payment_by_link_id(*args, **kwargs)


async def get_wata_payment_by_order_id(*args, **kwargs):
    wata_crud = import_module("app.database.crud.wata")
    return await wata_crud.get_wata_payment_by_order_id(*args, **kwargs)


async def get_wata_payment_by_transaction_uuid(*args, **kwargs):
    wata_crud = import_module("app.database.crud.wata")
    return await wata_crud.get_wata_payment_by_transaction_uuid(*args, **kwargs)


async def update_wata_payment_status(*args, **kwargs):
    wata_crud = import_module("app.database.crud.wata")
    return await wata_crud.update_wata_payment_status(*args, **kwargs)


async def link_wata_payment_to_transaction(*args, **kwargs):
    wata_crud = import_module("app.database.crud.wata")
    return await wata_crud.link_wata_payment_to_transaction(*args, **kwargs)


async def create_cryptobot_payment(*args, **kwargs):
    crypto_crud = import_module("app.database.crud.cryptobot")
    return await crypto_crud.create_cryptobot_payment(*args, **kwargs)


async def get_cryptobot_payment_by_invoice_id(*args, **kwargs):
    crypto_crud = import_module("app.database.crud.cryptobot")
    return await crypto_crud.get_cryptobot_payment_by_invoice_id(*args, **kwargs)


async def update_cryptobot_payment_status(*args, **kwargs):
    crypto_crud = import_module("app.database.crud.cryptobot")
    return await crypto_crud.update_cryptobot_payment_status(*args, **kwargs)


async def link_cryptobot_payment_to_transaction(*args, **kwargs):
    crypto_crud = import_module("app.database.crud.cryptobot")
    return await crypto_crud.link_cryptobot_payment_to_transaction(*args, **kwargs)


class PaymentService(
    PaymentCommonMixin,
    TelegramStarsMixin,
    YooKassaPaymentMixin,
    TributePaymentMixin,
    CryptoBotPaymentMixin,
    MulenPayPaymentMixin,
    Pal24PaymentMixin,
    WataPaymentMixin,
):
    """Основной интерфейс платежей, делегирующий работу специализированным mixin-ам."""

    def __init__(self, bot: Optional[Bot] = None) -> None:
        # Бот нужен для отправки уведомлений и создания звёздных инвойсов.
        self.bot = bot
        # Ниже инициализируем службы-обёртки только если соответствующий провайдер включён.
        self.yookassa_service = (
            YooKassaService() if settings.is_yookassa_enabled() else None
        )
        self.stars_service = TelegramStarsService(bot) if bot else None
        self.cryptobot_service = (
            CryptoBotService() if settings.is_cryptobot_enabled() else None
        )
        self.mulenpay_service = (
            MulenPayService() if settings.is_mulenpay_enabled() else None
        )
        self.pal24_service = (
            Pal24Service() if settings.is_pal24_enabled() else None
        )
        self.wata_service = WataService() if settings.is_wata_enabled() else None

        logger.debug(
            "PaymentService инициализирован (YooKassa=%s, Stars=%s, CryptoBot=%s, "
            "MulenPay=%s, Pal24=%s, WATA=%s)",
            bool(self.yookassa_service),
            bool(self.stars_service),
            bool(self.cryptobot_service),
            bool(self.mulenpay_service),
            bool(self.pal24_service),
            bool(self.wata_service),
        )
