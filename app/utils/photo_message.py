from aiogram import types
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, InputMediaPhoto

from app.config import settings
from .message_patch import (
    LOGO_PATH,
    append_privacy_hint,
    is_privacy_restricted_error,
    is_qr_message,
    prepare_privacy_safe_kwargs,
)


def _resolve_media(message: types.Message):
    # Всегда используем логотип если включен режим логотипа,
    # кроме специальных случаев (QR сообщения)
    if settings.ENABLE_LOGO_MODE and not is_qr_message(message):
        return FSInputFile(LOGO_PATH)
    # Только если режим логотипа выключен, используем фото из сообщения
    elif message.photo:
        return message.photo[-1].file_id
    return FSInputFile(LOGO_PATH)


def _get_language(callback: types.CallbackQuery) -> str | None:
    try:
        user = callback.from_user
        if user and getattr(user, "language_code", None):
            return user.language_code
    except AttributeError:
        pass
    return None


def _build_base_kwargs(keyboard: types.InlineKeyboardMarkup | None, parse_mode: str | None):
    kwargs: dict[str, object] = {}
    if parse_mode is not None:
        kwargs["parse_mode"] = parse_mode
    if keyboard is not None:
        kwargs["reply_markup"] = keyboard
    return kwargs


async def _answer_text(
    callback: types.CallbackQuery,
    caption: str,
    keyboard: types.InlineKeyboardMarkup | None,
    parse_mode: str | None,
    error: TelegramBadRequest | None = None,
) -> None:
    language = _get_language(callback)
    kwargs = _build_base_kwargs(keyboard, parse_mode)

    if error and is_privacy_restricted_error(error):
        caption = append_privacy_hint(caption, language)
        kwargs = prepare_privacy_safe_kwargs(kwargs)

    kwargs.setdefault("parse_mode", parse_mode or "HTML")

    await callback.message.answer(
        caption,
        **kwargs,
    )


async def edit_or_answer_photo(
    callback: types.CallbackQuery,
    caption: str,
    keyboard: types.InlineKeyboardMarkup,
    parse_mode: str | None = "HTML",
) -> None:
    resolved_parse_mode = parse_mode or "HTML"
    # Если режим логотипа выключен — работаем текстом
    if not settings.ENABLE_LOGO_MODE:
        try:
            if callback.message.photo:
                await callback.message.delete()
                await _answer_text(callback, caption, keyboard, resolved_parse_mode)
            else:
                await callback.message.edit_text(
                    caption,
                    reply_markup=keyboard,
                    parse_mode=resolved_parse_mode,
                )
        except TelegramBadRequest as error:
            await callback.message.delete()
            await _answer_text(callback, caption, keyboard, resolved_parse_mode, error)
        return

    # Если текст слишком длинный для caption — отправим как текст
    if caption and len(caption) > 1000:
        try:
            if callback.message.photo:
                await callback.message.delete()
            await _answer_text(callback, caption, keyboard, resolved_parse_mode)
        except TelegramBadRequest as error:
            await _answer_text(callback, caption, keyboard, resolved_parse_mode, error)
        return

    media = _resolve_media(callback.message)
    try:
        await callback.message.edit_media(
            InputMediaPhoto(media=media, caption=caption, parse_mode=(parse_mode or "HTML")),
            reply_markup=keyboard,
        )
    except TelegramBadRequest as error:
        if is_privacy_restricted_error(error):
            try:
                await callback.message.delete()
            except Exception:
                pass
            await _answer_text(callback, caption, keyboard, resolved_parse_mode, error)
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
                parse_mode=resolved_parse_mode,
            )
        except TelegramBadRequest as photo_error:
            await _answer_text(callback, caption, keyboard, resolved_parse_mode, photo_error)
        except Exception:
            # Последний фоллбек — обычный текст
            await _answer_text(callback, caption, keyboard, resolved_parse_mode)
