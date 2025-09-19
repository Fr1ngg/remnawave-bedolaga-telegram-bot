from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Depends, Query

from app.api.core.exception_response import exception_response
from app.api.domain.schemas.base import Page
from app.api.domain.schemas.exception.global_exc import NotFoundException
from app.api.domain.schemas.user import UserInfo, UserInfoShort, UserBalanceUpdate
from app.api.domain.services.user_service import UserService
from app.api.versions.v1.dependecies import get_available_auth

router = APIRouter(
    route_class=DishkaRoute,
    dependencies=[Depends(get_available_auth)]
)

@router.get("/list")
async def get_user_list(
    service: FromDishka[UserService],
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
) -> Page[UserInfoShort]:
    return await service.get_user_list_by_page(page, size)

@router.get("/search")
async def search_users_by_username_prefix(
    service: FromDishka[UserService],
    username_prefix: str = Query(..., min_length=1, max_length=255),
) -> list[UserInfoShort]:
    """Возвращает пользователей, чьи usernames начинаются с указанного префикса."""
    return await service.get_users_by_username_prefix(username_prefix=username_prefix)

@router.get(
    "/{user_id}",
    responses={404: exception_response(NotFoundException)})
async def get_user_info(
    user_id: int,
    service: FromDishka[UserService]
) -> UserInfo:
    """Route для получения информации об user по его id в системе"""
    return await service.get_user_info_by_id(user_id=user_id)


@router.patch(
    "/{user_id}/balance",
    responses={404: exception_response(NotFoundException)},
)
async def update_user_balance(
    user_id: int,
    payload: UserBalanceUpdate,
    service: FromDishka[UserService],
) -> UserInfo:
    return await service.update_user_balance(
        user_id=user_id,
        new_balance=payload.new_balance,
    )
