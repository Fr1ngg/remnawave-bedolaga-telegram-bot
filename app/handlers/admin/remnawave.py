import logging
import math
from datetime import datetime
from typing import Any, Dict, Optional

from aiogram import Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from app.states import (
    RemnaWaveSyncStates,
    SquadRenameStates,
    SquadCreateStates,
    SquadMigrationStates,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.database.crud.server_squad import (
    count_active_users_for_squad,
    get_all_server_squads,
    get_server_squad_by_uuid,
)
from app.keyboards.admin import (
   get_admin_remnawave_keyboard, get_sync_options_keyboard,
   get_node_management_keyboard, get_confirmation_keyboard,
   get_squad_management_keyboard, get_squad_edit_keyboard
)
from app.localization.texts import get_texts
from app.services.remnawave_service import RemnaWaveService, RemnaWaveConfigurationError
from app.services.remnawave_sync_service import (
    RemnaWaveAutoSyncStatus,
    remnawave_sync_service,
)
from app.services.system_settings_service import bot_configuration_service
from app.utils.decorators import admin_required, error_handler
from app.utils.formatters import format_bytes, format_datetime

logger = logging.getLogger(__name__)

squad_inbound_selections = {}
squad_create_data = {}

MIGRATION_PAGE_SIZE = 8


def _format_duration(seconds: float) -> str:
    if seconds < 1:
        return "<1с"

    minutes, sec = divmod(int(seconds), 60)
    if minutes:
        if sec:
            return f"{minutes} мин {sec} с"
        return f"{minutes} мин"
    return f"{sec} с"


def _format_user_stats(stats: Optional[Dict[str, Any]]) -> str:
    if not stats:
        return "—"

    created = stats.get("created", 0)
    updated = stats.get("updated", 0)
    deleted = stats.get("deleted", stats.get("deactivated", 0))
    errors = stats.get("errors", 0)

    return (
        f"• Создано: {created}\n"
        f"• Обновлено: {updated}\n"
        f"• Деактивировано: {deleted}\n"
        f"• Ошибок: {errors}"
    )


def _format_server_stats(stats: Optional[Dict[str, Any]]) -> str:
    if not stats:
        return "—"

    created = stats.get("created", 0)
    updated = stats.get("updated", 0)
    removed = stats.get("removed", 0)
    total = stats.get("total", 0)

    return (
        f"• Создано: {created}\n"
        f"• Обновлено: {updated}\n"
        f"• Удалено: {removed}\n"
        f"• Всего в панели: {total}"
    )


def _build_auto_sync_view(status: RemnaWaveAutoSyncStatus) -> tuple[str, types.InlineKeyboardMarkup]:
    times_text = ", ".join(t.strftime("%H:%M") for t in status.times) if status.times else "—"
    next_run_text = format_datetime(status.next_run) if status.next_run else "—"

    if status.last_run_finished_at:
        finished_text = format_datetime(status.last_run_finished_at)
        started_text = (
            format_datetime(status.last_run_started_at)
            if status.last_run_started_at
            else "—"
        )
        duration = (
            status.last_run_finished_at - status.last_run_started_at
            if status.last_run_started_at
            else None
        )
        duration_text = f" ({_format_duration(duration.total_seconds())})" if duration else ""
        reason_map = {
            "manual": "вручную",
            "auto": "по расписанию",
            "immediate": "при включении",
        }
        reason_text = reason_map.get(status.last_run_reason or "", "—")
        result_icon = "✅" if status.last_run_success else "❌"
        result_label = "успешно" if status.last_run_success else "с ошибками"
        error_block = (
            f"\n⚠️ Ошибка: {status.last_run_error}"
            if status.last_run_error
            else ""
        )
        last_run_text = (
            f"{result_icon} {result_label}\n"
            f"• Старт: {started_text}\n"
            f"• Завершено: {finished_text}{duration_text}\n"
            f"• Причина запуска: {reason_text}{error_block}"
        )
    elif status.last_run_started_at:
        last_run_text = (
            "⏳ Синхронизация началась, но еще не завершилась"
            if status.is_running
            else f"ℹ️ Последний запуск: {format_datetime(status.last_run_started_at)}"
        )
    else:
        last_run_text = "—"

    running_text = "⏳ Выполняется сейчас" if status.is_running else "Ожидание"
    toggle_text = "❌ Отключить" if status.enabled else "✅ Включить"

    text = f"""🔄 <b>Автосинхронизация RemnaWave</b>

⚙️ <b>Статус:</b> {'✅ Включена' if status.enabled else '❌ Отключена'}
🕒 <b>Расписание:</b> {times_text}
📅 <b>Следующий запуск:</b> {next_run_text if status.enabled else '—'}
⏱️ <b>Состояние:</b> {running_text}

📊 <b>Последний запуск:</b>
{last_run_text}

👥 <b>Пользователи:</b>
{_format_user_stats(status.last_user_stats)}

🌐 <b>Серверы:</b>
{_format_server_stats(status.last_server_stats)}
"""

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="🔁 Запустить сейчас",
                    callback_data="remnawave_auto_sync_run",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=toggle_text,
                    callback_data="remnawave_auto_sync_toggle",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="🕒 Изменить расписание",
                    callback_data="remnawave_auto_sync_times",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data="admin_rw_sync",
                )
            ],
        ]
    )

    return text, keyboard


def _format_migration_server_label(texts, server) -> str:
    status = (
        texts.t("ADMIN_SQUAD_MIGRATION_STATUS_AVAILABLE", "✅ Доступен")
        if getattr(server, "is_available", True)
        else texts.t("ADMIN_SQUAD_MIGRATION_STATUS_UNAVAILABLE", "🚫 Недоступен")
    )
    return texts.t(
        "ADMIN_SQUAD_MIGRATION_SERVER_LABEL",
        "{name} — 👥 {users} ({status})",
    ).format(name=server.display_name, users=server.current_users, status=status)


def _build_migration_keyboard(
    texts,
    squads,
    page: int,
    total_pages: int,
    stage: str,
    *,
    exclude_uuid: str = None,
):
    prefix = "admin_migration_source" if stage == "source" else "admin_migration_target"
    rows = []
    has_items = False

    button_template = texts.t(
        "ADMIN_SQUAD_MIGRATION_SQUAD_BUTTON",
        "🌍 {name} — 👥 {users} ({status})",
    )

    for squad in squads:
        if exclude_uuid and squad.squad_uuid == exclude_uuid:
            continue

        has_items = True
        status = (
            texts.t("ADMIN_SQUAD_MIGRATION_STATUS_AVAILABLE_SHORT", "✅")
            if getattr(squad, "is_available", True)
            else texts.t("ADMIN_SQUAD_MIGRATION_STATUS_UNAVAILABLE_SHORT", "🚫")
        )
        rows.append(
            [
                types.InlineKeyboardButton(
                    text=button_template.format(
                        name=squad.display_name,
                        users=squad.current_users,
                        status=status,
                    ),
                    callback_data=f"{prefix}_{squad.squad_uuid}",
                )
            ]
        )

    if total_pages > 1:
        nav_buttons = []
        if page > 1:
            nav_buttons.append(
                types.InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"{prefix}_page_{page - 1}",
                )
            )
        nav_buttons.append(
            types.InlineKeyboardButton(
                text=texts.t(
                    "ADMIN_SQUAD_MIGRATION_PAGE",
                    "Стр. {page}/{pages}",
                ).format(page=page, pages=total_pages),
                callback_data="admin_migration_page_info",
            )
        )
        if page < total_pages:
            nav_buttons.append(
                types.InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"{prefix}_page_{page + 1}",
                )
            )
        rows.append(nav_buttons)

    rows.append(
        [
            types.InlineKeyboardButton(
                text=texts.CANCEL,
                callback_data="admin_migration_cancel",
            )
        ]
    )

    return types.InlineKeyboardMarkup(inline_keyboard=rows), has_items


async def _fetch_migration_page(
    db: AsyncSession,
    page: int,
):
    squads, total = await get_all_server_squads(
        db,
        page=max(1, page),
        limit=MIGRATION_PAGE_SIZE,
    )
    total_pages = max(1, math.ceil(total / MIGRATION_PAGE_SIZE))

    if page < 1:
        page = 1
    if page > total_pages:
        page = total_pages
        squads, total = await get_all_server_squads(
            db,
            page=page,
            limit=MIGRATION_PAGE_SIZE,
        )
        total_pages = max(1, math.ceil(total / MIGRATION_PAGE_SIZE))

    return squads, page, total_pages


@admin_required
@error_handler
async def show_squad_migration_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    texts = get_texts(db_user.language)

    await state.clear()

    squads, page, total_pages = await _fetch_migration_page(db, page=1)
    keyboard, has_items = _build_migration_keyboard(
        texts,
        squads,
        page,
        total_pages,
        "source",
    )

    message = (
        texts.t("ADMIN_SQUAD_MIGRATION_TITLE", "🚚 <b>Переезд сквадов</b>")
        + "\n\n"
        + texts.t(
            "ADMIN_SQUAD_MIGRATION_SELECT_SOURCE",
            "Выберите сквад, из которого нужно переехать:",
        )
    )

    if not has_items:
        message += (
            "\n\n"
            + texts.t(
                "ADMIN_SQUAD_MIGRATION_NO_OPTIONS",
                "Нет доступных сквадов. Добавьте новые или отмените операцию.",
            )
        )

    await state.set_state(SquadMigrationStates.selecting_source)

    await callback.message.edit_text(
        message,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )
    await callback.answer()


