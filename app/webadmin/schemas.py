from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class AuthLoginRequest(BaseModel):
    username: str = Field(..., description="Имя пользователя")
    password: str = Field(..., description="Пароль")


class AuthTokens(BaseModel):
    token: str
    refresh_token: str
    expires_at: datetime


class AuthResponse(AuthTokens):
    username: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutResponse(BaseModel):
    status: str = "ok"
    timestamp: datetime


class DatabaseHealth(BaseModel):
    ok: bool
    dialect: Optional[str] = None
    message: Optional[str] = None


class BotConfigurationHealth(BaseModel):
    ok: bool
    settings_total: int
    overrides_total: int


class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
    database: DatabaseHealth
    bot_configuration: BotConfigurationHealth


class DashboardSummary(BaseModel):
    generated_at: datetime
    users_total: int
    active_subscriptions: int
    trial_subscriptions: int
    total_revenue_kopeks: int
    total_revenue_rub: float
    settings_total: int
    overrides_total: int
    categories_total: int


class SettingChoice(BaseModel):
    value: Any
    label: str
    description: Optional[str] = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


class SettingItem(BaseModel):
    key: str
    name: str
    category_key: str
    category_label: str
    type: str
    is_optional: bool
    value: Any
    value_display: str
    value_raw: Optional[str]
    value_preview: str
    original: Any
    original_display: str
    has_override: bool
    choices: List[SettingChoice]

    model_config = ConfigDict(arbitrary_types_allowed=True)


class SettingsListResponse(BaseModel):
    items: List[SettingItem]
    category_key: Optional[str] = None
    total: int


class SettingResponse(BaseModel):
    setting: SettingItem


class SettingsCategory(BaseModel):
    key: str
    label: str
    total: int
    overrides: int


class SettingsCategoriesResponse(BaseModel):
    categories: List[SettingsCategory]
    total: int
    overrides_total: int


class UpdateSettingRequest(BaseModel):
    value: Any = None
    raw_value: Optional[str] = None

    model_config = ConfigDict(extra="ignore")


class SessionInfoResponse(BaseModel):
    username: str
    expires_at: datetime
    issued_at: datetime


class ErrorResponse(BaseModel):
    detail: str
