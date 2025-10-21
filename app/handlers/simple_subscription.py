"""Обработчики для простой покупки подписки."""
import html
import logging
from datetime import datetime
from typing import Optional, Dict, Any
from aiogram import types, F
from aiogram.exceptions import TelegramBadRequest
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
    
    # Проверяем, есть ли у пользователя подписка (информируем, но не блокируем покупку)
    from app.database.crud.subscription import get_subscription_by_user_id
    current_subscription = await get_subscription_by_user_id(db, db_user.id)
    
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
    data = await state.get_data()
    resolved_squad_uuid = await _ensure_simple_subscription_squad_uuid(
        db,
        state,
        subscription_params,
        user_id=db_user.id,
        state_data=data,
    )
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

    trial_notice = ""
    if current_subscription and getattr(current_subscription, "is_trial", False):
        try:
            days_left = max(0, (current_subscription.end_date - datetime.utcnow()).days)
        except Exception:
            days_left = 0
        key = "SIMPLE_SUBSCRIPTION_TRIAL_NOTICE_ACTIVE" if current_subscription.is_active else "SIMPLE_SUBSCRIPTION_TRIAL_NOTICE_TRIAL"
        trial_notice = texts.t(
            key,
            "ℹ️ У вас уже есть триальная подписка. Она истекает через {days} дн.",
        ).format(days=days_left)

    server_label = _get_simple_subscription_server_label(
        texts,
        subscription_params,
        resolved_squad_uuid,
    )
    message_text = (
        f"⚡ <b>Простая покупка подписки</b>\n\n"
        f"📅 Период: {subscription_params['period_days']} дней\n"
        f"📱 Устройства: {subscription_params['device_limit']}\n"
        f"📊 Трафик: {'Безлимит' if subscription_params['traffic_limit_gb'] == 0 else f'{subscription_params['traffic_limit_gb']} ГБ'}\n"
        f"🌍 Сервер: {server_label}\n\n"
        f"💰 Стоимость: {settings.format_price(price_kopeks)}\n"
        f"💳 Ваш баланс: {settings.format_price(user_balance_kopeks)}\n\n"
        + (
            "Вы можете оплатить подписку с баланса или выбрать другой способ оплаты."
            if can_pay_from_balance
            else "Баланс пока недостаточный для мгновенной оплаты. Выберите подходящий способ оплаты:"
        )
    )

    if trial_notice:
        message_text = f"{trial_notice}\n\n{message_text}"

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

    if settings.is_heleket_enabled():
        keyboard.append([types.InlineKeyboardButton(
            text="🪙 Heleket",
            callback_data="simple_subscription_heleket"
        )])
    
    if settings.is_mulenpay_enabled():
        mulenpay_name = settings.get_mulenpay_display_name()
        keyboard.append([types.InlineKeyboardButton(
            text=f"💳 {mulenpay_name}",
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


def _get_simple_subscription_server_label(
    texts,
    subscription_params: Dict[str, Any],
    resolved_squad_uuid: Optional[str] = None,
) -> str:
    """Возвращает локализованное описание выбранного сервера."""

    if subscription_params.get("squad_uuid"):
        return texts.t("SIMPLE_SUBSCRIPTION_SERVER_SELECTED", "Выбранный")

    if resolved_squad_uuid:
        return texts.t(
            "SIMPLE_SUBSCRIPTION_SERVER_ASSIGNED",
            "Назначен автоматически",
        )

    return texts.t("SIMPLE_SUBSCRIPTION_SERVER_ANY", "Любой доступный")


async def _ensure_simple_subscription_squad_uuid(
    db: AsyncSession,
    state: FSMContext,
    subscription_params: Dict[str, Any],
    *,
    user_id: Optional[int] = None,
    state_data: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Определяет UUID сквада для простой подписки."""

    explicit_uuid = subscription_params.get("squad_uuid")
    if explicit_uuid:
        return explicit_uuid

    if state_data is None:
        state_data = await state.get_data()

    resolved_uuid = state_data.get("resolved_squad_uuid")
    if resolved_uuid:
        return resolved_uuid

    try:
        from app.database.crud.server_squad import get_random_active_squad_uuid

        resolved_uuid = await get_random_active_squad_uuid(db)
    except Exception as error:  # pragma: no cover - defensive logging
        logger.error(
            "SIMPLE_SUBSCRIPTION_RANDOM_SQUAD_ERROR | user=%s | error=%s",
            user_id,
            error,
        )
        return None

    if resolved_uuid:
        await state.update_data(resolved_squad_uuid=resolved_uuid)
        logger.info(
            "SIMPLE_SUBSCRIPTION_RANDOM_SQUAD_ASSIGNED | user=%s | squad=%s",
            user_id,
            resolved_uuid,
        )

    return resolved_uuid


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

    resolved_squad_uuid = await _ensure_simple_subscription_squad_uuid(
        db,
        state,
        subscription_params,
        user_id=db_user.id,
        state_data=data,
    )

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
            if resolved_squad_uuid:
                subscription.connected_squads = [resolved_squad_uuid]
            
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
                connected_squads=[resolved_squad_uuid] if resolved_squad_uuid else [],
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
        server_label = _get_simple_subscription_server_label(
            texts,
            subscription_params,
            resolved_squad_uuid,
        )
        success_message = (
            f"✅ <b>Подписка успешно активирована!</b>\n\n"
            f"📅 Период: {subscription_params['period_days']} дней\n"
            f"📱 Устройства: {subscription_params['device_limit']}\n"
            f"📊 Трафик: {'Безлимит' if subscription_params['traffic_limit_gb'] == 0 else f'{subscription_params['traffic_limit_gb']} ГБ'}\n"
            f"🌍 Сервер: {server_label}\n\n"
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
    resolved_squad_uuid = data.get("resolved_squad_uuid")
    server_label = _get_simple_subscription_server_label(
        texts,
        subscription_params,
        resolved_squad_uuid,
    )
    message_text = (
        f"💳 <b>Оплата подписки</b>\n\n"
        f"📅 Период: {subscription_params['period_days']} дней\n"
        f"📱 Устройства: {subscription_params['device_limit']}\n"
        f"📊 Трафик: {'Безлимит' if subscription_params['traffic_limit_gb'] == 0 else f'{subscription_params['traffic_limit_gb']} ГБ'}\n"
        f"🌍 Сервер: {server_label}\n\n"
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

        resolved_squad_uuid = await _ensure_simple_subscription_squad_uuid(
            db,
            state,
            subscription_params,
            user_id=db_user.id,
            state_data=data,
        )

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
                squad_uuid=resolved_squad_uuid,
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

            amount_rubles = price_kopeks / 100
            if amount_rubles < 100 or amount_rubles > 100000:
                await callback.answer(
                    "❌ Сумма должна быть от 100 до 100 000 ₽ для оплаты через CryptoBot",
                    show_alert=True,
                )
                return

            try:
                from app.utils.currency_converter import currency_converter

                usd_rate = await currency_converter.get_usd_to_rub_rate()
            except Exception as rate_error:
                logger.warning("Не удалось получить курс USD: %s", rate_error)
                usd_rate = 95.0

            amount_usd = round(amount_rubles / usd_rate, 2)
            if amount_usd < 1:
                await callback.answer(
                    "❌ Минимальная сумма для оплаты через CryptoBot — примерно 1 USD",
                    show_alert=True,
                )
                return
            if amount_usd > 1000:
                await callback.answer(
                    "❌ Максимальная сумма для оплаты через CryptoBot — 1000 USD",
                    show_alert=True,
                )
                return

            payment_service = PaymentService(callback.bot)
            crypto_result = await payment_service.create_cryptobot_payment(
                db=db,
                user_id=db_user.id,
                amount_usd=amount_usd,
                asset=settings.CRYPTOBOT_DEFAULT_ASSET,
                description=settings.get_subscription_payment_description(
                    subscription_params["period_days"],
                    price_kopeks,
                ),
                payload=f"simple_subscription_{db_user.id}_{price_kopeks}",
            )

            if not crypto_result:
                await callback.answer(
                    "❌ Ошибка создания платежа через CryptoBot. Попробуйте позже или обратитесь в поддержку.",
                    show_alert=True,
                )
                return

            payment_url = (
                crypto_result.get("mini_app_invoice_url")
                or crypto_result.get("bot_invoice_url")
                or crypto_result.get("web_app_invoice_url")
            )

            if not payment_url:
                await callback.answer(
                    "❌ Не удалось получить ссылку для оплаты. Обратитесь в поддержку.",
                    show_alert=True,
                )
                return

            keyboard = types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            text="🪙 Оплатить через CryptoBot",
                            url=payment_url,
                        )
                    ],
                    [
                        types.InlineKeyboardButton(
                            text=texts.t("CHECK_STATUS_BUTTON", "📊 Проверить статус"),
                            callback_data=f"check_simple_cryptobot_{crypto_result['local_payment_id']}",
                        )
                    ],
                    [types.InlineKeyboardButton(text=texts.BACK, callback_data="subscription_purchase")],
                ]
            )

            message_text = (
                "🪙 <b>Оплата через CryptoBot</b>\n\n"
                f"💰 Сумма к оплате: {amount_rubles:.0f} ₽\n"
                f"💵 В долларах: {amount_usd:.2f} USD\n"
                f"🪙 Актив: {crypto_result['asset']}\n"
                f"💱 Курс: 1 USD ≈ {usd_rate:.2f} ₽\n"
                f"🆔 ID платежа: {crypto_result['invoice_id'][:8]}...\n\n"
                "📱 <b>Инструкция:</b>\n"
                "1. Нажмите кнопку 'Оплатить через CryptoBot'\n"
                "2. Выберите актив и следуйте подсказкам\n"
                "3. Подтвердите перевод\n"
                "4. Средства зачислятся автоматически\n\n"
                f"❓ Если возникнут проблемы, обратитесь в {settings.get_support_contact_display_html()}"
            )

            await callback.message.edit_text(
                message_text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )

            await state.clear()
            await callback.answer()
            return

        elif payment_method == "heleket":
            if not settings.is_heleket_enabled():
                await callback.answer("❌ Оплата через Heleket временно недоступна", show_alert=True)
                return

            amount_rubles = price_kopeks / 100
            if amount_rubles < 100 or amount_rubles > 100000:
                await callback.answer(
                    "❌ Сумма должна быть от 100 до 100 000 ₽ для оплаты через Heleket",
                    show_alert=True,
                )
                return

            heleket_result = await payment_service.create_heleket_payment(
                db=db,
                user_id=db_user.id,
                amount_kopeks=price_kopeks,
                description=settings.get_subscription_payment_description(
                    subscription_params["period_days"],
                    price_kopeks,
                ),
                language=db_user.language,
            )

            if not heleket_result:
                await callback.answer(
                    "❌ Ошибка создания платежа Heleket. Попробуйте позже или обратитесь в поддержку.",
                    show_alert=True,
                )
                return

            payment_url = heleket_result.get("payment_url")
            if not payment_url:
                await callback.answer(
                    "❌ Не удалось получить ссылку для оплаты Heleket. Обратитесь в поддержку.",
                    show_alert=True,
                )
                return

            local_payment_id = heleket_result.get("local_payment_id")
            payer_amount = heleket_result.get("payer_amount")
            payer_currency = heleket_result.get("payer_currency")
            discount_percent = heleket_result.get("discount_percent")

            markup_percent = None
            if discount_percent is not None:
                try:
                    markup_percent = -int(discount_percent)
                except (TypeError, ValueError):
                    markup_percent = None

            keyboard = types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            text="🪙 Оплатить через Heleket",
                            url=payment_url,
                        )
                    ],
                    [
                        types.InlineKeyboardButton(
                            text=texts.t("CHECK_STATUS_BUTTON", "📊 Проверить статус"),
                            callback_data=f"check_simple_heleket_{local_payment_id}",
                        )
                    ],
                    [types.InlineKeyboardButton(text=texts.BACK, callback_data="subscription_purchase")],
                ]
            )

            message_lines = [
                "🪙 <b>Оплата через Heleket</b>",
                "",
                f"💰 Сумма: {settings.format_price(price_kopeks)}",
            ]

            if payer_amount and payer_currency:
                message_lines.append(f"🪙 К оплате: {payer_amount} {payer_currency}")
                try:
                    payer_amount_float = float(payer_amount)
                    if payer_amount_float > 0:
                        rub_per_currency = amount_rubles / payer_amount_float
                        message_lines.append(
                            f"💱 Курс: 1 {payer_currency} ≈ {rub_per_currency:.2f} ₽"
                        )
                except (TypeError, ValueError, ZeroDivisionError):
                    pass

            if markup_percent:
                sign = "+" if markup_percent > 0 else ""
                message_lines.append(f"📈 Наценка: {sign}{markup_percent}%")

            message_lines.extend(
                [
                    "",
                    "📱 <b>Инструкция:</b>",
                    "1. Нажмите кнопку 'Оплатить через Heleket'",
                    "2. Следуйте подсказкам на странице оплаты",
                    "3. Подтвердите перевод",
                    "4. Средства зачислятся автоматически",
                    "",
                    f"❓ Если возникнут проблемы, обратитесь в {settings.get_support_contact_display_html()}",
                ]
            )

            await callback.message.edit_text(
                "\n".join(message_lines),
                reply_markup=keyboard,
                parse_mode="HTML",
            )

            await state.clear()
            await callback.answer()
            return

        elif payment_method == "mulenpay":
            # Оплата через MulenPay
            mulenpay_name = settings.get_mulenpay_display_name()
            if not settings.is_mulenpay_enabled():
                await callback.answer(
                    f"❌ Оплата через {mulenpay_name} временно недоступна",
                    show_alert=True,
                )
                return

            if price_kopeks < settings.MULENPAY_MIN_AMOUNT_KOPEKS or price_kopeks > settings.MULENPAY_MAX_AMOUNT_KOPEKS:
                await callback.answer(
                    "❌ Сумма для Mulen Pay должна быть в пределах от {min_amount} до {max_amount}".format(
                        min_amount=settings.format_price(settings.MULENPAY_MIN_AMOUNT_KOPEKS),
                        max_amount=settings.format_price(settings.MULENPAY_MAX_AMOUNT_KOPEKS),
                    ),
                    show_alert=True,
                )
                return

            payment_service = PaymentService(callback.bot)
            mulen_result = await payment_service.create_mulenpay_payment(
                db=db,
                user_id=db_user.id,
                amount_kopeks=price_kopeks,
                description=settings.get_subscription_payment_description(
                    subscription_params["period_days"],
                    price_kopeks,
                ),
                language=db_user.language,
            )

            if not mulen_result or not mulen_result.get("payment_url"):
                await callback.answer(
                    texts.t(
                        "MULENPAY_PAYMENT_ERROR",
                        "❌ Ошибка создания платежа Mulen Pay. Попробуйте позже или обратитесь в поддержку.",
                    ),
                    show_alert=True,
                )
                return

            payment_url = mulen_result["payment_url"]
            local_payment_id = mulen_result.get("local_payment_id")
            payment_id_display = mulen_result.get("mulen_payment_id") or local_payment_id

            keyboard = types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            text=texts.t("MULENPAY_PAY_BUTTON", "💳 Оплатить через Mulen Pay"),
                            url=payment_url,
                        )
                    ],
                    [
                        types.InlineKeyboardButton(
                            text=texts.t("CHECK_STATUS_BUTTON", "📊 Проверить статус"),
                            callback_data=f"check_simple_mulenpay_{local_payment_id}",
                        )
                    ],
                    [types.InlineKeyboardButton(text=texts.BACK, callback_data="subscription_purchase")],
                ]
            )

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

            await callback.message.edit_text(
                message_template.format(
                    mulenpay_name=mulenpay_name,
                    mulenpay_name_html=settings.get_mulenpay_display_name_html(),
                    amount=settings.format_price(price_kopeks),
                    payment_id=payment_id_display,
                    support=settings.get_support_contact_display_html(),
                ),
                reply_markup=keyboard,
                parse_mode="HTML",
            )

            await state.clear()
            await callback.answer()
            return
            
        elif payment_method == "pal24":
            # Оплата через PayPalych
            if not settings.is_pal24_enabled():
                await callback.answer("❌ Оплата через PayPalych временно недоступна", show_alert=True)
                return

            payment_service = PaymentService(callback.bot)
            pal24_result = await payment_service.create_pal24_payment(
                db=db,
                user_id=db_user.id,
                amount_kopeks=price_kopeks,
                description=settings.get_subscription_payment_description(
                    subscription_params["period_days"],
                    price_kopeks,
                ),
                language=db_user.language,
            )

            if not pal24_result:
                await callback.answer(
                    texts.t(
                        "PAL24_PAYMENT_ERROR",
                        "❌ Ошибка создания платежа PayPalych. Попробуйте позже или обратитесь в поддержку.",
                    ),
                    show_alert=True,
                )
                return

            sbp_url = pal24_result.get("sbp_url") or pal24_result.get("transfer_url")
            card_url = pal24_result.get("card_url")
            fallback_url = pal24_result.get("link_page_url") or pal24_result.get("link_url")

            if not (sbp_url or card_url or fallback_url):
                await callback.answer(
                    texts.t(
                        "PAL24_PAYMENT_ERROR",
                        "❌ Ошибка создания платежа PayPalych. Попробуйте позже или обратитесь в поддержку.",
                    ),
                    show_alert=True,
                )
                return

            if not sbp_url:
                sbp_url = fallback_url

            bill_id = pal24_result.get("bill_id")
            local_payment_id = pal24_result.get("local_payment_id")

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
                        callback_data=f"check_simple_pal24_{local_payment_id}",
                    )
                ],
                [types.InlineKeyboardButton(text=texts.BACK, callback_data="subscription_purchase")],
            ]

            keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

            message_text = message_template.format(
                amount=settings.format_price(price_kopeks),
                bill_id=bill_id,
                steps="\n".join(steps),
                support=settings.get_support_contact_display_html(),
            )

            await callback.message.edit_text(
                message_text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )

            await state.clear()
            await callback.answer()
            return

        elif payment_method == "wata":
            # Оплата через WATA
            if not settings.is_wata_enabled():
                await callback.answer("❌ Оплата через WATA временно недоступна", show_alert=True)
                return
            if price_kopeks < settings.WATA_MIN_AMOUNT_KOPEKS or price_kopeks > settings.WATA_MAX_AMOUNT_KOPEKS:
                await callback.answer(
                    "❌ Сумма для WATA должна быть между {min_amount} и {max_amount}.".format(
                        min_amount=settings.format_price(settings.WATA_MIN_AMOUNT_KOPEKS),
                        max_amount=settings.format_price(settings.WATA_MAX_AMOUNT_KOPEKS),
                    ),
                    show_alert=True,
                )
                return

            payment_service = PaymentService(callback.bot)
            try:
                wata_result = await payment_service.create_wata_payment(
                    db=db,
                    user_id=db_user.id,
                    amount_kopeks=price_kopeks,
                    description=settings.get_subscription_payment_description(
                        subscription_params["period_days"],
                        price_kopeks,
                    ),
                    language=db_user.language,
                )
            except Exception as error:
                logger.error("Ошибка создания WATA платежа: %s", error)
                wata_result = None

            if not wata_result or not wata_result.get("payment_url"):
                await callback.answer(
                    texts.t(
                        "WATA_PAYMENT_ERROR",
                        "❌ Ошибка создания платежа WATA. Попробуйте позже или обратитесь в поддержку.",
                    ),
                    show_alert=True,
                )
                return

            payment_url = wata_result["payment_url"]
            payment_link_id = wata_result.get("payment_link_id")
            local_payment_id = wata_result.get("local_payment_id")

            keyboard = types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            text=texts.t("WATA_PAY_BUTTON", "💳 Оплатить через WATA"),
                            url=payment_url,
                        )
                    ],
                    [
                        types.InlineKeyboardButton(
                            text=texts.t("CHECK_STATUS_BUTTON", "📊 Проверить статус"),
                            callback_data=f"check_simple_wata_{local_payment_id}",
                        )
                    ],
                    [types.InlineKeyboardButton(text=texts.BACK, callback_data="subscription_purchase")],
                ]
            )

            message_template = texts.t(
                "WATA_PAYMENT_INSTRUCTIONS",
                (
                    "💳 <b>Оплата через WATA</b>\n\n"
                    "💰 Сумма: {amount}\n"
                    "🆔 ID платежа: {payment_id}\n\n"
                    "📱 <b>Инструкция:</b>\n"
                    "1. Нажмите кнопку 'Оплатить через WATA'\n"
                    "2. Следуйте подсказкам платежной системы\n"
                    "3. Подтвердите перевод\n"
                    "4. Средства зачислятся автоматически\n\n"
                    "❓ Если возникнут проблемы, обратитесь в {support}"
                ),
            )

            await callback.message.edit_text(
                message_template.format(
                    amount=settings.format_price(price_kopeks),
                    payment_id=payment_link_id,
                    support=settings.get_support_contact_display_html(),
                ),
                reply_markup=keyboard,
                parse_mode="HTML",
            )

            await state.clear()
            await callback.answer()
            return
            
        else:
            await callback.answer("❌ Неизвестный способ оплаты", show_alert=True)
            
    except Exception as e:
        logger.error(f"Ошибка обработки метода оплаты простой подписки: {e}")
        await callback.answer("❌ Ошибка обработки запроса. Попробуйте позже или обратитесь в поддержку.", show_alert=True)
        await state.clear()


@error_handler
async def check_simple_pal24_payment_status(
    callback: types.CallbackQuery,
    db: AsyncSession,
):
    try:
        local_payment_id = int(callback.data.rsplit('_', 1)[-1])
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
        links_meta = metadata.get("links") if isinstance(metadata, dict) else {}
        if not isinstance(links_meta, dict):
            links_meta = {}

        sbp_link = links_meta.get("sbp") or payment.link_url
        card_link = links_meta.get("card")
        if not card_link and payment.link_page_url and payment.link_page_url != sbp_link:
            card_link = payment.link_page_url

        db_user = getattr(callback, "db_user", None)
        texts = get_texts(db_user.language if db_user else settings.DEFAULT_LANGUAGE)

        message_lines = [
            "🏦 Статус платежа PayPalych:",
            "",
            f"🆔 ID счета: {payment.bill_id}",
            f"💰 Сумма: {settings.format_price(payment.amount_kopeks)}",
            f"📊 Статус: {emoji} {status_text}",
            f"📅 Создан: {payment.created_at.strftime('%d.%m.%Y %H:%M')}",
        ]

        if payment.is_paid:
            message_lines += ["", "✅ Платеж успешно завершен! Средства уже зачислены."]
        elif payment.status in {"NEW", "PROCESS"}:
            message_lines += [
                "",
                "⏳ Платеж еще не завершен. Оплатите счет и проверьте статус позже.",
            ]
            if sbp_link:
                message_lines += ["", f"🏦 СБП: {sbp_link}"]
            if card_link and card_link != sbp_link:
                message_lines.append(f"💳 Карта: {card_link}")
        elif payment.status in {"FAIL", "UNDERPAID", "OVERPAID"}:
            message_lines += [
                "",
                f"❌ Платеж не завершен корректно. Обратитесь в {settings.get_support_contact_display()}",
            ]

        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=texts.t("CHECK_STATUS_BUTTON", "📊 Проверить статус"),
                        callback_data=f"check_simple_pal24_{local_payment_id}",
                    )
                ],
                [types.InlineKeyboardButton(text=texts.BACK, callback_data="subscription_purchase")],
            ]
        )

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

    except Exception as error:
        logger.error(f"Ошибка проверки статуса PayPalych для простой подписки: {error}")
        await callback.answer("❌ Ошибка проверки статуса", show_alert=True)


@error_handler
async def check_simple_mulenpay_payment_status(
    callback: types.CallbackQuery,
    db: AsyncSession,
):
    try:
        local_payment_id = int(callback.data.rsplit('_', 1)[-1])
    except (ValueError, IndexError):
        await callback.answer("❌ Некорректный идентификатор платежа", show_alert=True)
        return

    payment_service = PaymentService(callback.bot)
    status_info = await payment_service.get_mulenpay_payment_status(db, local_payment_id)

    if not status_info:
        await callback.answer("❌ Платеж не найден", show_alert=True)
        return

    payment = status_info["payment"]

    user_language = settings.DEFAULT_LANGUAGE
    try:
        from app.services.payment_service import get_user_by_id as fetch_user_by_id

        user = await fetch_user_by_id(db, payment.user_id)
        if user and getattr(user, "language", None):
            user_language = user.language
    except Exception as error:
        logger.debug("Не удалось получить пользователя для MulenPay статуса: %s", error)

    texts = get_texts(user_language)
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

    message_lines = [
        "💳 Статус платежа Mulen Pay:",
        "",
        f"🆔 ID: {payment.mulen_payment_id or payment.id}",
        f"💰 Сумма: {settings.format_price(payment.amount_kopeks)}",
        f"📊 Статус: {emoji} {status_text}",
        f"📅 Создан: {payment.created_at.strftime('%d.%m.%Y %H:%M') if payment.created_at else '—'}",
    ]

    if payment.is_paid:
        message_lines.append("\n✅ Платеж успешно завершен! Средства уже зачислены.")
    elif payment.status in {"created", "processing"}:
        message_lines.append("\n⏳ Платеж еще не завершен. Завершите оплату и проверьте статус позже.")

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t("CHECK_STATUS_BUTTON", "📊 Проверить статус"),
                    callback_data=f"check_simple_mulenpay_{local_payment_id}",
                )
            ],
            [types.InlineKeyboardButton(text=texts.BACK, callback_data="subscription_purchase")],
        ]
    )

    await callback.answer()
    await callback.message.edit_text(
        "\n".join(message_lines),
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@error_handler
async def check_simple_cryptobot_payment_status(
    callback: types.CallbackQuery,
    db: AsyncSession,
):
    try:
        local_payment_id = int(callback.data.rsplit('_', 1)[-1])
    except (ValueError, IndexError):
        await callback.answer("❌ Некорректный идентификатор платежа", show_alert=True)
        return

    from app.database.crud.cryptobot import get_cryptobot_payment_by_id

    payment = await get_cryptobot_payment_by_id(db, local_payment_id)
    if not payment:
        await callback.answer("❌ Платеж не найден", show_alert=True)
        return

    status_labels = {
        "active": ("⏳", "Ожидает оплаты"),
        "paid": ("✅", "Оплачен"),
        "expired": ("❌", "Истек"),
    }
    emoji, status_text = status_labels.get(payment.status, ("❓", "Неизвестно"))

    language = settings.DEFAULT_LANGUAGE
    try:
        from app.services.payment_service import get_user_by_id as fetch_user_by_id

        user = await fetch_user_by_id(db, payment.user_id)
        if user and getattr(user, "language", None):
            language = user.language
    except Exception as error:
        logger.debug("Не удалось получить пользователя для CryptoBot статуса: %s", error)

    texts = get_texts(language)
    message_lines = [
        "🪙 <b>Статус платежа CryptoBot</b>",
        "",
        f"🆔 ID: {payment.invoice_id}",
        f"💰 Сумма: {payment.amount} {payment.asset}",
        f"📊 Статус: {emoji} {status_text}",
        f"📅 Создан: {payment.created_at.strftime('%d.%m.%Y %H:%M') if payment.created_at else '—'}",
    ]

    if payment.status == "paid":
        message_lines.append("\n✅ Платеж подтвержден. Средства уже зачислены.")
    elif payment.status == "active":
        message_lines.append("\n⏳ Платеж еще ожидает подтверждения. Оплатите счет и проверьте статус позже.")

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t("CHECK_STATUS_BUTTON", "📊 Проверить статус"),
                    callback_data=f"check_simple_cryptobot_{local_payment_id}",
                )
            ],
            [types.InlineKeyboardButton(text=texts.BACK, callback_data="subscription_purchase")],
        ]
    )

    await callback.answer()
    await callback.message.edit_text(
        "\n".join(message_lines),
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@error_handler
async def check_simple_heleket_payment_status(
    callback: types.CallbackQuery,
    db: AsyncSession,
):
    try:
        local_payment_id = int(callback.data.rsplit('_', 1)[-1])
    except (ValueError, IndexError):
        await callback.answer("❌ Некорректный идентификатор платежа", show_alert=True)
        return

    from app.database.crud.heleket import get_heleket_payment_by_id

    payment = await get_heleket_payment_by_id(db, local_payment_id)
    if not payment:
        await callback.answer("❌ Платеж не найден", show_alert=True)
        return

    status_labels = {
        "check": ("⏳", "Ожидает оплаты"),
        "paid": ("✅", "Оплачен"),
        "paid_over": ("✅", "Оплачен (переплата)"),
        "wrong_amount": ("⚠️", "Неверная сумма"),
        "cancel": ("❌", "Отменен"),
        "fail": ("❌", "Ошибка"),
        "process": ("⌛", "Обрабатывается"),
        "confirm_check": ("⌛", "Ожидает подтверждения"),
    }

    emoji, status_text = status_labels.get(payment.status, ("❓", "Неизвестно"))

    language = settings.DEFAULT_LANGUAGE
    try:
        from app.services.payment_service import get_user_by_id as fetch_user_by_id

        user = await fetch_user_by_id(db, payment.user_id)
        if user and getattr(user, "language", None):
            language = user.language
    except Exception as error:
        logger.debug("Не удалось получить пользователя для Heleket статуса: %s", error)

    texts = get_texts(language)

    message_lines = [
        "🪙 Статус платежа Heleket:",
        "",
        f"🆔 UUID: {payment.uuid[:8]}...",
        f"💰 Сумма: {settings.format_price(payment.amount_kopeks)}",
        f"📊 Статус: {emoji} {status_text}",
        f"📅 Создан: {payment.created_at.strftime('%d.%m.%Y %H:%M') if payment.created_at else '—'}",
    ]

    if payment.payer_amount and payment.payer_currency:
        message_lines.append(
            f"🪙 Оплата: {payment.payer_amount} {payment.payer_currency}"
        )

    if payment.is_paid:
        message_lines.append("\n✅ Платеж успешно завершен! Средства уже зачислены.")
    elif payment.status in {"check", "process", "confirm_check"}:
        message_lines.append("\n⏳ Платеж еще обрабатывается. Завершите оплату и проверьте статус позже.")
        if payment.payment_url:
            message_lines.append(f"\n🔗 Ссылка на оплату: {payment.payment_url}")
    elif payment.status in {"fail", "cancel", "wrong_amount"}:
        message_lines.append(
            f"\n❌ Платеж не завершен корректно. Обратитесь в {settings.get_support_contact_display()}"
        )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t("CHECK_STATUS_BUTTON", "📊 Проверить статус"),
                    callback_data=f"check_simple_heleket_{local_payment_id}",
                )
            ],
            [types.InlineKeyboardButton(text=texts.BACK, callback_data="subscription_purchase")],
        ]
    )

    await callback.answer()
    await callback.message.edit_text(
        "\n".join(message_lines),
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@error_handler
async def check_simple_wata_payment_status(
    callback: types.CallbackQuery,
    db: AsyncSession,
):
    try:
        local_payment_id = int(callback.data.rsplit('_', 1)[-1])
    except (ValueError, IndexError):
        await callback.answer("❌ Некорректный идентификатор платежа", show_alert=True)
        return

    payment_service = PaymentService(callback.bot)
    status_info = await payment_service.get_wata_payment_status(db, local_payment_id)

    if not status_info:
        await callback.answer("❌ Платеж не найден", show_alert=True)
        return

    payment = status_info["payment"]
    texts = get_texts(settings.DEFAULT_LANGUAGE)

    status_labels = {
        "Opened": ("⏳", texts.t("WATA_STATUS_OPENED", "Ожидает оплаты")),
        "Closed": ("⌛", texts.t("WATA_STATUS_CLOSED", "Обрабатывается")),
        "Paid": ("✅", texts.t("WATA_STATUS_PAID", "Оплачен")),
        "Declined": ("❌", texts.t("WATA_STATUS_DECLINED", "Отклонен")),
    }
    emoji, status_text = status_labels.get(payment.status, ("❓", texts.t("WATA_STATUS_UNKNOWN", "Неизвестно")))

    message_lines = [
        texts.t("WATA_STATUS_TITLE", "💳 <b>Статус платежа WATA</b>"),
        "",
        f"🆔 ID: {payment.payment_link_id}",
        f"💰 Сумма: {settings.format_price(payment.amount_kopeks)}",
        f"📊 Статус: {emoji} {status_text}",
        f"📅 Создан: {payment.created_at.strftime('%d.%m.%Y %H:%M') if payment.created_at else '—'}",
    ]

    if payment.is_paid:
        message_lines.append("\n✅ Платеж успешно завершен! Средства уже зачислены.")
    elif payment.status in {"Opened", "Closed"}:
        message_lines.append("\n⏳ Платеж еще не завершен. Завершите оплату и проверьте статус позже.")

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t("CHECK_STATUS_BUTTON", "📊 Проверить статус"),
                    callback_data=f"check_simple_wata_{local_payment_id}",
                )
            ],
            [types.InlineKeyboardButton(text=texts.BACK, callback_data="subscription_purchase")],
        ]
    )

    await callback.answer()
    await callback.message.edit_text(
        "\n".join(message_lines),
        reply_markup=keyboard,
        parse_mode="HTML",
    )

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

    dp.callback_query.register(
        check_simple_pal24_payment_status,
        F.data.startswith("check_simple_pal24_")
    )

    dp.callback_query.register(
        check_simple_mulenpay_payment_status,
        F.data.startswith("check_simple_mulenpay_")
    )

    dp.callback_query.register(
        check_simple_cryptobot_payment_status,
        F.data.startswith("check_simple_cryptobot_")
    )

    dp.callback_query.register(
        check_simple_heleket_payment_status,
        F.data.startswith("check_simple_heleket_")
    )

    dp.callback_query.register(
        check_simple_wata_payment_status,
        F.data.startswith("check_simple_wata_")
    )

    dp.callback_query.register(
        check_simple_pal24_payment_status,
        F.data.startswith("check_simple_pal24_")
    )
