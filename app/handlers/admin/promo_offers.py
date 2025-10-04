from __future__ import annotations

import logging
import re
from typing import List, Sequence

from aiogram import Dispatcher, F
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.discount_offer import upsert_discount_offer
from app.database.crud.promo_offer_template import (
    ensure_default_templates,
    get_promo_offer_template_by_id,
    list_promo_offer_templates,
    update_promo_offer_template,
)
from app.database.crud.user import get_users_for_promo_segment
from app.database.models import PromoOfferTemplate, User
from app.localization.texts import get_texts
from app.states import AdminStates
from app.utils.decorators import admin_required, error_handler

logger = logging.getLogger(__name__)


OFFER_TYPE_CONFIG = {
    "test_access": {
        "icon": "🧪",
        "label_key": "ADMIN_PROMO_OFFER_TEST_ACCESS",
        "default_label": "Тестовые сервера",
        "allowed_segments": [
            ("paid_active", "🟢 Активные платные"),
            ("trial_active", "🎁 Активные триалы"),
        ],
        "effect_type": "test_access",
    },
    "extend_discount": {
        "icon": "💎",
        "label_key": "ADMIN_PROMO_OFFER_EXTEND",
        "default_label": "Скидка на продление",
        "allowed_segments": [
            ("paid_active", "🟢 Активные платные"),
        ],
        "effect_type": "percent_discount",
    },
    "purchase_discount": {
        "icon": "🎯",
        "label_key": "ADMIN_PROMO_OFFER_PURCHASE",
        "default_label": "Скидка на покупку",
        "allowed_segments": [
            ("paid_expired", "🔴 Истёкшие платные"),
            ("trial_expired", "🥶 Истёкшие триалы"),
        ],
        "effect_type": "percent_discount",
    },
}

def _format_bonus(template: PromoOfferTemplate) -> str:
    return settings.format_price(template.bonus_amount_kopeks or 0)


def _render_template_text(template: PromoOfferTemplate, language: str) -> str:
    replacements = {
        "discount_percent": template.discount_percent,
        "bonus_amount": _format_bonus(template),
        "valid_hours": template.valid_hours,
        "test_duration_hours": template.test_duration_hours or 0,
    }
    try:
        return template.message_text.format(**replacements)
    except Exception:  # pragma: no cover - fallback for invalid placeholders
        logger.warning("Не удалось форматировать текст промо-предложения %s", template.id)
        return template.message_text


