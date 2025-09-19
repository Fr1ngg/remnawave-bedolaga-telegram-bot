from __future__ import annotations

import logging
from typing import Any, Dict

from app.config import settings
from app.localization.loader import (
    DEFAULT_LANGUAGE,
    clear_locale_cache,
    load_locale,
)

_logger = logging.getLogger(__name__)

_cached_rules: Dict[str, str] = {}


def _build_dynamic_values(language: str) -> Dict[str, Any]:
    language_code = (language or DEFAULT_LANGUAGE).split("-")[0].lower()

    if language_code == "ru":
        return {
            "PERIOD_14_DAYS": f"📅 14 дней - {settings.format_price(settings.PRICE_14_DAYS)}",
            "PERIOD_30_DAYS": f"📅 30 дней - {settings.format_price(settings.PRICE_30_DAYS)}",
            "PERIOD_60_DAYS": f"📅 60 дней - {settings.format_price(settings.PRICE_60_DAYS)}",
            "PERIOD_90_DAYS": f"📅 90 дней - {settings.format_price(settings.PRICE_90_DAYS)}",
            "PERIOD_180_DAYS": f"📅 180 дней - {settings.format_price(settings.PRICE_180_DAYS)}",
            "PERIOD_360_DAYS": f"📅 360 дней - {settings.format_price(settings.PRICE_360_DAYS)}",
            "TRAFFIC_5GB": f"📊 5 ГБ - {settings.format_price(settings.PRICE_TRAFFIC_5GB)}",
            "TRAFFIC_10GB": f"📊 10 ГБ - {settings.format_price(settings.PRICE_TRAFFIC_10GB)}",
            "TRAFFIC_25GB": f"📊 25 ГБ - {settings.format_price(settings.PRICE_TRAFFIC_25GB)}",
            "TRAFFIC_50GB": f"📊 50 ГБ - {settings.format_price(settings.PRICE_TRAFFIC_50GB)}",
            "TRAFFIC_100GB": f"📊 100 ГБ - {settings.format_price(settings.PRICE_TRAFFIC_100GB)}",
            "TRAFFIC_250GB": f"📊 250 ГБ - {settings.format_price(settings.PRICE_TRAFFIC_250GB)}",
            "TRAFFIC_UNLIMITED": f"📊 Безлимит - {settings.format_price(settings.PRICE_TRAFFIC_UNLIMITED)}",
            "SUPPORT_INFO": (
                "\n🛠️ <b>Техническая поддержка</b>\n\n"
                "По всем вопросам обращайтесь к нашей поддержке:\n\n"
                f"👤 {settings.SUPPORT_USERNAME}\n\n"
                "Мы поможем с:\n"
                "• Настройкой подключения\n"
                "• Решением технических проблем  \n"
                "• Вопросами по оплате\n"
                "• Другими вопросами\n\n"
                "⏰ Время ответа: обычно в течение 1-2 часов\n"
            ),
        }

    if language_code == "en":
        return {
            "PERIOD_14_DAYS": f"📅 14 days - {settings.format_price(settings.PRICE_14_DAYS)}",
            "PERIOD_30_DAYS": f"📅 30 days - {settings.format_price(settings.PRICE_30_DAYS)}",
            "PERIOD_60_DAYS": f"📅 60 days - {settings.format_price(settings.PRICE_60_DAYS)}",
            "PERIOD_90_DAYS": f"📅 90 days - {settings.format_price(settings.PRICE_90_DAYS)}",
            "PERIOD_180_DAYS": f"📅 180 days - {settings.format_price(settings.PRICE_180_DAYS)}",
            "PERIOD_360_DAYS": f"📅 360 days - {settings.format_price(settings.PRICE_360_DAYS)}",
            "TRAFFIC_5GB": f"📊 5 GB - {settings.format_price(settings.PRICE_TRAFFIC_5GB)}",
            "TRAFFIC_10GB": f"📊 10 GB - {settings.format_price(settings.PRICE_TRAFFIC_10GB)}",
            "TRAFFIC_25GB": f"📊 25 GB - {settings.format_price(settings.PRICE_TRAFFIC_25GB)}",
            "TRAFFIC_50GB": f"📊 50 GB - {settings.format_price(settings.PRICE_TRAFFIC_50GB)}",
            "TRAFFIC_100GB": f"📊 100 GB - {settings.format_price(settings.PRICE_TRAFFIC_100GB)}",
            "TRAFFIC_250GB": f"📊 250 GB - {settings.format_price(settings.PRICE_TRAFFIC_250GB)}",
            "TRAFFIC_UNLIMITED": f"📊 Unlimited - {settings.format_price(settings.PRICE_TRAFFIC_UNLIMITED)}",
            "SUPPORT_INFO": (
                "\n🛠️ <b>Technical support</b>\n\n"
                "For any questions contact our support:\n\n"
                f"👤 {settings.SUPPORT_USERNAME}\n\n"
                "We can help with:\n"
                "• Connection setup\n"
                "• Troubleshooting issues\n"
                "• Payment questions\n"
                "• Other requests\n\n"
                "⏰ Response time: usually within 1-2 hours\n"
            ),
        }

    return {}


