"""Expose helpdesk/ticket functionality for the web admin."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.database.models import Ticket, TicketMessage, TicketStatus
from app.webadmin.serializers import serialize_ticket, serialize_ticket_message


async def fetch_support_overview(session: AsyncSession) -> Dict[str, Any]:
    """Return counters for the support dashboard."""

    total_open = await session.scalar(
        select(func.count(Ticket.id)).where(Ticket.status == TicketStatus.OPEN.value)
    ) or 0
    total_answered = await session.scalar(
        select(func.count(Ticket.id)).where(Ticket.status == TicketStatus.ANSWERED.value)
    ) or 0
    total_pending = await session.scalar(
        select(func.count(Ticket.id)).where(Ticket.status == TicketStatus.PENDING.value)
    ) or 0
    total_closed = await session.scalar(
        select(func.count(Ticket.id)).where(Ticket.status == TicketStatus.CLOSED.value)
    ) or 0

    day_ago = datetime.utcnow() - timedelta(days=1)
    new_last_day = await session.scalar(
        select(func.count(Ticket.id)).where(Ticket.created_at >= day_ago)
    ) or 0

    return {
        "open": int(total_open),
        "answered": int(total_answered),
        "pending": int(total_pending),
        "closed": int(total_closed),
        "new_last_24h": int(new_last_day),
    }


async def fetch_tickets(
    session: AsyncSession,
    *,
    status: Optional[str] = None,
    page: int = 1,
    limit: int = 20,
) -> Dict[str, Any]:
    """Return paginated tickets."""

    page = max(page, 1)
    limit = max(1, min(limit, 100))
    offset = (page - 1) * limit

    query = select(Ticket).options(joinedload(Ticket.user)).order_by(desc(Ticket.updated_at))
    count_query = select(func.count(Ticket.id))

    if status:
        query = query.where(Ticket.status == status)
        count_query = count_query.where(Ticket.status == status)

    total = await session.scalar(count_query) or 0
    rows = await session.execute(query.offset(offset).limit(limit))

    return {
        "items": [serialize_ticket(ticket) for ticket in rows.scalars().all()],
        "pagination": {
            "page": page,
            "limit": limit,
            "total": int(total),
        },
    }


async def fetch_ticket_details(session: AsyncSession, ticket_id: int) -> Dict[str, Any]:
    """Return ticket with full message history."""

    result = await session.execute(
        select(Ticket)
        .options(
            joinedload(Ticket.user),
            joinedload(Ticket.messages).joinedload(TicketMessage.user),
        )
        .where(Ticket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        return {}

    return serialize_ticket(ticket, include_messages=True)


async def fetch_ticket_messages(
    session: AsyncSession,
    ticket_id: int,
    *,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """Return messages for a ticket (useful for incremental loading)."""

    rows = await session.execute(
        select(TicketMessage)
        .where(TicketMessage.ticket_id == ticket_id)
        .order_by(TicketMessage.created_at.asc())
        .offset(offset)
        .limit(limit)
    )
    messages = rows.scalars().all()
    return {
        "items": [serialize_ticket_message(message) for message in messages],
        "pagination": {
            "limit": limit,
            "offset": offset,
            "count": len(messages),
        },
    }
