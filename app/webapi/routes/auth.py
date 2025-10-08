from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Security, status, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import AdminUser
from app.webapi.schemas.auth import (
    AdminProfileResponse,
    AdminProfileUpdateRequest,
    AuthLoginRequest,
    AuthRegisterRequest,
    AuthTokenResponse,
    RegistrationStatusResponse,
)
from ..dependencies import get_db_session, require_api_token

router = APIRouter(prefix="/auth", tags=["auth"])


def _hash_password(password: str) -> str:
    # Простое sha256 с солью (на проде заменить на bcrypt/argon2)
    salt = b"bedolaga-static-salt"
    return hashlib.sha256(salt + password.encode("utf-8")).hexdigest()


def _create_jwt(payload: dict, secret: str, exp_minutes: int = 120) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    payload = dict(payload)
    payload["exp"] = int((datetime.now(timezone.utc) + timedelta(minutes=exp_minutes)).timestamp())

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    header_b64 = b64(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{b64(signature)}"


def _verify_jwt(token: str, secret: str) -> Optional[dict]:
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
        def b64d(s: str) -> bytes:
            pad = '=' * (-len(s) % 4)
            return base64.urlsafe_b64decode(s + pad)
        signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
        expected = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, b64d(sig_b64)):
            return None
        payload = json.loads(b64d(payload_b64))
        if int(payload.get("exp", 0)) < int(datetime.now(timezone.utc).timestamp()):
            return None
        return payload
    except Exception:
        return None


JWT_SECRET = "bedolaga-web-admin-secret"


@router.get("/can-register", response_model=RegistrationStatusResponse)
async def can_register(db: AsyncSession = Depends(get_db_session)) -> RegistrationStatusResponse:
    result = await db.execute(select(AdminUser.id))
    exists = result.first() is not None
    return RegistrationStatusResponse(can_register=not exists)


@router.post("/register", response_model=AdminProfileResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: AuthRegisterRequest, db: AsyncSession = Depends(get_db_session)) -> AdminProfileResponse:
    # Разрешаем регистрацию только если ещё нет ни одного администратора
    exists_result = await db.execute(select(AdminUser.id))
    if exists_result.first() is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Registration is disabled: admin already exists")
    username = payload.username.strip().lower()
    result = await db.execute(select(AdminUser).where(AdminUser.username == username))
    if result.scalar_one_or_none():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Username already exists")

    user = AdminUser(
        username=username,
        password_hash=_hash_password(payload.password),
        email=payload.email,
        name=payload.name,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return AdminProfileResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        name=user.name,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@router.post("/login", response_model=AuthTokenResponse)
async def login(payload: AuthLoginRequest, db: AsyncSession = Depends(get_db_session)) -> AuthTokenResponse:
    username = payload.username.strip().lower()
    result = await db.execute(select(AdminUser).where(AdminUser.username == username))
    user = result.scalar_one_or_none()
    if not user or user.password_hash != _hash_password(payload.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    token = _create_jwt({"sub": user.id, "username": user.username}, JWT_SECRET, exp_minutes=240)
    return AuthTokenResponse(access_token=token)


def _get_admin_from_token(token: str) -> Optional[int]:
    data = _verify_jwt(token, JWT_SECRET)
    if not data:
        return None
    return int(data.get("sub"))


@router.get("/me", response_model=AdminProfileResponse)
async def me(request: Request, db: AsyncSession = Depends(get_db_session)) -> AdminProfileResponse:
    # Читаем JWT из Authorization: Bearer <jwt>; допускаем фолбэк из X-API-Key, если там передали JWT
    token_str: Optional[str] = None
    auth_header = request.headers.get("Authorization") or ""
    scheme, _, credentials = auth_header.partition(" ")
    if scheme.lower() == "bearer" and credentials:
        token_str = credentials
    if not token_str:
        x_api = request.headers.get("X-API-Key") or ""
        if x_api.count(".") == 2:  # очень вероятно JWT
            token_str = x_api
    if not token_str:
        token_cookie = request.cookies.get("jwt")
        if token_cookie:
            token_str = token_cookie
    if not token_str:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    user_id = _get_admin_from_token(token_str)
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    result = await db.execute(select(AdminUser).where(AdminUser.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Admin not found")
    return AdminProfileResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        name=user.name,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@router.put("/me", response_model=AdminProfileResponse)
async def update_me(payload: AdminProfileUpdateRequest, request: Request, db: AsyncSession = Depends(get_db_session)) -> AdminProfileResponse:
    token_str: Optional[str] = None
    auth_header = request.headers.get("Authorization") or ""
    scheme, _, credentials = auth_header.partition(" ")
    if scheme.lower() == "bearer" and credentials:
        token_str = credentials
    if not token_str:
        x_api = request.headers.get("X-API-Key") or ""
        if x_api.count(".") == 2:
            token_str = x_api
    if not token_str:
        token_cookie = request.cookies.get("jwt")
        if token_cookie:
            token_str = token_cookie
    if not token_str:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    user_id = _get_admin_from_token(token_str)
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    result = await db.execute(select(AdminUser).where(AdminUser.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Admin not found")
    if payload.email is not None:
        user.email = payload.email
    if payload.name is not None:
        user.name = payload.name
    if payload.password:
        user.password_hash = _hash_password(payload.password)
    user.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(user)
    return AdminProfileResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        name=user.name,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@router.post("/reset-admin", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def reset_admin_http(request: Request, payload: AuthRegisterRequest, db: AsyncSession = Depends(get_db_session)) -> Response:
    token = request.headers.get("X-Admin-Reset-Token")
    expected = settings.get_admin_reset_token()
    if not expected or token != expected:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid reset token")
    allowlist = settings.get_admin_reset_ip_whitelist()
    client_ip = request.client.host if request.client else None
    if allowlist and client_ip not in allowlist:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "IP not allowed")

    # Recreate admin safely: delete all and create new
    from sqlalchemy import delete
    await db.execute(delete(AdminUser))
    user = AdminUser(
        username=payload.username.strip().lower(),
        password_hash=_hash_password(payload.password),
        email=payload.email,
        name=payload.name,
    )
    db.add(user)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
