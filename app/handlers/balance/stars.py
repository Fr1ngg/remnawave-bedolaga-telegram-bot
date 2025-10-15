import logging
from aiogram import types
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.keyboards.inline import get_back_keyboard, get_payment_methods_keyboard
from app.localization.texts import get_texts
from app.services.payment_service import PaymentService
from app.states import BalanceStates
from app.utils.decorators import error_handler
from app.external.telegram_stars import TelegramStarsService

logger = logging.getLogger(__name__)


@error_handler
async def start_stars_payment(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext
):
    texts = get_texts(db_user.language)
    
    if not settings.TELEGRAM_STARS_ENABLED:
        await callback.answer("❌ Пополнение через Stars временно недоступно", show_alert=True)
        return
    
    # Формируем текст сообщения в зависимости от настройки
    if settings.YOOKASSA_QUICK_AMOUNT_SELECTION_ENABLED and not settings.DISABLE_TOPUP_BUTTONS:
        message_text = (
            f"⭐ <b>Пополнение через Telegram Stars</b>\n\n"
            f"Выберите сумму пополнения или введите вручную:"
        )
    else:
        message_text = texts.TOP_UP_AMOUNT
    
    # Создаем клавиатуру
    keyboard = get_back_keyboard(db_user.language)
    
    # Если включен быстрый выбор суммы и не отключены кнопки, добавляем кнопки
    if settings.YOOKASSA_QUICK_AMOUNT_SELECTION_ENABLED and not settings.DISABLE_TOPUP_BUTTONS:
        from .main import get_quick_amount_buttons
        quick_amount_buttons = get_quick_amount_buttons(db_user.language)
        if quick_amount_buttons:
            # Вставляем кнопки быстрого выбора перед кнопкой "Назад"
            keyboard.inline_keyboard = quick_amount_buttons + keyboard.inline_keyboard
    
    await callback.message.edit_text(
        message_text,
        reply_markup=keyboard
    )
    
    await state.set_state(BalanceStates.waiting_for_amount)
    await state.update_data(payment_method="stars")
    await callback.answer()


@error_handler
async def process_stars_payment_amount(
    message: types.Message,
    db_user: User,
    amount_kopeks: int,
    state: FSMContext
):
    texts = get_texts(db_user.language)
    
    if not settings.TELEGRAM_STARS_ENABLED:
        await message.answer("⚠️ Оплата Stars временно недоступна")
        return
    
    try:
        amount_rubles = amount_kopeks / 100
        stars_amount = TelegramStarsService.calculate_stars_from_rubles(amount_rubles)
        stars_rate = settings.get_stars_rate() 
        
        payment_service = PaymentService(message.bot)
        invoice_link = await payment_service.create_stars_invoice(
            amount_kopeks=amount_kopeks,
            description=f"Пополнение баланса на {texts.format_price(amount_kopeks)}",
            payload=f"balance_{db_user.id}_{amount_kopeks}"
        )
        
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="⭐ Оплатить", url=invoice_link)],
            [types.InlineKeyboardButton(text=texts.BACK, callback_data="balance_topup")]
        ])
        
        await message.answer(
            f"⭐ <b>Оплата через Telegram Stars</b>\n\n"
            f"💰 Сумма: {texts.format_price(amount_kopeks)}\n"
            f"⭐ К оплате: {stars_amount} звезд\n"
            f"📊 Курс: {stars_rate}₽ за звезду\n\n"
            f"Нажмите кнопку ниже для оплаты:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        
        await state.clear()
        
    except Exception as e:
        logger.error(f"Ошибка создания Stars invoice: {e}")
        await message.answer("⚠️ Ошибка создания платежа")