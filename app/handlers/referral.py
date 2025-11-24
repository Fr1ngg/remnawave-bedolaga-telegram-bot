import logging
from pathlib import Path

import qrcode
from aiogram import Dispatcher, F, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.keyboards.inline import get_referral_keyboard
from app.localization.texts import get_texts
from app.services.referral_withdrawal_service import ReferralWithdrawalService
from app.states import ReferralWithdrawalStates
from app.utils.photo_message import edit_or_answer_photo
from app.utils.user_utils import (
    get_detailed_referral_list,
    get_effective_referral_commission_percent,
    get_referral_analytics,
    get_user_referral_summary,
)

logger = logging.getLogger(__name__)


async def show_referral_info(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    texts = get_texts(db_user.language)

    summary = await get_user_referral_summary(db, db_user.id)
    withdrawal_settings = await ReferralWithdrawalService.get_settings(db)
    
    bot_username = (await callback.bot.get_me()).username
    referral_link = f"https://t.me/{bot_username}?start={db_user.referral_code}"
    
    referral_text = (
        texts.t("REFERRAL_PROGRAM_TITLE", "👥 <b>Реферальная программа</b>")
        + "\n\n"
        + texts.t("REFERRAL_STATS_HEADER", "📊 <b>Ваша статистика:</b>")
        + "\n"
        + texts.t(
            "REFERRAL_STATS_INVITED",
            "• Приглашено пользователей: <b>{count}</b>",
        ).format(count=summary['invited_count'])
        + "\n"
        + texts.t(
            "REFERRAL_STATS_FIRST_TOPUPS",
            "• Сделали первое пополнение: <b>{count}</b>",
        ).format(count=summary['paid_referrals_count'])
        + "\n"
        + texts.t(
            "REFERRAL_STATS_ACTIVE",
            "• Активных рефералов: <b>{count}</b>",
        ).format(count=summary['active_referrals_count'])
        + "\n"
        + texts.t(
            "REFERRAL_STATS_CONVERSION",
            "• Конверсия: <b>{rate}%</b>",
        ).format(rate=summary['conversion_rate'])
        + "\n"
        + texts.t(
            "REFERRAL_STATS_TOTAL_EARNED",
            "• Заработано всего: <b>{amount}</b>",
        ).format(amount=texts.format_price(summary['total_earned_kopeks']))
        + "\n"
        + texts.t(
            "REFERRAL_STATS_MONTH_EARNED",
            "• За последний месяц: <b>{amount}</b>",
        ).format(amount=texts.format_price(summary['month_earned_kopeks']))
        + "\n\n"
        + texts.t("REFERRAL_REWARDS_HEADER", "🎁 <b>Как работают награды:</b>")
        + "\n"
        + texts.t(
            "REFERRAL_REWARD_NEW_USER",
            "• Новый пользователь получает: <b>{bonus}</b> при первом пополнении от <b>{minimum}</b>",
        ).format(
            bonus=texts.format_price(settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS),
            minimum=texts.format_price(settings.REFERRAL_MINIMUM_TOPUP_KOPEKS),
        )
        + "\n"
        + texts.t(
            "REFERRAL_REWARD_INVITER",
            "• Вы получаете при первом пополнении реферала: <b>{bonus}</b>",
        ).format(bonus=texts.format_price(settings.REFERRAL_INVITER_BONUS_KOPEKS))
        + "\n"
        + texts.t(
            "REFERRAL_REWARD_COMMISSION",
            "• Комиссия с каждого пополнения реферала: <b>{percent}%</b>",
        ).format(percent=get_effective_referral_commission_percent(db_user))
        + "\n\n"
        + texts.t("REFERRAL_LINK_TITLE", "🔗 <b>Ваша реферальная ссылка:</b>")
        + f"\n<code>{referral_link}</code>\n\n"
        + texts.t("REFERRAL_CODE_TITLE", "🆔 <b>Ваш код:</b> <code>{code}</code>").format(code=db_user.referral_code)
        + "\n\n"
    )

    if summary['recent_earnings']:
        meaningful_earnings = [
            earning for earning in summary['recent_earnings'][:5]
            if earning['amount_kopeks'] > 0
        ]

        if meaningful_earnings:
            referral_text += texts.t(
                "REFERRAL_RECENT_EARNINGS_HEADER",
                "💰 <b>Последние начисления:</b>",
            ) + "\n"
            for earning in meaningful_earnings[:3]:
                reason_text = {
                    "referral_first_topup": texts.t(
                        "REFERRAL_EARNING_REASON_FIRST_TOPUP",
                        "🎉 Первое пополнение",
                    ),
                    "referral_commission_topup": texts.t(
                        "REFERRAL_EARNING_REASON_COMMISSION_TOPUP",
                        "💰 Комиссия с пополнения",
                    ),
                    "referral_commission": texts.t(
                        "REFERRAL_EARNING_REASON_COMMISSION_PURCHASE",
                        "💰 Комиссия с покупки",
                    ),
                }.get(earning['reason'], earning['reason'])

                referral_text += texts.t(
                    "REFERRAL_RECENT_EARNINGS_ITEM",
                    "• {reason}: <b>{amount}</b> от {referral_name}",
                ).format(
                    reason=reason_text,
                    amount=texts.format_price(earning['amount_kopeks']),
                    referral_name=earning['referral_name'],
                ) + "\n"
            referral_text += "\n"

    if summary['earnings_by_type']:
        referral_text += texts.t(
            "REFERRAL_EARNINGS_BY_TYPE_HEADER",
            "📈 <b>Доходы по типам:</b>",
        ) + "\n"

        if 'referral_first_topup' in summary['earnings_by_type']:
            data = summary['earnings_by_type']['referral_first_topup']
            if data['total_amount_kopeks'] > 0:
                referral_text += texts.t(
                    "REFERRAL_EARNINGS_FIRST_TOPUPS",
                    "• Бонусы за первые пополнения: <b>{count}</b> ({amount})",
                ).format(
                    count=data['count'],
                    amount=texts.format_price(data['total_amount_kopeks']),
                ) + "\n"

        if 'referral_commission_topup' in summary['earnings_by_type']:
            data = summary['earnings_by_type']['referral_commission_topup']
            if data['total_amount_kopeks'] > 0:
                referral_text += texts.t(
                    "REFERRAL_EARNINGS_TOPUPS",
                    "• Комиссии с пополнений: <b>{count}</b> ({amount})",
                ).format(
                    count=data['count'],
                    amount=texts.format_price(data['total_amount_kopeks']),
                ) + "\n"

        if 'referral_commission' in summary['earnings_by_type']:
            data = summary['earnings_by_type']['referral_commission']
            if data['total_amount_kopeks'] > 0:
                referral_text += texts.t(
                    "REFERRAL_EARNINGS_PURCHASES",
                    "• Комиссии с покупок: <b>{count}</b> ({amount})",
                ).format(
                    count=data['count'],
                    amount=texts.format_price(data['total_amount_kopeks']),
                ) + "\n"

        referral_text += "\n"

    referral_text += texts.t(
        "REFERRAL_INVITE_FOOTER",
        "📢 Приглашайте друзей и зарабатывайте!",
    )

    await edit_or_answer_photo(
        callback,
        referral_text,
        get_referral_keyboard(
            db_user.language,
            show_withdrawal_button=withdrawal_settings.enabled,
        ),
    )
    await callback.answer()


async def show_referral_qr(
    callback: types.CallbackQuery,
    db_user: User,
):
    await callback.answer()

    texts = get_texts(db_user.language)

    bot_username = (await callback.bot.get_me()).username
    referral_link = f"https://t.me/{bot_username}?start={db_user.referral_code}"

    qr_dir = Path("data") / "referral_qr"
    qr_dir.mkdir(parents=True, exist_ok=True)

    file_path = qr_dir / f"{db_user.id}.png"
    if not file_path.exists():
        img = qrcode.make(referral_link)
        img.save(file_path)

    photo = FSInputFile(file_path)
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text=texts.BACK, callback_data="menu_referrals")]]
    )

    try:
        await callback.message.edit_media(
            types.InputMediaPhoto(
                media=photo,
                caption=texts.t(
                    "REFERRAL_LINK_CAPTION",
                    "🔗 Ваша реферальная ссылка:\n{link}",
                ).format(link=referral_link),
            ),
            reply_markup=keyboard,
        )
    except TelegramBadRequest:
        await callback.message.delete()
        await callback.message.answer_photo(
            photo,
            caption=texts.t(
                "REFERRAL_LINK_CAPTION",
                "🔗 Ваша реферальная ссылка:\n{link}",
            ).format(link=referral_link),
            reply_markup=keyboard,
        )


