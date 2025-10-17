import html
import logging
from aiogram import types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.database import AsyncSessionLocal
from app.database.models import User
from app.keyboards.inline import get_back_keyboard
from app.localization.texts import get_texts
from app.services.payment_service import PaymentService
from app.utils.decorators import error_handler
from app.states import BalanceStates

logger = logging.getLogger(__name__)


def _get_available_pal24_methods() -> list[str]:
    methods: list[str] = []
    if settings.is_pal24_sbp_button_visible():
        methods.append("sbp")
    if settings.is_pal24_card_button_visible():
        methods.append("card")
    if not methods:
        methods.append("sbp")
    return methods


async def _send_pal24_payment_message(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    amount_kopeks: int,
    payment_method: str,
    state: FSMContext,
) -> None:
    texts = get_texts(db_user.language)

    try:
        payment_service = PaymentService(message.bot)
        payment_result = await payment_service.create_pal24_payment(
            db=db,
            user_id=db_user.id,
            amount_kopeks=amount_kopeks,
            description=settings.get_balance_payment_description(amount_kopeks),
            language=db_user.language,
            payment_method=payment_method,
        )

        if not payment_result:
            await message.answer(
                texts.t(
                    "PAL24_PAYMENT_ERROR",
                    "❌ Ошибка создания платежа PayPalych. Попробуйте позже или обратитесь в поддержку.",
                )
            )
            await state.clear()
            return

        sbp_url = (
            payment_result.get("sbp_url")
            or payment_result.get("transfer_url")
        )
        card_url = payment_result.get("card_url")
        fallback_url = (
            payment_result.get("link_page_url")
            or payment_result.get("link_url")
        )

        if not (sbp_url or card_url or fallback_url):
            await message.answer(
                texts.t(
                    "PAL24_PAYMENT_ERROR",
                    "❌ Ошибка создания платежа PayPalych. Попробуйте позже или обратитесь в поддержку.",
                )
            )
            await state.clear()
            return

        if not sbp_url:
            sbp_url = fallback_url

        bill_id = payment_result.get("bill_id")
        local_payment_id = payment_result.get("local_payment_id")

        pay_buttons: list[list[types.InlineKeyboardButton]] = []
        steps: list[str] = []
        step_counter = 1

        default_sbp_text = texts.t(
            "PAL24_SBP_PAY_BUTTON",
            "🏦 Оплатить через PayPalych (СБП)",
        )
        sbp_button_text = settings.get_pal24_sbp_button_text(default_sbp_text)

        if sbp_url and settings.is_pal24_sbp_button_visible():
            pay_buttons.append(
                [
                    types.InlineKeyboardButton(
                        text=sbp_button_text,
                        url=sbp_url,
                    )
                ]
            )
            steps.append(
                texts.t(
                    "PAL24_INSTRUCTION_BUTTON",
                    "{step}. Нажмите кнопку «{button}»",
                ).format(step=step_counter, button=html.escape(sbp_button_text))
            )
            step_counter += 1

        default_card_text = texts.t(
            "PAL24_CARD_PAY_BUTTON",
            "💳 Оплатить банковской картой (PayPalych)",
        )
        card_button_text = settings.get_pal24_card_button_text(default_card_text)

        if card_url and card_url != sbp_url and settings.is_pal24_card_button_visible():
            pay_buttons.append(
                [
                    types.InlineKeyboardButton(
                        text=card_button_text,
                        url=card_url,
                    )
                ]
            )
            steps.append(
                texts.t(
                    "PAL24_INSTRUCTION_BUTTON",
                    "{step}. Нажмите кнопку «{button}»",
                ).format(step=step_counter, button=html.escape(card_button_text))
            )
            step_counter += 1

        if not pay_buttons and fallback_url and settings.is_pal24_sbp_button_visible():
            pay_buttons.append(
                [
                    types.InlineKeyboardButton(
                        text=sbp_button_text,
                        url=fallback_url,
                    )
                ]
            )
            steps.append(
                texts.t(
                    "PAL24_INSTRUCTION_BUTTON",
                    "{step}. Нажмите кнопку «{button}»",
                ).format(step=step_counter, button=html.escape(sbp_button_text))
            )
            step_counter += 1

        follow_template = texts.t(
            "PAL24_INSTRUCTION_FOLLOW",
            "{step}. Следуйте подсказкам платёжной системы",
        )
        steps.append(follow_template.format(step=step_counter))
        step_counter += 1

        confirm_template = texts.t(
            "PAL24_INSTRUCTION_CONFIRM",
            "{step}. Подтвердите перевод",
        )
        steps.append(confirm_template.format(step=step_counter))
        step_counter += 1

        success_template = texts.t(
            "PAL24_INSTRUCTION_COMPLETE",
            "{step}. Средства зачислятся автоматически",
        )
        steps.append(success_template.format(step=step_counter))

        message_template = texts.t(
            "PAL24_PAYMENT_INSTRUCTIONS",
            (
                "🏦 <b>Оплата через PayPalych</b>\n\n"
                "💰 Сумма: {amount}\n"
                "🆔 ID счета: {bill_id}\n\n"
                "📱 <b>Инструкция:</b>\n{steps}\n\n"
                "❓ Если возникнут проблемы, обратитесь в {support}"
            ),
        )

        keyboard_rows = pay_buttons + [
            [
                types.InlineKeyboardButton(
                    text=texts.t("CHECK_STATUS_BUTTON", "📊 Проверить статус"),
                    callback_data=f"check_pal24_{local_payment_id}",
                )
            ],
            [types.InlineKeyboardButton(text=texts.BACK, callback_data="balance_topup")],
        ]

        keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

        message_text = message_template.format(
            amount=settings.format_price(amount_kopeks),
            bill_id=bill_id,
            steps="\n".join(steps),
            support=settings.get_support_contact_display_html(),
        )

        await message.answer(
            message_text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )

        await state.clear()

        logger.info(
            "Создан PayPalych счет для пользователя %s: %s₽, ID: %s, метод: %s",
            db_user.telegram_id,
            amount_kopeks / 100,
            bill_id,
            payment_method,
        )

    except Exception as error:
        logger.error(f"Ошибка создания PayPalych платежа: {error}")
        await message.answer(
            texts.t(
                "PAL24_PAYMENT_ERROR",
                "❌ Ошибка создания платежа PayPalych. Попробуйте позже или обратитесь в поддержку.",
            )
        )
        await state.clear()

