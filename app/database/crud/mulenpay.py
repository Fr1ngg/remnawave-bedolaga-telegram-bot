import logging
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database.models import MulenPayPayment

logger = logging.getLogger(__name__)


async def create_mulenpay_payment(
    db: AsyncSession,
    *,
    user_id: int,
    amount_kopeks: int,
    uuid: str,
    description: str,
    payment_url: Optional[str],
    mulen_payment_id: Optional[int],
    currency: str,
    status: str,
    metadata: Optional[dict] = None,
) -> MulenPayPayment:
    payment = MulenPayPayment(
        user_id=user_id,
        amount_kopeks=amount_kopeks,
        uuid=uuid,
        description=description,
        payment_url=payment_url,
        mulen_payment_id=mulen_payment_id,
        currency=currency,
        status=status,
        metadata_json=metadata or {},
    )

    db.add(payment)
    await db.commit()
    await db.refresh(payment)

    logger.info(
        "Создан %s платеж #%s (uuid=%s) на сумму %s копеек для пользователя %s",
        settings.get_mulenpay_display_name(),
        payment.mulen_payment_id,
        uuid,
        amount_kopeks,
        user_id,
    )

    return payment


async def get_mulenpay_payment_by_local_id(
    db: AsyncSession, payment_id: int
) -> Optional[MulenPayPayment]:
    result = await db.execute(
        select(MulenPayPayment)
        .options(selectinload(MulenPayPayment.user))
        .where(MulenPayPayment.id == payment_id)
    )
    return result.scalar_one_or_none()


async def get_mulenpay_payment_by_uuid(
    db: AsyncSession, uuid: str
) -> Optional[MulenPayPayment]:
    result = await db.execute(
        select(MulenPayPayment)
        .options(selectinload(MulenPayPayment.user))
        .where(MulenPayPayment.uuid == uuid)
    )
    return result.scalar_one_or_none()


async def get_mulenpay_payment_by_mulen_id(
    db: AsyncSession, mulen_payment_id: int
) -> Optional[MulenPayPayment]:
    result = await db.execute(
        select(MulenPayPayment)
        .options(selectinload(MulenPayPayment.user))
        .where(MulenPayPayment.mulen_payment_id == mulen_payment_id)
    )
    return result.scalar_one_or_none()


async def get_pending_mulenpay_payments(
    db: AsyncSession,
    *,
    max_age_hours: int = 24,
) -> list[MulenPayPayment]:
    cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)

    result = await db.execute(
        select(MulenPayPayment)
        .options(selectinload(MulenPayPayment.user))
        .where(
            MulenPayPayment.is_paid.is_(False),
            MulenPayPayment.created_at >= cutoff,
        )
        .order_by(MulenPayPayment.created_at.desc())
    )
    return result.scalars().all()


async def update_mulenpay_payment_status(
    db: AsyncSession,
    *,
    payment: MulenPayPayment,
    status: str,
    is_paid: Optional[bool] = None,
    paid_at: Optional[datetime] = None,
    callback_payload: Optional[dict] = None,
    mulen_payment_id: Optional[int] = None,
) -> MulenPayPayment:
    payment.status = status
    if is_paid is not None:
        payment.is_paid = is_paid
    if paid_at:
        payment.paid_at = paid_at
    if callback_payload is not None:
        payment.callback_payload = callback_payload
    if mulen_payment_id is not None and not payment.mulen_payment_id:
        payment.mulen_payment_id = mulen_payment_id

    payment.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(payment)
    return payment


async def link_mulenpay_payment_to_transaction(
    db: AsyncSession,
    *,
    payment: MulenPayPayment,
    transaction_id: int,
) -> MulenPayPayment:
    payment.transaction_id = transaction_id
    payment.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(payment)
    return payment
