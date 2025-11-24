import logging
import datetime
from aiogram import Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.localization.texts import get_texts
from app.database.crud.referral import get_referral_statistics, get_user_referral_stats
from app.database.crud.user import get_user_by_id
from app.services.referral_withdrawal_service import ReferralWithdrawalService
from app.states import AdminStates
from app.utils.decorators import admin_required, error_handler

logger = logging.getLogger(__name__)


@admin_required
@error_handler
async def show_referral_statistics(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    try:
        stats = await get_referral_statistics(db)
        
        avg_per_referrer = 0
        if stats.get('active_referrers', 0) > 0:
            avg_per_referrer = stats.get('total_paid_kopeks', 0) / stats['active_referrers']
        
        current_time = datetime.datetime.now().strftime("%H:%M:%S")
        
        text = f"""
🤝 <b>Реферальная статистика</b>

<b>Общие показатели:</b>
- Пользователей с рефералами: {stats.get('users_with_referrals', 0)}
- Активных рефереров: {stats.get('active_referrers', 0)}
- Выплачено всего: {settings.format_price(stats.get('total_paid_kopeks', 0))}

<b>За период:</b>
- Сегодня: {settings.format_price(stats.get('today_earnings_kopeks', 0))}
- За неделю: {settings.format_price(stats.get('week_earnings_kopeks', 0))}
- За месяц: {settings.format_price(stats.get('month_earnings_kopeks', 0))}

<b>Средние показатели:</b>
- На одного реферера: {settings.format_price(int(avg_per_referrer))}

<b>Топ-5 рефереров:</b>
"""
        
        top_referrers = stats.get('top_referrers', [])
        if top_referrers:
            for i, referrer in enumerate(top_referrers[:5], 1):
                earned = referrer.get('total_earned_kopeks', 0)
                count = referrer.get('referrals_count', 0)
                user_id = referrer.get('user_id', 'N/A')
                
                if count > 0:
                    text += f"{i}. ID {user_id}: {settings.format_price(earned)} ({count} реф.)\n"
                else:
                    logger.warning(f"Реферер {user_id} имеет {count} рефералов, но есть в топе")
        else:
            text += "Нет данных\n"
        
        text += f"""

<b>Настройки реферальной системы:</b>
- Минимальное пополнение: {settings.format_price(settings.REFERRAL_MINIMUM_TOPUP_KOPEKS)}
- Бонус за первое пополнение: {settings.format_price(settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS)}
- Бонус пригласившему: {settings.format_price(settings.REFERRAL_INVITER_BONUS_KOPEKS)}
- Комиссия с покупок: {settings.REFERRAL_COMMISSION_PERCENT}%
- Уведомления: {'✅ Включены' if settings.REFERRAL_NOTIFICATIONS_ENABLED else '❌ Отключены'}

<i>🕐 Обновлено: {current_time}</i>
"""
        
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_referrals")],
            [types.InlineKeyboardButton(text="👥 Топ рефереров", callback_data="admin_referrals_top")],
            [
                types.InlineKeyboardButton(
                    text=texts.t("ADMIN_REFERRAL_WITHDRAWALS", "💸 Вывод партнёрки"),
                    callback_data="admin_referral_withdrawals",
                )
            ],
            [types.InlineKeyboardButton(text="⚙️ Настройки", callback_data="admin_referrals_settings")],
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")]
        ])
        
        try:
            await callback.message.edit_text(text, reply_markup=keyboard)
            await callback.answer("Обновлено")
        except Exception as edit_error:
            if "message is not modified" in str(edit_error):
                await callback.answer("Данные актуальны")
            else:
                logger.error(f"Ошибка редактирования сообщения: {edit_error}")
                await callback.answer("Ошибка обновления")
        
    except Exception as e:
        logger.error(f"Ошибка в show_referral_statistics: {e}", exc_info=True)
        
        current_time = datetime.datetime.now().strftime("%H:%M:%S")
        text = f"""
🤝 <b>Реферальная статистика</b>

❌ <b>Ошибка загрузки данных</b>

<b>Текущие настройки:</b>
- Минимальное пополнение: {settings.format_price(settings.REFERRAL_MINIMUM_TOPUP_KOPEKS)}
- Бонус за первое пополнение: {settings.format_price(settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS)}
- Бонус пригласившему: {settings.format_price(settings.REFERRAL_INVITER_BONUS_KOPEKS)}
- Комиссия с покупок: {settings.REFERRAL_COMMISSION_PERCENT}%

<i>🕐 Время: {current_time}</i>
"""
        
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🔄 Повторить", callback_data="admin_referrals")],
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")]
        ])
        
        try:
            await callback.message.edit_text(text, reply_markup=keyboard)
        except:
            pass
        await callback.answer("Произошла ошибка при загрузке статистики")


