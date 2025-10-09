import logging
import re
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import (
    CallbackQuery,
    Message,
    PreCheckoutQuery,
    TelegramObject,
    User as TgUser,
)

from app.config import settings
from app.localization.texts import get_texts

logger = logging.getLogger(__name__)


ZERO_WIDTH_PATTERN = re.compile(r"[\u200B-\u200D\uFEFF]")

LINK_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"t\.me/\+",
        r"joinchat",
        r"https?://",
        r"www\.",
        r"tg://",
        r"telegram\.me",
        r"t\.me",
    )
]

DOMAIN_OBFUSCATION_PATTERN = re.compile(
    r"(?<![0-9a-zа-яё])(?:t|т)[\s\W_]*?(?:m|м)(?:e|е)",
    re.IGNORECASE,
)

CHAR_TRANSLATION = str.maketrans({
    "а": "a",
    "е": "e",
    "о": "o",
    "р": "p",
    "с": "c",
    "х": "x",
    "у": "y",
    "к": "k",
    "т": "t",
    "г": "g",
    "м": "m",
    "н": "n",
    "л": "l",
    "і": "i",
    "ї": "i",
    "ё": "e",
    "ь": "",
    "ъ": "",
    "ў": "u",
    "＠": "@",
})

COLLAPSE_PATTERN = re.compile(r"[\s\._\-/\\|,:;•·﹒․⋅··`~'\"!?()\[\]{}<>+=]+")

SUSPICIOUS_KEYWORDS = [
    "telegram",
    "teleqram",
    "teiegram",
    "teieqram",
    "telegrarn",
    "service",
    "notification",
    "system",
    "security",
    "safety",
    "support",
    "moderation",
    "review",
    "compliance",
    "abuse",
    "spam",
    "report",
    "телеграм",
    "служебн",
    "уведомлен",
    "поддержк",
    "безопасн",
    "модерац",
    "жалоб",
    "абуз",
    "служб",
    "повiдом",
    "пiдтрим",
]


class DisplayNameRestrictionMiddleware(BaseMiddleware):
    """Blocks users whose display name imitates links or official accounts."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user: TgUser | None = None

        if isinstance(event, (Message, CallbackQuery, PreCheckoutQuery)):
            user = event.from_user

        if not user or user.is_bot:
            return await handler(event, data)

        display_name = self._build_display_name(user)
        username = user.username or ""

        display_suspicious = self._is_suspicious(display_name)
        username_suspicious = self._is_suspicious(username)

        if display_suspicious or username_suspicious:
            suspicious_value = display_name if display_suspicious else username
            language = self._resolve_language(user, data)
            texts = get_texts(language)
            warning = texts.get(
                "SUSPICIOUS_DISPLAY_NAME_BLOCKED",
                "🚫 Ваше отображаемое имя похоже на ссылку или служебный аккаунт. "
                "Пожалуйста, измените имя и попробуйте снова.",
            )

            logger.warning(
                "🚫 DisplayNameRestriction: user %s blocked due to suspicious name '%s'",
                user.id,
                suspicious_value,
            )

            if isinstance(event, Message):
                await event.answer(warning)
            elif isinstance(event, CallbackQuery):
                await event.answer(warning, show_alert=True)
            elif isinstance(event, PreCheckoutQuery):
                await event.answer(ok=False, error_message=warning)
            return None

        return await handler(event, data)

    @staticmethod
    def _build_display_name(user: TgUser) -> str:
        parts = [user.first_name or "", user.last_name or ""]
        return " ".join(part for part in parts if part).strip()

    @staticmethod
    def _resolve_language(user: TgUser, data: Dict[str, Any]) -> str:
        db_user = data.get("db_user")
        if db_user and getattr(db_user, "language", None):
            return db_user.language
        language_code = getattr(user, "language_code", None)
        return language_code or settings.DEFAULT_LANGUAGE

    def _is_suspicious(self, value: str) -> bool:
        if not value:
            return False

        cleaned = ZERO_WIDTH_PATTERN.sub("", value)
        lower_value = cleaned.lower()

        if "@" in cleaned or "＠" in cleaned:
            return True

        if any(pattern.search(lower_value) for pattern in LINK_PATTERNS):
            return True

        if DOMAIN_OBFUSCATION_PATTERN.search(lower_value):
            return True

        normalized = self._normalize_text(lower_value)
        collapsed = COLLAPSE_PATTERN.sub("", normalized)

        if "tme" in collapsed:
            return True

        return any(keyword in normalized for keyword in SUSPICIOUS_KEYWORDS)

    @staticmethod
    def _normalize_text(value: str) -> str:
        return value.translate(CHAR_TRANSLATION)

