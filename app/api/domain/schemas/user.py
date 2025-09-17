from typing import Optional

from pydantic import BaseModel


class UserInfoResponse(BaseModel):
    id: int
    telegram_id: int
    username: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    language: Optional[str]