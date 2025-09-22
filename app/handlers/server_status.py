import logging
from collections import OrderedDict
from datetime import datetime
from typing import List, Tuple

from aiogram import Dispatcher, F, types

from app.config import settings
from app.database.models import User
from app.keyboards.inline import get_server_status_keyboard
from app.localization.texts import get_texts
from app.services.server_status_service import (
    ServerStatusEntry,
    ServerStatusError,
    ServerStatusService,
)

logger = logging.getLogger(__name__)

_status_service = ServerStatusService()


async def show_server_status(callback: types.CallbackQuery, db_user: User) -> None:
    await _render_server_status(callback, db_user, page=1)


async def change_server_status_page(callback: types.CallbackQuery, db_user: User) -> None:
    try:
        _, page_str = callback.data.split(":", 1)
        page = int(page_str)
    except (ValueError, AttributeError, IndexError):
        page = 1

    await _render_server_status(callback, db_user, page=page)


async def refresh_server_status(callback: types.CallbackQuery, db_user: User) -> None:
    try:
        _, page_str = callback.data.split(":", 1)
        page = int(page_str)
    except (ValueError, AttributeError, IndexError):
        page = 1

    await _render_server_status(callback, db_user, page=page)


async def _render_server_status(
    callback: types.CallbackQuery,
    db_user: User,
    page: int = 1,
) -> None:
    texts = get_texts(db_user.language)

    if settings.get_server_status_mode() != "xray":
        await callback.answer(texts.t("SERVER_STATUS_NOT_CONFIGURED", "Функция недоступна."), show_alert=True)
        return

    try:
        servers = await _status_service.get_servers()
    except ServerStatusError as error:
        logger.warning("Server status error: %s", error)
        await callback.answer(
            texts.t("SERVER_STATUS_ERROR_SHORT", "Не удалось получить данные"),
            show_alert=True,
        )
        return
    except Exception as error:  # pragma: no cover - defensive logging
        logger.error("Unexpected server status error: %s", error)
        await callback.answer(
            texts.t("SERVER_STATUS_ERROR_SHORT", "Не удалось получить данные"),
            show_alert=True,
        )
        return

    message, total_pages, current_page = _build_status_message(servers, texts, page)
    keyboard = get_server_status_keyboard(db_user.language, current_page, total_pages)

    await callback.message.edit_text(
        message,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )
    await callback.answer()


def _build_status_message(
    servers: List[ServerStatusEntry],
    texts,
    page: int,
) -> Tuple[str, int, int]:
    total_servers = len(servers)
    online_servers = [server for server in servers if server.is_online]
    offline_servers = [server for server in servers if not server.is_online]

    items_per_page = settings.get_server_status_items_per_page()
    pages = _split_into_pages(online_servers, offline_servers, items_per_page)

    total_pages = max(1, len(pages))
    current_index = min(max(page - 1, 0), total_pages - 1)

    current_online, current_offline = pages[current_index] if pages else ([], [])

    lines: List[str] = [texts.t("SERVER_STATUS_TITLE", "📊 <b>Статус серверов</b>")]

    if total_servers == 0:
        lines.append("")
        lines.append(texts.t("SERVER_STATUS_NO_SERVERS", "Нет данных о серверах."))
        message = "\n".join(lines).strip()
        return message, 1, 1

    summary_total = texts.t(
        "SERVER_STATUS_TOTAL",
        "Всего серверов: {total}",
    ).format(total=total_servers)

    summary_breakdown = texts.t(
        "SERVER_STATUS_BREAKDOWN",
        "(В сети: {online}, Не сети: {offline})",
    ).format(
        online=len(online_servers),
        offline=len(offline_servers),
    )

    updated_at = texts.t(
        "SERVER_STATUS_UPDATED_AT",
        "Обновлено: {time}",
    ).format(time=datetime.now().strftime("%H:%M:%S"))

    lines.extend(["", summary_total, summary_breakdown, updated_at, ""])

    if current_online:
        lines.append(texts.t("SERVER_STATUS_AVAILABLE", "✅ <b>Доступны</b>"))
        lines.extend(_format_server_lines(current_online, texts, online=True))
        lines.append("")

    if current_offline:
        lines.append(texts.t("SERVER_STATUS_UNAVAILABLE", "❌ <b>Недоступны</b>"))
        lines.extend(_format_server_lines(current_offline, texts, online=False))
        lines.append("")

    if total_pages > 1:
        lines.append(
            texts.t("SERVER_STATUS_PAGINATION", "Страница {current} из {total}").format(
                current=current_index + 1,
                total=total_pages,
            )
        )

    message = "\n".join(line for line in lines if line is not None)
    message = message.strip()
    return message, total_pages, current_index + 1


def _split_into_pages(
    online: List[ServerStatusEntry],
    offline: List[ServerStatusEntry],
    items_per_page: int,
) -> List[Tuple[List[ServerStatusEntry], List[ServerStatusEntry]]]:
    if not online and not offline:
        return [([], [])]

    pages: List[Tuple[List[ServerStatusEntry], List[ServerStatusEntry]]] = []
    online_index = 0
    offline_index = 0

    while online_index < len(online) or offline_index < len(offline):
        current_online: List[ServerStatusEntry] = []
        current_offline: List[ServerStatusEntry] = []
        remaining = max(1, items_per_page)

        while remaining > 0 and online_index < len(online):
            current_online.append(online[online_index])
            online_index += 1
            remaining -= 1

        while remaining > 0 and offline_index < len(offline):
            current_offline.append(offline[offline_index])
            offline_index += 1
            remaining -= 1

        pages.append((current_online, current_offline))

    return pages if pages else [([], [])]


def _format_server_lines(
    servers: List[ServerStatusEntry],
    texts,
    *,
    online: bool,
) -> List[str]:
    lines: List[str] = []
    bullet = texts.t("SERVER_STATUS_LIST_BULLET", "•")
    grouped: OrderedDict[str, List[ServerStatusEntry]] = OrderedDict()

    for server in servers:
        flag_key = server.flag or ""
        grouped.setdefault(flag_key, []).append(server)

    for flag, entries in grouped.items():
        group_header = flag or texts.t("SERVER_STATUS_NO_FLAG", "🌐")
        lines.append(f"> {group_header}")

        for server in entries:
            if online:
                if server.latency_ms and server.latency_ms > 0:
                    latency_text = texts.t("SERVER_STATUS_LATENCY", "{latency} мс").format(
                        latency=server.latency_ms
                    )
                else:
                    latency_text = texts.t("SERVER_STATUS_LATENCY_UNKNOWN", "нет данных")
            else:
                latency_text = texts.t("SERVER_STATUS_OFFLINE", "нет ответа")

            name = server.display_name or server.name
            lines.append(f"> {bullet} {name} — {latency_text}")

        lines.append(None)

    if lines and lines[-1] is None:
        lines.pop()

    return lines


def register_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(
        show_server_status,
        F.data == "menu_server_status",
    )

    dp.callback_query.register(
        change_server_status_page,
        F.data.startswith("server_status_page:"),
    )

    dp.callback_query.register(
        refresh_server_status,
        F.data.startswith("server_status_refresh:"),
    )