async def show_detailed_referral_list(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    page: int = 1
):
    texts = get_texts(db_user.language)

    referrals_data = await get_detailed_referral_list(db, db_user.id, limit=10, offset=(page - 1) * 10)

    if not referrals_data['referrals']:
        await edit_or_answer_photo(
            callback,
            texts.t(
                "REFERRAL_LIST_EMPTY",
                "📋 У вас пока нет рефералов.\n\nПоделитесь своей реферальной ссылкой, чтобы начать зарабатывать!",
            ),
            types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text=texts.BACK, callback_data="menu_referrals")]]
            ),
            parse_mode=None,
        )
        await callback.answer()
        return

    text = texts.t(
        "REFERRAL_LIST_HEADER",
        "👥 <b>Ваши рефералы</b> (стр. {current}/{total})",
    ).format(
        current=referrals_data['current_page'],
        total=referrals_data['total_pages'],
    ) + "\n\n"
    
    for i, referral in enumerate(referrals_data['referrals'], 1):
        status_emoji = "🟢" if referral['status'] == 'active' else "🔴"
        
        topup_emoji = "💰" if referral['has_made_first_topup'] else "⏳"
        
        text += texts.t(
            "REFERRAL_LIST_ITEM_HEADER",
            "{index}. {status} <b>{name}</b>",
        ).format(index=i, status=status_emoji, name=referral['full_name']) + "\n"
        text += texts.t(
            "REFERRAL_LIST_ITEM_TOPUPS",
            "   {emoji} Пополнений: {count}",
        ).format(emoji=topup_emoji, count=referral['topups_count']) + "\n"
        text += texts.t(
            "REFERRAL_LIST_ITEM_EARNED",
            "   💎 Заработано с него: {amount}",
        ).format(amount=texts.format_price(referral['total_earned_kopeks'])) + "\n"
        text += texts.t(
            "REFERRAL_LIST_ITEM_REGISTERED",
            "   📅 Регистрация: {days} дн. назад",
        ).format(days=referral['days_since_registration']) + "\n"

        if referral['days_since_activity'] is not None:
            text += texts.t(
                "REFERRAL_LIST_ITEM_ACTIVITY",
                "   🕐 Активность: {days} дн. назад",
            ).format(days=referral['days_since_activity']) + "\n"
        else:
            text += texts.t(
                "REFERRAL_LIST_ITEM_ACTIVITY_LONG_AGO",
                "   🕐 Активность: давно",
            ) + "\n"
        
        text += "\n"
    
    keyboard = []
    nav_buttons = []
    
    if referrals_data['has_prev']:
        nav_buttons.append(types.InlineKeyboardButton(
            text=texts.t("REFERRAL_LIST_PREV_PAGE", "⬅️ Назад"),
            callback_data=f"referral_list_page_{page - 1}"
        ))

    if referrals_data['has_next']:
        nav_buttons.append(types.InlineKeyboardButton(
            text=texts.t("REFERRAL_LIST_NEXT_PAGE", "Вперед ➡️"),
            callback_data=f"referral_list_page_{page + 1}"
        ))
    
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    keyboard.append([types.InlineKeyboardButton(
        text=texts.BACK,
        callback_data="menu_referrals"
    )])

    await edit_or_answer_photo(
        callback,
        text,
        types.InlineKeyboardMarkup(inline_keyboard=keyboard),
    )
    await callback.answer()


