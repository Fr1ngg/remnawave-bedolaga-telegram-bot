"""Expose promotions (promo codes, campaigns, promo groups) for the web admin."""

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import AdvertisingCampaign, PromoCode, PromoGroup
from app.webadmin.serializers import (
    serialize_campaign,
    serialize_promocode,
    serialize_promo_group,
)


async def fetch_promo_overview(session: AsyncSession) -> Dict[str, Any]:
    """Return aggregated counters for all promo entities."""

    total_codes = await session.scalar(select(func.count(PromoCode.id))) or 0
    active_codes = await session.scalar(
        select(func.count(PromoCode.id)).where(PromoCode.is_active.is_(True))
    ) or 0
    exhausted_codes = await session.scalar(
        select(func.count(PromoCode.id)).where(PromoCode.current_uses >= PromoCode.max_uses)
    ) or 0

    total_campaigns = await session.scalar(select(func.count(AdvertisingCampaign.id))) or 0
    active_campaigns = await session.scalar(
        select(func.count(AdvertisingCampaign.id)).where(AdvertisingCampaign.is_active.is_(True))
    ) or 0

    total_groups = await session.scalar(select(func.count(PromoGroup.id))) or 0
    default_groups = await session.scalar(
        select(func.count(PromoGroup.id)).where(PromoGroup.is_default.is_(True))
    ) or 0

    return {
        "promocodes": {
            "total": int(total_codes),
            "active": int(active_codes),
            "exhausted": int(exhausted_codes),
        },
        "campaigns": {
            "total": int(total_campaigns),
            "active": int(active_campaigns),
        },
        "groups": {
            "total": int(total_groups),
            "default": int(default_groups),
        },
    }


async def fetch_promocodes(
    session: AsyncSession,
    *,
    active: Optional[bool] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """Return promo codes sorted by creation date."""

    query = select(PromoCode).order_by(desc(PromoCode.created_at))
    if active is not None:
        query = query.where(PromoCode.is_active.is_(active))

    rows = await session.execute(query.limit(limit))
    return {
        "items": [serialize_promocode(code) for code in rows.scalars().all()],
    }


async def fetch_campaigns(session: AsyncSession, *, limit: int = 50) -> Dict[str, Any]:
    """Return advertising campaigns with registration counts."""

    rows = await session.execute(
        select(AdvertisingCampaign)
        .options(selectinload(AdvertisingCampaign.registrations))
        .order_by(desc(AdvertisingCampaign.created_at))
        .limit(limit)
    )

    campaigns = []
    for campaign in rows.scalars().all():
        registrations = len(campaign.registrations or [])
        campaigns.append(serialize_campaign(campaign, registrations=registrations))

    return {"items": campaigns}


async def fetch_promo_groups(session: AsyncSession) -> Dict[str, Any]:
    """Return promo groups with number of assigned users."""

    rows = await session.execute(
        select(PromoGroup)
        .options(selectinload(PromoGroup.users))
        .order_by(PromoGroup.name.asc())
    )

    groups = []
    for group in rows.scalars().all():
        groups.append(serialize_promo_group(group, users_count=len(group.users or [])))

    return {"items": groups}