@admin_required
@error_handler
async def paginate_migration_source(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    if await state.get_state() != SquadMigrationStates.selecting_source:
        await callback.answer()
        return

    try:
        page = int(callback.data.split("_page_")[-1])
    except (ValueError, IndexError):
        await callback.answer()
        return

    squads, page, total_pages = await _fetch_migration_page(db, page=page)
    texts = get_texts(db_user.language)
    keyboard, has_items = _build_migration_keyboard(
        texts,
        squads,
        page,
        total_pages,
        "source",
    )

    message = (
        texts.t("ADMIN_SQUAD_MIGRATION_TITLE", "🚚 <b>Переезд сквадов</b>")
        + "\n\n"
        + texts.t(
            "ADMIN_SQUAD_MIGRATION_SELECT_SOURCE",
            "Выберите сквад, из которого нужно переехать:",
        )
    )

    if not has_items:
        message += (
            "\n\n"
            + texts.t(
                "ADMIN_SQUAD_MIGRATION_NO_OPTIONS",
                "Нет доступных сквадов. Добавьте новые или отмените операцию.",
            )
        )

    await callback.message.edit_text(
        message,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )
    await callback.answer()


@admin_required
@error_handler
async def handle_migration_source_selection(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    if await state.get_state() != SquadMigrationStates.selecting_source:
        await callback.answer()
        return

    if "_page_" in callback.data:
        await callback.answer()
        return

    source_uuid = callback.data.replace("admin_migration_source_", "", 1)

    texts = get_texts(db_user.language)
    server = await get_server_squad_by_uuid(db, source_uuid)

    if not server:
        await callback.answer(
            texts.t(
                "ADMIN_SQUAD_MIGRATION_SQUAD_NOT_FOUND",
                "Сквад не найден или недоступен.",
            ),
            show_alert=True,
        )
        return

    await state.update_data(
        source_uuid=server.squad_uuid,
        source_display=_format_migration_server_label(texts, server),
    )

    squads, page, total_pages = await _fetch_migration_page(db, page=1)
    keyboard, has_items = _build_migration_keyboard(
        texts,
        squads,
        page,
        total_pages,
        "target",
        exclude_uuid=server.squad_uuid,
    )

    message = (
        texts.t("ADMIN_SQUAD_MIGRATION_TITLE", "🚚 <b>Переезд сквадов</b>")
        + "\n\n"
        + texts.t(
            "ADMIN_SQUAD_MIGRATION_SELECTED_SOURCE",
            "Источник: {source}",
        ).format(source=_format_migration_server_label(texts, server))
        + "\n\n"
        + texts.t(
            "ADMIN_SQUAD_MIGRATION_SELECT_TARGET",
            "Выберите сквад, в который нужно переехать:",
        )
    )

    if not has_items:
        message += (
            "\n\n"
            + texts.t(
                "ADMIN_SQUAD_MIGRATION_TARGET_EMPTY",
                "Нет других сквадов для переезда. Отмените операцию или создайте новые сквады.",
            )
        )

    await state.set_state(SquadMigrationStates.selecting_target)

    await callback.message.edit_text(
        message,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )
    await callback.answer()


@admin_required
@error_handler
async def paginate_migration_target(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    if await state.get_state() != SquadMigrationStates.selecting_target:
        await callback.answer()
        return

    try:
        page = int(callback.data.split("_page_")[-1])
    except (ValueError, IndexError):
        await callback.answer()
        return

    data = await state.get_data()
    source_uuid = data.get("source_uuid")
    if not source_uuid:
        await callback.answer()
        return

    texts = get_texts(db_user.language)

    squads, page, total_pages = await _fetch_migration_page(db, page=page)
    keyboard, has_items = _build_migration_keyboard(
        texts,
        squads,
        page,
        total_pages,
        "target",
        exclude_uuid=source_uuid,
    )

    source_display = data.get("source_display") or source_uuid

    message = (
        texts.t("ADMIN_SQUAD_MIGRATION_TITLE", "🚚 <b>Переезд сквадов</b>")
        + "\n\n"
        + texts.t(
            "ADMIN_SQUAD_MIGRATION_SELECTED_SOURCE",
            "Источник: {source}",
        ).format(source=source_display)
        + "\n\n"
        + texts.t(
            "ADMIN_SQUAD_MIGRATION_SELECT_TARGET",
            "Выберите сквад, в который нужно переехать:",
        )
    )

    if not has_items:
        message += (
            "\n\n"
            + texts.t(
                "ADMIN_SQUAD_MIGRATION_TARGET_EMPTY",
                "Нет других сквадов для переезда. Отмените операцию или создайте новые сквады.",
            )
        )

    await callback.message.edit_text(
        message,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )
    await callback.answer()


@admin_required
@error_handler
async def handle_migration_target_selection(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    current_state = await state.get_state()
    if current_state != SquadMigrationStates.selecting_target:
        await callback.answer()
        return

    if "_page_" in callback.data:
        await callback.answer()
        return

    data = await state.get_data()
    source_uuid = data.get("source_uuid")

    if not source_uuid:
        await callback.answer()
        return

    target_uuid = callback.data.replace("admin_migration_target_", "", 1)

    texts = get_texts(db_user.language)

    if target_uuid == source_uuid:
        await callback.answer(
            texts.t(
                "ADMIN_SQUAD_MIGRATION_SAME_SQUAD",
                "Нельзя выбрать тот же сквад.",
            ),
            show_alert=True,
        )
        return

    target_server = await get_server_squad_by_uuid(db, target_uuid)
    if not target_server:
        await callback.answer(
            texts.t(
                "ADMIN_SQUAD_MIGRATION_SQUAD_NOT_FOUND",
                "Сквад не найден или недоступен.",
            ),
            show_alert=True,
        )
        return

    source_display = data.get("source_display") or source_uuid

    users_to_move = await count_active_users_for_squad(db, source_uuid)

    await state.update_data(
        target_uuid=target_server.squad_uuid,
        target_display=_format_migration_server_label(texts, target_server),
        migration_count=users_to_move,
    )

    await state.set_state(SquadMigrationStates.confirming)

    message_lines = [
        texts.t("ADMIN_SQUAD_MIGRATION_TITLE", "🚚 <b>Переезд сквадов</b>"),
        "",
        texts.t(
            "ADMIN_SQUAD_MIGRATION_CONFIRM_DETAILS",
            "Проверьте параметры переезда:",
        ),
        texts.t(
            "ADMIN_SQUAD_MIGRATION_CONFIRM_SOURCE",
            "• Из: {source}",
        ).format(source=source_display),
        texts.t(
            "ADMIN_SQUAD_MIGRATION_CONFIRM_TARGET",
            "• В: {target}",
        ).format(target=_format_migration_server_label(texts, target_server)),
        texts.t(
            "ADMIN_SQUAD_MIGRATION_CONFIRM_COUNT",
            "• Пользователей к переносу: {count}",
        ).format(count=users_to_move),
        "",
        texts.t(
            "ADMIN_SQUAD_MIGRATION_CONFIRM_PROMPT",
            "Подтвердите выполнение операции.",
        ),
    ]

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t(
                        "ADMIN_SQUAD_MIGRATION_CONFIRM_BUTTON",
                        "✅ Подтвердить",
                    ),
                    callback_data="admin_migration_confirm",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=texts.t(
                        "ADMIN_SQUAD_MIGRATION_CHANGE_TARGET",
                        "🔄 Изменить сервер назначения",
                    ),
                    callback_data="admin_migration_change_target",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=texts.CANCEL,
                    callback_data="admin_migration_cancel",
                )
            ],
        ]
    )

    await callback.message.edit_text(
        "\n".join(message_lines),
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )
    await callback.answer()


@admin_required
@error_handler
async def change_migration_target(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    data = await state.get_data()
    source_uuid = data.get("source_uuid")

    if not source_uuid:
        await callback.answer()
        return

    await state.set_state(SquadMigrationStates.selecting_target)

    texts = get_texts(db_user.language)
    squads, page, total_pages = await _fetch_migration_page(db, page=1)
    keyboard, has_items = _build_migration_keyboard(
        texts,
        squads,
        page,
        total_pages,
        "target",
        exclude_uuid=source_uuid,
    )

    source_display = data.get("source_display") or source_uuid

    message = (
        texts.t("ADMIN_SQUAD_MIGRATION_TITLE", "🚚 <b>Переезд сквадов</b>")
        + "\n\n"
        + texts.t(
            "ADMIN_SQUAD_MIGRATION_SELECTED_SOURCE",
            "Источник: {source}",
        ).format(source=source_display)
        + "\n\n"
        + texts.t(
            "ADMIN_SQUAD_MIGRATION_SELECT_TARGET",
            "Выберите сквад, в который нужно переехать:",
        )
    )

    if not has_items:
        message += (
            "\n\n"
            + texts.t(
                "ADMIN_SQUAD_MIGRATION_TARGET_EMPTY",
                "Нет других сквадов для переезда. Отмените операцию или создайте новые сквады.",
            )
        )

    await callback.message.edit_text(
        message,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )
    await callback.answer()


@admin_required
@error_handler
async def confirm_squad_migration(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    current_state = await state.get_state()
    if current_state != SquadMigrationStates.confirming:
        await callback.answer()
        return

    data = await state.get_data()
    source_uuid = data.get("source_uuid")
    target_uuid = data.get("target_uuid")

    if not source_uuid or not target_uuid:
        await callback.answer()
        return

    texts = get_texts(db_user.language)
    remnawave_service = RemnaWaveService()

    await callback.answer(texts.t("ADMIN_SQUAD_MIGRATION_IN_PROGRESS", "Запускаю переезд..."))

    try:
        result = await remnawave_service.migrate_squad_users(
            db,
            source_uuid=source_uuid,
            target_uuid=target_uuid,
        )
    except RemnaWaveConfigurationError as error:
        message = texts.t(
            "ADMIN_SQUAD_MIGRATION_API_ERROR",
            "❌ RemnaWave API не настроен: {error}",
        ).format(error=str(error))
        reply_markup = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=texts.t(
                            "ADMIN_SQUAD_MIGRATION_BACK_BUTTON",
                            "⬅️ В Remnawave",
                        ),
                        callback_data="admin_remnawave",
                    )
                ]
            ]
        )
        await callback.message.edit_text(message, reply_markup=reply_markup)
        await state.clear()
        return

    source_display = data.get("source_display") or source_uuid
    target_display = data.get("target_display") or target_uuid

    if not result.get("success"):
        error_message = result.get("message") or ""
        error_code = result.get("error") or "unexpected"
        message = texts.t(
            "ADMIN_SQUAD_MIGRATION_ERROR",
            "❌ Не удалось выполнить переезд (код: {code}). {details}",
        ).format(code=error_code, details=error_message)
        reply_markup = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=texts.t(
                            "ADMIN_SQUAD_MIGRATION_BACK_BUTTON",
                            "⬅️ В Remnawave",
                        ),
                        callback_data="admin_remnawave",
                    )
                ],
                [
                    types.InlineKeyboardButton(
                        text=texts.t(
                            "ADMIN_SQUAD_MIGRATION_NEW_BUTTON",
                            "🔁 Новый переезд",
                        ),
                        callback_data="admin_rw_migration",
                    )
                ],
            ]
        )
        await callback.message.edit_text(message, reply_markup=reply_markup)
        await state.clear()
        return

    message_lines = [
        texts.t("ADMIN_SQUAD_MIGRATION_SUCCESS_TITLE", "✅ Переезд завершен"),
        "",
        texts.t("ADMIN_SQUAD_MIGRATION_CONFIRM_SOURCE", "• Из: {source}").format(
            source=source_display
        ),
        texts.t("ADMIN_SQUAD_MIGRATION_CONFIRM_TARGET", "• В: {target}").format(
            target=target_display
        ),
        "",
        texts.t(
            "ADMIN_SQUAD_MIGRATION_RESULT_TOTAL",
            "Найдено подписок: {count}",
        ).format(count=result.get("total", 0)),
        texts.t(
            "ADMIN_SQUAD_MIGRATION_RESULT_UPDATED",
            "Перенесено: {count}",
        ).format(count=result.get("updated", 0)),
    ]

    panel_updated = result.get("panel_updated", 0)
    panel_failed = result.get("panel_failed", 0)

    if panel_updated:
        message_lines.append(
            texts.t(
                "ADMIN_SQUAD_MIGRATION_RESULT_PANEL_UPDATED",
                "Обновлено в панели: {count}",
            ).format(count=panel_updated)
        )
    if panel_failed:
        message_lines.append(
            texts.t(
                "ADMIN_SQUAD_MIGRATION_RESULT_PANEL_FAILED",
                "Не удалось обновить в панели: {count}",
            ).format(count=panel_failed)
        )

    reply_markup = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t(
                        "ADMIN_SQUAD_MIGRATION_NEW_BUTTON",
                        "🔁 Новый переезд",
                    ),
                    callback_data="admin_rw_migration",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=texts.t(
                        "ADMIN_SQUAD_MIGRATION_BACK_BUTTON",
                        "⬅️ В Remnawave",
                    ),
                    callback_data="admin_remnawave",
                )
            ],
        ]
    )

    await callback.message.edit_text(
        "\n".join(message_lines),
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )
    await state.clear()


@admin_required
@error_handler
async def cancel_squad_migration(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    await state.clear()

    message = texts.t(
        "ADMIN_SQUAD_MIGRATION_CANCELLED",
        "❌ Переезд отменен.",
    )

    reply_markup = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t(
                        "ADMIN_SQUAD_MIGRATION_BACK_BUTTON",
                        "⬅️ В Remnawave",
                    ),
                    callback_data="admin_remnawave",
                )
            ]
        ]
    )

    await callback.message.edit_text(message, reply_markup=reply_markup)
    await callback.answer()