async def show_referral_analytics(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    texts = get_texts(db_user.language)

    analytics = await get_referral_analytics(db, db_user.id)

    text = texts.t("REFERRAL_ANALYTICS_TITLE", "📊 <b>Аналитика рефералов</b>") + "\n\n"

    text += texts.t(
        "REFERRAL_ANALYTICS_EARNINGS_HEADER",
        "💰 <b>Доходы по периодам:</b>",
    ) + "\n"
    text += texts.t(
        "REFERRAL_ANALYTICS_EARNINGS_TODAY",
        "• Сегодня: {amount}",
    ).format(amount=texts.format_price(analytics['earnings_by_period']['today'])) + "\n"
    text += texts.t(
        "REFERRAL_ANALYTICS_EARNINGS_WEEK",
        "• За неделю: {amount}",
    ).format(amount=texts.format_price(analytics['earnings_by_period']['week'])) + "\n"
    text += texts.t(
        "REFERRAL_ANALYTICS_EARNINGS_MONTH",
        "• За месяц: {amount}",
    ).format(amount=texts.format_price(analytics['earnings_by_period']['month'])) + "\n"
    text += texts.t(
        "REFERRAL_ANALYTICS_EARNINGS_QUARTER",
        "• За квартал: {amount}",
    ).format(amount=texts.format_price(analytics['earnings_by_period']['quarter'])) + "\n\n"

    if analytics['top_referrals']:
        text += texts.t(
            "REFERRAL_ANALYTICS_TOP_TITLE",
            "🏆 <b>Топ-{count} рефералов:</b>",
        ).format(count=len(analytics['top_referrals'])) + "\n"
        for i, ref in enumerate(analytics['top_referrals'], 1):
            text += texts.t(
                "REFERRAL_ANALYTICS_TOP_ITEM",
                "{index}. {name}: {amount} ({count} начислений)",
            ).format(
                index=i,
                name=ref['referral_name'],
                amount=texts.format_price(ref['total_earned_kopeks']),
                count=ref['earnings_count'],
            ) + "\n"
        text += "\n"

    text += texts.t(
        "REFERRAL_ANALYTICS_FOOTER",
        "📈 Продолжайте развивать свою реферальную сеть!",
    )

    await edit_or_answer_photo(
        callback,
        text,
        types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text=texts.BACK, callback_data="menu_referrals")]
        ]),
    )
    await callback.answer()


