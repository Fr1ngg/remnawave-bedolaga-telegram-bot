from typing import Optional

from app.database.models import User
from app.utils.cache import UserCache


_CHECKOUT_SESSION_KEY = "subscription_checkout"
_CHECKOUT_TTL_SECONDS = 3600


async def save_subscription_checkout_draft(
    user_id: int, data: dict, ttl: int = _CHECKOUT_TTL_SECONDS
) -> bool:
    """Persist subscription checkout draft data in cache."""

    return await UserCache.set_user_session(user_id, _CHECKOUT_SESSION_KEY, data, ttl)


async def get_subscription_checkout_draft(user_id: int) -> Optional[dict]:
    """Retrieve subscription checkout draft from cache."""

    return await UserCache.get_user_session(user_id, _CHECKOUT_SESSION_KEY)


async def clear_subscription_checkout_draft(user_id: int) -> bool:
    """Remove stored subscription checkout draft for the user."""

    return await UserCache.delete_user_session(user_id, _CHECKOUT_SESSION_KEY)


async def has_subscription_checkout_draft(user_id: int) -> bool:
    draft = await get_subscription_checkout_draft(user_id)
    return draft is not None


def should_offer_checkout_resume(user: User, has_draft: bool) -> bool:
    """
    Determine whether checkout resume button should be available for the user.

    Only users without an active paid subscription or users currently on trial
    are eligible to continue assembling the subscription from the saved draft.
    """

    if not has_draft:
        return False

    subscription = getattr(user, "subscription", None)

    if subscription is None:
        return True

    return bool(getattr(subscription, "is_trial", False))
