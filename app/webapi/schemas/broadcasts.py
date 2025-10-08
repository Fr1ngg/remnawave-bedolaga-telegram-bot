from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Optional

from pydantic import BaseModel, Field, validator

from app.keyboards.admin import BROADCAST_BUTTONS, DEFAULT_BROADCAST_BUTTONS


class BroadcastMedia(BaseModel):
    type: str = Field(pattern=r"^(photo|video|document)$")
    file_id: str
    caption: Optional[str] = None


class BroadcastCreateRequest(BaseModel):
    target: str
    message_text: str = Field(..., min_length=1, max_length=4000)
    selected_buttons: list[str] = Field(
        default_factory=lambda: list(DEFAULT_BROADCAST_BUTTONS)
    )
    media: Optional[BroadcastMedia] = None

    _ALLOWED_TARGETS: ClassVar[set[str]] = {
        "all",
        "active",
        "trial",
        "no",
        "expiring",
        "expired",
        "active_zero",
        "trial_zero",
        "zero",
    }
    _CUSTOM_TARGETS: ClassVar[set[str]] = {
        "today",
        "week",
        "month",
        "active_today",
        "inactive_week",
        "inactive_month",
        "referrals",
        "direct",
    }
    _TARGET_ALIASES: ClassVar[dict[str, str]] = {
        "no_sub": "no",
    }

    @validator("target")
    def validate_target(cls, value: str) -> str:
        normalized = value.strip().lower()
        normalized = cls._TARGET_ALIASES.get(normalized, normalized)

        if normalized in cls._ALLOWED_TARGETS:
            return normalized

        if normalized.startswith("custom_"):
            criteria = normalized[len("custom_"):]
            if criteria in cls._CUSTOM_TARGETS:
                return normalized

        raise ValueError("Unsupported target value")

    @validator("selected_buttons", pre=True)
    def validate_selected_buttons(cls, value):
        if value is None:
            return []
        if not isinstance(value, (list, tuple)):
            raise TypeError("selected_buttons must be an array")

        seen = set()
        ordered: list[str] = []
        for raw_button in value:
            button = str(raw_button).strip()
            if not button:
                continue
            if button not in BROADCAST_BUTTONS:
                raise ValueError(f"Unsupported button '{button}'")
            if button in seen:
                continue
            ordered.append(button)
            seen.add(button)
        return ordered


class BroadcastResponse(BaseModel):
    id: int
    target_type: str
    message_text: str
    has_media: bool
    media_type: Optional[str] = None
    media_file_id: Optional[str] = None
    media_caption: Optional[str] = None
    total_count: int
    sent_count: int
    failed_count: int
    status: str
    admin_id: Optional[int] = None
    admin_name: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None


class BroadcastListResponse(BaseModel):
    items: list[BroadcastResponse]
    total: int
    limit: int
    offset: int

