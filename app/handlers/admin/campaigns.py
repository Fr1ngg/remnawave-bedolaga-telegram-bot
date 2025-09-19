import logging
import re
from typing import List

from aiogram import Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.campaign import (
    create_campaign,
    delete_campaign,
    get_campaign_by_id,
    get_campaign_by_start_parameter,
    get_campaign_statistics,
    get_campaigns_count,
    get_campaigns_list,
    get_campaigns_overview,
    update_campaign,
)
from app.database.crud.server_squad import get_all_server_squads, get_server_squad_by_id
from app.database.models import User
from app.keyboards.admin import (
    get_admin_campaigns_keyboard,
    get_admin_pagination_keyboard,
    get_campaign_bonus_type_keyboard,
    get_campaign_management_keyboard,
    get_confirmation_keyboard,
)
from app.localization.texts import get_texts
from app.states import AdminStates
from app.utils.decorators import admin_required, error_handler

logger = logging.getLogger(__name__)

_CAMPAIGN_PARAM_REGEX = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
_CAMPAIGNS_PAGE_SIZE = 5


def _format_campaign_summary(campaign, texts) -> str:
    status = "🟢 Активна" if campaign.is_active else "⚪️ Выключена"

    if campaign.is_balance_bonus:
        bonus_text = texts.format_price(campaign.balance_bonus_kopeks)
        bonus_info = f"💰 Бонус на баланс: <b>{bonus_text}</b>"
    else:
        traffic_text = texts.format_traffic(campaign.subscription_traffic_gb or 0)
        bonus_info = (
            "📱 Подписка: <b>{days} д.</b>\n"
            "🌐 Трафик: <b>{traffic}</b>\n"
            "📱 Устройства: <b>{devices}</b>"
        ).format(
            days=campaign.subscription_duration_days or 0,
            traffic=traffic_text,
            devices=campaign.subscription_device_limit or settings.DEFAULT_DEVICE_LIMIT,
        )

    return (
        f"<b>{campaign.name}</b>\n"
        f"Стартовый параметр: <code>{campaign.start_parameter}</code>\n"
        f"Статус: {status}\n"
        f"{bonus_info}\n"
    )


async def _get_bot_deep_link(
    callback: types.CallbackQuery, start_parameter: str
) -> str:
    bot = await callback.bot.get_me()
    return f"https://t.me/{bot.username}?start={start_parameter}"


async def _get_bot_deep_link_from_message(
    message: types.Message, start_parameter: str
) -> str:
    bot = await message.bot.get_me()
    return f"https://t.me/{bot.username}?start={start_parameter}"


async def _send_campaign_update_message(
    message: types.Message,
    db_user: User,
    campaign,
    prefix: str,
) -> None:
    texts = get_texts(db_user.language)
    deep_link = await _get_bot_deep_link_from_message(message, campaign.start_parameter)
    summary = _format_campaign_summary(campaign, texts)
    text = (
        f"{prefix}\n\n"
        f"{summary}\n"
        f"🔗 Ссылка: <code>{deep_link}</code>"
    )

    await message.answer(
        text,
        reply_markup=get_campaign_management_keyboard(
            campaign.id, campaign.is_active, db_user.language
        ),
    )


async def _edit_campaign_update_message(
    callback: types.CallbackQuery,
    db_user: User,
    campaign,
    prefix: str,
) -> None:
    texts = get_texts(db_user.language)
    deep_link = await _get_bot_deep_link(callback, campaign.start_parameter)
    summary = _format_campaign_summary(campaign, texts)
    text = (
        f"{prefix}\n\n"
        f"{summary}\n"
        f"🔗 Ссылка: <code>{deep_link}</code>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=get_campaign_management_keyboard(
            campaign.id, campaign.is_active, db_user.language
        ),
    )


def _build_campaign_servers_keyboard(
    servers, selected_uuids: List[str]
) -> types.InlineKeyboardMarkup:
    keyboard: List[List[types.InlineKeyboardButton]] = []

    for server in servers[:20]:
        is_selected = server.squad_uuid in selected_uuids
        emoji = "✅" if is_selected else ("⚪" if server.is_available else "🔒")
        text = f"{emoji} {server.display_name}"
        keyboard.append(
            [
                types.InlineKeyboardButton(
                    text=text, callback_data=f"campaign_toggle_server_{server.id}"
                )
            ]
        )

    keyboard.append(
        [
            types.InlineKeyboardButton(
                text="✅ Сохранить", callback_data="campaign_servers_save"
            ),
            types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_campaigns"),
        ]
    )

    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)


