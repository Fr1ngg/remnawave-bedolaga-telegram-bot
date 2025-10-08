from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status, UploadFile, File, Form
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import BroadcastHistory
from app.services.broadcast_service import (
    BroadcastConfig,
    BroadcastMediaConfig,
    broadcast_service,
)
from app.config import settings
import aiohttp
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from aiogram import Bot
from aiogram.types import BufferedInputFile

from ..dependencies import get_db_session, require_api_token
from .notifications import broker
from ..schemas.broadcasts import (
    BroadcastCreateRequest,
    BroadcastListResponse,
    BroadcastResponse,
)


router = APIRouter()


def _serialize_broadcast(broadcast: BroadcastHistory) -> BroadcastResponse:
    return BroadcastResponse(
        id=broadcast.id,
        target_type=broadcast.target_type,
        message_text=broadcast.message_text,
        has_media=broadcast.has_media,
        media_type=broadcast.media_type,
        media_file_id=broadcast.media_file_id,
        media_caption=broadcast.media_caption,
        total_count=broadcast.total_count,
        sent_count=broadcast.sent_count,
        failed_count=broadcast.failed_count,
        status=broadcast.status,
        admin_id=broadcast.admin_id,
        admin_name=broadcast.admin_name,
        created_at=broadcast.created_at,
        completed_at=broadcast.completed_at,
    )


@router.post("", response_model=BroadcastResponse, status_code=status.HTTP_201_CREATED)
async def create_broadcast(
    payload: BroadcastCreateRequest,
    token: Any = Depends(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> BroadcastResponse:
    message_text = payload.message_text.strip()
    # Allow empty message if media caption is provided (validated in schema too)
    if not message_text and not (payload.media and (payload.media.caption or "").strip()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Message text must not be empty")

    media_payload = payload.media

    broadcast = BroadcastHistory(
        target_type=payload.target,
        message_text=message_text,
        has_media=media_payload is not None,
        media_type=media_payload.type if media_payload else None,
        media_file_id=media_payload.file_id if media_payload else None,
        media_caption=media_payload.caption if media_payload else None,
        total_count=0,
        sent_count=0,
        failed_count=0,
        status="queued",
        admin_id=None,
        admin_name=getattr(token, "name", None) or getattr(token, "created_by", None),
    )
    db.add(broadcast)
    await db.commit()
    await db.refresh(broadcast)

    media_config = None
    if media_payload:
        media_config = BroadcastMediaConfig(
            type=media_payload.type,
            file_id=media_payload.file_id,
            caption=(media_payload.caption or message_text) or None,
        )

    config = BroadcastConfig(
        target=payload.target,
        message_text=message_text,
        selected_buttons=payload.selected_buttons,
        media=media_config,
        initiator_name=getattr(token, "name", None) or getattr(token, "created_by", None),
    )

    await broadcast_service.start_broadcast(broadcast.id, config)
    await db.refresh(broadcast)
    try:
        await broker.publish("broadcasts.update")
    except Exception:
        pass
    return _serialize_broadcast(broadcast)


@router.post("/media/upload")
async def upload_broadcast_media(
    media_type: str = Form(...),
    file: UploadFile = File(...),
    caption: Optional[str] = Form(default=None),
    _: Any = Depends(require_api_token),
):
    if media_type not in {"photo", "video", "document"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unsupported media type")

    chat_id = settings.get_admin_notifications_chat_id()
    if not chat_id:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "ADMIN_NOTIFICATIONS_CHAT_ID is not configured")

    token = getattr(settings, "BOT_TOKEN", None)
    if not token:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Bot token not configured")

    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")

    bot = Bot(token=token)
    try:
        input_file = BufferedInputFile(file=data, filename=file.filename or "upload")
        sent = None
        file_id: Optional[str] = None
        if media_type == "photo":
            sent = await bot.send_photo(chat_id=chat_id, photo=input_file, caption=caption or None)
            file_id = sent.photo[-1].file_id if getattr(sent, "photo", None) else None
        elif media_type == "video":
            sent = await bot.send_video(chat_id=chat_id, video=input_file, caption=caption or None)
            file_id = sent.video.file_id if getattr(sent, "video", None) else None
        elif media_type == "document":
            sent = await bot.send_document(chat_id=chat_id, document=input_file, caption=caption or None)
            file_id = sent.document.file_id if getattr(sent, "document", None) else None

        if not file_id:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Failed to obtain Telegram file_id")

        try:
            await bot.delete_message(chat_id=chat_id, message_id=sent.message_id)  # type: ignore[arg-type]
        except Exception:
            pass

        # Absolute preview URL is constructed on the client; keep path relative for backward compat
        preview_url = f"/broadcasts/media/{file_id}"
        return {"file_id": file_id, "type": media_type, "caption": caption, "preview_url": preview_url}
    finally:
        try:
            await bot.session.close()
        except Exception:
            pass


@router.get("/media/{file_id}")
async def get_broadcast_media(
    file_id: str,
):
    token = getattr(settings, "BOT_TOKEN", None)
    if not token:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Bot token not configured")

    api_base = f"https://api.telegram.org/bot{token}"
    file_api = f"{api_base}/getFile?file_id={file_id}"

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

        file_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
        upstream = await sess.get(file_url)
        if upstream.status != 200:
            await upstream.release()
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Failed to fetch media")

        headers: dict[str, str] = {}
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
        await sess.close()
        raise


@router.get("", response_model=BroadcastListResponse)
async def list_broadcasts(
    _: Any = Depends(require_api_token),
    db: AsyncSession = Depends(get_db_session),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> BroadcastListResponse:
    total = await db.scalar(select(func.count(BroadcastHistory.id))) or 0

    result = await db.execute(
        select(BroadcastHistory)
        .order_by(BroadcastHistory.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    broadcasts = result.scalars().all()

    return BroadcastListResponse(
        items=[_serialize_broadcast(item) for item in broadcasts],
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.post("/{broadcast_id}/stop", response_model=BroadcastResponse)
async def stop_broadcast(
    broadcast_id: int,
    _: Any = Depends(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> BroadcastResponse:
    broadcast = await db.get(BroadcastHistory, broadcast_id)
    if not broadcast:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Broadcast not found")

    if broadcast.status not in {"queued", "in_progress", "cancelling"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Broadcast is not running")

    is_running = await broadcast_service.request_stop(broadcast_id)

    if is_running:
        broadcast.status = "cancelling"
    else:
        broadcast.status = "cancelled"
        broadcast.completed_at = datetime.utcnow()

    await db.commit()
    await db.refresh(broadcast)
    try:
        await broker.publish("broadcasts.update")
    except Exception:
        pass
    return _serialize_broadcast(broadcast)
