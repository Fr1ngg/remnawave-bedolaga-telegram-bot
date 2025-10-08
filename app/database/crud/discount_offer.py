from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.crud.promo_offer_log import log_promo_offer_action
from app.database.models import DiscountOffer

logger = logging.getLogger(__name__)


async def upsert_discount_offer(
    db: AsyncSession,
    *,
    user_id: int,
    subscription_id: Optional[int],
    notification_type: str,
    discount_percent: int,
    bonus_amount_kopeks: int,
    valid_hours: int,
    effect_type: str = "percent_discount",
    extra_data: Optional[dict] = None,
) -> DiscountOffer:
    """Create or refresh a discount offer for a user."""

    expires_at = datetime.utcnow() + timedelta(hours=valid_hours)

    result = await db.execute(
        select(DiscountOffer)
        .where(
            DiscountOffer.user_id == user_id,
            DiscountOffer.notification_type == notification_type,
            DiscountOffer.is_active == True,  # noqa: E712
        )
        .order_by(DiscountOffer.created_at.desc())
    )
    offer = result.scalars().first()

    if offer and offer.claimed_at is None:
        offer.discount_percent = discount_percent
        offer.bonus_amount_kopeks = bonus_amount_kopeks
        offer.expires_at = expires_at
        offer.subscription_id = subscription_id
        offer.effect_type = effect_type
        offer.extra_data = extra_data
    else:
        offer = DiscountOffer(
            user_id=user_id,
            subscription_id=subscription_id,
            notification_type=notification_type,
            discount_percent=discount_percent,
            bonus_amount_kopeks=bonus_amount_kopeks,
            expires_at=expires_at,
            is_active=True,
            effect_type=effect_type,
            extra_data=extra_data,
        )
        db.add(offer)

    await db.commit()
    await db.refresh(offer)
    return offer


async def get_offer_by_id(db: AsyncSession, offer_id: int) -> Optional[DiscountOffer]:
    result = await db.execute(
        select(DiscountOffer)
        .options(
            selectinload(DiscountOffer.user),
            selectinload(DiscountOffer.subscription),
        )
        .where(DiscountOffer.id == offer_id)
    )
    return result.scalar_one_or_none()


async def list_discount_offers(
    db: AsyncSession,
    *,
    offset: int = 0,
    limit: int = 50,
    user_id: Optional[int] = None,
    notification_type: Optional[str] = None,
    notification_type_prefix: Optional[str] = None,
    is_active: Optional[bool] = None,
) -> List[DiscountOffer]:
    stmt = (
        select(DiscountOffer)
        .options(
            selectinload(DiscountOffer.user),
            selectinload(DiscountOffer.subscription),
        )
        .order_by(DiscountOffer.created_at.desc())
        .offset(offset)
        .limit(limit)
    )

    if user_id is not None:
        stmt = stmt.where(DiscountOffer.user_id == user_id)
    if notification_type:
        stmt = stmt.where(DiscountOffer.notification_type == notification_type)
    elif notification_type_prefix:
        stmt = stmt.where(DiscountOffer.notification_type.like(f"{notification_type_prefix}%"))
    if is_active is not None:
        stmt = stmt.where(DiscountOffer.is_active == is_active)

    result = await db.execute(stmt)
    return result.scalars().all()


async def count_discount_offers(
    db: AsyncSession,
    *,
    user_id: Optional[int] = None,
    notification_type: Optional[str] = None,
    notification_type_prefix: Optional[str] = None,
    is_active: Optional[bool] = None,
) -> int:
    stmt = select(func.count(DiscountOffer.id))

    if user_id is not None:
        stmt = stmt.where(DiscountOffer.user_id == user_id)
    if notification_type:
        stmt = stmt.where(DiscountOffer.notification_type == notification_type)
    elif notification_type_prefix:
        stmt = stmt.where(DiscountOffer.notification_type.like(f"{notification_type_prefix}%"))
    if is_active is not None:
        stmt = stmt.where(DiscountOffer.is_active == is_active)

    result = await db.execute(stmt)
    return int(result.scalar() or 0)


async def mark_offer_claimed(
    db: AsyncSession,
    offer: DiscountOffer,
    *,
    details: Optional[dict] = None,
) -> DiscountOffer:
    offer.claimed_at = datetime.utcnow()
    offer.is_active = False
    await db.commit()
    await db.refresh(offer)

    try:
        await log_promo_offer_action(
            db,
            user_id=offer.user_id,
            offer_id=offer.id,
            action="claimed",
            source=offer.notification_type,
            percent=offer.discount_percent,
            effect_type=offer.effect_type,
            details=details,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning(
            "Failed to record promo offer claim log for offer %s: %s",
            offer.id,
            exc,
        )
        try:
            await db.rollback()
        except Exception as rollback_error:  # pragma: no cover - defensive logging
            logger.warning(
                "Failed to rollback session after promo offer claim log failure: %s",
                rollback_error,
            )

    return offer


async def deactivate_expired_offers(db: AsyncSession) -> int:
    now = datetime.utcnow()
    result = await db.execute(
        select(DiscountOffer).where(
            DiscountOffer.is_active == True,  # noqa: E712
            DiscountOffer.expires_at < now,
        )
    )
    offers = result.scalars().all()
    if not offers:
        return 0

    count = 0
    log_payloads = []
    for offer in offers:
        offer.is_active = False
        count += 1
        log_payloads.append(
            {
                "user_id": offer.user_id,
                "offer_id": offer.id,
                "source": offer.notification_type,
                "percent": offer.discount_percent,
                "effect_type": offer.effect_type,
            }
        )

    await db.commit()

    for payload in log_payloads:
        if not payload.get("user_id"):
            continue
        try:
            await log_promo_offer_action(
                db,
                user_id=payload["user_id"],
                offer_id=payload["offer_id"],
                action="disabled",
                source=payload.get("source"),
                percent=payload.get("percent"),
                effect_type=payload.get("effect_type"),
                details={"reason": "offer_expired"},
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning(
                "Failed to record promo offer disable log for offer %s: %s",
                payload.get("offer_id"),
                exc,
            )
            try:
                await db.rollback()
            except Exception as rollback_error:  # pragma: no cover - defensive logging
                logger.warning(
                    "Failed to rollback session after promo offer disable log failure: %s",
                    rollback_error,
                )

    return count


async def get_latest_claimed_offer_for_user(
    db: AsyncSession,
    user_id: int,
    source: Optional[str] = None,
) -> Optional[DiscountOffer]:
    stmt = (
        select(DiscountOffer)
        .where(
            DiscountOffer.user_id == user_id,
            DiscountOffer.claimed_at.isnot(None),
        )
        .order_by(DiscountOffer.claimed_at.desc())
    )

    if source:
        stmt = stmt.where(DiscountOffer.notification_type == source)

    result = await db.execute(stmt)
    return result.scalars().first()
