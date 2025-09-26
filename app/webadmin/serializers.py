"""Serialization helpers for the web admin API."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from app.database.models import ServerSquad, Subscription, Transaction, User


def _isoformat(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=None).isoformat()
    return value.isoformat()


def _round_currency(kopeks: Optional[int]) -> float:
    if not kopeks:
        return 0.0
    return round(kopeks / 100, 2)


@dataclass(slots=True)
class SubscriptionView:
    id: int
    status: str
    status_display: str
    is_trial: bool
    start_date: Optional[str]
    end_date: Optional[str]
    days_left: Optional[int]
    traffic_limit_gb: int
    traffic_used_gb: float
    device_limit: int
    autopay_enabled: bool


@dataclass(slots=True)
class UserView:
    id: int
    telegram_id: int
    username: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    full_name: str
    status: str
    language: Optional[str]
    balance_kopeks: int
    balance_rub: float
    created_at: Optional[str]
    updated_at: Optional[str]
    last_activity: Optional[str]
    referral_code: Optional[str]
    has_had_paid_subscription: bool
    promo_group_id: Optional[int]
    subscription: Optional[SubscriptionView]


@dataclass(slots=True)
class TransactionView:
    id: int
    user_id: int
    type: str
    amount_kopeks: int
    amount_rub: float
    description: Optional[str]
    payment_method: Optional[str]
    created_at: Optional[str]
    is_completed: bool
    user: Optional[Dict[str, Any]] = None


@dataclass(slots=True)
class ServerView:
    id: int
    squad_uuid: str
    name: str
    original_name: Optional[str]
    country_code: Optional[str]
    is_available: bool
    status_label: str
    current_users: int
    max_users: Optional[int]
    price_kopeks: int
    price_rub: float
    description: Optional[str]
    updated_at: Optional[str]
    capacity_percent: float


def serialize_subscription(subscription: Subscription) -> SubscriptionView:
    now = datetime.utcnow()
    end_date = subscription.end_date
    days_left: Optional[int] = None
    if end_date:
        remaining = end_date - now
        days_left = max(0, int(remaining.total_seconds() // 86400))

    return SubscriptionView(
        id=subscription.id,
        status=subscription.status,
        status_display=subscription.status_display,
        is_trial=subscription.is_trial,
        start_date=_isoformat(subscription.start_date),
        end_date=_isoformat(subscription.end_date),
        days_left=days_left,
        traffic_limit_gb=subscription.traffic_limit_gb or 0,
        traffic_used_gb=float(subscription.traffic_used_gb or 0.0),
        device_limit=subscription.device_limit or 0,
        autopay_enabled=bool(subscription.autopay_enabled),
    )


def serialize_user(user: User) -> dict:
    subscription_view: Optional[SubscriptionView] = None
    if user.subscription:
        subscription_view = serialize_subscription(user.subscription)

    view = UserView(
        id=user.id,
        telegram_id=user.telegram_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        full_name=user.full_name,
        status=user.status,
        language=user.language,
        balance_kopeks=user.balance_kopeks or 0,
        balance_rub=_round_currency(user.balance_kopeks or 0),
        created_at=_isoformat(user.created_at),
        updated_at=_isoformat(user.updated_at),
        last_activity=_isoformat(user.last_activity),
        referral_code=user.referral_code,
        has_had_paid_subscription=bool(user.has_had_paid_subscription),
        promo_group_id=user.promo_group_id,
        subscription=subscription_view,
    )

    return asdict(view)


def serialize_transaction(transaction: Transaction) -> dict:
    related_user = transaction.__dict__.get("user")
    user_data: Optional[Dict[str, Any]] = None
    if isinstance(related_user, User):
        user_data = {
            "id": related_user.id,
            "telegram_id": related_user.telegram_id,
            "username": related_user.username,
            "full_name": related_user.full_name,
        }

    view = TransactionView(
        id=transaction.id,
        user_id=transaction.user_id,
        type=transaction.type,
        amount_kopeks=transaction.amount_kopeks,
        amount_rub=_round_currency(transaction.amount_kopeks),
        description=transaction.description,
        payment_method=transaction.payment_method,
        created_at=_isoformat(transaction.created_at),
        is_completed=bool(transaction.is_completed),
        user=user_data,
    )
    return asdict(view)


def serialize_server(server: ServerSquad) -> dict:
    max_users = server.max_users or 0
    capacity_percent = 0.0
    if max_users and server.current_users is not None:
        capacity_percent = round(min(100.0, (server.current_users / max_users) * 100), 2)

    view = ServerView(
        id=server.id,
        squad_uuid=server.squad_uuid,
        name=server.display_name,
        original_name=server.original_name,
        country_code=server.country_code,
        is_available=bool(server.is_available),
        status_label=server.availability_status,
        current_users=server.current_users or 0,
        max_users=server.max_users,
        price_kopeks=server.price_kopeks or 0,
        price_rub=_round_currency(server.price_kopeks or 0),
        description=server.description,
        updated_at=_isoformat(server.updated_at),
        capacity_percent=capacity_percent,
    )
    return asdict(view)
