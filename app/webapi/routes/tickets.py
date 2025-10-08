from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Security, status
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.ticket import TicketCRUD
from app.database.models import Ticket, TicketMessage, TicketStatus
from app.config import settings
from sqlalchemy import select
import aiohttp

from ..dependencies import get_db_session, require_api_token
from .notifications import broker
from ..schemas.tickets import (
    TicketMessageResponse,
    TicketPriorityUpdateRequest,
    TicketReplyBlockRequest,
    TicketResponse,
    TicketStatusUpdateRequest,
    TicketReplyRequest,
)

router = APIRouter()


def _serialize_message(
    message: TicketMessage,
    *,
    request: Optional[Request] = None,
) -> TicketMessageResponse:
    media_url: Optional[str] = None
    media_file_id = getattr(message, "media_file_id", None)
    if request and message.has_media and media_file_id:
        try:
            base_url = request.url_for(
                "get_ticket_message_media",
                ticket_id=str(message.ticket_id),
                message_id=str(message.id),
            )
            api_key = request.headers.get("X-API-Key")
            auth_header = request.headers.get("Authorization")
            bearer_token: Optional[str] = None
            if auth_header:
                scheme, _, credentials = auth_header.partition(" ")
                if scheme.lower() == "bearer" and credentials:
                    bearer_token = credentials

            # If API key or Bearer JWT is present, append as query for browser <img> access
            token_for_query = api_key or bearer_token
            if token_for_query:
                sep = "&" if ("?" in str(base_url)) else "?"
                media_url = f"{base_url}{sep}api_key={token_for_query}"
            else:
                media_url = str(base_url)
        except Exception:
            media_url = None

    return TicketMessageResponse(
        id=message.id,
        user_id=message.user_id,
        message_text=message.message_text,
        is_from_admin=message.is_from_admin,
        has_media=message.has_media,
        media_type=message.media_type,
        media_caption=message.media_caption,
        media_file_id=media_file_id,
        media_url=media_url,
        created_at=message.created_at,
    )


def _serialize_ticket(
    ticket: Ticket,
    *,
    include_messages: bool = False,
    request: Optional[Request] = None,
) -> TicketResponse:
    messages = []
    if include_messages:
        messages = sorted(ticket.messages, key=lambda m: m.created_at)

    return TicketResponse(
        id=ticket.id,
        user_id=ticket.user_id,
        title=ticket.title,
        status=ticket.status,
        priority=ticket.priority,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        closed_at=ticket.closed_at,
        user_reply_block_permanent=ticket.user_reply_block_permanent,
        user_reply_block_until=ticket.user_reply_block_until,
        messages=[_serialize_message(message, request=request) for message in messages],
    )


@router.get("", response_model=list[TicketResponse])
async def list_tickets(
    request: Request,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status_filter: Optional[TicketStatus] = Query(default=None, alias="status"),
    priority: Optional[str] = Query(default=None),
    user_id: Optional[int] = Query(default=None),
) -> list[TicketResponse]:
    status_value = status_filter.value if status_filter else None

    if user_id:
        tickets = await TicketCRUD.get_user_tickets(
            db,
            user_id=user_id,
            status=status_value,
            limit=limit,
            offset=offset,
        )
    else:
        tickets = await TicketCRUD.get_all_tickets(
            db,
            status=status_value,
            priority=priority,
            limit=limit,
            offset=offset,
        )

    return [_serialize_ticket(ticket, request=request) for ticket in tickets]


