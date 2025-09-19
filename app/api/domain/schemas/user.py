import datetime
from typing import Optional

from pydantic import BaseModel

from app.api.domain.schemas.base import Page


class UserInfoShort(BaseModel):
    id: int
    telegram_id: int
    username: str
    balance_kopeks: int

class UserInfo(BaseModel):
    id: int
    telegram_id: int
    username: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    status: str
    language: str
    balance_kopeks: int
    used_promocodes: int
    has_had_paid_subscription: bool
    referred_by_id: Optional[int]
    referral_code: Optional[str]
    created_at: datetime.datetime
    updated_at: datetime.datetime
    last_activity: datetime.datetime
    remnawave_uuid: Optional[str]
    lifetime_used_traffic_bytes: int
    last_remnawave_sync: Optional[int]
    trojan_password: Optional[int]
    vless_uuid: Optional[int]
    ss_password: Optional[int]
    has_made_first_topup: bool


class UserBalanceUpdate(BaseModel):
    new_balance: int
