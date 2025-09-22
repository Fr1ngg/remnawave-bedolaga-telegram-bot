"""Helpers to work with flag emojis and localized country names."""

from __future__ import annotations

from functools import lru_cache
from typing import Dict, Optional


def flag_to_country_code(flag: Optional[str]) -> Optional[str]:
    """Convert a flag emoji to a two-letter ISO country code."""
    if not flag:
        return None

    try:
        code_points = [ord(char) - 127397 for char in flag if 127462 <= ord(char) <= 127487]
    except TypeError:
        return None

    if not code_points:
        return None

    try:
        return "".join(chr(point) for point in code_points)
    except ValueError:
        return None


_COUNTRY_NAMES_RU: Dict[str, str] = {
    "AE": "ОАЭ",
    "AR": "Аргентина",
    "AT": "Австрия",
    "AU": "Австралия",
    "BE": "Бельгия",
    "BG": "Болгария",
    "BR": "Бразилия",
    "CA": "Канада",
    "CH": "Швейцария",
    "CN": "Китай",
    "CY": "Кипр",
    "CZ": "Чехия",
    "DE": "Германия",
    "DK": "Дания",
    "EE": "Эстония",
    "ES": "Испания",
    "FI": "Финляндия",
    "FR": "Франция",
    "GB": "Великобритания",
    "GR": "Греция",
    "HK": "Гонконг",
    "HR": "Хорватия",
    "HU": "Венгрия",
    "IE": "Ирландия",
    "IL": "Израиль",
    "IN": "Индия",
    "IS": "Исландия",
    "IT": "Италия",
    "JP": "Япония",
    "KR": "Южная Корея",
    "KZ": "Казахстан",
    "LT": "Литва",
    "LU": "Люксембург",
    "LV": "Латвия",
    "MX": "Мексика",
    "NL": "Нидерланды",
    "NO": "Норвегия",
    "NZ": "Новая Зеландия",
    "PL": "Польша",
    "PT": "Португалия",
    "RO": "Румыния",
    "RS": "Сербия",
    "RU": "Россия",
    "SE": "Швеция",
    "SG": "Сингапур",
    "SI": "Словения",
    "SK": "Словакия",
    "TH": "Таиланд",
    "TR": "Турция",
    "UA": "Украина",
    "US": "США",
    "VN": "Вьетнам",
}


_COUNTRY_NAMES_EN: Dict[str, str] = {
    "AE": "United Arab Emirates",
    "AR": "Argentina",
    "AT": "Austria",
    "AU": "Australia",
    "BE": "Belgium",
    "BG": "Bulgaria",
    "BR": "Brazil",
    "CA": "Canada",
    "CH": "Switzerland",
    "CN": "China",
    "CY": "Cyprus",
    "CZ": "Czech Republic",
    "DE": "Germany",
    "DK": "Denmark",
    "EE": "Estonia",
    "ES": "Spain",
    "FI": "Finland",
    "FR": "France",
    "GB": "United Kingdom",
    "GR": "Greece",
    "HK": "Hong Kong",
    "HR": "Croatia",
    "HU": "Hungary",
    "IE": "Ireland",
    "IL": "Israel",
    "IN": "India",
    "IS": "Iceland",
    "IT": "Italy",
    "JP": "Japan",
    "KR": "South Korea",
    "KZ": "Kazakhstan",
    "LT": "Lithuania",
    "LU": "Luxembourg",
    "LV": "Latvia",
    "MX": "Mexico",
    "NL": "Netherlands",
    "NO": "Norway",
    "NZ": "New Zealand",
    "PL": "Poland",
    "PT": "Portugal",
    "RO": "Romania",
    "RS": "Serbia",
    "RU": "Russia",
    "SE": "Sweden",
    "SG": "Singapore",
    "SI": "Slovenia",
    "SK": "Slovakia",
    "TH": "Thailand",
    "TR": "Turkey",
    "UA": "Ukraine",
    "US": "United States",
    "VN": "Vietnam",
}


@lru_cache(maxsize=None)
def _get_names(language: str) -> Dict[str, str]:
    lang_code = (language or "en").split("-")[0].lower()
    if lang_code == "ru":
        return _COUNTRY_NAMES_RU
    if lang_code == "en":
        return _COUNTRY_NAMES_EN
    return _COUNTRY_NAMES_EN


def get_country_name_by_flag(flag: Optional[str], language: str = "ru") -> Optional[str]:
    """Return a localized country name for the given flag emoji."""
    code = flag_to_country_code(flag)
    if not code:
        return None

    code = code.upper()
    names = _get_names(language)
    if code in names:
        return names[code]

    # Fallback to English if the requested language has no translation.
    if language.split("-")[0].lower() != "en" and code in _COUNTRY_NAMES_EN:
        return _COUNTRY_NAMES_EN[code]

    return None
