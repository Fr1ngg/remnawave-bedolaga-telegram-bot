from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Security, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.crud.promo_group import get_promo_group_by_id
from app.database.crud.user import (
    add_user_balance,
    create_user,
    get_user_by_id,
    get_user_by_referral_code,
    get_user_by_telegram_id,
    update_user,
)
from app.database.models import PromoGroup, Subscription, User, UserStatus

from ..dependencies import get_db_session, require_api_token
from .notifications import broker
from ..schemas.users import (
    BalanceUpdateRequest,
    PromoGroupSummary,
    SubscriptionSummary,
    UserCreateRequest,
    UserListResponse,
    UserResponse,
    UserUpdateRequest,
)

router = APIRouter()


def _serialize_promo_group(group: Optional[PromoGroup]) -> Optional[PromoGroupSummary]:
    if not group:
        return None
    return PromoGroupSummary(
        id=group.id,
        name=group.name,
        server_discount_percent=group.server_discount_percent,
        traffic_discount_percent=group.traffic_discount_percent,
        device_discount_percent=group.device_discount_percent,
        apply_discounts_to_addons=getattr(group, "apply_discounts_to_addons", True),
    )


def _serialize_subscription(subscription: Optional[Subscription]) -> Optional[SubscriptionSummary]:
    if not subscription:
        return None

    return SubscriptionSummary(
        id=subscription.id,
        status=subscription.status,
        actual_status=subscription.actual_status,
        is_trial=subscription.is_trial,
        start_date=subscription.start_date,
        end_date=subscription.end_date,
        traffic_limit_gb=subscription.traffic_limit_gb,
        traffic_used_gb=subscription.traffic_used_gb,
        device_limit=subscription.device_limit,
        autopay_enabled=subscription.autopay_enabled,
        autopay_days_before=subscription.autopay_days_before,
        subscription_url=subscription.subscription_url,
        subscription_crypto_link=subscription.subscription_crypto_link,
        connected_squads=list(subscription.connected_squads or []),
    )


def _serialize_user(user: User) -> UserResponse:
    subscription = getattr(user, "subscription", None)
    promo_group = getattr(user, "promo_group", None)

    return UserResponse(
        id=user.id,
        telegram_id=user.telegram_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        status=user.status,
        language=user.language,
        balance_kopeks=user.balance_kopeks,
        balance_rubles=round(user.balance_kopeks / 100, 2),
        referral_code=user.referral_code,
        referred_by_id=user.referred_by_id,
        has_had_paid_subscription=user.has_had_paid_subscription,
        has_made_first_topup=user.has_made_first_topup,
        created_at=user.created_at,
        updated_at=user.updated_at,
        last_activity=user.last_activity,
        promo_group=_serialize_promo_group(promo_group),
        subscription=_serialize_subscription(subscription),
    )


def _apply_search_filter(query, search: str):
    search_lower = f"%{search.lower()}%"
    conditions = [
        func.lower(User.username).like(search_lower),
        func.lower(User.first_name).like(search_lower),
        func.lower(User.last_name).like(search_lower),
        func.lower(User.referral_code).like(search_lower),
    ]

    if search.isdigit():
        conditions.append(User.telegram_id == int(search))
        conditions.append(User.id == int(search))

    return query.where(or_(*conditions))


@router.get("", response_model=UserListResponse)
async def list_users(
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status_filter: Optional[UserStatus] = Query(default=None, alias="status"),
    promo_group_id: Optional[int] = Query(default=None),
    search: Optional[str] = Query(default=None),
) -> UserListResponse:
    base_query = (
        select(User)
        .options(
            selectinload(User.subscription),
            selectinload(User.promo_group),
        )
    )

    if status_filter:
        base_query = base_query.where(User.status == status_filter.value)

    if promo_group_id:
        base_query = base_query.where(User.promo_group_id == promo_group_id)

    if search:
        base_query = _apply_search_filter(base_query, search)

    total_query = base_query.with_only_columns(func.count()).order_by(None)
    total = await db.scalar(total_query) or 0

    result = await db.execute(
        base_query.order_by(User.created_at.desc()).offset(offset).limit(limit)
    )
    users = result.scalars().unique().all()

    return UserListResponse(
        items=[_serialize_user(user) for user in users],
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    return _serialize_user(user)


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user_endpoint(
    payload: UserCreateRequest,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    existing = await get_user_by_telegram_id(db, payload.telegram_id)
    if existing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "User with this telegram_id already exists")

    user = await create_user(
        db,
        telegram_id=payload.telegram_id,
        username=payload.username,
        first_name=payload.first_name,
        last_name=payload.last_name,
        language=payload.language,
        referred_by_id=payload.referred_by_id,
    )

    if payload.promo_group_id and payload.promo_group_id != user.promo_group_id:
        promo_group = await get_promo_group_by_id(db, payload.promo_group_id)
        if not promo_group:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Promo group not found")
        user = await update_user(db, user, promo_group_id=promo_group.id)

    user = await get_user_by_id(db, user.id)
    try:
        await broker.publish("users.update")
    except Exception:
        pass
    return _serialize_user(user)


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user_endpoint(
    user_id: int,
    payload: UserUpdateRequest,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    updates: dict[str, Any] = {}

    if payload.username is not None:
        updates["username"] = payload.username
    if payload.first_name is not None:
        updates["first_name"] = payload.first_name
    if payload.last_name is not None:
        updates["last_name"] = payload.last_name
    if payload.language is not None:
        updates["language"] = payload.language
    if payload.has_had_paid_subscription is not None:
        updates["has_had_paid_subscription"] = payload.has_had_paid_subscription
    if payload.has_made_first_topup is not None:
        updates["has_made_first_topup"] = payload.has_made_first_topup

    if payload.status is not None:
        try:
            status_value = UserStatus(payload.status).value
        except ValueError as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid status") from error
        updates["status"] = status_value

    if payload.promo_group_id is not None:
        promo_group = await get_promo_group_by_id(db, payload.promo_group_id)
        if not promo_group:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Promo group not found")
        updates["promo_group_id"] = promo_group.id

    if payload.referral_code is not None and payload.referral_code != user.referral_code:
        existing_code_owner = await get_user_by_referral_code(db, payload.referral_code)
        if existing_code_owner and existing_code_owner.id != user.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Referral code already in use")
        updates["referral_code"] = payload.referral_code

    if not updates:
        return _serialize_user(user)

    user = await update_user(db, user, **updates)
    user = await get_user_by_id(db, user.id)
    try:
        await broker.publish("users.update")
    except Exception:
        pass
    return _serialize_user(user)


@router.post("/{user_id}/balance", response_model=UserResponse)
async def update_balance(
    user_id: int,
    payload: BalanceUpdateRequest,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    if payload.amount_kopeks == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Amount must be non-zero")

    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    success = await add_user_balance(
        db,
        user,
        amount_kopeks=payload.amount_kopeks,
        description=payload.description or "Корректировка через веб-API",
        create_transaction=payload.create_transaction,
    )

    if not success:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to update balance")

    user = await get_user_by_id(db, user_id)
    try:
        await broker.publish("users.update")
    except Exception:
        pass
    return _serialize_user(user)
