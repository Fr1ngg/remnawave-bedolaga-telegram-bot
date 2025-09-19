from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Depends

from app.api.core.exception_response import exception_response
from app.api.domain.schemas.exception.global_exc import (
    NotFoundException,
    RemnawaveIntegrationException,
)
from app.api.domain.schemas.subscription import (
    SubscriptionExtendRequest,
    SubscriptionInfo,
)
from app.api.domain.services.subscription_service import SubscriptionService
from app.api.versions.v1.dependecies import get_available_auth


router = APIRouter(
    route_class=DishkaRoute,
    dependencies=[Depends(get_available_auth)],
)


@router.post(
    "/{user_id}/extend",
    responses={
        404: exception_response(NotFoundException),
        502: exception_response(RemnawaveIntegrationException),
    },
)
async def extend_subscription(
    user_id: int,
    payload: SubscriptionExtendRequest,
    service: FromDishka[SubscriptionService],
) -> SubscriptionInfo:
    """Продлевает подписку пользователя и обновляет данные в RemnaWave."""
    return await service.extend_user_subscription(user_identifier=user_id, days=payload.days)
