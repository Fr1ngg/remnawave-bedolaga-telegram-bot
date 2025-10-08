from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class RichTextPageResponse(BaseModel):
    """Generic representation for rich text informational pages."""

    requested_language: str = Field(..., description="Язык, запрошенный клиентом")
    language: str = Field(..., description="Фактический язык найденной записи")
    is_enabled: Optional[bool] = Field(
        default=None,
        description="Текущий статус публикации страницы (если применимо)",
    )
    content: str = Field(..., description="Полное содержимое страницы")
    content_pages: List[str] = Field(
        default_factory=list,
        description="Содержимое, разбитое на страницы фиксированной длины",
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="Дата создания записи",
    )
    updated_at: Optional[datetime] = Field(
        default=None,
        description="Дата последнего обновления записи",
    )


class RichTextPageUpdateRequest(BaseModel):
    language: str = Field(
        default="ru",
        min_length=2,
        max_length=10,
        description="Язык, для которого выполняется обновление",
    )
    content: str = Field(..., description="Новое содержимое страницы")
    is_enabled: Optional[bool] = Field(
        default=None,
        description="Если указано — обновить статус публикации",
    )


class FaqPageResponse(BaseModel):
    id: int
    language: str
    title: str
    content: str
    content_pages: List[str] = Field(default_factory=list)
    display_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class FaqPageListResponse(BaseModel):
    requested_language: str
    language: str
    is_enabled: bool
    total: int
    items: List[FaqPageResponse]


class FaqPageCreateRequest(BaseModel):
    language: str = Field(
        default="ru",
        min_length=2,
        max_length=10,
        description="Язык создаваемой страницы",
    )
    title: str = Field(..., min_length=1, max_length=255)
    content: str = Field(...)
    display_order: Optional[int] = Field(
        default=None,
        ge=0,
        description="Порядок отображения (если не указан — будет рассчитан автоматически)",
    )
    is_active: Optional[bool] = Field(
        default=True,
        description="Начальный статус активности страницы",
    )


class FaqPageUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    content: Optional[str] = None
    display_order: Optional[int] = Field(default=None, ge=0)
    is_active: Optional[bool] = None


class FaqReorderItem(BaseModel):
    id: int = Field(..., ge=1)
    display_order: int = Field(..., ge=0)


class FaqReorderRequest(BaseModel):
    language: str = Field(
        default="ru",
        min_length=2,
        max_length=10,
        description="Язык, для которого применяется сортировка",
    )
    items: List[FaqReorderItem]


class FaqStatusResponse(BaseModel):
    requested_language: str
    language: str
    is_enabled: bool


class FaqStatusUpdateRequest(BaseModel):
    language: str = Field(
        default="ru",
        min_length=2,
        max_length=10,
    )
    is_enabled: bool


class ServiceRulesResponse(BaseModel):
    id: int
    title: str
    content: str
    language: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ServiceRulesUpdateRequest(BaseModel):
    language: str = Field(
        default="ru",
        min_length=2,
        max_length=10,
        description="Язык, для которого обновляются правила",
    )
    title: Optional[str] = Field(
        default="Правила сервиса",
        min_length=1,
        max_length=255,
    )
    content: str = Field(...)


class ServiceRulesHistoryResponse(BaseModel):
    language: str
    total: int
    items: List[ServiceRulesResponse]

