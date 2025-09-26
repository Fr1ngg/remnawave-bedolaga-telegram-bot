"""Serialization helpers for the web admin API."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import List, Optional

from app.database.models import (
    AdvertisingCampaign,
    PromoCode,
    PromoGroup,
    UserMessage,
    WelcomeText,
    ServerSquad,
    Subscription,
    Ticket,
    TicketMessage,
    Transaction,
    User,
)


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
    type: str
    amount_kopeks: int
    amount_rub: float
    description: Optional[str]
    payment_method: Optional[str]
    created_at: Optional[str]
    is_completed: bool


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


@dataclass(slots=True)
class TicketMessageView:
    id: int
    is_admin: bool
    text: str
    has_media: bool
    media_type: Optional[str]
    created_at: Optional[str]


@dataclass(slots=True)
class TicketView:
    id: int
    title: str
    status: str
    status_emoji: str
    priority: str
    created_at: Optional[str]
    updated_at: Optional[str]
    closed_at: Optional[str]
    user: dict
    unread_messages: int
    messages: Optional[List[TicketMessageView]] = None


@dataclass(slots=True)
class PromoCodeView:
    id: int
    code: str
    type: str
    is_active: bool
    max_uses: int
    current_uses: int
    valid_from: Optional[str]
    valid_until: Optional[str]
    balance_bonus_kopeks: int
    subscription_days: int


@dataclass(slots=True)
class PromoGroupView:
    id: int
    name: str
    is_default: bool
    server_discount_percent: int
    traffic_discount_percent: int
    device_discount_percent: int
    users_count: int


@dataclass(slots=True)
class CampaignView:
    id: int
    name: str
    start_parameter: str
    bonus_type: str
    is_active: bool
    created_at: Optional[str]
    registrations_count: int


@dataclass(slots=True)
class WelcomeTextView:
    id: int
    text_content: str
    is_active: bool
    is_enabled: bool
    created_at: Optional[str]
    updated_at: Optional[str]


@dataclass(slots=True)
class UserMessageView:
    id: int
    message_text: str
    is_active: bool
    sort_order: int
    created_at: Optional[str]
    updated_at: Optional[str]


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
    view = TransactionView(
        id=transaction.id,
        type=transaction.type,
        amount_kopeks=transaction.amount_kopeks,
        amount_rub=_round_currency(transaction.amount_kopeks),
        description=transaction.description,
        payment_method=transaction.payment_method,
        created_at=_isoformat(transaction.created_at),
        is_completed=bool(transaction.is_completed),
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


def serialize_subscription_with_user(subscription: Subscription) -> dict:
    """Serialize subscription together with a lightweight user summary."""

    payload = serialize_subscription(subscription)
    user = subscription.user
    if user:
        payload["user"] = {
            "id": user.id,
            "telegram_id": user.telegram_id,
            "full_name": user.full_name,
            "username": user.username,
            "status": user.status,
        }
    payload["is_active"] = subscription.is_active
    payload["actual_status"] = subscription.actual_status
    payload["connected_squads"] = list(subscription.connected_squads or [])
    return payload


def serialize_ticket(ticket: Ticket, *, include_messages: bool = False) -> dict:
    user = ticket.user
    payload = asdict(
        TicketView(
            id=ticket.id,
            title=ticket.title,
            status=ticket.status,
            status_emoji=ticket.status_emoji,
            priority=ticket.priority,
            created_at=_isoformat(ticket.created_at),
            updated_at=_isoformat(ticket.updated_at),
            closed_at=_isoformat(ticket.closed_at),
            user={
                "id": user.id if user else None,
                "telegram_id": user.telegram_id if user else None,
                "full_name": user.full_name if user else None,
                "username": user.username if user else None,
            },
            unread_messages=sum(1 for message in ticket.messages if not message.is_from_admin),
            messages=None,
        )
    )

    if include_messages:
        payload["messages"] = [
            asdict(
                TicketMessageView(
                    id=message.id,
                    is_admin=message.is_from_admin,
                    text=message.message_text,
                    has_media=bool(message.has_media),
                    media_type=message.media_type,
                    created_at=_isoformat(message.created_at),
                )
            )
            for message in sorted(ticket.messages, key=lambda item: item.created_at or datetime.utcnow())
        ]
    return payload


def serialize_ticket_message(message: TicketMessage) -> dict:
    return asdict(
        TicketMessageView(
            id=message.id,
            is_admin=message.is_from_admin,
            text=message.message_text,
            has_media=bool(message.has_media),
            media_type=message.media_type,
            created_at=_isoformat(message.created_at),
        )
    )


def serialize_promocode(promocode: PromoCode) -> dict:
    return asdict(
        PromoCodeView(
            id=promocode.id,
            code=promocode.code,
            type=promocode.type,
            is_active=bool(promocode.is_active),
            max_uses=promocode.max_uses or 0,
            current_uses=promocode.current_uses or 0,
            valid_from=_isoformat(promocode.valid_from),
            valid_until=_isoformat(promocode.valid_until),
            balance_bonus_kopeks=promocode.balance_bonus_kopeks or 0,
            subscription_days=promocode.subscription_days or 0,
        )
    )


def serialize_promo_group(group: PromoGroup, *, users_count: int = 0) -> dict:
    return asdict(
        PromoGroupView(
            id=group.id,
            name=group.name,
            is_default=bool(group.is_default),
            server_discount_percent=group.server_discount_percent or 0,
            traffic_discount_percent=group.traffic_discount_percent or 0,
            device_discount_percent=group.device_discount_percent or 0,
            users_count=users_count,
        )
    )


def serialize_campaign(campaign: AdvertisingCampaign, *, registrations: int = 0) -> dict:
    return asdict(
        CampaignView(
            id=campaign.id,
            name=campaign.name,
            start_parameter=campaign.start_parameter,
            bonus_type=campaign.bonus_type,
            is_active=bool(campaign.is_active),
            created_at=_isoformat(campaign.created_at),
            registrations_count=registrations,
        )
    )


def serialize_welcome_text(entry: WelcomeText) -> dict:
    return asdict(
        WelcomeTextView(
            id=entry.id,
            text_content=entry.text_content,
            is_active=bool(entry.is_active),
            is_enabled=bool(entry.is_enabled),
            created_at=_isoformat(entry.created_at),
            updated_at=_isoformat(entry.updated_at),
        )
    )


def serialize_user_message(entry: UserMessage) -> dict:
    return asdict(
        UserMessageView(
            id=entry.id,
            message_text=entry.message_text,
            is_active=bool(entry.is_active),
            sort_order=entry.sort_order or 0,
            created_at=_isoformat(entry.created_at),
            updated_at=_isoformat(entry.updated_at),
        )
    )
