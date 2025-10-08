import logging
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.faq import (
    bulk_update_order,
    create_faq_page,
    delete_faq_page,
    get_faq_page_by_id,
    get_faq_pages,
    get_faq_setting,
    set_faq_enabled,
    update_faq_page,
)
from app.database.models import FaqPage, FaqSetting


logger = logging.getLogger(__name__)


class FaqService:
    MAX_PAGE_LENGTH = 3500

    @staticmethod
    def _normalize_language(language: str) -> str:
        base_language = language or settings.DEFAULT_LANGUAGE or "ru"
        return base_language.split("-")[0].lower()

    @staticmethod
    def normalize_language(language: str) -> str:
        return FaqService._normalize_language(language)

    @classmethod
    async def get_setting(
        cls,
        db: AsyncSession,
        language: str,
        *,
        fallback: bool = True,
    ) -> Optional[FaqSetting]:
        lang = cls._normalize_language(language)
        setting = await get_faq_setting(db, lang)

        if setting or not fallback:
            return setting

        default_lang = cls._normalize_language(settings.DEFAULT_LANGUAGE)
        if lang != default_lang:
            return await get_faq_setting(db, default_lang)

        return setting

    @classmethod
    async def is_enabled(cls, db: AsyncSession, language: str) -> bool:
        pages = await cls.get_pages(db, language)
        return bool(pages)

    @classmethod
    async def set_enabled(
        cls,
        db: AsyncSession,
        language: str,
        enabled: bool,
    ) -> FaqSetting:
        lang = cls._normalize_language(language)
        return await set_faq_enabled(db, lang, enabled)

    @classmethod
    async def toggle_enabled(
        cls,
        db: AsyncSession,
        language: str,
    ) -> FaqSetting:
        lang = cls._normalize_language(language)
        setting = await get_faq_setting(db, lang)
        new_status = True
        if setting:
            new_status = not setting.is_enabled
        return await set_faq_enabled(db, lang, new_status)

    @classmethod
    async def get_pages(
        cls,
        db: AsyncSession,
        language: str,
        *,
        include_inactive: bool = False,
        fallback: bool = True,
    ) -> List[FaqPage]:
        lang = cls._normalize_language(language)
        pages = await get_faq_pages(db, lang, include_inactive=include_inactive)

        if pages:
            setting = await get_faq_setting(db, lang)
            if setting and not setting.is_enabled and not include_inactive:
                return []
            return pages

        if not fallback:
            return []

        default_lang = cls._normalize_language(settings.DEFAULT_LANGUAGE)
        if lang == default_lang:
            return []

        fallback_pages = await get_faq_pages(
            db,
            default_lang,
            include_inactive=include_inactive,
        )

        if not fallback_pages:
            return []

        setting = await get_faq_setting(db, default_lang)
        if setting and not setting.is_enabled and not include_inactive:
            return []

        return fallback_pages

    @classmethod
    async def get_page(
        cls,
        db: AsyncSession,
        page_id: int,
        language: str,
        *,
        fallback: bool = True,
        include_inactive: bool = False,
    ) -> Optional[FaqPage]:
        page = await get_faq_page_by_id(db, page_id)
        if not page:
            return None

        lang = cls._normalize_language(language)
        default_lang = cls._normalize_language(settings.DEFAULT_LANGUAGE)

        if not include_inactive and not page.is_active:
            return None

        if page.language == lang:
            return page

        if fallback and page.language == default_lang:
            return page

        return None

    @classmethod
    async def create_page(
        cls,
        db: AsyncSession,
        *,
        language: str,
        title: str,
        content: str,
        display_order: Optional[int] = None,
        is_active: Optional[bool] = None,
    ) -> FaqPage:
        lang = cls._normalize_language(language)
        is_active_value = True if is_active is None else bool(is_active)
        page = await create_faq_page(
            db,
            language=lang,
            title=title,
            content=content,
            display_order=display_order,
            is_active=is_active_value,
        )

        setting = await get_faq_setting(db, lang)
        if not setting:
            await set_faq_enabled(db, lang, True)

        return page

    @classmethod
    async def update_page(
        cls,
        db: AsyncSession,
        page: FaqPage,
        *,
        title: Optional[str] = None,
        content: Optional[str] = None,
        display_order: Optional[int] = None,
        is_active: Optional[bool] = None,
    ) -> FaqPage:
        return await update_faq_page(
            db,
            page,
            title=title,
            content=content,
            display_order=display_order,
            is_active=is_active,
        )

    @classmethod
    async def delete_page(cls, db: AsyncSession, page_id: int) -> None:
        await delete_faq_page(db, page_id)

    @classmethod
    async def reorder_pages(
        cls,
        db: AsyncSession,
        language: str,
        pages: List[FaqPage],
    ) -> None:
        lang = cls._normalize_language(language)
        ordered = [page for page in pages if page.language == lang]
        payload = [(page.id, index + 1) for index, page in enumerate(ordered)]
        await bulk_update_order(db, payload)

    @staticmethod
    def split_content_into_pages(
        content: str,
        *,
        max_length: Optional[int] = None,
    ) -> List[str]:
        if not content:
            return []

        normalized = content.replace("\r\n", "\n").strip()
        if not normalized:
            return []

        limit = max_length or FaqService.MAX_PAGE_LENGTH
        if len(normalized) <= limit:
            return [normalized]

        paragraphs = [
            paragraph.strip()
            for paragraph in normalized.split("\n\n")
            if paragraph.strip()
        ]

        pages: List[str] = []
        current = ""

        def flush_current() -> None:
            nonlocal current
            if current:
                pages.append(current.strip())
                current = ""

        for paragraph in paragraphs:
            candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
            if len(candidate) <= limit:
                current = candidate
                continue

            flush_current()

            if len(paragraph) <= limit:
                current = paragraph
                continue

            start = 0
            while start < len(paragraph):
                chunk = paragraph[start : start + limit]
                pages.append(chunk.strip())
                start += limit

            current = ""

        flush_current()

        if not pages:
            return [normalized[:limit]]

        return pages