@admin_required
@error_handler
async def handle_migration_page_info(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    await callback.answer(
        texts.t("ADMIN_SQUAD_MIGRATION_PAGE_HINT", "Это текущая страница."),
        show_alert=False,
    )

@admin_required
@error_handler
async def show_remnawave_menu(
   callback: types.CallbackQuery,
   db_user: User,
   db: AsyncSession
):
   remnawave_service = RemnaWaveService()
   connection_test = await remnawave_service.test_api_connection()

   status = connection_test.get("status")
   if status == "connected":
       status_emoji = "✅"
   elif status == "not_configured":
       status_emoji = "ℹ️"
   else:
       status_emoji = "❌"

   api_url_display = settings.REMNAWAVE_API_URL or "—"

   text = f"""
🖥️ <b>Управление Remnawave</b>

📡 <b>Соединение:</b> {status_emoji} {connection_test.get("message", "Нет данных")}
🌐 <b>URL:</b> <code>{api_url_display}</code>

Выберите действие:
"""
   
   await callback.message.edit_text(
       text,
       reply_markup=get_admin_remnawave_keyboard(db_user.language)
   )
   await callback.answer()


@admin_required
@error_handler
async def show_system_stats(
   callback: types.CallbackQuery,
   db_user: User,
   db: AsyncSession
):
   from datetime import datetime, timedelta
   
   remnawave_service = RemnaWaveService()
   stats = await remnawave_service.get_system_statistics()
   
   if "error" in stats:
       await callback.message.edit_text(
           f"❌ Ошибка получения статистики: {stats['error']}",
           reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
               [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_remnawave")]
           ])
       )
       await callback.answer()
       return
   
   system = stats.get("system", {})
   users_by_status = stats.get("users_by_status", {})
   server_info = stats.get("server_info", {})
   bandwidth = stats.get("bandwidth", {})
   traffic_periods = stats.get("traffic_periods", {})
   nodes_realtime = stats.get("nodes_realtime", [])
   nodes_weekly = stats.get("nodes_weekly", [])
   
   memory_total = server_info.get('memory_total', 1)
   memory_used_percent = (server_info.get('memory_used', 0) / memory_total * 100) if memory_total > 0 else 0
   
   uptime_seconds = server_info.get('uptime_seconds', 0)
   uptime_days = int(uptime_seconds // 86400)
   uptime_hours = int((uptime_seconds % 86400) // 3600)
   uptime_str = f"{uptime_days}д {uptime_hours}ч"
   
   users_status_text = ""
   for status, count in users_by_status.items():
       status_emoji = {
           'ACTIVE': '✅',
           'DISABLED': '❌', 
           'LIMITED': '⚠️',
           'EXPIRED': '⏰'
       }.get(status, '❓')
       users_status_text += f"  {status_emoji} {status}: {count}\n"
   
   top_nodes_text = ""
   for i, node in enumerate(nodes_weekly[:3], 1):
       top_nodes_text += f"  {i}. {node['name']}: {format_bytes(node['total_bytes'])}\n"
   
   realtime_nodes_text = ""
   for node in nodes_realtime[:3]:
       node_total = node.get('downloadBytes', 0) + node.get('uploadBytes', 0)
       if node_total > 0:
           realtime_nodes_text += f"  📡 {node.get('nodeName', 'Unknown')}: {format_bytes(node_total)}\n"
   
   def format_traffic_change(difference_str):
       if not difference_str or difference_str == '0':
           return ""
       elif difference_str.startswith('-'):
           return f" (🔻 {difference_str[1:]})"
       else:
           return f" (🔺 {difference_str})"
   
   text = f"""
📊 <b>Детальная статистика Remnawave</b>

🖥️ <b>Сервер:</b>
- CPU: {server_info.get('cpu_cores', 0)} ядер ({server_info.get('cpu_physical_cores', 0)} физ.)
- RAM: {format_bytes(server_info.get('memory_used', 0))} / {format_bytes(memory_total)} ({memory_used_percent:.1f}%)
- Свободно: {format_bytes(server_info.get('memory_available', 0))}
- Uptime: {uptime_str}

👥 <b>Пользователи ({system.get('total_users', 0)} всего):</b>
- 🟢 Онлайн сейчас: {system.get('users_online', 0)}
- 📅 За сутки: {system.get('users_last_day', 0)}
- 📊 За неделю: {system.get('users_last_week', 0)}
- 💤 Никогда не заходили: {system.get('users_never_online', 0)}

<b>Статусы пользователей:</b>
{users_status_text}

🌐 <b>Ноды ({system.get('nodes_online', 0)} онлайн):</b>"""

   if realtime_nodes_text:
       text += f"""
<b>Реалтайм активность:</b>
{realtime_nodes_text}"""
   
   if top_nodes_text:
       text += f"""
<b>Топ нод за неделю:</b>
{top_nodes_text}"""
   
   text += f"""

📈 <b>Общий трафик пользователей:</b> {format_bytes(system.get('total_user_traffic', 0))}

📊 <b>Трафик по периодам:</b>
- 2 дня: {format_bytes(traffic_periods.get('last_2_days', {}).get('current', 0))}{format_traffic_change(traffic_periods.get('last_2_days', {}).get('difference', ''))}
- 7 дней: {format_bytes(traffic_periods.get('last_7_days', {}).get('current', 0))}{format_traffic_change(traffic_periods.get('last_7_days', {}).get('difference', ''))}
- 30 дней: {format_bytes(traffic_periods.get('last_30_days', {}).get('current', 0))}{format_traffic_change(traffic_periods.get('last_30_days', {}).get('difference', ''))}
- Месяц: {format_bytes(traffic_periods.get('current_month', {}).get('current', 0))}{format_traffic_change(traffic_periods.get('current_month', {}).get('difference', ''))}
- Год: {format_bytes(traffic_periods.get('current_year', {}).get('current', 0))}{format_traffic_change(traffic_periods.get('current_year', {}).get('difference', ''))}
"""

   if bandwidth.get('realtime_total', 0) > 0:
       text += f"""
⚡ <b>Реалтайм трафик:</b>
- Скачивание: {format_bytes(bandwidth.get('realtime_download', 0))}
- Загрузка: {format_bytes(bandwidth.get('realtime_upload', 0))}
- Итого: {format_bytes(bandwidth.get('realtime_total', 0))}
"""
   
   text += f"""
🕒 <b>Обновлено:</b> {format_datetime(stats.get('last_updated', datetime.now()))}
"""
   
   keyboard = [
       [types.InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_rw_system")],
       [types.InlineKeyboardButton(text="📈 Ноды", callback_data="admin_rw_nodes"),
        types.InlineKeyboardButton(text="👥 Синхронизация", callback_data="admin_rw_sync")],
       [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_remnawave")]
   ]
   
   await callback.message.edit_text(
       text,
       reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
   )
   await callback.answer()

@admin_required
@error_handler
async def show_traffic_stats(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    from datetime import datetime, timedelta
    
    remnawave_service = RemnaWaveService()
    
    try:
        async with remnawave_service.get_api_client() as api:
            bandwidth_stats = await api.get_bandwidth_stats()
            
            realtime_usage = await api.get_nodes_realtime_usage()
            
            nodes_stats = await api.get_nodes_statistics()
            
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Ошибка получения статистики трафика: {str(e)}",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_remnawave")]
            ])
        )
        await callback.answer()
        return
    
    def parse_bandwidth(bandwidth_str):
        return remnawave_service._parse_bandwidth_string(bandwidth_str)
    
    total_realtime_download = sum(node.get('downloadBytes', 0) for node in realtime_usage)
    total_realtime_upload = sum(node.get('uploadBytes', 0) for node in realtime_usage)
    total_realtime = total_realtime_download + total_realtime_upload
    
    total_download_speed = sum(node.get('downloadSpeedBps', 0) for node in realtime_usage)
    total_upload_speed = sum(node.get('uploadSpeedBps', 0) for node in realtime_usage)
    
    periods = {
        'last_2_days': bandwidth_stats.get('bandwidthLastTwoDays', {}),
        'last_7_days': bandwidth_stats.get('bandwidthLastSevenDays', {}),
        'last_30_days': bandwidth_stats.get('bandwidthLast30Days', {}),
        'current_month': bandwidth_stats.get('bandwidthCalendarMonth', {}),
        'current_year': bandwidth_stats.get('bandwidthCurrentYear', {})
    }
    
    def format_change(diff_str):
        if not diff_str or diff_str == '0':
            return ""
        elif diff_str.startswith('-'):
            return f" 🔻 {diff_str[1:]}"
        else:
            return f" 🔺 {diff_str}"
    
    text = f"""
📊 <b>Статистика трафика Remnawave</b>

⚡ <b>Реалтайм данные:</b>
- Скачивание: {format_bytes(total_realtime_download)}
- Загрузка: {format_bytes(total_realtime_upload)}
- Общий трафик: {format_bytes(total_realtime)}

🚀 <b>Текущие скорости:</b>
- Скорость скачивания: {format_bytes(total_download_speed)}/с
- Скорость загрузки: {format_bytes(total_upload_speed)}/с
- Общая скорость: {format_bytes(total_download_speed + total_upload_speed)}/с

📈 <b>Статистика по периодам:</b>

<b>За 2 дня:</b>
- Текущий: {format_bytes(parse_bandwidth(periods['last_2_days'].get('current', '0')))}
- Предыдущий: {format_bytes(parse_bandwidth(periods['last_2_days'].get('previous', '0')))}
- Изменение:{format_change(periods['last_2_days'].get('difference', ''))}

<b>За 7 дней:</b>
- Текущий: {format_bytes(parse_bandwidth(periods['last_7_days'].get('current', '0')))}
- Предыдущий: {format_bytes(parse_bandwidth(periods['last_7_days'].get('previous', '0')))}
- Изменение:{format_change(periods['last_7_days'].get('difference', ''))}

<b>За 30 дней:</b>
- Текущий: {format_bytes(parse_bandwidth(periods['last_30_days'].get('current', '0')))}
- Предыдущий: {format_bytes(parse_bandwidth(periods['last_30_days'].get('previous', '0')))}
- Изменение:{format_change(periods['last_30_days'].get('difference', ''))}

<b>Текущий месяц:</b>
- Текущий: {format_bytes(parse_bandwidth(periods['current_month'].get('current', '0')))}
- Предыдущий: {format_bytes(parse_bandwidth(periods['current_month'].get('previous', '0')))}
- Изменение:{format_change(periods['current_month'].get('difference', ''))}

<b>Текущий год:</b>
- Текущий: {format_bytes(parse_bandwidth(periods['current_year'].get('current', '0')))}
- Предыдущий: {format_bytes(parse_bandwidth(periods['current_year'].get('previous', '0')))}
- Изменение:{format_change(periods['current_year'].get('difference', ''))}
"""
    
    if realtime_usage:
        text += "\n🌐 <b>Трафик по нодам (реалтайм):</b>\n"
        for node in sorted(realtime_usage, key=lambda x: x.get('totalBytes', 0), reverse=True):
            node_total = node.get('totalBytes', 0)
            if node_total > 0:
                text += f"- {node.get('nodeName', 'Unknown')}: {format_bytes(node_total)}\n"
    
    if nodes_stats.get('lastSevenDays'):
        text += "\n📊 <b>Топ нод за 7 дней:</b>\n"
        
        nodes_weekly = {}
        for day_data in nodes_stats['lastSevenDays']:
            node_name = day_data['nodeName']
            if node_name not in nodes_weekly:
                nodes_weekly[node_name] = 0
            nodes_weekly[node_name] += int(day_data['totalBytes'])
        
        sorted_nodes = sorted(nodes_weekly.items(), key=lambda x: x[1], reverse=True)
        for i, (node_name, total_bytes) in enumerate(sorted_nodes[:5], 1):
            text += f"{i}. {node_name}: {format_bytes(total_bytes)}\n"
    
    text += f"\n🕒 <b>Обновлено:</b> {format_datetime(datetime.now())}"
    
    keyboard = [
        [types.InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_rw_traffic")],
        [types.InlineKeyboardButton(text="📈 Ноды", callback_data="admin_rw_nodes"),
         types.InlineKeyboardButton(text="📊 Система", callback_data="admin_rw_system")],
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_remnawave")]
    ]
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@admin_required
@error_handler
async def show_nodes_management(
   callback: types.CallbackQuery,
   db_user: User,
   db: AsyncSession
):
   remnawave_service = RemnaWaveService()
   nodes = await remnawave_service.get_all_nodes()
   
   if not nodes:
       await callback.message.edit_text(
           "🖥️ Ноды не найдены или ошибка подключения",
           reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
               [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_remnawave")]
           ])
       )
       await callback.answer()
       return
   
   text = "🖥️ <b>Управление нодами</b>\n\n"
   keyboard = []
   
   for node in nodes:
       status_emoji = "🟢" if node["is_node_online"] else "🔴"
       connection_emoji = "📡" if node["is_connected"] else "📵"
       
       text += f"{status_emoji} {connection_emoji} <b>{node['name']}</b>\n"
       text += f"🌍 {node['country_code']} • {node['address']}\n"
       text += f"👥 Онлайн: {node['users_online'] or 0}\n\n"
       
       keyboard.append([
           types.InlineKeyboardButton(
               text=f"⚙️ {node['name']}",
               callback_data=f"admin_node_manage_{node['uuid']}"
           )
       ])
   
   keyboard.extend([
       [types.InlineKeyboardButton(text="🔄 Перезагрузить все", callback_data="admin_restart_all_nodes")],
       [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_remnawave")]
   ])
   
   await callback.message.edit_text(
       text,
       reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
   )
   await callback.answer()


@admin_required
@error_handler
async def show_node_details(
   callback: types.CallbackQuery,
   db_user: User,
   db: AsyncSession
):
   node_uuid = callback.data.split('_')[-1]
   
   remnawave_service = RemnaWaveService()
   node = await remnawave_service.get_node_details(node_uuid)
   
   if not node:
       await callback.answer("❌ Нода не найдена", show_alert=True)
       return
   
   status_emoji = "🟢" if node["is_node_online"] else "🔴"
   xray_emoji = "✅" if node["is_xray_running"] else "❌"
   
   text = f"""
🖥️ <b>Нода: {node['name']}</b>

<b>Статус:</b>
- Онлайн: {status_emoji} {'Да' if node['is_node_online'] else 'Нет'}
- Xray: {xray_emoji} {'Запущен' if node['is_xray_running'] else 'Остановлен'}
- Подключена: {'📡 Да' if node['is_connected'] else '📵 Нет'}
- Отключена: {'❌ Да' if node['is_disabled'] else '✅ Нет'}

<b>Информация:</b>
- Адрес: {node['address']}
- Страна: {node['country_code']}
- Пользователей онлайн: {node['users_online']}

<b>Трафик:</b>
- Использовано: {format_bytes(node['traffic_used_bytes'])}
- Лимит: {format_bytes(node['traffic_limit_bytes']) if node['traffic_limit_bytes'] else 'Без лимита'}
"""
   
   await callback.message.edit_text(
       text,
       reply_markup=get_node_management_keyboard(node_uuid, db_user.language)
   )
   await callback.answer()


@admin_required
@error_handler
async def manage_node(
   callback: types.CallbackQuery,
   db_user: User,
   db: AsyncSession
):
   action, node_uuid = callback.data.split('_')[1], callback.data.split('_')[-1]
   
   remnawave_service = RemnaWaveService()
   success = await remnawave_service.manage_node(node_uuid, action)
   
   if success:
       action_text = {"enable": "включена", "disable": "отключена", "restart": "перезагружена"}
       await callback.answer(f"✅ Нода {action_text.get(action, 'обработана')}")
   else:
       await callback.answer("❌ Ошибка выполнения действия", show_alert=True)
   
   await show_node_details(
       types.CallbackQuery(
           id=callback.id,
           from_user=callback.from_user,
           chat_instance=callback.chat_instance,
           data=f"admin_node_manage_{node_uuid}",
           message=callback.message
       ),
       db_user,
       db
   )

@admin_required
@error_handler
async def show_node_statistics(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    node_uuid = callback.data.split('_')[-1]
    
    remnawave_service = RemnaWaveService()
    
    node = await remnawave_service.get_node_details(node_uuid)
    
    if not node:
        await callback.answer("❌ Нода не найдена", show_alert=True)
        return
    
    try:
        from datetime import datetime, timedelta
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)
        
        node_usage = await remnawave_service.get_node_user_usage_by_range(
            node_uuid, start_date, end_date
        )
        
        realtime_stats = await remnawave_service.get_nodes_realtime_usage()
        
        node_realtime = None
        for stats in realtime_stats:
            if stats.get('nodeUuid') == node_uuid:
                node_realtime = stats
                break
        
        status_emoji = "🟢" if node["is_node_online"] else "🔴"
        xray_emoji = "✅" if node["is_xray_running"] else "❌"
        
        text = f"""
📊 <b>Статистика ноды: {node['name']}</b>

<b>Статус:</b>
- Онлайн: {status_emoji} {'Да' if node['is_node_online'] else 'Нет'}
- Xray: {xray_emoji} {'Запущен' if node['is_xray_running'] else 'Остановлен'}
- Пользователей онлайн: {node['users_online'] or 0}

<b>Трафик:</b>
- Использовано: {format_bytes(node['traffic_used_bytes'] or 0)}
- Лимит: {format_bytes(node['traffic_limit_bytes']) if node['traffic_limit_bytes'] else 'Без лимита'}
"""

        if node_realtime:
            text += f"""
<b>Реалтайм статистика:</b>
- Скачано: {format_bytes(node_realtime.get('downloadBytes', 0))}
- Загружено: {format_bytes(node_realtime.get('uploadBytes', 0))}
- Общий трафик: {format_bytes(node_realtime.get('totalBytes', 0))}
- Скорость скачивания: {format_bytes(node_realtime.get('downloadSpeedBps', 0))}/с
- Скорость загрузки: {format_bytes(node_realtime.get('uploadSpeedBps', 0))}/с
"""

        if node_usage:
            text += f"\n<b>Статистика за 7 дней:</b>\n"
            total_usage = 0
            for usage in node_usage[-5:]: 
                daily_usage = usage.get('total', 0)
                total_usage += daily_usage
                text += f"- {usage.get('date', 'N/A')}: {format_bytes(daily_usage)}\n"
            
            text += f"\n<b>Общий трафик за 7 дней:</b> {format_bytes(total_usage)}"
        else:
            text += "\n<b>Статистика за 7 дней:</b> Данные недоступны"
        
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🔄 Обновить", callback_data=f"node_stats_{node_uuid}")],
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin_node_manage_{node_uuid}")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard)
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка получения статистики ноды {node_uuid}: {e}")
        
        text = f"""
📊 <b>Статистика ноды: {node['name']}</b>

<b>Статус:</b>
- Онлайн: {status_emoji} {'Да' if node['is_node_online'] else 'Нет'}  
- Xray: {xray_emoji} {'Запущен' if node['is_xray_running'] else 'Остановлен'}
- Пользователей онлайн: {node['users_online'] or 0}

<b>Трафик:</b>
- Использовано: {format_bytes(node['traffic_used_bytes'] or 0)}
- Лимит: {format_bytes(node['traffic_limit_bytes']) if node['traffic_limit_bytes'] else 'Без лимита'}

⚠️ <b>Детальная статистика временно недоступна</b>
Возможные причины:
• Проблемы с подключением к API
• Нода недавно добавлена
• Недостаточно данных для отображения

<b>Обновлено:</b> {format_datetime('now')}
"""
        
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🔄 Попробовать снова", callback_data=f"node_stats_{node_uuid}")],
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin_node_manage_{node_uuid}")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard)
        await callback.answer()

@admin_required
@error_handler
async def show_squad_details(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    squad_uuid = callback.data.split('_')[-1]
    
    remnawave_service = RemnaWaveService()
    squad = await remnawave_service.get_squad_details(squad_uuid)
    
    if not squad:
        await callback.answer("❌ Сквад не найден", show_alert=True)
        return
    
    text = f"""
🌐 <b>Сквад: {squad['name']}</b>

<b>Информация:</b>
- UUID: <code>{squad['uuid']}</code>
- Участников: {squad['members_count']}
- Инбаундов: {squad['inbounds_count']}

<b>Инбаунды:</b>
"""
    
    if squad.get('inbounds'):
        for inbound in squad['inbounds']:
            text += f"- {inbound['tag']} ({inbound['type']})\n"
    else:
        text += "Нет активных инбаундов"
    
    await callback.message.edit_text(
        text,
        reply_markup=get_squad_management_keyboard(squad_uuid, db_user.language)
    )
    await callback.answer()


@admin_required
@error_handler
async def manage_squad_action(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    parts = callback.data.split('_')
    action = parts[1] 
    squad_uuid = parts[-1]
    
    remnawave_service = RemnaWaveService()
    
    if action == "add_users":
        success = await remnawave_service.add_all_users_to_squad(squad_uuid)
        if success:
            await callback.answer("✅ Задача добавления пользователей в очередь")
        else:
            await callback.answer("❌ Ошибка добавления пользователей", show_alert=True)
            
    elif action == "remove_users":
        success = await remnawave_service.remove_all_users_from_squad(squad_uuid)
        if success:
            await callback.answer("✅ Задача удаления пользователей в очередь")
        else:
            await callback.answer("❌ Ошибка удаления пользователей", show_alert=True)
            
    elif action == "delete":
        success = await remnawave_service.delete_squad(squad_uuid)
        if success:
            await callback.message.edit_text(
                "✅ Сквад успешно удален",
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text="⬅️ К сквадам", callback_data="admin_rw_squads")]
                ])
            )
        else:
            await callback.answer("❌ Ошибка удаления сквада", show_alert=True)
        return
    
    await show_squad_details(
        types.CallbackQuery(
            id=callback.id,
            from_user=callback.from_user,
            chat_instance=callback.chat_instance,
            data=f"admin_squad_manage_{squad_uuid}",
            message=callback.message
        ),
        db_user,
        db
    )

