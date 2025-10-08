from __future__ import annotations

from typing import AsyncGenerator, Optional, Any

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import AsyncSessionLocal
from app.database.models import AdminUser, WebApiToken
from app.services.web_api_token_service import web_api_token_service


api_key_header_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def require_api_token(
    request: Request,
    api_key_header: str | None = Security(api_key_header_scheme),
    db: AsyncSession = Depends(get_db_session),
) -> Any:
    # --- Helper: minimal JWT verification to allow admin Bearer JWT for all endpoints ---
    # Duplicated logic with app/webapi/routes/auth.py to avoid circular imports.
    import base64
    import hashlib
    import hmac
    import json
    from datetime import datetime, timezone

    def _b64decode_url(data: str) -> bytes:
        pad = "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode((data + pad).encode("ascii"))

    def _verify_jwt(token: str, secret: str) -> Optional[dict]:
        try:
            header_b64, payload_b64, sig_b64 = token.split(".")
            signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
            expected = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
            if not hmac.compare_digest(expected, _b64decode_url(sig_b64)):
                return None
            payload = json.loads(_b64decode_url(payload_b64))
            if int(payload.get("exp", 0)) < int(datetime.now(timezone.utc).timestamp()):
                return None
            return payload
        except Exception:
            return None

    # Keep in sync with JWT secret used in auth routes
    JWT_SECRET = "bedolaga-web-admin-secret"

    # Read candidate credentials from headers or query
    bearer_cred: Optional[str] = None
    api_key: Optional[str] = api_key_header

    authorization = request.headers.get("Authorization")
    if authorization:
        scheme, _, credentials = authorization.partition(" ")
        if scheme.lower() == "bearer" and credentials:
            bearer_cred = credentials

    # Also support passing token via query param (used by EventSource which can't set headers)
    if not api_key:
        try:
            api_key = request.query_params.get("api_key")  # type: ignore[attr-defined]
        except Exception:
            api_key = None

    class _AdminActor:
        def __init__(self, username: str, user_id: Optional[int]):
            self.name = username
            self.user_id = user_id
            self.is_active = True

        def __repr__(self) -> str:  # pragma: no cover
            return f"<AdminActor username='{self.name}'>"

    async def _actor_from_payload(payload: dict) -> _AdminActor:
        username_hint = str(payload.get("username") or payload.get("sub") or "admin")
        user_id: Optional[int] = None
        if payload.get("sub") is not None:
            try:
                user_id = int(payload["sub"])
            except Exception:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")

        admin = None
        if user_id is not None:
            result = await db.execute(select(AdminUser).where(AdminUser.id == user_id))
            admin = result.scalar_one_or_none()

        if not admin and payload.get("username"):
            username_candidate = str(payload["username"]).strip().lower()
            result = await db.execute(select(AdminUser).where(AdminUser.username == username_candidate))
            admin = result.scalar_one_or_none()
            if admin:
                user_id = admin.id

        if not admin:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Admin not found")

        return _AdminActor(username=admin.username or username_hint, user_id=user_id)

    # 1) Prefer JWT if provided explicitly via Authorization header
    if bearer_cred and bearer_cred.count(".") == 2:
        payload = _verify_jwt(bearer_cred, JWT_SECRET)
        if not payload:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
        return await _actor_from_payload(payload)

    # 2) If query provides something that looks like a JWT, accept it the same way (for SSE)
    if api_key and api_key.count(".") == 2:
        payload = _verify_jwt(api_key, JWT_SECRET)
        if not payload:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
        return await _actor_from_payload(payload)

    # 3) Fall back to legacy API key token
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key",
        )

    token = await web_api_token_service.authenticate(
        db,
        api_key,
        remote_ip=request.client.host if request.client else None,
    )

    if not token:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired API key",
        )

    await db.commit()
    return token
