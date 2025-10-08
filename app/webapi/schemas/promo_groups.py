from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from pydantic import BaseModel, Field, validator


def _normalize_period_discounts(value: Optional[Dict[object, object]]) -> Optional[Dict[int, int]]:
    if value is None:
        return None

    normalized: Dict[int, int] = {}
    if isinstance(value, dict):
        for raw_key, raw_value in value.items():
            try:
                key = int(raw_key)
                normalized[key] = int(raw_value)
            except (TypeError, ValueError):
                continue

    return normalized or None


class PromoGroupResponse(BaseModel):
    id: int
    name: str
    server_discount_percent: int
    traffic_discount_percent: int
    device_discount_percent: int
    period_discounts: Dict[int, int] = Field(default_factory=dict)
    auto_assign_total_spent_kopeks: Optional[int] = None
    apply_discounts_to_addons: bool
    is_default: bool
    members_count: int = 0
    created_at: datetime
    updated_at: datetime


class _PromoGroupBase(BaseModel):
    period_discounts: Optional[Dict[int, int]] = Field(
        default=None,
        description=(
            "Словарь скидок по длительности подписки. Ключ — количество месяцев, "
            "значение — скидка в процентах. Например: {1: 10, 6: 20}."
        ),
        example={1: 10, 6: 20},
    )

    @validator("period_discounts", pre=True)
    def validate_period_discounts(cls, value):  # noqa: D401,B902
        return _normalize_period_discounts(value)


class PromoGroupCreateRequest(_PromoGroupBase):
    name: str
    server_discount_percent: int = 0
    traffic_discount_percent: int = 0
    device_discount_percent: int = 0
    auto_assign_total_spent_kopeks: Optional[int] = None
    apply_discounts_to_addons: bool = True
    is_default: bool = False


class PromoGroupUpdateRequest(_PromoGroupBase):
    name: Optional[str] = None
    server_discount_percent: Optional[int] = None
    traffic_discount_percent: Optional[int] = None
    device_discount_percent: Optional[int] = None
    auto_assign_total_spent_kopeks: Optional[int] = None
    apply_discounts_to_addons: Optional[bool] = None
    is_default: Optional[bool] = None


class PromoGroupListResponse(BaseModel):
    items: list[PromoGroupResponse]
    total: int
    limit: int
    offset: int
