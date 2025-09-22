import logging
from typing import Optional

from aiogram import Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.promo_group import (
    get_promo_groups_with_counts,
    get_promo_group_by_id,
    create_promo_group,
    update_promo_group,
    delete_promo_group,
    get_promo_group_members,
    count_promo_group_members,
)
from app.database.models import PromoGroup
from app.localization.texts import get_texts
from app.states import AdminStates
from app.utils.decorators import admin_required, error_handler
from app.keyboards.admin import (
    get_admin_pagination_keyboard,
    get_confirmation_keyboard,
)

logger = logging.getLogger(__name__)


def _format_discount_line(texts, group) -> str:
    return texts.t(
        "ADMIN_PROMO_GROUPS_DISCOUNTS",
        "Скидки — серверы: {servers}%, трафик: {traffic}%, устройства: {devices}%",
    ).format(
        servers=group.server_discount_percent,
        traffic=group.traffic_discount_percent,
        devices=group.device_discount_percent,
    )


@admin_required
@error_handler
async def show_promo_groups_menu(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    groups = await get_promo_groups_with_counts(db)

    total_members = sum(count for _, count in groups)
    header = texts.t("ADMIN_PROMO_GROUPS_TITLE", "💳 <b>Промогруппы</b>")

    if groups:
        summary = texts.t(
            "ADMIN_PROMO_GROUPS_SUMMARY",
            "Всего групп: {count}\nВсего участников: {members}",
        ).format(count=len(groups), members=total_members)
        lines = [header, "", summary, ""]

        keyboard_rows = []
        for group, member_count in groups:
            default_suffix = (
                texts.t("ADMIN_PROMO_GROUPS_DEFAULT_LABEL", " (базовая)")
                if group.is_default
                else ""
            )
            lines.extend(
                [
                    f"{'⭐' if group.is_default else '🎯'} <b>{group.name}</b>{default_suffix}",
                    _format_discount_line(texts, group),
                    texts.t(
                        "ADMIN_PROMO_GROUPS_MEMBERS_COUNT",
                        "Участников: {count}",
                    ).format(count=member_count),
                    "",
                ]
            )
            keyboard_rows.append([
                types.InlineKeyboardButton(
                    text=f"{'⭐' if group.is_default else '🎯'} {group.name}",
                    callback_data=f"promo_group_manage_{group.id}",
                )
            ])
    else:
        lines = [header, "", texts.t("ADMIN_PROMO_GROUPS_EMPTY", "Промогруппы не найдены.")]
        keyboard_rows = []

    keyboard_rows.append(
        [types.InlineKeyboardButton(text="➕ Создать", callback_data="admin_promo_group_create")]
    )
    keyboard_rows.append(
        [types.InlineKeyboardButton(text=texts.BACK, callback_data="admin_submenu_promo")]
    )

    await callback.message.edit_text(
        "\n".join(line for line in lines if line is not None),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
        parse_mode="HTML",
    )
    await callback.answer()


async def _get_group_or_alert(
    callback: types.CallbackQuery,
    db: AsyncSession,
) -> Optional[PromoGroup]:
    group_id = int(callback.data.split("_")[-1])
    group = await get_promo_group_by_id(db, group_id)
    if not group:
        await callback.answer("❌ Промогруппа не найдена", show_alert=True)
        return None
    return group


@admin_required
@error_handler
async def show_promo_group_details(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
):
    group = await _get_group_or_alert(callback, db)
    if not group:
        return

    texts = get_texts(db_user.language)
    member_count = await count_promo_group_members(db, group.id)

    default_note = (
        "\n" + texts.t("ADMIN_PROMO_GROUP_DETAILS_DEFAULT", "Это базовая группа.")
        if group.is_default
        else ""
    )

    text = "\n".join(
        [
            texts.t(
                "ADMIN_PROMO_GROUP_DETAILS_TITLE",
                "💳 <b>Промогруппа:</b> {name}",
            ).format(name=group.name),
            _format_discount_line(texts, group),
            texts.t(
                "ADMIN_PROMO_GROUP_DETAILS_MEMBERS",
                "Участников: {count}",
            ).format(count=member_count),
            default_note,
        ]
    )

    keyboard_rows = []
    if member_count > 0:
        keyboard_rows.append(
            [
                types.InlineKeyboardButton(
                    text=texts.t("ADMIN_PROMO_GROUP_MEMBERS_BUTTON", "👥 Участники"),
                    callback_data=f"promo_group_members_{group.id}_page_1",
                )
            ]
        )

    keyboard_rows.append(
        [
            types.InlineKeyboardButton(
                text=texts.t("ADMIN_PROMO_GROUP_EDIT_BUTTON", "✏️ Изменить"),
                callback_data=f"promo_group_edit_{group.id}",
            )
        ]
    )

    if not group.is_default:
        keyboard_rows.append(
            [
                types.InlineKeyboardButton(
                    text=texts.t("ADMIN_PROMO_GROUP_DELETE_BUTTON", "🗑️ Удалить"),
                    callback_data=f"promo_group_delete_{group.id}",
                )
            ]
        )

    keyboard_rows.append(
        [types.InlineKeyboardButton(text=texts.BACK, callback_data="admin_promo_groups")]
    )

    await callback.message.edit_text(
        text.strip(),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
        parse_mode="HTML",
    )
    await callback.answer()


def _validate_percent(value: str) -> int:
    percent = int(value)
    if percent < 0 or percent > 100:
        raise ValueError
    return percent


async def _prompt_for_discount(
    message: types.Message,
    state: FSMContext,
    prompt_key: str,
    default_text: str,
):
    data = await state.get_data()
    texts = get_texts(data.get("language", "ru"))
    await message.answer(texts.t(prompt_key, default_text))


@admin_required
@error_handler
async def start_create_promo_group(
    callback: types.CallbackQuery,
    db_user,
    state: FSMContext,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    await state.set_state(AdminStates.creating_promo_group_name)
    await state.update_data(language=db_user.language)
    await callback.message.edit_text(
        texts.t("ADMIN_PROMO_GROUP_CREATE_NAME_PROMPT", "Введите название новой промогруппы:"),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text=texts.BACK, callback_data="admin_promo_groups")]
            ]
        ),
    )
    await callback.answer()


