from app.api.domain.schemas.exception.global_exc import NotFoundException
from app.api.domain.schemas.user import UserInfoResponse
from app.api.infrastructure.db import HolderRepo


class UserService:
    def __init__(self, holder_repo: HolderRepo):
        self.holder_repo = holder_repo

    async def get_user_info_by_id(self, user_id: int) -> UserInfoResponse:
        user = await self.holder_repo.user_repository.get_user_by_id(user_id)
        if user is None:
            raise NotFoundException

        return UserInfoResponse(
            id=user.id,
            telegram_id=user.telegram_id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            language=user.language,
        )