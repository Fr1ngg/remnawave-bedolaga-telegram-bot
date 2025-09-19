import logging
from aiogram import Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta 

from app.config import settings
from app.database.crud.user import get_user_by_telegram_id, update_user
from app.keyboards.inline import get_main_menu_keyboard
from app.localization.texts import get_texts, get_rules_text
from app.database.models import User
from app.utils.user_utils import mark_user_as_had_paid_subscription
from app.database.crud.user_message import get_random_active_message
from app.services.subscription_checkout_service import (
    has_subscription_checkout_draft,
    should_offer_checkout_resume,
)

logger = logging.getLogger(__name__)


async def show_main_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    texts = get_texts(db_user.language)

    from datetime import datetime
    db_user.last_activity = datetime.utcnow()
    await db.commit()

    has_active_subscription = bool(db_user.subscription)
    subscription_is_active = False

    if db_user.subscription:
        subscription_is_active = db_user.subscription.is_active

    menu_text = await get_main_menu_text(db_user, texts, db)

    draft_exists = await has_subscription_checkout_draft(db_user.id)
    show_resume_checkout = should_offer_checkout_resume(db_user, draft_exists)

    await callback.message.edit_text(
        menu_text,
        reply_markup=get_main_menu_keyboard(
            language=db_user.language,
            is_admin=settings.is_admin(db_user.telegram_id),
            has_had_paid_subscription=db_user.has_had_paid_subscription,
            has_active_subscription=has_active_subscription,
            subscription_is_active=subscription_is_active,
            balance_kopeks=db_user.balance_kopeks,
            subscription=db_user.subscription,
            show_resume_checkout=show_resume_checkout,
        ),
        parse_mode="HTML"
    )
    await callback.answer()


async def mark_user_as_had_paid_subscription(
    db: AsyncSession,
    user: User
) -> None:
    if not user.has_had_paid_subscription:
        user.has_had_paid_subscription = True
        user.updated_at = datetime.utcnow()
        await db.commit()
        logger.info(f"🎯 Пользователь {user.telegram_id} отмечен как имевший платную подписку")


async def show_service_rules(
    callback: types.CallbackQuery, 
    db_user: User, 
    db: AsyncSession
):
    from app.database.crud.rules import get_current_rules_content

    texts = get_texts(db_user.language)
    rules_text = await get_current_rules_content(db, db_user.language)

    if not rules_text:
        rules_text = await get_rules_text(db_user.language)

    await callback.message.edit_text(
        f"{texts.t('RULES_HEADER', '📋 <b>Правила сервиса</b>')}\n\n{rules_text}",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")]
        ])
    )
    await callback.answer()


async def handle_back_to_menu(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession
):
    await state.clear()

    texts = get_texts(db_user.language)

    has_active_subscription = db_user.subscription is not None
    subscription_is_active = False

    if db_user.subscription:
        subscription_is_active = db_user.subscription.is_active

    menu_text = await get_main_menu_text(db_user, texts, db)

    draft_exists = await has_subscription_checkout_draft(db_user.id)
    show_resume_checkout = should_offer_checkout_resume(db_user, draft_exists)

    await callback.message.edit_text(
        menu_text,
        reply_markup=get_main_menu_keyboard(
            language=db_user.language,
            is_admin=settings.is_admin(db_user.telegram_id),
            has_had_paid_subscription=db_user.has_had_paid_subscription,
            has_active_subscription=has_active_subscription,
            subscription_is_active=subscription_is_active,
            balance_kopeks=db_user.balance_kopeks,
            subscription=db_user.subscription,
            show_resume_checkout=show_resume_checkout,
        ),
        parse_mode="HTML"
    )
    await callback.answer()

def _get_subscription_status(user: User, texts) -> str:
    if not user.subscription:
        return texts.t("SUB_STATUS_NONE", "❌ Отсутствует")
    
    subscription = user.subscription
    current_time = datetime.utcnow()
    
    if subscription.end_date <= current_time:
        return texts.t(
            "SUB_STATUS_EXPIRED",
            "🔴 Истекла\n📅 {end_date}",
        ).format(end_date=subscription.end_date.strftime('%d.%m.%Y'))
    
    days_left = (subscription.end_date - current_time).days
    
    if subscription.is_trial:
        if days_left > 1:
            return texts.t(
                "SUB_STATUS_TRIAL_ACTIVE",
                "🎁 Тестовая подписка\n📅 до {end_date} ({days} дн.)",
            ).format(
                end_date=subscription.end_date.strftime('%d.%m.%Y'),
                days=days_left,
            )
        elif days_left == 1:
            return texts.t(
                "SUB_STATUS_TRIAL_TOMORROW",
                "🎁 Тестовая подписка\n⚠️ истекает завтра!",
            )
        else:
            return texts.t(
                "SUB_STATUS_TRIAL_TODAY",
                "🎁 Тестовая подписка\n⚠️ истекает сегодня!",
            )

    else: 
        if days_left > 7:
            return texts.t(
                "SUB_STATUS_ACTIVE_LONG",
                "💎 Активна\n📅 до {end_date} ({days} дн.)",
            ).format(
                end_date=subscription.end_date.strftime('%d.%m.%Y'),
                days=days_left,
            )
        elif days_left > 1:
            return texts.t(
                "SUB_STATUS_ACTIVE_FEW_DAYS",
                "💎 Активна\n⚠️ истекает через {days} дн.",
            ).format(days=days_left)
        elif days_left == 1:
            return texts.t(
                "SUB_STATUS_ACTIVE_TOMORROW",
                "💎 Активна\n⚠️ истекает завтра!",
            )
        else:
            return texts.t(
                "SUB_STATUS_ACTIVE_TODAY",
                "💎 Активна\n⚠️ истекает сегодня!",
            )


def _insert_random_message(base_text: str, random_message: str, action_prompt: str) -> str:
    if not random_message:
        return base_text

    prompt = action_prompt or ""
    if prompt and prompt in base_text:
        parts = base_text.split(prompt, 1)
        if len(parts) == 2:
            return f"{parts[0]}\n{random_message}\n\n{prompt}{parts[1]}"
        return base_text.replace(prompt, f"\n{random_message}\n\n{prompt}", 1)

    return f"{base_text}\n\n{random_message}"


async def get_main_menu_text(user, texts, db: AsyncSession):

    base_text = texts.MAIN_MENU.format(
        user_name=user.full_name,
        subscription_status=_get_subscription_status(user, texts)
    )
    
    action_prompt = texts.t("MAIN_MENU_ACTION_PROMPT", "Выберите действие:")

    try:
        random_message = await get_random_active_message(db)
        if random_message:
            return _insert_random_message(base_text, random_message, action_prompt)
                
    except Exception as e:
        logger.error(f"Ошибка получения случайного сообщения: {e}")
    
    return base_text


def register_handlers(dp: Dispatcher):
    
    dp.callback_query.register(
        handle_back_to_menu,
        F.data == "back_to_menu"
    )
    
    dp.callback_query.register(
        show_service_rules,
        F.data == "menu_rules"
    )