async def create_invite_message(
    callback: types.CallbackQuery,
    db_user: User
):
    texts = get_texts(db_user.language)

    bot_username = (await callback.bot.get_me()).username
    referral_link = f"https://t.me/{bot_username}?start={db_user.referral_code}"

    invite_text = (
        texts.t("REFERRAL_INVITE_TITLE", "🎉 Присоединяйся к VPN сервису!")
        + "\n\n"
        + texts.t(
            "REFERRAL_INVITE_BONUS",
            "💎 При первом пополнении от {minimum} ты получишь {bonus} бонусом на баланс!",
        ).format(
            minimum=texts.format_price(settings.REFERRAL_MINIMUM_TOPUP_KOPEKS),
            bonus=texts.format_price(settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS),
        )
        + "\n\n"
        + texts.t("REFERRAL_INVITE_FEATURE_FAST", "🚀 Быстрое подключение")
        + "\n"
        + texts.t("REFERRAL_INVITE_FEATURE_SERVERS", "🌍 Серверы по всему миру")
        + "\n"
        + texts.t("REFERRAL_INVITE_FEATURE_SECURE", "🔒 Надежная защита")
        + "\n\n"
        + texts.t("REFERRAL_INVITE_LINK_PROMPT", "👇 Переходи по ссылке:")
        + f"\n{referral_link}"
    )

    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(
            text=texts.t("REFERRAL_SHARE_BUTTON", "📤 Поделиться"),
            switch_inline_query=invite_text
        )],
        [types.InlineKeyboardButton(
            text=texts.BACK,
            callback_data="menu_referrals"
        )]
    ])

    await edit_or_answer_photo(
        callback,
        (
            texts.t("REFERRAL_INVITE_CREATED_TITLE", "📝 <b>Приглашение создано!</b>")
            + "\n\n"
            + texts.t(
                "REFERRAL_INVITE_CREATED_INSTRUCTION",
                "Нажмите кнопку «📤 Поделиться» чтобы отправить приглашение в любой чат, или скопируйте текст ниже:",
            )
            + "\n\n"
            f"<code>{invite_text}</code>"
        ),
        keyboard,
    )
    await callback.answer()


