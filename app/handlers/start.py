import logging
from datetime import datetime
from aiogram import Dispatcher, types, F, Bot
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.states import RegistrationStates
from app.database.crud.user import (
    get_user_by_telegram_id,
    create_user,
    get_user_by_referral_code,
)
from app.database.crud.campaign import (
    get_campaign_by_start_parameter,
    get_campaign_by_id,
)
from app.database.models import UserStatus
from app.keyboards.inline import (
    get_rules_keyboard, get_main_menu_keyboard, get_post_registration_keyboard
)
from app.localization.loader import DEFAULT_LANGUAGE
from app.localization.texts import get_texts, get_rules_text
from app.services.referral_service import process_referral_registration
from app.services.campaign_service import AdvertisingCampaignService
from app.utils.user_utils import generate_unique_referral_code
from app.database.crud.user_message import get_random_active_message


logger = logging.getLogger(__name__)


async def _apply_campaign_bonus_if_needed(
    db: AsyncSession,
    user,
    state_data: dict,
    texts,
):
    campaign_id = state_data.get("campaign_id") if state_data else None
    if not campaign_id:
        return None

    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign or not campaign.is_active:
        return None

    service = AdvertisingCampaignService()
    result = await service.apply_campaign_bonus(db, user, campaign)
    if not result.success:
        return None

    if result.bonus_type == "balance":
        amount_text = texts.format_price(result.balance_kopeks)
        return texts.CAMPAIGN_BONUS_BALANCE.format(
            amount=amount_text,
            name=campaign.name,
        )

    if result.bonus_type == "subscription":
        traffic_text = texts.format_traffic(result.subscription_traffic_gb or 0)
        return texts.CAMPAIGN_BONUS_SUBSCRIPTION.format(
            name=campaign.name,
            days=result.subscription_days,
            traffic=traffic_text,
            devices=result.subscription_device_limit,
        )

    return None


async def handle_potential_referral_code(
    message: types.Message, 
    state: FSMContext, 
    db: AsyncSession
):
    current_state = await state.get_state()
    logger.info(f"🔍 REFERRAL CHECK: Проверка сообщения '{message.text}' в состоянии {current_state}")
    
    if current_state not in [
        RegistrationStates.waiting_for_rules_accept.state,
        RegistrationStates.waiting_for_referral_code.state,
        None 
    ]:
        return False
    
    user = await get_user_by_telegram_id(db, message.from_user.id)
    if user and user.status == UserStatus.ACTIVE.value:
        return False

    data = await state.get_data() or {}
    language = (
        data.get("language")
        or (getattr(user, "language", None) if user else None)
        or DEFAULT_LANGUAGE
    )
    texts = get_texts(language)

    potential_code = message.text.strip()
    if len(potential_code) < 4 or len(potential_code) > 20:
        return False

    referrer = await get_user_by_referral_code(db, potential_code)
    if not referrer:
        await message.answer(texts.t(
            "REFERRAL_CODE_INVALID_HELP",
            "❌ Неверный реферальный код.\n\n"
            "💡 Если у вас есть реферальный код, убедитесь что он введен правильно.\n"
            "⏭️ Для продолжения регистрации без реферального кода используйте команду /start",
        ))
        return True

    data['referral_code'] = potential_code
    data['referrer_id'] = referrer.id
    await state.set_data(data)

    await message.answer(texts.t("REFERRAL_CODE_ACCEPTED", "✅ Реферальный код принят!"))
    logger.info(f"✅ Реферальный код {potential_code} применен для пользователя {message.from_user.id}")
    
    if current_state != RegistrationStates.waiting_for_referral_code.state:
        language = data.get('language', DEFAULT_LANGUAGE)
        texts = get_texts(language)
        
        rules_text = await get_rules_text(language)

        await message.answer(
            rules_text,
            reply_markup=get_rules_keyboard(language)
        )
        await state.set_state(RegistrationStates.waiting_for_rules_accept)
        logger.info("📋 Правила отправлены после ввода реферального кода")
    else:
        await complete_registration(message, state, db)
    
    return True 


