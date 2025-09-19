from sqlalchemy.ext.asyncio import AsyncSession

from app.api.infrastructure.db.user_repository import UserRepository
from app.api.infrastructure.db.subscription_repository import SubscriptionRepository


class HolderRepo:
    def __init__(self, session: AsyncSession):
        self.user_repository = UserRepository(session)
        self.subscription_repository = SubscriptionRepository(session)
