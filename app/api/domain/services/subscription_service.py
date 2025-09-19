import logging

from app.api.domain.schemas.exception.global_exc import (
    NotFoundException,
    RemnawaveIntegrationException,
)
from app.api.domain.schemas.subscription import SubscriptionInfo
from app.api.infrastructure.db import HolderRepo
from app.services.subscription_service import SubscriptionService as CoreSubscriptionService


logger = logging.getLogger(__name__)


class SubscriptionService:
    def __init__(self, holder_repo: HolderRepo):
        self.holder_repo = holder_repo
        self._core_subscription_service = CoreSubscriptionService()

    async def extend_user_subscription(self, user_identifier: int, days: int) -> SubscriptionInfo:
        user = await self.holder_repo.user_repository.get_user_by_id(user_identifier)
        if user is None:
            user = await self.holder_repo.user_repository.get_user_by_telegram_id(user_identifier)
            if user is None:
                logger.debug("User %s not found", user_identifier)
                raise NotFoundException

        subscription = await self.holder_repo.subscription_repository.get_subscription_by_user_id(user.id)
        if subscription is None:
            logger.debug("Subscription for user %s not found, creating new one", user.id)
            subscription = await self.holder_repo.subscription_repository.create_subscription_for_user(user)

        original_end_date = subscription.end_date
        original_status = subscription.status
        original_updated_at = subscription.updated_at

        subscription = await self.holder_repo.subscription_repository.extend_subscription(subscription, days)

        try:
            updated_user = await self._core_subscription_service.update_remnawave_user(
                self.holder_repo.subscription_repository.session,
                subscription,
            )
        except Exception as exc:
            logger.exception("RemnaWave update raised exception for user %s", user.id)
            await self.holder_repo.subscription_repository.restore_subscription_state(
                subscription,
                end_date=original_end_date,
                status=original_status,
                updated_at=original_updated_at,
            )
            raise RemnawaveIntegrationException("RemnaWave API update failed") from exc

        if updated_user is None:
            logger.error("RemnaWave update returned empty response for user %s", user.id)
            await self.holder_repo.subscription_repository.restore_subscription_state(
                subscription,
                end_date=original_end_date,
                status=original_status,
                updated_at=original_updated_at,
            )
            raise RemnawaveIntegrationException("RemnaWave API returned empty response")

        return SubscriptionInfo.from_orm(subscription)
