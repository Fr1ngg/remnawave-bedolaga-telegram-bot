from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MiniAppBranding(BaseModel):
    service_name: Dict[str, Optional[str]] = Field(default_factory=dict)
    service_description: Dict[str, Optional[str]] = Field(default_factory=dict)


class MiniAppSubscriptionRequest(BaseModel):
    init_data: str = Field(..., alias="initData")


class MiniAppSubscriptionUser(BaseModel):
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    display_name: str
    language: Optional[str] = None
    status: str
    subscription_status: str
    subscription_actual_status: str
    status_label: str
    expires_at: Optional[datetime] = None
    device_limit: Optional[int] = None
    traffic_used_gb: float = 0.0
    traffic_used_label: str
    traffic_limit_gb: Optional[int] = None
    traffic_limit_label: str
    lifetime_used_traffic_gb: float = 0.0
    has_active_subscription: bool = False


class MiniAppPromoGroup(BaseModel):
    id: int
    name: str


class MiniAppConnectedServer(BaseModel):
    uuid: str
    name: str


class MiniAppDevice(BaseModel):
    platform: Optional[str] = None
    device_model: Optional[str] = None
    app_version: Optional[str] = None
    last_seen: Optional[str] = None
    last_ip: Optional[str] = None


class MiniAppTransaction(BaseModel):
    id: int
    type: str
    amount_kopeks: int
    amount_rubles: float
    description: Optional[str] = None
    payment_method: Optional[str] = None
    external_id: Optional[str] = None
    is_completed: bool
    created_at: datetime
    completed_at: Optional[datetime] = None


class MiniAppSubscriptionResponse(BaseModel):
    success: bool = True
    subscription_id: int
    remnawave_short_uuid: Optional[str] = None
    user: MiniAppSubscriptionUser
    subscription_url: Optional[str] = None
    subscription_crypto_link: Optional[str] = None
    subscription_purchase_url: Optional[str] = None
    links: List[str] = Field(default_factory=list)
    ss_conf_links: Dict[str, str] = Field(default_factory=dict)
    connected_squads: List[str] = Field(default_factory=list)
    connected_servers: List[MiniAppConnectedServer] = Field(default_factory=list)
    connected_devices_count: int = 0
    connected_devices: List[MiniAppDevice] = Field(default_factory=list)
    happ: Optional[Dict[str, Any]] = None
    happ_link: Optional[str] = None
    happ_crypto_link: Optional[str] = None
    happ_cryptolink_redirect_link: Optional[str] = None
    balance_kopeks: int = 0
    balance_rubles: float = 0.0
    balance_currency: Optional[str] = None
    transactions: List[MiniAppTransaction] = Field(default_factory=list)
    promo_group: Optional[MiniAppPromoGroup] = None
    subscription_type: str
    autopay_enabled: bool = False
    branding: Optional[MiniAppBranding] = None