async def start_referral_withdrawal_request(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state
):
    texts = get_texts(db_user.language)
    settings_obj = await ReferralWithdrawalService.get_settings(db)

    if not settings_obj.enabled:
        await callback.answer(
            texts.t(
                "REFERRAL_WITHDRAWAL_DISABLED",
                "Вывод реферального дохода сейчас недоступен.",
            ),
            show_alert=True,
        )
        return

    available = await ReferralWithdrawalService.get_available_amount(db, db_user.id)
    if available < settings_obj.min_amount_kopeks:
        await callback.answer(
            texts.t(
                "REFERRAL_WITHDRAWAL_TOO_LOW",
                "Минимальная сумма для вывода: {min_amount}. Доступно: {available}.",
            ).format(
                min_amount=texts.format_price(settings_obj.min_amount_kopeks),
                available=texts.format_price(available),
            ),
            show_alert=True,
        )
        return

    prompt_template = settings_obj.prompt_text
    if not prompt_template or prompt_template == ReferralWithdrawalService.DEFAULT_PROMPT:
        prompt_template = texts.t(
            "REFERRAL_WITHDRAWAL_PROMPT",
            ReferralWithdrawalService.DEFAULT_PROMPT,
        )

    prompt_text = prompt_template.format(
        available=texts.format_price(available),
        min_amount=texts.format_price(settings_obj.min_amount_kopeks),
    )

    await state.set_state(ReferralWithdrawalStates.waiting_for_requisites)
    await state.update_data(referral_withdraw_available=available)
    await callback.message.answer(
        prompt_text,
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text=texts.BACK, callback_data="menu_referrals")]]
        ),
    )
    await callback.answer()


async def handle_referral_withdrawal_requisites(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state
):
    texts = get_texts(db_user.language)
    requisites = (message.text or "").strip()

    if not requisites:
        await message.answer(
            texts.t(
                "REFERRAL_WITHDRAWAL_ENTER_REQUISITES",
                "Пожалуйста, отправьте реквизиты для вывода.",
            )
        )
        return

    request = await ReferralWithdrawalService.create_request(
        db, db_user.id, requisites
    )
    await state.clear()

    if not request:
        settings_obj = await ReferralWithdrawalService.get_settings(db)
        await message.answer(
            texts.t(
                "REFERRAL_WITHDRAWAL_TOO_LOW",
                "Минимальная сумма для вывода: {min_amount}. Доступно: {available}.",
            ).format(
                min_amount=texts.format_price(settings_obj.min_amount_kopeks),
                available=texts.format_price(
                    await ReferralWithdrawalService.get_available_amount(db, db_user.id)
                ),
            )
        )
        return

    settings_obj = await ReferralWithdrawalService.get_settings(db)
    success_template = settings_obj.success_text
    if not success_template or success_template == ReferralWithdrawalService.DEFAULT_SUCCESS:
        success_template = texts.t(
            "REFERRAL_WITHDRAWAL_SUBMITTED",
            ReferralWithdrawalService.DEFAULT_SUCCESS,
        )

    await message.answer(
        success_template.format(
            amount=texts.format_price(request.amount_kopeks),
            available=texts.format_price(
                await ReferralWithdrawalService.get_available_amount(db, db_user.id)
            ),
        )
    )


def register_handlers(dp: Dispatcher):
    
    dp.callback_query.register(
        show_referral_info,
        F.data == "menu_referrals"
    )
    
    dp.callback_query.register(
        create_invite_message,
        F.data == "referral_create_invite"
    )

    dp.callback_query.register(
        show_referral_qr,
        F.data == "referral_show_qr"
    )
    
    dp.callback_query.register(
        show_detailed_referral_list,
        F.data == "referral_list"
    )
    
    dp.callback_query.register(
        show_referral_analytics,
        F.data == "referral_analytics"
    )

    dp.callback_query.register(
        start_referral_withdrawal_request,
        F.data == "referral_withdrawal_request",
    )

    dp.message.register(
        handle_referral_withdrawal_requisites,
        ReferralWithdrawalStates.waiting_for_requisites,
    )
    
    dp.callback_query.register(
        lambda callback, db_user, db: show_detailed_referral_list(
            callback, db_user, db, int(callback.data.split('_')[-1])
        ),
        F.data.startswith("referral_list_page_")
    )
