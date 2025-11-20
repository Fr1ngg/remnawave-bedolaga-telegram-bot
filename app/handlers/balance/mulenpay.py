import logging
from aiogram import types
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.keyboards.inline import get_back_keyboard
from app.localization.texts import get_texts
from app.services.payment_service import PaymentService
from app.utils.decorators import error_handler
from app.states import BalanceStates

logger = logging.getLogger(__name__)


@error_handler
async def start_mulenpay_payment(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    mulenpay_name = settings.get_mulenpay_display_name()
    mulenpay_name_html = settings.get_mulenpay_display_name_html()

    if not settings.is_mulenpay_enabled():
        await callback.answer(
            f"❌ Оплата через {mulenpay_name} временно недоступна",
            show_alert=True,
        )
        return

    message_template = texts.t(
        "MULENPAY_TOPUP_PROMPT",
        (
            "💳 <b>Оплата через {mulenpay_name_html}</b>\n\n"
            "Введите сумму для пополнения от 100 до 100 000 ₽.\n"
            "Оплата происходит через защищенную платформу {mulenpay_name}."
        ),
    )
    message_text = message_template.format(
        mulenpay_name=mulenpay_name,
        mulenpay_name_html=mulenpay_name_html,
    )

    keyboard = get_back_keyboard(db_user.language)

    if settings.YOOKASSA_QUICK_AMOUNT_SELECTION_ENABLED and not settings.DISABLE_TOPUP_BUTTONS:
        from .main import get_quick_amount_buttons
        quick_amount_buttons = get_quick_amount_buttons(db_user.language, db_user)
        if quick_amount_buttons:
            keyboard.inline_keyboard = quick_amount_buttons + keyboard.inline_keyboard

    edited_message = await callback.message.edit_text(
        message_text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )

    payment_service = PaymentService(callback.bot)
    await payment_service.remember_topup_invoice_message(
        db_user.id,
        callback.message.chat.id,
        edited_message.message_id,
    )

    await state.set_state(BalanceStates.waiting_for_amount)
    await state.update_data(payment_method="mulenpay")
    await callback.answer()


@error_handler
async def process_mulenpay_payment_amount(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    amount_kopeks: int,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    mulenpay_name = settings.get_mulenpay_display_name()
    mulenpay_name_html = settings.get_mulenpay_display_name_html()

    if not settings.is_mulenpay_enabled():
        await message.answer(f"❌ Оплата через {mulenpay_name} временно недоступна")
        return

    if amount_kopeks < settings.MULENPAY_MIN_AMOUNT_KOPEKS:
        await message.answer(
            f"Минимальная сумма пополнения: {settings.format_price(settings.MULENPAY_MIN_AMOUNT_KOPEKS)}"
        )
        return

    if amount_kopeks > settings.MULENPAY_MAX_AMOUNT_KOPEKS:
        await message.answer(
            f"Максимальная сумма пополнения: {settings.format_price(settings.MULENPAY_MAX_AMOUNT_KOPEKS)}"
        )
        return

    amount_rubles = amount_kopeks / 100

    try:
        payment_service = PaymentService(message.bot)
        payment_result = await payment_service.create_mulenpay_payment(
            db=db,
            user_id=db_user.id,
            amount_kopeks=amount_kopeks,
            description=settings.get_balance_payment_description(amount_kopeks),
            language=db_user.language,
        )

        if not payment_result or not payment_result.get("payment_url"):
            await message.answer(
                texts.t(
                    "MULENPAY_PAYMENT_ERROR",
                    "❌ Ошибка создания платежа {mulenpay_name}. Попробуйте позже или обратитесь в поддержку.",
                ).format(mulenpay_name=mulenpay_name)
            )
            await state.clear()
            return

        payment_url = payment_result.get("payment_url")
        mulen_payment_id = payment_result.get("mulen_payment_id")
        local_payment_id = payment_result.get("local_payment_id")

        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=texts.t(
                            "MULENPAY_PAY_BUTTON",
                            "💳 Оплатить через {mulenpay_name}",
                        ).format(mulenpay_name=mulenpay_name),
                        url=payment_url,
                    )
                ],
                [
                    types.InlineKeyboardButton(
                        text=texts.t("CHECK_STATUS_BUTTON", "📊 Проверить статус"),
                        callback_data=f"check_mulenpay_{local_payment_id}",
                    )
                ],
                [types.InlineKeyboardButton(text=texts.BACK, callback_data="balance_topup")],
            ]
        )

        payment_id_display = mulen_payment_id if mulen_payment_id is not None else local_payment_id

        message_template = texts.t(
            "MULENPAY_PAYMENT_INSTRUCTIONS",
            (
                "💳 <b>Оплата через {mulenpay_name_html}</b>\n\n"
                "💰 Сумма: {amount}\n"
                "🆔 ID платежа: {payment_id}\n\n"
                "📱 <b>Инструкция:</b>\n"
                "1. Нажмите кнопку 'Оплатить через {mulenpay_name}'\n"
                "2. Следуйте подсказкам платежной системы\n"
                "3. Подтвердите перевод\n"
                "4. Средства зачислятся автоматически\n\n"
                "❓ Если возникнут проблемы, обратитесь в {support}"
            ),
        )

        message_text = message_template.format(
            amount=settings.format_price(amount_kopeks),
            payment_id=payment_id_display,
            support=settings.get_support_contact_display_html(),
            mulenpay_name=mulenpay_name,
            mulenpay_name_html=mulenpay_name_html,
        )

        sent_message = await message.answer(
            message_text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )

        await payment_service.remember_topup_invoice_message(
            db_user.id,
            message.chat.id,
            sent_message.message_id,
        )

        await state.clear()

        logger.info(
            "Создан %s платеж для пользователя %s: %s₽, ID: %s",
            mulenpay_name,
            db_user.telegram_id,
            amount_rubles,
            payment_id_display,
        )

    except Exception as e:
        logger.error(f"Ошибка создания {mulenpay_name} платежа: {e}")
        await message.answer(
            texts.t(
                "MULENPAY_PAYMENT_ERROR",
                "❌ Ошибка создания платежа {mulenpay_name}. Попробуйте позже или обратитесь в поддержку.",
            ).format(mulenpay_name=mulenpay_name)
        )
        await state.clear()