async def process_create_group_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    if not name:
        texts = get_texts((await state.get_data()).get("language", "ru"))
        await message.answer(texts.t("ADMIN_PROMO_GROUP_INVALID_NAME", "Название не может быть пустым."))
        return

    await state.update_data(new_group_name=name)
    await state.set_state(AdminStates.creating_promo_group_traffic_discount)
    await _prompt_for_discount(
        message,
        state,
        "ADMIN_PROMO_GROUP_CREATE_TRAFFIC_PROMPT",
        "Введите скидку на трафик (0-100):",
    )


async def process_create_group_traffic(message: types.Message, state: FSMContext):
    texts = get_texts((await state.get_data()).get("language", "ru"))
    try:
        value = _validate_percent(message.text)
    except (ValueError, TypeError):
        await message.answer(texts.t("ADMIN_PROMO_GROUP_INVALID_PERCENT", "Введите число от 0 до 100."))
        return

    await state.update_data(new_group_traffic=value)
    await state.set_state(AdminStates.creating_promo_group_server_discount)
    await _prompt_for_discount(
        message,
        state,
        "ADMIN_PROMO_GROUP_CREATE_SERVERS_PROMPT",
        "Введите скидку на серверы (0-100):",
    )


async def process_create_group_servers(message: types.Message, state: FSMContext):
    texts = get_texts((await state.get_data()).get("language", "ru"))
    try:
        value = _validate_percent(message.text)
    except (ValueError, TypeError):
        await message.answer(texts.t("ADMIN_PROMO_GROUP_INVALID_PERCENT", "Введите число от 0 до 100."))
        return

    await state.update_data(new_group_servers=value)
    await state.set_state(AdminStates.creating_promo_group_device_discount)
    await _prompt_for_discount(
        message,
        state,
        "ADMIN_PROMO_GROUP_CREATE_DEVICES_PROMPT",
        "Введите скидку на устройства (0-100):",
    )


@admin_required
@error_handler
async def process_create_group_devices(
    message: types.Message,
    state: FSMContext,
    db_user,
    db: AsyncSession,
):
    data = await state.get_data()
    texts = get_texts(data.get("language", db_user.language))

    try:
        devices_discount = _validate_percent(message.text)
    except (ValueError, TypeError):
        await message.answer(texts.t("ADMIN_PROMO_GROUP_INVALID_PERCENT", "Введите число от 0 до 100."))
        return

    try:
        group = await create_promo_group(
            db,
            data["new_group_name"],
            traffic_discount_percent=data["new_group_traffic"],
            server_discount_percent=data["new_group_servers"],
            device_discount_percent=devices_discount,
        )
    except Exception as e:
        logger.error(f"Не удалось создать промогруппу: {e}")
        await message.answer(texts.ERROR)
        await state.clear()
        return

    await state.clear()
    await message.answer(
        texts.t("ADMIN_PROMO_GROUP_CREATED", "Промогруппа «{name}» создана.").format(
            name=group.name
        ),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=texts.t(
                            "ADMIN_PROMO_GROUP_CREATED_BACK_BUTTON",
                            "↩️ К промогруппам",
                        ),
                        callback_data="admin_promo_groups",
                    )
                ]
            ]
        ),
    )


