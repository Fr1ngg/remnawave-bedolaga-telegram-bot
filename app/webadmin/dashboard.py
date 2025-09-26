"""Dashboard data helpers for the web admin API."""

from __future__ import annotations
"""Data aggregation helpers for the web admin dashboard."""

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, case, func, or_, select
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
    transaction_type: Optional[str] = None,
    status: Optional[str] = None,
    payment_method: Optional[str] = None,
) -> Dict[str, Any]:
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    base_query = (
        select(Transaction)
        .options(selectinload(Transaction.user))
        .order_by(Transaction.created_at.desc())
    )
    count_query = select(func.count(Transaction.id))
    summary_query = select(
        func.coalesce(func.sum(Transaction.amount_kopeks), 0).label("total_amount"),
        func.coalesce(
            func.sum(
                case((Transaction.amount_kopeks > 0, Transaction.amount_kopeks), else_=0)
            ),
            0,
        ).label("income_amount"),
        func.coalesce(
            func.sum(
                case((Transaction.amount_kopeks < 0, Transaction.amount_kopeks), else_=0)
            ),
            0,
        ).label("expense_amount"),
        func.coalesce(
            func.sum(case((Transaction.is_completed.is_(True), 1), else_=0)), 0
        ).label("completed_count"),
        func.coalesce(
            func.sum(case((Transaction.is_completed.is_(False), 1), else_=0)), 0
        ).label("pending_count"),
    )

    conditions = []
    join_user = False

    if transaction_type:
        conditions.append(Transaction.type == transaction_type)

    if status == "completed":
        conditions.append(Transaction.is_completed.is_(True))
    elif status == "pending":
        conditions.append(Transaction.is_completed.is_(False))

    if payment_method:
        conditions.append(Transaction.payment_method == payment_method)

    if search:
        normalized = search.strip()
        if normalized:
            join_user = True
            pattern = f"%{normalized.lower()}%"
            search_conditions = [
                func.lower(func.coalesce(Transaction.description, "")).like(pattern),
                func.lower(func.coalesce(Transaction.payment_method, "")).like(pattern),
                func.lower(func.coalesce(Transaction.external_id, "")).like(pattern),
            ]
            search_conditions.extend(
                [
                    func.lower(func.coalesce(User.username, "")).like(pattern),
                    func.lower(func.coalesce(User.first_name, "")).like(pattern),
                    func.lower(func.coalesce(User.last_name, "")).like(pattern),
                ]
            )
            if normalized.isdigit():
                numeric = int(normalized)
                search_conditions.extend(
                    [Transaction.id == numeric, User.telegram_id == numeric, User.id == numeric]
                )
            conditions.append(or_(*search_conditions))

    if join_user:
        base_query = base_query.join(User)
        count_query = count_query.join(User)
        summary_query = summary_query.join(User)

    for condition in conditions:
        base_query = base_query.where(condition)
        count_query = count_query.where(condition)
        summary_query = summary_query.where(condition)

    base_query = base_query.offset(offset).limit(limit)

    result = await session.execute(base_query)
    transactions = result.scalars().unique().all()

    total = await session.scalar(count_query) or 0

    summary_result = await session.execute(summary_query)
    summary_row = summary_result.one()

    income_amount = int(summary_row.income_amount or 0)
    expense_raw = int(summary_row.expense_amount or 0)
    completed_count = int(summary_row.completed_count or 0)
    pending_count = int(summary_row.pending_count or 0)

    summary = {
        "total_count": int(total),
        "completed_count": completed_count,
        "pending_count": pending_count,
        "income_kopeks": income_amount,
        "expense_kopeks": abs(expense_raw),
        "net_kopeks": income_amount + expense_raw,
        "income_rub": round(income_amount / 100, 2),
        "expense_rub": round(abs(expense_raw) / 100, 2),
        "net_rub": round((income_amount + expense_raw) / 100, 2),
    }

    return {
        "items": [serialize_transaction(tx) for tx in transactions],
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "summary": summary,
    }


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