async def cmd_start(message: types.Message, state: FSMContext, db: AsyncSession, db_user=None):
    logger.info(f"🚀 START: Обработка /start от {message.from_user.id}")
    
    referral_code = None
    campaign = None
    start_args = message.text.split()
    if len(start_args) > 1:
        start_parameter = start_args[1]
        campaign = await get_campaign_by_start_parameter(
            db,
            start_parameter,
            only_active=True,
        )

        if campaign:
            logger.info(
                "📣 Найдена рекламная кампания %s (start=%s)",
                campaign.id,
                campaign.start_parameter,
            )
            await state.update_data(campaign_id=campaign.id)
        else:
            referral_code = start_parameter
            logger.info(f"🔎 Найден реферальный код: {referral_code}")

    if referral_code:
        await state.update_data(referral_code=referral_code)
    
    user = db_user if db_user else await get_user_by_telegram_id(db, message.from_user.id)
    
    if user and user.status != UserStatus.DELETED.value:
        logger.info(f"✅ Активный пользователь найден: {user.telegram_id}")
        
        profile_updated = False
        
        if user.username != message.from_user.username:
            old_username = user.username
            user.username = message.from_user.username
            logger.info(f"📝 Username обновлен: '{old_username}' → '{user.username}'")
            profile_updated = True
        
        if user.first_name != message.from_user.first_name:
            old_first_name = user.first_name
            user.first_name = message.from_user.first_name
            logger.info(f"📝 Имя обновлено: '{old_first_name}' → '{user.first_name}'")
            profile_updated = True
        
        if user.last_name != message.from_user.last_name:
            old_last_name = user.last_name
            user.last_name = message.from_user.last_name
            logger.info(f"📝 Фамилия обновлена: '{old_last_name}' → '{user.last_name}'")
            profile_updated = True
        
        user.last_activity = datetime.utcnow()
        
        if profile_updated:
            user.updated_at = datetime.utcnow()
            await db.commit()
            await db.refresh(user)
            logger.info(f"💾 Профиль пользователя {user.telegram_id} обновлен")
        else:
            await db.commit()
        
        texts = get_texts(user.language)

        if referral_code and not user.referred_by_id:
            await message.answer(
                texts.t(
                    "ALREADY_REGISTERED_REFERRAL",
                    "ℹ️ Вы уже зарегистрированы в системе. Реферальная ссылка не может быть применена.",
                )
            )

        if campaign:
            try:
                await message.answer(
                    texts.t(
                        "CAMPAIGN_EXISTING_USERL",
                        "ℹ️ Эта рекламная ссылка доступна только новым пользователям.",
                    )
                )
            except Exception as e:
                logger.error(
                    f"Ошибка отправки уведомления о рекламной кампании: {e}"
                )
        
        has_active_subscription = user.subscription is not None
        subscription_is_active = False
        
        if user.subscription:
            subscription_is_active = user.subscription.is_active
        
        menu_text = await get_main_menu_text(user, texts, db)
        
        await message.answer(
            menu_text,
            reply_markup=get_main_menu_keyboard(
                language=user.language,
                is_admin=settings.is_admin(user.telegram_id),
                has_had_paid_subscription=user.has_had_paid_subscription,
                has_active_subscription=has_active_subscription,
                subscription_is_active=subscription_is_active,
                balance_kopeks=user.balance_kopeks,
                subscription=user.subscription
            ),
            parse_mode="HTML"
        )
        await state.clear()
        return
    
    if user and user.status == UserStatus.DELETED.value:
        logger.info(f"🔄 Удаленный пользователь {user.telegram_id} начинает повторную регистрацию")
        
        try:
            from app.services.user_service import UserService
            from app.database.models import (
                Subscription, Transaction, PromoCodeUse, 
                ReferralEarning, SubscriptionServer
            )
            from sqlalchemy import delete
            
            if user.subscription:
                await db.execute(
                    delete(SubscriptionServer).where(
                        SubscriptionServer.subscription_id == user.subscription.id
                    )
                )
                logger.info(f"🗑️ Удалены записи SubscriptionServer")
            
            if user.subscription:
                await db.delete(user.subscription)
                logger.info(f"🗑️ Удалена подписка пользователя")
            
            await db.execute(
                delete(PromoCodeUse).where(PromoCodeUse.user_id == user.id)
            )
            
            await db.execute(
                delete(ReferralEarning).where(ReferralEarning.user_id == user.id)
            )
            await db.execute(
                delete(ReferralEarning).where(ReferralEarning.referral_id == user.id)
            )
            
            await db.execute(
                delete(Transaction).where(Transaction.user_id == user.id)
            )
            
            user.status = UserStatus.ACTIVE.value
            user.balance_kopeks = 0
            user.remnawave_uuid = None
            user.has_had_paid_subscription = False
            user.referred_by_id = None
            
            user.username = message.from_user.username
            user.first_name = message.from_user.first_name
            user.last_name = message.from_user.last_name
            user.updated_at = datetime.utcnow()
            user.last_activity = datetime.utcnow()
            
            from app.utils.user_utils import generate_unique_referral_code
            user.referral_code = await generate_unique_referral_code(db, user.telegram_id)
            
            await db.commit()
            
            logger.info(f"✅ Пользователь {user.telegram_id} подготовлен к восстановлению")
            
        except Exception as e:
            logger.error(f"❌ Ошибка подготовки к восстановлению: {e}")
            await db.rollback()
    else:
        logger.info(f"🆕 Новый пользователь, начинаем регистрацию")
    
    language = DEFAULT_LANGUAGE
    texts = get_texts(language)
    
    data = await state.get_data() or {}
    data['language'] = language
    await state.set_data(data)
    logger.info(f"💾 Установлен язык по умолчанию: {language}")
    if settings.SKIP_RULES_ACCEPT:
        logger.info("⚙️ SKIP_RULES_ACCEPT включен - пропускаем принятие правил")
        if data.get('referral_code'):
            referrer = await get_user_by_referral_code(db, data['referral_code'])
            if referrer:
                data['referrer_id'] = referrer.id
                await state.set_data(data)
                logger.info(f"✅ Реферер найден: {referrer.id}")

        if settings.SKIP_REFERRAL_CODE or data.get('referral_code'):
            await complete_registration(message, state, db)
        else:
            try:
                await message.answer(
                    texts.t(
                        "REFERRAL_CODE_QUESTION",
                        "У вас есть реферальный код? Введите его или нажмите 'Пропустить'",
                    ),
                    reply_markup=get_referral_code_keyboard(language)
                )
                await state.set_state(RegistrationStates.waiting_for_referral_code)
                logger.info("🔍 Ожидание ввода реферального кода")
            except Exception as e:
                logger.error(f"Ошибка при показе вопроса о реферальном коде: {e}")
                await complete_registration(message, state, db)
        return

    rules_text = await get_rules_text(language)

    await message.answer(
        rules_text,
        reply_markup=get_rules_keyboard(language)
    )
    logger.info(f"📋 Правила отправлены")

    await state.set_state(RegistrationStates.waiting_for_rules_accept)
    current_state = await state.get_state()
    logger.info(f"📊 Установлено состояние: {current_state}")


