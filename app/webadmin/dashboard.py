"""Dashboard data helpers for the web admin API."""

from __future__ import annotations
"""Data aggregation helpers for the web admin dashboard."""

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import (
    ServerSquad,
    Subscription,
    SubscriptionConversion,
    SubscriptionStatus,
    Transaction,
    TransactionType,
    User,
)
from app.webadmin.serializers import (
    serialize_server,
    serialize_transaction,
    serialize_user,
)


def _calculate_growth(current: int, previous: int) -> float:
    if previous <= 0:
        return 100.0 if current > 0 else 0.0
    return round(((current - previous) / previous) * 100, 2)


async def collect_dashboard_summary(session: AsyncSession) -> Dict[str, Any]:
    """Collect key dashboard metrics."""

    now = datetime.utcnow()
    start_7 = now - timedelta(days=7)
    prev_7 = now - timedelta(days=14)
    start_30 = now - timedelta(days=30)
    start_60 = now - timedelta(days=60)

    total_users = await session.scalar(select(func.count(User.id))) or 0
    new_users_last_7 = (
        await session.scalar(select(func.count(User.id)).where(User.created_at >= start_7))
    ) or 0
    new_users_prev_7 = (
        await session.scalar(
            select(func.count(User.id)).where(
                and_(User.created_at >= prev_7, User.created_at < start_7)
            )
        )
    ) or 0

    active_statuses = [
        SubscriptionStatus.ACTIVE.value,
        SubscriptionStatus.TRIAL.value,
    ]
    active_subscriptions = (
        await session.scalar(
            select(func.count(Subscription.id)).where(
                Subscription.status.in_(active_statuses),
                Subscription.end_date > now,
            )
        )
    ) or 0
    active_trials = (
        await session.scalar(
            select(func.count(Subscription.id)).where(
                Subscription.status == SubscriptionStatus.TRIAL.value,
                Subscription.end_date > now,
            )
        )
    ) or 0
    active_paid = max(0, active_subscriptions - active_trials)

    revenue_types = [
        TransactionType.SUBSCRIPTION_PAYMENT.value,
        TransactionType.DEPOSIT.value,
    ]
    revenue_current = (
        await session.scalar(
            select(func.coalesce(func.sum(Transaction.amount_kopeks), 0)).where(
                Transaction.created_at >= start_30,
                Transaction.type.in_(revenue_types),
                Transaction.amount_kopeks > 0,
            )
        )
    ) or 0
    revenue_previous = (
        await session.scalar(
            select(func.coalesce(func.sum(Transaction.amount_kopeks), 0)).where(
                Transaction.created_at >= start_60,
                Transaction.created_at < start_30,
                Transaction.type.in_(revenue_types),
                Transaction.amount_kopeks > 0,
            )
        )
    ) or 0

    new_trials_last_30 = (
        await session.scalar(
            select(func.count(Subscription.id)).where(
                Subscription.is_trial.is_(True),
                Subscription.created_at >= start_30,
            )
        )
    ) or 0
    conversions_last_30 = (
        await session.scalar(
            select(func.count(SubscriptionConversion.id)).where(
                SubscriptionConversion.converted_at >= start_30
            )
        )
    ) or 0

    conversion_rate = (
        round((conversions_last_30 / new_trials_last_30) * 100, 2)
        if new_trials_last_30
        else 0.0
    )

    total_servers = await session.scalar(select(func.count(ServerSquad.id))) or 0
    available_servers = (
        await session.scalar(
            select(func.count(ServerSquad.id)).where(ServerSquad.is_available.is_(True))
        )
    ) or 0
    active_servers_percent = (
        round((available_servers / total_servers) * 100, 2)
        if total_servers
        else 0.0
    )

    return {
        "total_users": int(total_users),
        "new_users_last_7": int(new_users_last_7),
        "new_users_prev_7": int(new_users_prev_7),
        "user_growth_percent": _calculate_growth(new_users_last_7, new_users_prev_7),
        "active_subscriptions": int(active_subscriptions),
        "active_trials": int(active_trials),
        "active_paid": int(active_paid),
        "monthly_revenue_kopeks": int(revenue_current),
        "monthly_revenue": round(revenue_current / 100, 2),
        "monthly_revenue_change_percent": _calculate_growth(
            revenue_current, revenue_previous
        ),
        "total_servers": int(total_servers),
        "available_servers": int(available_servers),
        "active_servers_percent": active_servers_percent,
        "new_trials_last_30": int(new_trials_last_30),
        "conversions_last_30": int(conversions_last_30),
        "conversion_rate_percent": conversion_rate,
    }


