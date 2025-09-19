import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class SubscriptionInfo(BaseModel):
    id: int
    user_id: int
    status: str
    is_trial: bool
    start_date: datetime.datetime
    end_date: datetime.datetime
    traffic_limit_gb: int
    device_limit: int
    subscription_url: Optional[str]
    connected_squads: List[str] = Field(default_factory=list)
    remnawave_short_uuid: Optional[str]

    class Config:
        orm_mode = True


class SubscriptionExtendRequest(BaseModel):
    days: int = Field(..., ge=1, description="Количество дней для продления подписки")