@error_handler
async def start_pal24_payment(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
):
    texts = get_texts(db_user.language)

    if not settings.is_pal24_enabled():
        await callback.answer("❌ Оплата через PayPalych временно недоступна", show_alert=True)
        return

    # Формируем текст сообщения в зависимости от доступных способов оплаты
    if settings.is_pal24_sbp_button_visible() and settings.is_pal24_card_button_visible():
        payment_methods_text = "СБП и банковской картой"
    elif settings.is_pal24_sbp_button_visible():
        payment_methods_text = "СБП"
    elif settings.is_pal24_card_button_visible():
        payment_methods_text = "банковской картой"
    else:
        # Если обе кнопки отключены, используем общий текст
        payment_methods_text = "доступными способами"

    message_text = texts.t(
        "PAL24_TOPUP_PROMPT",
        (
            f"🏦 <b>Оплата через PayPalych ({payment_methods_text})</b>\n\n"
            "Введите сумму для пополнения от 100 до 1 000 000 ₽.\n"
            f"Оплата проходит через PayPalych ({payment_methods_text})."
        ),
    )

    keyboard = get_back_keyboard(db_user.language)

    if settings.YOOKASSA_QUICK_AMOUNT_SELECTION_ENABLED and not settings.DISABLE_TOPUP_BUTTONS:
        from .main import get_quick_amount_buttons
        quick_amount_buttons = get_quick_amount_buttons(db_user.language)
        if quick_amount_buttons:
            keyboard.inline_keyboard = quick_amount_buttons + keyboard.inline_keyboard

    await callback.message.edit_text(
        message_text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )

    await state.set_state(BalanceStates.waiting_for_amount)
    await state.update_data(payment_method="pal24")
    await callback.answer()


@error_handler
async def process_pal24_payment_amount(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    amount_kopeks: int,
    state: FSMContext,
):
    texts = get_texts(db_user.language)

    if not settings.is_pal24_enabled():
        await message.answer("❌ Оплата через PayPalych временно недоступна")
        return

    if amount_kopeks < settings.PAL24_MIN_AMOUNT_KOPEKS:
        min_rubles = settings.PAL24_MIN_AMOUNT_KOPEKS / 100
        await message.answer(f"❌ Минимальная сумма для оплаты через PayPalych: {min_rubles:.0f} ₽")
        return

    if amount_kopeks > settings.PAL24_MAX_AMOUNT_KOPEKS:
        max_rubles = settings.PAL24_MAX_AMOUNT_KOPEKS / 100
        await message.answer(
            f"❌ Максимальная сумма для оплаты через PayPalych: {max_rubles:,.0f} ₽".replace(',', ' ')
        )
        return

    available_methods = _get_available_pal24_methods()

    if len(available_methods) == 1:
        await _send_pal24_payment_message(
            message,
            db_user,
            db,
            amount_kopeks,
            available_methods[0],
            state,
        )
        return

    await state.update_data(pal24_amount_kopeks=amount_kopeks)
    await state.set_state(BalanceStates.waiting_for_pal24_method)

    method_buttons: list[list[types.InlineKeyboardButton]] = []
    if "sbp" in available_methods:
        method_buttons.append(
            [
                types.InlineKeyboardButton(
                    text=settings.get_pal24_sbp_button_text(
                        texts.t("PAL24_SBP_PAY_BUTTON", "🏦 Оплатить через PayPalych (СБП)")
                    ),
                    callback_data="pal24_method_sbp",
                )
            ]
        )
    if "card" in available_methods:
        method_buttons.append(
            [
                types.InlineKeyboardButton(
                    text=settings.get_pal24_card_button_text(
                        texts.t("PAL24_CARD_PAY_BUTTON", "💳 Оплатить банковской картой (PayPalych)")
                    ),
                    callback_data="pal24_method_card",
                )
            ]
        )

    method_buttons.append([types.InlineKeyboardButton(text=texts.BACK, callback_data="balance_topup")])

    await message.answer(
        texts.t(
            "PAL24_SELECT_PAYMENT_METHOD",
            "Выберите способ оплаты PayPalych:",
        ),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=method_buttons),
    )


