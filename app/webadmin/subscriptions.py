"""Expose subscription management helpers for the web admin."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import case, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.database.models import Subscription, SubscriptionStatus, SubscriptionConversion, User
from app.webadmin.serializers import serialize_subscription_with_user


async def fetch_subscriptions_overview(session: AsyncSession) -> Dict[str, Any]:
    """Return aggregated counters that mirror the Telegram admin overview."""

    now = datetime.utcnow()
    in_seven_days = now + timedelta(days=7)

    total = await session.scalar(select(func.count(Subscription.id))) or 0
    active = await session.scalar(
        select(func.count(Subscription.id)).where(Subscription.status == SubscriptionStatus.ACTIVE.value)
    ) or 0
    trials = await session.scalar(
        select(func.count(Subscription.id)).where(Subscription.is_trial.is_(True))
    ) or 0
    expired = await session.scalar(
        select(func.count(Subscription.id)).where(Subscription.status == SubscriptionStatus.EXPIRED.value)
    ) or 0

    expiring = await session.scalar(
        select(func.count(Subscription.id)).where(
            Subscription.status == SubscriptionStatus.ACTIVE.value,
            Subscription.end_date.between(now, in_seven_days),
        )
    ) or 0

    conversions_last_30 = await session.scalar(
        select(func.count(SubscriptionConversion.id)).where(
            SubscriptionConversion.converted_at >= now - timedelta(days=30)
        )
    ) or 0

    return {
        "total": int(total),
        "active": int(active),
        "trials": int(trials),
        "expired": int(expired),
        "expiring_soon": int(expiring),
        "conversions_last_30": int(conversions_last_30),
    }


async def fetch_subscriptions(
    session: AsyncSession,
    *,
    page: int = 1,
    limit: int = 20,
    status: Optional[str] = None,
    search: Optional[str] = None,
    sort: str = "end_date",
    expiring_before: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Return a paginated list of subscriptions with optional filters."""

    page = max(page, 1)
    limit = max(1, min(limit, 100))
    offset = (page - 1) * limit

    query = select(Subscription).options(joinedload(Subscription.user))
    count_query = select(func.count(Subscription.id))

    filters = []
    if status:
        filters.append(Subscription.status == status)

    if expiring_before is not None:
        filters.append(Subscription.end_date <= expiring_before)

    if search:
        search = search.strip()
        search_expr = f"%{search}%"
        name_filters = [
            User.first_name.ilike(search_expr),
            User.last_name.ilike(search_expr),
            User.username.ilike(search_expr),
        ]
        if search.isdigit():
            name_filters.append(User.telegram_id == int(search))
        filters.append(Subscription.user.has(or_(*name_filters)))

    for condition in filters:
        query = query.where(condition)
        count_query = count_query.where(condition)

    if sort == "status":
        status_case = case(
            (
                Subscription.status == SubscriptionStatus.ACTIVE.value,
                0,
            ),
            (
                Subscription.status == SubscriptionStatus.TRIAL.value,
                1,
            ),
            else_=2,
        )
        query = query.order_by(status_case, desc(Subscription.end_date))
    else:
        query = query.order_by(desc(Subscription.end_date))

    total = await session.scalar(count_query) or 0

    rows = await session.execute(query.offset(offset).limit(limit))
    subscriptions = [serialize_subscription_with_user(sub) for sub in rows.scalars().all()]

    return {
        "items": subscriptions,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": int(total or 0),
        },
    }


async def fetch_expiring_subscriptions(session: AsyncSession, *, days: int = 7) -> Dict[str, Any]:
    """Return subscriptions that will expire soon."""

    now = datetime.utcnow()
    end = now + timedelta(days=max(1, days))

    rows = await session.execute(
        select(Subscription)
        .options(joinedload(Subscription.user))
        .where(
            Subscription.status == SubscriptionStatus.ACTIVE.value,
            Subscription.end_date.between(now, end),
        )
        .order_by(Subscription.end_date.asc())
    )

    return {
        "items": [serialize_subscription_with_user(sub) for sub in rows.scalars().all()],
        "period": {
            "from": now.isoformat(),
            "to": end.isoformat(),
        },
    }
