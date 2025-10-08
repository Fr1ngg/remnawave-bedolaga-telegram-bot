from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import List

from aiogram import types
from aiogram.types import InlineKeyboardButton
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    MainMenuButton,
    MainMenuButtonActionType,
    MainMenuButtonVisibility,
)


@dataclass(frozen=True)
class _MainMenuButtonData:
    text: str
    action_type: MainMenuButtonActionType
    action_value: str
    visibility: MainMenuButtonVisibility
    is_active: bool
    display_order: int


class MainMenuButtonService:
    _cache: List[_MainMenuButtonData] | None = None
    _lock: asyncio.Lock = asyncio.Lock()

    @classmethod
    def invalidate_cache(cls) -> None:
        cls._cache = None

    @classmethod
    async def _load_cache(cls, db: AsyncSession) -> List[_MainMenuButtonData]:
        if cls._cache is not None:
            return cls._cache

        async with cls._lock:
            if cls._cache is not None:
                return cls._cache

            result = await db.execute(
                select(MainMenuButton).order_by(
                    MainMenuButton.display_order.asc(),
                    MainMenuButton.id.asc(),
                )
            )

            items: List[_MainMenuButtonData] = []
            for record in result.scalars().all():
                text = (record.text or "").strip()
                action_value = (record.action_value or "").strip()

                if not text or not action_value:
                    continue

                try:
                    action_type = MainMenuButtonActionType(record.action_type)
                except ValueError:
                    continue

                try:
                    visibility = MainMenuButtonVisibility(record.visibility)
                except ValueError:
                    visibility = MainMenuButtonVisibility.ALL

                items.append(
                    _MainMenuButtonData(
                        text=text,
                        action_type=action_type,
                        action_value=action_value,
                        visibility=visibility,
                        is_active=bool(record.is_active),
                        display_order=int(record.display_order or 0),
                    )
                )

            cls._cache = items
            return items

    @classmethod
    async def get_buttons_for_user(
        cls,
        db: AsyncSession,
        *,
        is_admin: bool,
        has_active_subscription: bool,
        subscription_is_active: bool,
    ) -> list[InlineKeyboardButton]:
        data = await cls._load_cache(db)
        has_subscription = bool(has_active_subscription and subscription_is_active)

        buttons: list[InlineKeyboardButton] = []
        for item in data:
            if not item.is_active:
                continue

            if item.visibility == MainMenuButtonVisibility.ADMINS and not is_admin:
                continue

            if item.visibility == MainMenuButtonVisibility.SUBSCRIBERS and not has_subscription:
                continue

            button = cls._build_button(item)
            if button:
                buttons.append(button)

        return buttons

    @staticmethod
    def _build_button(item: _MainMenuButtonData) -> InlineKeyboardButton | None:
        if item.action_type == MainMenuButtonActionType.URL:
            return InlineKeyboardButton(text=item.text, url=item.action_value)

        if item.action_type == MainMenuButtonActionType.MINI_APP:
            return InlineKeyboardButton(
                text=item.text,
                web_app=types.WebAppInfo(url=item.action_value),
            )

        return None