def _build_edit_campaign_servers_keyboard(
    servers, selected_uuids: List[str], campaign_id: int
) -> types.InlineKeyboardMarkup:
    keyboard: List[List[types.InlineKeyboardButton]] = []

    for server in servers[:20]:
        is_selected = server.squad_uuid in selected_uuids
        emoji = "✅" if is_selected else ("⚪" if server.is_available else "🔒")
        text = f"{emoji} {server.display_name}"
        keyboard.append(
            [
                types.InlineKeyboardButton(
                    text=text,
                    callback_data=f"campaign_edit_toggle_server_{server.id}",
                )
            ]
        )

    keyboard.append(
        [
            types.InlineKeyboardButton(
                text="✅ Сохранить", callback_data="campaign_edit_servers_save"
            ),
            types.InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=f"campaign_edit_bonus_menu_{campaign_id}",
            ),
        ]
    )

    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)


@admin_required
@error_handler
async def show_campaigns_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    overview = await get_campaigns_overview(db)

    text = (
        "📣 <b>Рекламные кампании</b>\n\n"
        f"Всего кампаний: <b>{overview['total']}</b>\n"
        f"Активных: <b>{overview['active']}</b> | Выключены: <b>{overview['inactive']}</b>\n"
        f"Регистраций: <b>{overview['registrations']}</b>\n"
        f"Выдано баланса: <b>{texts.format_price(overview['balance_total'])}</b>\n"
        f"Выдано подписок: <b>{overview['subscription_total']}</b>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=get_admin_campaigns_keyboard(db_user.language),
    )
    await callback.answer()


@admin_required
@error_handler
async def show_campaigns_overall_stats(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    overview = await get_campaigns_overview(db)

    text = ["📊 <b>Общая статистика кампаний</b>\n"]
    text.append(f"Всего кампаний: <b>{overview['total']}</b>")
    text.append(
        f"Активны: <b>{overview['active']}</b>, выключены: <b>{overview['inactive']}</b>"
    )
    text.append(f"Всего регистраций: <b>{overview['registrations']}</b>")
    text.append(
        f"Суммарно выдано баланса: <b>{texts.format_price(overview['balance_total'])}</b>"
    )
    text.append(f"Выдано подписок: <b>{overview['subscription_total']}</b>")

    await callback.message.edit_text(
        "\n".join(text),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="⬅️ Назад", callback_data="admin_campaigns"
                    )
                ]
            ]
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def show_campaigns_list(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)

    page = 1
    if callback.data.startswith("admin_campaigns_list_page_"):
        try:
            page = int(callback.data.split("_")[-1])
        except ValueError:
            page = 1

    offset = (page - 1) * _CAMPAIGNS_PAGE_SIZE
    campaigns = await get_campaigns_list(
        db,
        offset=offset,
        limit=_CAMPAIGNS_PAGE_SIZE,
    )
    total = await get_campaigns_count(db)
    total_pages = max(1, (total + _CAMPAIGNS_PAGE_SIZE - 1) // _CAMPAIGNS_PAGE_SIZE)

    if not campaigns:
        await callback.message.edit_text(
            "❌ Рекламные кампании не найдены.",
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            text="➕ Создать", callback_data="admin_campaigns_create"
                        )
                    ],
                    [
                        types.InlineKeyboardButton(
                            text="⬅️ Назад", callback_data="admin_campaigns"
                        )
                    ],
                ]
            ),
        )
        await callback.answer()
        return

    text_lines = ["📋 <b>Список кампаний</b>\n"]

    for campaign in campaigns:
        registrations = len(campaign.registrations or [])
        total_balance = sum(
            r.balance_bonus_kopeks or 0 for r in campaign.registrations or []
        )
        status = "🟢" if campaign.is_active else "⚪"
        line = (
            f"{status} <b>{campaign.name}</b> — <code>{campaign.start_parameter}</code>\n"
            f"   Регистраций: {registrations}, баланс: {texts.format_price(total_balance)}"
        )
        if campaign.is_subscription_bonus:
            line += f", подписка: {campaign.subscription_duration_days or 0} д."
        else:
            line += ", бонус: баланс"
        text_lines.append(line)

    keyboard_rows = [
        [
            types.InlineKeyboardButton(
                text=f"🔍 {campaign.name}",
                callback_data=f"admin_campaign_manage_{campaign.id}",
            )
        ]
        for campaign in campaigns
    ]

    pagination = get_admin_pagination_keyboard(
        current_page=page,
        total_pages=total_pages,
        callback_prefix="admin_campaigns_list",
        back_callback="admin_campaigns",
        language=db_user.language,
    )

    keyboard_rows.extend(pagination.inline_keyboard)

    await callback.message.edit_text(
        "\n".join(text_lines),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
    )
    await callback.answer()


