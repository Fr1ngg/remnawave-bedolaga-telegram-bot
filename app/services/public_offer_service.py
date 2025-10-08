import logging
from html.parser import HTMLParser
from typing import List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.public_offer import (
    get_public_offer,
    set_public_offer_enabled,
    upsert_public_offer,
)
from app.database.models import PublicOffer

logger = logging.getLogger(__name__)


class PublicOfferService:
    """Helpers for managing the public offer text and visibility."""

    MAX_PAGE_LENGTH = 3500

    @staticmethod
    def _normalize_language(language: str) -> str:
        base_language = language or settings.DEFAULT_LANGUAGE or "ru"
        return base_language.split("-")[0].lower()

    @staticmethod
    def normalize_language(language: str) -> str:
        return PublicOfferService._normalize_language(language)

    @classmethod
    async def get_offer(
        cls,
        db: AsyncSession,
        language: str,
        *,
        fallback: bool = False,
    ) -> Optional[PublicOffer]:
        lang = cls._normalize_language(language)
        offer = await get_public_offer(db, lang)

        if offer or not fallback:
            return offer

        default_lang = cls._normalize_language(settings.DEFAULT_LANGUAGE)
        if lang != default_lang:
            return await get_public_offer(db, default_lang)

        return offer

    @classmethod
    async def get_active_offer(
        cls,
        db: AsyncSession,
        language: str,
    ) -> Optional[PublicOffer]:
        lang = cls._normalize_language(language)
        offer = await get_public_offer(db, lang)

        if offer:
            if offer.is_enabled and offer.content.strip():
                return offer

            if not offer.is_enabled:
                return None

        default_lang = cls._normalize_language(settings.DEFAULT_LANGUAGE)
        if lang != default_lang:
            fallback_offer = await get_public_offer(db, default_lang)
            if fallback_offer and fallback_offer.is_enabled and fallback_offer.content.strip():
                return fallback_offer

        return None

    @classmethod
    async def is_offer_enabled(cls, db: AsyncSession, language: str) -> bool:
        offer = await cls.get_active_offer(db, language)
        return offer is not None

    @classmethod
    async def save_offer(
        cls,
        db: AsyncSession,
        language: str,
        content: str,
    ) -> PublicOffer:
        lang = cls._normalize_language(language)
        enable_if_new = True
        offer = await upsert_public_offer(
            db,
            lang,
            content,
            enable_if_new=enable_if_new,
        )
        logger.info("✅ Публичная оферта обновлена для языка %s", lang)
        return offer

    @classmethod
    async def set_enabled(
        cls,
        db: AsyncSession,
        language: str,
        enabled: bool,
    ) -> PublicOffer:
        lang = cls._normalize_language(language)
        return await set_public_offer_enabled(db, lang, enabled)

    @classmethod
    async def toggle_enabled(
        cls,
        db: AsyncSession,
        language: str,
    ) -> PublicOffer:
        lang = cls._normalize_language(language)
        offer = await get_public_offer(db, lang)

        if offer:
            new_status = not offer.is_enabled
        else:
            new_status = True

        return await set_public_offer_enabled(db, lang, new_status)

    class _RichTextPaginator(HTMLParser):
        """Split HTML-like text into Telegram-safe chunks."""

        SELF_CLOSING_TAGS = {"br", "hr", "img", "input", "meta", "link"}

        def __init__(self, max_len: int) -> None:
            super().__init__(convert_charrefs=False)
            self.max_len = max_len
            self.pages: List[str] = []
            self.current_parts: List[str] = []
            self.current_length = 0
            self.open_stack: List[Tuple[str, str, int]] = []
            self.closing_length = 0
            self.needs_prefix = False
            self.prefix_length = 0

        def _closing_sequence(self) -> str:
            return "".join(f"</{name}>" for name, _, _ in reversed(self.open_stack))

        def _ensure_prefix(self) -> None:
            if not self.needs_prefix:
                return

            prefix = "".join(token for _, token, _ in self.open_stack)
            if prefix:
                self.current_parts.append(prefix)
                self.current_length += len(prefix)
            self.needs_prefix = False

        def _flush(self) -> None:
            if not self.current_parts and not self.closing_length:
                return

            content = "".join(self.current_parts)
            closing_tags = self._closing_sequence()
            page = (content + closing_tags).strip()
            if page:
                self.pages.append(page)

            self.current_parts = []
            self.current_length = 0
            if self.prefix_length + self.closing_length > self.max_len:
                self.open_stack = []
                self.closing_length = 0
                self.prefix_length = 0
                self.needs_prefix = False
            else:
                self.needs_prefix = bool(self.open_stack)

        @staticmethod
        def _format_attrs(attrs: List[Tuple[str, Optional[str]]]) -> str:
            parts = []
            for name, value in attrs:
                if value is None:
                    parts.append(f" {name}")
                else:
                    parts.append(f" {name}=\"{value}\"")
            return "".join(parts)

        def _append_token(self, token: str) -> None:
            while True:
                self._ensure_prefix()
                if self.current_length + len(token) + self.closing_length <= self.max_len:
                    self.current_parts.append(token)
                    self.current_length += len(token)
                    break

                self._flush()

                if len(token) > self.max_len and not self.current_parts:
                    self.current_parts.append(token)
                    self.current_length += len(token)
                    break

        def handle_data(self, data: str) -> None:
            if not data:
                return

            remaining = data
            while remaining:
                self._ensure_prefix()
                available = self.max_len - (self.current_length + self.closing_length)
                if available <= 0:
                    self._flush()
                    continue

                piece = remaining[:available]
                self.current_parts.append(piece)
                self.current_length += len(piece)
                remaining = remaining[available:]

                if remaining:
                    self._flush()

        def handle_entityref(self, name: str) -> None:
            self.handle_data(f"&{name};")

        def handle_charref(self, name: str) -> None:
            self.handle_data(f"&#{name};")

        def _handle_self_closing(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
            token = f"<{tag}{self._format_attrs(attrs)}/>"
            self._append_token(token)

        def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
            if tag in self.SELF_CLOSING_TAGS:
                self._handle_self_closing(tag, attrs)
                return

            token = f"<{tag}{self._format_attrs(attrs)}>"
            closing_token = f"</{tag}>"
            closing_len = len(closing_token)

            while True:
                self._ensure_prefix()
                projected_length = self.current_length + len(token) + self.closing_length + closing_len
                if projected_length <= self.max_len:
                    self.current_parts.append(token)
                    self.current_length += len(token)
                    self.open_stack.append((tag, token, closing_len))
                    self.closing_length += closing_len
                    self.prefix_length += len(token)
                    break

                self._flush()

                if len(token) + closing_len > self.max_len and not self.current_parts:
                    self.current_parts.append(token)
                    self.current_length += len(token)
                    self.open_stack.append((tag, token, closing_len))
                    self.closing_length += closing_len
                    self.prefix_length += len(token)
                    break

        def handle_endtag(self, tag: str) -> None:
            token = f"</{tag}>"
            closing_len_reduction = 0
            index_to_remove = None
            for index in range(len(self.open_stack) - 1, -1, -1):
                if self.open_stack[index][0] == tag:
                    closing_len_reduction = self.open_stack[index][2]
                    index_to_remove = index
                    break

            while True:
                self._ensure_prefix()
                projected_closing_length = self.closing_length - closing_len_reduction
                if projected_closing_length < 0:
                    projected_closing_length = 0

                projected_total = self.current_length + len(token) + projected_closing_length
                if projected_total <= self.max_len or not self.current_parts:
                    self.current_parts.append(token)
                    self.current_length += len(token)
                    if index_to_remove is not None:
                        removed_tag = self.open_stack.pop(index_to_remove)
                        self.closing_length -= closing_len_reduction
                        self.prefix_length -= len(removed_tag[1])
                    break

                self._flush()

        def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
            self._handle_self_closing(tag, attrs)

        def finalize(self) -> List[str]:
            self._flush()
            return self.pages

    @classmethod
    def _split_rich_paragraph(cls, paragraph: str, max_len: int) -> List[str]:
        if len(paragraph) <= max_len:
            return [paragraph]

        paginator = cls._RichTextPaginator(max_len)
        paginator.feed(paragraph)
        paginator.close()
        pages = paginator.finalize()
        return pages or [paragraph]

    @classmethod
    def split_content_into_pages(
        cls,
        content: str,
        *,
        max_length: int = None,
    ) -> List[str]:
        if not content:
            return []

        normalized = content.replace("\r\n", "\n").strip()
        if not normalized:
            return []

        max_len = max_length or cls.MAX_PAGE_LENGTH

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
            segments = cls._split_rich_paragraph(paragraph, max_len)
            for segment in segments:
                segment = segment.strip()
                if not segment:
                    continue

                candidate = f"{current}\n\n{segment}".strip() if current else segment
                if len(candidate) <= max_len:
                    current = candidate
                    continue

                flush_current()
                current = segment

        flush_current()

        if not pages:
            return [normalized[:max_len]]

        return pages
