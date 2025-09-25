from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import WebAdminSession


async def create_session(
    db: AsyncSession,
    *,
    token: str,
    refresh_token: str,
    expires_at: datetime,
    ip_address: Optional[str],
    user_agent: Optional[str],
) -> WebAdminSession:
    session = WebAdminSession(
        token=token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(session)
    await db.flush()
    await db.refresh(session)
    return session


async def get_session_by_token(db: AsyncSession, token: str) -> Optional[WebAdminSession]:
    result = await db.execute(
        select(WebAdminSession).where(WebAdminSession.token == token)
    )
    return result.scalar_one_or_none()


async def get_session_by_refresh_token(
    db: AsyncSession, refresh_token: str
) -> Optional[WebAdminSession]:
    result = await db.execute(
        select(WebAdminSession).where(WebAdminSession.refresh_token == refresh_token)
    )
    return result.scalar_one_or_none()


async def revoke_session(db: AsyncSession, session: WebAdminSession) -> None:
    session.revoked_at = datetime.utcnow()
    await db.flush()


async def purge_expired_sessions(db: AsyncSession, *, now: datetime | None = None) -> int:
    if now is None:
        now = datetime.utcnow()

    result = await db.execute(
        select(WebAdminSession).where(WebAdminSession.expires_at < now)
    )
    expired = result.scalars().all()
    count = 0
    for session in expired:
        await db.delete(session)
        count += 1

    if count:
        await db.flush()

    return count
