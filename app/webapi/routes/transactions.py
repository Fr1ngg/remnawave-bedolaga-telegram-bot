from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import logging

from fastapi import APIRouter, Depends, Query, Security
from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Transaction

from ..dependencies import get_db_session, require_api_token
from ..schemas.transactions import TransactionListResponse, TransactionResponse

router = APIRouter()

logger = logging.getLogger(__name__)

_TRANSACTION_COLUMNS_READY = False


async def _ensure_transaction_columns(db: AsyncSession) -> None:
    global _TRANSACTION_COLUMNS_READY
    if _TRANSACTION_COLUMNS_READY:
        return

    conn = await db.connection()
    dialect = conn.dialect.name

    async def column_exists(column: str) -> bool:
        if dialect == "postgresql":
            result = await conn.scalar(
                text(
                    """
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'transactions' AND column_name = :column
                    LIMIT 1
                    """
                ),
                {"column": column},
            )
            return result is not None
        if dialect == "sqlite":
            result = await conn.execute(text("PRAGMA table_info('transactions')"))
            return any(row[1] == column for row in result)
        if dialect == "mysql":
            result = await conn.scalar(
                text(
                    """
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = DATABASE()
                      AND table_name = 'transactions'
                      AND column_name = :column
                    LIMIT 1
                    """
                ),
                {"column": column},
            )
            return result is not None

        logger.warning("Неизвестный драйвер БД %s для проверки колонок transactions", dialect)
        return True

    added = False

    async def ensure_column(column: str, ddl_type: str) -> None:
        nonlocal added
        if await column_exists(column):
            return
        logger.info("Добавляем колонку transactions.%s", column)
        await conn.execute(text(f"ALTER TABLE transactions ADD COLUMN {column} {ddl_type}"))
        added = True

    try:
        await ensure_column("status", "VARCHAR(50)")
        await ensure_column("currency", "VARCHAR(10)")
        if added:
            await db.commit()
    except Exception as error:
        await db.rollback()
        logger.error("Не удалось обновить таблицу transactions: %s", error)
        raise
    else:
        _TRANSACTION_COLUMNS_READY = True


def _serialize(transaction: Transaction) -> TransactionResponse:
    return TransactionResponse(
        id=transaction.id,
        user_id=transaction.user_id,
        type=transaction.type,
        amount_kopeks=transaction.amount_kopeks,
        amount_rubles=round(transaction.amount_kopeks / 100, 2),
        description=transaction.description,
        payment_method=transaction.payment_method,
        status=transaction.status,
        currency=transaction.currency,
        external_id=transaction.external_id,
        is_completed=transaction.is_completed,
        created_at=transaction.created_at,
        completed_at=transaction.completed_at,
    )


@router.get("", response_model=TransactionListResponse)
async def list_transactions(
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user_id: Optional[int] = Query(default=None),
    type_filter: Optional[str] = Query(default=None, alias="type"),
    payment_method: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    is_completed: Optional[bool] = Query(default=None),
    date_from: Optional[datetime] = Query(default=None),
    date_to: Optional[datetime] = Query(default=None),
    amount_min: Optional[int] = Query(default=None),
    amount_max: Optional[int] = Query(default=None),
    currency: Optional[str] = Query(default=None),
) -> TransactionListResponse:
    await _ensure_transaction_columns(db)

    base_query = select(Transaction)
    conditions = []

    if user_id:
        conditions.append(Transaction.user_id == user_id)
    if type_filter:
        conditions.append(Transaction.type == type_filter)
    if payment_method:
        conditions.append(Transaction.payment_method == payment_method)
    if status:
        conditions.append(Transaction.status == status)
    if is_completed is not None:
        conditions.append(Transaction.is_completed.is_(is_completed))
    if date_from:
        conditions.append(Transaction.created_at >= date_from)
    if date_to:
        conditions.append(Transaction.created_at <= date_to)
    safe_min = int(amount_min) if amount_min is not None else None
    safe_max = int(amount_max) if amount_max is not None else None
    if safe_min is not None and safe_max is not None and safe_max < safe_min:
        safe_max = safe_min
    if safe_min is not None:
        conditions.append(Transaction.amount_kopeks >= safe_min)
    if safe_max is not None:
        conditions.append(Transaction.amount_kopeks <= safe_max)
    if currency:
        conditions.append(Transaction.currency == currency)

    if conditions:
        base_query = base_query.where(and_(*conditions))

    total_query = base_query.with_only_columns(func.count()).order_by(None)
    total = await db.scalar(total_query) or 0

    result = await db.execute(
        base_query.order_by(Transaction.created_at.desc()).offset(offset).limit(limit)
    )
    transactions = result.scalars().all()

    return TransactionListResponse(
        items=[_serialize(tx) for tx in transactions],
        total=int(total),
        limit=limit,
        offset=offset,
    )
