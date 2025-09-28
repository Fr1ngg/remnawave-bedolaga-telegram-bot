from pathlib import Path
from aiogram.types import Message, FSInputFile, InputMediaPhoto
from aiogram.exceptions import TelegramBadRequest
import re

from app.config import settings

LOGO_PATH = Path(settings.LOGO_FILE)


def is_qr_message(message: Message) -> bool:
    return bool(message.caption and message.caption.startswith("\U0001F517 Ваша реферальная ссылка"))


_original_answer = Message.answer
_original_edit_text = Message.edit_text


def _strip_html_tags(text: str) -> str:
    """Strip HTML tags from text"""
    if not text:
        return text
    return re.sub(r'<[^>]+>', '', text)

async def _answer_with_photo(self: Message, text: str = None, **kwargs):
    # Уважаем флаг в рантайме: если логотип выключен — не подменяем ответ
    if not settings.ENABLE_LOGO_MODE:
        return await _original_answer(self, text, **kwargs)
    
    # Strip HTML tags from text to prevent parsing errors
    if text:
        original_text = text
        text = _strip_html_tags(text)
        if original_text != text:
            print(f"DEBUG: Stripped HTML from text: {original_text[:50]}... -> {text[:50]}...")
    
    # Если текст содержит потенциальные HTML-теги — отправим как обычный текст
    try:
        if text and ("<" in text or ">" in text):
            print(f"DEBUG: Forcing plain text due to HTML chars: {text[:100]}...")
            safe_kwargs = dict(kwargs)
            safe_kwargs.pop("parse_mode", None)
            return await _original_answer(self, text, parse_mode=None, **safe_kwargs)
    except Exception:
        pass
    # Если caption слишком длинный для фото — отправим как текст
    try:
        if text is not None and len(text) > 900:
            safe_kwargs = dict(kwargs)
            safe_kwargs.pop("parse_mode", None)
            return await _original_answer(self, text, parse_mode=None, **safe_kwargs)
    except Exception:
        pass
    if LOGO_PATH.exists():
        try:
            # DEBUG: Log the text being sent to identify the source of HTML
            if text and ("<" in text or ">" in text):
                print(f"DEBUG: Text contains HTML-like chars: {text[:100]}...")
            photo_kwargs = dict(kwargs)
            # Иначе Telegram может попытаться парсить HTML в caption
            photo_kwargs.pop("parse_mode", None)
            return await self.answer_photo(
                FSInputFile(LOGO_PATH), caption=text, parse_mode=None, **photo_kwargs
            )
        except Exception:
            # Фоллбек, если Telegram ругается на caption: отправим как текст
            safe_kwargs = dict(kwargs)
            safe_kwargs.pop("parse_mode", None)
            return await _original_answer(self, text, parse_mode=None, **safe_kwargs)
    safe_kwargs = dict(kwargs)
    safe_kwargs.pop("parse_mode", None)
    return await _original_answer(self, text, parse_mode=None, **safe_kwargs)


async def _edit_with_photo(self: Message, text: str, **kwargs):
    # Уважаем флаг в рантайме: если логотип выключен — не подменяем редактирование
    if not settings.ENABLE_LOGO_MODE:
        return await _original_edit_text(self, text, **kwargs)
    
    # Strip HTML tags from text to prevent parsing errors
    if text:
        original_text = text
        text = _strip_html_tags(text)
        if original_text != text:
            print(f"DEBUG: Stripped HTML from edit text: {original_text[:50]}... -> {text[:50]}...")
    
    if self.photo:
        # Если текст содержит потенциальные HTML-теги — отправим как текст вместо caption
        try:
            if text and ("<" in text or ">" in text):
                try:
                    await self.delete()
                except Exception:
                    pass
                safe_kwargs = dict(kwargs)
                safe_kwargs.pop("parse_mode", None)
                return await _original_answer(self, text, parse_mode=None, **safe_kwargs)
        except Exception:
            pass
        # Если caption потенциально слишком длинный — отправим как текст вместо caption
        try:
            if text is not None and len(text) > 900:
                try:
                    await self.delete()
                except Exception:
                    pass
                safe_kwargs = dict(kwargs)
                safe_kwargs.pop("parse_mode", None)
                return await _original_answer(self, text, parse_mode=None, **safe_kwargs)
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
        media_kwargs["parse_mode"] = kwargs.pop("parse_mode", None)
        try:
            return await self.edit_media(InputMediaPhoto(**media_kwargs), **kwargs)
        except TelegramBadRequest:
            # Фоллбек: удалим и отправим обычный текст без фото
            try:
                await self.delete()
            except Exception:
                pass
            safe_kwargs = dict(kwargs)
            safe_kwargs.pop("parse_mode", None)
            return await _original_answer(self, text, parse_mode=None, **safe_kwargs)
    return await _original_edit_text(self, text, **kwargs)


def patch_message_methods():
    if not settings.ENABLE_LOGO_MODE:
        return
    Message.answer = _answer_with_photo
    Message.edit_text = _edit_with_photo

