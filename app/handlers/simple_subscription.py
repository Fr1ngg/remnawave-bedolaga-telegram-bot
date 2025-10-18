"""Обработчики для простой покупки подписки."""
import logging
from typing import Optional, Dict, Any
from aiogram import types, F
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.keyboards.inline import get_back_keyboard, get_happ_download_button_row
from app.localization.texts import get_texts
from app.services.payment_service import PaymentService
from app.services.subscription_purchase_service import SubscriptionPurchaseService
from app.utils.decorators import error_handler
from app.states import SubscriptionStates
from app.utils.subscription_utils import get_display_subscription_link

logger = logging.getLogger(__name__)


@error_handler
async def start_simple_subscription_purchase(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    """Начинает процесс простой покупки подписки."""
    texts = get_texts(db_user.language)
    
    if not settings.SIMPLE_SUBSCRIPTION_ENABLED:
        await callback.answer("❌ Простая покупка подписки временно недоступна", show_alert=True)
        return
    
    # Проверяем, есть ли у пользователя активная подписка
    from app.database.crud.subscription import get_subscription_by_user_id
    current_subscription = await get_subscription_by_user_id(db, db_user.id)
    
    if current_subscription and current_subscription.is_active:
        await callback.answer("❌ У вас уже есть активная подписка", show_alert=True)
        return
    
    # Подготовим параметры простой подписки
    subscription_params = {
        "period_days": settings.SIMPLE_SUBSCRIPTION_PERIOD_DAYS,
        "device_limit": settings.SIMPLE_SUBSCRIPTION_DEVICE_LIMIT,
        "traffic_limit_gb": settings.SIMPLE_SUBSCRIPTION_TRAFFIC_GB,
        "squad_uuid": settings.SIMPLE_SUBSCRIPTION_SQUAD_UUID
    }
    
    # Сохраняем параметры в состояние
    await state.update_data(subscription_params=subscription_params)
    
    # Проверяем баланс пользователя
    user_balance_kopeks = getattr(db_user, "balance_kopeks", 0)
    # Рассчитываем цену подписки
    price_kopeks = _calculate_simple_subscription_price(subscription_params)
    period_days = subscription_params["period_days"]
    recorded_price = getattr(settings, f"PRICE_{period_days}_DAYS", price_kopeks)
    direct_purchase_min_balance = recorded_price
    extra_components = []
    traffic_limit = subscription_params.get("traffic_limit_gb", 0)
    if traffic_limit and traffic_limit > 0:
        traffic_price = settings.get_traffic_price(traffic_limit)
        direct_purchase_min_balance += traffic_price
        extra_components.append(f"traffic={traffic_limit}GB->{traffic_price}")

    device_limit = subscription_params.get("device_limit", 1)
    if device_limit and device_limit > settings.DEFAULT_DEVICE_LIMIT:
        additional_devices = device_limit - settings.DEFAULT_DEVICE_LIMIT
        devices_price = additional_devices * settings.PRICE_PER_DEVICE
        direct_purchase_min_balance += devices_price
        extra_components.append(f"devices+{additional_devices}->{devices_price}")
    logger.warning(
        "SIMPLE_SUBSCRIPTION_DEBUG_START | user=%s | period=%s | base_price=%s | recorded_price=%s | extras=%s | total=%s | env_PRICE_30=%s",
        db_user.id,
        period_days,
        price_kopeks,
        recorded_price,
        ",".join(extra_components) if extra_components else "none",
        direct_purchase_min_balance,
        getattr(settings, "PRICE_30_DAYS", None),
    )

    can_pay_from_balance = user_balance_kopeks >= direct_purchase_min_balance
    logger.warning(
        "SIMPLE_SUBSCRIPTION_DEBUG_START_BALANCE | user=%s | balance=%s | min_required=%s | can_pay=%s",
        db_user.id,
        user_balance_kopeks,
        direct_purchase_min_balance,
        can_pay_from_balance,
    )

    message_text = (
        f"⚡ <b>Простая покупка подписки</b>\n\n"
        f"📅 Период: {subscription_params['period_days']} дней\n"
        f"📱 Устройства: {subscription_params['device_limit']}\n"
        f"📊 Трафик: {'Безлимит' if subscription_params['traffic_limit_gb'] == 0 else f'{subscription_params['traffic_limit_gb']} ГБ'}\n"
        f"🌍 Сервер: {'Любой доступный' if not subscription_params['squad_uuid'] else 'Выбранный'}\n\n"
        f"💰 Стоимость: {settings.format_price(price_kopeks)}\n"
        f"💳 Ваш баланс: {settings.format_price(user_balance_kopeks)}\n\n"
        + (
            "Вы можете оплатить подписку с баланса или выбрать другой способ оплаты."
            if can_pay_from_balance
            else "Баланс пока недостаточный для мгновенной оплаты. Выберите подходящий способ оплаты:"
        )
    )

    methods_keyboard = _get_simple_subscription_payment_keyboard(db_user.language)
    keyboard_rows = []

    if can_pay_from_balance:
        keyboard_rows.append([
            types.InlineKeyboardButton(
                text="✅ Оплатить с баланса",
                callback_data="simple_subscription_pay_with_balance",
            )
        ])

    keyboard_rows.extend(methods_keyboard.inline_keyboard)

    keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    
    await callback.message.edit_text(
        message_text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    
    await state.set_state(SubscriptionStates.waiting_for_simple_subscription_payment_method)
    await callback.answer()


def _calculate_simple_subscription_price(params: dict) -> int:
    """Рассчитывает цену простой подписки."""
    period_days = params.get("period_days", 30)
    attr_name = f"PRICE_{period_days}_DAYS"
    attr_value = getattr(settings, attr_name, None)

    logger.warning(
        "SIMPLE_SUBSCRIPTION_DEBUG_PRICE_FUNC | period=%s | attr=%s | attr_value=%s | base_price=%s",
        period_days,
        attr_name,
        attr_value,
        settings.BASE_SUBSCRIPTION_PRICE,
    )

    # Получаем цену для стандартного периода
    if attr_value is not None:
        return attr_value
    else:
        # Если нет цены для конкретного периода, используем базовую цену
        return settings.BASE_SUBSCRIPTION_PRICE


def _get_simple_subscription_payment_keyboard(language: str) -> types.InlineKeyboardMarkup:
    """Создает клавиатуру с методами оплаты для простой подписки."""
    texts = get_texts(language)
    keyboard = []
    
    # Добавляем доступные методы оплаты
    if settings.TELEGRAM_STARS_ENABLED:
        keyboard.append([types.InlineKeyboardButton(
            text="⭐ Telegram Stars",
            callback_data="simple_subscription_stars"
        )])
    
    if settings.is_yookassa_enabled():
        yookassa_methods = []
        if settings.YOOKASSA_SBP_ENABLED:
            yookassa_methods.append(types.InlineKeyboardButton(
                text="🏦 YooKassa (СБП)",
                callback_data="simple_subscription_yookassa_sbp"
            ))
        yookassa_methods.append(types.InlineKeyboardButton(
            text="💳 YooKassa (Карта)",
            callback_data="simple_subscription_yookassa"
        ))
        if yookassa_methods:
            keyboard.append(yookassa_methods)
    
    if settings.is_cryptobot_enabled():
        keyboard.append([types.InlineKeyboardButton(
            text="🪙 CryptoBot",
            callback_data="simple_subscription_cryptobot"
        )])
    
    if settings.is_mulenpay_enabled():
        keyboard.append([types.InlineKeyboardButton(
            text="💳 MulenPay",
            callback_data="simple_subscription_mulenpay"
        )])
    
    if settings.is_pal24_enabled():
        keyboard.append([types.InlineKeyboardButton(
            text="💳 PayPalych",
            callback_data="simple_subscription_pal24"
        )])
    
    if settings.is_wata_enabled():
        keyboard.append([types.InlineKeyboardButton(
            text="💳 WATA",
            callback_data="simple_subscription_wata"
        )])
    
    # Кнопка назад
    keyboard.append([types.InlineKeyboardButton(
        text=texts.BACK,
        callback_data="subscription_purchase"
    )])
    
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)


@error_handler
async def handle_simple_subscription_pay_with_balance(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    """Обрабатывает оплату простой подписки с баланса."""
    texts = get_texts(db_user.language)
    
    data = await state.get_data()
    subscription_params = data.get("subscription_params", {})
    
    if not subscription_params:
        await callback.answer("❌ Данные подписки устарели. Пожалуйста, начните сначала.", show_alert=True)
        return
    
    # Рассчитываем цену подписки
    price_kopeks = _calculate_simple_subscription_price(subscription_params)
    recorded_price = getattr(settings, f"PRICE_{subscription_params['period_days']}_DAYS", price_kopeks)
    total_required = recorded_price
    extras = []
    traffic_limit = subscription_params.get("traffic_limit_gb", 0)
    if traffic_limit and traffic_limit > 0:
        traffic_price = settings.get_traffic_price(traffic_limit)
        total_required += traffic_price
        extras.append(f"traffic={traffic_limit}GB->{traffic_price}")
    device_limit = subscription_params.get("device_limit", 1)
    if device_limit and device_limit > settings.DEFAULT_DEVICE_LIMIT:
        additional_devices = device_limit - settings.DEFAULT_DEVICE_LIMIT
        devices_price = additional_devices * settings.PRICE_PER_DEVICE
        total_required += devices_price
        extras.append(f"devices+{additional_devices}->{devices_price}")
    logger.warning(
        "SIMPLE_SUBSCRIPTION_DEBUG_PAY_BALANCE | user=%s | period=%s | base_price=%s | extras=%s | total_required=%s | balance=%s",
        db_user.id,
        subscription_params["period_days"],
        price_kopeks,
        ",".join(extras) if extras else "none",
        total_required,
        getattr(db_user, "balance_kopeks", 0),
    )

    # Проверяем баланс пользователя
    user_balance_kopeks = getattr(db_user, "balance_kopeks", 0)

    if user_balance_kopeks < total_required:
        await callback.answer("❌ Недостаточно средств на балансе для оплаты подписки", show_alert=True)
        return
    
    try:
        # Списываем средства с баланса пользователя
        from app.database.crud.user import subtract_user_balance
        success = await subtract_user_balance(
            db,
            db_user,
            price_kopeks,
            f"Оплата подписки на {subscription_params['period_days']} дней",
            consume_promo_offer=False,
        )
        
        if not success:
            await callback.answer("❌ Ошибка списания средств с баланса", show_alert=True)
            return
        
        # Проверяем, есть ли у пользователя уже подписка
        from app.database.crud.subscription import get_subscription_by_user_id, extend_subscription
        
        existing_subscription = await get_subscription_by_user_id(db, db_user.id)
        
        if existing_subscription:
            # Если подписка уже существует, продлеваем её
            subscription = await extend_subscription(
                db=db,
                subscription=existing_subscription,
                days=subscription_params["period_days"]
            )
            # Обновляем параметры подписки
            subscription.traffic_limit_gb = subscription_params["traffic_limit_gb"]
            subscription.device_limit = subscription_params["device_limit"]
            if subscription_params["squad_uuid"]:
                subscription.connected_squads = [subscription_params["squad_uuid"]]
            
            await db.commit()
            await db.refresh(subscription)
        else:
            # Если подписки нет, создаём новую
            from app.database.crud.subscription import create_paid_subscription
            subscription = await create_paid_subscription(
                db=db,
                user_id=db_user.id,
                duration_days=subscription_params["period_days"],
                traffic_limit_gb=subscription_params["traffic_limit_gb"],
                device_limit=subscription_params["device_limit"],
                connected_squads=[subscription_params["squad_uuid"]] if subscription_params["squad_uuid"] else [],
                update_server_counters=True,
            )
        
        if not subscription:
            # Возвращаем средства на баланс в случае ошибки
            from app.services.payment_service import add_user_balance
            await add_user_balance(
                db,
                db_user.id,
                price_kopeks,
                f"Возврат средств за неудавшуюся подписку на {subscription_params['period_days']} дней",
            )
            await callback.answer("❌ Ошибка создания подписки. Средства возвращены на баланс.", show_alert=True)
            return
        
        # Обновляем баланс пользователя
        await db.refresh(db_user)

        # Обновляем или создаём ссылку подписки в RemnaWave
        try:
            from app.services.subscription_service import SubscriptionService
            subscription_service = SubscriptionService()
            remnawave_user = await subscription_service.create_remnawave_user(db, subscription)
            if remnawave_user:
                await db.refresh(subscription)
        except Exception as sync_error:
            logger.error(f"Ошибка синхронизации подписки с RemnaWave для пользователя {db_user.id}: {sync_error}", exc_info=True)
        
        # Отправляем уведомление об успешной покупке
        success_message = (
            f"✅ <b>Подписка успешно активирована!</b>\n\n"
            f"📅 Период: {subscription_params['period_days']} дней\n"
            f"📱 Устройства: {subscription_params['device_limit']}\n"
            f"📊 Трафик: {'Безлимит' if subscription_params['traffic_limit_gb'] == 0 else f'{subscription_params['traffic_limit_gb']} ГБ'}\n"
            f"🌍 Сервер: {'Любой доступный' if not subscription_params['squad_uuid'] else 'Выбранный'}\n\n"
            f"💰 Списано с баланса: {settings.format_price(price_kopeks)}\n"
            f"💳 Ваш баланс: {settings.format_price(db_user.balance_kopeks)}\n\n"
            f"🔗 Для подключения перейдите в раздел 'Подключиться'"
        )
        
        connect_mode = settings.CONNECT_BUTTON_MODE
        subscription_link = get_display_subscription_link(subscription)
        connect_button_text = texts.t("CONNECT_BUTTON", "🔗 Подключиться")

        def _fallback_connect_button() -> types.InlineKeyboardButton:
            return types.InlineKeyboardButton(
                text=connect_button_text,
                callback_data="subscription_connect",
            )

        if connect_mode == "miniapp_subscription":
            if subscription_link:
                connect_row = [
                    types.InlineKeyboardButton(
                        text=connect_button_text,
                        web_app=types.WebAppInfo(url=subscription_link),
                    )
                ]
            else:
                connect_row = [_fallback_connect_button()]
        elif connect_mode == "miniapp_custom":
            custom_url = settings.MINIAPP_CUSTOM_URL
            if custom_url:
                connect_row = [
                    types.InlineKeyboardButton(
                        text=connect_button_text,
                        web_app=types.WebAppInfo(url=custom_url),
                    )
                ]
            else:
                connect_row = [_fallback_connect_button()]
        elif connect_mode == "link":
            if subscription_link:
                connect_row = [
                    types.InlineKeyboardButton(
                        text=connect_button_text,
                        url=subscription_link,
                    )
                ]
            else:
                connect_row = [_fallback_connect_button()]
        elif connect_mode == "happ_cryptolink":
            if subscription_link:
                connect_row = [
                    types.InlineKeyboardButton(
                        text=connect_button_text,
                        callback_data="open_subscription_link",
                    )
                ]
            else:
                connect_row = [_fallback_connect_button()]
        else:
            connect_row = [_fallback_connect_button()]

        keyboard_rows = [connect_row]

        happ_row = get_happ_download_button_row(texts)
        if happ_row:
            keyboard_rows.append(happ_row)

        keyboard_rows.append(
            [types.InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_menu")]
        )

        keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

        await callback.message.edit_text(
            success_message,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        
        # Отправляем уведомление админам
        try:
            from app.services.admin_notification_service import AdminNotificationService
            notification_service = AdminNotificationService(callback.bot)
            await notification_service.send_subscription_purchase_notification(
                db,
                db_user,
                subscription,
                None,  # transaction
                subscription_params["period_days"],
                False,  # was_trial_conversion
                amount_kopeks=price_kopeks,
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления админам о покупке: {e}")
        
        await state.clear()
        await callback.answer()

        logger.info(f"Пользователь {db_user.telegram_id} успешно купил подписку с баланса на {price_kopeks/100}₽")

    except Exception as error:
        logger.error(
            "Ошибка оплаты простой подписки с баланса для пользователя %s: %s",
            db_user.id,
            error,
            exc_info=True,
        )
        await callback.answer(
            "❌ Ошибка оплаты подписки. Попробуйте позже или обратитесь в поддержку.",
            show_alert=True,
        )
        await state.clear()


@error_handler
async def handle_simple_subscription_pay_with_balance_disabled(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    """Показывает уведомление, если баланса недостаточно для прямой оплаты."""
    await callback.answer(
        "❌ Недостаточно средств на балансе. Пополните баланс или выберите другой способ оплаты.",
        show_alert=True,
    )


@error_handler
async def handle_simple_subscription_other_payment_methods(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    """Обрабатывает выбор других способов оплаты."""
    texts = get_texts(db_user.language)
    
    data = await state.get_data()
    subscription_params = data.get("subscription_params", {})
    
    if not subscription_params:
        await callback.answer("❌ Данные подписки устарели. Пожалуйста, начните сначала.", show_alert=True)
        return
    
    # Рассчитываем цену подписки
    price_kopeks = _calculate_simple_subscription_price(subscription_params)
    
    user_balance_kopeks = getattr(db_user, "balance_kopeks", 0)
    recorded_price = getattr(settings, f"PRICE_{subscription_params['period_days']}_DAYS", price_kopeks)
    total_required = recorded_price
    if subscription_params.get("traffic_limit_gb", 0) > 0:
        total_required += settings.get_traffic_price(subscription_params["traffic_limit_gb"])
    if subscription_params.get("device_limit", 1) > settings.DEFAULT_DEVICE_LIMIT:
        additional_devices = subscription_params["device_limit"] - settings.DEFAULT_DEVICE_LIMIT
        total_required += additional_devices * settings.PRICE_PER_DEVICE
    can_pay_from_balance = user_balance_kopeks >= total_required
    logger.warning(
        "SIMPLE_SUBSCRIPTION_DEBUG_METHODS | user=%s | balance=%s | base_price=%s | total_required=%s | can_pay=%s",
        db_user.id,
        user_balance_kopeks,
        price_kopeks,
        total_required,
        can_pay_from_balance,
    )

    # Отображаем доступные методы оплаты
    message_text = (
        f"💳 <b>Оплата подписки</b>\n\n"
        f"📅 Период: {subscription_params['period_days']} дней\n"
        f"📱 Устройства: {subscription_params['device_limit']}\n"
        f"📊 Трафик: {'Безлимит' if subscription_params['traffic_limit_gb'] == 0 else f'{subscription_params['traffic_limit_gb']} ГБ'}\n"
        f"🌍 Сервер: {'Любой доступный' if not subscription_params['squad_uuid'] else 'Выбранный'}\n\n"
        f"💰 Стоимость: {settings.format_price(price_kopeks)}\n\n"
        + (
            "Вы можете оплатить подписку с баланса или выбрать другой способ оплаты:"
            if can_pay_from_balance
            else "Выберите подходящий способ оплаты:"
        )
    )
    
    base_keyboard = _get_simple_subscription_payment_keyboard(db_user.language)
    keyboard_rows = []
    
    if can_pay_from_balance:
        keyboard_rows.append([
            types.InlineKeyboardButton(
                text="✅ Оплатить с баланса",
                callback_data="simple_subscription_pay_with_balance"
            )
        ])
    
    keyboard_rows.extend(base_keyboard.inline_keyboard)
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    
    await callback.message.edit_text(
        message_text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    
    await callback.answer()


@error_handler
async def handle_simple_subscription_payment_method(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    """Обрабатывает выбор метода оплаты для простой подписки."""
    texts = get_texts(db_user.language)
    
    data = await state.get_data()
    subscription_params = data.get("subscription_params", {})
    
    if not subscription_params:
        await callback.answer("❌ Данные подписки устарели. Пожалуйста, начните сначала.", show_alert=True)
        return
    
    # Рассчитываем цену подписки
    price_kopeks = _calculate_simple_subscription_price(subscription_params)
    
    payment_method = callback.data.replace("simple_subscription_", "")
    
    try:
        payment_service = PaymentService(callback.bot)
        
        if payment_method == "stars":
            # Оплата через Telegram Stars
            stars_count = settings.rubles_to_stars(settings.kopeks_to_rubles(price_kopeks))
            
            await callback.bot.send_invoice(
                chat_id=callback.from_user.id,
                title=f"Подписка на {subscription_params['period_days']} дней",
                description=(
                    f"Простая покупка подписки\n"
                    f"Период: {subscription_params['period_days']} дней\n"
                    f"Устройства: {subscription_params['device_limit']}\n"
                    f"Трафик: {'Безлимит' if subscription_params['traffic_limit_gb'] == 0 else f'{subscription_params['traffic_limit_gb']} ГБ'}"
                ),
                payload=f"simple_sub_{db_user.id}_{subscription_params['period_days']}",
                provider_token="",  # Пустой токен для Telegram Stars
                currency="XTR",  # Telegram Stars
                prices=[types.LabeledPrice(label="Подписка", amount=stars_count)]
            )
            
            await state.clear()
            await callback.answer()
            
        elif payment_method in ["yookassa", "yookassa_sbp"]:
            # Оплата через YooKassa
            if not settings.is_yookassa_enabled():
                await callback.answer("❌ Оплата через YooKassa временно недоступна", show_alert=True)
                return
            
            if payment_method == "yookassa_sbp" and not settings.YOOKASSA_SBP_ENABLED:
                await callback.answer("❌ Оплата через СБП временно недоступна", show_alert=True)
                return
            
            # Создаем заказ на подписку
            purchase_service = SubscriptionPurchaseService()
            
            order = await purchase_service.create_subscription_order(
                db=db,
                user_id=db_user.id,
                period_days=subscription_params["period_days"],
                device_limit=subscription_params["device_limit"],
                traffic_limit_gb=subscription_params["traffic_limit_gb"],
                squad_uuid=subscription_params["squad_uuid"],
                payment_method="yookassa_sbp" if payment_method == "yookassa_sbp" else "yookassa",
                total_price_kopeks=price_kopeks
            )
            
            if not order:
                await callback.answer("❌ Ошибка создания заказа", show_alert=True)
                return
            
            # Создаем платеж через YooKassa
            if payment_method == "yookassa_sbp":
                payment_result = await payment_service.create_yookassa_sbp_payment(
                    db=db,
                    user_id=db_user.id,
                    amount_kopeks=price_kopeks,
                    description=f"Оплата подписки на {subscription_params['period_days']} дней",
                    receipt_email=db_user.email if hasattr(db_user, 'email') and db_user.email else None,
                    receipt_phone=db_user.phone if hasattr(db_user, 'phone') and db_user.phone else None,
                    metadata={
                        "user_telegram_id": str(db_user.telegram_id),
                        "user_username": db_user.username or "",
                        "order_id": str(order.id),
                        "subscription_period": str(subscription_params["period_days"]),
                        "payment_purpose": "simple_subscription_purchase"
                    }
                )
            else:
                payment_result = await payment_service.create_yookassa_payment(
                    db=db,
                    user_id=db_user.id,
                    amount_kopeks=price_kopeks,
                    description=f"Оплата подписки на {subscription_params['period_days']} дней",
                    receipt_email=db_user.email if hasattr(db_user, 'email') and db_user.email else None,
                    receipt_phone=db_user.phone if hasattr(db_user, 'phone') and db_user.phone else None,
                    metadata={
                        "user_telegram_id": str(db_user.telegram_id),
                        "user_username": db_user.username or "",
                        "order_id": str(order.id),
                        "subscription_period": str(subscription_params["period_days"]),
                        "payment_purpose": "simple_subscription_purchase"
                    }
                )
            
            if not payment_result:
                await callback.answer("❌ Ошибка создания платежа", show_alert=True)
                return
            
            # Отправляем QR-код и/или ссылку для оплаты
            confirmation_url = payment_result.get("confirmation_url")
            qr_confirmation_data = payment_result.get("qr_confirmation_data")
            
            if not confirmation_url and not qr_confirmation_data:
                await callback.answer("❌ Ошибка получения данных для оплаты", show_alert=True)
                return
            
            # Подготовим QR-код для вставки в основное сообщение
            qr_photo = None
            if qr_confirmation_data or confirmation_url:
                try:
                    # Импортируем необходимые модули для генерации QR-кода
                    import base64
                    from io import BytesIO
                    import qrcode
                    from aiogram.types import BufferedInputFile
                    
                    # Используем qr_confirmation_data если доступно, иначе confirmation_url
                    qr_data = qr_confirmation_data if qr_confirmation_data else confirmation_url
                    
                    # Создаем QR-код из полученных данных
                    qr = qrcode.QRCode(version=1, box_size=10, border=5)
                    qr.add_data(qr_data)
                    qr.make(fit=True)
                    
                    img = qr.make_image(fill_color="black", back_color="white")
                    
                    # Сохраняем изображение в байты
                    img_bytes = BytesIO()
                    img.save(img_bytes, format='PNG')
                    img_bytes.seek(0)
                    
                    qr_photo = BufferedInputFile(img_bytes.getvalue(), filename="qrcode.png")
                except ImportError:
                    logger.warning("qrcode библиотека не установлена, QR-код не будет сгенерирован")
                except Exception as e:
                    logger.error(f"Ошибка генерации QR-кода: {e}")
            
            # Создаем клавиатуру с кнопками для оплаты по ссылке и проверки статуса
            keyboard_buttons = []
            
            # Добавляем кнопку оплаты, если доступна ссылка
            if confirmation_url:
                keyboard_buttons.append([types.InlineKeyboardButton(text="🔗 Перейти к оплате", url=confirmation_url)])
            else:
                # Если ссылка недоступна, предлагаем оплатить через ID платежа в приложении банка
                keyboard_buttons.append([types.InlineKeyboardButton(text="📱 Оплатить в приложении банка", callback_data="temp_disabled")])
            
            # Добавляем общие кнопки
            keyboard_buttons.append([types.InlineKeyboardButton(text="📊 Проверить статус", callback_data=f"check_yookassa_{payment_result['local_payment_id']}")])
            keyboard_buttons.append([types.InlineKeyboardButton(text=texts.BACK, callback_data="subscription_purchase")])
            
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
            
            # Подготавливаем текст сообщения
            message_text = (
                f"💳 <b>Оплата подписки через YooKassa</b>\n\n"
                f"📅 Период: {subscription_params['period_days']} дней\n"
                f"📱 Устройства: {subscription_params['device_limit']}\n"
                f"📊 Трафик: {'Безлимит' if subscription_params['traffic_limit_gb'] == 0 else f'{subscription_params['traffic_limit_gb']} ГБ'}\n"
                f"💰 Сумма: {settings.format_price(price_kopeks)}\n"
                f"🆔 ID платежа: {payment_result['yookassa_payment_id'][:8]}...\n\n"
            )
            
            # Добавляем инструкции в зависимости от доступных способов оплаты
            if not confirmation_url:
                message_text += (
                    f"📱 <b>Инструкция по оплате:</b>\n"
                    f"1. Откройте приложение вашего банка\n"
                    f"2. Найдите функцию оплаты по реквизитам или перевод по СБП\n"
                    f"3. Введите ID платежа: <code>{payment_result['yookassa_payment_id']}</code>\n"
                    f"4. Подтвердите платеж в приложении банка\n"
                    f"5. Деньги поступят на баланс автоматически\n\n"
                )
            
            message_text += (
                f"🔒 Оплата происходит через защищенную систему YooKassa\n"
                f"✅ Принимаем карты: Visa, MasterCard, МИР\n\n"
                f"❓ Если возникнут проблемы, обратитесь в {settings.get_support_contact_display_html()}"
            )
            
            # Отправляем сообщение с инструкциями и клавиатурой
            # Если есть QR-код, отправляем его как медиа-сообщение
            if qr_photo:
                # Используем метод отправки фото с описанием
                await callback.message.edit_media(
                    media=types.InputMediaPhoto(
                        media=qr_photo,
                        caption=message_text,
                        parse_mode="HTML"
                    ),
                    reply_markup=keyboard
                )
            else:
                # Если QR-код недоступен, отправляем обычное текстовое сообщение
                await callback.message.edit_text(
                    message_text,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            
            await state.clear()
            await callback.answer()
            
        elif payment_method == "cryptobot":
            # Оплата через CryptoBot
            if not settings.is_cryptobot_enabled():
                await callback.answer("❌ Оплата через CryptoBot временно недоступна", show_alert=True)
                return
            
            # Здесь должна быть реализация оплаты через CryptoBot
            await callback.answer("❌ Оплата через CryptoBot пока не реализована", show_alert=True)
            
        elif payment_method == "mulenpay":
            # Оплата через MulenPay
            if not settings.is_mulenpay_enabled():
                await callback.answer("❌ Оплата через MulenPay временно недоступна", show_alert=True)
                return
            
            # Здесь должна быть реализация оплаты через MulenPay
            await callback.answer("❌ Оплата через MulenPay пока не реализована", show_alert=True)
            
        elif payment_method == "pal24":
            # Оплата через PayPalych
            if not settings.is_pal24_enabled():
                await callback.answer("❌ Оплата через PayPalych временно недоступна", show_alert=True)
                return
            
            # Здесь должна быть реализация оплаты через PayPalych
            await callback.answer("❌ Оплата через PayPalych пока не реализована", show_alert=True)
            
        elif payment_method == "wata":
            # Оплата через WATA
            if not settings.is_wata_enabled():
                await callback.answer("❌ Оплата через WATA временно недоступна", show_alert=True)
                return
            
            # Здесь должна быть реализация оплаты через WATA
            await callback.answer("❌ Оплата через WATA пока не реализована", show_alert=True)
            
        else:
            await callback.answer("❌ Неизвестный способ оплаты", show_alert=True)
            
    except Exception as e:
        logger.error(f"Ошибка обработки метода оплаты простой подписки: {e}")
        await callback.answer("❌ Ошибка обработки запроса. Попробуйте позже или обратитесь в поддержку.", show_alert=True)
        await state.clear()


def register_simple_subscription_handlers(dp):
    """Регистрирует обработчики простой покупки подписки."""
    
    dp.callback_query.register(
        start_simple_subscription_purchase,
        F.data == "simple_subscription_purchase"
    )
    
    dp.callback_query.register(
        handle_simple_subscription_pay_with_balance,
        F.data == "simple_subscription_pay_with_balance"
    )
    
    dp.callback_query.register(
        handle_simple_subscription_pay_with_balance_disabled,
        F.data == "simple_subscription_pay_with_balance_disabled"
    )
    
    dp.callback_query.register(
        handle_simple_subscription_other_payment_methods,
        F.data == "simple_subscription_other_payment_methods"
    )
    
    dp.callback_query.register(
        handle_simple_subscription_payment_method,
        F.data.startswith("simple_subscription_")
    )
