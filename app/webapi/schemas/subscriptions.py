from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class SubscriptionResponse(BaseModel):
    id: int
    user_id: int
    status: str
    actual_status: str
    is_trial: bool
    start_date: datetime
    end_date: datetime
    traffic_limit_gb: int
    traffic_used_gb: float
    device_limit: int
    autopay_enabled: bool
    autopay_days_before: int
    subscription_url: Optional[str] = None
    subscription_crypto_link: Optional[str] = None
    connected_squads: List[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class SubscriptionCreateRequest(BaseModel):
    user_id: int
    is_trial: bool = False
    duration_days: Optional[int] = None
    traffic_limit_gb: Optional[int] = None
    device_limit: Optional[int] = None
    squad_uuid: Optional[str] = None
    connected_squads: Optional[List[str]] = None
    replace_existing: bool = False


class SubscriptionExtendRequest(BaseModel):
    days: int = Field(..., gt=0)


class SubscriptionTrafficRequest(BaseModel):
    gb: int = Field(..., gt=0)


class SubscriptionDevicesRequest(BaseModel):
    devices: int = Field(..., gt=0)


class SubscriptionSquadRequest(BaseModel):
    squad_uuid: str
