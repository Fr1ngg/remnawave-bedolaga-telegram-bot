"""Helpers for inspecting and manually checking pending top-up payments."""

from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database.database import AsyncSessionLocal
from app.database.models import (
    CryptoBotPayment,
    HeleketPayment,
    MulenPayPayment,
    Pal24Payment,
    PaymentMethod,
    Transaction,
    TransactionType,
    User,
    WataPayment,
    YooKassaPayment,
)

logger = logging.getLogger(__name__)


PENDING_MAX_AGE = timedelta(hours=24)


@dataclass(slots=True)
class PendingPayment:
    """Normalized representation of a provider specific payment entry."""

    method: PaymentMethod
    local_id: int
    identifier: str
    amount_kopeks: int
    status: str
    is_paid: bool
    created_at: datetime
    user: User
    payment: Any
    expires_at: Optional[datetime] = None

    def is_recent(self, max_age: timedelta = PENDING_MAX_AGE) -> bool:
        return (datetime.utcnow() - self.created_at) <= max_age


SUPPORTED_MANUAL_CHECK_METHODS: frozenset[PaymentMethod] = frozenset(
    {
        PaymentMethod.YOOKASSA,
        PaymentMethod.MULENPAY,
        PaymentMethod.PAL24,
        PaymentMethod.WATA,
        PaymentMethod.HELEKET,
        PaymentMethod.CRYPTOBOT,
    }
)


SUPPORTED_AUTO_CHECK_METHODS: frozenset[PaymentMethod] = frozenset(
    {
        PaymentMethod.YOOKASSA,
        PaymentMethod.MULENPAY,
        PaymentMethod.PAL24,
        PaymentMethod.WATA,
        PaymentMethod.CRYPTOBOT,
    }
)


def method_display_name(method: PaymentMethod) -> str:
    if method == PaymentMethod.MULENPAY:
        return settings.get_mulenpay_display_name()
    if method == PaymentMethod.PAL24:
        return "PayPalych"
    if method == PaymentMethod.YOOKASSA:
        return "YooKassa"
    if method == PaymentMethod.WATA:
        return "WATA"
    if method == PaymentMethod.CRYPTOBOT:
        return "CryptoBot"
    if method == PaymentMethod.HELEKET:
        return "Heleket"
    if method == PaymentMethod.TELEGRAM_STARS:
        return "Telegram Stars"
    return method.value


def _method_is_enabled(method: PaymentMethod) -> bool:
    if method == PaymentMethod.YOOKASSA:
        return settings.is_yookassa_enabled()
    if method == PaymentMethod.MULENPAY:
        return settings.is_mulenpay_enabled()
    if method == PaymentMethod.PAL24:
        return settings.is_pal24_enabled()
    if method == PaymentMethod.WATA:
        return settings.is_wata_enabled()
    if method == PaymentMethod.CRYPTOBOT:
        return settings.is_cryptobot_enabled()
    if method == PaymentMethod.HELEKET:
        return settings.is_heleket_enabled()
    return False


def get_enabled_auto_methods() -> List[PaymentMethod]:
    return [
        method
        for method in SUPPORTED_AUTO_CHECK_METHODS
        if _method_is_enabled(method)
    ]


