from datetime import datetime, timedelta
from typing import Tuple
import logging

logger = logging.getLogger(__name__)


def calculate_months_from_days(days: int) -> int:
    return max(1, round(days / 30))


def get_remaining_months(end_date: datetime) -> int:
    current_time = datetime.utcnow()
    if end_date <= current_time:
        return 1 
    
    remaining_days = (end_date - current_time).days
    return max(1, round(remaining_days / 30))


def calculate_period_multiplier(period_days: int) -> Tuple[int, float]:
    exact_months = period_days / 30
    months_count = max(1, round(exact_months))
    
    logger.debug(f"Период {period_days} дней = {exact_months:.2f} точных месяцев ≈ {months_count} месяцев для расчета")
    
    return months_count, exact_months


def calculate_prorated_price(
    monthly_price: int,
    end_date: datetime,
    min_charge_months: int = 1
) -> Tuple[int, int]:
    months_remaining = get_remaining_months(end_date)
    months_to_charge = max(min_charge_months, months_remaining)
    
    total_price = monthly_price * months_to_charge
    
    logger.debug(f"Расчет пропорциональной цены: {monthly_price/100}₽/мес × {months_to_charge} мес = {total_price/100}₽")
    
    return total_price, months_to_charge


def apply_percentage_discount(amount: int, percent: int) -> Tuple[int, int]:
    if amount <= 0 or percent <= 0:
        return amount, 0

    clamped_percent = max(0, min(100, percent))
    discount_value = amount * clamped_percent // 100
    discounted_amount = amount - discount_value

    # Round the discounted price up to the nearest full ruble (100 kopeks)
    # to avoid undercharging users because of fractional kopeks.
    if discount_value >= 100 and discounted_amount % 100:
        discounted_amount += 100 - (discounted_amount % 100)
        discounted_amount = min(discounted_amount, amount)
        discount_value = amount - discounted_amount

    logger.debug(
        "Применена скидка %s%%: %s → %s (скидка %s)",
        clamped_percent,
        amount,
        discounted_amount,
        discount_value,
    )

    return discounted_amount, discount_value


def format_period_description(days: int, language: str = "ru") -> str:
    months = calculate_months_from_days(days)
    
    if language == "ru":
        if days == 14:
            return "14 дней"
        if days == 30:
            return "1 месяц"
        elif days == 60:
            return "2 месяца"
        elif days == 90:
            return "3 месяца"
        elif days == 180:
            return "6 месяцев"
        elif days == 360:
            return "12 месяцев"
        else:
            month_word = "месяц" if months == 1 else ("месяца" if 2 <= months <= 4 else "месяцев")
            return f"{days} дней ({months} {month_word})"
    else:
        if days == 14:
            return "14 days"
        month_word = "month" if months == 1 else "months"
        return f"{days} days ({months} {month_word})"


def validate_pricing_calculation(
    base_price: int,
    monthly_additions: int,
    months: int,
    total_calculated: int
) -> bool:
    expected_total = base_price + (monthly_additions * months)
    is_valid = expected_total == total_calculated
    
    if not is_valid:
        logger.warning(f"Несоответствие в расчете цены: ожидалось {expected_total/100}₽, получено {total_calculated/100}₽")
        logger.warning(f"Детали: базовая цена {base_price/100}₽ + месячные дополнения {monthly_additions/100}₽ × {months} мес")
    
    return is_valid


STANDARD_PERIODS = {
    14: {"months": 0.5, "display_ru": "2 недели", "display_en": "2 weeks"},
    30: {"months": 1, "display_ru": "1 месяц", "display_en": "1 month"},
    60: {"months": 2, "display_ru": "2 месяца", "display_en": "2 months"},
    90: {"months": 3, "display_ru": "3 месяца", "display_en": "3 months"},
    180: {"months": 6, "display_ru": "6 месяцев", "display_en": "6 months"},
    360: {"months": 12, "display_ru": "1 год", "display_en": "1 year"},
}


def get_period_info(days: int) -> dict:
    return STANDARD_PERIODS.get(days)
