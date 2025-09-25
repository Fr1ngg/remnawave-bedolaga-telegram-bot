import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import AdminApiAuditLog, AdminApiClient

SUPPORTED_AUTH_TYPES = {"api_key", "bearer", "basic", "cookie"}
TOKEN_PREFIX_LENGTH = 16


def _normalize_str_list(values: Optional[Sequence[str]]) -> Optional[List[str]]:
    if not values:
        return None
    normalized: List[str] = []
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            normalized.append(text)
    return normalized or None


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _build_token_metadata(secret: str) -> Tuple[str, str]:
    digest = _hash_secret(secret)
    return digest[:TOKEN_PREFIX_LENGTH], digest


def _current_utc() -> datetime:
    return datetime.utcnow()


@dataclass(slots=True)
class CreatedClient:
    client: AdminApiClient
    token: Optional[str] = None
    basic_password: Optional[str] = None
    cookie_value: Optional[str] = None


async def create_admin_api_client(
    db: AsyncSession,
    *,
    name: str,
    description: Optional[str] = None,
    auth_type: str = "api_key",
    secret: Optional[str] = None,
    basic_username: Optional[str] = None,
    cookie_key: Optional[str] = None,
    allowed_origins: Optional[Sequence[str]] = None,
    allowed_ips: Optional[Sequence[str]] = None,
    permissions: Optional[Sequence[str]] = None,
    metadata: Optional[dict] = None,
) -> CreatedClient:
    auth_type_normalized = auth_type.lower().strip()
    if auth_type_normalized not in SUPPORTED_AUTH_TYPES:
        raise ValueError(f"Unsupported auth_type: {auth_type}")

    secret_value: Optional[str] = secret.strip() if isinstance(secret, str) else secret

    if auth_type_normalized in {"api_key", "bearer"}:
        if not secret_value:
            secret_value = secrets.token_hex(32)
        returned_token = secret_value
        basic_password = None
        cookie_plain = None
    elif auth_type_normalized == "basic":
        if not basic_username or not basic_username.strip():
            raise ValueError("basic_username is required for basic auth clients")
        if not secret_value:
            raise ValueError("secret (password) is required for basic auth clients")
        password_plain = secret_value
        secret_value = f"{basic_username.strip()}:{password_plain}"
        returned_token = None
        basic_password = password_plain
        cookie_plain = None
    else:  # cookie auth
        if not cookie_key or not cookie_key.strip():
            raise ValueError("cookie_key is required for cookie auth clients")
        if not secret_value:
            raise ValueError("secret (cookie value) is required for cookie auth clients")
        returned_token = None
        basic_password = None
        cookie_plain = secret_value

    token_prefix, token_hash = _build_token_metadata(secret_value or "")

    client = AdminApiClient(
        name=name,
        description=description,
        auth_type=auth_type_normalized,
        token_prefix=token_prefix,
        token_hash=token_hash,
        basic_username=basic_username.strip() if basic_username and auth_type_normalized == "basic" else None,
        cookie_key=cookie_key.strip() if cookie_key and auth_type_normalized == "cookie" else None,
        cookie_value_hash=token_hash if auth_type_normalized == "cookie" else None,
        allowed_origins=_normalize_str_list(allowed_origins),
        allowed_ips=_normalize_str_list(allowed_ips),
        permissions=_normalize_str_list(permissions),
        metadata_json=metadata or None,
    )

    db.add(client)
    try:
        await db.commit()
    except IntegrityError as error:
        await db.rollback()
        raise ValueError("Failed to create admin api client due to integrity error") from error

    await db.refresh(client)

    return CreatedClient(
        client=client,
        token=returned_token,
        basic_password=basic_password,
        cookie_value=cookie_plain,
    )


async def list_admin_api_clients(db: AsyncSession, *, include_inactive: bool = True) -> List[AdminApiClient]:
    query = select(AdminApiClient).order_by(AdminApiClient.created_at.asc())
    if not include_inactive:
        query = query.where(AdminApiClient.is_active.is_(True))
    result = await db.execute(query)
    return result.scalars().all()


async def get_admin_api_client_by_id(db: AsyncSession, client_id: int) -> Optional[AdminApiClient]:
    result = await db.execute(
        select(AdminApiClient)
        .options(selectinload(AdminApiClient.audit_logs))
        .where(AdminApiClient.id == client_id)
    )
    return result.scalar_one_or_none()


async def delete_admin_api_client(db: AsyncSession, client: AdminApiClient) -> None:
    await db.delete(client)
    await db.commit()


async def _get_candidates_by_prefix(
    db: AsyncSession,
    *,
    prefix: str,
    auth_type: str,
    extra_conditions: Optional[Iterable] = None,
) -> List[AdminApiClient]:
    conditions = [
        AdminApiClient.token_prefix == prefix,
        AdminApiClient.auth_type == auth_type,
        AdminApiClient.is_active.is_(True),
    ]
    if extra_conditions:
        conditions.extend(extra_conditions)

    stmt = select(AdminApiClient).where(and_(*conditions))
    result = await db.execute(stmt)
    return result.scalars().all()


