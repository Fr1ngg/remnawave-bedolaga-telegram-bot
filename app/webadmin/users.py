"""Helpers that expose the admin user management functionality to the web UI."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.database.crud.transaction import get_user_transactions
from app.database.models import Subscription, SubscriptionStatus, Transaction, User
from app.services.user_service import UserService
from app.webadmin.serializers import serialize_transaction, serialize_user


def _base_user_query() -> select:
    """Return a base query that preloads the most frequently used relationships."""

    return (
        select(User)
        .options(
            joinedload(User.subscription),
            joinedload(User.promo_group),
        )
    )


async def fetch_users_overview(session: AsyncSession) -> Dict[str, Any]:
    """Return aggregated statistics that back the admin "Users" dashboard section."""

    service = UserService()
    stats = await service.get_user_statistics(session)

    trial_count_result = await session.execute(
        select(func.count(Subscription.id)).where(Subscription.is_trial.is_(True))
    )
    trial_count = int(trial_count_result.scalar() or 0)

    active_paid_result = await session.execute(
        select(func.count(Subscription.id)).where(
            Subscription.status == SubscriptionStatus.ACTIVE.value,
            Subscription.is_trial.is_(False),
        )
    )
    active_paid = int(active_paid_result.scalar() or 0)

    week_ago = datetime.utcnow() - timedelta(days=7)
    recent_result = await session.execute(
        select(func.count(User.id)).where(User.created_at >= week_ago)
    )
    recent_total = int(recent_result.scalar() or 0)

    return {
        "totals": {
            "all": int(stats.get("total_users", 0)),
            "active": int(stats.get("active_users", 0)),
            "blocked": int(stats.get("blocked_users", 0)),
            "deleted": int(stats.get("deleted_users", 0)),
        },
        "growth": {
            "new_today": int(stats.get("new_today", 0)),
            "new_week": int(stats.get("new_week", 0)),
            "new_month": int(stats.get("new_month", 0)),
        },
        "subscriptions": {
            "trials": trial_count,
            "active_paid": active_paid,
        },
        "recent_registrations": recent_total,
    }


async def fetch_users_page(
    session: AsyncSession,
    *,
    page: int = 1,
    limit: int = 20,
    search: Optional[str] = None,
    status: Optional[str] = None,
    order: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a paginated listing of users."""

    page = max(page, 1)
    limit = max(1, min(limit, 100))
    offset = (page - 1) * limit

    query = _base_user_query().order_by(desc(User.created_at))

    if order == "balance":
        query = query.order_by(desc(User.balance_kopeks))
    elif order == "activity":
        query = query.order_by(User.last_activity.desc().nullslast())

    if search:
        search = search.strip()
        search_expr = f"%{search}%"
        filters = [
            User.first_name.ilike(search_expr),
            User.last_name.ilike(search_expr),
            User.username.ilike(search_expr),
        ]
        if search.isdigit():
            filters.append(User.telegram_id == int(search))
        query = query.where(or_(*filters))

    if status:
        query = query.where(User.status == status)

    total_query = select(func.count(User.id))
    if status:
        total_query = total_query.where(User.status == status)
    if search:
        search_expr = f"%{search}%"
        conditions = [
            User.first_name.ilike(search_expr),
            User.last_name.ilike(search_expr),
            User.username.ilike(search_expr),
        ]
        if search.isdigit():
            conditions.append(User.telegram_id == int(search))
        total_query = total_query.where(or_(*conditions))

    total_result = await session.execute(total_query)
    total = int(total_result.scalar() or 0)

    rows = await session.execute(query.offset(offset).limit(limit))
    users = [serialize_user(user) for user in rows.scalars().all()]

    return {
        "items": users,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
        },
    }


async def fetch_user_details(session: AsyncSession, user_id: int) -> Dict[str, Any]:
    """Return extended information for a single user."""

    service = UserService()
    profile = await service.get_user_profile(session, user_id)
    if not profile:
        return {}

    user = profile["user"]
    serialized_user = serialize_user(user)

    transactions: list[Transaction] = await get_user_transactions(session, user_id, limit=20)
    serialized_transactions = [serialize_transaction(tx) for tx in transactions]

    return {
        "user": serialized_user,
        "subscription": serialized_user.get("subscription"),
        "transactions": serialized_transactions,
        "meta": {
            "is_admin": bool(profile.get("is_admin")),
            "registration_days": int(profile.get("registration_days", 0)),
            "transactions_count": int(profile.get("transactions_count", len(serialized_transactions))),
        },
    }

