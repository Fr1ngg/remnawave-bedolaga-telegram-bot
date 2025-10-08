import logging
from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import FaqPage, FaqSetting

logger = logging.getLogger(__name__)


async def get_faq_setting(db: AsyncSession, language: str) -> Optional[FaqSetting]:
    result = await db.execute(
        select(FaqSetting).where(FaqSetting.language == language)
    )
    return result.scalar_one_or_none()


async def set_faq_enabled(db: AsyncSession, language: str, enabled: bool) -> FaqSetting:
    setting = await get_faq_setting(db, language)

    if setting:
        setting.is_enabled = bool(enabled)
        setting.updated_at = datetime.utcnow()
    else:
        setting = FaqSetting(
            language=language,
            is_enabled=bool(enabled),
        )
        db.add(setting)

    await db.commit()
    await db.refresh(setting)

    logger.info(
        "✅ Статус FAQ для языка %s обновлен: %s",
        language,
        "enabled" if setting.is_enabled else "disabled",
    )

    return setting


async def upsert_faq_setting(db: AsyncSession, language: str, enabled: bool) -> FaqSetting:
    return await set_faq_enabled(db, language, enabled)


async def get_faq_pages(
    db: AsyncSession,
    language: str,
    *,
    include_inactive: bool = False,
) -> list[FaqPage]:
    query = select(FaqPage).where(FaqPage.language == language)

    if not include_inactive:
        query = query.where(FaqPage.is_active.is_(True))

    query = query.order_by(FaqPage.display_order.asc(), FaqPage.id.asc())

    result = await db.execute(query)
    pages = list(result.scalars().all())
    return pages


async def get_faq_page_by_id(db: AsyncSession, page_id: int) -> Optional[FaqPage]:
    result = await db.execute(select(FaqPage).where(FaqPage.id == page_id))
    return result.scalar_one_or_none()


async def create_faq_page(
    db: AsyncSession,
    *,
    language: str,
    title: str,
    content: str,
    display_order: Optional[int] = None,
    is_active: bool = True,
) -> FaqPage:
    if display_order is None:
        result = await db.execute(
            select(func.max(FaqPage.display_order)).where(FaqPage.language == language)
        )
        max_order = result.scalar() or 0
        display_order = max_order + 1

    page = FaqPage(
        language=language,
        title=title,
        content=content,
        display_order=display_order,
        is_active=is_active,
    )

    db.add(page)
    await db.commit()
    await db.refresh(page)

    logger.info("✅ Создана страница FAQ %s для языка %s", page.id, language)

    return page


async def update_faq_page(
    db: AsyncSession,
    page: FaqPage,
    *,
    title: Optional[str] = None,
    content: Optional[str] = None,
    display_order: Optional[int] = None,
    is_active: Optional[bool] = None,
) -> FaqPage:
    if title is not None:
        page.title = title
    if content is not None:
        page.content = content
    if display_order is not None:
        page.display_order = display_order
    if is_active is not None:
        page.is_active = bool(is_active)

    page.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(page)

    logger.info("✅ Страница FAQ %s обновлена", page.id)

    return page


async def delete_faq_page(db: AsyncSession, page_id: int) -> None:
    await db.execute(delete(FaqPage).where(FaqPage.id == page_id))
    await db.commit()
    logger.info("🗑️ Страница FAQ %s удалена", page_id)


async def bulk_update_order(
    db: AsyncSession,
    pages: Iterable[tuple[int, int]],
) -> None:
    for page_id, order in pages:
        await db.execute(
            update(FaqPage)
            .where(FaqPage.id == page_id)
            .values(display_order=order, updated_at=datetime.utcnow())
        )
    await db.commit()

