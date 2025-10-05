from pathlib import Path
from aiogram.types import (
    Message,
    FSInputFile,
    InputMediaPhoto,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.exceptions import TelegramBadRequest

from app.config import settings

LOGO_PATH = Path(settings.LOGO_FILE)


def is_qr_message(message: Message) -> bool:
    return bool(message.caption and message.caption.startswith("\U0001F517 Ваша реферальная ссылка"))


_original_answer = Message.answer
_original_edit_text = Message.edit_text


def sanitize_keyboard_for_privacy(
    keyboard: InlineKeyboardMarkup | None,
) -> tuple[InlineKeyboardMarkup | None, bool]:
    """Remove buttons that may be blocked by user privacy settings.

    Currently Telegram returns BUTTON_USER_PRIVACY_RESTRICTED when sending inline buttons
    that point directly to a user via ``tg://user`` links if the target user hides their
    account. In such cases we strip these buttons and retry the message.
    """

    if keyboard is None or not isinstance(keyboard, InlineKeyboardMarkup):
        return keyboard, False

    changed = False
    new_rows: list[list[InlineKeyboardButton]] = []

    for row in keyboard.inline_keyboard:
        new_row = []
        for button in row:
            url = getattr(button, "url", None)
            if isinstance(url, str) and url.startswith("tg://user?id="):
                changed = True
                continue
            new_row.append(button)
        if new_row:
            new_rows.append(new_row)
        elif row:
            changed = True

    if not changed:
        return keyboard, False

    return InlineKeyboardMarkup(inline_keyboard=new_rows), True


async def _answer_with_photo(self: Message, text: str = None, **kwargs):
    # Уважаем флаг в рантайме: если логотип выключен — не подменяем ответ
    if not settings.ENABLE_LOGO_MODE:
        return await _original_answer(self, text, **kwargs)
    # Если caption слишком длинный для фото — отправим как текст
    try:
        if text is not None and len(text) > 900:
            return await _original_answer(self, text, **kwargs)
    except Exception:
        pass
    reply_markup = kwargs.get("reply_markup")
    sanitized_keyboard, sanitized_applied = sanitize_keyboard_for_privacy(reply_markup)
    if LOGO_PATH.exists():
        try:
            # Отправляем caption как есть; при ошибке парсинга ниже сработает фоллбек
            return await self.answer_photo(FSInputFile(LOGO_PATH), caption=text, **kwargs)
        except TelegramBadRequest as exc:
            if sanitized_applied and "BUTTON_USER_PRIVACY_RESTRICTED" in str(exc):
                safe_kwargs = dict(kwargs)
                if sanitized_keyboard is None:
                    safe_kwargs.pop("reply_markup", None)
                else:
                    safe_kwargs["reply_markup"] = sanitized_keyboard
                try:
                    return await self.answer_photo(
                        FSInputFile(LOGO_PATH), caption=text, **safe_kwargs
                    )
                except TelegramBadRequest:
                    pass
            # Фоллбек, если Telegram ругается на caption или клавиатуру: отправим как текст
        except Exception:
            pass
    safe_kwargs = dict(kwargs)
    if sanitized_applied:
        if sanitized_keyboard is None:
            safe_kwargs.pop("reply_markup", None)
        else:
            safe_kwargs["reply_markup"] = sanitized_keyboard
    return await _original_answer(self, text, **safe_kwargs)


async def _edit_with_photo(self: Message, text: str, **kwargs):
    # Уважаем флаг в рантайме: если логотип выключен — не подменяем редактирование
    if not settings.ENABLE_LOGO_MODE:
        return await _original_edit_text(self, text, **kwargs)
    if self.photo:
        # Если caption потенциально слишком длинный — отправим как текст вместо caption
        try:
            if text is not None and len(text) > 900:
                try:
                    await self.delete()
                except Exception:
                    pass
                return await _original_answer(self, text, **kwargs)
        except Exception:
            pass
        # Всегда используем логотип если включен режим логотипа,
        # кроме специальных случаев (QR сообщения)
        if settings.ENABLE_LOGO_MODE and LOGO_PATH.exists() and not is_qr_message(self):
            media = FSInputFile(LOGO_PATH)
        elif is_qr_message(self) and LOGO_PATH.exists():
            media = FSInputFile(LOGO_PATH)
        else:
            media = self.photo[-1].file_id
        media_kwargs = {"media": media, "caption": text}
        if "parse_mode" in kwargs:
            _pm = kwargs.pop("parse_mode")
            media_kwargs["parse_mode"] = _pm if _pm is not None else "HTML"
        else:
            media_kwargs["parse_mode"] = "HTML"
        reply_markup = kwargs.get("reply_markup")
        sanitized_keyboard, sanitized_applied = sanitize_keyboard_for_privacy(reply_markup)
        try:
            return await self.edit_media(InputMediaPhoto(**media_kwargs), **kwargs)
        except TelegramBadRequest as exc:
            if sanitized_applied and "BUTTON_USER_PRIVACY_RESTRICTED" in str(exc):
                safe_kwargs = dict(kwargs)
                if sanitized_keyboard is None:
                    safe_kwargs.pop("reply_markup", None)
                else:
                    safe_kwargs["reply_markup"] = sanitized_keyboard
                try:
                    return await self.edit_media(InputMediaPhoto(**media_kwargs), **safe_kwargs)
                except TelegramBadRequest:
                    pass
        # Фоллбек: удалим и отправим обычный текст без фото
        try:
            await self.delete()
        except Exception:
            pass
        safe_kwargs = dict(kwargs)
        if sanitized_applied:
            if sanitized_keyboard is None:
                safe_kwargs.pop("reply_markup", None)
            else:
                safe_kwargs["reply_markup"] = sanitized_keyboard
        return await _original_answer(self, text, **safe_kwargs)
    return await _original_edit_text(self, text, **kwargs)


def patch_message_methods():
    if not settings.ENABLE_LOGO_MODE:
        return
    Message.answer = _answer_with_photo
    Message.edit_text = _edit_with_photo

