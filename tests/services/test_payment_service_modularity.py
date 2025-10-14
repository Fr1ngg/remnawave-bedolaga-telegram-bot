"""Проверяем, что PaymentService собирается из mixin-классов."""

from pathlib import Path
import sys

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.payment import (  # noqa: E402
    CryptoBotPaymentMixin,
    MulenPayPaymentMixin,
    Pal24PaymentMixin,
    PaymentCommonMixin,
    TelegramStarsMixin,
    TributePaymentMixin,
    WataPaymentMixin,
    YooKassaPaymentMixin,
)
from app.services.payment_service import PaymentService  # noqa: E402


def test_payment_service_mro_contains_all_mixins() -> None:
    """Убеждаемся, что сервис действительно включает все mixin-классы."""
    mixins = {
        PaymentCommonMixin,
        TelegramStarsMixin,
        YooKassaPaymentMixin,
        TributePaymentMixin,
        CryptoBotPaymentMixin,
        MulenPayPaymentMixin,
        Pal24PaymentMixin,
        WataPaymentMixin,
    }
    service_mro = set(PaymentService.__mro__)
    assert mixins.issubset(service_mro), "PaymentService должен содержать все mixin-классы"


@pytest.mark.parametrize(
    "attribute",
    [
        "build_topup_success_keyboard",
        "create_stars_invoice",
        "create_yookassa_payment",
        "create_tribute_payment",
        "create_cryptobot_payment",
        "create_mulenpay_payment",
        "create_pal24_payment",
        "create_wata_payment",
    ],
)
def test_payment_service_exposes_provider_methods(attribute: str) -> None:
    """Каждый mixin обязан добавить публичный метод в PaymentService."""
    assert hasattr(PaymentService, attribute), f"Отсутствует метод {attribute}"
