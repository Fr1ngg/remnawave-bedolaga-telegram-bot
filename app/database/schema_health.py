"""Utilities for verifying and bootstrapping the database schema."""

from __future__ import annotations

import logging
from typing import Iterable, Tuple

from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

from app.database.database import engine
from app.database.models import Base

logger = logging.getLogger(__name__)

# A minimal set of tables that must exist for the bot to operate.
CRITICAL_SCHEMA_TABLES: Tuple[str, ...] = (
    "system_settings",
    "users",
    "subscriptions",
    "discount_offers",
    "monitoring_logs",
)


async def _fetch_existing_tables() -> list[str]:
    async with engine.begin() as conn:
        table_names: list[str] = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_table_names()
        )
    return table_names


async def ensure_tables_exist(table_names: Iterable[str]) -> list[str]:
    """Create the specified tables if they are known to the metadata."""

    metadata_tables = [
        Base.metadata.tables.get(name)
        for name in dict.fromkeys(table_names)
        if Base.metadata.tables.get(name) is not None
    ]

    if not metadata_tables:
        return []

    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn, tables=metadata_tables: Base.metadata.create_all(
                bind=sync_conn,
                tables=tables,
                checkfirst=True,
            )
        )

    return [table.name for table in metadata_tables]


async def check_database_schema_readiness(
    required_tables: Iterable[str] | None = None,
) -> tuple[bool, list[str], list[str]]:
    """Return schema readiness status, missing tables, and existing tables."""

    tables_to_check = tuple(required_tables or CRITICAL_SCHEMA_TABLES)

    try:
        existing_tables = await _fetch_existing_tables()
    except SQLAlchemyError as error:
        logger.error("Не удалось проверить схему базы данных: %s", error)
        return False, list(tables_to_check), []
    except Exception as error:  # pragma: no cover - диагностический блок
        logger.error("Неожиданная ошибка проверки схемы БД: %s", error)
        return False, list(tables_to_check), []

    missing_tables = [
        table_name for table_name in tables_to_check if table_name not in existing_tables
    ]

    if missing_tables:
        logger.warning(
            "Отсутствуют критические таблицы: %s", ", ".join(sorted(missing_tables))
        )
        return False, missing_tables, existing_tables

    return True, [], existing_tables


__all__ = [
    "check_database_schema_readiness",
    "ensure_tables_exist",
    "CRITICAL_SCHEMA_TABLES",
]

