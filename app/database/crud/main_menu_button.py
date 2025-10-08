from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    MainMenuButton,
    MainMenuButtonActionType,
    MainMenuButtonVisibility,
)


async def count_main_menu_buttons(db: AsyncSession) -> int:
    result = await db.execute(select(func.count()).select_from(MainMenuButton))
    return int(result.scalar() or 0)


async def get_main_menu_buttons(
    db: AsyncSession,
    *,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
) -> list[MainMenuButton]:
    stmt = select(MainMenuButton).order_by(
        MainMenuButton.display_order.asc(),
        MainMenuButton.id.asc(),
    )

    if offset is not None:
        stmt = stmt.offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)

    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_main_menu_button_by_id(
    db: AsyncSession, button_id: int
) -> MainMenuButton | None:
    result = await db.execute(
        select(MainMenuButton).where(MainMenuButton.id == button_id)
    )
    return result.scalar_one_or_none()


async def get_next_display_order(db: AsyncSession) -> int:
    result = await db.execute(select(func.max(MainMenuButton.display_order)))
    current_max = result.scalar()
    return (int(current_max) if current_max is not None else 0) + 1


def _enum_value(value, enum_cls):
    if value is None:
        return None
    if isinstance(value, enum_cls):
        return value.value
    return str(value)


async def create_main_menu_button(
    db: AsyncSession,
    *,
    text: str,
    action_type: MainMenuButtonActionType | str,
    action_value: str,
    visibility: MainMenuButtonVisibility | str = MainMenuButtonVisibility.ALL,
    is_active: bool = True,
    display_order: Optional[int] = None,
) -> MainMenuButton:
    if display_order is None:
        display_order = await get_next_display_order(db)

    button = MainMenuButton(
        text=text,
        action_type=_enum_value(action_type, MainMenuButtonActionType),
        action_value=action_value,
        visibility=_enum_value(visibility, MainMenuButtonVisibility)
        or MainMenuButtonVisibility.ALL.value,
        is_active=bool(is_active),
        display_order=int(display_order),
    )

    db.add(button)
    await db.commit()
    await db.refresh(button)
    return button


async def update_main_menu_button(
    db: AsyncSession,
    button: MainMenuButton,
    *,
    text: Optional[str] = None,
    action_type: MainMenuButtonActionType | str | None = None,
    action_value: Optional[str] = None,
    visibility: MainMenuButtonVisibility | str | None = None,
    is_active: Optional[bool] = None,
    display_order: Optional[int] = None,
) -> MainMenuButton:
    if text is not None:
        button.text = text
    if action_type is not None:
        button.action_type = _enum_value(action_type, MainMenuButtonActionType)
    if action_value is not None:
        button.action_value = action_value
    if visibility is not None:
        button.visibility = _enum_value(visibility, MainMenuButtonVisibility)
    if is_active is not None:
        button.is_active = bool(is_active)
    if display_order is not None:
        button.display_order = int(display_order)

    await db.commit()
    await db.refresh(button)
    return button


async def delete_main_menu_button(db: AsyncSession, button: MainMenuButton) -> None:
    await db.delete(button)
    await db.commit()


async def reorder_main_menu_buttons(
    db: AsyncSession,
    ordered_ids: Sequence[int],
) -> None:
    if not ordered_ids:
        return

    order_map = {int(button_id): index for index, button_id in enumerate(ordered_ids)}

    result = await db.execute(
        select(MainMenuButton).where(MainMenuButton.id.in_(order_map.keys()))
    )
    buttons = result.scalars().all()

    for button in buttons:
        desired_order = order_map.get(button.id)
        if desired_order is not None:
            button.display_order = desired_order

    await db.commit()
