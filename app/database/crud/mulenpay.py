import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import MulenPayPayment

logger = logging.getLogger(__name__)


async def create_mulenpay_payment(
    db: AsyncSession,
    user_id: int,
    mulen_payment_id: int,
    uuid: str,
    amount_kopeks: int,
    currency: str,
    description: str,
    status: str,
    payment_url: Optional[str] = None,
    metadata_json: Optional[dict] = None,
    payment_data_json: Optional[dict] = None,
    callback_data: Optional[dict] = None,
    is_paid: bool = False,
    paid_at: Optional[datetime] = None,
) -> MulenPayPayment:

    payment = MulenPayPayment(
        user_id=user_id,
        mulen_payment_id=mulen_payment_id,
        uuid=uuid,
        amount_kopeks=amount_kopeks,
        currency=currency,
        description=description,
        status=status,
        payment_url=payment_url,
        metadata_json=metadata_json,
        payment_data_json=payment_data_json,
        callback_data=callback_data,
        is_paid=is_paid,
        paid_at=paid_at,
    )

    db.add(payment)
    await db.commit()
    await db.refresh(payment)

    logger.info(
        "Создан MulenPay платеж %s на %.2f₽ для пользователя %s",
        mulen_payment_id,
        amount_kopeks / 100,
        user_id,
    )
    return payment



async def get_mulenpay_payment_by_id(
    db: AsyncSession,
    mulen_payment_id: int,
) -> Optional[MulenPayPayment]:
    result = await db.execute(
        select(MulenPayPayment)
        .options(selectinload(MulenPayPayment.user))
        .where(MulenPayPayment.mulen_payment_id == mulen_payment_id)
    )
    return result.scalar_one_or_none()


async def get_mulenpay_payment_by_uuid(
    db: AsyncSession,
    uuid: str,
) -> Optional[MulenPayPayment]:
    result = await db.execute(
        select(MulenPayPayment)
        .options(selectinload(MulenPayPayment.user))
        .where(MulenPayPayment.uuid == uuid)
    )
    return result.scalar_one_or_none()


async def get_mulenpay_payment_by_local_id(
    db: AsyncSession,
    local_id: int,
) -> Optional[MulenPayPayment]:
    result = await db.execute(
        select(MulenPayPayment)
        .options(selectinload(MulenPayPayment.user))
        .where(MulenPayPayment.id == local_id)
    )
    return result.scalar_one_or_none()


async def update_mulenpay_payment_status(
    db: AsyncSession,
    mulen_payment_id: int,
    status: str,
    *,
    is_paid: Optional[bool] = None,
    payment_data: Optional[dict] = None,
    callback_data: Optional[dict] = None,
    paid_at: Optional[datetime] = None,
) -> Optional[MulenPayPayment]:
    update_data = {
        "status": status,
        "updated_at": datetime.utcnow(),
    }

    if is_paid is not None:
        update_data["is_paid"] = is_paid
    if payment_data is not None:
        update_data["payment_data_json"] = payment_data
    if callback_data is not None:
        update_data["callback_data"] = callback_data
    if paid_at is not None:
        update_data["paid_at"] = paid_at

    await db.execute(
        update(MulenPayPayment)
        .where(MulenPayPayment.mulen_payment_id == mulen_payment_id)
        .values(**update_data)
    )
    await db.commit()

    result = await db.execute(
        select(MulenPayPayment)
        .options(selectinload(MulenPayPayment.user))
        .where(MulenPayPayment.mulen_payment_id == mulen_payment_id)
    )
    payment = result.scalar_one_or_none()

    if payment:
        logger.info(
            "Обновлен статус MulenPay платежа %s: %s",
            mulen_payment_id,
            status,
        )

    return payment


async def link_mulenpay_payment_to_transaction(
    db: AsyncSession,
    mulen_payment_id: int,
    transaction_id: int,
) -> Optional[MulenPayPayment]:
    await db.execute(
        update(MulenPayPayment)
        .where(MulenPayPayment.mulen_payment_id == mulen_payment_id)
        .values(transaction_id=transaction_id, updated_at=datetime.utcnow())
    )
    await db.commit()

    result = await db.execute(
        select(MulenPayPayment)
        .options(
            selectinload(MulenPayPayment.user),
            selectinload(MulenPayPayment.transaction),
        )
        .where(MulenPayPayment.mulen_payment_id == mulen_payment_id)
    )
    payment = result.scalar_one_or_none()

    if payment:
        logger.info(
            "MulenPay платеж %s связан с транзакцией %s",
            mulen_payment_id,
            transaction_id,
        )

    return payment
