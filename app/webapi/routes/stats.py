from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Security
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Subscription,
    SubscriptionStatus,
    Ticket,
    TicketStatus,
    Transaction,
    TransactionType,
    User,
    UserStatus,
)

from ..dependencies import get_db_session, require_api_token

router = APIRouter()


@router.get(
    "/overview",
    summary="Общая статистика",
    response_description="Агрегированные показатели пользователей, подписок, саппорта и платежей",
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "users": {
                            "total": 12345,
                            "active": 9876,
                            "blocked": 321,
                            "balance_kopeks": 1234567,
                            "balance_rubles": 12345.67,
                        },
                        "subscriptions": {
                            "active": 4321,
                            "expired": 210,
                        },
                        "support": {
                            "open_tickets": 42,
                        },
                        "payments": {
                            "today_kopeks": 654321,
                            "today_rubles": 6543.21,
                        },
                    }
                }
            }
        }
    },
)
async def stats_overview(
    _: object = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    total_users = await db.scalar(select(func.count()).select_from(User)) or 0
    active_users = await db.scalar(
        select(func.count()).select_from(User).where(User.status == UserStatus.ACTIVE.value)
    ) or 0
    blocked_users = await db.scalar(
        select(func.count()).select_from(User).where(User.status == UserStatus.BLOCKED.value)
    ) or 0

    total_balance_kopeks = await db.scalar(
        select(func.coalesce(func.sum(User.balance_kopeks), 0))
    ) or 0

    active_subscriptions = await db.scalar(
        select(func.count()).select_from(Subscription).where(
            Subscription.status == SubscriptionStatus.ACTIVE.value,
        )
    ) or 0

    expired_subscriptions = await db.scalar(
        select(func.count()).select_from(Subscription).where(
            Subscription.status == SubscriptionStatus.EXPIRED.value,
        )
    ) or 0

    pending_tickets = await db.scalar(
        select(func.count()).select_from(Ticket).where(
            Ticket.status.in_([TicketStatus.OPEN.value, TicketStatus.ANSWERED.value])
        )
    ) or 0

    today = datetime.utcnow().date()
    today_transactions = await db.scalar(
        select(func.coalesce(func.sum(Transaction.amount_kopeks), 0)).where(
            func.date(Transaction.created_at) == today,
            Transaction.type == TransactionType.DEPOSIT.value,
        )
    ) or 0

    return {
        "users": {
            "total": total_users,
            "active": active_users,
            "blocked": blocked_users,
            "balance_kopeks": int(total_balance_kopeks),
            "balance_rubles": round(total_balance_kopeks / 100, 2),
        },
        "subscriptions": {
            "active": active_subscriptions,
            "expired": expired_subscriptions,
        },
        "support": {
            "open_tickets": pending_tickets,
        },
        "payments": {
            "today_kopeks": int(today_transactions),
            "today_rubles": round(today_transactions / 100, 2),
        },
    }
