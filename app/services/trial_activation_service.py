from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.user import subtract_user_balance
from app.database.models import User


class TrialPaymentError(Exception):
    """Base exception for trial activation payment issues."""


@dataclass(slots=True)
class TrialPaymentInsufficientFunds(TrialPaymentError):
    required_amount: int
    balance_amount: int

    @property
    def missing_amount(self) -> int:
        return max(0, self.required_amount - self.balance_amount)


class TrialPaymentChargeFailed(TrialPaymentError):
    """Raised when balance charge could not be completed."""


async def charge_trial_activation_if_required(
    db: AsyncSession,
    user: User,
    *,
    description: Optional[str] = None,
) -> int:
    """Charges the user's balance if paid trial activation is enabled.

    Returns the charged amount in kopeks. If payment is not required or the
    configured price is zero, the function returns ``0``.
    """

    if not settings.is_trial_paid_activation_enabled():
        return 0

    price_kopeks = settings.get_trial_activation_price()
    if price_kopeks <= 0:
        return 0

    balance = int(getattr(user, "balance_kopeks", 0) or 0)
    if balance < price_kopeks:
        raise TrialPaymentInsufficientFunds(price_kopeks, balance)

    charge_description = description or "Активация триальной подписки"

    success = await subtract_user_balance(
        db,
        user,
        price_kopeks,
        charge_description,
    )
    if not success:
        raise TrialPaymentChargeFailed()

    # subtract_user_balance обновляет пользователя, но на всякий случай приводим к int
    return int(price_kopeks)
