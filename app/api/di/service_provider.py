from dishka import Scope, Provider, provide

from app.api.domain.services.user_service import UserService
from app.api.domain.services.subscription_service import SubscriptionService
from app.api.infrastructure.db import HolderRepo


class ServiceProvider(Provider):
    """DI Scope для services создается в момент инициализации функции"""
    scope = Scope.REQUEST

    @provide
    def get_user_service(self, holder_repo: HolderRepo) -> UserService:
        return UserService(holder_repo=holder_repo)

    @provide
    def get_subscription_service(self, holder_repo: HolderRepo) -> SubscriptionService:
        return SubscriptionService(holder_repo=holder_repo)