def _build_templates_keyboard(templates: Sequence[PromoOfferTemplate], language: str) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    rows: List[List[InlineKeyboardButton]] = []
    for template in templates:
        config = OFFER_TYPE_CONFIG.get(template.offer_type, {})
        icon = config.get("icon", "📨")
        label = texts.t(config.get("label_key", ""), config.get("default_label", template.offer_type))
        rows.append([
            InlineKeyboardButton(
                text=f"{icon} {label}",
                callback_data=f"promo_offer_{template.id}",
            )
        ])
    rows.append([InlineKeyboardButton(text=texts.BACK, callback_data="admin_submenu_communications")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_offer_detail_keyboard(template: PromoOfferTemplate, language: str) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    config = OFFER_TYPE_CONFIG.get(template.offer_type, {})
    rows: List[List[InlineKeyboardButton]] = []

    rows.append([
        InlineKeyboardButton(text="✏️ Текст", callback_data=f"promo_offer_edit_message_{template.id}"),
        InlineKeyboardButton(text="🪄 Кнопка", callback_data=f"promo_offer_edit_button_{template.id}"),
    ])
    rows.append([
        InlineKeyboardButton(text="⏱️ Срок", callback_data=f"promo_offer_edit_valid_{template.id}"),
    ])

    if template.offer_type != "test_access":
        rows[-1].append(InlineKeyboardButton(text="📉 %", callback_data=f"promo_offer_edit_discount_{template.id}"))
    else:
        rows.append([
            InlineKeyboardButton(text="⏳ Длительность", callback_data=f"promo_offer_edit_duration_{template.id}"),
            InlineKeyboardButton(text="🌍 Сквады", callback_data=f"promo_offer_edit_squads_{template.id}"),
        ])

    rows.append([
        InlineKeyboardButton(text="📬 Отправить", callback_data=f"promo_offer_send_menu_{template.id}"),
    ])
    rows.append([
        InlineKeyboardButton(text=texts.BACK, callback_data="admin_messages"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_send_keyboard(template: PromoOfferTemplate, language: str) -> InlineKeyboardMarkup:
    config = OFFER_TYPE_CONFIG.get(template.offer_type, {})
    segments = config.get("allowed_segments", [])
    rows = [
        [
            InlineKeyboardButton(
                text=label,
                callback_data=f"promo_offer_send_{template.id}_{segment}",
            )
        ]
        for segment, label in segments
    ]
    texts = get_texts(language)
    rows.append([InlineKeyboardButton(text=texts.BACK, callback_data=f"promo_offer_{template.id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _describe_offer(template: PromoOfferTemplate, language: str) -> str:
    texts = get_texts(language)
    config = OFFER_TYPE_CONFIG.get(template.offer_type, {})
    label = texts.t(config.get("label_key", ""), config.get("default_label", template.offer_type))
    icon = config.get("icon", "📨")

    lines = [f"{icon} <b>{template.name}</b>", ""]
    lines.append(texts.t("ADMIN_PROMO_OFFER_TYPE", "Тип: {label}").format(label=label))
    lines.append(texts.t("ADMIN_PROMO_OFFER_VALID", "Срок действия: {hours} ч").format(hours=template.valid_hours))

    if template.offer_type != "test_access":
        lines.append(texts.t("ADMIN_PROMO_OFFER_DISCOUNT", "Скидка: {percent}%").format(percent=template.discount_percent))
    else:
        duration = template.test_duration_hours or 0
        lines.append(texts.t("ADMIN_PROMO_OFFER_TEST_DURATION", "Доступ: {hours} ч").format(hours=duration))
        squads = template.test_squad_uuids or []
        if squads:
            lines.append(texts.t("ADMIN_PROMO_OFFER_TEST_SQUADS", "Сквады: {squads}").format(squads=", ".join(squads)))
        else:
            lines.append(texts.t("ADMIN_PROMO_OFFER_TEST_SQUADS_EMPTY", "Сквады: не указаны"))

    allowed_segments = config.get("allowed_segments", [])
    if allowed_segments:
        segment_labels = [label for _, label in allowed_segments]
        lines.append("")
        lines.append(texts.t("ADMIN_PROMO_OFFER_ALLOWED", "Доступные категории:") )
        lines.extend(segment_labels)

    lines.append("")
    lines.append(texts.t("ADMIN_PROMO_OFFER_PREVIEW", "Предпросмотр:"))
    lines.append(_render_template_text(template, language))

    return "\n".join(lines)


@admin_required
@error_handler
async def show_promo_offers_menu(callback: CallbackQuery, db_user: User, db: AsyncSession):
    await ensure_default_templates(db, created_by=db_user.id)
    templates = await list_promo_offer_templates(db)
    texts = get_texts(db_user.language)
    header = texts.t("ADMIN_PROMO_OFFERS_TITLE", "🎯 <b>Промо-предложения</b>\n\nВыберите предложение для настройки:")
    await callback.message.edit_text(
        header,
        reply_markup=_build_templates_keyboard(templates, db_user.language),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_required
@error_handler
async def show_promo_offer_details(callback: CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    try:
        template_id = int(callback.data.split("_")[-1])
    except (ValueError, AttributeError):
        await callback.answer("❌ Неверный идентификатор", show_alert=True)
        return

    template = await get_promo_offer_template_by_id(db, template_id)
    if not template:
        await callback.answer("❌ Предложение не найдено", show_alert=True)
        return

    await state.update_data(selected_promo_offer=template.id)
    description = _describe_offer(template, db_user.language)
    await callback.message.edit_text(
        description,
        reply_markup=_build_offer_detail_keyboard(template, db_user.language),
        parse_mode="HTML",
    )
    await callback.answer()


async def _prompt_edit(callback: CallbackQuery, state: FSMContext, template_id: int, prompt: str, new_state):
    await state.update_data(
        selected_promo_offer=template_id,
        promo_edit_message_id=callback.message.message_id,
        promo_edit_chat_id=callback.message.chat.id,
    )
    await callback.message.edit_text(prompt)
    await state.set_state(new_state)
    await callback.answer()


@admin_required
@error_handler
async def prompt_edit_message(callback: CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    template_id = int(callback.data.split("_")[-1])
    texts = get_texts(db_user.language)
    prompt = texts.t("ADMIN_PROMO_OFFER_PROMPT_MESSAGE", "Введите новый текст предложения:")
    await _prompt_edit(callback, state, template_id, prompt, AdminStates.editing_promo_offer_message)


@admin_required
@error_handler
async def prompt_edit_button(callback: CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    template_id = int(callback.data.split("_")[-1])
    texts = get_texts(db_user.language)
    prompt = texts.t("ADMIN_PROMO_OFFER_PROMPT_BUTTON", "Введите новый текст кнопки:")
    await _prompt_edit(callback, state, template_id, prompt, AdminStates.editing_promo_offer_button)


@admin_required
@error_handler
async def prompt_edit_valid(callback: CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    template_id = int(callback.data.split("_")[-1])
    texts = get_texts(db_user.language)
    prompt = texts.t("ADMIN_PROMO_OFFER_PROMPT_VALID", "Укажите срок действия (в часах):")
    await _prompt_edit(callback, state, template_id, prompt, AdminStates.editing_promo_offer_valid_hours)


@admin_required
@error_handler
async def prompt_edit_discount(callback: CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    template_id = int(callback.data.split("_")[-1])
    texts = get_texts(db_user.language)
    prompt = texts.t("ADMIN_PROMO_OFFER_PROMPT_DISCOUNT", "Введите размер скидки в процентах:")
    await _prompt_edit(callback, state, template_id, prompt, AdminStates.editing_promo_offer_discount)


@admin_required
@error_handler
async def prompt_edit_duration(callback: CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    template_id = int(callback.data.split("_")[-1])
    texts = get_texts(db_user.language)
    prompt = texts.t("ADMIN_PROMO_OFFER_PROMPT_DURATION", "Введите длительность тестового доступа (в часах):")
    await _prompt_edit(callback, state, template_id, prompt, AdminStates.editing_promo_offer_test_duration)


@admin_required
@error_handler
async def prompt_edit_squads(callback: CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    template_id = int(callback.data.split("_")[-1])
    texts = get_texts(db_user.language)
    prompt = texts.t(
        "ADMIN_PROMO_OFFER_PROMPT_SQUADS",
        "Перечислите UUID сквадов через запятую или пробел. Для очистки отправьте 'clear':",
    )
    await _prompt_edit(callback, state, template_id, prompt, AdminStates.editing_promo_offer_squads)


async def _handle_edit_field(
    message: Message,
    state: FSMContext,
    db: AsyncSession,
    db_user: User,
    field: str,
):
    data = await state.get_data()
    template_id = data.get("selected_promo_offer")
    if not template_id:
        await message.answer("❌ Не удалось определить предложение. Повторите действие.")
        await state.clear()
        return

    template = await get_promo_offer_template_by_id(db, int(template_id))
    if not template:
        await message.answer("❌ Предложение не найдено.")
        await state.clear()
        return

    value = message.text.strip()
    try:
        if field == "message_text":
            await update_promo_offer_template(db, template, message_text=value)
        elif field == "button_text":
            await update_promo_offer_template(db, template, button_text=value)
        elif field == "valid_hours":
            hours = max(1, int(value))
            await update_promo_offer_template(db, template, valid_hours=hours)
        elif field == "discount_percent":
            percent = max(0, min(100, int(value)))
            await update_promo_offer_template(db, template, discount_percent=percent)
        elif field == "test_duration_hours":
            hours = max(1, int(value))
            await update_promo_offer_template(db, template, test_duration_hours=hours)
        elif field == "test_squad_uuids":
            if value.lower() in {"clear", "очистить"}:
                squads: List[str] = []
            else:
                squads = [item for item in re.split(r"[\s,]+", value) if item]
            await update_promo_offer_template(db, template, test_squad_uuids=squads)
        else:
            raise ValueError("Unsupported field")
    except ValueError:
        await message.answer("❌ Некорректное значение. Попробуйте снова.")
        return

    edit_message_id = data.get("promo_edit_message_id")
    edit_chat_id = data.get("promo_edit_chat_id", message.chat.id)

    await state.clear()
    updated_template = await get_promo_offer_template_by_id(db, template.id)
    if not updated_template:
        await message.answer("❌ Предложение не найдено после обновления.")
        return

    description = _describe_offer(updated_template, db_user.language)
    reply_markup = _build_offer_detail_keyboard(updated_template, db_user.language)

    if edit_message_id:
        try:
            await message.bot.edit_message_text(
                description,
                chat_id=edit_chat_id,
                message_id=edit_message_id,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
        except TelegramBadRequest as exc:
            logger.warning("Не удалось обновить сообщение редактирования промо: %s", exc)
            await message.answer(description, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await message.answer(description, reply_markup=reply_markup, parse_mode="HTML")


@admin_required
@error_handler
async def show_send_segments(callback: CallbackQuery, db_user: User, db: AsyncSession):
    template_id = int(callback.data.split("_")[-1])
    template = await get_promo_offer_template_by_id(db, template_id)
    if not template:
        await callback.answer("❌ Предложение не найдено", show_alert=True)
        return

    await callback.message.edit_reply_markup(
        reply_markup=_build_send_keyboard(template, db_user.language)
    )
    await callback.answer()


@admin_required
@error_handler
async def send_offer_to_segment(callback: CallbackQuery, db_user: User, db: AsyncSession):
    try:
        prefix = "promo_offer_send_"
        if not callback.data.startswith(prefix):
            raise ValueError("invalid prefix")
        data = callback.data[len(prefix):]
        template_id_str, segment = data.split("_", 1)
        template_id = int(template_id_str)
    except (ValueError, AttributeError):
        await callback.answer("❌ Некорректные данные", show_alert=True)
        return

    template = await get_promo_offer_template_by_id(db, template_id)
    if not template:
        await callback.answer("❌ Предложение не найдено", show_alert=True)
        return

    config = OFFER_TYPE_CONFIG.get(template.offer_type, {})
    allowed_segments = {seg for seg, _ in config.get("allowed_segments", [])}
    if segment not in allowed_segments:
        await callback.answer("⚠️ Нельзя отправить это предложение выбранной категории", show_alert=True)
        return

    texts = get_texts(db_user.language)
    await callback.answer(texts.t("ADMIN_PROMO_OFFER_SENDING", "Начинаем рассылку..."), show_alert=True)

    users = await get_users_for_promo_segment(db, segment)
    if not users:
        await callback.message.answer(texts.t("ADMIN_PROMO_OFFER_NO_USERS", "Подходящих пользователей не найдено."))
        return

    sent = 0
    failed = 0
    effect_type = config.get("effect_type", "balance_bonus")

    for user in users:
        try:
            offer_record = await upsert_discount_offer(
                db,
                user_id=user.id,
                subscription_id=user.subscription.id if user.subscription else None,
                notification_type=f"promo_template_{template.id}",
                discount_percent=template.discount_percent,
                bonus_amount_kopeks=0,
                valid_hours=template.valid_hours,
                effect_type=effect_type,
                extra_data={
                    "template_id": template.id,
                    "offer_type": template.offer_type,
                    "test_duration_hours": template.test_duration_hours,
                    "test_squad_uuids": template.test_squad_uuids,
                },
            )

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=template.button_text, callback_data=f"claim_discount_{offer_record.id}")]
            ])

            message_text = _render_template_text(template, user.language or db_user.language)
            await callback.bot.send_message(
                chat_id=user.telegram_id,
                text=message_text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
            sent += 1
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            logger.warning("Не удалось отправить предложение пользователю %s: %s", user.telegram_id, exc)
            failed += 1
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.error("Ошибка рассылки промо предложения пользователю %s: %s", user.telegram_id, exc)
            failed += 1

    summary = texts.t(
        "ADMIN_PROMO_OFFER_RESULT",
        "📬 Рассылка завершена\nОтправлено: {sent}\nОшибок: {failed}",
    ).format(sent=sent, failed=failed)
    refreshed = await get_promo_offer_template_by_id(db, template.id)
    if refreshed:
        description = _describe_offer(refreshed, db_user.language)
        await callback.message.edit_text(
            description,
            reply_markup=_build_offer_detail_keyboard(refreshed, db_user.language),
            parse_mode="HTML",
        )
    await callback.message.answer(summary)


async def process_edit_message_text(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    await _handle_edit_field(message, state, db, db_user, "message_text")


async def process_edit_button_text(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    await _handle_edit_field(message, state, db, db_user, "button_text")


async def process_edit_valid_hours(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    await _handle_edit_field(message, state, db, db_user, "valid_hours")


async def process_edit_discount_percent(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    await _handle_edit_field(message, state, db, db_user, "discount_percent")


async def process_edit_test_duration(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    await _handle_edit_field(message, state, db, db_user, "test_duration_hours")


async def process_edit_test_squads(message: Message, state: FSMContext, db: AsyncSession, db_user: User):
    await _handle_edit_field(message, state, db, db_user, "test_squad_uuids")


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_promo_offers_menu, F.data == "admin_promo_offers")
    dp.callback_query.register(prompt_edit_message, F.data.startswith("promo_offer_edit_message_"))
    dp.callback_query.register(prompt_edit_button, F.data.startswith("promo_offer_edit_button_"))
    dp.callback_query.register(prompt_edit_valid, F.data.startswith("promo_offer_edit_valid_"))
    dp.callback_query.register(prompt_edit_discount, F.data.startswith("promo_offer_edit_discount_"))
    dp.callback_query.register(prompt_edit_duration, F.data.startswith("promo_offer_edit_duration_"))
    dp.callback_query.register(prompt_edit_squads, F.data.startswith("promo_offer_edit_squads_"))
    dp.callback_query.register(show_send_segments, F.data.startswith("promo_offer_send_menu_"))
    dp.callback_query.register(send_offer_to_segment, F.data.startswith("promo_offer_send_"))
    dp.callback_query.register(show_promo_offer_details, F.data.startswith("promo_offer_"))

    dp.message.register(process_edit_message_text, AdminStates.editing_promo_offer_message)
    dp.message.register(process_edit_button_text, AdminStates.editing_promo_offer_button)
    dp.message.register(process_edit_valid_hours, AdminStates.editing_promo_offer_valid_hours)
    dp.message.register(process_edit_discount_percent, AdminStates.editing_promo_offer_discount)
    dp.message.register(process_edit_test_duration, AdminStates.editing_promo_offer_test_duration)
    dp.message.register(process_edit_test_squads, AdminStates.editing_promo_offer_squads)