@admin_required
@error_handler
async def show_top_referrers(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    try:
        stats = await get_referral_statistics(db)
        top_referrers = stats.get('top_referrers', [])
        
        text = "🏆 <b>Топ рефереров</b>\n\n"
        
        if top_referrers:
            for i, referrer in enumerate(top_referrers[:20], 1): 
                earned = referrer.get('total_earned_kopeks', 0)
                count = referrer.get('referrals_count', 0)
                display_name = referrer.get('display_name', 'N/A')
                username = referrer.get('username', '')
                telegram_id = referrer.get('telegram_id', 'N/A')
                
                if username:
                    display_text = f"@{username} (ID{telegram_id})"
                elif display_name and display_name != f"ID{telegram_id}":
                    display_text = f"{display_name} (ID{telegram_id})"
                else:
                    display_text = f"ID{telegram_id}"
                
                emoji = ""
                if i == 1:
                    emoji = "🥇 "
                elif i == 2:
                    emoji = "🥈 "
                elif i == 3:
                    emoji = "🥉 "
                
                text += f"{emoji}{i}. {display_text}\n"
                text += f"   💰 {settings.format_price(earned)} | 👥 {count} реф.\n\n"
        else:
            text += "Нет данных о рефererах\n"
        
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="⬅️ К статистике", callback_data="admin_referrals")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard)
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка в show_top_referrers: {e}", exc_info=True)
        await callback.answer("Ошибка загрузки топа рефереров")


@admin_required
@error_handler
async def show_referral_settings(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    text = f"""
⚙️ <b>Настройки реферальной системы</b>

<b>Бонусы и награды:</b>
• Минимальная сумма пополнения для участия: {settings.format_price(settings.REFERRAL_MINIMUM_TOPUP_KOPEKS)}
• Бонус за первое пополнение реферала: {settings.format_price(settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS)}
• Бонус пригласившему за первое пополнение: {settings.format_price(settings.REFERRAL_INVITER_BONUS_KOPEKS)}

<b>Комиссионные:</b>
• Процент с каждой покупки реферала: {settings.REFERRAL_COMMISSION_PERCENT}%

<b>Уведомления:</b>
• Статус: {'✅ Включены' if settings.REFERRAL_NOTIFICATIONS_ENABLED else '❌ Отключены'}
• Попытки отправки: {getattr(settings, 'REFERRAL_NOTIFICATION_RETRY_ATTEMPTS', 3)}

<i>💡 Для изменения настроек отредактируйте файл .env и перезапустите бота</i>
"""
    
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="⬅️ К статистике", callback_data="admin_referrals")]
    ])

    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@admin_required
@error_handler
async def show_referral_withdrawals_settings(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    settings_obj = await ReferralWithdrawalService.get_settings(db)
    pending_requests = await ReferralWithdrawalService.list_requests(
        db, status="pending", limit=200
    )

    text = (
        texts.t("ADMIN_REFERRAL_WITHDRAWALS_TITLE", "💸 <b>Вывод партнёрки</b>")
        + "\n\n"
        + texts.t(
            "ADMIN_REFERRAL_WITHDRAWALS_STATUS",
            "Статус: {status}",
        ).format(status="✅ Включен" if settings_obj.enabled else "❌ Выключен")
        + "\n"
        + texts.t(
            "ADMIN_REFERRAL_WITHDRAWALS_MIN_AMOUNT",
            "Минимальная сумма: {amount}",
        ).format(amount=settings.format_price(settings_obj.min_amount_kopeks))
        + "\n"
        + texts.t(
            "ADMIN_REFERRAL_WITHDRAWALS_PENDING",
            "Заявок в ожидании: {count}",
        ).format(count=len(pending_requests))
        + "\n\n"
        + texts.t(
            "ADMIN_REFERRAL_WITHDRAWALS_PROMPT_TEXT",
            "Текст запроса:\n{prompt}",
        ).format(prompt=settings_obj.prompt_text)
        + "\n\n"
        + texts.t(
            "ADMIN_REFERRAL_WITHDRAWALS_SUCCESS_TEXT",
            "Текст подтверждения:\n{success}",
        ).format(success=settings_obj.success_text)
    )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=("⏸️ Выключить" if settings_obj.enabled else "▶️ Включить"),
                    callback_data="admin_referral_withdrawals_toggle",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="✏️ Минимальная сумма",
                    callback_data="admin_referral_withdrawals_min",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="✏️ Текст запроса",
                    callback_data="admin_referral_withdrawals_prompt",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text="✏️ Текст подтверждения",
                    callback_data="admin_referral_withdrawals_success",
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=texts.t("ADMIN_REFERRAL_WITHDRAWALS", "💸 Вывод партнёрки"),
                    callback_data="admin_referral_withdrawal_requests",
                )
            ],
            [types.InlineKeyboardButton(text=texts.BACK, callback_data="admin_referrals")],
        ]
    )

    await state.set_state(None)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@admin_required
