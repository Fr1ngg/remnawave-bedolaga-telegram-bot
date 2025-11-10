"""Helpers for validating the database schema before starting services."""

from __future__ import annotations

import logging
from typing import Iterable, Sequence, Tuple

from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

from app.database.database import engine

logger = logging.getLogger(__name__)

# A minimal set of tables that must exist for the bot to operate.
CRITICAL_SCHEMA_TABLES: Tuple[str, ...] = (
    "alembic_version",
    "system_settings",
    "users",
    "subscriptions",
)


async def _collect_missing_tables(required_tables: Sequence[str]) -> list[str]:
    async with engine.begin() as conn:
        missing_tables: list[str] = await conn.run_sync(
            lambda sync_conn: [
                table
                for table in required_tables
                if not inspect(sync_conn).has_table(table)
            ]
        )
    return missing_tables


async def database_has_tables() -> bool:
    """Return ``True`` when the database already contains any tables."""

    async with engine.begin() as conn:
        table_names: list[str] = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_table_names()
        )
    return bool(table_names)


async def check_database_schema_readiness(
    required_tables: Iterable[str] | None = None,
) -> tuple[bool, list[str]]:
    """Return schema readiness status and the list of missing tables."""

    tables_to_check = tuple(required_tables or CRITICAL_SCHEMA_TABLES)

    try:
        missing_tables = await _collect_missing_tables(tables_to_check)
    except SQLAlchemyError as error:
        logger.error("Не удалось проверить схему базы данных: %s", error)
        return False, list(tables_to_check)
    except Exception as error:  # pragma: no cover - диагностический блок
        logger.error("Неожиданная ошибка проверки схемы БД: %s", error)
        return False, list(tables_to_check)

    if missing_tables:
        logger.warning(
            "Отсутствуют критические таблицы: %s", ", ".join(sorted(missing_tables))
        )
        return False, missing_tables

    return True, []


__all__ = [
    "check_database_schema_readiness",
    "CRITICAL_SCHEMA_TABLES",
    "database_has_tables",
]

