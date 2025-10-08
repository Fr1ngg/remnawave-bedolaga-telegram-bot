from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class TokenResponse(BaseModel):
    id: int
    name: str
    prefix: str = Field(..., description="Первые символы токена для идентификации")
    description: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    last_used_ip: Optional[str] = None
    created_by: Optional[str] = None


class TokenCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    expires_at: Optional[datetime] = None


class TokenCreateResponse(TokenResponse):
    token: str = Field(..., description="Полное значение токена (возвращается один раз)")