@admin_required
@error_handler
async def show_campaign_detail(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    await state.clear()
    campaign_id = int(callback.data.split("_")[-1])
    campaign = await get_campaign_by_id(db, campaign_id)

    if not campaign:
        await callback.answer("❌ Кампания не найдена", show_alert=True)
        return

    texts = get_texts(db_user.language)
    stats = await get_campaign_statistics(db, campaign_id)
    deep_link = await _get_bot_deep_link(callback, campaign.start_parameter)

    text = ["📣 <b>Управление кампанией</b>\n"]
    text.append(_format_campaign_summary(campaign, texts))
    text.append(f"🔗 Ссылка: <code>{deep_link}</code>")
    text.append("\n📊 <b>Статистика</b>")
    text.append(f"• Регистраций: <b>{stats['registrations']}</b>")
    text.append(
        f"• Выдано баланса: <b>{texts.format_price(stats['balance_issued'])}</b>"
    )
    text.append(f"• Выдано подписок: <b>{stats['subscription_issued']}</b>")
    if stats["last_registration"]:
        text.append(
            f"• Последняя: {stats['last_registration'].strftime('%d.%m.%Y %H:%M')}"
        )

    await callback.message.edit_text(
        "\n".join(text),
        reply_markup=get_campaign_management_keyboard(
            campaign.id, campaign.is_active, db_user.language
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_campaign_status(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    campaign_id = int(callback.data.split("_")[-1])
    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await callback.answer("❌ Кампания не найдена", show_alert=True)
        return

    new_status = not campaign.is_active
    await update_campaign(db, campaign, is_active=new_status)
    status_text = "включена" if new_status else "выключена"
    logger.info("🔄 Кампания %s переключена: %s", campaign_id, status_text)

    await show_campaign_detail(callback, db_user, state, db)


@admin_required
@error_handler
async def show_campaign_stats(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    campaign_id = int(callback.data.split("_")[-1])
    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await callback.answer("❌ Кампания не найдена", show_alert=True)
        return

    texts = get_texts(db_user.language)
    stats = await get_campaign_statistics(db, campaign_id)

    text = ["📊 <b>Статистика кампании</b>\n"]
    text.append(_format_campaign_summary(campaign, texts))
    text.append(f"Регистраций: <b>{stats['registrations']}</b>")
    text.append(f"Выдано баланса: <b>{texts.format_price(stats['balance_issued'])}</b>")
    text.append(f"Выдано подписок: <b>{stats['subscription_issued']}</b>")
    if stats["last_registration"]:
        text.append(
            f"Последняя регистрация: {stats['last_registration'].strftime('%d.%m.%Y %H:%M')}"
        )

    await callback.message.edit_text(
        "\n".join(text),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="⬅️ Назад",
                        callback_data=f"admin_campaign_manage_{campaign_id}",
                    )
                ]
            ]
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def confirm_delete_campaign(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    campaign_id = int(callback.data.split("_")[-1])
    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await callback.answer("❌ Кампания не найдена", show_alert=True)
        return

    text = (
        "🗑️ <b>Удаление кампании</b>\n\n"
        f"Название: <b>{campaign.name}</b>\n"
        f"Параметр: <code>{campaign.start_parameter}</code>\n\n"
        "Вы уверены, что хотите удалить кампанию?"
    )

    await callback.message.edit_text(
        text,
        reply_markup=get_confirmation_keyboard(
            confirm_action=f"admin_campaign_delete_confirm_{campaign_id}",
            cancel_action=f"admin_campaign_manage_{campaign_id}",
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def delete_campaign_confirmed(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    campaign_id = int(callback.data.split("_")[-1])
    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await callback.answer("❌ Кампания не найдена", show_alert=True)
        return

    await delete_campaign(db, campaign)
    await callback.message.edit_text(
        "✅ Кампания удалена.",
        reply_markup=get_admin_campaigns_keyboard(db_user.language),
    )
    await callback.answer("Удалено")


@admin_required
@error_handler
async def open_campaign_edit_menu(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    campaign_id = int(callback.data.split("_")[-1])
    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await callback.answer("❌ Кампания не найдена", show_alert=True)
        return

    await state.clear()

    texts = get_texts(db_user.language)
    summary = _format_campaign_summary(campaign, texts)
    deep_link = await _get_bot_deep_link(callback, campaign.start_parameter)
    text = (
        "✏️ <b>Редактирование кампании</b>\n\n"
        f"{summary}\n"
        f"🔗 Ссылка: <code>{deep_link}</code>\n\n"
        "Выберите, что хотите изменить:"
    )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="✏️ Название",
                    callback_data=f"campaign_edit_name_{campaign_id}",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="🔗 Стартовый параметр",
                    callback_data=f"campaign_edit_start_{campaign_id}",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="🎯 Тип бонуса",
                    callback_data=f"campaign_edit_bonus_menu_{campaign_id}",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=f"admin_campaign_manage_{campaign_id}",
                )
            ],
        ]
    )

    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@admin_required
@error_handler
async def prompt_edit_campaign_name(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    campaign_id = int(callback.data.split("_")[-1])
    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await callback.answer("❌ Кампания не найдена", show_alert=True)
        return

    await state.set_state(AdminStates.editing_campaign_name)
    await state.update_data(campaign_id=campaign_id)

    await callback.message.edit_text(
        "✏️ Введите новое название кампании (3-100 символов):",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="❌ Отмена",
                        callback_data=f"admin_campaign_edit_{campaign_id}",
                    )
                ]
            ]
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_campaign_name(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    data = await state.get_data()
    campaign_id = data.get("campaign_id")
    if not campaign_id:
        await state.clear()
        await message.answer("❌ Сессия редактирования не найдена. Попробуйте снова.")
        return

    name = (message.text or "").strip()
    if len(name) < 3 or len(name) > 100:
        await message.answer("❌ Название должно содержать от 3 до 100 символов.")
        return

    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await state.clear()
        await message.answer("❌ Кампания не найдена. Операция отменена.")
        return

    await update_campaign(db, campaign, name=name)
    await state.clear()

    await _send_campaign_update_message(
        message, db_user, campaign, "✅ Название кампании обновлено."
    )


@admin_required
@error_handler
async def prompt_edit_campaign_start_parameter(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    campaign_id = int(callback.data.split("_")[-1])
    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await callback.answer("❌ Кампания не найдена", show_alert=True)
        return

    await state.set_state(AdminStates.editing_campaign_start)
    await state.update_data(campaign_id=campaign_id)

    text = (
        "🔗 Введите новый параметр старта (латиница, цифры, - и _, 3-32 символа).\n"
        f"Текущее значение: <code>{campaign.start_parameter}</code>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="❌ Отмена",
                        callback_data=f"admin_campaign_edit_{campaign_id}",
                    )
                ]
            ]
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_campaign_start_parameter(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    data = await state.get_data()
    campaign_id = data.get("campaign_id")
    if not campaign_id:
        await state.clear()
        await message.answer("❌ Сессия редактирования не найдена. Попробуйте снова.")
        return

    start_param = (message.text or "").strip()
    if not _CAMPAIGN_PARAM_REGEX.match(start_param):
        await message.answer(
            "❌ Разрешены только латинские буквы, цифры, символы - и _. Длина 3-32 символа."
        )
        return

    existing = await get_campaign_by_start_parameter(db, start_param)
    if existing and existing.id != campaign_id:
        await message.answer(
            "❌ Кампания с таким параметром уже существует. Введите другой параметр."
        )
        return

    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await state.clear()
        await message.answer("❌ Кампания не найдена. Операция отменена.")
        return

    await update_campaign(db, campaign, start_parameter=start_param)
    await state.clear()

    await _send_campaign_update_message(
        message, db_user, campaign, "✅ Параметр старта обновлён."
    )


@admin_required
@error_handler
async def open_campaign_edit_bonus_menu(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    campaign_id = int(callback.data.split("_")[-1])
    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await callback.answer("❌ Кампания не найдена", show_alert=True)
        return

    await state.clear()

    current_type = "Бонус на баланс" if campaign.is_balance_bonus else "Подписка"
    text = (
        "🎯 Выберите новый тип бонуса для кампании.\n"
        f"Текущее значение: <b>{current_type}</b>"
    )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="💰 Бонус на баланс",
                    callback_data=f"campaign_edit_bonus_balance_{campaign_id}",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="📱 Подписка",
                    callback_data=f"campaign_edit_bonus_subscription_{campaign_id}",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=f"admin_campaign_edit_{campaign_id}",
                )
            ],
        ]
    )

    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@admin_required
@error_handler
async def prompt_edit_campaign_balance(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    campaign_id = int(callback.data.split("_")[-1])
    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await callback.answer("❌ Кампания не найдена", show_alert=True)
        return

    await state.set_state(AdminStates.editing_campaign_balance)
    await state.update_data(campaign_id=campaign_id)

    texts = get_texts(db_user.language)
    current_amount = texts.format_price(campaign.balance_bonus_kopeks or 0)
    text = (
        "💰 Введите сумму бонуса на баланс в рублях (например, 100 или 99.5).\n"
        f"Текущее значение: <b>{current_amount}</b>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="❌ Отмена",
                        callback_data=f"campaign_edit_bonus_menu_{campaign_id}",
                    )
                ]
            ]
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_campaign_balance(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    data = await state.get_data()
    campaign_id = data.get("campaign_id")
    if not campaign_id:
        await state.clear()
        await message.answer("❌ Сессия редактирования не найдена. Попробуйте снова.")
        return

    try:
        amount_rubles = float((message.text or "").replace(",", "."))
    except ValueError:
        await message.answer("❌ Введите корректную сумму (например, 100 или 99.5)")
        return

    if amount_rubles <= 0:
        await message.answer("❌ Сумма должна быть больше нуля")
        return

    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await state.clear()
        await message.answer("❌ Кампания не найдена. Операция отменена.")
        return

    amount_kopeks = int(round(amount_rubles * 100))

    await update_campaign(
        db,
        campaign,
        bonus_type="balance",
        balance_bonus_kopeks=amount_kopeks,
        subscription_duration_days=None,
        subscription_traffic_gb=None,
        subscription_device_limit=None,
        subscription_squads=[],
    )
    await state.clear()

    await _send_campaign_update_message(
        message, db_user, campaign, "✅ Бонус на баланс обновлён."
    )


@admin_required
@error_handler
async def start_edit_campaign_subscription(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    campaign_id = int(callback.data.split("_")[-1])
    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await callback.answer("❌ Кампания не найдена", show_alert=True)
        return

    servers, _ = await get_all_server_squads(db, available_only=False)
    if not servers:
        await callback.answer("❌ Нет доступных серверов для подписки", show_alert=True)
        return

    await state.set_state(AdminStates.editing_campaign_subscription_days)
    await state.update_data(
        campaign_id=campaign_id,
        campaign_subscription_squads=list(campaign.subscription_squads or []),
    )

    current_days = campaign.subscription_duration_days
    days_text = current_days if current_days else "не задано"
    text = (
        "📅 Введите длительность подписки в днях (1-730).\n"
        f"Текущее значение: <b>{days_text}</b>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="❌ Отмена",
                        callback_data=f"campaign_edit_bonus_menu_{campaign_id}",
                    )
                ]
            ]
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_campaign_subscription_days(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    data = await state.get_data()
    campaign_id = data.get("campaign_id")
    if not campaign_id:
        await state.clear()
        await message.answer("❌ Сессия редактирования не найдена. Попробуйте снова.")
        return

    try:
        days = int((message.text or "").strip())
    except ValueError:
        await message.answer("❌ Введите число дней (1-730)")
        return

    if days <= 0 or days > 730:
        await message.answer("❌ Длительность должна быть от 1 до 730 дней")
        return

    await state.update_data(campaign_subscription_days=days)
    await state.set_state(AdminStates.editing_campaign_subscription_traffic)

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=f"campaign_edit_bonus_menu_{campaign_id}",
                )
            ]
        ]
    )

    await message.answer(
        "🌐 Введите лимит трафика в ГБ (0 = безлимит):",
        reply_markup=keyboard,
    )


