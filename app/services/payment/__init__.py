"""Пакет с mixin-классами, делающими платёжный сервис модульным.

Здесь собираем все вспомогательные части, чтобы основной `PaymentService`
оставался компактным и импортировал только нужные компоненты.
"""

from .common import PaymentCommonMixin
from .stars import TelegramStarsMixin
from .yookassa import YooKassaPaymentMixin
from .tribute import TributePaymentMixin
from .cryptobot import CryptoBotPaymentMixin
from .mulenpay import MulenPayPaymentMixin
from .pal24 import Pal24PaymentMixin
from .wata import WataPaymentMixin

__all__ = [
    "PaymentCommonMixin",
    "TelegramStarsMixin",
    "YooKassaPaymentMixin",
    "TributePaymentMixin",
    "CryptoBotPaymentMixin",
    "MulenPayPaymentMixin",
    "Pal24PaymentMixin",
    "WataPaymentMixin",
]
