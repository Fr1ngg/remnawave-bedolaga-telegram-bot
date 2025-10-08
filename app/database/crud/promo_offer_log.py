from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import PromoOfferLog

logger = logging.getLogger(__name__)


async def log_promo_offer_action(
    db: AsyncSession,
    *,
    user_id: Optional[int],
    offer_id: Optional[int],
    action: str,
    source: Optional[str] = None,
    percent: Optional[int] = None,
    effect_type: Optional[str] = None,
    details: Optional[Dict[str, object]] = None,
    commit: bool = True,
) -> PromoOfferLog:
    """Persist a promo offer log entry."""

    entry = PromoOfferLog(
        user_id=user_id,
        offer_id=offer_id,
        action=action,
        source=source,
        percent=percent,
        effect_type=effect_type,
        details=(details or {}).copy(),
    )
    db.add(entry)

    if commit:
        try:
            await db.commit()
            await db.refresh(entry)
        except Exception:
            logger.exception("Failed to commit promo offer log entry")
            raise

    return entry


async def list_promo_offer_logs(
    db: AsyncSession,
    offset: int = 0,
    limit: int = 20,
    *,
    user_id: Optional[int] = None,
    offer_id: Optional[int] = None,
    action: Optional[str] = None,
    source: Optional[str] = None,
) -> Tuple[List[PromoOfferLog], int]:
    stmt = (
        select(PromoOfferLog)
        .options(
            selectinload(PromoOfferLog.user),
            selectinload(PromoOfferLog.offer),
        )
        .order_by(PromoOfferLog.created_at.desc())
        .offset(offset)
        .limit(limit)
    )

    if user_id is not None:
        stmt = stmt.where(PromoOfferLog.user_id == user_id)
    if offer_id is not None:
        stmt = stmt.where(PromoOfferLog.offer_id == offer_id)
    if action:
        stmt = stmt.where(PromoOfferLog.action == action)
    if source:
        stmt = stmt.where(PromoOfferLog.source == source)

    result = await db.execute(stmt)
    logs = result.scalars().all()

    count_stmt = select(func.count(PromoOfferLog.id))
    if user_id is not None:
        count_stmt = count_stmt.where(PromoOfferLog.user_id == user_id)
    if offer_id is not None:
        count_stmt = count_stmt.where(PromoOfferLog.offer_id == offer_id)
    if action:
        count_stmt = count_stmt.where(PromoOfferLog.action == action)
    if source:
        count_stmt = count_stmt.where(PromoOfferLog.source == source)

    total = (await db.execute(count_stmt)).scalar() or 0

    return logs, total
