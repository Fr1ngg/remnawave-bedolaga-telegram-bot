import logging
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import WataPayment

logger = logging.getLogger(__name__)


async def create_wata_payment(
    db: AsyncSession,
    *,
    user_id: int,
    payment_link_id: str,
    amount_kopeks: int,
    currency: str,
    description: Optional[str],
    status: str,
    url: Optional[str],
    type_: Optional[str] = None,
    order_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> WataPayment:
    payment = WataPayment(
        user_id=user_id,
        payment_link_id=payment_link_id,
        order_id=order_id,
        type=type_,
        amount_kopeks=amount_kopeks,
        currency=currency,
        description=description,
        status=status,
        url=url,
        metadata_json=metadata or {},
    )

    db.add(payment)
    await db.flush()
    await db.refresh(payment)

    logger.info(
        "Создан WATA платеж #%s (link=%s) на сумму %s копеек для пользователя %s",
        payment.id,
        payment_link_id,
        amount_kopeks,
        user_id,
    )

    return payment


async def get_wata_payment_by_id(db: AsyncSession, payment_id: int) -> Optional[WataPayment]:
    result = await db.execute(
        select(WataPayment).where(WataPayment.id == payment_id)
    )
    return result.scalar_one_or_none()


async def get_wata_payment_by_link_id(
    db: AsyncSession,
    payment_link_id: str,
) -> Optional[WataPayment]:
    result = await db.execute(
        select(WataPayment).where(WataPayment.payment_link_id == payment_link_id)
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
    payment: WataPayment,
    *,
    status: Optional[str] = None,
    url: Optional[str] = None,
    is_paid: Optional[bool] = None,
    paid_at: Optional[datetime] = None,
    transaction_uuid: Optional[str] = None,
    transaction_status: Optional[str] = None,
    transaction_type: Optional[str] = None,
    error_code: Optional[str] = None,
    error_description: Optional[str] = None,
    payment_time: Optional[datetime] = None,
    metadata: Optional[Dict[str, Any]] = None,
    callback_payload: Optional[Dict[str, Any]] = None,
) -> WataPayment:
    if status is not None:
        payment.status = status
    if url is not None:
        payment.url = url
    if is_paid is not None:
        payment.is_paid = is_paid
    if paid_at is not None:
        payment.paid_at = paid_at
    if transaction_uuid is not None:
        payment.transaction_uuid = transaction_uuid
    if transaction_status is not None:
        payment.transaction_status = transaction_status
    if transaction_type is not None:
        payment.transaction_type = transaction_type
    if error_code is not None:
        payment.error_code = error_code
    if error_description is not None:
        payment.error_description = error_description
    if payment_time is not None:
        payment.payment_time = payment_time
    if metadata is not None:
        payment.metadata_json = metadata
    if callback_payload is not None:
        payment.callback_payload = callback_payload

    await db.flush()
    await db.refresh(payment)
    return payment


async def link_wata_payment_to_transaction(
    db: AsyncSession,
    payment: WataPayment,
    transaction_id: int,
) -> WataPayment:
    payment.transaction_id = transaction_id
    payment.is_paid = True
    await db.flush()
    await db.refresh(payment)
    logger.info(
        "Привязана транзакция %s к WATA платежу %s",
        transaction_id,
        payment.id,
    )
    return payment