async def process_rules_accept(
    callback: types.CallbackQuery, 
    state: FSMContext, 
    db: AsyncSession
):
    
    logger.info(f"📋 RULES: Начало обработки правил")
    logger.info(f"📊 Callback data: {callback.data}")
    logger.info(f"👤 User: {callback.from_user.id}")
    
    current_state = await state.get_state()
    logger.info(f"📊 Текущее состояние: {current_state}")
    
    language = DEFAULT_LANGUAGE
    texts = get_texts(language)

    try:
        await callback.answer()

        data = await state.get_data() or {}
        language = data.get('language', language)
        texts = get_texts(language)
        
        if callback.data == 'rules_accept':
            logger.info(f"✅ Правила приняты пользователем {callback.from_user.id}")
            
            try:
                await callback.message.delete()
                logger.info(f"🗑️ Сообщение с правилами удалено")
            except Exception as e:
                logger.warning(f"⚠️ Не удалось удалить сообщение с правилами: {e}")
                try:
                    await callback.message.edit_text(
                        texts.t(
                            "RULES_ACCEPTED_PROCESSING",
                            "✅ Правила приняты! Завершаем регистрацию...",
                        ),
                        reply_markup=None
                    )
                except Exception:
                    pass
            
            if data.get('referral_code'):
                logger.info(f"🎫 Найден реферальный код из deep link: {data['referral_code']}")

                referrer = await get_user_by_referral_code(db, data['referral_code'])
                if referrer:
                    data['referrer_id'] = referrer.id
                    await state.set_data(data)
                    logger.info(f"✅ Реферер найден: {referrer.id}")

                await complete_registration_from_callback(callback, state, db)
            else:
                if settings.SKIP_REFERRAL_CODE:
                    logger.info("⚙️ SKIP_REFERRAL_CODE включен - пропускаем запрос реферального кода")
                    await complete_registration_from_callback(callback, state, db)
                else:
                    try:
                        await callback.message.answer(
                            texts.t(
                                "REFERRAL_CODE_QUESTION",
                                "У вас есть реферальный код? Введите его или нажмите 'Пропустить'",
                            ),
                            reply_markup=get_referral_code_keyboard(language)
                        )
                        await state.set_state(RegistrationStates.waiting_for_referral_code)
                        logger.info(f"🔍 Ожидание ввода реферального кода")
                    except Exception as e:
                        logger.error(f"Ошибка при показе вопроса о реферальном коде: {e}")
                        await complete_registration_from_callback(callback, state, db)
                    
        else:
            logger.info(f"❌ Правила отклонены пользователем {callback.from_user.id}")
            
            rules_required_text = texts.t(
                "RULES_REQUIRED",
                "Для использования бота необходимо принять правила сервиса.",
            )

            try:
                await callback.message.edit_text(
                    rules_required_text,
                    reply_markup=get_rules_keyboard(language)
                )
            except Exception as e:
                logger.error(f"Ошибка при показе сообщения об отклонении правил: {e}")
                await callback.message.edit_text(
                    rules_required_text,
                    reply_markup=get_rules_keyboard(language)
                )
        
        logger.info(f"✅ Правила обработаны для пользователя {callback.from_user.id}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка обработки правил: {e}", exc_info=True)
        await callback.answer(
            texts.t("ERROR_TRY_AGAIN", "❌ Произошла ошибка. Попробуйте еще раз."),
            show_alert=True,
        )

        try:
            data = await state.get_data() or {}
            language = data.get('language', language)
            texts = get_texts(language)
            await callback.message.answer(
                texts.t(
                    "ERROR_RULES_RETRY",
                    "Произошла ошибка. Попробуйте принять правила еще раз:",
                ),
                reply_markup=get_rules_keyboard(language)
            )
            await state.set_state(RegistrationStates.waiting_for_rules_accept)
        except:
            pass