@error_handler
async def check_mulenpay_payment_status(
    callback: types.CallbackQuery,
    db: AsyncSession
):
    try:
        local_payment_id = int(callback.data.split('_')[-1])
        payment_service = PaymentService(callback.bot)
        status_info = await payment_service.get_mulenpay_payment_status(db, local_payment_id)

        if not status_info:
            await callback.answer("❌ Платеж не найден", show_alert=True)
            return

        payment = status_info["payment"]

        status_labels = {
            "created": ("⏳", "Ожидает оплаты"),
            "processing": ("⌛", "Обрабатывается"),
            "success": ("✅", "Оплачен"),
            "canceled": ("❌", "Отменен"),
            "error": ("⚠️", "Ошибка"),
            "hold": ("🔒", "Холд"),
            "unknown": ("❓", "Неизвестно"),
        }

        emoji, status_text = status_labels.get(payment.status, ("❓", "Неизвестно"))

        mulenpay_name = settings.get_mulenpay_display_name()
        message_lines = [
            f"💳 Статус платежа {mulenpay_name}:\n\n",
            f"🆔 ID: {payment.mulen_payment_id or payment.id}\n",
            f"💰 Сумма: {settings.format_price(payment.amount_kopeks)}\n",
            f"📊 Статус: {emoji} {status_text}\n",
            f"📅 Создан: {payment.created_at.strftime('%d.%m.%Y %H:%M')}\n",
        ]

        if payment.is_paid:
            message_lines.append("\n✅ Платеж успешно завершен! Средства уже на балансе.")
        elif payment.status in {"created", "processing"}:
            message_lines.append(
                "\n⏳ Платеж еще не завершен. Завершите оплату по ссылке и проверьте статус позже."
            )
            if payment.payment_url:
                message_lines.append(f"\n🔗 Ссылка на оплату: {payment.payment_url}")
        elif payment.status in {"canceled", "error"}:
            message_lines.append(
                f"\n❌ Платеж не был завершен. Попробуйте создать новый платеж или обратитесь в {settings.get_support_contact_display()}"
            )

        message_text = "".join(message_lines)

        if len(message_text) > 190:
            await callback.message.answer(message_text)
            await callback.answer("ℹ️ Статус платежа отправлен в чат", show_alert=True)
        else:
            await callback.answer(message_text, show_alert=True)

    except Exception as e:
        logger.error(
            f"Ошибка проверки статуса {settings.get_mulenpay_display_name()}: {e}"
        )
        await callback.answer("❌ Ошибка проверки статуса", show_alert=True)