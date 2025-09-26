"""Expose communication settings (broadcast messages, welcome text) for the web admin."""

from __future__ import annotations

from sqlalchemy import asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import UserMessage, WelcomeText
from app.webadmin.serializers import serialize_user_message, serialize_welcome_text


async def fetch_communications_overview(session: AsyncSession) -> dict:
    active_welcome = await session.scalar(
        select(func.count(WelcomeText.id)).where(WelcomeText.is_enabled.is_(True))
    ) or 0
    active_user_messages = await session.scalar(
        select(func.count(UserMessage.id)).where(UserMessage.is_active.is_(True))
    ) or 0

    return {
        "welcome": {
            "active": int(active_welcome),
        },
        "user_messages": {
            "active": int(active_user_messages),
        },
    }


async def fetch_welcome_texts(session: AsyncSession) -> dict:
    rows = await session.execute(
        select(WelcomeText).order_by(desc(WelcomeText.created_at))
    )
    return {
        "items": [serialize_welcome_text(entry) for entry in rows.scalars().all()],
    }


async def fetch_user_messages(session: AsyncSession) -> dict:
    rows = await session.execute(
        select(UserMessage).order_by(asc(UserMessage.sort_order), desc(UserMessage.created_at))
    )
    return {
        "items": [serialize_user_message(entry) for entry in rows.scalars().all()],
    }