class AutoPaymentVerificationService:
    """Background checker that periodically refreshes pending payments."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task[None]] = None
        self._payment_service: Optional["PaymentService"] = None

    def set_payment_service(self, payment_service: "PaymentService") -> None:
        self._payment_service = payment_service

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        await self.stop()

        if not settings.is_payment_verification_auto_check_enabled():
            logger.info("Автопроверка пополнений отключена настройками")
            return

        if not self._payment_service:
            logger.warning(
                "Автопроверка пополнений не запущена: PaymentService не инициализирован"
            )
            return

        methods = get_enabled_auto_methods()
        if not methods:
            logger.info(
                "Автопроверка пополнений не запущена: нет активных провайдеров"
            )
            return

        display_names = ", ".join(
            sorted(method_display_name(method) for method in methods)
        )
        interval_minutes = settings.get_payment_verification_auto_check_interval()

        self._task = asyncio.create_task(self._auto_check_loop())
        logger.info(
            "🔄 Автопроверка пополнений запущена (каждые %s мин) для: %s",
            interval_minutes,
            display_names,
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _auto_check_loop(self) -> None:
        try:
            while True:
                interval_minutes = settings.get_payment_verification_auto_check_interval()
                try:
                    if (
                        settings.is_payment_verification_auto_check_enabled()
                        and self._payment_service
                    ):
                        methods = get_enabled_auto_methods()
                        if methods:
                            await self._run_checks(methods)
                        else:
                            logger.debug(
                                "Автопроверка пополнений: активных провайдеров нет"
                            )
                    else:
                        logger.debug(
                            "Автопроверка пополнений: отключена настройками или сервис не готов"
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as error:  # noqa: BLE001 - логируем непредвиденные ошибки
                    logger.error(
                        "Ошибка автопроверки пополнений: %s",
                        error,
                        exc_info=True,
                    )

                await asyncio.sleep(max(1, interval_minutes) * 60)
        except asyncio.CancelledError:
            logger.info("Автопроверка пополнений остановлена")
            raise

    async def _run_checks(self, methods: List[PaymentMethod]) -> None:
        if not self._payment_service:
            return

        async with AsyncSessionLocal() as session:
            try:
                pending = await list_recent_pending_payments(session)
                candidates = [
                    record
                    for record in pending
                    if record.method in methods and not record.is_paid
                ]

                if not candidates:
                    logger.debug(
                        "Автопроверка пополнений: подходящих ожидающих платежей нет"
                    )
                    return

                counts = Counter(record.method for record in candidates)
                summary = ", ".join(
                    f"{method_display_name(method)}: {count}"
                    for method, count in sorted(
                        counts.items(), key=lambda item: method_display_name(item[0])
                    )
                )
                logger.info(
                    "🔄 Автопроверка пополнений: найдено %s инвойсов (%s)",
                    len(candidates),
                    summary,
                )

                for record in candidates:
                    refreshed = await run_manual_check(
                        session,
                        record.method,
                        record.local_id,
                        self._payment_service,
                    )

                    if not refreshed:
                        logger.debug(
                            "Автопроверка пополнений: не удалось обновить %s %s",
                            method_display_name(record.method),
                            record.identifier,
                        )
                        continue

                    if refreshed.is_paid and not record.is_paid:
                        logger.info(
                            "✅ %s %s отмечен как оплаченный после автопроверки",
                            method_display_name(refreshed.method),
                            refreshed.identifier,
                        )
                    elif refreshed.status != record.status:
                        logger.info(
                            "ℹ️ %s %s обновлён: %s → %s",
                            method_display_name(refreshed.method),
                            refreshed.identifier,
                            record.status or "—",
                            refreshed.status or "—",
                        )
                    else:
                        logger.debug(
                            "Автопроверка пополнений: %s %s без изменений (%s)",
                            method_display_name(refreshed.method),
                            refreshed.identifier,
                            refreshed.status or "—",
                        )

                await session.commit()
            except Exception:
                await session.rollback()
                raise


auto_payment_verification_service = AutoPaymentVerificationService()

def _is_pal24_pending(payment: Pal24Payment) -> bool:
    if payment.is_paid:
        return False
    status = (payment.status or "").upper()
    return status in {"NEW", "PROCESS"}


def _is_mulenpay_pending(payment: MulenPayPayment) -> bool:
    if payment.is_paid:
        return False
    status = (payment.status or "").lower()
    return status in {"created", "processing", "hold"}


def _is_wata_pending(payment: WataPayment) -> bool:
    if payment.is_paid:
        return False
    status = (payment.status or "").lower()
    return status not in {
        "paid",
        "closed",
        "declined",
        "canceled",
        "cancelled",
        "expired",
    }


def _is_heleket_pending(payment: HeleketPayment) -> bool:
    if payment.is_paid:
        return False
    status = (payment.status or "").lower()
    return status not in {"paid", "paid_over", "cancel", "canceled", "failed", "fail", "expired"}


def _is_yookassa_pending(payment: YooKassaPayment) -> bool:
    if getattr(payment, "is_paid", False) and payment.status == "succeeded":
        return False
    status = (payment.status or "").lower()
    return status in {"pending", "waiting_for_capture"}


def _is_cryptobot_pending(payment: CryptoBotPayment) -> bool:
    status = (payment.status or "").lower()
    return status == "active"


def _parse_cryptobot_amount_kopeks(payment: CryptoBotPayment) -> int:
    payload = payment.payload or ""
    match = re.search(r"_(\d+)$", payload)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return 0
    return 0


def _metadata_is_balance(payment: YooKassaPayment) -> bool:
    metadata = getattr(payment, "metadata_json", {}) or {}
    payment_type = str(metadata.get("type") or metadata.get("payment_type") or "").lower()
    return payment_type.startswith("balance_topup")


def _build_record(method: PaymentMethod, payment: Any, *, identifier: str, amount_kopeks: int,
                  status: str, is_paid: bool, expires_at: Optional[datetime] = None) -> Optional[PendingPayment]:
    user = getattr(payment, "user", None)
    if user is None:
        logger.debug("Skipping %s payment %s without linked user", method.value, identifier)
        return None

    created_at = getattr(payment, "created_at", None)
    if not isinstance(created_at, datetime):
        logger.debug("Skipping %s payment %s without valid created_at", method.value, identifier)
        return None

    local_id = getattr(payment, "id", None)
    if local_id is None:
        logger.debug("Skipping %s payment without local id", method.value)
        return None

    return PendingPayment(
        method=method,
        local_id=int(local_id),
        identifier=identifier,
        amount_kopeks=amount_kopeks,
        status=status,
        is_paid=is_paid,
        created_at=created_at,
        user=user,
        payment=payment,
        expires_at=expires_at,
    )


async def _fetch_pal24_payments(db: AsyncSession, cutoff: datetime) -> List[PendingPayment]:
    stmt = (
        select(Pal24Payment)
        .options(selectinload(Pal24Payment.user))
        .where(Pal24Payment.created_at >= cutoff)
        .order_by(desc(Pal24Payment.created_at))
    )
    result = await db.execute(stmt)
    records: List[PendingPayment] = []
    for payment in result.scalars().all():
        if not _is_pal24_pending(payment):
            continue
        record = _build_record(
            PaymentMethod.PAL24,
            payment,
            identifier=payment.bill_id,
            amount_kopeks=payment.amount_kopeks,
            status=payment.status or "",
            is_paid=bool(payment.is_paid),
            expires_at=getattr(payment, "expires_at", None),
        )
        if record:
            records.append(record)
    return records


async def _fetch_mulenpay_payments(db: AsyncSession, cutoff: datetime) -> List[PendingPayment]:
    stmt = (
        select(MulenPayPayment)
        .options(selectinload(MulenPayPayment.user))
        .where(MulenPayPayment.created_at >= cutoff)
        .order_by(desc(MulenPayPayment.created_at))
    )
    result = await db.execute(stmt)
    records: List[PendingPayment] = []
    for payment in result.scalars().all():
        if not _is_mulenpay_pending(payment):
            continue
        record = _build_record(
            PaymentMethod.MULENPAY,
            payment,
            identifier=payment.uuid,
            amount_kopeks=payment.amount_kopeks,
            status=payment.status or "",
            is_paid=bool(payment.is_paid),
        )
        if record:
            records.append(record)
    return records


async def _fetch_wata_payments(db: AsyncSession, cutoff: datetime) -> List[PendingPayment]:
    stmt = (
        select(WataPayment)
        .options(selectinload(WataPayment.user))
        .where(WataPayment.created_at >= cutoff)
        .order_by(desc(WataPayment.created_at))
    )
    result = await db.execute(stmt)
    records: List[PendingPayment] = []
    for payment in result.scalars().all():
        if not _is_wata_pending(payment):
            continue
        record = _build_record(
            PaymentMethod.WATA,
            payment,
            identifier=payment.payment_link_id,
            amount_kopeks=payment.amount_kopeks,
            status=payment.status or "",
            is_paid=bool(payment.is_paid),
            expires_at=getattr(payment, "expires_at", None),
        )
        if record:
            records.append(record)
    return records


async def _fetch_heleket_payments(db: AsyncSession, cutoff: datetime) -> List[PendingPayment]:
    stmt = (
        select(HeleketPayment)
        .options(selectinload(HeleketPayment.user))
        .where(HeleketPayment.created_at >= cutoff)
        .order_by(desc(HeleketPayment.created_at))
    )
    result = await db.execute(stmt)
    records: List[PendingPayment] = []
    for payment in result.scalars().all():
        if not _is_heleket_pending(payment):
            continue
        record = _build_record(
            PaymentMethod.HELEKET,
            payment,
            identifier=payment.uuid,
            amount_kopeks=payment.amount_kopeks,
            status=payment.status or "",
            is_paid=bool(payment.is_paid),
            expires_at=getattr(payment, "expires_at", None),
        )
        if record:
            records.append(record)
    return records


async def _fetch_yookassa_payments(db: AsyncSession, cutoff: datetime) -> List[PendingPayment]:
    stmt = (
        select(YooKassaPayment)
        .options(selectinload(YooKassaPayment.user))
        .where(YooKassaPayment.created_at >= cutoff)
        .order_by(desc(YooKassaPayment.created_at))
    )
    result = await db.execute(stmt)
    records: List[PendingPayment] = []
    for payment in result.scalars().all():
        if payment.transaction_id:
            continue
        if not _metadata_is_balance(payment):
            continue
        if not _is_yookassa_pending(payment):
            continue
        record = _build_record(
            PaymentMethod.YOOKASSA,
            payment,
            identifier=payment.yookassa_payment_id,
            amount_kopeks=payment.amount_kopeks,
            status=payment.status or "",
            is_paid=bool(getattr(payment, "is_paid", False)),
        )
        if record:
            records.append(record)
    return records


async def _fetch_cryptobot_payments(db: AsyncSession, cutoff: datetime) -> List[PendingPayment]:
    stmt = (
        select(CryptoBotPayment)
        .options(selectinload(CryptoBotPayment.user))
        .where(CryptoBotPayment.created_at >= cutoff)
        .order_by(desc(CryptoBotPayment.created_at))
    )
    result = await db.execute(stmt)
    records: List[PendingPayment] = []
    for payment in result.scalars().all():
        status = (payment.status or "").lower()
        if not _is_cryptobot_pending(payment) and status != "paid":
            continue
        amount_kopeks = _parse_cryptobot_amount_kopeks(payment)
        record = _build_record(
            PaymentMethod.CRYPTOBOT,
            payment,
            identifier=payment.invoice_id,
            amount_kopeks=amount_kopeks,
            status=payment.status or "",
            is_paid=bool(payment.is_paid),
        )
        if record:
            records.append(record)
    return records


async def _fetch_stars_transactions(db: AsyncSession, cutoff: datetime) -> List[PendingPayment]:
    stmt = (
        select(Transaction)
        .options(selectinload(Transaction.user))
        .where(
            Transaction.created_at >= cutoff,
            Transaction.type == TransactionType.DEPOSIT.value,
            Transaction.payment_method == PaymentMethod.TELEGRAM_STARS.value,
        )
        .order_by(desc(Transaction.created_at))
    )
    result = await db.execute(stmt)
    records: List[PendingPayment] = []
    for transaction in result.scalars().all():
        record = _build_record(
            PaymentMethod.TELEGRAM_STARS,
            transaction,
            identifier=transaction.external_id or str(transaction.id),
            amount_kopeks=transaction.amount_kopeks,
            status="paid" if transaction.is_completed else "pending",
            is_paid=bool(transaction.is_completed),
        )
        if record:
            records.append(record)
    return records


async def list_recent_pending_payments(
    db: AsyncSession,
    *,
    max_age: timedelta = PENDING_MAX_AGE,
) -> List[PendingPayment]:
    """Return pending payments (top-ups) from supported providers within the age window."""

    cutoff = datetime.utcnow() - max_age

    tasks: Iterable[List[PendingPayment]] = (
        await _fetch_yookassa_payments(db, cutoff),
        await _fetch_pal24_payments(db, cutoff),
        await _fetch_mulenpay_payments(db, cutoff),
        await _fetch_wata_payments(db, cutoff),
        await _fetch_heleket_payments(db, cutoff),
        await _fetch_cryptobot_payments(db, cutoff),
        await _fetch_stars_transactions(db, cutoff),
    )

    records: List[PendingPayment] = []
    for batch in tasks:
        records.extend(batch)

    records.sort(key=lambda item: item.created_at, reverse=True)
    return records


async def get_payment_record(
    db: AsyncSession,
    method: PaymentMethod,
    local_payment_id: int,
) -> Optional[PendingPayment]:
    """Load single payment record and normalize it to :class:`PendingPayment`."""

    cutoff = datetime.utcnow() - PENDING_MAX_AGE

    if method == PaymentMethod.PAL24:
        payment = await db.get(Pal24Payment, local_payment_id)
        if not payment:
            return None
        await db.refresh(payment, attribute_names=["user"])
        return _build_record(
            method,
            payment,
            identifier=payment.bill_id,
            amount_kopeks=payment.amount_kopeks,
            status=payment.status or "",
            is_paid=bool(payment.is_paid),
            expires_at=getattr(payment, "expires_at", None),
        )

    if method == PaymentMethod.MULENPAY:
        payment = await db.get(MulenPayPayment, local_payment_id)
        if not payment:
            return None
        await db.refresh(payment, attribute_names=["user"])
        return _build_record(
            method,
            payment,
            identifier=payment.uuid,
            amount_kopeks=payment.amount_kopeks,
            status=payment.status or "",
            is_paid=bool(payment.is_paid),
        )

    if method == PaymentMethod.WATA:
        payment = await db.get(WataPayment, local_payment_id)
        if not payment:
            return None
        await db.refresh(payment, attribute_names=["user"])
        return _build_record(
            method,
            payment,
            identifier=payment.payment_link_id,
            amount_kopeks=payment.amount_kopeks,
            status=payment.status or "",
            is_paid=bool(payment.is_paid),
            expires_at=getattr(payment, "expires_at", None),
        )

    if method == PaymentMethod.HELEKET:
        payment = await db.get(HeleketPayment, local_payment_id)
        if not payment:
            return None
        await db.refresh(payment, attribute_names=["user"])
        return _build_record(
            method,
            payment,
            identifier=payment.uuid,
            amount_kopeks=payment.amount_kopeks,
            status=payment.status or "",
            is_paid=bool(payment.is_paid),
            expires_at=getattr(payment, "expires_at", None),
        )

    if method == PaymentMethod.YOOKASSA:
        payment = await db.get(YooKassaPayment, local_payment_id)
        if not payment:
            return None
        await db.refresh(payment, attribute_names=["user"])
        if payment.created_at < cutoff:
            logger.debug("YooKassa payment %s is older than cutoff", payment.id)
        return _build_record(
            method,
            payment,
            identifier=payment.yookassa_payment_id,
            amount_kopeks=payment.amount_kopeks,
            status=payment.status or "",
            is_paid=bool(getattr(payment, "is_paid", False)),
        )

    if method == PaymentMethod.CRYPTOBOT:
        payment = await db.get(CryptoBotPayment, local_payment_id)
        if not payment:
            return None
        await db.refresh(payment, attribute_names=["user"])
        amount_kopeks = _parse_cryptobot_amount_kopeks(payment)
        return _build_record(
            method,
            payment,
            identifier=payment.invoice_id,
            amount_kopeks=amount_kopeks,
            status=payment.status or "",
            is_paid=bool(payment.is_paid),
        )

    if method == PaymentMethod.TELEGRAM_STARS:
        transaction = await db.get(Transaction, local_payment_id)
        if not transaction:
            return None
        await db.refresh(transaction, attribute_names=["user"])
        if transaction.payment_method != PaymentMethod.TELEGRAM_STARS.value:
            return None
        return _build_record(
            method,
            transaction,
            identifier=transaction.external_id or str(transaction.id),
            amount_kopeks=transaction.amount_kopeks,
            status="paid" if transaction.is_completed else "pending",
            is_paid=bool(transaction.is_completed),
        )

    logger.debug("Unsupported payment method requested: %s", method)
    return None


async def run_manual_check(
    db: AsyncSession,
    method: PaymentMethod,
    local_payment_id: int,
    payment_service: "PaymentService",
) -> Optional[PendingPayment]:
    """Trigger provider specific status refresh and return the updated record."""

    try:
        if method == PaymentMethod.PAL24:
            result = await payment_service.get_pal24_payment_status(db, local_payment_id)
            payment = result.get("payment") if result else None
        elif method == PaymentMethod.MULENPAY:
            result = await payment_service.get_mulenpay_payment_status(db, local_payment_id)
            payment = result.get("payment") if result else None
        elif method == PaymentMethod.WATA:
            result = await payment_service.get_wata_payment_status(db, local_payment_id)
            payment = result.get("payment") if result else None
        elif method == PaymentMethod.HELEKET:
            payment = await payment_service.sync_heleket_payment_status(
                db, local_payment_id=local_payment_id
            )
        elif method == PaymentMethod.YOOKASSA:
            result = await payment_service.get_yookassa_payment_status(db, local_payment_id)
            payment = result.get("payment") if result else None
        elif method == PaymentMethod.CRYPTOBOT:
            result = await payment_service.get_cryptobot_payment_status(db, local_payment_id)
            payment = result.get("payment") if result else None
        else:
            logger.warning("Manual check requested for unsupported method %s", method)
            return None

        if not payment:
            return None

        return await get_payment_record(db, method, local_payment_id)

    except Exception as error:  # pragma: no cover - defensive logging
        logger.error(
            "Manual status check failed for %s payment %s: %s",
            method.value,
            local_payment_id,
            error,
            exc_info=True,
        )
        return None


if TYPE_CHECKING:  # pragma: no cover
    from app.services.payment_service import PaymentService