@admin_required
@error_handler
async def show_squad_edit_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    squad_uuid = callback.data.split('_')[-1]
    
    remnawave_service = RemnaWaveService()
    squad = await remnawave_service.get_squad_details(squad_uuid)
    
    if not squad:
        await callback.answer("❌ Сквад не найден", show_alert=True)
        return
    
    text = f"""
✏️ <b>Редактирование сквада: {squad['name']}</b>

<b>Текущие инбаунды:</b>
"""
    
    if squad.get('inbounds'):
        for inbound in squad['inbounds']:
            text += f"✅ {inbound['tag']} ({inbound['type']})\n"
    else:
        text += "Нет активных инбаундов\n"
    
    text += "\n<b>Доступные действия:</b>"
    
    await callback.message.edit_text(
        text,
        reply_markup=get_squad_edit_keyboard(squad_uuid, db_user.language)
    )
    await callback.answer()

@admin_required
@error_handler
async def show_squad_inbounds_selection(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    squad_uuid = callback.data.split('_')[-1]
    
    remnawave_service = RemnaWaveService()
    
    squad = await remnawave_service.get_squad_details(squad_uuid)
    all_inbounds = await remnawave_service.get_all_inbounds()
    
    if not squad:
        await callback.answer("❌ Сквад не найден", show_alert=True)
        return
    
    if not all_inbounds:
        await callback.answer("❌ Нет доступных инбаундов", show_alert=True)
        return
    
    if squad_uuid not in squad_inbound_selections:
        squad_inbound_selections[squad_uuid] = set(
            inbound['uuid'] for inbound in squad.get('inbounds', [])
        )
    
    text = f"""
🔧 <b>Изменение инбаундов</b>

<b>Сквад:</b> {squad['name']}
<b>Текущих инбаундов:</b> {len(squad_inbound_selections[squad_uuid])}

<b>Доступные инбаунды:</b>
"""
    
    keyboard = []
    
    for i, inbound in enumerate(all_inbounds[:15]): 
        is_selected = inbound['uuid'] in squad_inbound_selections[squad_uuid]
        emoji = "✅" if is_selected else "☐"
        
        keyboard.append([
            types.InlineKeyboardButton(
                text=f"{emoji} {inbound['tag']} ({inbound['type']})",
                callback_data=f"sqd_tgl_{i}_{squad_uuid[:8]}"
            )
        ])
    
    if len(all_inbounds) > 15:
        text += f"\n⚠️ Показано первые 15 из {len(all_inbounds)} инбаундов"
    
    keyboard.extend([
        [types.InlineKeyboardButton(text="💾 Сохранить изменения", callback_data=f"sqd_save_{squad_uuid[:8]}")],
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"sqd_edit_{squad_uuid[:8]}")]
    ])
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()

