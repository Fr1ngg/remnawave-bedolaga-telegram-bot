from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.database.models import PromoCodeType


class PromoCodeResponse(BaseModel):
    id: int
    code: str
    type: PromoCodeType
    balance_bonus_kopeks: int
    balance_bonus_rubles: float
    subscription_days: int
    max_uses: int
    current_uses: int
    uses_left: int
    is_active: bool
    is_valid: bool
    valid_from: datetime
    valid_until: Optional[datetime] = None
    created_by: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class PromoCodeListResponse(BaseModel):
    items: list[PromoCodeResponse]
    total: int
    limit: int
    offset: int


class PromoCodeCreateRequest(BaseModel):
    code: str
    type: PromoCodeType
    balance_bonus_kopeks: int = 0
    subscription_days: int = 0
    max_uses: int = Field(default=1, ge=0)
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    is_active: bool = True
    created_by: Optional[int] = None


class PromoCodeUpdateRequest(BaseModel):
    code: Optional[str] = None
    type: Optional[PromoCodeType] = None
    balance_bonus_kopeks: Optional[int] = None
    subscription_days: Optional[int] = None
    max_uses: Optional[int] = Field(default=None, ge=0)
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    is_active: Optional[bool] = None


class PromoCodeRecentUse(BaseModel):
    id: int
    user_id: int
    user_username: Optional[str] = None
    user_full_name: Optional[str] = None
    user_telegram_id: Optional[int] = None
    used_at: datetime


class PromoCodeDetailResponse(PromoCodeResponse):
    total_uses: int
    today_uses: int
    recent_uses: list[PromoCodeRecentUse] = Field(default_factory=list)