@admin_required
@error_handler
async def start_edit_promo_group(
    callback: types.CallbackQuery,
    db_user,
    state: FSMContext,
    db: AsyncSession,
):
    group = await _get_group_or_alert(callback, db)
    if not group:
        return

    texts = get_texts(db_user.language)
    await state.set_state(AdminStates.editing_promo_group_name)
    await state.update_data(edit_group_id=group.id, language=db_user.language)

    await callback.message.edit_text(
        texts.t(
            "ADMIN_PROMO_GROUP_EDIT_NAME_PROMPT",
            "Введите новое название промогруппы (текущее: {name}):",
        ).format(name=group.name),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text=texts.BACK, callback_data=f"promo_group_manage_{group.id}")]
            ]
        ),
    )
    await callback.answer()


async def process_edit_group_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    if not name:
        texts = get_texts((await state.get_data()).get("language", "ru"))
        await message.answer(texts.t("ADMIN_PROMO_GROUP_INVALID_NAME", "Название не может быть пустым."))
        return

    await state.update_data(edit_group_name=name)
    await state.set_state(AdminStates.editing_promo_group_traffic_discount)
    await _prompt_for_discount(
        message,
        state,
        "ADMIN_PROMO_GROUP_EDIT_TRAFFIC_PROMPT",
        "Введите новую скидку на трафик (0-100):",
    )


async def process_edit_group_traffic(message: types.Message, state: FSMContext):
    texts = get_texts((await state.get_data()).get("language", "ru"))
    try:
        value = _validate_percent(message.text)
    except (ValueError, TypeError):
        await message.answer(texts.t("ADMIN_PROMO_GROUP_INVALID_PERCENT", "Введите число от 0 до 100."))
        return

    await state.update_data(edit_group_traffic=value)
    await state.set_state(AdminStates.editing_promo_group_server_discount)
    await _prompt_for_discount(
        message,
        state,
        "ADMIN_PROMO_GROUP_EDIT_SERVERS_PROMPT",
        "Введите новую скидку на серверы (0-100):",
    )


async def process_edit_group_servers(message: types.Message, state: FSMContext):
    texts = get_texts((await state.get_data()).get("language", "ru"))
    try:
        value = _validate_percent(message.text)
    except (ValueError, TypeError):
        await message.answer(texts.t("ADMIN_PROMO_GROUP_INVALID_PERCENT", "Введите число от 0 до 100."))
        return

    await state.update_data(edit_group_servers=value)
    await state.set_state(AdminStates.editing_promo_group_device_discount)
    await _prompt_for_discount(
        message,
        state,
        "ADMIN_PROMO_GROUP_EDIT_DEVICES_PROMPT",
        "Введите новую скидку на устройства (0-100):",
    )


@admin_required
@error_handler
async def process_edit_group_devices(
    message: types.Message,
    state: FSMContext,
    db_user,
    db: AsyncSession,
):
    data = await state.get_data()
    texts = get_texts(data.get("language", db_user.language))

    try:
        devices_discount = _validate_percent(message.text)
    except (ValueError, TypeError):
        await message.answer(texts.t("ADMIN_PROMO_GROUP_INVALID_PERCENT", "Введите число от 0 до 100."))
        return

    group = await get_promo_group_by_id(db, data["edit_group_id"])
    if not group:
        await message.answer("❌ Промогруппа не найдена")
        await state.clear()
        return

    await update_promo_group(
        db,
        group,
        name=data["edit_group_name"],
        traffic_discount_percent=data["edit_group_traffic"],
        server_discount_percent=data["edit_group_servers"],
        device_discount_percent=devices_discount,
    )

    await state.clear()
    await message.answer(
        texts.t("ADMIN_PROMO_GROUP_UPDATED", "Промогруппа «{name}» обновлена.").format(name=group.name)
    )


