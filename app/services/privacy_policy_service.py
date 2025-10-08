import logging
from typing import Optional, List

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.privacy_policy import (
    get_privacy_policy,
    set_privacy_policy_enabled,
    upsert_privacy_policy,
)
from app.database.models import PrivacyPolicy

logger = logging.getLogger(__name__)


class PrivacyPolicyService:
    """Utility helpers around privacy policy storage and presentation."""

    MAX_PAGE_LENGTH = 3500

    @staticmethod
    def _normalize_language(language: str) -> str:
        base_language = language or settings.DEFAULT_LANGUAGE or "ru"
        return base_language.split("-")[0].lower()

    @staticmethod
    def normalize_language(language: str) -> str:
        return PrivacyPolicyService._normalize_language(language)

    @classmethod
    async def get_policy(
        cls,
        db: AsyncSession,
        language: str,
        *,
        fallback: bool = False,
    ) -> Optional[PrivacyPolicy]:
        lang = cls._normalize_language(language)
        policy = await get_privacy_policy(db, lang)

        if policy or not fallback:
            return policy

        default_lang = cls._normalize_language(settings.DEFAULT_LANGUAGE)
        if lang != default_lang:
            return await get_privacy_policy(db, default_lang)

        return policy

    @classmethod
    async def get_active_policy(
        cls,
        db: AsyncSession,
        language: str,
    ) -> Optional[PrivacyPolicy]:
        lang = cls._normalize_language(language)
        policy = await get_privacy_policy(db, lang)

        if policy and policy.is_enabled and policy.content.strip():
            return policy

        default_lang = cls._normalize_language(settings.DEFAULT_LANGUAGE)
        if lang != default_lang:
            fallback_policy = await get_privacy_policy(db, default_lang)
            if fallback_policy and fallback_policy.is_enabled and fallback_policy.content.strip():
                return fallback_policy

        return None

    @classmethod
    async def is_policy_enabled(cls, db: AsyncSession, language: str) -> bool:
        policy = await cls.get_active_policy(db, language)
        return policy is not None

    @classmethod
    async def save_policy(
        cls,
        db: AsyncSession,
        language: str,
        content: str,
    ) -> PrivacyPolicy:
        lang = cls._normalize_language(language)
        enable_if_new = True
        policy = await upsert_privacy_policy(
            db,
            lang,
            content,
            enable_if_new=enable_if_new,
        )
        logger.info("✅ Политика конфиденциальности обновлена для языка %s", lang)
        return policy

    @classmethod
    async def set_enabled(
        cls,
        db: AsyncSession,
        language: str,
        enabled: bool,
    ) -> PrivacyPolicy:
        lang = cls._normalize_language(language)
        return await set_privacy_policy_enabled(db, lang, enabled)

    @classmethod
    async def toggle_enabled(
        cls,
        db: AsyncSession,
        language: str,
    ) -> PrivacyPolicy:
        lang = cls._normalize_language(language)
        policy = await get_privacy_policy(db, lang)

        if policy:
            new_status = not policy.is_enabled
        else:
            new_status = True

        return await set_privacy_policy_enabled(db, lang, new_status)

    @staticmethod
    def split_content_into_pages(
        content: str,
        *,
        max_length: int = None,
    ) -> List[str]:
        if not content:
            return []

        normalized = content.replace("\r\n", "\n").strip()
        if not normalized:
            return []

        max_len = max_length or PrivacyPolicyService.MAX_PAGE_LENGTH

        if len(normalized) <= max_len:
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
            if len(candidate) <= max_len:
                current = candidate
                continue

            flush_current()

            if len(paragraph) <= max_len:
                current = paragraph
                continue

            start_index = 0
            while start_index < len(paragraph):
                chunk = paragraph[start_index:start_index + max_len]
                pages.append(chunk.strip())
                start_index += max_len

            current = ""

        flush_current()

        if not pages:
            return [normalized[:max_len]]

        return pages
