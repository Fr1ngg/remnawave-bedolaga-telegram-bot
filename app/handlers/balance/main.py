import html
import logging
from aiogram import Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.states import BalanceStates
from app.database.crud.user import add_user_balance
from app.database.crud.transaction import (
    get_user_transactions, get_user_transactions_count,
    create_transaction
)
from app.database.models import User, TransactionType, PaymentMethod
from app.keyboards.inline import (
    get_balance_keyboard, get_payment_methods_keyboard,
    get_back_keyboard, get_pagination_keyboard
)
from app.localization.texts import get_texts
from app.services.payment_service import PaymentService
from app.utils.pagination import paginate_list
from app.utils.decorators import error_handler

logger = logging.getLogger(__name__)

TRANSACTIONS_PER_PAGE = 10


def get_quick_amount_buttons(language: str) -> list:
    if not settings.YOOKASSA_QUICK_AMOUNT_SELECTION_ENABLED or settings.DISABLE_TOPUP_BUTTONS:
        return []
    
    buttons = []
    periods = settings.get_available_subscription_periods()
    
    periods = periods[:6]
    
    for period in periods:
        price_attr = f"PRICE_{period}_DAYS"
        if hasattr(settings, price_attr):
            price_kopeks = getattr(settings, price_attr)
            price_rubles = price_kopeks // 100
            
            callback_data = f"quick_amount_{price_kopeks}"
            
            buttons.append(
                types.InlineKeyboardButton(
                    text=f"{price_rubles} ₽ ({period} дней)",
                    callback_data=callback_data
                )
            )
    
    keyboard_rows = []
    for i in range(0, len(buttons), 2):
        keyboard_rows.append(buttons[i:i + 2])
    
    return keyboard_rows


@error_handler
async def show_balance_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    texts = get_texts(db_user.language)
    
    balance_text = texts.BALANCE_INFO.format(
        balance=texts.format_price(db_user.balance_kopeks)
    )
    
    await callback.message.edit_text(
        balance_text,
        reply_markup=get_balance_keyboard(db_user.language)
    )
    await callback.answer()


@error_handler
async def show_balance_history(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    page: int = 1
):
    texts = get_texts(db_user.language)
    
    offset = (page - 1) * TRANSACTIONS_PER_PAGE
    
    raw_transactions = await get_user_transactions(
        db, db_user.id, 
        limit=TRANSACTIONS_PER_PAGE * 3, 
        offset=offset
    )
    
    seen_transactions = set()
    unique_transactions = []
    
    for transaction in raw_transactions:
        rounded_time = transaction.created_at.replace(second=0, microsecond=0)
        transaction_key = (
            transaction.amount_kopeks,
            transaction.description,
            rounded_time
        )
        
        if transaction_key not in seen_transactions:
            seen_transactions.add(transaction_key)
            unique_transactions.append(transaction)
            
            if len(unique_transactions) >= TRANSACTIONS_PER_PAGE:
                break
    
    all_transactions = await get_user_transactions(db, db_user.id, limit=1000)
    seen_all = set()
    total_unique = 0
    
    for transaction in all_transactions:
        rounded_time = transaction.created_at.replace(second=0, microsecond=0)
        transaction_key = (
            transaction.amount_kopeks,
            transaction.description,
            rounded_time
        )
        if transaction_key not in seen_all:
            seen_all.add(transaction_key)
            total_unique += 1
    
    if not unique_transactions:
        await callback.message.edit_text(
            "📊 История операций пуста",
            reply_markup=get_back_keyboard(db_user.language)
        )
        await callback.answer()
        return
    
    text = "📊 <b>История операций</b>\n\n"
    
    for transaction in unique_transactions:
        emoji = "💰" if transaction.type == TransactionType.DEPOSIT.value else "💸"
        amount_text = f"+{texts.format_price(transaction.amount_kopeks)}" if transaction.type == TransactionType.DEPOSIT.value else f"-{texts.format_price(transaction.amount_kopeks)}"
        
        text += f"{emoji} {amount_text}\n"
        text += f"📝 {transaction.description}\n"
        text += f"📅 {transaction.created_at.strftime('%d.%m.%Y %H:%M')}\n\n"
    
    keyboard = []
    total_pages = (total_unique + TRANSACTIONS_PER_PAGE - 1) // TRANSACTIONS_PER_PAGE
    
    if total_pages > 1:
        pagination_row = get_pagination_keyboard(
            page, total_pages, "balance_history", db_user.language
        )
        keyboard.extend(pagination_row)
    
    keyboard.append([
        types.InlineKeyboardButton(text=texts.BACK, callback_data="menu_balance")
    ])
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="HTML"
    )
    await callback.answer()


