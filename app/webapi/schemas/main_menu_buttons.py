from __future__ import annotations

from datetime import datetime
from typing import Optional

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, validator

from app.database.models import MainMenuButtonActionType, MainMenuButtonVisibility


def _clean_text(value: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValueError("Text cannot be empty")
    return cleaned


def _validate_action_value(value: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValueError("Action value cannot be empty")
    if not cleaned.lower().startswith(("http://", "https://")):
        raise ValueError("Action value must start with http:// or https://")
    return cleaned


class MainMenuButtonResponse(BaseModel):
    id: int
    text: str
    action_type: MainMenuButtonActionType
    action_value: str
    visibility: MainMenuButtonVisibility
    is_active: bool
    display_order: int
    created_at: datetime
    updated_at: datetime


class MainMenuButtonCreateRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=64)
    action_type: MainMenuButtonActionType
    action_value: str = Field(..., min_length=1, max_length=1024)
    visibility: MainMenuButtonVisibility = MainMenuButtonVisibility.ALL
    is_active: bool = True
    display_order: Optional[int] = Field(None, ge=0)

    _normalize_text = validator("text", allow_reuse=True)(_clean_text)
    _normalize_action_value = validator("action_value", allow_reuse=True)(_validate_action_value)


class MainMenuButtonUpdateRequest(BaseModel):
    text: Optional[str] = Field(None, min_length=1, max_length=64)
    action_type: Optional[MainMenuButtonActionType] = None
    action_value: Optional[str] = Field(None, min_length=1, max_length=1024)
    visibility: Optional[MainMenuButtonVisibility] = None
    is_active: Optional[bool] = None
    display_order: Optional[int] = Field(None, ge=0)

    @validator("text")
    def validate_text(cls, value):  # noqa: D401,B902
        if value is None:
            return value
        return _clean_text(value)

    @validator("action_value")
    def validate_action_value(cls, value):  # noqa: D401,B902
        if value is None:
            return value
        return _validate_action_value(value)


class MainMenuButtonListResponse(BaseModel):
    items: list[MainMenuButtonResponse]
    total: int
    limit: int
    offset: int
