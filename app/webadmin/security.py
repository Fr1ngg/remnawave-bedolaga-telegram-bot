from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud import webadmin_session as webadmin_session_crud
from app.database.database import get_db
from app.database.models import WebAdminSession
from app.services.webadmin_auth_service import webadmin_auth_service


bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_session(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
)-> WebAdminSession:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    session = await webadmin_session_crud.get_session_by_token(db, credentials.credentials)
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    if not webadmin_auth_service.is_session_active(session):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")

    return session
