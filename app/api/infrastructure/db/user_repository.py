from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.core.base_repo import BaseRepo
from app.api.domain.schemas.base import Page
from app.database.models import User as UserORM


class UserRepository(BaseRepo[UserORM]):
    def __init__(self, session: AsyncSession):
        super().__init__(model=UserORM, session=session)

    async def get_user_by_id(self, user_id: int) -> Optional[UserORM]:
        smtp = select(self.model).where(self.model.id == user_id)
        data = await self.session.execute(smtp)
        return data.scalars().one_or_none()

    async def get_user_by_telegram_id(self, telegram_id: int) -> Optional[UserORM]:
        stmt = select(self.model).where(self.model.telegram_id == telegram_id)
        result = await self.session.execute(stmt)
        return result.scalars().one_or_none()

    async def get_paginated_list(self, page: int, size: int) -> Page[UserORM]:
        stmt = select(self.model).offset((page - 1) * size).limit(size)
        result = await self.session.execute(stmt)
        users = result.scalars().all()

        total_stmt = select(func.count()).select_from(self.model)
        total = await self.session.scalar(total_stmt)
        return Page[UserORM](
            total=total,
            page=page,
            size=size,
            items=users
        )

    async def get_users_by_username_prefix(self, prefix: str) -> list[UserORM]:
        stmt = select(self.model).where(self.model.username.ilike(f"{prefix}%"))
        result = await self.session.execute(stmt)
        return result.scalars().all()
