from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BufferedInputFile
from fastapi import APIRouter, File, Form, HTTPException, Security, UploadFile, status

from app.config import settings

from ..dependencies import require_api_token
from ..schemas.media import MediaUploadResponse


router = APIRouter()
logger = logging.getLogger(__name__)

ALLOWED_MEDIA_TYPES = {"photo", "video", "document"}


def _resolve_target_chat_id() -> int:
    """Выбирает чат для загрузки файлов (канал уведомлений или первый админ)."""

    chat_id = settings.get_admin_notifications_chat_id()
    if chat_id is not None:
        return chat_id

    admin_ids = settings.get_admin_ids()
    if admin_ids:
        return admin_ids[0]

    raise HTTPException(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "Не настроен чат для загрузки файлов (ADMIN_NOTIFICATIONS_CHAT_ID или ADMIN_IDS)",
    )


async def _build_media_url(bot: Bot, file_id: str) -> str | None:
    try:
        file = await bot.get_file(file_id)
        if file.file_path:
            return f"https://api.telegram.org/file/bot{settings.BOT_TOKEN}/{file.file_path}"
    except Exception as error:  # pragma: no cover - защита от неожиданных сбоев
        logger.warning("Failed to build media URL for %s: %s", file_id, error)
    return None


@router.post("/upload", response_model=MediaUploadResponse, tags=["media"], status_code=status.HTTP_201_CREATED)
async def upload_media(
    _: Any = Security(require_api_token),
    file: UploadFile = File(...),
    media_type: str = Form("document", description="Тип файла: photo, video или document"),
    caption: str | None = Form(None, description="Необязательная подпись к файлу"),
) -> MediaUploadResponse:
    media_type_normalized = (media_type or "").strip().lower()
    if media_type_normalized not in ALLOWED_MEDIA_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unsupported media type")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File is empty")

    target_chat_id = _resolve_target_chat_id()
    upload = BufferedInputFile(file_bytes, filename=file.filename or "upload")

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    try:
        if media_type_normalized == "photo":
            message = await bot.send_photo(
                chat_id=target_chat_id,
                photo=upload,
                caption=caption,
            )
            media = message.photo[-1]
        elif media_type_normalized == "video":
            message = await bot.send_video(
                chat_id=target_chat_id,
                video=upload,
                caption=caption,
            )
            media = message.video
        else:
            message = await bot.send_document(
                chat_id=target_chat_id,
                document=upload,
                caption=caption,
            )
            media = message.document

        media_url = await _build_media_url(bot, media.file_id)
        return MediaUploadResponse(
            media_type=media_type_normalized,
            file_id=media.file_id,
            file_unique_id=getattr(media, "file_unique_id", None),
            media_url=media_url,
        )
    except HTTPException:
        raise
    except Exception as error:
        logger.error("Failed to upload media: %s", error)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to upload media") from error
    finally:
        await bot.session.close()