async def process_referral_code_input(
    message: types.Message, 
    state: FSMContext, 
    db: AsyncSession
):
    
    logger.info(f"🎫 REFERRAL: Обработка реферального кода: {message.text}")
    
    data = await state.get_data() or {}
    language = data.get('language', DEFAULT_LANGUAGE)
    texts = get_texts(language)

    referral_code = message.text.strip()

    referrer = await get_user_by_referral_code(db, referral_code)
    if referrer:
        data['referrer_id'] = referrer.id
        await state.set_data(data)
        await message.answer(texts.t("REFERRAL_CODE_ACCEPTED", "✅ Реферальный код принят!"))
        logger.info(f"✅ Реферальный код применен")
    else:
        await message.answer(texts.t("REFERRAL_CODE_INVALID", "❌ Неверный реферальный код"))
        logger.info(f"❌ Неверный реферальный код")
        return
    
    await complete_registration(message, state, db)


async def process_referral_code_skip(
    callback: types.CallbackQuery,
    state: FSMContext,
    db: AsyncSession
):

    logger.info(f"⭐️ SKIP: Пропуск реферального кода от пользователя {callback.from_user.id}")
    await callback.answer()

    data = await state.get_data() or {}
    language = data.get('language', DEFAULT_LANGUAGE)
    texts = get_texts(language)

    try:
        await callback.message.delete()
        logger.info(f"🗑️ Сообщение с вопросом о реферальном коде удалено")
    except Exception as e:
        logger.warning(f"⚠️ Не удалось удалить сообщение с вопросом о реферальном коде: {e}")
        try:
            await callback.message.edit_text(
                texts.t("REGISTRATION_COMPLETING", "✅ Завершаем регистрацию..."),
                reply_markup=None
            )
        except:
            pass
    
    await complete_registration_from_callback(callback, state, db)