@admin_required
@error_handler
async def process_edit_campaign_subscription_traffic(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    data = await state.get_data()
    campaign_id = data.get("campaign_id")
    if not campaign_id:
        await state.clear()
        await message.answer("❌ Сессия редактирования не найдена. Попробуйте снова.")
        return

    try:
        traffic = int((message.text or "").strip())
    except ValueError:
        await message.answer("❌ Введите целое число (0 или больше)")
        return

    if traffic < 0 or traffic > 10000:
        await message.answer("❌ Лимит трафика должен быть от 0 до 10000 ГБ")
        return

    await state.update_data(campaign_subscription_traffic=traffic)
    await state.set_state(AdminStates.editing_campaign_subscription_devices)

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=f"campaign_edit_bonus_menu_{campaign_id}",
                )
            ]
        ]
    )

    await message.answer(
        f"📱 Введите количество устройств (1-{settings.MAX_DEVICES_LIMIT}):",
        reply_markup=keyboard,
    )


@admin_required
@error_handler
async def process_edit_campaign_subscription_devices(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    data = await state.get_data()
    campaign_id = data.get("campaign_id")
    if not campaign_id:
        await state.clear()
        await message.answer("❌ Сессия редактирования не найдена. Попробуйте снова.")
        return

    try:
        devices = int((message.text or "").strip())
    except ValueError:
        await message.answer("❌ Введите целое число устройств")
        return

    if devices < 1 or devices > settings.MAX_DEVICES_LIMIT:
        await message.answer(
            f"❌ Количество устройств должно быть от 1 до {settings.MAX_DEVICES_LIMIT}"
        )
        return

    await state.update_data(campaign_subscription_devices=devices)
    await state.set_state(AdminStates.editing_campaign_subscription_servers)

    servers, _ = await get_all_server_squads(db, available_only=False)
    if not servers:
        await message.answer(
            "❌ Не найдены доступные серверы. Добавьте серверы перед продолжением."
        )
        await state.clear()
        return

    selected = data.get("campaign_subscription_squads", [])
    keyboard = _build_edit_campaign_servers_keyboard(servers, selected, campaign_id)

    await message.answer(
        "🌍 Выберите серверы, которые будут доступны по подписке (максимум 20 отображаются).",
        reply_markup=keyboard,
    )


@admin_required
@error_handler
async def toggle_edit_campaign_server(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    data = await state.get_data()
    campaign_id = data.get("campaign_id")
    if not campaign_id:
        await callback.answer("❌ Сессия редактирования не найдена", show_alert=True)
        return

    server_id = int(callback.data.split("_")[-1])
    server = await get_server_squad_by_id(db, server_id)
    if not server:
        await callback.answer("❌ Сервер не найден", show_alert=True)
        return

    selected = list(data.get("campaign_subscription_squads", []))

    if server.squad_uuid in selected:
        selected.remove(server.squad_uuid)
    else:
        selected.append(server.squad_uuid)

    await state.update_data(campaign_subscription_squads=selected)

    servers, _ = await get_all_server_squads(db, available_only=False)
    keyboard = _build_edit_campaign_servers_keyboard(servers, selected, campaign_id)

    await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer()


@admin_required
@error_handler
async def finalize_edit_campaign_subscription(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    data = await state.get_data()
    campaign_id = data.get("campaign_id")
    if not campaign_id:
        await callback.answer("❌ Сессия редактирования не найдена", show_alert=True)
        return

    selected = data.get("campaign_subscription_squads", [])
    if not selected:
        await callback.answer("❗ Выберите хотя бы один сервер", show_alert=True)
        return

    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await state.clear()
        await callback.answer("❌ Кампания не найдена", show_alert=True)
        return

    await update_campaign(
        db,
        campaign,
        bonus_type="subscription",
        balance_bonus_kopeks=0,
        subscription_duration_days=data.get("campaign_subscription_days"),
        subscription_traffic_gb=data.get("campaign_subscription_traffic"),
        subscription_device_limit=data.get("campaign_subscription_devices"),
        subscription_squads=selected,
    )
    await state.clear()

    await _edit_campaign_update_message(
        callback, db_user, campaign, "✅ Настройки подписки обновлены."
    )
    await callback.answer()


@admin_required
@error_handler
async def start_campaign_creation(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    await state.clear()
    await callback.message.edit_text(
        "🆕 <b>Создание рекламной кампании</b>\n\nВведите название кампании:",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="⬅️ Назад", callback_data="admin_campaigns"
                    )
                ]
            ]
        ),
    )
    await state.set_state(AdminStates.creating_campaign_name)
    await callback.answer()


