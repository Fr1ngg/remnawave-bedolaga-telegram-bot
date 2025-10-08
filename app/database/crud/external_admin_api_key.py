from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import ExternalAdminApiKey


async def list_api_keys_for_creator(
    db: AsyncSession,
    creator_user_id: int,
) -> List[ExternalAdminApiKey]:
    query = (
        select(ExternalAdminApiKey)
        .where(ExternalAdminApiKey.creator_user_id == creator_user_id)
        .order_by(ExternalAdminApiKey.created_at.desc())
    )
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_api_key_by_creator_and_target(
    db: AsyncSession,
    *,
    creator_user_id: int,
    target_telegram_id: int,
) -> Optional[ExternalAdminApiKey]:
    query = select(ExternalAdminApiKey).where(
        ExternalAdminApiKey.creator_user_id == creator_user_id,
        ExternalAdminApiKey.target_telegram_id == target_telegram_id,
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_api_key_by_target(
    db: AsyncSession,
    *,
    target_telegram_id: int,
) -> Optional[ExternalAdminApiKey]:
    query = select(ExternalAdminApiKey).where(
        ExternalAdminApiKey.target_telegram_id == target_telegram_id,
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_api_key_by_id(
    db: AsyncSession,
    key_id: int,
) -> Optional[ExternalAdminApiKey]:
    return await db.get(ExternalAdminApiKey, key_id)


async def create_api_key(
    db: AsyncSession,
    *,
    creator_user_id: int,
    target_telegram_id: int,
) -> ExternalAdminApiKey:
    api_key = ExternalAdminApiKey(
        creator_user_id=creator_user_id,
        target_telegram_id=target_telegram_id,
    )
    db.add(api_key)
    await db.flush()
    await db.refresh(api_key)
    return api_key


async def delete_api_key(
    db: AsyncSession,
    api_key: ExternalAdminApiKey,
) -> None:
    await db.delete(api_key)


__all__ = [
    "list_api_keys_for_creator",
    "get_api_key_by_creator_and_target",
    "get_api_key_by_target",
    "get_api_key_by_id",
    "create_api_key",
    "delete_api_key",
]