async def complete_registration_from_callback(
    callback: types.CallbackQuery,
    state: FSMContext, 
    db: AsyncSession
):
    logger.info(f"🎯 COMPLETE: Завершение регистрации для пользователя {callback.from_user.id}")
    
    from sqlalchemy.orm import selectinload
    
    existing_user = await get_user_by_telegram_id(db, callback.from_user.id)
    
    if existing_user and existing_user.status == UserStatus.ACTIVE.value:
        logger.warning(f"⚠️ Пользователь {callback.from_user.id} уже активен! Показываем главное меню.")
        texts = get_texts(existing_user.language)
        
        data = await state.get_data() or {}
        if data.get('referral_code') and not existing_user.referred_by_id:
            await callback.message.answer(
                texts.t(
                    "ALREADY_REGISTERED_REFERRAL",
                    "ℹ️ Вы уже зарегистрированы в системе. Реферальная ссылка не может быть применена.",
                )
            )
        
        await db.refresh(existing_user, ['subscription'])
        
        has_active_subscription = existing_user.subscription is not None
        subscription_is_active = False
        
        if existing_user.subscription:
            subscription_is_active = existing_user.subscription.is_active
        
        menu_text = await get_main_menu_text(existing_user, texts, db)
        
        try:
            await callback.message.answer(
                menu_text,
                reply_markup=get_main_menu_keyboard(
                    language=existing_user.language,
                    is_admin=settings.is_admin(existing_user.telegram_id),
                    has_had_paid_subscription=existing_user.has_had_paid_subscription,
                    has_active_subscription=has_active_subscription,
                    subscription_is_active=subscription_is_active,
                    balance_kopeks=existing_user.balance_kopeks,
                    subscription=existing_user.subscription
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка при показе главного меню существующему пользователю: {e}")
            await callback.message.answer(
                texts.t(
                    "WELCOME_FALLBACK",
                    "Добро пожаловать, {user_name}!",
                ).format(user_name=existing_user.full_name)
            )
        
        await state.clear()
        return
    
    data = await state.get_data() or {}
    language = data.get('language', DEFAULT_LANGUAGE)
    texts = get_texts(language)
    
    referrer_id = data.get('referrer_id')
    if not referrer_id and data.get('referral_code'):
        referrer = await get_user_by_referral_code(db, data['referral_code'])
        if referrer:
            referrer_id = referrer.id
    
    if existing_user and existing_user.status == UserStatus.DELETED.value:
        logger.info(f"🔄 Восстанавливаем удаленного пользователя {callback.from_user.id}")
        
        existing_user.username = callback.from_user.username
        existing_user.first_name = callback.from_user.first_name
        existing_user.last_name = callback.from_user.last_name
        existing_user.language = language
        existing_user.referred_by_id = referrer_id
        existing_user.status = UserStatus.ACTIVE.value
        existing_user.balance_kopeks = 0
        existing_user.has_had_paid_subscription = False
        
        from datetime import datetime
        existing_user.updated_at = datetime.utcnow()
        existing_user.last_activity = datetime.utcnow()
        
        await db.commit()
        await db.refresh(existing_user, ['subscription'])
        
        user = existing_user
        logger.info(f"✅ Пользователь {callback.from_user.id} восстановлен")
        
    elif not existing_user:
        logger.info(f"🆕 Создаем нового пользователя {callback.from_user.id}")
        
        referral_code = await generate_unique_referral_code(db, callback.from_user.id)
        
        user = await create_user(
            db=db,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
            last_name=callback.from_user.last_name,
            language=language,
            referred_by_id=referrer_id,
            referral_code=referral_code 
        )
        await db.refresh(user, ['subscription'])
    else:
        logger.info(f"🔄 Обновляем существующего пользователя {callback.from_user.id}")
        existing_user.status = UserStatus.ACTIVE.value
        existing_user.language = language
        if referrer_id and not existing_user.referred_by_id:
            existing_user.referred_by_id = referrer_id
        
        from datetime import datetime
        existing_user.updated_at = datetime.utcnow()
        existing_user.last_activity = datetime.utcnow()
        
        await db.commit()
        await db.refresh(existing_user, ['subscription'])
        user = existing_user
    
    if referrer_id:
        try:
            await process_referral_registration(db, user.id, referrer_id, callback.bot)
            logger.info(f"✅ Реферальная регистрация обработана для {user.id}")
        except Exception as e:
            logger.error(f"Ошибка при обработке реферальной регистрации: {e}")

    campaign_message = await _apply_campaign_bonus_if_needed(db, user, data, texts)

    try:
        await db.refresh(user)
    except Exception as refresh_error:
        logger.error(
            "Ошибка обновления данных пользователя %s после бонуса кампании: %s",
            user.telegram_id,
            refresh_error,
        )

    try:
        await db.refresh(user, ["subscription"])
    except Exception as refresh_subscription_error:
        logger.error(
            "Ошибка обновления подписки пользователя %s после бонуса кампании: %s",
            user.telegram_id,
            refresh_subscription_error,
        )

    await state.clear()

    if campaign_message:
        try:
            await callback.message.answer(campaign_message)
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения о бонусе кампании: {e}")

    from app.database.crud.welcome_text import get_welcome_text_for_user
    offer_text = await get_welcome_text_for_user(db, callback.from_user)

    if offer_text:
        try:
            await callback.message.answer(
                offer_text,
                reply_markup=get_post_registration_keyboard(user.language),
            )
            logger.info(f"✅ Приветственное сообщение отправлено пользователю {user.telegram_id}")
        except Exception as e:
            logger.error(f"Ошибка при отправке приветственного сообщения: {e}")
    else:
        logger.info(f"ℹ️ Приветственные сообщения отключены, показываем главное меню для пользователя {user.telegram_id}")
        
        has_active_subscription = bool(getattr(user, "subscription", None))
        subscription_is_active = False

        if getattr(user, "subscription", None):
            subscription_is_active = user.subscription.is_active
        
        menu_text = await get_main_menu_text(user, texts, db)
        
        try:
            await callback.message.answer(
                menu_text,
                reply_markup=get_main_menu_keyboard(
                    language=user.language,
                    is_admin=settings.is_admin(user.telegram_id),
                    has_had_paid_subscription=user.has_had_paid_subscription,
                    has_active_subscription=has_active_subscription,
                    subscription_is_active=subscription_is_active,
                    balance_kopeks=user.balance_kopeks,
                    subscription=user.subscription
                ),
                parse_mode="HTML"
            )
            logger.info(f"✅ Главное меню показано пользователю {user.telegram_id}")
        except Exception as e:
            logger.error(f"Ошибка при показе главного меню: {e}")
            await callback.message.answer(
                texts.t(
                    "WELCOME_FALLBACK",
                    "Добро пожаловать, {user_name}!",
                ).format(user_name=user.full_name)
            )

    logger.info(f"✅ Регистрация завершена для пользователя: {user.telegram_id}")


async def complete_registration(
    message: types.Message, 
    state: FSMContext, 
    db: AsyncSession
):
    logger.info(f"🎯 COMPLETE: Завершение регистрации для пользователя {message.from_user.id}")
    
    existing_user = await get_user_by_telegram_id(db, message.from_user.id)
    
    if existing_user and existing_user.status == UserStatus.ACTIVE.value:
        logger.warning(f"⚠️ Пользователь {message.from_user.id} уже активен! Показываем главное меню.")
        texts = get_texts(existing_user.language)
        
        data = await state.get_data() or {}
        if data.get('referral_code') and not existing_user.referred_by_id:
            await message.answer(
                texts.t(
                    "ALREADY_REGISTERED_REFERRAL",
                    "ℹ️ Вы уже зарегистрированы в системе. Реферальная ссылка не может быть применена.",
                )
            )
        
        await db.refresh(existing_user, ['subscription'])
        
        has_active_subscription = existing_user.subscription is not None
        subscription_is_active = False
        
        if existing_user.subscription:
            subscription_is_active = existing_user.subscription.is_active
        
        menu_text = await get_main_menu_text(existing_user, texts, db)
        
        try:
            await message.answer(
                menu_text,
                reply_markup=get_main_menu_keyboard(
                    language=existing_user.language,
                    is_admin=settings.is_admin(existing_user.telegram_id),
                    has_had_paid_subscription=existing_user.has_had_paid_subscription,
                    has_active_subscription=has_active_subscription,
                    subscription_is_active=subscription_is_active,
                    balance_kopeks=existing_user.balance_kopeks,
                    subscription=existing_user.subscription
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка при показе главного меню существующему пользователю: {e}")
            await message.answer(
                texts.t(
                    "WELCOME_FALLBACK",
                    "Добро пожаловать, {user_name}!",
                ).format(user_name=existing_user.full_name)
            )
        
        await state.clear()
        return
    
    data = await state.get_data() or {}
    language = data.get('language', DEFAULT_LANGUAGE)
    texts = get_texts(language)
    
    referrer_id = data.get('referrer_id')
    if not referrer_id and data.get('referral_code'):
        referrer = await get_user_by_referral_code(db, data['referral_code'])
        if referrer:
            referrer_id = referrer.id
    
    if existing_user and existing_user.status == UserStatus.DELETED.value:
        logger.info(f"🔄 Восстанавливаем удаленного пользователя {message.from_user.id}")
        
        existing_user.username = message.from_user.username
        existing_user.first_name = message.from_user.first_name
        existing_user.last_name = message.from_user.last_name
        existing_user.language = language
        existing_user.referred_by_id = referrer_id
        existing_user.status = UserStatus.ACTIVE.value
        existing_user.balance_kopeks = 0
        existing_user.has_had_paid_subscription = False
        
        from datetime import datetime
        existing_user.updated_at = datetime.utcnow()
        existing_user.last_activity = datetime.utcnow()
        
        await db.commit()
        await db.refresh(existing_user, ['subscription'])
        
        user = existing_user
        logger.info(f"✅ Пользователь {message.from_user.id} восстановлен")
        
    elif not existing_user:
        logger.info(f"🆕 Создаем нового пользователя {message.from_user.id}")
        
        referral_code = await generate_unique_referral_code(db, message.from_user.id)
        
        user = await create_user(
            db=db,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            language=language,
            referred_by_id=referrer_id,
            referral_code=referral_code
        )
        await db.refresh(user, ['subscription'])
    else:
        logger.info(f"🔄 Обновляем существующего пользователя {message.from_user.id}")
        existing_user.status = UserStatus.ACTIVE.value
        existing_user.language = language
        if referrer_id and not existing_user.referred_by_id:
            existing_user.referred_by_id = referrer_id
        
        from datetime import datetime
        existing_user.updated_at = datetime.utcnow()
        existing_user.last_activity = datetime.utcnow()
        
        await db.commit()
        await db.refresh(existing_user, ['subscription'])
        user = existing_user
    
    if referrer_id:
        try:
            await process_referral_registration(db, user.id, referrer_id, message.bot)
            logger.info(f"✅ Реферальная регистрация обработана для {user.id}")
        except Exception as e:
            logger.error(f"Ошибка при обработке реферальной регистрации: {e}")

    campaign_message = await _apply_campaign_bonus_if_needed(db, user, data, texts)

    try:
        await db.refresh(user)
    except Exception as refresh_error:
        logger.error(
            "Ошибка обновления данных пользователя %s после бонуса кампании: %s",
            user.telegram_id,
            refresh_error,
        )

    try:
        await db.refresh(user, ["subscription"])
    except Exception as refresh_subscription_error:
        logger.error(
            "Ошибка обновления подписки пользователя %s после бонуса кампании: %s",
            user.telegram_id,
            refresh_subscription_error,
        )

    await state.clear()

    if campaign_message:
        try:
            await message.answer(campaign_message)
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения о бонусе кампании: {e}")

    from app.database.crud.welcome_text import get_welcome_text_for_user
    offer_text = await get_welcome_text_for_user(db, message.from_user)

    if offer_text:
        try:
            await message.answer(
                offer_text,
                reply_markup=get_post_registration_keyboard(user.language),
            )
            logger.info(f"✅ Приветственное сообщение отправлено пользователю {user.telegram_id}")
        except Exception as e:
            logger.error(f"Ошибка при отправке приветственного сообщения: {e}")
    else:
        logger.info(f"ℹ️ Приветственные сообщения отключены, показываем главное меню для пользователя {user.telegram_id}")
        
        has_active_subscription = bool(getattr(user, "subscription", None))
        subscription_is_active = False

        if getattr(user, "subscription", None):
            subscription_is_active = user.subscription.is_active
        
        menu_text = await get_main_menu_text(user, texts, db)
        
        try:
            await message.answer(
                menu_text,
                reply_markup=get_main_menu_keyboard(
                    language=user.language,
                    is_admin=settings.is_admin(user.telegram_id),
                    has_had_paid_subscription=user.has_had_paid_subscription,
                    has_active_subscription=has_active_subscription,
                    subscription_is_active=subscription_is_active,
                    balance_kopeks=user.balance_kopeks,
                    subscription=user.subscription
                ),
                parse_mode="HTML"
            )
            logger.info(f"✅ Главное меню показано пользователю {user.telegram_id}")
        except Exception as e:
            logger.error(f"Ошибка при показе главного меню: {e}")
            await message.answer(
                texts.t(
                    "WELCOME_FALLBACK",
                    "Добро пожаловать, {user_name}!",
                ).format(user_name=user.full_name)
            )

    logger.info(f"✅ Регистрация завершена для пользователя: {user.telegram_id}")


def _get_subscription_status(user, texts):
    if not user or not hasattr(user, "subscription") or not user.subscription:
        return texts.t("SUBSCRIPTION_NONE", "Нет активной подписки")

    subscription = user.subscription

    from datetime import datetime

    end_date = getattr(subscription, "end_date", None)
    current_time = datetime.utcnow()

    if end_date and end_date <= current_time:
        return texts.t(
            "SUB_STATUS_EXPIRED",
            "🔴 Истекла\n📅 {end_date}",
        ).format(end_date=end_date.strftime('%d.%m.%Y'))

    if not end_date:
        return texts.t("SUBSCRIPTION_ACTIVE", "✅ Активна")

    days_left = (end_date - current_time).days
    is_trial = getattr(subscription, "is_trial", False)

    if is_trial:
        if days_left > 1:
            return texts.t(
                "SUB_STATUS_TRIAL_ACTIVE",
                "🎁 Тестовая подписка\n📅 до {end_date} ({days} дн.)",
            ).format(end_date=end_date.strftime('%d.%m.%Y'), days=days_left)
        if days_left == 1:
            return texts.t(
                "SUB_STATUS_TRIAL_TOMORROW",
                "🎁 Тестовая подписка\n⚠️ истекает завтра!",
            )
        return texts.t(
            "SUB_STATUS_TRIAL_TODAY",
            "🎁 Тестовая подписка\n⚠️ истекает сегодня!",
        )

    if days_left > 7:
        return texts.t(
            "SUB_STATUS_ACTIVE_LONG",
            "💎 Активна\n📅 до {end_date} ({days} дн.)",
        ).format(end_date=end_date.strftime('%d.%m.%Y'), days=days_left)
    if days_left > 1:
        return texts.t(
            "SUB_STATUS_ACTIVE_FEW_DAYS",
            "💎 Активна\n⚠️ истекает через {days} дн.",
        ).format(days=days_left)
    if days_left == 1:
        return texts.t(
            "SUB_STATUS_ACTIVE_TOMORROW",
            "💎 Активна\n⚠️ истекает завтра!",
        )
    return texts.t(
        "SUB_STATUS_ACTIVE_TODAY",
        "💎 Активна\n⚠️ истекает сегодня!",
    )


def _get_subscription_status_simple(texts):
    return texts.t("SUBSCRIPTION_NONE", "Нет активной подписки")


def _insert_random_message(base_text: str, random_message: str, action_prompt: str) -> str:
    if not random_message:
        return base_text

    prompt = action_prompt or ""
    if prompt and prompt in base_text:
        parts = base_text.split(prompt, 1)
        if len(parts) == 2:
            return f"{parts[0]}\n{random_message}\n\n{prompt}{parts[1]}"
        return base_text.replace(prompt, f"\n{random_message}\n\n{prompt}", 1)

    return f"{base_text}\n\n{random_message}"


def get_referral_code_keyboard(language: str):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=texts.t("REFERRAL_CODE_SKIP", "⭐️ Пропустить"),
            callback_data="referral_skip"
        )]
    ])

