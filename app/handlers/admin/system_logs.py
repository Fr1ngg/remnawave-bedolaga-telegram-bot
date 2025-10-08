import logging
from datetime import datetime
from html import escape
from pathlib import Path

from aiogram import Dispatcher, F, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, FSInputFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.utils.decorators import admin_required, error_handler

logger = logging.getLogger(__name__)

LOG_PREVIEW_LIMIT = 2300


def _resolve_log_path() -> Path:
    log_path = Path(settings.LOG_FILE)
    if not log_path.is_absolute():
        log_path = Path.cwd() / log_path
    return log_path


def _format_preview_block(text: str) -> str:
    escaped_text = escape(text) if text else ""
    return f"<blockquote expandable><pre><code>{escaped_text}</code></pre></blockquote>"


def _build_logs_message(log_path: Path) -> str:
    if not log_path.exists():
        message = (
            "🧾 <b>Системные логи</b>\n\n"
            f"Файл <code>{log_path}</code> пока не создан.\n"
            "Логи появятся автоматически после первой записи."
        )
        return message

    try:
        content = log_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as error:  # pragma: no cover - защита от проблем чтения
        logger.error("Ошибка чтения лог-файла %s: %s", log_path, error)
        message = (
            "❌ <b>Ошибка чтения логов</b>\n\n"
            f"Не удалось прочитать файл <code>{log_path}</code>."
        )
        return message

    total_length = len(content)
    stats = log_path.stat()
    updated_at = datetime.fromtimestamp(stats.st_mtime)

    if not content:
        preview_text = "Лог-файл пуст."
        truncated = False
    else:
        preview_text = content[-LOG_PREVIEW_LIMIT:]
        truncated = total_length > LOG_PREVIEW_LIMIT

    details_lines = [
        "🧾 <b>Системные логи</b>",
        "",
        f"📁 <b>Файл:</b> <code>{log_path}</code>",
        f"🕒 <b>Обновлен:</b> {updated_at.strftime('%d.%m.%Y %H:%M:%S')}",
        f"🧮 <b>Размер:</b> {total_length} символов",
        (
            f"👇 Показаны последние {LOG_PREVIEW_LIMIT} символов."
            if truncated
            else "📄 Показано все содержимое файла."
        ),
        "",
        _format_preview_block(preview_text),
    ]

    return "\n".join(details_lines)


def _get_logs_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_system_logs_refresh")],
            [InlineKeyboardButton(text="⬇️ Скачать лог", callback_data="admin_system_logs_download")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_submenu_system")],
        ]
    )


@admin_required
@error_handler
async def show_system_logs(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    log_path = _resolve_log_path()
    message = _build_logs_message(log_path)

    reply_markup = _get_logs_keyboard()
    await callback.message.edit_text(message, reply_markup=reply_markup, parse_mode="HTML")
    await callback.answer()


@admin_required
@error_handler
async def refresh_system_logs(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    log_path = _resolve_log_path()
    message = _build_logs_message(log_path)

    reply_markup = _get_logs_keyboard()
    await callback.message.edit_text(message, reply_markup=reply_markup, parse_mode="HTML")
    await callback.answer("🔄 Обновлено")


@admin_required
@error_handler
async def download_system_logs(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    log_path = _resolve_log_path()

    if not log_path.exists() or not log_path.is_file():
        await callback.answer("❌ Лог-файл не найден", show_alert=True)
        return

    try:
        await callback.answer("⬇️ Отправляю лог...")

        document = FSInputFile(log_path)
        stats = log_path.stat()
        updated_at = datetime.fromtimestamp(stats.st_mtime).strftime("%d.%m.%Y %H:%M:%S")
        caption = (
            f"🧾 Лог-файл <code>{log_path.name}</code>\n"
            f"📁 Путь: <code>{log_path}</code>\n"
            f"🕒 Обновлен: {updated_at}"
        )
        await callback.message.answer_document(document=document, caption=caption, parse_mode="HTML")
    except Exception as error:  # pragma: no cover - защита от ошибок отправки
        logger.error("Ошибка отправки лог-файла %s: %s", log_path, error)
        await callback.message.answer(
            "❌ <b>Не удалось отправить лог-файл</b>\n\n"
            "Проверьте журналы приложения или повторите попытку позже.",
            parse_mode="HTML",
        )


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(
        show_system_logs,
        F.data == "admin_system_logs",
    )
    dp.callback_query.register(
        refresh_system_logs,
        F.data == "admin_system_logs_refresh",
    )
    dp.callback_query.register(
        download_system_logs,
        F.data == "admin_system_logs_download",
    )
