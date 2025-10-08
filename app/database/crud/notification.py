import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.database.models import SentNotification

logger = logging.getLogger(__name__)


async def notification_sent(
    db: AsyncSession,
    user_id: int,
    subscription_id: int,
    notification_type: str,
    days_before: Optional[int] = None,
) -> bool:
    result = await db.execute(
        select(SentNotification).where(
            SentNotification.user_id == user_id,
            SentNotification.subscription_id == subscription_id,
            SentNotification.notification_type == notification_type,
            SentNotification.days_before == days_before,
        )
    )
    return result.scalar_one_or_none() is not None


async def record_notification(
    db: AsyncSession,
    user_id: int,
    subscription_id: int,
    notification_type: str,
    days_before: Optional[int] = None,
) -> None:
    notification = SentNotification(
        user_id=user_id,
        subscription_id=subscription_id,
        notification_type=notification_type,
        days_before=days_before,
    )
    db.add(notification)
    await db.commit()


async def clear_notifications(db: AsyncSession, subscription_id: int) -> None:
    await db.execute(
        delete(SentNotification).where(
            SentNotification.subscription_id == subscription_id
        )
    )
    await db.commit()


async def clear_notification_by_type(
    db: AsyncSession,
    subscription_id: int,
    notification_type: str,
) -> None:
    await db.execute(
        delete(SentNotification).where(
            SentNotification.subscription_id == subscription_id,
            SentNotification.notification_type == notification_type,
        )
    )
    await db.commit()
