from starlette import status

from app.api.domain.schemas.exception.base import AppException


class UserNotFoundException(AppException):
    status_code = status.HTTP_404_NOT_FOUND
    code = "user_not_found"
    detail = "User not found"