"""CRUD helpers for WATA payment records."""

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import WataPayment

logger = logging.getLogger(__name__)


async def create_wata_payment(
    db: AsyncSession,
    *,
    user_id: int,
    link_id: str,
    order_id: str,
    amount_kopeks: int,
    currency: str,
    description: str,
    link_status: Optional[str],
    payment_url: Optional[str],
    metadata: Optional[dict] = None,
) -> WataPayment:
    payment = WataPayment(
        user_id=user_id,
        link_id=link_id,
        order_id=order_id,
        amount_kopeks=amount_kopeks,
        currency=currency,
        description=description,
        link_status=link_status,
        payment_url=payment_url,
        metadata_json=metadata or {},
    )

    db.add(payment)
    await db.commit()
    await db.refresh(payment)

    logger.info(
        "Создан WATA платеж #%s (link_id=%s) на сумму %s копеек для пользователя %s",
        payment.id,
        link_id,
        amount_kopeks,
        user_id,
    )

    return payment


async def get_wata_payment_by_id(
    db: AsyncSession,
    payment_id: int,
) -> Optional[WataPayment]:
    result = await db.execute(
        select(WataPayment).where(WataPayment.id == payment_id)
    )
    return result.scalar_one_or_none()


async def get_wata_payment_by_link_id(
    db: AsyncSession,
    link_id: str,
) -> Optional[WataPayment]:
    result = await db.execute(
        select(WataPayment).where(WataPayment.link_id == link_id)
    )
    return result.scalar_one_or_none()


async def get_wata_payment_by_order_id(
    db: AsyncSession,
    order_id: str,
) -> Optional[WataPayment]:
    result = await db.execute(
        select(WataPayment).where(WataPayment.order_id == order_id)
    )
    return result.scalar_one_or_none()


async def get_wata_payment_by_transaction_uuid(
    db: AsyncSession,
    transaction_uuid: str,
) -> Optional[WataPayment]:
    result = await db.execute(
        select(WataPayment).where(WataPayment.transaction_uuid == transaction_uuid)
    )
    return result.scalar_one_or_none()


async def update_wata_payment_status(
    db: AsyncSession,
    *,
    payment: WataPayment,
    link_status: Optional[str] = None,
    transaction_status: Optional[str] = None,
    transaction_uuid: Optional[str] = None,
    is_paid: Optional[bool] = None,
    paid_at: Optional[datetime] = None,
    callback_payload: Optional[dict] = None,
) -> WataPayment:
    if link_status is not None:
        payment.link_status = link_status
    if transaction_status is not None:
        payment.transaction_status = transaction_status
    if transaction_uuid is not None:
        payment.transaction_uuid = transaction_uuid
    if is_paid is not None:
        payment.is_paid = is_paid
    if paid_at is not None:
        payment.paid_at = paid_at
    if callback_payload is not None:
        payment.callback_payload = callback_payload

    payment.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(payment)
    return payment


async def link_wata_payment_to_transaction(
    db: AsyncSession,
    payment: WataPayment,
    transaction_id: int,
) -> WataPayment:
    payment.transaction_id = transaction_id
    payment.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(payment)
    return payment
