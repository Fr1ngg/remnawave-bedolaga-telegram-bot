from pathlib import Path
from typing import Any, Dict

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, InputMediaPhoto, Message

from app.config import settings
from app.localization.texts import get_texts

LOGO_PATH = Path(settings.LOGO_FILE)
_PRIVACY_RESTRICTED_CODE = "BUTTON_USER_PRIVACY_RESTRICTED"


def is_qr_message(message: Message) -> bool:
    return bool(message.caption and message.caption.startswith("\U0001F517 Ваша реферальная ссылка"))


_original_answer = Message.answer
_original_edit_text = Message.edit_text


def _get_language(message: Message) -> str | None:
    try:
        user = message.from_user
        if user and getattr(user, "language_code", None):
            return user.language_code
    except AttributeError:
        pass
    return None


def _default_privacy_hint(language: str | None) -> str:
    if language and language.lower().startswith("en"):
        return (
            "⚠️ Telegram blocked the contact request button because of your privacy settings. "
            "Please allow sharing your contact information or send the required details manually."
        )
    return (
        "⚠️ Telegram запретил кнопку запроса контакта из-за настроек приватности. "
        "Разрешите отправку контакта в настройках Telegram или отправьте данные вручную."
    )


def append_privacy_hint(text: str | None, language: str | None) -> str:
    base_text = text or ""
    try:
        hint = get_texts(language).t(
            "PRIVACY_RESTRICTED_BUTTON_HINT",
            default=_default_privacy_hint(language),
        )
    except Exception:
        hint = _default_privacy_hint(language)

    hint = hint.strip()
    if not hint:
        return base_text

    if hint in base_text:
        return base_text

    if base_text:
        return f"{base_text}\n\n{hint}"
    return hint


def prepare_privacy_safe_kwargs(kwargs: Dict[str, Any] | None = None) -> Dict[str, Any]:
    safe_kwargs: Dict[str, Any] = dict(kwargs or {})
    safe_kwargs.pop("reply_markup", None)
    return safe_kwargs


def is_privacy_restricted_error(error: Exception) -> bool:
    if not isinstance(error, TelegramBadRequest):
        return False

    message = getattr(error, "message", "") or ""
    description = str(error)
    return _PRIVACY_RESTRICTED_CODE in message or _PRIVACY_RESTRICTED_CODE in description


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
    language = _get_language(self)

    if LOGO_PATH.exists():
        try:
            # Отправляем caption как есть; при ошибке парсинга ниже сработает фоллбек
            return await self.answer_photo(FSInputFile(LOGO_PATH), caption=text, **kwargs)
        except TelegramBadRequest as error:
            if is_privacy_restricted_error(error):
                fallback_text = append_privacy_hint(text, language)
                safe_kwargs = prepare_privacy_safe_kwargs(kwargs)
                return await _original_answer(self, fallback_text, **safe_kwargs)
            # Фоллбек, если Telegram ругается на caption или другое ограничение: отправим как текст
            return await _original_answer(self, text, **kwargs)
        except Exception:
            return await _original_answer(self, text, **kwargs)
    return await _original_answer(self, text, **kwargs)


async def _edit_with_photo(self: Message, text: str, **kwargs):
    # Уважаем флаг в рантайме: если логотип выключен — не подменяем редактирование
    if not settings.ENABLE_LOGO_MODE:
        return await _original_edit_text(self, text, **kwargs)
    if self.photo:
        language = _get_language(self)
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
        edit_kwargs = dict(kwargs)
        if "parse_mode" in edit_kwargs:
            _pm = edit_kwargs.pop("parse_mode")
            media_kwargs["parse_mode"] = _pm if _pm is not None else "HTML"
        else:
            media_kwargs["parse_mode"] = "HTML"
        try:
            return await self.edit_media(InputMediaPhoto(**media_kwargs), **edit_kwargs)
        except TelegramBadRequest as error:
            if is_privacy_restricted_error(error):
                fallback_text = append_privacy_hint(text, language)
                safe_kwargs = prepare_privacy_safe_kwargs(kwargs)
                try:
                    await self.delete()
                except Exception:
                    pass
                return await _original_answer(self, fallback_text, **safe_kwargs)
            # Фоллбек: удалим и отправим обычный текст без фото
            try:
                await self.delete()
            except Exception:
                pass
            return await _original_answer(self, text, **kwargs)
    return await _original_edit_text(self, text, **kwargs)


def patch_message_methods():
    if not settings.ENABLE_LOGO_MODE:
        return
    Message.answer = _answer_with_photo
    Message.edit_text = _edit_with_photo