async def get_main_menu_text(user, texts, db: AsyncSession):

    base_text = texts.MAIN_MENU.format(
        user_name=user.full_name,
        subscription_status=_get_subscription_status(user, texts)
    )

    action_prompt = texts.t("MAIN_MENU_ACTION_PROMPT", "Выберите действие:")

    try:
        random_message = await get_random_active_message(db)
        if random_message:
            return _insert_random_message(base_text, random_message, action_prompt)

    except Exception as e:
        logger.error(f"Ошибка получения случайного сообщения: {e}")

    return base_text

async def get_main_menu_text_simple(user_name, texts, db: AsyncSession):

    base_text = texts.MAIN_MENU.format(
        user_name=user_name,
        subscription_status=_get_subscription_status_simple(texts)
    )

    action_prompt = texts.t("MAIN_MENU_ACTION_PROMPT", "Выберите действие:")

    try:
        random_message = await get_random_active_message(db)
        if random_message:
            return _insert_random_message(base_text, random_message, action_prompt)

    except Exception as e:
        logger.error(f"Ошибка получения случайного сообщения: {e}")

    return base_text


async def required_sub_channel_check(
    query: types.CallbackQuery,
    bot: Bot,
    state: FSMContext,
    db: AsyncSession,
    db_user=None
):
    language = DEFAULT_LANGUAGE
    texts = get_texts(language)

    try:
        state_data = await state.get_data() or {}

        user = db_user
        if not user:
            user = await get_user_by_telegram_id(db, query.from_user.id)

        if user and getattr(user, "language", None):
            language = user.language
        elif state_data.get("language"):
            language = state_data["language"]

        texts = get_texts(language)

        chat_member = await bot.get_chat_member(
            chat_id=settings.CHANNEL_SUB_ID,
            user_id=query.from_user.id
        )

        if chat_member.status not in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
            return await query.answer(
                texts.t("CHANNEL_SUBSCRIBE_REQUIRED_ALERT", "❌ Вы не подписались на канал!"),
                show_alert=True,
            )

        await query.answer(
            texts.t("CHANNEL_SUBSCRIBE_THANKS", "✅ Спасибо за подписку"),
            show_alert=True,
        )

        try:
            await query.message.delete()
        except Exception as e:
            logger.warning(f"Не удалось удалить сообщение: {e}")

        if user and user.status != UserStatus.DELETED.value:
            has_active_subscription = bool(user.subscription)
            subscription_is_active = bool(user.subscription and user.subscription.is_active)

            menu_text = await get_main_menu_text(user, texts, db)

            from app.utils.message_patch import LOGO_PATH
            from aiogram.types import FSInputFile

            keyboard = get_main_menu_keyboard(
                language=user.language,
                is_admin=settings.is_admin(user.telegram_id),
                has_had_paid_subscription=user.has_had_paid_subscription,
                has_active_subscription=has_active_subscription,
                subscription_is_active=subscription_is_active,
                balance_kopeks=user.balance_kopeks,
                subscription=user.subscription,
            )

            if settings.ENABLE_LOGO_MODE:
                await bot.send_photo(
                    chat_id=query.from_user.id,
                    photo=FSInputFile(LOGO_PATH),
                    caption=menu_text,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )
            else:
                await bot.send_message(
                    chat_id=query.from_user.id,
                    text=menu_text,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )
        else:
            from app.keyboards.inline import get_rules_keyboard

            state_data['language'] = language
            await state.set_data(state_data)

            if settings.SKIP_RULES_ACCEPT:
                if settings.SKIP_REFERRAL_CODE:
                    from app.utils.user_utils import generate_unique_referral_code

                    referral_code = await generate_unique_referral_code(db, query.from_user.id)

                    user = await create_user(
                        db=db,
                        telegram_id=query.from_user.id,
                        username=query.from_user.username,
                        first_name=query.from_user.first_name,
                        last_name=query.from_user.last_name,
                        language=language,
                        referral_code=referral_code,
                    )

                    await bot.send_message(
                        chat_id=query.from_user.id,
                        text=texts.t("WELCOME_FALLBACK", "Добро пожаловать, {user_name}!").format(user_name=user.full_name),
                    )
                else:
                    await bot.send_message(
                        chat_id=query.from_user.id,
                        text=texts.t(
                            "REFERRAL_CODE_QUESTION",
                            "У вас есть реферальный код? Введите его или нажмите 'Пропустить'",
                        ),
                        reply_markup=get_referral_code_keyboard(language),
                    )
                    await state.set_state(RegistrationStates.waiting_for_referral_code)
            else:
                from app.utils.message_patch import LOGO_PATH
                from aiogram.types import FSInputFile

                rules_text = await get_rules_text(language)

                if settings.ENABLE_LOGO_MODE:
                    await bot.send_photo(
                        chat_id=query.from_user.id,
                        photo=FSInputFile(LOGO_PATH),
                        caption=rules_text,
                        reply_markup=get_rules_keyboard(language),
                    )
                else:
                    await bot.send_message(
                        chat_id=query.from_user.id,
                        text=rules_text,
                        reply_markup=get_rules_keyboard(language),
                    )
                await state.set_state(RegistrationStates.waiting_for_rules_accept)

    except Exception as e:
        logger.error(f"Ошибка в required_sub_channel_check: {e}")
        await query.answer(f"{texts.ERROR}!", show_alert=True)

