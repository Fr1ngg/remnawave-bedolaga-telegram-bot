from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PromoOfferUserInfo(BaseModel):
    id: int
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    full_name: Optional[str] = None


class PromoOfferSubscriptionInfo(BaseModel):
    id: int
    status: str
    is_trial: bool
    start_date: datetime
    end_date: datetime
    autopay_enabled: bool


class PromoOfferResponse(BaseModel):
    id: int
    user_id: int
    subscription_id: Optional[int] = None
    notification_type: str
    discount_percent: int
    bonus_amount_kopeks: int
    expires_at: datetime
    claimed_at: Optional[datetime] = None
    is_active: bool
    effect_type: str
    extra_data: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    user: Optional[PromoOfferUserInfo] = None
    subscription: Optional[PromoOfferSubscriptionInfo] = None


class PromoOfferListResponse(BaseModel):
    items: List[PromoOfferResponse]
    total: int
    limit: int
    offset: int


class PromoOfferCreateRequest(BaseModel):
    user_id: int
    notification_type: str = Field(..., min_length=1)
    valid_hours: int = Field(..., ge=1, description="Срок действия предложения в часах")
    discount_percent: int = Field(0, ge=0)
    bonus_amount_kopeks: int = Field(0, ge=0)
    subscription_id: Optional[int] = None
    effect_type: str = Field("percent_discount", min_length=1)
    extra_data: Dict[str, Any] = Field(default_factory=dict)


class PromoOfferTemplateResponse(BaseModel):
    id: int
    name: str
    offer_type: str
    message_text: str
    button_text: str
    valid_hours: int
    discount_percent: int
    bonus_amount_kopeks: int
    active_discount_hours: Optional[int] = None
    test_duration_hours: Optional[int] = None
    test_squad_uuids: List[str]
    is_active: bool
    created_by: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class PromoOfferTemplateListResponse(BaseModel):
    items: List[PromoOfferTemplateResponse]


class PromoOfferTemplateUpdateRequest(BaseModel):
    name: Optional[str] = None
    message_text: Optional[str] = None
    button_text: Optional[str] = None
    valid_hours: Optional[int] = Field(None, ge=1)
    discount_percent: Optional[int] = Field(None, ge=0)
    bonus_amount_kopeks: Optional[int] = Field(None, ge=0)
    active_discount_hours: Optional[int] = Field(None, ge=1)
    test_duration_hours: Optional[int] = Field(None, ge=1)
    test_squad_uuids: Optional[List[str]] = None
    is_active: Optional[bool] = None


class PromoOfferLogOfferInfo(BaseModel):
    id: int
    notification_type: Optional[str] = None
    discount_percent: Optional[int] = None
    bonus_amount_kopeks: Optional[int] = None
    effect_type: Optional[str] = None
    expires_at: Optional[datetime] = None
    claimed_at: Optional[datetime] = None
    is_active: Optional[bool] = None


class PromoOfferLogResponse(BaseModel):
    id: int
    user_id: Optional[int] = None
    offer_id: Optional[int] = None
    action: str
    source: Optional[str] = None
    percent: Optional[int] = None
    effect_type: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    user: Optional[PromoOfferUserInfo] = None
    offer: Optional[PromoOfferLogOfferInfo] = None


class PromoOfferLogListResponse(BaseModel):
    items: List[PromoOfferLogResponse]
    total: int
    limit: int
    offset: int