@error_handler
async def handle_pal24_method_selection(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
):
    data = await state.get_data()
    amount_kopeks = data.get("pal24_amount_kopeks")
    if not amount_kopeks:
        texts = get_texts(db_user.language)
        await callback.answer(
            texts.t(
                "PAL24_PAYMENT_ERROR",
                "❌ Ошибка создания платежа PayPalych. Попробуйте позже или обратитесь в поддержку.",
            ),
            show_alert=True,
        )
        await state.clear()
        return

    method = "sbp" if callback.data.endswith("_sbp") else "card"

    await callback.answer()

    async with AsyncSessionLocal() as db:
        await _send_pal24_payment_message(
            callback.message,
            db_user,
            db,
            int(amount_kopeks),
            method,
            state,
        )


@error_handler
async def check_pal24_payment_status(
    callback: types.CallbackQuery,
    db: AsyncSession,
):
    try:
        local_payment_id = int(callback.data.split('_')[-1])
        payment_service = PaymentService(callback.bot)
        status_info = await payment_service.get_pal24_payment_status(db, local_payment_id)

        if not status_info:
            await callback.answer("❌ Платеж не найден", show_alert=True)
            return

        payment = status_info["payment"]

        status_labels = {
            "NEW": ("⏳", "Ожидает оплаты"),
            "PROCESS": ("⌛", "Обрабатывается"),
            "SUCCESS": ("✅", "Оплачен"),
            "FAIL": ("❌", "Отменен"),
            "UNDERPAID": ("⚠️", "Недоплата"),
            "OVERPAID": ("⚠️", "Переплата"),
        }

        emoji, status_text = status_labels.get(payment.status, ("❓", "Неизвестно"))

        metadata = payment.metadata_json or {}
        links_meta = metadata.get("links") if isinstance(metadata, dict) else None
        if not isinstance(links_meta, dict):
            links_meta = {}

        sbp_link = links_meta.get("sbp") or payment.link_url
        card_link = links_meta.get("card")

        if not card_link and payment.link_page_url and payment.link_page_url != sbp_link:
            card_link = payment.link_page_url

        message_lines = [
            "🏦 Статус платежа PayPalych:",
            "",
            f"🆔 ID счета: {payment.bill_id}",
            f"💰 Сумма: {settings.format_price(payment.amount_kopeks)}",
            f"📊 Статус: {emoji} {status_text}",
            f"📅 Создан: {payment.created_at.strftime('%d.%m.%Y %H:%M')}",
        ]

        if payment.is_paid:
            message_lines.append("")
            message_lines.append("✅ Платеж успешно завершен! Средства уже на балансе.")
        elif payment.status in {"NEW", "PROCESS"}:
            message_lines.append("")
            message_lines.append("⏳ Платеж еще не завершен. Оплатите счет и проверьте статус позже.")
            if sbp_link:
                message_lines.append("")
                message_lines.append(f"🏦 СБП: {sbp_link}")
            if card_link and card_link != sbp_link:
                message_lines.append(f"💳 Банковская карта: {card_link}")
        elif payment.status in {"FAIL", "UNDERPAID", "OVERPAID"}:
            message_lines.append("")
            message_lines.append(
                f"❌ Платеж не завершен корректно. Обратитесь в {settings.get_support_contact_display()}"
            )

        from app.localization.texts import get_texts
        db_user = getattr(callback, 'db_user', None)
        texts = get_texts(db_user.language if db_user else 'ru') if db_user else get_texts('ru')

        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text=texts.t("CHECK_STATUS_BUTTON", "📊 Проверить статус"), callback_data=f"check_pal24_{local_payment_id}")],
            [types.InlineKeyboardButton(text=texts.BACK, callback_data="balance_topup")]
        ])
        
        await callback.answer()
        try:
            await callback.message.edit_text(
                "\n".join(message_lines),
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
        except TelegramBadRequest as error:
            if "message is not modified" in str(error).lower():
                await callback.answer(texts.t("CHECK_STATUS_NO_CHANGES", "Статус не изменился"))
            else:
                raise

    except Exception as e:
        logger.error(f"Ошибка проверки статуса PayPalych: {e}")
        await callback.answer("❌ Ошибка проверки статуса", show_alert=True)