@router.get("/{ticket_id}", response_model=TicketResponse)
async def get_ticket(
    ticket_id: int,
    request: Request,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> TicketResponse:
    ticket = await TicketCRUD.get_ticket_by_id(db, ticket_id, load_messages=True, load_user=False)
    if not ticket:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    return _serialize_ticket(ticket, include_messages=True, request=request)


@router.post("/{ticket_id}/status", response_model=TicketResponse)
async def update_ticket_status(
    ticket_id: int,
    payload: TicketStatusUpdateRequest,
    request: Request,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> TicketResponse:
    try:
        status_value = TicketStatus(payload.status).value
    except ValueError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid ticket status") from error

    closed_at = datetime.utcnow() if status_value == TicketStatus.CLOSED.value else None
    success = await TicketCRUD.update_ticket_status(db, ticket_id, status_value, closed_at)
    if not success:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")

    ticket = await TicketCRUD.get_ticket_by_id(db, ticket_id, load_messages=True, load_user=False)
    try:
        await broker.publish("ticket.update")
        await broker.publish(f"ticket.status:{ticket.id}")
    except Exception:
        pass
    return _serialize_ticket(ticket, include_messages=True, request=request)


@router.post("/{ticket_id}/priority", response_model=TicketResponse)
async def update_ticket_priority(
    ticket_id: int,
    payload: TicketPriorityUpdateRequest,
    request: Request,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> TicketResponse:
    allowed_priorities = {"low", "normal", "high", "urgent"}
    if payload.priority not in allowed_priorities:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid priority")

    ticket = await TicketCRUD.get_ticket_by_id(db, ticket_id, load_messages=True, load_user=False)
    if not ticket:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")

    ticket.priority = payload.priority
    ticket.updated_at = datetime.utcnow()
    await db.commit()

    ticket = await TicketCRUD.get_ticket_by_id(db, ticket_id, load_messages=True, load_user=False)
    try:
        await broker.publish("ticket.update")
        await broker.publish(f"ticket.priority:{ticket.id}")
    except Exception:
        pass
    return _serialize_ticket(ticket, include_messages=True, request=request)


@router.post("/{ticket_id}/reply-block", response_model=TicketResponse)
async def update_reply_block(
    ticket_id: int,
    payload: TicketReplyBlockRequest,
    request: Request,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> TicketResponse:
    until = payload.until
    if not payload.permanent and until and until <= datetime.utcnow():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Block expiration must be in the future")

    success = await TicketCRUD.set_user_reply_block(
        db,
        ticket_id,
        permanent=payload.permanent,
        until=until,
    )
    if not success:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")

    ticket = await TicketCRUD.get_ticket_by_id(db, ticket_id, load_messages=True, load_user=False)
    try:
        await broker.publish("ticket.update")
        await broker.publish(f"ticket.block:{ticket.id}")
    except Exception:
        pass
    return _serialize_ticket(ticket, include_messages=True, request=request)


@router.delete("/{ticket_id}/reply-block", response_model=TicketResponse)
async def clear_reply_block(
    ticket_id: int,
    request: Request,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> TicketResponse:
    success = await TicketCRUD.set_user_reply_block(
        db,
        ticket_id,
        permanent=False,
        until=None,
    )
    if not success:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")

    ticket = await TicketCRUD.get_ticket_by_id(db, ticket_id, load_messages=True, load_user=False)
    try:
        await broker.publish("ticket.update")
        await broker.publish(f"ticket.block:{ticket.id}")
    except Exception:
        pass
    return _serialize_ticket(ticket, include_messages=True, request=request)


@router.post("/{ticket_id}/reply", response_model=TicketResponse)
async def reply_to_ticket(
    ticket_id: int,
    payload: TicketReplyRequest,
    request: Request,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> TicketResponse:
    from app.database.crud.ticket import TicketMessageCRUD

    ticket = await TicketCRUD.get_ticket_by_id(db, ticket_id, load_messages=False)
    if not ticket:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")

    # Админ ответ — помечаем is_from_admin=True
    await TicketMessageCRUD.add_message(
        db,
        ticket_id=ticket_id,
        user_id=ticket.user_id,
        message_text=payload.message_text,
        is_from_admin=True,
        media_type=payload.media_type,
        media_file_id=payload.media_file_id,
        media_caption=payload.media_caption,
    )

    # Вернём обновлённый тикет с сообщениями
    ticket = await TicketCRUD.get_ticket_by_id(db, ticket_id, load_messages=True, load_user=False)
    try:
        await broker.publish("ticket.update")
        await broker.publish(f"ticket.message:{ticket.id}")
    except Exception:
        pass
    return _serialize_ticket(ticket, include_messages=True, request=request)


@router.get("/{ticket_id}/messages/{message_id}/media")
async def get_ticket_message_media(
    ticket_id: int,
    message_id: int,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
):
    # Validate message belongs to ticket and has media
    result = await db.execute(
        select(TicketMessage).where(
            TicketMessage.id == message_id,
            TicketMessage.ticket_id == ticket_id,
        )
    )
    message = result.scalar_one_or_none()
    if not message or not getattr(message, "has_media", False) or not getattr(message, "media_file_id", None):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Media not found")

    bot_token = getattr(settings, "BOT_TOKEN", None)
    if not bot_token:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Bot token not configured")

    api_base = f"https://api.telegram.org/bot{bot_token}"
    file_api = f"{api_base}/getFile?file_id={message.media_file_id}"

    session_timeout = aiohttp.ClientTimeout(total=30)
    sess = aiohttp.ClientSession(timeout=session_timeout)
    try:
        resp = await sess.get(file_api)
        try:
            if resp.status != 200:
                raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Failed to resolve media")
            payload = await resp.json()
            file_path = payload.get("result", {}).get("file_path")
            if not file_path:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "File path not found")
        finally:
            await resp.release()

        file_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
        upstream = await sess.get(file_url)
        if upstream.status != 200:
            await upstream.release()
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Failed to fetch media")

        headers = {}
        ct = upstream.headers.get("Content-Type")
        if ct:
            headers["Content-Type"] = ct

        async def streamer():
            try:
                async for chunk in upstream.content.iter_chunked(64 * 1024):
                    if chunk:
                        yield chunk
            finally:
                await upstream.release()

        background = BackgroundTask(sess.close)
        return StreamingResponse(streamer(), headers=headers, background=background)
    except Exception:
        # Ensure session is closed on any error path
        await sess.close()
        raise
