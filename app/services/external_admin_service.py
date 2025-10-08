"""Утилиты для синхронизации токена внешней админки."""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.database.database import AsyncSessionLocal
from app.database.models import SystemSetting
from app.services.system_settings_service import (
    ReadOnlySettingError,
    bot_configuration_service,
)


logger = logging.getLogger(__name__)


async def ensure_external_admin_token(
    bot_username: Optional[str],
    bot_id: Optional[int],
) -> Optional[str]:
    """Генерирует и сохраняет токен внешней админки, если требуется."""

    username_raw = (bot_username or "").strip()
    if not username_raw:
        logger.warning(
            "⚠️ Не удалось обеспечить токен внешней админки: username бота отсутствует",
        )
        return None

    normalized_username = username_raw.lstrip("@").lower()
    if not normalized_username:
        logger.warning(
            "⚠️ Не удалось обеспечить токен внешней админки: username пустой после нормализации",
        )
        return None

    try:
        token = settings.build_external_admin_token(normalized_username)
    except Exception as error:  # pragma: no cover - защитный блок
        logger.error("❌ Ошибка генерации токена внешней админки: %s", error)
        return None

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(SystemSetting.key, SystemSetting.value).where(
                    SystemSetting.key.in_(
                        ["EXTERNAL_ADMIN_TOKEN", "EXTERNAL_ADMIN_TOKEN_BOT_ID"]
                    )
                )
            )
            rows = dict(result.all())
            existing_token = rows.get("EXTERNAL_ADMIN_TOKEN")
            existing_bot_id_raw = rows.get("EXTERNAL_ADMIN_TOKEN_BOT_ID")

            existing_bot_id: Optional[int] = None
            if existing_bot_id_raw is not None:
                try:
                    existing_bot_id = int(existing_bot_id_raw)
                except (TypeError, ValueError):  # pragma: no cover - защита от мусорных значений
                    logger.warning(
                        "⚠️ Не удалось разобрать сохраненный идентификатор бота внешней админки: %s",
                        existing_bot_id_raw,
                    )

            if existing_token == token and existing_bot_id == bot_id:
                if settings.get_external_admin_token() != token:
                    settings.EXTERNAL_ADMIN_TOKEN = token
                if settings.EXTERNAL_ADMIN_TOKEN_BOT_ID != existing_bot_id:
                    settings.EXTERNAL_ADMIN_TOKEN_BOT_ID = existing_bot_id
                return token

            if existing_bot_id is not None and bot_id is not None and existing_bot_id != bot_id:
                logger.error(
                    "❌ Обнаружено несовпадение ID бота для токена внешней админки: сохранен %s, текущий %s",
                    existing_bot_id,
                    bot_id,
                )

                try:
                    await bot_configuration_service.reset_value(
                        session,
                        "EXTERNAL_ADMIN_TOKEN",
                        force=True,
                    )
                    await bot_configuration_service.reset_value(
                        session,
                        "EXTERNAL_ADMIN_TOKEN_BOT_ID",
                        force=True,
                    )
                    await session.commit()
                    logger.warning(
                        "⚠️ Токен внешней админки очищен из-за несовпадения идентификаторов бота",
                    )
                except Exception as cleanup_error:  # pragma: no cover - защитный блок
                    await session.rollback()
                    logger.error(
                        "❌ Не удалось очистить токен внешней админки после обнаружения подмены: %s",
                        cleanup_error,
                    )
                finally:
                    settings.EXTERNAL_ADMIN_TOKEN = None
                    settings.EXTERNAL_ADMIN_TOKEN_BOT_ID = None

                return None

            updates: list[tuple[str, object]] = []
            if existing_token != token:
                updates.append(("EXTERNAL_ADMIN_TOKEN", token))

            if bot_id is not None and existing_bot_id != bot_id:
                updates.append(("EXTERNAL_ADMIN_TOKEN_BOT_ID", bot_id))

            if not updates:
                # Токен совпал, но могли отсутствовать значения в настройках приложения
                if settings.get_external_admin_token() != (existing_token or token):
                    settings.EXTERNAL_ADMIN_TOKEN = existing_token or token
                if existing_bot_id is not None and (
                    settings.EXTERNAL_ADMIN_TOKEN_BOT_ID != existing_bot_id
                ):
                    settings.EXTERNAL_ADMIN_TOKEN_BOT_ID = existing_bot_id
                elif (
                    bot_id is not None
                    and settings.EXTERNAL_ADMIN_TOKEN_BOT_ID != bot_id
                    and existing_bot_id is None
                ):
                    settings.EXTERNAL_ADMIN_TOKEN_BOT_ID = bot_id
                return existing_token or token

            try:
                for key, value in updates:
                    await bot_configuration_service.set_value(
                        session,
                        key,
                        value,
                        force=True,
                    )
                await session.commit()
                logger.info(
                    "✅ Токен внешней админки синхронизирован для @%s",
                    normalized_username,
                )
            except ReadOnlySettingError:  # pragma: no cover - force=True предотвращает исключение
                await session.rollback()
                logger.warning(
                    "⚠️ Не удалось сохранить токен внешней админки из-за ограничения доступа",
                )
                return None

            return token
    except SQLAlchemyError as error:
        logger.error("❌ Ошибка сохранения токена внешней админки: %s", error)
        return None

