from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud import webadmin_session as webadmin_session_crud
from app.database.models import WebAdminSession


logger = logging.getLogger(__name__)


class WebAdminAuthService:
    """Сервис управления сессиями веб-админки."""

    TOKEN_BYTES: int = 32
    REFRESH_TOKEN_BYTES: int = 48

    def verify_credentials(self, username: str, password: str) -> bool:
        expected_username = settings.WEBADMIN_USERNAME or ""
        if not expected_username or username.strip() != expected_username:
            return False

        expected_hash = settings.get_webadmin_password_hash()
        if not expected_hash:
            logger.warning("Попытка входа без настроенного пароля веб-админки")
            return False

        provided_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return secrets.compare_digest(provided_hash, expected_hash)

    def _make_token(self, size: int) -> str:
        return secrets.token_urlsafe(size)

    def _get_expiration(self) -> datetime:
        ttl_minutes = max(settings.WEBADMIN_SESSION_TTL_MINUTES, 1)
        return datetime.utcnow() + timedelta(minutes=ttl_minutes)

    async def purge_expired(self, db: AsyncSession) -> int:
        return await webadmin_session_crud.purge_expired_sessions(db)

    async def create_session(
        self,
        db: AsyncSession,
        *,
        ip_address: Optional[str],
        user_agent: Optional[str],
    ) -> WebAdminSession:
        token = self._make_token(self.TOKEN_BYTES)
        refresh = self._make_token(self.REFRESH_TOKEN_BYTES)
        expires_at = self._get_expiration()

        session = await webadmin_session_crud.create_session(
            db,
            token=token,
            refresh_token=refresh,
            expires_at=expires_at,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return session

    async def rotate_session(
        self,
        db: AsyncSession,
        session: WebAdminSession,
    ) -> WebAdminSession:
        session.token = self._make_token(self.TOKEN_BYTES)
        session.refresh_token = self._make_token(self.REFRESH_TOKEN_BYTES)
        session.expires_at = self._get_expiration()
        session.refreshed_at = datetime.utcnow()
        session.revoked_at = None
        await db.flush()
        await db.refresh(session)
        return session

    def is_session_active(self, session: WebAdminSession) -> bool:
        if session.revoked_at is not None:
            return False
        return session.expires_at > datetime.utcnow()

    async def revoke_session(self, db: AsyncSession, session: WebAdminSession) -> None:
        await webadmin_session_crud.revoke_session(db, session)


webadmin_auth_service = WebAdminAuthService()
