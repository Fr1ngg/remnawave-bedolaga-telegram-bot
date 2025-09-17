from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter

from app.api.core.exception_response import exception_response
from app.api.domain.schemas.exception.global_exc import NotFoundException
from app.api.domain.services.user_service import UserService

router = APIRouter(
    route_class=DishkaRoute
)

@router.get("/{user_id}", responses={404: exception_response(NotFoundException)})
async def get_user_info(
    user_id: int,
    service: FromDishka[UserService]
):
    """Route для получения информации об user по его id в системе"""
    return await service.get_user_info_by_id(user_id=user_id)