async def get_active_client_by_token(
    db: AsyncSession,
    *,
    token: str,
    auth_type: str,
    basic_username: Optional[str] = None,
) -> Optional[AdminApiClient]:
    prefix, digest = _build_token_metadata(token)
    candidates = await _get_candidates_by_prefix(
        db,
        prefix=prefix,
        auth_type=auth_type,
        extra_conditions=[AdminApiClient.basic_username == basic_username] if basic_username else None,
    )
    for candidate in candidates:
        if hmac.compare_digest(candidate.token_hash, digest):
            return candidate
    return None


async def get_active_client_by_cookie(
    db: AsyncSession,
    *,
    cookie_key: str,
    cookie_value: str,
) -> Optional[AdminApiClient]:
    prefix, digest = _build_token_metadata(cookie_value)
    candidates = await _get_candidates_by_prefix(
        db,
        prefix=prefix,
        auth_type="cookie",
        extra_conditions=[AdminApiClient.cookie_key == cookie_key],
    )
    for candidate in candidates:
        stored_hash = candidate.cookie_value_hash or candidate.token_hash
        if hmac.compare_digest(stored_hash, digest):
            return candidate
    return None


async def record_client_usage(
    db: AsyncSession,
    client: AdminApiClient,
    *,
    ip_address: Optional[str],
    user_agent: Optional[str],
) -> AdminApiClient:
    client.last_used_at = _current_utc()
    client.last_used_ip = (ip_address or "").strip() or None
    client.last_user_agent = (user_agent or "").strip() or None
    await db.commit()
    await db.refresh(client)
    return client


async def update_admin_api_client(
    db: AsyncSession,
    client: AdminApiClient,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    is_active: Optional[bool] = None,
    allowed_origins: Optional[Sequence[str]] = None,
    allowed_ips: Optional[Sequence[str]] = None,
    permissions: Optional[Sequence[str]] = None,
    metadata: Optional[dict] = None,
) -> AdminApiClient:
    if name is not None:
        client.name = name
    if description is not None:
        client.description = description
    if is_active is not None:
        client.is_active = bool(is_active)
    if allowed_origins is not None:
        client.allowed_origins = _normalize_str_list(allowed_origins)
    if allowed_ips is not None:
        client.allowed_ips = _normalize_str_list(allowed_ips)
    if permissions is not None:
        client.permissions = _normalize_str_list(permissions)
    if metadata is not None:
        client.metadata_json = metadata or None
    client.updated_at = _current_utc()
    await db.commit()
    await db.refresh(client)
    return client


async def create_audit_log(
    db: AsyncSession,
    *,
    client: Optional[AdminApiClient],
    token_prefix: Optional[str],
    auth_type: Optional[str],
    method: str,
    path: str,
    status_code: int,
    ip_address: Optional[str],
    user_agent: Optional[str],
    response_time_ms: Optional[float],
    metadata: Optional[dict] = None,
) -> AdminApiAuditLog:
    log = AdminApiAuditLog(
        client_id=client.id if client else None,
        token_prefix=token_prefix,
        auth_type=auth_type,
        method=method,
        path=path,
        status_code=status_code,
        ip_address=(ip_address or "").strip() or None,
        user_agent=(user_agent or "").strip() or None,
        response_time_ms=response_time_ms,
        metadata_json=metadata or None,
        created_at=_current_utc(),
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)
    return log


async def list_audit_logs(
    db: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
    client_id: Optional[int] = None,
    token_prefix: Optional[str] = None,
    status_code: Optional[int] = None,
    auth_type: Optional[str] = None,
) -> List[AdminApiAuditLog]:
    stmt = select(AdminApiAuditLog).order_by(AdminApiAuditLog.created_at.desc())

    conditions = []
    if client_id is not None:
        conditions.append(AdminApiAuditLog.client_id == client_id)
    if token_prefix:
        conditions.append(AdminApiAuditLog.token_prefix == token_prefix)
    if status_code is not None:
        conditions.append(AdminApiAuditLog.status_code == status_code)
    if auth_type:
        conditions.append(AdminApiAuditLog.auth_type == auth_type)

    if conditions:
        stmt = stmt.where(and_(*conditions))

    stmt = stmt.offset(offset).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


async def count_audit_logs(
    db: AsyncSession,
    *,
    client_id: Optional[int] = None,
    token_prefix: Optional[str] = None,
    status_code: Optional[int] = None,
    auth_type: Optional[str] = None,
) -> int:
    stmt = select(func.count(AdminApiAuditLog.id))

    conditions = []
    if client_id is not None:
        conditions.append(AdminApiAuditLog.client_id == client_id)
    if token_prefix:
        conditions.append(AdminApiAuditLog.token_prefix == token_prefix)
    if status_code is not None:
        conditions.append(AdminApiAuditLog.status_code == status_code)
    if auth_type:
        conditions.append(AdminApiAuditLog.auth_type == auth_type)

    if conditions:
        stmt = stmt.where(and_(*conditions))

    result = await db.execute(stmt)
    return int(result.scalar() or 0)