class Texts:
    def __init__(self, language: str = DEFAULT_LANGUAGE):
        self.language = language or DEFAULT_LANGUAGE
        raw_data = load_locale(self.language)
        self._values = {key: value for key, value in raw_data.items()}

        if self.language != DEFAULT_LANGUAGE:
            fallback_data = load_locale(DEFAULT_LANGUAGE)
        else:
            fallback_data = self._values

        self._fallback_values = {
            key: value for key, value in fallback_data.items() if key not in self._values
        }

        self._values.update(_build_dynamic_values(self.language))

    def __getattr__(self, item: str) -> Any:
        if item == "language":
            return super().__getattribute__(item)
        try:
            return self._get_value(item)
        except KeyError as error:
            raise AttributeError(item) from error

    def __getitem__(self, item: str) -> Any:
        return self._get_value(item)

    def get(self, item: str, default: Any = None) -> Any:
        try:
            return self._get_value(item)
        except KeyError:
            return default

    def t(self, key: str, default: Any = None) -> Any:
        try:
            return self._get_value(key)
        except KeyError:
            if default is not None:
                return default
            raise

    def _get_value(self, item: str) -> Any:
        if item == "RULES_TEXT":
            if self.language in _cached_rules:
                return _cached_rules[self.language]
            return _get_default_rules(self.language)

        if item in self._values:
            return self._values[item]

        if item in self._fallback_values:
            return self._fallback_values[item]

        _logger.warning(
            "Missing localization key '%s' for language '%s'",
            item,
            self.language,
        )
        raise KeyError(item)

    @staticmethod
    def format_price(kopeks: int) -> str:
        return settings.format_price(kopeks)

    @staticmethod
    def format_traffic(gb: float) -> str:
        if gb == 0:
            return "∞ (безлимит)"
        if gb >= 1024:
            return f"{gb / 1024:.1f} ТБ"
        return f"{gb:.0f} ГБ"


def get_texts(language: str = DEFAULT_LANGUAGE) -> Texts:
    return Texts(language)


async def get_rules_from_db(language: str = DEFAULT_LANGUAGE) -> str:
    try:
        from app.database.database import get_db
        from app.database.crud.rules import get_current_rules_content

        async for db in get_db():
            rules = await get_current_rules_content(db, language)
            if rules:
                _cached_rules[language] = rules
                return rules
            break

    except Exception as error:  # pragma: no cover - defensive logging
        _logger.warning("Failed to load rules from DB for %s: %s", language, error)

    default = _get_default_rules(language)
    _cached_rules[language] = default
    return default


def _get_default_rules(language: str = DEFAULT_LANGUAGE) -> str:
    default_key = "RULES_TEXT_DEFAULT"
    locale = load_locale(language)
    if default_key in locale:
        return locale[default_key]
    fallback = load_locale(DEFAULT_LANGUAGE)
    return fallback.get(default_key, "")


async def get_rules_text(language: str = DEFAULT_LANGUAGE) -> str:
    if language in _cached_rules:
        return _cached_rules[language]

    return await get_rules_from_db(language)


async def refresh_rules_cache(language: str = DEFAULT_LANGUAGE) -> None:
    if language in _cached_rules:
        del _cached_rules[language]
    await get_rules_from_db(language)


def clear_rules_cache() -> None:
    _cached_rules.clear()


def reload_locales() -> None:
    clear_locale_cache()
