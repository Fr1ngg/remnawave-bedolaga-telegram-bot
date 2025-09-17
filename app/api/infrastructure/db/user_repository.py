from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.core.base_repo import BaseRepo
from app.database.models import User as UserORM


class UserRepository(BaseRepo[UserORM]):
    def __init__(self, session: AsyncSession):
        super().__init__(model=UserORM, session=session)

    async def get_user_by_id(self, user_id: int) -> Optional[UserORM]:
        smtp = select(self.model).where(self.model.id == user_id)
        data = await self.session.execute(smtp)
        return data.scalars().one_or_none()