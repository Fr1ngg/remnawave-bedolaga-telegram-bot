from aiogram import types
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, InputMediaPhoto

from app.config import settings
from .message_patch import LOGO_PATH, is_qr_message


def _resolve_media(message: types.Message):
    # Всегда используем логотип если включен режим логотипа,
    # кроме специальных случаев (QR сообщения)
    if settings.ENABLE_LOGO_MODE and not is_qr_message(message):
        return FSInputFile(LOGO_PATH)
    # Только если режим логотипа выключен, используем фото из сообщения
    elif message.photo:
        return message.photo[-1].file_id
    return FSInputFile(LOGO_PATH)


async def edit_or_answer_photo(
    callback: types.CallbackQuery,
    caption: str,
    keyboard: types.InlineKeyboardMarkup,
    parse_mode: str | None = None,
) -> None:
    # Если режим логотипа выключен — работаем текстом
    if not settings.ENABLE_LOGO_MODE:
        try:
            if callback.message.photo:
                await callback.message.delete()
                await callback.message.answer(
                    caption,
                    reply_markup=keyboard,
                    parse_mode=None,
                )
            else:
                await callback.message.edit_text(
                    caption,
                    reply_markup=keyboard,
                    parse_mode=None,
                )
        except TelegramBadRequest:
            await callback.message.delete()
            await callback.message.answer(
                caption,
                reply_markup=keyboard,
                parse_mode=None,
            )
        return

    # Если текст слишком длинный для caption — отправим как текст
    if caption and ("<" in caption or ">" in caption or len(caption) > 1000):
        try:
            if callback.message.photo:
                await callback.message.delete()
            await callback.message.answer(
                caption,
                reply_markup=keyboard,
                parse_mode=None,
            )
        except TelegramBadRequest:
            pass
        return

    media = _resolve_media(callback.message)
    try:
        await callback.message.edit_media(
            InputMediaPhoto(media=media, caption=caption, parse_mode=None),
            reply_markup=keyboard,
        )
    except TelegramBadRequest:
        # Фоллбек: если не удалось обновить фото — отправим текст, чтобы не упасть на лимите caption
        try:
            await callback.message.delete()
        except Exception:
            pass
        try:
            # Отправим как фото с логотипом
            await callback.message.answer_photo(
                photo=media if isinstance(media, FSInputFile) else FSInputFile(LOGO_PATH),
                caption=caption,
                reply_markup=keyboard,
                parse_mode=None,
            )
        except Exception:
            # Последний фоллбек — обычный текст
            await callback.message.answer(
                caption,
                reply_markup=keyboard,
                parse_mode=None,
            )