@admin_required
@error_handler
async def process_campaign_name(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    name = message.text.strip()
    if len(name) < 3 or len(name) > 100:
        await message.answer(
            "❌ Название должно содержать от 3 до 100 символов. Попробуйте снова."
        )
        return

    await state.update_data(campaign_name=name)
    await state.set_state(AdminStates.creating_campaign_start)
    await message.answer(
        "🔗 Теперь введите параметр старта (латинские буквы, цифры, - или _):",
    )


@admin_required
@error_handler
async def process_campaign_start_parameter(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    start_param = message.text.strip()
    if not _CAMPAIGN_PARAM_REGEX.match(start_param):
        await message.answer(
            "❌ Разрешены только латинские буквы, цифры, символы - и _. Длина 3-32 символа."
        )
        return

    existing = await get_campaign_by_start_parameter(db, start_param)
    if existing:
        await message.answer(
            "❌ Кампания с таким параметром уже существует. Введите другой параметр."
        )
        return

    await state.update_data(campaign_start_parameter=start_param)
    await state.set_state(AdminStates.creating_campaign_bonus)
    await message.answer(
        "🎯 Выберите тип бонуса для кампании:",
        reply_markup=get_campaign_bonus_type_keyboard(db_user.language),
    )


@admin_required
@error_handler
async def select_campaign_bonus_type(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    bonus_type = "balance" if callback.data.endswith("balance") else "subscription"
    await state.update_data(campaign_bonus_type=bonus_type)

    if bonus_type == "balance":
        await state.set_state(AdminStates.creating_campaign_balance)
        await callback.message.edit_text(
            "💰 Введите сумму бонуса на баланс (в рублях):",
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            text="⬅️ Назад", callback_data="admin_campaigns"
                        )
                    ]
                ]
            ),
        )
    else:
        await state.set_state(AdminStates.creating_campaign_subscription_days)
        await callback.message.edit_text(
            "📅 Введите длительность подписки в днях (1-730):",
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            text="⬅️ Назад", callback_data="admin_campaigns"
                        )
                    ]
                ]
            ),
        )
    await callback.answer()


