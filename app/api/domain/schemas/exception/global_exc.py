from starlette import status

from app.api.domain.schemas.exception.base import AppException


class NotFoundException(AppException):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    detail = "Not found"