def register_handlers(dp: Dispatcher):
    
    logger.info("🔧 === НАЧАЛО регистрации обработчиков start.py ===")
    
    dp.message.register(
        cmd_start,
        Command("start")
    )
    logger.info("✅ Зарегистрирован cmd_start")
    
    dp.callback_query.register(
        process_rules_accept,
        F.data.in_(["rules_accept", "rules_decline"]),
        StateFilter(RegistrationStates.waiting_for_rules_accept)
    )
    logger.info("✅ Зарегистрирован process_rules_accept")
    
    dp.callback_query.register(
        process_referral_code_skip,
        F.data == "referral_skip",
        StateFilter(RegistrationStates.waiting_for_referral_code)
    )
    logger.info("✅ Зарегистрирован process_referral_code_skip")
    
    dp.message.register(
        process_referral_code_input,
        StateFilter(RegistrationStates.waiting_for_referral_code)
    )
    logger.info("✅ Зарегистрирован process_referral_code_input")
    
    dp.message.register(
        handle_potential_referral_code,
        StateFilter(
            RegistrationStates.waiting_for_rules_accept,
            RegistrationStates.waiting_for_referral_code
        )
    )
    logger.info("✅ Зарегистрирован handle_potential_referral_code")

    dp.callback_query.register(
        required_sub_channel_check,
        F.data.in_(["sub_channel_check"])
    )
    logger.info("✅ Зарегистрирован required_sub_channel_check")
    
    logger.info("🔧 === КОНЕЦ регистрации обработчиков start.py ===")
 
