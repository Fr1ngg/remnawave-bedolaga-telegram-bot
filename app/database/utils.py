"""Utility helpers for database maintenance tasks."""

from __future__ import annotations

import logging
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_TRANSACTION_COLUMNS_READY = False


async def ensure_transaction_columns(db: AsyncSession) -> None:
    """Ensure optional columns of the ``transactions`` table exist.

    Older installations might miss the ``status`` or ``currency`` columns.
    Some services (backup export, web API) rely on them, so we add them on
    the fly if the database schema is outdated.
    """

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

        logger.warning(
            "Неизвестный драйвер БД %s для проверки колонок transactions", dialect
        )
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
    except Exception as error:  # pragma: no cover - defensive
        await db.rollback()
        logger.error("Не удалось обновить таблицу transactions: %s", error)
        raise
    else:
        _TRANSACTION_COLUMNS_READY = True