@admin_required
@error_handler
async def show_squad_rename_form(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    squad_uuid = callback.data.split('_')[-1]
    
    remnawave_service = RemnaWaveService()
    squad = await remnawave_service.get_squad_details(squad_uuid)
    
    if not squad:
        await callback.answer("❌ Сквад не найден", show_alert=True)
        return
    
    await state.update_data(squad_uuid=squad_uuid, squad_name=squad['name'])
    await state.set_state(SquadRenameStates.waiting_for_new_name)
    
    text = f"""
✏️ <b>Переименование сквада</b>

<b>Текущее название:</b> {squad['name']}

📝 <b>Введите новое название сквада:</b>

<i>Требования к названию:</i>
• От 2 до 20 символов
• Только буквы, цифры, дефисы и подчеркивания
• Без пробелов и специальных символов

Отправьте сообщение с новым названием или нажмите "Отмена" для выхода.
"""
    
    keyboard = [
        [types.InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel_rename_{squad_uuid}")]
    ]
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()

@admin_required
@error_handler
async def cancel_squad_rename(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    squad_uuid = callback.data.split('_')[-1]
    
    await state.clear()
    
    new_callback = types.CallbackQuery(
        id=callback.id,
        from_user=callback.from_user,
        chat_instance=callback.chat_instance,
        data=f"squad_edit_{squad_uuid}",
        message=callback.message
    )
    
    await show_squad_edit_menu(new_callback, db_user, db)

@admin_required
@error_handler
async def process_squad_new_name(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    data = await state.get_data()
    squad_uuid = data.get('squad_uuid')
    old_name = data.get('squad_name')
    
    if not squad_uuid:
        await message.answer("❌ Ошибка: сквад не найден")
        await state.clear()
        return
    
    new_name = message.text.strip()
    
    if not new_name:
        await message.answer("❌ Название не может быть пустым. Попробуйте еще раз:")
        return
    
    if len(new_name) < 2 or len(new_name) > 20:
        await message.answer("❌ Название должно быть от 2 до 20 символов. Попробуйте еще раз:")
        return
    
    import re
    if not re.match(r'^[A-Za-z0-9_-]+$', new_name):
        await message.answer("❌ Название может содержать только буквы, цифры, дефисы и подчеркивания. Попробуйте еще раз:")
        return
    
    if new_name == old_name:
        await message.answer("❌ Новое название совпадает с текущим. Введите другое название:")
        return
    
    remnawave_service = RemnaWaveService()
    success = await remnawave_service.rename_squad(squad_uuid, new_name)
    
    if success:
        await message.answer(
            f"✅ <b>Сквад успешно переименован!</b>\n\n"
            f"<b>Старое название:</b> {old_name}\n"
            f"<b>Новое название:</b> {new_name}",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="📋 Детали сквада", callback_data=f"admin_squad_manage_{squad_uuid}")],
                [types.InlineKeyboardButton(text="⬅️ К сквадам", callback_data="admin_rw_squads")]
            ])
        )
        await state.clear()
    else:
        await message.answer(
            "❌ <b>Ошибка переименования сквада</b>\n\n"
            "Возможные причины:\n"
            "• Сквад с таким названием уже существует\n"
            "• Проблемы с подключением к API\n"
            "• Недостаточно прав\n\n"
            "Попробуйте другое название:",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel_rename_{squad_uuid}")]
            ])
        )


@admin_required
@error_handler
async def toggle_squad_inbound(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    parts = callback.data.split('_')
    inbound_index = int(parts[2])
    short_squad_uuid = parts[3]
    
    remnawave_service = RemnaWaveService()
    squads = await remnawave_service.get_all_squads()
    
    full_squad_uuid = None
    for squad in squads:
        if squad['uuid'].startswith(short_squad_uuid):
            full_squad_uuid = squad['uuid']
            break
    
    if not full_squad_uuid:
        await callback.answer("❌ Сквад не найден", show_alert=True)
        return
    
    all_inbounds = await remnawave_service.get_all_inbounds()
    if inbound_index >= len(all_inbounds):
        await callback.answer("❌ Инбаунд не найден", show_alert=True)
        return
    
    selected_inbound = all_inbounds[inbound_index]
    
    if full_squad_uuid not in squad_inbound_selections:
        squad_inbound_selections[full_squad_uuid] = set()
    
    if selected_inbound['uuid'] in squad_inbound_selections[full_squad_uuid]:
        squad_inbound_selections[full_squad_uuid].remove(selected_inbound['uuid'])
        await callback.answer(f"➖ Убран: {selected_inbound['tag']}")
    else:
        squad_inbound_selections[full_squad_uuid].add(selected_inbound['uuid'])
        await callback.answer(f"➕ Добавлен: {selected_inbound['tag']}")
    
    text = f"""
🔧 <b>Изменение инбаундов</b>

<b>Сквад:</b> {squads[0]['name'] if squads else 'Неизвестно'}
<b>Выбрано инбаундов:</b> {len(squad_inbound_selections[full_squad_uuid])}

<b>Доступные инбаунды:</b>
"""
    
    keyboard = []
    for i, inbound in enumerate(all_inbounds[:15]):
        is_selected = inbound['uuid'] in squad_inbound_selections[full_squad_uuid]
        emoji = "✅" if is_selected else "☐"
        
        keyboard.append([
            types.InlineKeyboardButton(
                text=f"{emoji} {inbound['tag']} ({inbound['type']})",
                callback_data=f"sqd_tgl_{i}_{short_squad_uuid}"
            )
        ])
    
    keyboard.extend([
        [types.InlineKeyboardButton(text="💾 Сохранить изменения", callback_data=f"sqd_save_{short_squad_uuid}")],
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"sqd_edit_{short_squad_uuid}")]
    ])
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )


@admin_required  
@error_handler
async def save_squad_inbounds(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    short_squad_uuid = callback.data.split('_')[-1]
    
    remnawave_service = RemnaWaveService()
    squads = await remnawave_service.get_all_squads()
    
    full_squad_uuid = None
    squad_name = None
    for squad in squads:
        if squad['uuid'].startswith(short_squad_uuid):
            full_squad_uuid = squad['uuid']
            squad_name = squad['name']
            break
    
    if not full_squad_uuid:
        await callback.answer("❌ Сквад не найден", show_alert=True)
        return
    
    selected_inbounds = squad_inbound_selections.get(full_squad_uuid, set())
    
    try:
        success = await remnawave_service.update_squad_inbounds(full_squad_uuid, list(selected_inbounds))
        
        if success:
            if full_squad_uuid in squad_inbound_selections:
                del squad_inbound_selections[full_squad_uuid]
            
            await callback.message.edit_text(
                f"✅ <b>Инбаунды сквада обновлены</b>\n\n"
                f"<b>Сквад:</b> {squad_name}\n"
                f"<b>Количество инбаундов:</b> {len(selected_inbounds)}",
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text="⬅️ К сквадам", callback_data="admin_rw_squads")],
                    [types.InlineKeyboardButton(text="📋 Детали сквада", callback_data=f"admin_squad_manage_{full_squad_uuid}")]
                ])
            )
            await callback.answer("✅ Изменения сохранены!")
        else:
            await callback.answer("❌ Ошибка сохранения изменений", show_alert=True)
            
    except Exception as e:
        logger.error(f"Error saving squad inbounds: {e}")
        await callback.answer("❌ Ошибка при сохранении", show_alert=True)

