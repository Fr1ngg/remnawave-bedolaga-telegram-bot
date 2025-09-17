from sqlalchemy.ext.asyncio import AsyncSession

from app.api.infrastructure.db.user_repository import UserRepository


class HolderRepo:
    def __init__(self, session: AsyncSession):
        self.user_repository = UserRepository(session)