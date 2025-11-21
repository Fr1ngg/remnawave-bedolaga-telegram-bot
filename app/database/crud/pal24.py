"""CRUD helpers for PayPalych (Pal24) payments."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Pal24Payment

logger = logging.getLogger(__name__)


async def create_pal24_payment(
    db: AsyncSession,
    *,
    user_id: int,
    bill_id: str,
    amount_kopeks: int,
    description: Optional[str],
    status: str,
    type_: str,
    currency: str,
    link_url: Optional[str],
    link_page_url: Optional[str],
    order_id: Optional[str] = None,
    ttl: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Pal24Payment:
    payment = Pal24Payment(
        user_id=user_id,
        bill_id=bill_id,
        order_id=order_id,
        amount_kopeks=amount_kopeks,
        currency=currency,
        description=description,
        status=status,
        type=type_,
        link_url=link_url,
        link_page_url=link_page_url,
        metadata_json=metadata or {},
        ttl=ttl,
    )

    db.add(payment)
    await db.commit()
    await db.refresh(payment)

    logger.info(
        "Создан Pal24 платеж #%s для пользователя %s: %s копеек (статус %s)",
        payment.id,
        user_id,
        amount_kopeks,
        status,
    )

    return payment


async def get_pal24_payment_by_id(db: AsyncSession, payment_id: int) -> Optional[Pal24Payment]:
    result = await db.execute(
        select(Pal24Payment).where(Pal24Payment.id == payment_id)
    )
    return result.scalar_one_or_none()


async def get_pal24_payment_by_bill_id(db: AsyncSession, bill_id: str) -> Optional[Pal24Payment]:
    result = await db.execute(
        select(Pal24Payment).where(Pal24Payment.bill_id == bill_id)
    )
    return result.scalar_one_or_none()


async def get_pal24_payment_by_order_id(db: AsyncSession, order_id: str) -> Optional[Pal24Payment]:
    result = await db.execute(
        select(Pal24Payment).where(Pal24Payment.order_id == order_id)
    )
    return result.scalar_one_or_none()


async def update_pal24_payment_status(
    db: AsyncSession,
    payment: Pal24Payment,
    *,
    status: str,
    is_active: Optional[bool] = None,
    is_paid: Optional[bool] = None,
    paid_at: Optional[datetime] = None,
    payment_id: Optional[str] = None,
    payment_status: Optional[str] = None,
    payment_method: Optional[str] = None,
    balance_amount: Optional[str] = None,
    balance_currency: Optional[str] = None,
    payer_account: Optional[str] = None,
    callback_payload: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Pal24Payment:
    update_values: Dict[str, Any] = {
        "status": status,
    }

    if is_active is not None:
        update_values["is_active"] = is_active
    if is_paid is not None:
        update_values["is_paid"] = is_paid
    if paid_at is not None:
        update_values["paid_at"] = paid_at
    if payment_id is not None:
        update_values["payment_id"] = payment_id
    if payment_status is not None:
        update_values["payment_status"] = payment_status
    if payment_method is not None:
        update_values["payment_method"] = payment_method
    if balance_amount is not None:
        update_values["balance_amount"] = balance_amount
    if balance_currency is not None:
        update_values["balance_currency"] = balance_currency
    if payer_account is not None:
        update_values["payer_account"] = payer_account
    if callback_payload is not None:
        update_values["callback_payload"] = callback_payload
    if metadata is not None:
        existing = getattr(payment, "metadata_json", {}) or {}
        if not isinstance(existing, dict):
            existing = {}
        existing.update(metadata)
        update_values["metadata_json"] = existing

    update_values["last_status"] = status

    await db.execute(
        update(Pal24Payment)
        .where(Pal24Payment.id == payment.id)
        .values(**update_values)
    )

    await db.commit()
    await db.refresh(payment)

    logger.info(
        "Обновлен Pal24 платеж %s: статус=%s, is_paid=%s",
        payment.bill_id,
        payment.status,
        payment.is_paid,
    )

    return payment


async def link_pal24_payment_to_transaction(
    db: AsyncSession,
    payment: Pal24Payment,
    transaction_id: int,
) -> Pal24Payment:
    await db.execute(
        update(Pal24Payment)
        .where(Pal24Payment.id == payment.id)
        .values(transaction_id=transaction_id)
    )
    await db.commit()
    await db.refresh(payment)
    logger.info(
        "Pal24 платеж %s привязан к транзакции %s",
        payment.bill_id,
        transaction_id,
    )
    return payment