@error_handler
async def handle_balance_history_pagination(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    page = int(callback.data.split('_')[-1])
    await show_balance_history(callback, db_user, db, page)


@error_handler
async def show_payment_methods(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext
):
    from app.utils.payment_utils import get_payment_methods_text
    
    texts = get_texts(db_user.language)
    payment_text = get_payment_methods_text(db_user.language)
    
    await callback.message.edit_text(
        payment_text,
        reply_markup=get_payment_methods_keyboard(0, db_user.language), 
        parse_mode="HTML"
    )
    await callback.answer()


@error_handler
async def handle_payment_methods_unavailable(
    callback: types.CallbackQuery,
    db_user: User
):
    texts = get_texts(db_user.language)
    
    await callback.answer(
        texts.t(
            "PAYMENT_METHODS_UNAVAILABLE_ALERT",
            "⚠️ В данный момент автоматические способы оплаты временно недоступны. Для пополнения баланса обратитесь в техподдержку.",
        ),
        show_alert=True
    )


@error_handler
async def handle_successful_topup_with_cart(
    user_id: int,
    amount_kopeks: int,
    bot,
    db: AsyncSession
):
    from app.database.crud.user import get_user_by_id
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from app.bot import dp
    
    user = await get_user_by_id(db, user_id)
    if not user:
        return
    
    storage = dp.storage
    key = StorageKey(bot_id=bot.id, chat_id=user.telegram_id, user_id=user.telegram_id)
    
    try:
        state_data = await storage.get_data(key)
        current_state = await storage.get_state(key)
        
        if (current_state == "SubscriptionStates:cart_saved_for_topup" and 
            state_data.get('saved_cart')):
            
            texts = get_texts(user.language)
            total_price = state_data.get('total_price', 0)
            
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(
                    text="🛒 Вернуться к оформлению подписки", 
                    callback_data="return_to_saved_cart"
                )],
                [types.InlineKeyboardButton(
                    text="💰 Мой баланс", 
                    callback_data="menu_balance"
                )],
                [types.InlineKeyboardButton(
                    text="🏠 Главное меню", 
                    callback_data="back_to_menu"
                )]
            ])
            
            success_text = (
                f"✅ Баланс пополнен на {texts.format_price(amount_kopeks)}!\n\n"
                f"💰 Текущий баланс: {texts.format_price(user.balance_kopeks)}\n\n"
                f"🛒 У вас есть сохраненная корзина подписки\n"
                f"Стоимость: {texts.format_price(total_price)}\n\n"
                f"Хотите продолжить оформление?"
            )
            
            await bot.send_message(
                chat_id=user.telegram_id,
                text=success_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            
    except Exception as e:
        logger.error(f"Ошибка обработки успешного пополнения с корзиной: {e}")


@error_handler
async def request_support_topup(
    callback: types.CallbackQuery,
    db_user: User
):
    texts = get_texts(db_user.language)
    
    support_text = f"""
🛠️ <b>Пополнение через поддержку</b>

Для пополнения баланса обратитесь в техподдержку:
{settings.get_support_contact_display_html()}

Укажите:
• ID: {db_user.telegram_id}
• Сумму пополнения
• Способ оплаты

⏰ Время обработки: 1-24 часа

<b>Доступные способы:</b>
• Криптовалюта
• Переводы между банками
• Другие платежные системы
"""
    
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(
            text="💬 Написать в поддержку",
            url=settings.get_support_contact_url() or "https://t.me/"
        )],
        [types.InlineKeyboardButton(text=texts.BACK, callback_data="balance_topup")]
    ])
    
    await callback.message.edit_text(
        support_text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()


@error_handler
async def process_topup_amount(
    message: types.Message,
    db_user: User,
    state: FSMContext
):
    texts = get_texts(db_user.language)

    try:
        if not message.text:
            if message.successful_payment:
                logger.info(
                    "Получено сообщение об успешном платеже без текста, "
                    "обработчик суммы пополнения завершает работу"
                )
                await state.clear()
                return

            await message.answer(
                texts.INVALID_AMOUNT,
                reply_markup=get_back_keyboard(db_user.language)
            )
            return

        amount_text = message.text.strip()
        if not amount_text:
            await message.answer(
                texts.INVALID_AMOUNT,
                reply_markup=get_back_keyboard(db_user.language)
            )
            return

        amount_rubles = float(amount_text.replace(',', '.'))

        if amount_rubles < 1:
            await message.answer("Минимальная сумма пополнения: 1 ₽")
            return
        
        if amount_rubles > 50000:
            await message.answer("Максимальная сумма пополнения: 50,000 ₽")
            return
        
        amount_kopeks = int(amount_rubles * 100)
        data = await state.get_data()
        payment_method = data.get("payment_method", "stars")
        
        if payment_method in ["yookassa", "yookassa_sbp"]:
            if amount_kopeks < settings.YOOKASSA_MIN_AMOUNT_KOPEKS:
                min_rubles = settings.YOOKASSA_MIN_AMOUNT_KOPEKS / 100
                await message.answer(f"❌ Минимальная сумма для оплаты через YooKassa: {min_rubles:.0f} ₽")
                return
            
            if amount_kopeks > settings.YOOKASSA_MAX_AMOUNT_KOPEKS:
                max_rubles = settings.YOOKASSA_MAX_AMOUNT_KOPEKS / 100
                await message.answer(f"❌ Максимальная сумма для оплаты через YooKassa: {max_rubles:,.0f} ₽".replace(',', ' '))
                return
        
        if payment_method == "stars":
            from .stars import process_stars_payment_amount
            await process_stars_payment_amount(message, db_user, amount_kopeks, state)
        elif payment_method == "yookassa":
            from app.database.database import AsyncSessionLocal
            from .yookassa import process_yookassa_payment_amount
            async with AsyncSessionLocal() as db:
                await process_yookassa_payment_amount(message, db_user, db, amount_kopeks, state)
        elif payment_method == "yookassa_sbp":
            from app.database.database import AsyncSessionLocal
            from .yookassa import process_yookassa_sbp_payment_amount
            async with AsyncSessionLocal() as db:
                await process_yookassa_sbp_payment_amount(message, db_user, db, amount_kopeks, state)
        elif payment_method == "mulenpay":
            from app.database.database import AsyncSessionLocal
            from .mulenpay import process_mulenpay_payment_amount
            async with AsyncSessionLocal() as db:
                await process_mulenpay_payment_amount(message, db_user, db, amount_kopeks, state)
        elif payment_method == "wata":
            from app.database.database import AsyncSessionLocal
            from .wata import process_wata_payment_amount

            async with AsyncSessionLocal() as db:
                await process_wata_payment_amount(message, db_user, db, amount_kopeks, state)
        elif payment_method == "pal24":
            from app.database.database import AsyncSessionLocal
            from .pal24 import process_pal24_payment_amount
            async with AsyncSessionLocal() as db:
                await process_pal24_payment_amount(message, db_user, db, amount_kopeks, state)
        elif payment_method == "cryptobot":
            from app.database.database import AsyncSessionLocal
            from .cryptobot import process_cryptobot_payment_amount
            async with AsyncSessionLocal() as db:
                await process_cryptobot_payment_amount(message, db_user, db, amount_kopeks, state)
        else:
            await message.answer("Неизвестный способ оплаты")
        
    except ValueError:
        await message.answer(
            texts.INVALID_AMOUNT,
            reply_markup=get_back_keyboard(db_user.language)
        )


@error_handler
async def handle_sbp_payment(
    callback: types.CallbackQuery,
    db: AsyncSession
):
    try:
        local_payment_id = int(callback.data.split('_')[-1])
        
        from app.database.crud.yookassa import get_yookassa_payment_by_local_id
        payment = await get_yookassa_payment_by_local_id(db, local_payment_id)
        
        if not payment:
            await callback.answer("❌ Платеж не найден", show_alert=True)
            return
        
        import json
        metadata = json.loads(payment.metadata_json) if payment.metadata_json else {}
        confirmation_token = metadata.get("confirmation_token")
        
        if not confirmation_token:
            await callback.answer("❌ Токен подтверждения не найден", show_alert=True)
            return
        
        await callback.message.answer(
            f"Для оплаты через СБП откройте приложение вашего банка и подтвердите платеж.\\n\\n"
            f"Если у вас не открылось банковское приложение автоматически, вы можете:\\n"
            f"1. Скопировать этот токен: <code>{confirmation_token}</code>\\n"
            f"2. Открыть приложение вашего банка\\n"
            f"3. Найти функцию оплаты по токену\\n"
            f"4. Вставить токен и подтвердить платеж",
            parse_mode="HTML"
        )
        
        await callback.answer("Информация об оплате отправлена", show_alert=True)
        
    except Exception as e:
        logger.error(f"Ошибка обработки embedded платежа СБП: {e}")
        await callback.answer("❌ Ошибка обработки платежа", show_alert=True)


@error_handler
async def handle_quick_amount_selection(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext
):
    """
    Обработчик выбора суммы через кнопки быстрого выбора
    """
    # Извлекаем сумму из callback_data
    try:
        amount_kopeks = int(callback.data.split('_')[-1])
        amount_rubles = amount_kopeks / 100
        
        # Получаем метод оплаты из состояния
        data = await state.get_data()
        payment_method = data.get("payment_method", "yookassa")
        
        # Проверяем, какой метод оплаты был выбран и вызываем соответствующий обработчик
        if payment_method == "yookassa":
            from app.database.database import AsyncSessionLocal
            from .yookassa import process_yookassa_payment_amount
            async with AsyncSessionLocal() as db:
                await process_yookassa_payment_amount(
                    callback.message, db_user, db, amount_kopeks, state
                )
        elif payment_method == "yookassa_sbp":
            from app.database.database import AsyncSessionLocal
            from .yookassa import process_yookassa_sbp_payment_amount
            async with AsyncSessionLocal() as db:
                await process_yookassa_sbp_payment_amount(
                    callback.message, db_user, db, amount_kopeks, state
                )
        elif payment_method == "mulenpay":
            from app.database.database import AsyncSessionLocal
            from .mulenpay import process_mulenpay_payment_amount
            async with AsyncSessionLocal() as db:
                await process_mulenpay_payment_amount(
                    callback.message, db_user, db, amount_kopeks, state
                )
        elif payment_method == "wata":
            from app.database.database import AsyncSessionLocal
            from .wata import process_wata_payment_amount

            async with AsyncSessionLocal() as db:
                await process_wata_payment_amount(
                    callback.message, db_user, db, amount_kopeks, state
                )
        elif payment_method == "pal24":
            from app.database.database import AsyncSessionLocal
            from .pal24 import process_pal24_payment_amount
            async with AsyncSessionLocal() as db:
                await process_pal24_payment_amount(
                    callback.message, db_user, db, amount_kopeks, state
                )
        elif payment_method == "cryptobot":
            from app.database.database import AsyncSessionLocal
            from .cryptobot import process_cryptobot_payment_amount

            async with AsyncSessionLocal() as db:
                await process_cryptobot_payment_amount(
                    callback.message, db_user, db, amount_kopeks, state
                )
        elif payment_method == "stars":
            from .stars import process_stars_payment_amount

            await process_stars_payment_amount(
                callback.message, db_user, amount_kopeks, state
            )
        else:
            await callback.answer("❌ Неизвестный способ оплаты", show_alert=True)
            return

    except ValueError:
        await callback.answer("❌ Ошибка обработки суммы", show_alert=True)
    except Exception as e:
        logger.error(f"Ошибка обработки быстрого выбора суммы: {e}")
        await callback.answer("❌ Ошибка обработки запроса", show_alert=True)


@error_handler
async def handle_topup_amount_callback(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
):
    try:
        _, method, amount_str = callback.data.split("|", 2)
        amount_kopeks = int(amount_str)
    except ValueError:
        await callback.answer("❌ Некорректный запрос", show_alert=True)
        return

    if amount_kopeks <= 0:
        await callback.answer("❌ Некорректная сумма", show_alert=True)
        return

    try:
        if method == "yookassa":
            from app.database.database import AsyncSessionLocal
            from .yookassa import process_yookassa_payment_amount
            async with AsyncSessionLocal() as db:
                await process_yookassa_payment_amount(
                    callback.message, db_user, db, amount_kopeks, state
                )
        elif method == "yookassa_sbp":
            from app.database.database import AsyncSessionLocal
            from .yookassa import process_yookassa_sbp_payment_amount
            async with AsyncSessionLocal() as db:
                await process_yookassa_sbp_payment_amount(
                    callback.message, db_user, db, amount_kopeks, state
                )
        elif method == "mulenpay":
            from app.database.database import AsyncSessionLocal
            from .mulenpay import process_mulenpay_payment_amount
            async with AsyncSessionLocal() as db:
                await process_mulenpay_payment_amount(
                    callback.message, db_user, db, amount_kopeks, state
                )
        elif method == "pal24":
            from app.database.database import AsyncSessionLocal
            from .pal24 import process_pal24_payment_amount
            async with AsyncSessionLocal() as db:
                await process_pal24_payment_amount(
                    callback.message, db_user, db, amount_kopeks, state
                )
        elif method == "cryptobot":
            from app.database.database import AsyncSessionLocal
            from .cryptobot import process_cryptobot_payment_amount
            async with AsyncSessionLocal() as db:
                await process_cryptobot_payment_amount(
                    callback.message, db_user, db, amount_kopeks, state
                )
        elif method == "stars":
            from .stars import process_stars_payment_amount
            await process_stars_payment_amount(
                callback.message, db_user, amount_kopeks, state
            )
        elif method == "tribute":
            from .tribute import start_tribute_payment
            await start_tribute_payment(callback, db_user)
            return
        else:
            await callback.answer("❌ Неизвестный способ оплаты", show_alert=True)
            return

        await callback.answer()

    except Exception as error:
        logger.error(f"Ошибка быстрого пополнения: {error}")
        await callback.answer("❌ Ошибка обработки запроса", show_alert=True)


def register_balance_handlers(dp: Dispatcher):
    
    dp.callback_query.register(
        show_balance_menu,
        F.data == "menu_balance"
    )
    
    dp.callback_query.register(
        show_balance_history,
        F.data == "balance_history"
    )
    
    dp.callback_query.register(
        handle_balance_history_pagination,
        F.data.startswith("balance_history_page_")
    )
    
    dp.callback_query.register(
        show_payment_methods,
        F.data == "balance_topup"
    )
    
    from .stars import start_stars_payment
    dp.callback_query.register(
        start_stars_payment,
        F.data == "topup_stars"
    )
    
    from .yookassa import start_yookassa_payment
    dp.callback_query.register(
        start_yookassa_payment,
        F.data == "topup_yookassa"
    )
    
    from .yookassa import start_yookassa_sbp_payment
    dp.callback_query.register(
        start_yookassa_sbp_payment,
        F.data == "topup_yookassa_sbp"
    )

    from .mulenpay import start_mulenpay_payment
    dp.callback_query.register(
        start_mulenpay_payment,
        F.data == "topup_mulenpay"
    )

    from .wata import start_wata_payment
    dp.callback_query.register(
        start_wata_payment,
        F.data == "topup_wata"
    )

    from .pal24 import start_pal24_payment
    dp.callback_query.register(
        start_pal24_payment,
        F.data == "topup_pal24"
    )
    from .pal24 import handle_pal24_method_selection
    dp.callback_query.register(
        handle_pal24_method_selection,
        F.data.startswith("pal24_method_"),
    )

    from .yookassa import check_yookassa_payment_status
    dp.callback_query.register(
        check_yookassa_payment_status,
        F.data.startswith("check_yookassa_")
    )

    from .tribute import start_tribute_payment
    dp.callback_query.register(
        start_tribute_payment,
        F.data == "topup_tribute"
    )
    
    dp.callback_query.register(
        request_support_topup,
        F.data == "topup_support"
    )
    
    from .yookassa import check_yookassa_payment_status
    dp.callback_query.register(
        check_yookassa_payment_status,
        F.data.startswith("check_yookassa_")
    )
    
    dp.message.register(
        process_topup_amount,
        BalanceStates.waiting_for_amount
    )

    from .cryptobot import start_cryptobot_payment
    dp.callback_query.register(
        start_cryptobot_payment,
        F.data == "topup_cryptobot"
    )
    
    from .cryptobot import check_cryptobot_payment_status
    dp.callback_query.register(
        check_cryptobot_payment_status,
        F.data.startswith("check_cryptobot_")
    )

    from .mulenpay import check_mulenpay_payment_status
    dp.callback_query.register(
        check_mulenpay_payment_status,
        F.data.startswith("check_mulenpay_")
    )

    from .wata import check_wata_payment_status
    dp.callback_query.register(
        check_wata_payment_status,
        F.data.startswith("check_wata_")
    )

    from .pal24 import check_pal24_payment_status
    dp.callback_query.register(
        check_pal24_payment_status,
        F.data.startswith("check_pal24_")
    )

    dp.callback_query.register(
        handle_payment_methods_unavailable,
        F.data == "payment_methods_unavailable"
    )
    
    # Регистрируем обработчик для кнопок быстрого выбора суммы
    dp.callback_query.register(
        handle_quick_amount_selection,
        F.data.startswith("quick_amount_")
    )

    dp.callback_query.register(
        handle_topup_amount_callback,
        F.data.startswith("topup_amount|")
    )