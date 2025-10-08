import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import PublicOffer

logger = logging.getLogger(__name__)


async def get_public_offer(db: AsyncSession, language: str) -> Optional[PublicOffer]:
    result = await db.execute(
        select(PublicOffer).where(PublicOffer.language == language)
    )
    return result.scalar_one_or_none()


async def upsert_public_offer(
    db: AsyncSession,
    language: str,
    content: str,
    *,
    enable_if_new: bool = True,
) -> PublicOffer:
    offer = await get_public_offer(db, language)

    if offer:
        offer.content = content or ""
        offer.updated_at = datetime.utcnow()
    else:
        offer = PublicOffer(
            language=language,
            content=content or "",
            is_enabled=True if enable_if_new else False,
        )
        db.add(offer)

    await db.commit()
    await db.refresh(offer)

    logger.info(
        "✅ Публичная оферта для языка %s обновлена (ID: %s)",
        language,
        offer.id,
    )

    return offer


async def set_public_offer_enabled(
    db: AsyncSession,
    language: str,
    enabled: bool,
) -> PublicOffer:
    offer = await get_public_offer(db, language)

    if offer:
        offer.is_enabled = bool(enabled)
        offer.updated_at = datetime.utcnow()
    else:
        offer = PublicOffer(
            language=language,
            content="",
            is_enabled=bool(enabled),
        )
        db.add(offer)

    await db.commit()
    await db.refresh(offer)

    logger.info(
        "✅ Статус публичной оферты для языка %s обновлен: %s",
        language,
        "enabled" if offer.is_enabled else "disabled",
    )

    return offer
