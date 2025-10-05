from aiogram import types
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, InputMediaPhoto

from app.config import settings
from .message_patch import LOGO_PATH, is_qr_message, sanitize_keyboard_for_privacy


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
    parse_mode: str | None = "HTML",
    *,
    _privacy_retry: bool = False,
) -> None:
    async def _retry_privacy(exc: Exception) -> bool:
        if _privacy_retry or "BUTTON_USER_PRIVACY_RESTRICTED" not in str(exc):
            return False
        safe_keyboard, changed = sanitize_keyboard_for_privacy(keyboard)
        if not changed:
            return False
        await edit_or_answer_photo(
            callback=callback,
            caption=caption,
            keyboard=safe_keyboard,
            parse_mode=parse_mode,
            _privacy_retry=True,
        )
        return True
    # Если режим логотипа выключен — работаем текстом
    if not settings.ENABLE_LOGO_MODE:
        try:
            if callback.message.photo:
                await callback.message.delete()
                await callback.message.answer(
                    caption,
                    reply_markup=keyboard,
                    parse_mode=parse_mode,
                )
            else:
                await callback.message.edit_text(
                    caption,
                    reply_markup=keyboard,
                    parse_mode=parse_mode,
                )
        except TelegramBadRequest as exc:
            if await _retry_privacy(exc):
                return
            await callback.message.delete()
            try:
                await callback.message.answer(
                    caption,
                    reply_markup=keyboard,
                    parse_mode=parse_mode,
                )
            except TelegramBadRequest as final_exc:
                if await _retry_privacy(final_exc):
                    return
                await callback.message.answer(
                    caption,
                    parse_mode=parse_mode,
                )
        return

    # Если текст слишком длинный для caption — отправим как текст
    if caption and len(caption) > 1000:
        try:
            if callback.message.photo:
                await callback.message.delete()
            await callback.message.answer(
                caption,
                reply_markup=keyboard,
                parse_mode=parse_mode,
            )
        except TelegramBadRequest as exc:
            if await _retry_privacy(exc):
                return
        return

    media = _resolve_media(callback.message)
    try:
        await callback.message.edit_media(
            InputMediaPhoto(media=media, caption=caption, parse_mode=(parse_mode or "HTML")),
            reply_markup=keyboard,
        )
    except TelegramBadRequest as exc:
        if await _retry_privacy(exc):
            return
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
                parse_mode=(parse_mode or "HTML"),
            )
        except TelegramBadRequest as inner_exc:
            if await _retry_privacy(inner_exc):
                return
        except Exception:
            pass
        else:
            return
        # Последний фоллбек — обычный текст
        try:
            await callback.message.answer(
                caption,
                reply_markup=keyboard,
                parse_mode=(parse_mode or "HTML"),
            )
        except TelegramBadRequest as final_exc:
            if await _retry_privacy(final_exc):
                return
            await callback.message.answer(
                caption,
                parse_mode=(parse_mode or "HTML"),
            )