@error_handler
async def toggle_referral_withdrawals(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    current_settings = await ReferralWithdrawalService.get_settings(db)
    await ReferralWithdrawalService.set_enabled(db, not current_settings.enabled)
    await callback.answer("Изменено")
    await show_referral_withdrawals_settings(callback, db_user, db, state)


@admin_required
@error_handler
async def prompt_referral_withdraw_min_amount(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    await state.set_state(AdminStates.editing_referral_withdraw_min_amount)
    await callback.message.edit_text(
        texts.t(
            "ADMIN_REFERRAL_WITHDRAWALS_MIN_PROMPT",
            "Введите минимальную сумму для вывода (в рублях):",
        ),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text=texts.BACK, callback_data="admin_referral_withdrawals")]]
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def prompt_referral_withdraw_prompt_text(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    await state.set_state(AdminStates.editing_referral_withdraw_prompt_text)
    await callback.message.edit_text(
        texts.t(
            "ADMIN_REFERRAL_WITHDRAWALS_PROMPT_EDIT",
            "Отправьте текст, который увидит пользователь при запросе вывода."
            "\nДоступные плейсхолдеры: {available}, {min_amount}.",
        ),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text=texts.BACK, callback_data="admin_referral_withdrawals")]]
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def prompt_referral_withdraw_success_text(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    await state.set_state(AdminStates.editing_referral_withdraw_success_text)
    await callback.message.edit_text(
        texts.t(
            "ADMIN_REFERRAL_WITHDRAWALS_SUCCESS_EDIT",
            "Отправьте текст подтверждения после создания заявки."
            "\nДоступные плейсхолдеры: {amount}, {available}.",
        ),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text=texts.BACK, callback_data="admin_referral_withdrawals")]]
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def handle_referral_withdraw_min_amount(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    amount_kopeks = ReferralWithdrawalService.parse_amount_to_kopeks(message.text or "")
    if amount_kopeks is None:
        await message.answer(
            texts.t(
                "ADMIN_REFERRAL_WITHDRAWALS_MIN_INVALID",
                "Некорректная сумма. Введите число, например 500 или 750.50",
            )
        )
        return

    await ReferralWithdrawalService.set_min_amount(db, amount_kopeks)
    await state.set_state(None)
    await message.answer(
        texts.t("ADMIN_REFERRAL_WITHDRAWALS_MIN_SAVED", "Минимум сохранён."),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text=texts.BACK, callback_data="admin_referral_withdrawals")]]
        ),
    )


@admin_required
@error_handler
async def handle_referral_withdraw_prompt_text(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    await ReferralWithdrawalService.set_prompt_text(db, message.text or "")
    await state.set_state(None)
    await message.answer(
        "Текст сохранён.",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text=get_texts(db_user.language).BACK, callback_data="admin_referral_withdrawals")]]
        ),
    )


@admin_required
@error_handler
async def handle_referral_withdraw_success_text(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    await ReferralWithdrawalService.set_success_text(db, message.text or "")
    await state.set_state(None)
    await message.answer(
        "Текст сохранён.",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text=get_texts(db_user.language).BACK, callback_data="admin_referral_withdrawals")]]
        ),
    )


