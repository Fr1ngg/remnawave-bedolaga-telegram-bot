from __future__ import annotations

from dataclasses import dataclass
from typing import List

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.external_admin_api_key import (
    create_api_key,
    delete_api_key,
    get_api_key_by_creator_and_target,
    get_api_key_by_target,
    get_api_key_by_id,
    list_api_keys_for_creator,
)
from app.database.models import ExternalAdminApiKey


class ExternalAdminTokenMissingError(RuntimeError):
    """Raised when external admin token is not configured."""


class ExternalAdminApiKeyConflictError(RuntimeError):
    """Raised when an API key for a target is already owned by another admin."""

    def __init__(self, *, target_telegram_id: int) -> None:
        self.target_telegram_id = target_telegram_id
        super().__init__(
            f"API key for target {target_telegram_id} already exists",
        )


@dataclass(slots=True)
class ApiKeyResult:
    api_key: ExternalAdminApiKey
    created: bool


class ExternalAdminApiKeysService:
    """Service for managing external admin API keys."""

    @staticmethod
    def _ensure_token() -> str:
        token = settings.get_external_admin_token()
        if not token:
            raise ExternalAdminTokenMissingError(
                "External admin token is not configured",
            )
        return token

    @classmethod
    def build_api_key_value(cls, target_telegram_id: int) -> str:
        cls._ensure_token()
        return settings.build_external_admin_api_key(target_telegram_id)

    async def list_for_creator(
        self,
        db: AsyncSession,
        creator_user_id: int,
    ) -> List[ExternalAdminApiKey]:
        self._ensure_token()
        return await list_api_keys_for_creator(db, creator_user_id)

    async def ensure_key(
        self,
        db: AsyncSession,
        *,
        creator_user_id: int,
        target_telegram_id: int,
    ) -> ApiKeyResult:
        self._ensure_token()
        existing = await get_api_key_by_creator_and_target(
            db,
            creator_user_id=creator_user_id,
            target_telegram_id=target_telegram_id,
        )
        if existing:
            return ApiKeyResult(api_key=existing, created=False)

        occupied = await get_api_key_by_target(
            db,
            target_telegram_id=target_telegram_id,
        )
        if occupied and occupied.creator_user_id != creator_user_id:
            raise ExternalAdminApiKeyConflictError(
                target_telegram_id=target_telegram_id,
            )

        api_key = await create_api_key(
            db,
            creator_user_id=creator_user_id,
            target_telegram_id=target_telegram_id,
        )
        return ApiKeyResult(api_key=api_key, created=True)

    async def delete_key(
        self,
        db: AsyncSession,
        *,
        key_id: int,
        creator_user_id: int,
    ) -> bool:
        api_key = await get_api_key_by_id(db, key_id)
        if not api_key or api_key.creator_user_id != creator_user_id:
            return False

        await delete_api_key(db, api_key)
        return True


__all__ = [
    "ExternalAdminApiKeysService",
    "ExternalAdminTokenMissingError",
    "ExternalAdminApiKeyConflictError",
    "ApiKeyResult",
]
