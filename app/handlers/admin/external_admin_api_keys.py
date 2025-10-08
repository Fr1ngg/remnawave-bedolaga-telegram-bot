from datetime import datetime
from typing import Iterable, Tuple

from aiogram import Dispatcher, F, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.localization.texts import get_texts
from app.services.external_admin_api_keys_service import (
    ExternalAdminApiKeysService,
    ExternalAdminTokenMissingError,
)
from app.states import ExternalAdminApiKeysStates
from app.utils.decorators import admin_required, error_handler


service = ExternalAdminApiKeysService()


def _format_datetime(value: datetime | None) -> str:
    if not value:
        return "—"
    try:
        return value.strftime("%d.%m.%Y %H:%M")
    except Exception:  # pragma: no cover - defensive
        return value.isoformat()


def _build_api_keys_view(
    *,
    texts,
    keys: Iterable,
) -> Tuple[str, types.InlineKeyboardMarkup]:
    keys_list = list(keys)
    lines: list[str] = [
        texts.t("ADMIN_API_KEYS_TITLE", "🔐 <b>API ключи внешней админки</b>"),
        "",
        texts.t(
            "ADMIN_API_KEYS_DESCRIPTION",
            (
                "Эти ключи используют внешние сервисы для проверки запросов.\n"
                "Ключ рассчитывается на основе Telegram ID и общего токена."
            ),
        ),
        "",
    ]

    if not keys_list:
        lines.append(
            texts.t(
                "ADMIN_API_KEYS_EMPTY",
                "Пока нет созданных ключей. Нажмите кнопку ниже, чтобы добавить первый.",
            )
        )
    else:
        for item in keys_list:
            api_key_value = service.build_api_key_value(item.target_telegram_id)
            created_at = _format_datetime(getattr(item, "created_at", None))
            lines.append(
                texts.t(
                    "ADMIN_API_KEYS_ITEM",
                    "• <code>{telegram_id}</code> → <code>{api_key}</code> (создан {created_at})",
                ).format(
                    telegram_id=item.target_telegram_id,
                    api_key=api_key_value,
                    created_at=created_at,
                )
            )

    keyboard_rows: list[list[types.InlineKeyboardButton]] = [
        [
            types.InlineKeyboardButton(
                text=texts.t("ADMIN_API_KEYS_CREATE_BUTTON", "➕ Создать ключ"),
                callback_data="admin_api_keys_create",
            )
        ]
    ]

    if keys_list:
        for item in keys_list:
            keyboard_rows.append(
                [
                    types.InlineKeyboardButton(
                        text=texts.t(
                            "ADMIN_API_KEYS_DELETE_BUTTON",
                            "🗑 Удалить {telegram_id}",
                        ).format(telegram_id=item.target_telegram_id),
                        callback_data=f"admin_api_keys_delete_{item.id}",
                    )
                ]
            )

    keyboard_rows.append([
        types.InlineKeyboardButton(text=texts.BACK, callback_data="admin_submenu_settings"),
    ])

    return "\n".join(lines), types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


def _token_missing_markup(texts) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.BACK,
                    callback_data="admin_submenu_settings",
                )
            ]
        ]
    )


