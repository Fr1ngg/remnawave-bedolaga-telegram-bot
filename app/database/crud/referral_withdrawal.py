import logging
from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import ReferralWithdrawalRequest

logger = logging.getLogger(__name__)


async def create_referral_withdrawal_request(
    db: AsyncSession,
    user_id: int,
    amount_kopeks: int,
    requisites: str,
    status: str = "pending",
) -> ReferralWithdrawalRequest:
    request = ReferralWithdrawalRequest(
        user_id=user_id,
        amount_kopeks=amount_kopeks,
        requisites=requisites,
        status=status,
    )

    db.add(request)
    await db.commit()
    await db.refresh(request)

    logger.info(
        "💸 Создан запрос на вывод партнёрки: %s₽ для пользователя %s",
        amount_kopeks / 100,
        user_id,
    )
    return request


async def get_total_requested_amount(
    db: AsyncSession,
    user_id: int,
    statuses: Optional[Iterable[str]] = None,
) -> int:
    query = select(func.coalesce(func.sum(ReferralWithdrawalRequest.amount_kopeks), 0)).where(
        ReferralWithdrawalRequest.user_id == user_id
    )

    if statuses:
        query = query.where(ReferralWithdrawalRequest.status.in_(list(statuses)))

    result = await db.execute(query)
    return result.scalar() or 0


async def get_referral_withdrawal_requests(
    db: AsyncSession,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    query = select(ReferralWithdrawalRequest).options(
        selectinload(ReferralWithdrawalRequest.user),
        selectinload(ReferralWithdrawalRequest.closed_by),
    ).order_by(ReferralWithdrawalRequest.created_at.desc())

    if status:
        query = query.where(ReferralWithdrawalRequest.status == status)

    result = await db.execute(query.offset(offset).limit(limit))
    return result.scalars().all()


async def get_referral_withdrawal_request_by_id(
    db: AsyncSession, request_id: int
) -> Optional[ReferralWithdrawalRequest]:
    result = await db.execute(
        select(ReferralWithdrawalRequest)
        .options(
            selectinload(ReferralWithdrawalRequest.user),
            selectinload(ReferralWithdrawalRequest.closed_by),
        )
        .where(ReferralWithdrawalRequest.id == request_id)
    )
    return result.scalar_one_or_none()


async def close_referral_withdrawal_request(
    db: AsyncSession, request: ReferralWithdrawalRequest, closed_by_id: Optional[int]
) -> ReferralWithdrawalRequest:
    request.status = "closed"
    request.closed_by_id = closed_by_id
    request.closed_at = datetime.utcnow()

    await db.commit()
    await db.refresh(request)
    return request

