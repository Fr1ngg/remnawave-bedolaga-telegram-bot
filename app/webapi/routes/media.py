from __future__ import annotations

import logging
from typing import Any, Optional

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Message
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.config import settings

from ..dependencies import require_api_token
from ..schemas.media import MediaUploadResponse
from ..utils.media import build_media_url

logger = logging.getLogger(__name__)

router = APIRouter()


def _detect_media_type(upload: UploadFile, explicit_type: Optional[str]) -> str:
    if explicit_type:
        return explicit_type.lower()

    content_type = (upload.content_type or "").lower()
    if content_type.startswith("image"):
        return "photo"
    if content_type.startswith("video"):
        return "video"
    if content_type.startswith("audio"):
        return "voice"
    return "document"


async def _send_media(bot: Bot, chat_id: int, upload: UploadFile, media_type: str, caption: Optional[str]) -> Message:
    file = upload.file
    file.seek(0)

    if media_type == "photo":
        return await bot.send_photo(chat_id, photo=file, caption=caption)
    if media_type == "video":
        return await bot.send_video(chat_id, video=file, caption=caption)
    if media_type == "voice":
        return await bot.send_voice(chat_id, voice=file, caption=caption)
    return await bot.send_document(chat_id, document=file, caption=caption, disable_content_type_detection=True)


def _extract_file_id(message: Message, media_type: str) -> Optional[str]:
    if media_type == "photo" and message.photo:
        return message.photo[-1].file_id
    if media_type == "video" and message.video:
        return message.video.file_id
    if media_type == "voice" and message.voice:
        return message.voice.file_id
    if message.document:
        return message.document.file_id
    return None


@router.post("/upload", response_model=MediaUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_media(
    _: Any = Depends(require_api_token),
    file: UploadFile = File(...),
    media_type: Optional[str] = Form(default=None),
    caption: Optional[str] = Form(default=None),
) -> MediaUploadResponse:
    chat_id = settings.get_admin_notifications_chat_id()
    if chat_id is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Upload target chat is not configured")

    detected_type = _detect_media_type(file, media_type)

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        message = await _send_media(bot, chat_id, file, detected_type, caption)
        file_id = _extract_file_id(message, detected_type)
        if not file_id:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to obtain file_id from Telegram")

        media_url = await build_media_url(bot, file_id)
        file_size = None
        if message.document and message.document.file_size:
            file_size = message.document.file_size
        elif message.photo:
            file_size = message.photo[-1].file_size
        elif message.video and message.video.file_size:
            file_size = message.video.file_size
        elif message.voice and message.voice.file_size:
            file_size = message.voice.file_size

        return MediaUploadResponse(
            file_id=file_id,
            media_type=detected_type,
            media_url=media_url,
            file_name=getattr(message.document, "file_name", file.filename),
            file_size=file_size,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to upload media to Telegram: %s", exc)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to upload media") from exc
    finally:
        await bot.session.close()
