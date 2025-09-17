import logging

from app.api.domain.schemas.base import Page
from app.api.domain.schemas.exception.global_exc import NotFoundException
from app.api.domain.schemas.user import UserInfo, UserInfoShort
from app.api.infrastructure.db import HolderRepo

logger = logging.getLogger(__name__)

class UserService:
    def __init__(self, holder_repo: HolderRepo):
        self.holder_repo = holder_repo

    async def get_user_info_by_id(self, user_id: int) -> UserInfo:
        user = await self.holder_repo.user_repository.get_user_by_id(user_id)
        if user is None:
            raise NotFoundException
        return UserInfo(**user.__dict__)

    async def get_user_list_by_page(self, page: int, page_size: int) -> Page[UserInfoShort]:
        paginate_res = await self.holder_repo.user_repository.get_paginated_list(page, page_size)
        mapped_users = [UserInfoShort(**user.__dict__) for user in paginate_res.items]
        paginate_res.items = mapped_users
        return paginate_res