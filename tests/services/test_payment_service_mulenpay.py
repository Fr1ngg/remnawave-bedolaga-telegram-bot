"""Тесты для сценариев MulenPay в PaymentService."""

from pathlib import Path
from typing import Any, Dict, Optional
from types import ModuleType, SimpleNamespace
import sys
from datetime import datetime

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import app.services.payment_service as payment_service_module  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.payment_service import PaymentService  # noqa: E402


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class DummySession:
    async def commit(self) -> None:  # pragma: no cover - метод вызывается, но без логики
        return None

    async def refresh(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class DummyLocalPayment:
    def __init__(self, payment_id: int = 501) -> None:
        self.id = payment_id
        self.created_at = datetime(2024, 1, 1, 12, 0, 0)


class StubMulenPayService:
    def __init__(self, response: Optional[Dict[str, Any]]) -> None:
        self.response = response
        self.calls: list[Dict[str, Any]] = []

    async def create_payment(self, **kwargs: Any) -> Optional[Dict[str, Any]]:
        self.calls.append(kwargs)
        return self.response


def _make_service(stub: Optional[StubMulenPayService]) -> PaymentService:
    service = PaymentService.__new__(PaymentService)  # type: ignore[call-arg]
    service.bot = None
    service.mulenpay_service = stub
    service.pal24_service = None
    service.yookassa_service = None
    service.stars_service = None
    service.cryptobot_service = None
    service.heleket_service = None
    return service


@pytest.mark.anyio("asyncio")
async def test_create_mulenpay_payment_success(monkeypatch: pytest.MonkeyPatch) -> None:
    response = {"id": 123, "paymentUrl": "https://mulenpay/pay"}
    stub = StubMulenPayService(response)
    service = _make_service(stub)
    db = DummySession()

    captured_args: Dict[str, Any] = {}

    async def fake_create_mulenpay_payment(**kwargs: Any) -> DummyLocalPayment:
        captured_args.update(kwargs)
        return DummyLocalPayment(payment_id=999)

    monkeypatch.setattr(
        payment_service_module,
        "create_mulenpay_payment",
        fake_create_mulenpay_payment,
        raising=False,
    )
    monkeypatch.setattr(settings, "MULENPAY_MIN_AMOUNT_KOPEKS", 1000, raising=False)
    monkeypatch.setattr(settings, "MULENPAY_MAX_AMOUNT_KOPEKS", 1_000_000, raising=False)
    monkeypatch.setattr(settings, "MULENPAY_VAT_CODE", 1, raising=False)
    monkeypatch.setattr(settings, "MULENPAY_PAYMENT_SUBJECT", "service", raising=False)
    monkeypatch.setattr(settings, "MULENPAY_PAYMENT_MODE", "full_payment", raising=False)
    monkeypatch.setattr(settings, "MULENPAY_LANGUAGE", "ru", raising=False)
    monkeypatch.setattr(settings, "WEBHOOK_URL", "https://example.com", raising=False)

    result = await service.create_mulenpay_payment(
        db=db,
        user_id=77,
        amount_kopeks=25000,
        description="Пополнение",
        language="en",
    )

    assert result is not None
    assert result["local_payment_id"] == 999
    assert result["mulen_payment_id"] == 123
    assert result["payment_url"] == "https://mulenpay/pay"
    assert result["status"] == "created"
    assert stub.calls and stub.calls[0]["language"] == "en"
    assert captured_args["user_id"] == 77
    assert captured_args["amount_kopeks"] == 25000
    assert captured_args["uuid"].startswith("mulen_77_")


@pytest.mark.anyio("asyncio")
async def test_create_mulenpay_payment_respects_amount_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = StubMulenPayService({"id": 1})
    service = _make_service(stub)
    db = DummySession()

    monkeypatch.setattr(settings, "MULENPAY_MIN_AMOUNT_KOPEKS", 5000, raising=False)
    monkeypatch.setattr(settings, "MULENPAY_MAX_AMOUNT_KOPEKS", 10_000, raising=False)

    result_low = await service.create_mulenpay_payment(
        db=db,
        user_id=1,
        amount_kopeks=1000,
        description="Пополнение",
    )
    assert result_low is None

    result_high = await service.create_mulenpay_payment(
        db=db,
        user_id=1,
        amount_kopeks=20_000,
        description="Пополнение",
    )
    assert result_high is None
    assert not stub.calls


@pytest.mark.anyio("asyncio")
async def test_create_mulenpay_payment_returns_none_without_service() -> None:
    service = _make_service(None)
    db = DummySession()

    result = await service.create_mulenpay_payment(
        db=db,
        user_id=1,
        amount_kopeks=5000,
        description="Пополнение",
    )
    assert result is None


@pytest.mark.anyio("asyncio")
async def test_process_mulenpay_callback_avoids_duplicate_transactions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _make_service(None)
    db = DummySession()

    class DummyPayment:
        def __init__(self) -> None:
            self.user_id = 42
            self.amount_kopeks = 1500
            self.description = "Пополнение"
            self.uuid = "mulen_1_test"
            self.transaction_id: Optional[int] = None
            self.mulen_payment_id: Optional[int] = None
            self.status = "created"
            self.is_paid = False

    payment = DummyPayment()

    async def fake_get_mulenpay_payment_by_uuid(
        _db: DummySession, uuid: str
    ) -> DummyPayment:
        assert uuid == payment.uuid
        return payment

    async def fake_update_mulenpay_payment_status(
        _db: DummySession, **kwargs: Any
    ) -> DummyPayment:
        payment.status = kwargs.get("status", payment.status)
        payment.mulen_payment_id = kwargs.get("mulen_payment_id", payment.mulen_payment_id)
        return payment

    transaction_calls: list[Dict[str, Any]] = []

    class DummyTransaction:
        def __init__(self, transaction_id: int = 555) -> None:
            self.id = transaction_id

    async def fake_create_transaction(_db: DummySession, **kwargs: Any) -> DummyTransaction:
        transaction_calls.append(kwargs)
        return DummyTransaction()

    async def fake_link_payment(
        db: DummySession, *, payment: DummyPayment, transaction_id: int
    ) -> DummyPayment:
        payment.transaction_id = transaction_id
        return payment

    class DummyUser:
        def __init__(self) -> None:
            self.id = payment.user_id
            self.telegram_id = 99
            self.balance_kopeks = 0
            self.has_made_first_topup = False
            self.language = "ru"
            self.promo_group = None
            self.subscription = None

    dummy_user = DummyUser()

    async def fake_get_user_by_id(_db: DummySession, user_id: int) -> DummyUser:
        assert user_id == payment.user_id
        return dummy_user

    balance_call: Dict[str, Any] = {}

    async def fake_add_user_balance(
        _db: DummySession,
        user: DummyUser,
        amount_kopeks: int,
        description: str,
        *,
        create_transaction: bool = True,
        **_kwargs: Any,
    ) -> bool:
        balance_call.update(
            {
                "create_transaction": create_transaction,
                "description": description,
                "amount_kopeks": amount_kopeks,
            }
        )
        user.balance_kopeks += amount_kopeks
        return True

    async def fake_process_referral_topup(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def fake_auto_purchase_saved_cart_after_topup(*_args: Any, **_kwargs: Any) -> bool:
        return False

    async def fake_has_user_cart(*_args: Any, **_kwargs: Any) -> bool:
        return False

    referral_module = ModuleType("app.services.referral_service")
    referral_module.process_referral_topup = fake_process_referral_topup  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.services.referral_service", referral_module)

    auto_module = ModuleType("app.services.subscription_auto_purchase_service")
    auto_module.auto_purchase_saved_cart_after_topup = (  # type: ignore[attr-defined]
        fake_auto_purchase_saved_cart_after_topup
    )
    monkeypatch.setitem(sys.modules, "app.services.subscription_auto_purchase_service", auto_module)

    user_cart_module = ModuleType("app.services.user_cart_service")
    user_cart_module.user_cart_service = SimpleNamespace(  # type: ignore[attr-defined]
        has_user_cart=fake_has_user_cart
    )
    monkeypatch.setitem(sys.modules, "app.services.user_cart_service", user_cart_module)

    monkeypatch.setattr(
        payment_service_module,
        "get_mulenpay_payment_by_uuid",
        fake_get_mulenpay_payment_by_uuid,
        raising=False,
    )
    monkeypatch.setattr(
        payment_service_module,
        "update_mulenpay_payment_status",
        fake_update_mulenpay_payment_status,
        raising=False,
    )
    monkeypatch.setattr(
        payment_service_module,
        "create_transaction",
        fake_create_transaction,
        raising=False,
    )
    monkeypatch.setattr(
        payment_service_module,
        "link_mulenpay_payment_to_transaction",
        fake_link_payment,
        raising=False,
    )
    monkeypatch.setattr(
        payment_service_module,
        "get_user_by_id",
        fake_get_user_by_id,
        raising=False,
    )
    monkeypatch.setattr(
        payment_service_module,
        "add_user_balance",
        fake_add_user_balance,
        raising=False,
    )

    result = await service.process_mulenpay_callback(
        db,
        {"uuid": payment.uuid, "payment_status": "success", "id": 123, "amount": 1500},
    )

    assert result is True
    assert transaction_calls, "create_transaction should be called"
    assert balance_call["create_transaction"] is False
    assert dummy_user.balance_kopeks == payment.amount_kopeks
    assert payment.transaction_id is not None
