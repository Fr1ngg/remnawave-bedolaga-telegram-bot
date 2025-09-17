from fastapi import Header

from app.api.domain.schemas.exception.global_exc import AuthException
from app.config import settings

async def get_available_auth(
    x_api_key: str = Header(...)
):
    if settings.TOKEN_BOT_API == x_api_key:
        return True
    raise AuthException