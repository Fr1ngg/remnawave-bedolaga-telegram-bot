from app.api.domain.schemas.exception.base import ErrorResponse, AppException


def exception_response(exc_class: type[AppException]) -> dict:
    """Генерирует описание для OpenAPI по классу исключения"""
    return {
        "description": exc_class.detail,
        "model": ErrorResponse,
        "content": {
            "application/json": {
                "example": {"detail": exc_class.detail, "code": exc_class.code}
            }
        },
    }