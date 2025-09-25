from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud import webadmin_session as webadmin_session_crud
from app.database.database import get_db
from app.database.models import (
    Subscription,
    SubscriptionStatus,
    Transaction,
    User,
    WebAdminSession,
)
from app.services.system_settings_service import bot_configuration_service
from app.services.webadmin_auth_service import webadmin_auth_service

from .schemas import (
    AuthLoginRequest,
    AuthResponse,
    BotConfigurationHealth,
    DashboardSummary,
    DatabaseHealth,
    HealthResponse,
    LogoutResponse,
    RefreshRequest,
    SessionInfoResponse,
    SettingItem,
    SettingResponse,
    SettingsCategoriesResponse,
    SettingsCategory,
    SettingsListResponse,
    SettingChoice,
    UpdateSettingRequest,
)
from .security import get_current_session


router = APIRouter(prefix="/api", tags=["webadmin"])


def ensure_webadmin_enabled() -> None:
    if not settings.is_webadmin_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Web admin interface is disabled",
        )


def _build_setting_item(definition) -> SettingItem:
    key = definition.key
    current = bot_configuration_service.get_current_value(key)
    original = bot_configuration_service.get_original_value(key)
    value_raw = bot_configuration_service.serialize_value(key, current)

    choices = [
        SettingChoice(value=option.value, label=option.label, description=option.description)
        for option in bot_configuration_service.get_choice_options(key)
    ]

    # Для JSON сериализации возвращаем базовые типы
    if current is not None and definition.python_type not in {bool, int, float, str}:
        current_value = value_raw
    else:
        current_value = current

    if original is not None and definition.python_type not in {bool, int, float, str}:
        original_value = bot_configuration_service.serialize_value(key, original)
    else:
        original_value = original

    return SettingItem(
        key=key,
        name=definition.display_name,
        category_key=definition.category_key,
        category_label=definition.category_label,
        type=definition.type_label,
        is_optional=definition.is_optional,
        value=current_value,
        value_display=bot_configuration_service.format_value(current),
        value_raw=value_raw,
        value_preview=bot_configuration_service.format_value_for_list(key),
        original=original_value,
        original_display=bot_configuration_service.format_value(original),
        has_override=bot_configuration_service.has_override(key),
        choices=choices,
    )


async def _coerce_setting_value(
    key: str,
    definition,
    payload: UpdateSettingRequest,
) -> Optional[object]:
    if payload.raw_value is not None:
        return bot_configuration_service.parse_user_value(key, payload.raw_value)

    value = payload.value

    if value is None:
        if definition.is_optional:
            return None
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Value is required")

    if isinstance(value, bool):
        text = "true" if value else "false"
        return bot_configuration_service.parse_user_value(key, text)

    if isinstance(value, (int, float)):
        return bot_configuration_service.parse_user_value(key, str(value))

    if isinstance(value, str):
        return bot_configuration_service.parse_user_value(key, value)

    return bot_configuration_service.parse_user_value(key, str(value))