@admin_required
@error_handler
async def show_squad_edit_menu_short(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    short_squad_uuid = callback.data.split('_')[-1]
    
    remnawave_service = RemnaWaveService()
    squads = await remnawave_service.get_all_squads()
    
    full_squad_uuid = None
    for squad in squads:
        if squad['uuid'].startswith(short_squad_uuid):
            full_squad_uuid = squad['uuid']
            break
    
    if not full_squad_uuid:
        await callback.answer("❌ Сквад не найден", show_alert=True)
        return
    
    new_callback = types.CallbackQuery(
        id=callback.id,
        from_user=callback.from_user,
        chat_instance=callback.chat_instance,
        data=f"squad_edit_{full_squad_uuid}",
        message=callback.message
    )
    
    await show_squad_edit_menu(new_callback, db_user, db)

@admin_required
@error_handler
async def start_squad_creation(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    await state.set_state(SquadCreateStates.waiting_for_name)
    
    text = """
➕ <b>Создание нового сквада</b>

<b>Шаг 1 из 2: Название сквада</b>

📝 <b>Введите название для нового сквада:</b>

<i>Требования к названию:</i>
• От 2 до 20 символов
• Только буквы, цифры, дефисы и подчеркивания
• Без пробелов и специальных символов

Отправьте сообщение с названием или нажмите "Отмена" для выхода.
"""
    
    keyboard = [
        [types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_squad_create")]
    ]
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@admin_required
@error_handler
async def process_squad_name(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    squad_name = message.text.strip()
    
    if not squad_name:
        await message.answer("❌ Название не может быть пустым. Попробуйте еще раз:")
        return
    
    if len(squad_name) < 2 or len(squad_name) > 20:
        await message.answer("❌ Название должно быть от 2 до 20 символов. Попробуйте еще раз:")
        return
    
    import re
    if not re.match(r'^[A-Za-z0-9_-]+$', squad_name):
        await message.answer("❌ Название может содержать только буквы, цифры, дефисы и подчеркивания. Попробуйте еще раз:")
        return
    
    await state.update_data(squad_name=squad_name)
    await state.set_state(SquadCreateStates.selecting_inbounds)
    
    user_id = message.from_user.id
    squad_create_data[user_id] = {'name': squad_name, 'selected_inbounds': set()}
    
    remnawave_service = RemnaWaveService()
    all_inbounds = await remnawave_service.get_all_inbounds()
    
    if not all_inbounds:
        await message.answer(
            "❌ <b>Нет доступных инбаундов</b>\n\n"
            "Для создания сквада необходимо иметь хотя бы один инбаунд.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="⬅️ К сквадам", callback_data="admin_rw_squads")]
            ])
        )
        await state.clear()
        return
    
    text = f"""
➕ <b>Создание сквада: {squad_name}</b>

<b>Шаг 2 из 2: Выбор инбаундов</b>

<b>Выбрано инбаундов:</b> 0

<b>Доступные инбаунды:</b>
"""
    
    keyboard = []
    
    for i, inbound in enumerate(all_inbounds[:15]): 
        keyboard.append([
            types.InlineKeyboardButton(
                text=f"☐ {inbound['tag']} ({inbound['type']})",
                callback_data=f"create_tgl_{i}"
            )
        ])
    
    if len(all_inbounds) > 15:
        text += f"\n⚠️ Показано первые 15 из {len(all_inbounds)} инбаундов"
    
    keyboard.extend([
        [types.InlineKeyboardButton(text="✅ Создать сквад", callback_data="create_squad_finish")],
        [types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_squad_create")]
    ])
    
    await message.answer(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

@admin_required
@error_handler
async def toggle_create_inbound(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    inbound_index = int(callback.data.split('_')[-1])
    user_id = callback.from_user.id
    
    if user_id not in squad_create_data:
        await callback.answer("❌ Ошибка: данные сессии не найдены", show_alert=True)
        await state.clear()
        return
    
    remnawave_service = RemnaWaveService()
    all_inbounds = await remnawave_service.get_all_inbounds()
    
    if inbound_index >= len(all_inbounds):
        await callback.answer("❌ Инбаунд не найден", show_alert=True)
        return
    
    selected_inbound = all_inbounds[inbound_index]
    selected_inbounds = squad_create_data[user_id]['selected_inbounds']
    
    if selected_inbound['uuid'] in selected_inbounds:
        selected_inbounds.remove(selected_inbound['uuid'])
        await callback.answer(f"➖ Убран: {selected_inbound['tag']}")
    else:
        selected_inbounds.add(selected_inbound['uuid'])
        await callback.answer(f"➕ Добавлен: {selected_inbound['tag']}")
    
    squad_name = squad_create_data[user_id]['name']
    
    text = f"""
➕ <b>Создание сквада: {squad_name}</b>

<b>Шаг 2 из 2: Выбор инбаундов</b>

<b>Выбрано инбаундов:</b> {len(selected_inbounds)}

<b>Доступные инбаунды:</b>
"""
    
    keyboard = []
    
    for i, inbound in enumerate(all_inbounds[:15]):
        is_selected = inbound['uuid'] in selected_inbounds
        emoji = "✅" if is_selected else "☐"
        
        keyboard.append([
            types.InlineKeyboardButton(
                text=f"{emoji} {inbound['tag']} ({inbound['type']})",
                callback_data=f"create_tgl_{i}"
            )
        ])
    
    keyboard.extend([
        [types.InlineKeyboardButton(text="✅ Создать сквад", callback_data="create_squad_finish")],
        [types.InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_squad_create")]
    ])
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

@admin_required
@error_handler
async def finish_squad_creation(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    user_id = callback.from_user.id
    
    if user_id not in squad_create_data:
        await callback.answer("❌ Ошибка: данные сессии не найдены", show_alert=True)
        await state.clear()
        return
    
    squad_name = squad_create_data[user_id]['name']
    selected_inbounds = list(squad_create_data[user_id]['selected_inbounds'])
    
    if not selected_inbounds:
        await callback.answer("❌ Необходимо выбрать хотя бы один инбаунд", show_alert=True)
        return
    
    remnawave_service = RemnaWaveService()
    success = await remnawave_service.create_squad(squad_name, selected_inbounds)
    
    if user_id in squad_create_data:
        del squad_create_data[user_id]
    await state.clear()
    
    if success:
        await callback.message.edit_text(
            f"✅ <b>Сквад успешно создан!</b>\n\n"
            f"<b>Название:</b> {squad_name}\n"
            f"<b>Количество инбаундов:</b> {len(selected_inbounds)}\n\n"
            f"Сквад готов к использованию!",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="📋 Список сквадов", callback_data="admin_rw_squads")],
                [types.InlineKeyboardButton(text="⬅️ К панели Remnawave", callback_data="admin_remnawave")]
            ])
        )
        await callback.answer("✅ Сквад создан!")
    else:
        await callback.message.edit_text(
            f"❌ <b>Ошибка создания сквада</b>\n\n"
            f"<b>Название:</b> {squad_name}\n\n"
            f"Возможные причины:\n"
            f"• Сквад с таким названием уже существует\n"
            f"• Проблемы с подключением к API\n"
            f"• Недостаточно прав\n"
            f"• Некорректные инбаунды",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="admin_squad_create")],
                [types.InlineKeyboardButton(text="⬅️ К сквадам", callback_data="admin_rw_squads")]
            ])
        )
        await callback.answer("❌ Ошибка создания сквада", show_alert=True)

@admin_required
@error_handler
async def cancel_squad_creation(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    user_id = callback.from_user.id
    
    if user_id in squad_create_data:
        del squad_create_data[user_id]
    await state.clear()
    
    await show_squads_management(callback, db_user, db)


@admin_required
@error_handler
async def restart_all_nodes(
   callback: types.CallbackQuery,
   db_user: User,
   db: AsyncSession
):
   remnawave_service = RemnaWaveService()
   success = await remnawave_service.restart_all_nodes()
   
   if success:
       await callback.message.edit_text(
           "✅ Команда перезагрузки всех нод отправлена",
           reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
               [types.InlineKeyboardButton(text="⬅️ К нодам", callback_data="admin_rw_nodes")]
           ])
       )
   else:
       await callback.message.edit_text(
           "❌ Ошибка перезагрузки нод",
           reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
               [types.InlineKeyboardButton(text="⬅️ К нодам", callback_data="admin_rw_nodes")]
           ])
       )
   
   await callback.answer()


@admin_required
@error_handler
async def show_sync_options(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    status = remnawave_sync_service.get_status()
    times_text = ", ".join(t.strftime("%H:%M") for t in status.times) if status.times else "—"
    next_run_text = format_datetime(status.next_run) if status.next_run else "—"
    last_result = "—"

    if status.last_run_finished_at:
        result_icon = "✅" if status.last_run_success else "❌"
        result_label = "успешно" if status.last_run_success else "с ошибками"
        finished_text = format_datetime(status.last_run_finished_at)
        last_result = f"{result_icon} {result_label} ({finished_text})"
    elif status.last_run_started_at:
        last_result = f"⏳ Запущено {format_datetime(status.last_run_started_at)}"

    status_lines = [
        f"⚙️ Статус: {'✅ Включена' if status.enabled else '❌ Отключена'}",
        f"🕒 Расписание: {times_text}",
        f"📅 Следующий запуск: {next_run_text if status.enabled else '—'}",
        f"📊 Последний запуск: {last_result}",
    ]

    text = (
        "🔄 <b>Синхронизация с Remnawave</b>\n\n"
        "🔄 <b>Полная синхронизация выполняет:</b>\n"
        "• Создание новых пользователей из панели в боте\n"
        "• Обновление данных существующих пользователей\n"
        "• Деактивация подписок пользователей, отсутствующих в панели\n"
        "• Сохранение балансов пользователей\n"
        "• ⏱️ Время выполнения: 2-5 минут\n\n"
        "⚠️ <b>Важно:</b>\n"
        "• Во время синхронизации не выполняйте другие операции\n"
        "• При полной синхронизации подписки пользователей, отсутствующих в панели, будут деактивированы\n"
        "• Рекомендуется делать полную синхронизацию ежедневно\n"
        "• Баланс пользователей НЕ удаляется\n\n"
        + "\n".join(status_lines)
    )

    keyboard = [
        [
            types.InlineKeyboardButton(
                text="🔄 Запустить полную синхронизацию",
                callback_data="sync_all_users",
            )
        ],
        [
            types.InlineKeyboardButton(
                text="⚙️ Настройки автосинхронизации",
                callback_data="admin_rw_auto_sync",
            )
        ],
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_remnawave")],
    ]

    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
    )
    await callback.answer()


