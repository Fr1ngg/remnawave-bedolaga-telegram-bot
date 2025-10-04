from datetime import datetime, timedelta
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import DiscountOffer


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
        select(DiscountOffer).where(DiscountOffer.id == offer_id)
    )
    return result.scalar_one_or_none()


async def mark_offer_claimed(
    db: AsyncSession,
    offer: DiscountOffer,
    *,
    deactivate: bool = True,
) -> DiscountOffer:
    offer.claimed_at = datetime.utcnow()
    if deactivate:
        offer.is_active = False
    await db.commit()
    await db.refresh(offer)
    return offer


async def get_pending_percent_discount_offer(
    db: AsyncSession,
    user_id: int,
    *,
    allowed_offer_types: Optional[Iterable[str]] = None,
) -> Optional[DiscountOffer]:
    result = await db.execute(
        select(DiscountOffer)
        .where(
            DiscountOffer.user_id == user_id,
            DiscountOffer.effect_type == "percent_discount",
            DiscountOffer.claimed_at.isnot(None),
        )
        .order_by(DiscountOffer.claimed_at.desc(), DiscountOffer.created_at.desc())
    )
    offers = result.scalars().all()

    now = datetime.utcnow()
    offer_type_whitelist = set(allowed_offer_types or []) if allowed_offer_types else None

    for offer in offers:
        if offer.expires_at and offer.expires_at <= now:
            continue

        payload = offer.extra_data or {}
        if payload.get("version") != "percent_discount_v1":
            continue
        if payload.get("consumed") is True:
            continue
        if offer_type_whitelist and payload.get("offer_type") not in offer_type_whitelist:
            continue

        return offer

    return None


async def consume_percent_discount_offer(
    db: AsyncSession,
    offer: DiscountOffer,
) -> DiscountOffer:
    payload = dict(offer.extra_data or {})
    payload["consumed"] = True
    payload["consumed_at"] = datetime.utcnow().isoformat()
    offer.extra_data = payload
    offer.is_active = False
    await db.commit()
    await db.refresh(offer)
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
    for offer in offers:
        offer.is_active = False
        count += 1

    await db.commit()
    return count
