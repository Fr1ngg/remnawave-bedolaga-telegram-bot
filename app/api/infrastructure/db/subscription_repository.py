from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.core.base_repo import BaseRepo
from app.database.models import (
    Subscription as SubscriptionORM,
    SubscriptionStatus,
    User as UserORM,
)


class SubscriptionRepository(BaseRepo[SubscriptionORM]):
    def __init__(self, session: AsyncSession):
        super().__init__(model=SubscriptionORM, session=session)

    async def get_subscription_by_user_id(self, user_id: int) -> Optional[SubscriptionORM]:
        stmt = (
            select(self.model)
            .options(selectinload(self.model.user))
            .where(self.model.user_id == user_id)
        )
        result = await self.session.execute(stmt)
        subscription = result.scalars().one_or_none()

        if subscription is None:
            return None

        await self._sync_subscription_status(subscription)
        return subscription

    async def extend_subscription(self, subscription: SubscriptionORM, days: int) -> SubscriptionORM:
        current_time = datetime.utcnow()

        if subscription.end_date > current_time:
            subscription.end_date = subscription.end_date + timedelta(days=days)
        else:
            subscription.end_date = current_time + timedelta(days=days)

        if subscription.status == SubscriptionStatus.EXPIRED.value:
            subscription.status = SubscriptionStatus.ACTIVE.value

        subscription.updated_at = current_time

        await self.session.commit()
        await self.session.refresh(subscription)
        return subscription

    async def create_subscription_for_user(self, user: UserORM) -> SubscriptionORM:
        current_time = datetime.utcnow()
        subscription = SubscriptionORM(
            user_id=user.id,
            status=SubscriptionStatus.ACTIVE.value,
            is_trial=False,
            start_date=current_time,
            end_date=current_time,
            traffic_limit_gb=0,
            traffic_used_gb=0.0,
            device_limit=1,
            connected_squads=[],
        )
        self.session.add(subscription)
        await self.session.commit()
        await self.session.refresh(subscription)
        return subscription

    async def restore_subscription_state(
        self,
        subscription: SubscriptionORM,
        *,
        end_date: datetime,
        status: str,
        updated_at: datetime,
    ) -> SubscriptionORM:
        subscription.end_date = end_date
        subscription.status = status
        subscription.updated_at = updated_at
        await self.session.commit()
        await self.session.refresh(subscription)
        return subscription

    async def _sync_subscription_status(self, subscription: SubscriptionORM) -> None:
        current_time = datetime.utcnow()
        if (
            subscription.status == SubscriptionStatus.ACTIVE.value
            and subscription.end_date <= current_time
        ):
            subscription.status = SubscriptionStatus.EXPIRED.value
            subscription.updated_at = current_time
            await self.session.commit()
            await self.session.refresh(subscription)