@admin_required
@error_handler
async def show_referral_withdrawal_requests(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    requests = await ReferralWithdrawalService.list_requests(db, limit=50)

    if not requests:
        await callback.message.edit_text(
            texts.t("ADMIN_REFERRAL_WITHDRAWALS_EMPTY", "Нет заявок на вывод."),
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text=texts.BACK, callback_data="admin_referral_withdrawals")]]
            ),
        )
        await callback.answer()
        return

    keyboard = []
    for request in requests:
        user_display = request.user.full_name if request.user else f"ID{request.user_id}"
        button_text = f"#{request.id} | {settings.format_price(request.amount_kopeks)} | {user_display}"
        keyboard.append(
            [
                types.InlineKeyboardButton(
                    text=button_text,
                    callback_data=f"admin_referral_withdrawal_{request.id}",
                )
            ]
        )

    keyboard.append(
        [types.InlineKeyboardButton(text=texts.BACK, callback_data="admin_referral_withdrawals")]
    )

    await callback.message.edit_text(
        texts.t(
            "ADMIN_REFERRAL_WITHDRAWALS_LIST_TITLE",
            "📨 Заявки на вывод партнёрки",
        ),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
    )
    await callback.answer()


@admin_required
@error_handler
async def show_referral_withdrawal_request(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    request_id = int(callback.data.split("_")[-1])
    request = await ReferralWithdrawalService.get_request(db, request_id)

    if not request:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    user = request.user
    user_info = (
        f"{user.full_name}\n@{user.username}" if user and user.username else (user.full_name if user else "—")
    )
    text = (
        f"💸 <b>Заявка #{request.id}</b>\n\n"
        f"👤 {user_info}\n"
        f"🆔 {user.telegram_id if user else request.user_id}\n"
        f"💰 Сумма: {settings.format_price(request.amount_kopeks)}\n"
        f"📅 Создана: {request.created_at.strftime('%d.%m.%Y %H:%M')}\n"
        f"📌 Статус: {request.status}\n\n"
        f"🧾 Реквизиты:\n{request.requisites}"
    )

    if request.status == "closed" and request.closed_at:
        text += f"\n\n✅ Закрыта: {request.closed_at.strftime('%d.%m.%Y %H:%M')}"
        if request.closed_by:
            text += f"\n👮‍♂️ Закрыл: {request.closed_by.full_name}"

    keyboard = []
    if request.status != "closed":
        keyboard.append(
            [
                types.InlineKeyboardButton(
                    text=texts.t(
                        "ADMIN_REFERRAL_WITHDRAWALS_CLOSE", "✅ Закрыть заявку"
                    ),
                    callback_data=f"admin_referral_withdrawal_close_{request.id}",
                )
            ]
        )
    keyboard.append(
        [types.InlineKeyboardButton(text=texts.BACK, callback_data="admin_referral_withdrawal_requests")]
    )

    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
    )
    await callback.answer()


@admin_required
@error_handler
async def close_referral_withdrawal_request(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    request_id = int(callback.data.split("_")[-1])
    closed = await ReferralWithdrawalService.close_request(db, request_id, db_user.id)
    if not closed:
        await callback.answer("Не удалось закрыть заявку", show_alert=True)
        return
    await callback.answer("Заявка закрыта")
    await show_referral_withdrawal_request(callback, db_user, db)


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_referral_statistics, F.data == "admin_referrals")
    dp.callback_query.register(show_top_referrers, F.data == "admin_referrals_top")
    dp.callback_query.register(show_referral_settings, F.data == "admin_referrals_settings")
    dp.callback_query.register(
        show_referral_withdrawals_settings,
        F.data == "admin_referral_withdrawals",
    )
    dp.callback_query.register(
        toggle_referral_withdrawals, F.data == "admin_referral_withdrawals_toggle"
    )
    dp.callback_query.register(
        prompt_referral_withdraw_min_amount,
        F.data == "admin_referral_withdrawals_min",
    )
    dp.callback_query.register(
        prompt_referral_withdraw_prompt_text,
        F.data == "admin_referral_withdrawals_prompt",
    )
    dp.callback_query.register(
        prompt_referral_withdraw_success_text,
        F.data == "admin_referral_withdrawals_success",
    )
    dp.callback_query.register(
        show_referral_withdrawal_requests,
        F.data == "admin_referral_withdrawal_requests",
    )
    dp.callback_query.register(
        close_referral_withdrawal_request,
        F.data.startswith("admin_referral_withdrawal_close_"),
    )
    dp.callback_query.register(
        show_referral_withdrawal_request,
        F.data.startswith("admin_referral_withdrawal_"),
    )
    dp.message.register(
        handle_referral_withdraw_min_amount,
        AdminStates.editing_referral_withdraw_min_amount,
    )
    dp.message.register(
        handle_referral_withdraw_prompt_text,
        AdminStates.editing_referral_withdraw_prompt_text,
    )
    dp.message.register(
        handle_referral_withdraw_success_text,
        AdminStates.editing_referral_withdraw_success_text,
    )