@admin_required
@error_handler
async def show_promo_group_members(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
):
    parts = callback.data.split("_")
    group_id = int(parts[3])
    page = int(parts[-1])
    limit = 10
    offset = (page - 1) * limit

    group = await get_promo_group_by_id(db, group_id)
    if not group:
        await callback.answer("❌ Промогруппа не найдена", show_alert=True)
        return

    texts = get_texts(db_user.language)
    members = await get_promo_group_members(db, group_id, offset=offset, limit=limit)
    total_members = await count_promo_group_members(db, group_id)
    total_pages = max(1, (total_members + limit - 1) // limit)

    title = texts.t(
        "ADMIN_PROMO_GROUP_MEMBERS_TITLE",
        "👥 Участники группы {name}",
    ).format(name=group.name)

    if not members:
        body = texts.t("ADMIN_PROMO_GROUP_MEMBERS_EMPTY", "В этой группе пока нет участников.")
    else:
        lines = []
        for index, user in enumerate(members, start=offset + 1):
            username = f"@{user.username}" if user.username else "—"
            lines.append(
                f"{index}. {user.full_name} (ID {user.id}, {username}, TG {user.telegram_id})"
            )
        body = "\n".join(lines)

    keyboard = []
    if total_pages > 1:
        pagination = get_admin_pagination_keyboard(
            page,
            total_pages,
            f"promo_group_members_{group_id}",
            f"promo_group_manage_{group_id}",
            db_user.language,
        )
        keyboard.extend(pagination.inline_keyboard)

    keyboard.append(
        [types.InlineKeyboardButton(text=texts.BACK, callback_data=f"promo_group_manage_{group_id}")]
    )

    await callback.message.edit_text(
        f"{title}\n\n{body}",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
    )
    await callback.answer()


@admin_required
@error_handler
async def request_delete_promo_group(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
):
    group = await _get_group_or_alert(callback, db)
    if not group:
        return

    texts = get_texts(db_user.language)

    if group.is_default:
        await callback.answer(
            texts.t("ADMIN_PROMO_GROUP_DELETE_FORBIDDEN", "Базовую промогруппу нельзя удалить."),
            show_alert=True,
        )
        return

    confirm_text = texts.t(
        "ADMIN_PROMO_GROUP_DELETE_CONFIRM",
        "Удалить промогруппу «{name}»? Все пользователи будут переведены в базовую группу.",
    ).format(name=group.name)

    await callback.message.edit_text(
        confirm_text,
        reply_markup=get_confirmation_keyboard(
            confirm_action=f"promo_group_delete_confirm_{group.id}",
            cancel_action=f"promo_group_manage_{group.id}",
            language=db_user.language,
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def delete_promo_group_confirmed(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
):
    group = await _get_group_or_alert(callback, db)
    if not group:
        return

    texts = get_texts(db_user.language)

    success = await delete_promo_group(db, group)
    if not success:
        await callback.answer(
            texts.t("ADMIN_PROMO_GROUP_DELETE_FORBIDDEN", "Базовую промогруппу нельзя удалить."),
            show_alert=True,
        )
        return

    await callback.message.edit_text(
        texts.t("ADMIN_PROMO_GROUP_DELETED", "Промогруппа «{name}» удалена.").format(name=group.name),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text=texts.BACK, callback_data="admin_promo_groups")]
            ]
        ),
    )
    await callback.answer()


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_promo_groups_menu, F.data == "admin_promo_groups")
    dp.callback_query.register(show_promo_group_details, F.data.startswith("promo_group_manage_"))
    dp.callback_query.register(start_create_promo_group, F.data == "admin_promo_group_create")
    dp.callback_query.register(start_edit_promo_group, F.data.startswith("promo_group_edit_"))
    dp.callback_query.register(
        request_delete_promo_group,
        F.data.startswith("promo_group_delete_")
        & ~F.data.startswith("promo_group_delete_confirm_"),
    )
    dp.callback_query.register(
        delete_promo_group_confirmed,
        F.data.startswith("promo_group_delete_confirm_"),
    )
    dp.callback_query.register(
        show_promo_group_members,
        F.data.regexp(r"^promo_group_members_\d+_page_\d+$"),
    )

    dp.message.register(process_create_group_name, AdminStates.creating_promo_group_name)
    dp.message.register(
        process_create_group_traffic,
        AdminStates.creating_promo_group_traffic_discount,
    )
    dp.message.register(
        process_create_group_servers,
        AdminStates.creating_promo_group_server_discount,
    )
    dp.message.register(
        process_create_group_devices,
        AdminStates.creating_promo_group_device_discount,
    )

    dp.message.register(process_edit_group_name, AdminStates.editing_promo_group_name)
    dp.message.register(
        process_edit_group_traffic,
        AdminStates.editing_promo_group_traffic_discount,
    )
    dp.message.register(
        process_edit_group_servers,
        AdminStates.editing_promo_group_server_discount,
    )
    dp.message.register(
        process_edit_group_devices,
        AdminStates.editing_promo_group_device_discount,
    )
