import logging
from aiogram import Dispatcher, types, F
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Subscription, SubscriptionStatus, User
from app.database.crud.subscription import reset_all_trial_subscriptions
from app.keyboards.admin import get_confirmation_keyboard
from app.localization.texts import get_texts
from app.utils.decorators import admin_required, error_handler

logger = logging.getLogger(__name__)


def _get_trials_menu_keyboard(texts):
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(
                text=texts.t("ADMIN_TRIALS_RESET_BUTTON", "♻️ Сбросить триалы всем"),
                callback_data="admin_trials_reset",
            )
        ],
        [types.InlineKeyboardButton(text=texts.BACK, callback_data="admin_submenu_users")],
    ])


def _get_trials_reset_result_keyboard(texts):
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(
                text=texts.t("ADMIN_TRIALS_BACK", "⬅️ К триалам"),
                callback_data="admin_trials",
            )
        ],
        [types.InlineKeyboardButton(text=texts.BACK, callback_data="admin_submenu_users")],
    ])


async def _get_trial_stats(db: AsyncSession) -> tuple[int, int]:
    total_result = await db.execute(
        select(func.count()).select_from(Subscription).where(Subscription.is_trial.is_(True))
    )
    total_trials = total_result.scalar_one() or 0

    active_result = await db.execute(
        select(func.count())
        .select_from(Subscription)
        .where(
            and_(
                Subscription.is_trial.is_(True),
                Subscription.status == SubscriptionStatus.ACTIVE.value,
            )
        )
    )
    active_trials = active_result.scalar_one() or 0

    return total_trials, active_trials


@admin_required
@error_handler
async def show_trials_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    total_trials, active_trials = await _get_trial_stats(db)

    message = texts.t(
        "ADMIN_TRIALS_TITLE",
        "🎁 <b>Триалы</b>",
    )
    message += "\n\n" + texts.t(
        "ADMIN_TRIALS_STATS",
        "Всего триальных подписок: <b>{total}</b>\n"
        "Активных сейчас: <b>{active}</b>\n\nВыберите действие:",
    ).format(total=total_trials, active=active_trials)

    await callback.message.edit_text(
        message,
        reply_markup=_get_trials_menu_keyboard(texts),
        parse_mode="HTML",
    )
    await callback.answer()


@admin_required
@error_handler
async def confirm_trials_reset(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)

    confirmation_text = texts.t(
        "ADMIN_TRIALS_RESET_CONFIRM",
        "⚠️ Сбросить все триалы для всех пользователей?\n\n"
        "Текущие тестовые подписки будут удалены.",
    )

    await callback.message.edit_text(
        confirmation_text,
        reply_markup=get_confirmation_keyboard(
            "admin_trials_reset_confirm",
            "admin_trials",
            db_user.language,
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def reset_trials(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)

    try:
        reset_count = await reset_all_trial_subscriptions(db)
    except Exception as error:
        logger.error("Ошибка массового сброса триалов: %s", error)
        await callback.message.edit_text(
            texts.t("ADMIN_TRIALS_RESET_ERROR", "❌ Не удалось сбросить триалы."),
            reply_markup=_get_trials_reset_result_keyboard(texts),
        )
        await callback.answer()
        return

    if reset_count:
        result_text = texts.t(
            "ADMIN_TRIALS_RESET_SUCCESS",
            "✅ Сброшено триалов: {count}",
        ).format(count=reset_count)
    else:
        result_text = texts.t(
            "ADMIN_TRIALS_RESET_EMPTY",
            "ℹ️ Триальные подписки не найдены.",
        )

    await callback.message.edit_text(
        result_text,
        reply_markup=_get_trials_reset_result_keyboard(texts),
    )
    await callback.answer()


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_trials_menu, F.data == "admin_trials")
    dp.callback_query.register(confirm_trials_reset, F.data == "admin_trials_reset")
    dp.callback_query.register(reset_trials, F.data == "admin_trials_reset_confirm")
