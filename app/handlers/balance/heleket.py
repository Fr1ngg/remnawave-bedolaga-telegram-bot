import logging
from typing import Optional

from aiogram import types
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.keyboards.inline import get_back_keyboard
from app.localization.texts import get_texts
from app.services.payment_service import PaymentService
from app.states import BalanceStates
from app.utils.decorators import error_handler

logger = logging.getLogger(__name__)


@error_handler
async def start_heleket_payment(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
) -> None:
    texts = get_texts(db_user.language)

    if not settings.is_heleket_enabled():
        await callback.answer("❌ Оплата через Heleket недоступна", show_alert=True)
        return

    markup = settings.get_heleket_markup_percent()
    markup_text: Optional[str]
    if markup > 0:
        label = texts.t("PAYMENT_HELEKET_MARKUP_LABEL", "Наценка провайдера")
        markup_text = f"{label}: {markup:.0f}%"
    elif markup < 0:
        label = texts.t("PAYMENT_HELEKET_DISCOUNT_LABEL", "Скидка провайдера")
        markup_text = f"{label}: {abs(markup):.0f}%"
    else:
        markup_text = None

    message_lines = [
        "🪙 <b>Пополнение через Heleket</b>",
        "\n",
        "Введите сумму пополнения от 100 до 100,000 ₽:",
        "",
        "⚡ Мгновенное зачисление", "🔒 Безопасная оплата",
    ]

    if markup_text:
        message_lines.extend(["", markup_text])

    keyboard = get_back_keyboard(db_user.language)

    if settings.YOOKASSA_QUICK_AMOUNT_SELECTION_ENABLED and not settings.DISABLE_TOPUP_BUTTONS:
        from .main import get_quick_amount_buttons

        quick_buttons = get_quick_amount_buttons(db_user.language)
        if quick_buttons:
            keyboard.inline_keyboard = quick_buttons + keyboard.inline_keyboard

    await callback.message.edit_text(
        "\n".join(filter(None, message_lines)),
        reply_markup=keyboard,
        parse_mode="HTML",
    )

    await state.set_state(BalanceStates.waiting_for_amount)
    await state.update_data(payment_method="heleket")
    await callback.answer()


@error_handler
async def process_heleket_payment_amount(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    amount_kopeks: int,
    state: FSMContext,
) -> None:
    texts = get_texts(db_user.language)

    if not settings.is_heleket_enabled():
        await message.answer("❌ Оплата через Heleket недоступна")
        return

    amount_rubles = amount_kopeks / 100

    if amount_rubles < 100:
        await message.answer("Минимальная сумма пополнения: 100 ₽")
        return

    if amount_rubles > 100000:
        await message.answer("Максимальная сумма пополнения: 100,000 ₽")
        return

    payment_service = PaymentService(message.bot)

    result = await payment_service.create_heleket_payment(
        db=db,
        user_id=db_user.id,
        amount_kopeks=amount_kopeks,
        description=f"Пополнение баланса на {amount_rubles:.0f} ₽",
        language=db_user.language,
    )

    if not result:
        await message.answer(
            "❌ Не удалось создать счёт в Heleket. Попробуйте позже или обратитесь в поддержку."
        )
        await state.clear()
        return

    payment_url = result.get("payment_url")
    if not payment_url:
        await message.answer("❌ Не удалось получить ссылку для оплаты Heleket")
        await state.clear()
        return

    payer_amount = result.get("payer_amount")
    payer_currency = result.get("payer_currency")
    exchange_rate = result.get("exchange_rate")
    discount_percent = result.get("discount_percent")

    details = [
        "🪙 <b>Оплата через Heleket</b>",
        "",
        f"💰 Сумма к зачислению: {amount_rubles:.0f} ₽",
    ]

    if payer_amount and payer_currency:
        details.append(f"🪙 К оплате: {payer_amount} {payer_currency}")

    markup_percent: Optional[float] = None
    if discount_percent is not None:
        try:
            discount_int = int(discount_percent)
            markup_percent = -discount_int
        except (TypeError, ValueError):
            markup_percent = None

    if markup_percent:
        label_markup = texts.t("PAYMENT_HELEKET_MARKUP_LABEL", "Наценка провайдера")
        label_discount = texts.t("PAYMENT_HELEKET_DISCOUNT_LABEL", "Скидка провайдера")
        absolute = abs(markup_percent)
        if markup_percent > 0:
            details.append(f"📈 {label_markup}: +{absolute}%")
        else:
            details.append(f"📉 {label_discount}: {absolute}%")

    if payer_amount and payer_currency:
        try:
            payer_amount_float = float(payer_amount)
            if payer_amount_float > 0:
                rub_per_currency = amount_rubles / payer_amount_float
                details.append(
                    f"💱 Курс: 1 {payer_currency} ≈ {rub_per_currency:.2f} ₽"
                )
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    details.extend(
        [
            "",
            "📱 Инструкция:",
            "1. Нажмите кнопку 'Оплатить'",
            "2. Перейдите на страницу Heleket",
            "3. Оплатите указанную сумму",
            "4. Баланс пополнится автоматически",
        ]
    )

    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=texts.t("PAY_WITH_COINS_BUTTON", "🪙 Оплатить"), url=payment_url)],
        [
            types.InlineKeyboardButton(
                text=texts.t("CHECK_STATUS_BUTTON", "📊 Проверить статус"),
                callback_data=f"check_heleket_{result['local_payment_id']}"
            )
        ],
        [types.InlineKeyboardButton(text=texts.BACK, callback_data="balance_topup")],
    ])

    await message.answer("\n".join(details), parse_mode="HTML", reply_markup=keyboard)
    await state.clear()


@error_handler
async def check_heleket_payment_status(
    callback: types.CallbackQuery,
    db: AsyncSession,
) -> None:
    try:
        local_payment_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("Некорректный идентификатор платежа", show_alert=True)
        return

    from app.database.crud.heleket import get_heleket_payment_by_id

    payment = await get_heleket_payment_by_id(db, local_payment_id)
    if not payment:
        await callback.answer("Платёж не найден", show_alert=True)
        return

    if payment.is_paid:
        await callback.answer("✅ Платёж уже оплачен", show_alert=True)
        return

    await callback.answer("Платёж ещё не оплачен", show_alert=True)
