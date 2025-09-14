import logging
from aiogram import Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta 

from app.config import settings
from app.database.crud.user import get_user_by_telegram_id, update_user
from app.keyboards.inline import get_main_menu_keyboard
from app.localization.texts import get_texts
from app.database.models import User
from app.utils.user_utils import mark_user_as_had_paid_subscription
from app.database.crud.user_message import get_random_active_message

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
    
    rules_text = await get_current_rules_content(db, db_user.language)
    
    if not rules_text:
        texts = get_texts(db_user.language)
        rules_text = texts._get_default_rules(db_user.language) if hasattr(texts, '_get_default_rules') else """
📋 <b>Правила использования сервиса</b>

1. Запрещается использование сервиса для незаконной деятельности
2. Запрещается нарушение авторских прав
3. Запрещается спам и рассылка вредоносного ПО
4. Запрещается использование сервиса для DDoS атак
5. Один аккаунт - один пользователь
6. Возврат средств производится только в исключительных случаях
7. Администрация оставляет за собой право заблокировать аккаунт при нарушении правил

<b>Принимая правила, вы соглашаетесь соблюдать их.</b>
"""
    
    await callback.message.edit_text(
        f"📋 <b>Правила сервиса</b>\n\n{rules_text}",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_menu")]
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
    
    await callback.message.edit_text(
        menu_text,
        reply_markup=get_main_menu_keyboard(
            language=db_user.language,
            is_admin=settings.is_admin(db_user.telegram_id),
            has_had_paid_subscription=db_user.has_had_paid_subscription,
            has_active_subscription=has_active_subscription,
            subscription_is_active=subscription_is_active,
            balance_kopeks=db_user.balance_kopeks,
            subscription=db_user.subscription
        ),
        parse_mode="HTML"
    )
    await callback.answer()


def _get_subscription_status(user: User, texts) -> str:
    if not user.subscription:
        return "❌ Отсутствует"
    
    subscription = user.subscription
    current_time = datetime.utcnow()
    
    if subscription.end_date <= current_time:
        return f"🔴 Истекла\n📅 {subscription.end_date.strftime('%d.%m.%Y')}"
    
    days_left = (subscription.end_date - current_time).days
    
    if subscription.is_trial:
        if days_left > 1:
            return f"🎁 Тестовая подписка\n📅 до {subscription.end_date.strftime('%d.%m.%Y')} ({days_left} дн.)"
        elif days_left == 1:
            return f"🎁 Тестовая подписка\n⚠️ истекает завтра!"
        else:
            return f"🎁 Тестовая подписка\n⚠️ истекает сегодня!"
    
    else: 
        if days_left > 7:
            return f"💎 Активна\n📅 до {subscription.end_date.strftime('%d.%m.%Y')} ({days_left} дн.)"
        elif days_left > 1:
            return f"💎 Активна\n⚠️ истекает через {days_left} дн."
        elif days_left == 1:
            return f"💎 Активна\n⚠️ истекает завтра!"
        else:
            return f"💎 Активна\n⚠️ истекает сегодня!"

async def get_main_menu_text(user, texts, db: AsyncSession):
    
    base_text = texts.MAIN_MENU.format(
        user_name=user.full_name,
        subscription_status=_get_subscription_status(user, texts)
    )
    
    try:
        random_message = await get_random_active_message(db)
        if random_message:
            if "Выберите действие:" in base_text:
                parts = base_text.split("Выберите действие:")
                if len(parts) == 2:
                    return f"{parts[0]}\n{random_message}\n\nВыберите действие:{parts[1]}"
            
            if "Выберите действие:" in base_text:
                return base_text.replace("Выберите действие:", f"\n{random_message}\n\nВыберите действие:")
            else:
                return f"{base_text}\n\n{random_message}"
                
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