@admin_required
@error_handler
async def process_campaign_balance_value(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    try:
        amount_rubles = float(message.text.replace(",", "."))
    except ValueError:
        await message.answer("❌ Введите корректную сумму (например, 100 или 99.5)")
        return

    if amount_rubles <= 0:
        await message.answer("❌ Сумма должна быть больше нуля")
        return

    amount_kopeks = int(round(amount_rubles * 100))
    data = await state.get_data()

    campaign = await create_campaign(
        db,
        name=data["campaign_name"],
        start_parameter=data["campaign_start_parameter"],
        bonus_type="balance",
        balance_bonus_kopeks=amount_kopeks,
        created_by=db_user.id,
    )

    await state.clear()

    deep_link = await _get_bot_deep_link_from_message(message, campaign.start_parameter)
    texts = get_texts(db_user.language)
    summary = _format_campaign_summary(campaign, texts)
    text = (
        "✅ <b>Кампания создана!</b>\n\n"
        f"{summary}\n"
        f"🔗 Ссылка: <code>{deep_link}</code>"
    )

    await message.answer(
        text,
        reply_markup=get_campaign_management_keyboard(
            campaign.id, campaign.is_active, db_user.language
        ),
    )


@admin_required
@error_handler
async def process_campaign_subscription_days(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    try:
        days = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите число дней (1-730)")
        return

    if days <= 0 or days > 730:
        await message.answer("❌ Длительность должна быть от 1 до 730 дней")
        return

    await state.update_data(campaign_subscription_days=days)
    await state.set_state(AdminStates.creating_campaign_subscription_traffic)
    await message.answer("🌐 Введите лимит трафика в ГБ (0 = безлимит):")


@admin_required
@error_handler
async def process_campaign_subscription_traffic(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    try:
        traffic = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите целое число (0 или больше)")
        return

    if traffic < 0 or traffic > 10000:
        await message.answer("❌ Лимит трафика должен быть от 0 до 10000 ГБ")
        return

    await state.update_data(campaign_subscription_traffic=traffic)
    await state.set_state(AdminStates.creating_campaign_subscription_devices)
    await message.answer(
        f"📱 Введите количество устройств (1-{settings.MAX_DEVICES_LIMIT}):"
    )


@admin_required
@error_handler
async def process_campaign_subscription_devices(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    try:
        devices = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введите целое число устройств")
        return

    if devices < 1 or devices > settings.MAX_DEVICES_LIMIT:
        await message.answer(
            f"❌ Количество устройств должно быть от 1 до {settings.MAX_DEVICES_LIMIT}"
        )
        return

    await state.update_data(campaign_subscription_devices=devices)
    await state.update_data(campaign_subscription_squads=[])
    await state.set_state(AdminStates.creating_campaign_subscription_servers)

    servers, _ = await get_all_server_squads(db, available_only=False)
    if not servers:
        await message.answer(
            "❌ Не найдены доступные серверы. Добавьте сервера перед созданием кампании.",
        )
        await state.clear()
        return

    keyboard = _build_campaign_servers_keyboard(servers, [])
    await message.answer(
        "🌍 Выберите серверы, которые будут доступны по подписке (максимум 20 отображаются).",
        reply_markup=keyboard,
    )


@admin_required
@error_handler
async def toggle_campaign_server(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    server_id = int(callback.data.split("_")[-1])
    server = await get_server_squad_by_id(db, server_id)
    if not server:
        await callback.answer("❌ Сервер не найден", show_alert=True)
        return

    data = await state.get_data()
    selected = list(data.get("campaign_subscription_squads", []))

    if server.squad_uuid in selected:
        selected.remove(server.squad_uuid)
    else:
        selected.append(server.squad_uuid)

    await state.update_data(campaign_subscription_squads=selected)

    servers, _ = await get_all_server_squads(db, available_only=False)
    keyboard = _build_campaign_servers_keyboard(servers, selected)

    await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer()


@admin_required
@error_handler
async def finalize_campaign_subscription(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    data = await state.get_data()
    selected = data.get("campaign_subscription_squads", [])

    if not selected:
        await callback.answer("❗ Выберите хотя бы один сервер", show_alert=True)
        return

    campaign = await create_campaign(
        db,
        name=data["campaign_name"],
        start_parameter=data["campaign_start_parameter"],
        bonus_type="subscription",
        subscription_duration_days=data.get("campaign_subscription_days"),
        subscription_traffic_gb=data.get("campaign_subscription_traffic"),
        subscription_device_limit=data.get("campaign_subscription_devices"),
        subscription_squads=selected,
        created_by=db_user.id,
    )

    await state.clear()

    deep_link = await _get_bot_deep_link(callback, campaign.start_parameter)
    texts = get_texts(db_user.language)
    summary = _format_campaign_summary(campaign, texts)
    text = (
        "✅ <b>Кампания создана!</b>\n\n"
        f"{summary}\n"
        f"🔗 Ссылка: <code>{deep_link}</code>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=get_campaign_management_keyboard(
            campaign.id, campaign.is_active, db_user.language
        ),
    )
    await callback.answer()


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_campaigns_menu, F.data == "admin_campaigns")
    dp.callback_query.register(
        show_campaigns_overall_stats, F.data == "admin_campaigns_stats"
    )
    dp.callback_query.register(show_campaigns_list, F.data == "admin_campaigns_list")
    dp.callback_query.register(
        show_campaigns_list, F.data.startswith("admin_campaigns_list_page_")
    )
    dp.callback_query.register(
        start_campaign_creation, F.data == "admin_campaigns_create"
    )
    dp.callback_query.register(
        show_campaign_stats, F.data.startswith("admin_campaign_stats_")
    )
    dp.callback_query.register(
        show_campaign_detail, F.data.startswith("admin_campaign_manage_")
    )
    dp.callback_query.register(
        open_campaign_edit_menu, F.data.startswith("admin_campaign_edit_")
    )
    dp.callback_query.register(
        delete_campaign_confirmed, F.data.startswith("admin_campaign_delete_confirm_")
    )
    dp.callback_query.register(
        confirm_delete_campaign, F.data.startswith("admin_campaign_delete_")
    )
    dp.callback_query.register(
        toggle_campaign_status, F.data.startswith("admin_campaign_toggle_")
    )
    dp.callback_query.register(
        prompt_edit_campaign_name, F.data.startswith("campaign_edit_name_")
    )
    dp.callback_query.register(
        prompt_edit_campaign_start_parameter,
        F.data.startswith("campaign_edit_start_"),
    )
    dp.callback_query.register(
        open_campaign_edit_bonus_menu,
        F.data.startswith("campaign_edit_bonus_menu_"),
    )
    dp.callback_query.register(
        prompt_edit_campaign_balance,
        F.data.startswith("campaign_edit_bonus_balance_"),
    )
    dp.callback_query.register(
        start_edit_campaign_subscription,
        F.data.startswith("campaign_edit_bonus_subscription_"),
    )
    dp.callback_query.register(
        finalize_campaign_subscription, F.data == "campaign_servers_save"
    )
    dp.callback_query.register(
        finalize_edit_campaign_subscription, F.data == "campaign_edit_servers_save"
    )
    dp.callback_query.register(
        toggle_campaign_server, F.data.startswith("campaign_toggle_server_")
    )
    dp.callback_query.register(
        toggle_edit_campaign_server, F.data.startswith("campaign_edit_toggle_server_")
    )
    dp.callback_query.register(
        select_campaign_bonus_type, F.data.startswith("campaign_bonus_")
    )

    dp.message.register(process_campaign_name, AdminStates.creating_campaign_name)
    dp.message.register(
        process_campaign_start_parameter, AdminStates.creating_campaign_start
    )
    dp.message.register(
        process_campaign_balance_value, AdminStates.creating_campaign_balance
    )
    dp.message.register(
        process_campaign_subscription_days,
        AdminStates.creating_campaign_subscription_days,
    )
    dp.message.register(
        process_campaign_subscription_traffic,
        AdminStates.creating_campaign_subscription_traffic,
    )
    dp.message.register(
        process_campaign_subscription_devices,
        AdminStates.creating_campaign_subscription_devices,
    )
    dp.message.register(
        process_edit_campaign_name, AdminStates.editing_campaign_name
    )
    dp.message.register(
        process_edit_campaign_start_parameter, AdminStates.editing_campaign_start
    )
    dp.message.register(
        process_edit_campaign_balance, AdminStates.editing_campaign_balance
    )
    dp.message.register(
        process_edit_campaign_subscription_days,
        AdminStates.editing_campaign_subscription_days,
    )
    dp.message.register(
        process_edit_campaign_subscription_traffic,
        AdminStates.editing_campaign_subscription_traffic,
    )
    dp.message.register(
        process_edit_campaign_subscription_devices,
        AdminStates.editing_campaign_subscription_devices,
    )