@admin_required
@error_handler
async def show_api_keys(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    await state.clear()

    try:
        keys = await service.list_for_creator(db, db_user.id)
        text, keyboard = _build_api_keys_view(texts=texts, keys=keys)
    except ExternalAdminTokenMissingError:
        text = texts.t(
            "ADMIN_API_KEYS_TOKEN_MISSING",
            (
                "⚠️ Внешняя админка не настроена.\n\n"
                "Сначала сгенерируйте EXTERNAL_ADMIN_TOKEN в разделе конфигурации бота."
            ),
        )
        keyboard = _token_missing_markup(texts)

    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@admin_required
@error_handler
async def prompt_create_api_key(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)

    try:
        await service.list_for_creator(db, db_user.id)
    except ExternalAdminTokenMissingError:
        await callback.answer(
            texts.t(
                "ADMIN_API_KEYS_TOKEN_MISSING_SHORT",
                "⚠️ Сначала настройте внешний токен в конфигурации бота.",
            ),
            show_alert=True,
        )
        return

    await state.set_state(ExternalAdminApiKeysStates.waiting_for_target_id)
    await callback.message.edit_text(
        texts.t(
            "ADMIN_API_KEYS_PROMPT",
            (
                "✏️ <b>Создание API-ключа</b>\n\n"
                "Отправьте Telegram ID пользователя, для которого требуется ключ.\n"
                "Можно указать свой ID или ID другого администратора.\n\n"
                "Отправьте <code>отмена</code>, чтобы вернуться без изменений."
            ),
        ),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text=texts.BACK, callback_data="admin_api_keys")]]
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def delete_api_key(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)

    try:
        key_id = int(callback.data.split("_")[-1])
    except (IndexError, ValueError):
        await callback.answer(texts.t("ADMIN_API_KEYS_DELETE_ERROR", "❌ Не удалось удалить ключ."), show_alert=True)
        return

    try:
        deleted = await service.delete_key(db, key_id=key_id, creator_user_id=db_user.id)
        if deleted:
            await db.commit()
            await callback.answer(
                texts.t("ADMIN_API_KEYS_DELETED", "🗑 Ключ удален."),
            )
        else:
            await callback.answer(
                texts.t("ADMIN_API_KEYS_NOT_FOUND", "⚠️ Ключ не найден или принадлежит другому администратору."),
                show_alert=True,
            )
            return
    except ExternalAdminTokenMissingError:
        await callback.answer(
            texts.t(
                "ADMIN_API_KEYS_TOKEN_MISSING_SHORT",
                "⚠️ Сначала настройте внешний токен в конфигурации бота.",
            ),
            show_alert=True,
        )
        return

    keys = await service.list_for_creator(db, db_user.id)
    text, keyboard = _build_api_keys_view(texts=texts, keys=keys)
    await callback.message.edit_text(text, reply_markup=keyboard)


@admin_required
@error_handler
async def process_api_key_input(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    raw_value = (message.text or "").strip()

    if raw_value.lower() in {"отмена", "cancel", "/cancel"}:
        await state.clear()
        keys = await service.list_for_creator(db, db_user.id)
        text, keyboard = _build_api_keys_view(texts=texts, keys=keys)
        await message.answer(
            texts.t("ADMIN_API_KEYS_CANCELLED", "❌ Создание ключа отменено."),
        )
        await message.answer(text, reply_markup=keyboard)
        return

    try:
        target_id = int(raw_value)
    except ValueError:
        await message.answer(
            texts.t(
                "ADMIN_API_KEYS_INVALID_ID",
                "❌ Укажите корректный Telegram ID — только цифры без пробелов.",
            )
        )
        return

    if target_id <= 0:
        await message.answer(
            texts.t(
                "ADMIN_API_KEYS_INVALID_ID",
                "❌ Укажите корректный Telegram ID — только цифры без пробелов.",
            )
        )
        return

    try:
        result = await service.ensure_key(
            db,
            creator_user_id=db_user.id,
            target_telegram_id=target_id,
        )
        await db.commit()
    except ExternalAdminTokenMissingError:
        await message.answer(
            texts.t(
                "ADMIN_API_KEYS_TOKEN_MISSING",
                "⚠️ Внешняя админка не настроена. Сначала создайте общий токен.",
            )
        )
        await state.clear()
        return

    api_key_value = service.build_api_key_value(target_id)
    if result.created:
        response_text = texts.t(
            "ADMIN_API_KEYS_CREATED",
            "✅ Ключ для <code>{telegram_id}</code> создан.\n\n<code>{api_key}</code>",
        ).format(telegram_id=target_id, api_key=api_key_value)
    else:
        response_text = texts.t(
            "ADMIN_API_KEYS_EXISTS",
            "ℹ️ Ключ для <code>{telegram_id}</code> уже существует.\n\n<code>{api_key}</code>",
        ).format(telegram_id=target_id, api_key=api_key_value)

    await message.answer(response_text)

    keys = await service.list_for_creator(db, db_user.id)
    text, keyboard = _build_api_keys_view(texts=texts, keys=keys)
    await message.answer(text, reply_markup=keyboard)
    await state.clear()


def register_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(
        show_api_keys,
        F.data == "admin_api_keys",
        StateFilter(None),
    )
    dp.callback_query.register(
        show_api_keys,
        F.data == "admin_api_keys",
        ExternalAdminApiKeysStates.waiting_for_target_id,
    )
    dp.callback_query.register(
        prompt_create_api_key,
        F.data == "admin_api_keys_create",
        StateFilter(None),
    )
    dp.callback_query.register(
        delete_api_key,
        F.data.startswith("admin_api_keys_delete_"),
        StateFilter(None),
    )
    dp.message.register(
        process_api_key_input,
        ExternalAdminApiKeysStates.waiting_for_target_id,
    )