@router.get("/health", response_model=HealthResponse)
async def get_health(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    try:
        result = await db.execute(select(1))
        _ = result.scalar_one_or_none()
        database_ok = True
        db_message = None
    except Exception as error:  # pragma: no cover - defensive
        database_ok = False
        db_message = str(error)

    total_settings = bot_configuration_service.count_settings()
    overrides_total = bot_configuration_service.count_overrides()

    dialect_name: Optional[str]
    try:
        bind = db.bind
        dialect_name = bind.dialect.name if bind is not None else None
    except AttributeError:  # pragma: no cover - defensive
        dialect_name = None

    return HealthResponse(
        status="ok" if database_ok else "error",
        timestamp=datetime.utcnow(),
        database=DatabaseHealth(
            ok=database_ok,
            dialect=dialect_name if database_ok else None,
            message=db_message,
        ),
        bot_configuration=BotConfigurationHealth(
            ok=total_settings > 0,
            settings_total=total_settings,
            overrides_total=overrides_total,
        ),
    )


@router.post("/auth/login", response_model=AuthResponse)
async def login(
    payload: AuthLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    ensure_webadmin_enabled()

    if not webadmin_auth_service.verify_credentials(payload.username, payload.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    await webadmin_auth_service.purge_expired(db)

    client = request.client
    ip_address = client.host if client else None
    user_agent = request.headers.get("User-Agent")

    session = await webadmin_auth_service.create_session(
        db,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    return AuthResponse(
        username=payload.username,
        token=session.token,
        refresh_token=session.refresh_token,
        expires_at=session.expires_at,
    )


@router.post("/auth/refresh", response_model=AuthResponse)
async def refresh_token(
    payload: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    ensure_webadmin_enabled()

    session = await webadmin_session_crud.get_session_by_refresh_token(db, payload.refresh_token)
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    if not webadmin_auth_service.is_session_active(session):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")

    session = await webadmin_auth_service.rotate_session(db, session)

    return AuthResponse(
        username=settings.WEBADMIN_USERNAME or "admin",
        token=session.token,
        refresh_token=session.refresh_token,
        expires_at=session.expires_at,
    )


@router.post("/auth/logout", response_model=LogoutResponse)
async def logout(
    current_session: WebAdminSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
) -> LogoutResponse:
    ensure_webadmin_enabled()

    await webadmin_auth_service.revoke_session(db, current_session)
    return LogoutResponse(status="ok", timestamp=datetime.utcnow())


@router.get("/auth/session", response_model=SessionInfoResponse)
async def get_session_info(
    current_session: WebAdminSession = Depends(get_current_session),
) -> SessionInfoResponse:
    ensure_webadmin_enabled()

    return SessionInfoResponse(
        username=settings.WEBADMIN_USERNAME or "admin",
        expires_at=current_session.expires_at,
        issued_at=current_session.created_at,
    )


@router.get("/dashboard/summary", response_model=DashboardSummary)
async def get_dashboard_summary(
    current_session: WebAdminSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
) -> DashboardSummary:
    ensure_webadmin_enabled()

    users_total = await _scalar(db, select(func.count(User.id)))

    active_subscriptions = await _scalar(
        db,
        select(func.count(Subscription.id)).where(
            Subscription.status == SubscriptionStatus.ACTIVE.value
        ),
    )

    trial_subscriptions = await _scalar(
        db,
        select(func.count(Subscription.id)).where(
            Subscription.status == SubscriptionStatus.TRIAL.value
        ),
    )

    total_revenue = await _scalar(
        db,
        select(func.coalesce(func.sum(Transaction.amount_kopeks), 0)).where(
            Transaction.is_completed.is_(True)
        ),
    )

    settings_total = bot_configuration_service.count_settings()
    overrides_total = bot_configuration_service.count_overrides()
    categories_total = len(bot_configuration_service.get_categories())

    return DashboardSummary(
        generated_at=datetime.utcnow(),
        users_total=users_total,
        active_subscriptions=active_subscriptions,
        trial_subscriptions=trial_subscriptions,
        total_revenue_kopeks=total_revenue,
        total_revenue_rub=total_revenue / 100,
        settings_total=settings_total,
        overrides_total=overrides_total,
        categories_total=categories_total,
    )


async def _scalar(db: AsyncSession, stmt: Select) -> int:
    result = await db.execute(stmt)
    value = result.scalar_one_or_none()
    return int(value or 0)


@router.get("/settings/categories", response_model=SettingsCategoriesResponse)
async def list_settings_categories(
    current_session: WebAdminSession = Depends(get_current_session),
) -> SettingsCategoriesResponse:
    ensure_webadmin_enabled()

    categories_data = []
    total_settings = 0
    overrides_total = 0

    for key, label, count in bot_configuration_service.get_categories():
        total_settings += count
        definitions = bot_configuration_service.get_settings_for_category(key)
        overrides = sum(1 for definition in definitions if bot_configuration_service.has_override(definition.key))
        overrides_total += overrides

        categories_data.append(
            SettingsCategory(key=key, label=label, total=count, overrides=overrides)
        )

    return SettingsCategoriesResponse(
        categories=categories_data,
        total=total_settings,
        overrides_total=overrides_total,
    )


@router.get("/settings", response_model=SettingsListResponse)
async def list_settings(
    current_session: WebAdminSession = Depends(get_current_session),
    category: Optional[str] = None,
    search: Optional[str] = None,
) -> SettingsListResponse:
    ensure_webadmin_enabled()

    if category:
        definitions = bot_configuration_service.get_settings_for_category(category)
    else:
        definitions = bot_configuration_service.list_definitions()

    items: List[SettingItem] = []
    search_lower = (search or "").strip().lower()

    for definition in definitions:
        item = _build_setting_item(definition)
        if search_lower:
            haystack = f"{item.key} {item.name}".lower()
            if search_lower not in haystack:
                continue
        items.append(item)

    items.sort(key=lambda setting: setting.key)

    return SettingsListResponse(
        items=items,
        category_key=category,
        total=len(items),
    )


@router.get("/settings/{key}", response_model=SettingResponse)
async def get_setting(
    key: str,
    current_session: WebAdminSession = Depends(get_current_session),
) -> SettingResponse:
    ensure_webadmin_enabled()

    try:
        definition = bot_configuration_service.get_definition(key)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Setting not found")

    return SettingResponse(setting=_build_setting_item(definition))


@router.put("/settings/{key}", response_model=SettingResponse)
async def update_setting(
    key: str,
    payload: UpdateSettingRequest,
    current_session: WebAdminSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
) -> SettingResponse:
    ensure_webadmin_enabled()

    try:
        definition = bot_configuration_service.get_definition(key)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Setting not found")

    value = await _coerce_setting_value(key, definition, payload)
    await bot_configuration_service.set_value(db, key, value)

    return SettingResponse(setting=_build_setting_item(definition))


@router.delete("/settings/{key}", response_model=SettingResponse)
async def reset_setting(
    key: str,
    current_session: WebAdminSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
) -> SettingResponse:
    ensure_webadmin_enabled()

    try:
        definition = bot_configuration_service.get_definition(key)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Setting not found")

    await bot_configuration_service.reset_value(db, key)
    return SettingResponse(setting=_build_setting_item(definition))
