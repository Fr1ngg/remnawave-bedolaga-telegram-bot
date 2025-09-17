from starlette import status
from pydantic import BaseModel


class ErrorResponse(BaseModel):
    detail: str
    code: str | None = None   # например, business_error_code

class AppException(Exception):
    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "app_error"
    detail: str = "Application error"

    def __init__(self, detail: str | None = None):
        if detail:
            self.detail = detail