@admin_required
@error_handler
async def show_auto_sync_settings(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    await state.clear()
    status = remnawave_sync_service.get_status()
    text, keyboard = _build_auto_sync_view(status)

    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_auto_sync_setting(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    await state.clear()
    new_value = not bool(settings.REMNAWAVE_AUTO_SYNC_ENABLED)
    await bot_configuration_service.set_value(
        db,
        "REMNAWAVE_AUTO_SYNC_ENABLED",
        new_value,
    )
    await db.commit()

    status = remnawave_sync_service.get_status()
    text, keyboard = _build_auto_sync_view(status)

    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer(
        f"Автосинхронизация {'включена' if new_value else 'отключена'}"
    )


@admin_required
@error_handler
async def prompt_auto_sync_schedule(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    status = remnawave_sync_service.get_status()
    current_schedule = ", ".join(t.strftime("%H:%M") for t in status.times) if status.times else "—"

    instructions = (
        "🕒 <b>Настройка расписания автосинхронизации</b>\n\n"
        "Укажите время запуска через запятую или с новой строки в формате HH:MM.\n"
        f"Текущее расписание: <code>{current_schedule}</code>\n\n"
        "Примеры: <code>03:00, 15:30</code> или <code>00:15\n06:00\n18:45</code>\n\n"
        "Отправьте <b>отмена</b>, чтобы вернуться без изменений."
    )

    await state.set_state(RemnaWaveSyncStates.waiting_for_schedule)
    await state.update_data(
        auto_sync_message_id=callback.message.message_id,
        auto_sync_message_chat_id=callback.message.chat.id,
    )

    await callback.message.edit_text(
        instructions,
        parse_mode="HTML",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="❌ Отмена",
                        callback_data="remnawave_auto_sync_cancel",
                    )
                ]
            ]
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def cancel_auto_sync_schedule(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    await state.clear()
    status = remnawave_sync_service.get_status()
    text, keyboard = _build_auto_sync_view(status)

    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer("Изменение расписания отменено")


@admin_required
@error_handler
async def run_auto_sync_now(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    if remnawave_sync_service.get_status().is_running:
        await callback.answer("Синхронизация уже выполняется", show_alert=True)
        return

    await state.clear()
    await callback.message.edit_text(
        "🔄 Запуск автосинхронизации...\n\nПодождите, это может занять несколько минут.",
        parse_mode="HTML",
    )
    await callback.answer("Автосинхронизация запущена")

    result = await remnawave_sync_service.run_sync_now(reason="manual")
    status = remnawave_sync_service.get_status()
    base_text, keyboard = _build_auto_sync_view(status)

    if not result.get("started"):
        await callback.message.edit_text(
            "⚠️ <b>Синхронизация уже выполняется</b>\n\n" + base_text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        return

    if result.get("success"):
        user_stats = result.get("user_stats") or {}
        server_stats = result.get("server_stats") or {}
        summary = (
            "✅ <b>Синхронизация завершена</b>\n"
            f"👥 Пользователи: создано {user_stats.get('created', 0)}, обновлено {user_stats.get('updated', 0)}, "
            f"деактивировано {user_stats.get('deleted', user_stats.get('deactivated', 0))}, ошибок {user_stats.get('errors', 0)}\n"
            f"🌐 Серверы: создано {server_stats.get('created', 0)}, обновлено {server_stats.get('updated', 0)}, удалено {server_stats.get('removed', 0)}\n\n"
        )
        final_text = summary + base_text
        await callback.message.edit_text(
            final_text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    else:
        error_text = result.get("error") or "Неизвестная ошибка"
        summary = (
            "❌ <b>Синхронизация завершилась с ошибкой</b>\n"
            f"Причина: {error_text}\n\n"
        )
        await callback.message.edit_text(
            summary + base_text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )


@admin_required
@error_handler
async def save_auto_sync_schedule(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    text = (message.text or "").strip()
    data = await state.get_data()

    if text.lower() in {"отмена", "cancel"}:
        await state.clear()
        status = remnawave_sync_service.get_status()
        view_text, keyboard = _build_auto_sync_view(status)
        message_id = data.get("auto_sync_message_id")
        chat_id = data.get("auto_sync_message_chat_id", message.chat.id)
        if message_id:
            await message.bot.edit_message_text(
                view_text,
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        else:
            await message.answer(
                view_text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        await message.answer("Настройка расписания отменена")
        return

    parsed_times = settings.parse_daily_time_list(text)

    if not parsed_times:
        await message.answer(
            "❌ Не удалось распознать время. Используйте формат HH:MM, например 03:00 или 18:45.",
        )
        return

    normalized_value = ", ".join(t.strftime("%H:%M") for t in parsed_times)
    await bot_configuration_service.set_value(
        db,
        "REMNAWAVE_AUTO_SYNC_TIMES",
        normalized_value,
    )

    status = remnawave_sync_service.get_status()
    view_text, keyboard = _build_auto_sync_view(status)
    message_id = data.get("auto_sync_message_id")
    chat_id = data.get("auto_sync_message_chat_id", message.chat.id)

    if message_id:
        await message.bot.edit_message_text(
            view_text,
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    else:
        await message.answer(
            view_text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )

    await state.clear()
    await message.answer("✅ Расписание автосинхронизации обновлено")


@admin_required
@error_handler
async def sync_all_users(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Выполняет полную синхронизацию всех пользователей"""
    
    progress_text = """
🔄 <b>Выполняется полная синхронизация...</b>

📋 Этапы:
• Загрузка ВСЕХ пользователей из панели Remnawave
• Создание новых пользователей в боте
• Обновление существующих пользователей
• Деактивация подписок отсутствующих пользователей
• Сохранение балансов

⏳ Пожалуйста, подождите...
"""
    
    await callback.message.edit_text(progress_text, reply_markup=None)
    
    remnawave_service = RemnaWaveService()
    stats = await remnawave_service.sync_users_from_panel(db, "all")
    
    total_operations = stats['created'] + stats['updated'] + stats.get('deleted', 0)
    
    if stats['errors'] == 0:
        status_emoji = "✅"
        status_text = "успешно завершена"
    elif stats['errors'] < total_operations:
        status_emoji = "⚠️"
        status_text = "завершена с предупреждениями"
    else:
        status_emoji = "❌"
        status_text = "завершена с ошибками"
    
    text = f"""
{status_emoji} <b>Полная синхронизация {status_text}</b>

📊 <b>Результат:</b>
• 🆕 Создано: {stats['created']}
• 🔄 Обновлено: {stats['updated']}  
• 🗑️ Деактивировано: {stats.get('deleted', 0)}
• ❌ Ошибок: {stats['errors']}
"""
    
    if stats.get('deleted', 0) > 0:
        text += f"""

🗑️ <b>Деактивированные подписки:</b>
Деактивированы подписки пользователей, которые
отсутствуют в панели Remnawave.
💰 Балансы пользователей сохранены.
"""
    
    if stats['errors'] > 0:
        text += f"""

⚠️ <b>Внимание:</b>
Некоторые операции завершились с ошибками.
Проверьте логи для получения подробной информации.
"""
    
    text += f"""

💡 <b>Рекомендации:</b>
• Полная синхронизация выполнена
• Рекомендуется запускать раз в день
• Все пользователи из панели синхронизированы
"""
    
    keyboard = []
    
    if stats['errors'] > 0:
        keyboard.append([
            types.InlineKeyboardButton(
                text="🔄 Повторить синхронизацию", 
                callback_data="sync_all_users"
            )
        ])
    
    keyboard.extend([
        [
            types.InlineKeyboardButton(text="📊 Статистика системы", callback_data="admin_rw_system"),
            types.InlineKeyboardButton(text="🌐 Ноды", callback_data="admin_rw_nodes")
        ],
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_remnawave")]
    ])
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()

@admin_required
@error_handler
async def show_sync_recommendations(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    await callback.message.edit_text(
        "🔍 Анализируем состояние синхронизации...",
        reply_markup=None
    )
    
    remnawave_service = RemnaWaveService()
    recommendations = await remnawave_service.get_sync_recommendations(db)
    
    priority_emoji = {
        "low": "🟢",
        "medium": "🟡", 
        "high": "🔴"
    }
    
    text = f"""
💡 <b>Рекомендации по синхронизации</b>

{priority_emoji.get(recommendations['priority'], '🟢')} <b>Приоритет:</b> {recommendations['priority'].upper()}
⏱️ <b>Время выполнения:</b> {recommendations['estimated_time']}

<b>Рекомендуемое действие:</b>
"""
    
    if recommendations['sync_type'] == 'all':
        text += "🔄 Полная синхронизация"
    elif recommendations['sync_type'] == 'update_only':
        text += "📈 Обновление данных"
    elif recommendations['sync_type'] == 'new_only':
        text += "🆕 Синхронизация новых"
    else:
        text += "✅ Синхронизация не требуется"
    
    text += "\n\n<b>Причины:</b>\n"
    for reason in recommendations['reasons']:
        text += f"• {reason}\n"
    
    keyboard = []
    
    if recommendations['should_sync'] and recommendations['sync_type'] != 'none':
        keyboard.append([
            types.InlineKeyboardButton(
                text=f"✅ Выполнить рекомендацию", 
                callback_data=f"sync_{recommendations['sync_type']}_users" if recommendations['sync_type'] != 'update_only' else "sync_update_data"
            )
        ])
    
    keyboard.extend([
        [types.InlineKeyboardButton(text="🔄 Другие опции", callback_data="admin_rw_sync")],
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_remnawave")]
    ])
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()

@admin_required
@error_handler
async def validate_subscriptions(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    await callback.message.edit_text(
        "🔍 Выполняется валидация подписок...\n\nПроверяем данные, может занять несколько минут.",
        reply_markup=None
    )
    
    remnawave_service = RemnaWaveService()
    stats = await remnawave_service.validate_and_fix_subscriptions(db)
    
    if stats['errors'] == 0:
        status_emoji = "✅"
        status_text = "успешно завершена"
    else:
        status_emoji = "⚠️"
        status_text = "завершена с ошибками"
    
    text = f"""
{status_emoji} <b>Валидация {status_text}</b>

📊 <b>Результаты:</b>
• 🔍 Проверено подписок: {stats['checked']}
• 🔧 Исправлено подписок: {stats['fixed']}
• ⚠️ Найдено проблем: {stats['issues_found']}
• ❌ Ошибок: {stats['errors']}
"""
    
    if stats['fixed'] > 0:
        text += "\n✅ <b>Исправленные проблемы:</b>\n"
        text += "• Статусы просроченных подписок\n"
        text += "• Отсутствующие данные Remnawave\n" 
        text += "• Некорректные лимиты трафика\n"
        text += "• Настройки устройств\n"
    
    if stats['errors'] > 0:
        text += f"\n⚠️ Обнаружены ошибки при обработке.\nПроверьте логи для подробной информации."
    
    keyboard = [
        [types.InlineKeyboardButton(text="🔄 Повторить валидацию", callback_data="sync_validate")],
        [types.InlineKeyboardButton(text="🔄 Полная синхронизация", callback_data="sync_all_users")],
        [types.InlineKeyboardButton(text="⬅️ К синхронизации", callback_data="admin_rw_sync")]
    ]
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()

@admin_required
@error_handler
async def cleanup_subscriptions(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    await callback.message.edit_text(
        "🧹 Выполняется очистка неактуальных подписок...\n\nУдаляем подписки пользователей, отсутствующих в панели.",
        reply_markup=None
    )
    
    remnawave_service = RemnaWaveService()
    stats = await remnawave_service.cleanup_orphaned_subscriptions(db)
    
    if stats['errors'] == 0:
        status_emoji = "✅"
        status_text = "успешно завершена"
    else:
        status_emoji = "⚠️"
        status_text = "завершена с ошибками"
    
    text = f"""
{status_emoji} <b>Очистка {status_text}</b>

📊 <b>Результаты:</b>
• 🔍 Проверено подписок: {stats['checked']}
• 🗑️ Деактивировано: {stats['deactivated']}
• ❌ Ошибок: {stats['errors']}
"""
    
    if stats['deactivated'] > 0:
        text += f"\n🗑️ <b>Деактивированные подписки:</b>\n"
        text += f"Отключены подписки пользователей, которые\n"
        text += f"отсутствуют в панели Remnawave.\n"
    else:
        text += f"\n✅ Все подписки актуальны!\nНеактуальных подписок не найдено."
    
    if stats['errors'] > 0:
        text += f"\n⚠️ Обнаружены ошибки при обработке.\nПроверьте логи для подробной информации."
    
    keyboard = [
        [types.InlineKeyboardButton(text="🔄 Повторить очистку", callback_data="sync_cleanup")],
        [types.InlineKeyboardButton(text="🔍 Валидация", callback_data="sync_validate")],
        [types.InlineKeyboardButton(text="⬅️ К синхронизации", callback_data="admin_rw_sync")]
    ]
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()

@admin_required
@error_handler
async def force_cleanup_all_orphaned_users(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    await callback.message.edit_text(
        "🗑️ Выполняется принудительная очистка всех пользователей, отсутствующих в панели...\n\n"
        "⚠️ ВНИМАНИЕ: Это полностью удалит ВСЕ данные пользователей!\n"
        "📊 Включая: транзакции, реферальные доходы, промокоды, серверы, балансы\n\n"
        "⏳ Пожалуйста, подождите...",
        reply_markup=None
    )
    
    remnawave_service = RemnaWaveService()
    stats = await remnawave_service.cleanup_orphaned_subscriptions(db)
    
    if stats['errors'] == 0:
        status_emoji = "✅"
        status_text = "успешно завершена"
    else:
        status_emoji = "⚠️"
        status_text = "завершена с ошибками"
    
    text = f"""
{status_emoji} <b>Принудительная очистка {status_text}</b>

📊 <b>Результаты:</b>
• 🔍 Проверено подписок: {stats['checked']}
• 🗑️ Полностью очищено: {stats['deactivated']}
• ❌ Ошибок: {stats['errors']}
"""
    
    if stats['deactivated'] > 0:
        text += f"""

🗑️ <b>Полностью очищенные данные:</b>
• Подписки сброшены к начальному состоянию
• Удалены ВСЕ транзакции пользователей
• Удалены ВСЕ реферальные доходы  
• Удалены использования промокодов
• Сброшены балансы к нулю
• Удалены подключенные серверы
• Сброшены HWID устройства в Remnawave
• Очищены Remnawave UUID
"""
    else:
        text += f"\n✅ Неактуальных подписок не найдено!\nВсе пользователи синхронизированы с панелью."
    
    if stats['errors'] > 0:
        text += f"\n⚠️ Обнаружены ошибки при обработке.\nПроверьте логи для подробной информации."
    
    keyboard = [
        [types.InlineKeyboardButton(text="🔄 Повторить очистку", callback_data="force_cleanup_orphaned")],
        [types.InlineKeyboardButton(text="🔄 Полная синхронизация", callback_data="sync_all_users")],
        [types.InlineKeyboardButton(text="⬅️ К синхронизации", callback_data="admin_rw_sync")]
    ]
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@admin_required
@error_handler
async def confirm_force_cleanup(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    text = """
⚠️ <b>ВНИМАНИЕ! ОПАСНАЯ ОПЕРАЦИЯ!</b>

🗑️ <b>Принудительная очистка полностью удалит:</b>
• ВСЕ транзакции пользователей отсутствующих в панели
• ВСЕ реферальные доходы и связи
• ВСЕ использования промокодов
• ВСЕ подключенные серверы подписок
• ВСЕ балансы (сброс к нулю)
• ВСЕ HWID устройства в Remnawave
• ВСЕ Remnawave UUID и ссылки

⚡ <b>Это действие НЕОБРАТИМО!</b>

Используйте только если:
• Обычная синхронизация не помогает
• Нужно полностью очистить "мусорные" данные
• После массового удаления пользователей из панели

❓ <b>Вы действительно хотите продолжить?</b>
"""
    
    keyboard = [
        [types.InlineKeyboardButton(text="🗑️ ДА, ОЧИСТИТЬ ВСЕ", callback_data="force_cleanup_orphaned")],
        [types.InlineKeyboardButton(text="❌ Отмена", callback_data="admin_rw_sync")]
    ]
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@admin_required
@error_handler
async def sync_users(
   callback: types.CallbackQuery,
   db_user: User,
   db: AsyncSession
):
   sync_type = callback.data.split('_')[-2] + "_" + callback.data.split('_')[-1]
   
   progress_text = "🔄 Выполняется синхронизация...\n\n"
   
   if sync_type == "all_users":
       progress_text += "📋 Тип: Полная синхронизация\n"
       progress_text += "• Создание новых пользователей\n"
       progress_text += "• Обновление существующих\n"
       progress_text += "• Удаление неактуальных подписок\n"
   elif sync_type == "new_users":
       progress_text += "📋 Тип: Только новые пользователи\n"
       progress_text += "• Создание пользователей из панели\n"
   elif sync_type == "update_data":
       progress_text += "📋 Тип: Обновление данных\n"
       progress_text += "• Обновление информации о трафике\n"
       progress_text += "• Синхронизация подписок\n"
   
   progress_text += "\n⏳ Пожалуйста, подождите..."
   
   await callback.message.edit_text(
       progress_text,
       reply_markup=None
   )
   
   remnawave_service = RemnaWaveService()
   
   sync_map = {
       "all_users": "all",
       "new_users": "new_only", 
       "update_data": "update_only"
   }
   
   stats = await remnawave_service.sync_users_from_panel(db, sync_map.get(sync_type, "all"))
   
   total_operations = stats['created'] + stats['updated'] + stats.get('deleted', 0)
   success_operations = stats['created'] + stats['updated'] + stats.get('deleted', 0)
   
   if stats['errors'] == 0:
       status_emoji = "✅"
       status_text = "успешно завершена"
   elif stats['errors'] < total_operations:
       status_emoji = "⚠️"
       status_text = "завершена с предупреждениями"
   else:
       status_emoji = "❌"
       status_text = "завершена с ошибками"
   
   text = f"""
{status_emoji} <b>Синхронизация {status_text}</b>

📊 <b>Результат:</b>
"""
   
   if sync_type == "all_users":
       text += f"• 🆕 Создано: {stats['created']}\n"
       text += f"• 🔄 Обновлено: {stats['updated']}\n"
       if 'deleted' in stats:
           text += f"• 🗑️ Удалено: {stats['deleted']}\n"
       text += f"• ❌ Ошибок: {stats['errors']}\n"
   elif sync_type == "new_users":
       text += f"• 🆕 Создано: {stats['created']}\n"
       text += f"• ❌ Ошибок: {stats['errors']}\n"
       if stats['created'] == 0 and stats['errors'] == 0:
           text += "\n💡 Новых пользователей не найдено"
   elif sync_type == "update_data":
       text += f"• 🔄 Обновлено: {stats['updated']}\n"
       text += f"• ❌ Ошибок: {stats['errors']}\n"
       if stats['updated'] == 0 and stats['errors'] == 0:
           text += "\n💡 Все данные актуальны"
   
   if stats['errors'] > 0:
       text += f"\n⚠️ <b>Внимание:</b>\n"
       text += f"Некоторые операции завершились с ошибками.\n"
       text += f"Проверьте логи для получения подробной информации."
   
   if sync_type == "all_users" and 'deleted' in stats and stats['deleted'] > 0:
       text += f"\n🗑️ <b>Удаленные подписки:</b>\n"
       text += f"Деактивированы подписки пользователей,\n"
       text += f"которые отсутствуют в панели Remnawave."
   
   text += f"\n\n💡 <b>Рекомендации:</b>\n"
   if sync_type == "all_users":
       text += "• Полная синхронизация выполнена\n"
       text += "• Рекомендуется запускать раз в день\n"
   elif sync_type == "new_users":
       text += "• Синхронизация новых пользователей\n"
       text += "• Используйте при массовом добавлении\n"
   elif sync_type == "update_data":
       text += "• Обновление данных о трафике\n"
       text += "• Запускайте для актуализации статистики\n"
   
   keyboard = []
   
   if stats['errors'] > 0:
       keyboard.append([
           types.InlineKeyboardButton(
               text="🔄 Повторить синхронизацию", 
               callback_data=callback.data
           )
       ])
   
   if sync_type != "all_users":
       keyboard.append([
           types.InlineKeyboardButton(
               text="🔄 Полная синхронизация", 
               callback_data="sync_all_users"
           )
       ])
   
   keyboard.extend([
       [
           types.InlineKeyboardButton(text="📊 Статистика системы", callback_data="admin_rw_system"),
           types.InlineKeyboardButton(text="🌐 Ноды", callback_data="admin_rw_nodes")
       ],
       [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_remnawave")]
   ])
   
   await callback.message.edit_text(
       text,
       reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
   )
   await callback.answer()


@admin_required
@error_handler
async def show_squads_management(
   callback: types.CallbackQuery,
   db_user: User,
   db: AsyncSession
):
   remnawave_service = RemnaWaveService()
   squads = await remnawave_service.get_all_squads()
   
   text = "🌍 <b>Управление сквадами</b>\n\n"
   keyboard = []
   
   if squads:
       for squad in squads:
           text += f"🔹 <b>{squad['name']}</b>\n"
           text += f"👥 Участников: {squad['members_count']}\n"
           text += f"📡 Инбаундов: {squad['inbounds_count']}\n\n"
           
           keyboard.append([
               types.InlineKeyboardButton(
                   text=f"⚙️ {squad['name']}",
                   callback_data=f"admin_squad_manage_{squad['uuid']}"
               )
           ])
   else:
       text += "Сквады не найдены"
   
   keyboard.extend([
       [types.InlineKeyboardButton(text="➕ Создать сквад", callback_data="admin_squad_create")],
       [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_remnawave")]
   ])
   
   await callback.message.edit_text(
       text,
       reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
   )
   await callback.answer()


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_remnawave_menu, F.data == "admin_remnawave")
    dp.callback_query.register(show_system_stats, F.data == "admin_rw_system")
    dp.callback_query.register(show_traffic_stats, F.data == "admin_rw_traffic")
    dp.callback_query.register(show_nodes_management, F.data == "admin_rw_nodes")
    dp.callback_query.register(show_node_details, F.data.startswith("admin_node_manage_"))
    dp.callback_query.register(show_node_statistics, F.data.startswith("node_stats_"))
    dp.callback_query.register(manage_node, F.data.startswith("node_enable_"))
    dp.callback_query.register(manage_node, F.data.startswith("node_disable_"))
    dp.callback_query.register(manage_node, F.data.startswith("node_restart_"))
    dp.callback_query.register(restart_all_nodes, F.data == "admin_restart_all_nodes")
    dp.callback_query.register(show_sync_options, F.data == "admin_rw_sync")
    dp.callback_query.register(show_auto_sync_settings, F.data == "admin_rw_auto_sync")
    dp.callback_query.register(toggle_auto_sync_setting, F.data == "remnawave_auto_sync_toggle")
    dp.callback_query.register(prompt_auto_sync_schedule, F.data == "remnawave_auto_sync_times")
    dp.callback_query.register(cancel_auto_sync_schedule, F.data == "remnawave_auto_sync_cancel")
    dp.callback_query.register(run_auto_sync_now, F.data == "remnawave_auto_sync_run")
    dp.callback_query.register(sync_all_users, F.data == "sync_all_users")
    dp.callback_query.register(show_squad_migration_menu, F.data == "admin_rw_migration")
    dp.callback_query.register(paginate_migration_source, F.data.startswith("admin_migration_source_page_"))
    dp.callback_query.register(handle_migration_source_selection, F.data.startswith("admin_migration_source_"))
    dp.callback_query.register(paginate_migration_target, F.data.startswith("admin_migration_target_page_"))
    dp.callback_query.register(handle_migration_target_selection, F.data.startswith("admin_migration_target_"))
    dp.callback_query.register(change_migration_target, F.data == "admin_migration_change_target")
    dp.callback_query.register(confirm_squad_migration, F.data == "admin_migration_confirm")
    dp.callback_query.register(cancel_squad_migration, F.data == "admin_migration_cancel")
    dp.callback_query.register(handle_migration_page_info, F.data == "admin_migration_page_info")
    dp.callback_query.register(show_squads_management, F.data == "admin_rw_squads")
    dp.callback_query.register(show_squad_details, F.data.startswith("admin_squad_manage_"))
    dp.callback_query.register(manage_squad_action, F.data.startswith("squad_add_users_"))
    dp.callback_query.register(manage_squad_action, F.data.startswith("squad_remove_users_"))
    dp.callback_query.register(manage_squad_action, F.data.startswith("squad_delete_"))
    dp.callback_query.register(show_squad_edit_menu, F.data.startswith("squad_edit_") & ~F.data.startswith("squad_edit_inbounds_"))
    dp.callback_query.register(show_squad_inbounds_selection, F.data.startswith("squad_edit_inbounds_"))
    dp.callback_query.register(show_squad_rename_form, F.data.startswith("squad_rename_"))
    dp.callback_query.register(cancel_squad_rename, F.data.startswith("cancel_rename_"))
    dp.callback_query.register(toggle_squad_inbound, F.data.startswith("sqd_tgl_"))
    dp.callback_query.register(save_squad_inbounds, F.data.startswith("sqd_save_"))
    dp.callback_query.register(show_squad_edit_menu_short, F.data.startswith("sqd_edit_"))
    dp.callback_query.register(start_squad_creation, F.data == "admin_squad_create")
    dp.callback_query.register(cancel_squad_creation, F.data == "cancel_squad_create")
    dp.callback_query.register(toggle_create_inbound, F.data.startswith("create_tgl_"))
    dp.callback_query.register(finish_squad_creation, F.data == "create_squad_finish")
    
    dp.message.register(
        process_squad_new_name,
        SquadRenameStates.waiting_for_new_name,
        F.text
    )

    dp.message.register(
        process_squad_name,
        SquadCreateStates.waiting_for_name,
        F.text
    )

    dp.message.register(
        save_auto_sync_schedule,
        RemnaWaveSyncStates.waiting_for_schedule,
        F.text,
    )