async def collect_revenue_series(
    session: AsyncSession,
    *,
    days: int = 14,
) -> List[Dict[str, Any]]:
    """Collect revenue per day for the specified period."""

    days = max(1, min(days, 90))
    now = datetime.utcnow()
    start = (now - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    revenue_types = [
        TransactionType.SUBSCRIPTION_PAYMENT.value,
        TransactionType.DEPOSIT.value,
    ]

    rows = await session.execute(
        select(Transaction.created_at, Transaction.amount_kopeks)
        .where(
            Transaction.created_at >= start,
            Transaction.type.in_(revenue_types),
            Transaction.amount_kopeks > 0,
        )
        .order_by(Transaction.created_at.asc())
    )

    revenue_by_day: Dict[datetime, int] = defaultdict(int)
    for offset in range(days):
        revenue_by_day[(start + timedelta(days=offset)).date()] = 0

    for created_at, amount in rows:
        if not created_at:
            continue
        day = created_at.date()
        revenue_by_day[day] += amount or 0

    series = []
    for day, amount in sorted(revenue_by_day.items()):
        series.append(
            {
                "date": day.isoformat(),
                "revenue_kopeks": int(amount),
                "revenue": round(amount / 100, 2),
            }
        )

    return series


async def fetch_recent_users(
    session: AsyncSession,
    *,
    limit: int = 8,
) -> List[Dict[str, Any]]:
    result = await session.execute(
        select(User)
        .options(selectinload(User.subscription))
        .order_by(User.created_at.desc())
        .limit(limit)
    )
    users = result.scalars().all()
    return [serialize_user(user) for user in users]


async def list_users(
    session: AsyncSession,
    *,
    limit: int = 20,
    offset: int = 0,
    search: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    base_query = select(User).options(selectinload(User.subscription))
    count_query = select(func.count(User.id))

    if search:
        normalized = search.strip().lower()
        pattern = f"%{normalized}%"
        filters = [
            func.lower(User.username).like(pattern),
            func.lower(User.first_name).like(pattern),
            func.lower(User.last_name).like(pattern),
        ]
        numeric_filters = []
        if normalized.isdigit():
            numeric_value = int(normalized)
            numeric_filters.extend(
                [User.telegram_id == numeric_value, User.id == numeric_value]
            )

        conditions = filters + numeric_filters
        if conditions:
            base_query = base_query.where(or_(*conditions))
            count_query = count_query.where(or_(*conditions))

    base_query = base_query.order_by(User.created_at.desc()).offset(offset).limit(limit)

    result = await session.execute(base_query)
    users = result.scalars().all()

    total = await session.scalar(count_query) or 0
    return [serialize_user(user) for user in users], int(total)


async def list_transactions(
    session: AsyncSession,
    *,
    limit: int = 20,
    offset: int = 0,
    search: Optional[str] = None,
    types: Optional[List[str]] = None,
    payment_method: Optional[str] = None,
    is_completed: Optional[bool] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    base_query = (
        select(Transaction)
        .options(selectinload(Transaction.user))
        .join(User)
    )
    count_query = select(func.count(Transaction.id)).select_from(Transaction).join(User)

    if types:
        base_query = base_query.where(Transaction.type.in_(types))
        count_query = count_query.where(Transaction.type.in_(types))

    if payment_method:
        base_query = base_query.where(Transaction.payment_method == payment_method)
        count_query = count_query.where(Transaction.payment_method == payment_method)

    if is_completed is not None:
        condition = Transaction.is_completed.is_(True if is_completed else False)
        base_query = base_query.where(condition)
        count_query = count_query.where(condition)

    if search:
        normalized = search.strip()
        if normalized:
            pattern = f"%{normalized.lower()}%"
            text_filters = [
                func.lower(func.coalesce(User.username, "")).like(pattern),
                func.lower(func.coalesce(User.first_name, "")).like(pattern),
                func.lower(func.coalesce(User.last_name, "")).like(pattern),
                func.lower(func.coalesce(Transaction.description, "")).like(pattern),
                func.lower(func.coalesce(Transaction.payment_method, "")).like(pattern),
                func.lower(func.coalesce(Transaction.external_id, "")).like(pattern),
            ]
            numeric_filters: List[Any] = []
            if normalized.isdigit():
                numeric_value = int(normalized)
                numeric_filters.extend(
                    [
                        User.telegram_id == numeric_value,
                        User.id == numeric_value,
                        Transaction.id == numeric_value,
                    ]
                )

            conditions = text_filters + numeric_filters
            if conditions:
                search_condition = or_(*conditions)
                base_query = base_query.where(search_condition)
                count_query = count_query.where(search_condition)

    base_query = (
        base_query.order_by(Transaction.created_at.desc(), Transaction.id.desc())
        .offset(offset)
        .limit(limit)
    )

    result = await session.execute(base_query)
    transactions = result.scalars().all()

    total = await session.scalar(count_query) or 0

    return [serialize_transaction(tx) for tx in transactions], int(total)


async def get_user_details(
    session: AsyncSession,
    user_id: int,
) -> Optional[Dict[str, Any]]:
    result = await session.execute(
        select(User)
        .options(selectinload(User.subscription))
        .where(User.id == user_id)
        .limit(1)
    )
    user = result.scalar_one_or_none()
    if not user:
        return None

    transactions_result = await session.execute(
        select(Transaction)
        .where(Transaction.user_id == user_id)
        .order_by(Transaction.created_at.desc())
        .limit(25)
    )
    transactions = transactions_result.scalars().all()

    return {
        "user": serialize_user(user),
        "transactions": [serialize_transaction(tx) for tx in transactions],
    }


async def fetch_server_overview(session: AsyncSession) -> List[Dict[str, Any]]:
    result = await session.execute(
        select(ServerSquad).order_by(ServerSquad.sort_order.asc(), ServerSquad.display_name.asc())
    )
    servers = result.scalars().all()
    return [serialize_server(server) for server in servers]
