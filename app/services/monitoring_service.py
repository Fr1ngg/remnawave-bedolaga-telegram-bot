import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Set

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import FSInputFile
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database.database import get_db
from app.database.crud.discount_offer import (
    deactivate_expired_offers,
    upsert_discount_offer,
)
from app.database.crud.notification import (
    notification_sent,
    record_notification,
)
from app.database.crud.subscription import (
    deactivate_subscription,
    extend_subscription,
    get_expired_subscriptions,
    get_expiring_subscriptions,
    get_subscriptions_for_autopay,
)
from app.database.crud.user import (
    delete_user,
    get_inactive_users,
    get_user_by_id,
    subtract_user_balance,
)
from app.database.models import MonitoringLog, SubscriptionStatus, Subscription, User, Ticket, TicketStatus
from app.localization.texts import get_texts
from app.services.notification_settings_service import NotificationSettingsService
from app.services.payment_service import PaymentService
from app.services.subscription_service import SubscriptionService

from app.external.remnawave_api import (
    RemnaWaveAPIError,
    RemnaWaveUser,
    TrafficLimitStrategy,
    UserStatus,
)

logger = logging.getLogger(__name__)


LOGO_PATH = Path(settings.LOGO_FILE)


class MonitoringService:
    
    def __init__(self, bot=None):
        self.is_running = False
        self.subscription_service = SubscriptionService()
        self.payment_service = PaymentService()
        self.bot = bot
        self._notified_users: Set[str] = set()
        self._last_cleanup = datetime.utcnow()
        self._sla_task = None

    async def _send_message_with_logo(
        self,
        chat_id: int,
        text: str,
        reply_markup=None,
        parse_mode: Optional[str] = "HTML",
    ):
        """Отправляет сообщение, добавляя логотип при необходимости."""
        if not self.bot:
            raise RuntimeError("Bot instance is not available")

        if (
            settings.ENABLE_LOGO_MODE
            and LOGO_PATH.exists()
            and (text is None or len(text) <= 1000)
        ):
            try:
                return await self.bot.send_photo(
                    chat_id=chat_id,
                    photo=FSInputFile(LOGO_PATH),
                    caption=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                )
            except TelegramBadRequest as exc:
                logger.warning(
                    "Не удалось отправить сообщение с логотипом пользователю %s: %s. "
                    "Отправляем текстовое сообщение.",
                    chat_id,
                    exc,
                )

        return await self.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )

    @staticmethod
    def _is_unreachable_error(error: TelegramBadRequest) -> bool:
        message = str(error).lower()
        unreachable_markers = (
            "chat not found",
            "user is deactivated",
            "bot was blocked by the user",
            "bot can't initiate conversation",
            "can't initiate conversation",
            "user not found",
            "peer id invalid",
        )
        return any(marker in message for marker in unreachable_markers)

    def _handle_unreachable_user(self, user: User, error: Exception, context: str) -> bool:
        if isinstance(error, TelegramForbiddenError):
            logger.warning(
                "⚠️ Пользователь %s недоступен (%s): бот заблокирован",
                user.telegram_id,
                context,
            )
            return True

        if isinstance(error, TelegramBadRequest) and self._is_unreachable_error(error):
            logger.warning(
                "⚠️ Пользователь %s недоступен (%s): %s",
                user.telegram_id,
                context,
                error,
            )
            return True

        return False
    
    async def start_monitoring(self):
        if self.is_running:
            logger.warning("Мониторинг уже запущен")
            return
        
        self.is_running = True
        logger.info("🔄 Запуск службы мониторинга")
        # Start dedicated SLA loop with its own interval for timely 5-min checks
        try:
            if not self._sla_task or self._sla_task.done():
                self._sla_task = asyncio.create_task(self._sla_loop())
        except Exception as e:
            logger.error(f"Не удалось запустить SLA-мониторинг: {e}")
        
        while self.is_running:
            try:
                await self._monitoring_cycle()
                await asyncio.sleep(settings.MONITORING_INTERVAL * 60) 
                
            except Exception as e:
                logger.error(f"Ошибка в цикле мониторинга: {e}")
                await asyncio.sleep(60) 
    
    def stop_monitoring(self):
        self.is_running = False
        logger.info("ℹ️ Мониторинг остановлен")
        try:
            if self._sla_task and not self._sla_task.done():
                self._sla_task.cancel()
        except Exception:
            pass
    
    async def _monitoring_cycle(self):
        async for db in get_db():
            try:
                await self._cleanup_notification_cache()

                expired_offers = await deactivate_expired_offers(db)
                if expired_offers:
                    logger.info(f"🧹 Деактивировано {expired_offers} просроченных скидочных предложений")

                await self._check_expired_subscriptions(db)
                await self._check_expiring_subscriptions(db)
                await self._check_trial_expiring_soon(db)
                await self._check_trial_inactivity_notifications(db)
                await self._check_expired_subscription_followups(db)
                await self._process_autopayments(db)
                await self._cleanup_inactive_users(db)
                await self._sync_with_remnawave(db)
                
                await self._log_monitoring_event(
                    db, "monitoring_cycle_completed", 
                    "Цикл мониторинга успешно завершен", 
                    {"timestamp": datetime.utcnow().isoformat()}
                )
                
            except Exception as e:
                logger.error(f"Ошибка в цикле мониторинга: {e}")
                await self._log_monitoring_event(
                    db, "monitoring_cycle_error", 
                    f"Ошибка в цикле мониторинга: {str(e)}", 
                    {"error": str(e)},
                    is_success=False
                )
            finally:
                break 
    
    async def _cleanup_notification_cache(self):
        current_time = datetime.utcnow()
        
        if (current_time - self._last_cleanup).total_seconds() >= 3600:
            old_count = len(self._notified_users)
            self._notified_users.clear()
            self._last_cleanup = current_time
            logger.info(f"🧹 Очищен кеш уведомлений ({old_count} записей)")
    
    async def _check_expired_subscriptions(self, db: AsyncSession):
        try:
            expired_subscriptions = await get_expired_subscriptions(db)
            
            for subscription in expired_subscriptions:
                from app.database.crud.subscription import expire_subscription
                await expire_subscription(db, subscription)
                
                user = await get_user_by_id(db, subscription.user_id)
                if user and user.remnawave_uuid:
                    await self.subscription_service.disable_remnawave_user(user.remnawave_uuid)
                
                if user and self.bot:
                    await self._send_subscription_expired_notification(user)
                
                logger.info(f"🔴 Подписка пользователя {subscription.user_id} истекла и статус изменен на 'expired'")
            
            if expired_subscriptions:
                await self._log_monitoring_event(
                    db, "expired_subscriptions_processed",
                    f"Обработано {len(expired_subscriptions)} истёкших подписок",
                    {"count": len(expired_subscriptions)}
                )
                
        except Exception as e:
            logger.error(f"Ошибка проверки истёкших подписок: {e}")

    async def update_remnawave_user(
        self,
        db: AsyncSession,
        subscription: Subscription
    ) -> Optional[RemnaWaveUser]:
        
        try:
            user = await get_user_by_id(db, subscription.user_id)
            if not user or not user.remnawave_uuid:
                logger.error(f"RemnaWave UUID не найден для пользователя {subscription.user_id}")
                return None
            
            current_time = datetime.utcnow()
            is_active = (subscription.status == SubscriptionStatus.ACTIVE.value and 
                        subscription.end_date > current_time)
            
            if (subscription.status == SubscriptionStatus.ACTIVE.value and 
                subscription.end_date <= current_time):
                subscription.status = SubscriptionStatus.EXPIRED.value
                await db.commit()
                is_active = False
                logger.info(f"📝 Статус подписки {subscription.id} обновлен на 'expired'")
            
            async with self.api as api:
                updated_user = await api.update_user(
                    uuid=user.remnawave_uuid,
                    status=UserStatus.ACTIVE if is_active else UserStatus.EXPIRED,
                    expire_at=subscription.end_date,
                    traffic_limit_bytes=self._gb_to_bytes(subscription.traffic_limit_gb),
                    traffic_limit_strategy=TrafficLimitStrategy.MONTH,
                    hwid_device_limit=subscription.device_limit,
                    description=settings.format_remnawave_user_description(
                        full_name=user.full_name,
                        username=user.username,
                        telegram_id=user.telegram_id
                    ),
                    active_internal_squads=subscription.connected_squads
                )
                
                subscription.subscription_url = updated_user.subscription_url
                await db.commit()
                
                status_text = "активным" if is_active else "истёкшим"
                logger.info(f"✅ Обновлен RemnaWave пользователь {user.remnawave_uuid} со статусом {status_text}")
                return updated_user
                
        except RemnaWaveAPIError as e:
            logger.error(f"Ошибка обновления RemnaWave пользователя: {e}")
            return None
        except Exception as e:
            logger.error(f"Ошибка обновления RemnaWave пользователя: {e}")
            return None
    
    async def _check_expiring_subscriptions(self, db: AsyncSession):
        try:
            warning_days = settings.get_autopay_warning_days()
            all_processed_users = set() 
            
            for days in warning_days:
                expiring_subscriptions = await self._get_expiring_paid_subscriptions(db, days)
                sent_count = 0
                
                for subscription in expiring_subscriptions:
                    user = await get_user_by_id(db, subscription.user_id)
                    if not user:
                        continue

                    user_key = f"user_{user.telegram_id}_today"

                    if (await notification_sent(db, user.id, subscription.id, "expiring", days) or
                        user_key in all_processed_users):
                        logger.debug(f"🔄 Пропускаем дублирование для пользователя {user.telegram_id} на {days} дней")
                        continue

                    should_send = True
                    for other_days in warning_days:
                        if other_days < days:
                            other_subs = await self._get_expiring_paid_subscriptions(db, other_days)
                            if any(s.user_id == user.id for s in other_subs):
                                should_send = False
                                logger.debug(f"🎯 Пропускаем уведомление на {days} дней для пользователя {user.telegram_id}, есть более срочное на {other_days} дней")
                                break

                    if not should_send:
                        continue

                    if self.bot:
                        success = await self._send_subscription_expiring_notification(user, subscription, days)
                        if success:
                            await record_notification(db, user.id, subscription.id, "expiring", days)
                            all_processed_users.add(user_key)
                            sent_count += 1
                            logger.info(f"✅ Пользователю {user.telegram_id} отправлено уведомление об истечении подписки через {days} дней")
                        else:
                            logger.warning(f"❌ Не удалось отправить уведомление пользователю {user.telegram_id}")
                
                if sent_count > 0:
                    await self._log_monitoring_event(
                        db, "expiring_notifications_sent",
                        f"Отправлено {sent_count} уведомлений об истечении через {days} дней",
                        {"days": days, "count": sent_count}
                    )
                    
        except Exception as e:
            logger.error(f"Ошибка проверки истекающих подписок: {e}")
    
    async def _check_trial_expiring_soon(self, db: AsyncSession):
        try:
            threshold_time = datetime.utcnow() + timedelta(hours=2)

            result = await db.execute(
                select(Subscription)
                .options(selectinload(Subscription.user))
                .where(
                    and_(
                        Subscription.status == SubscriptionStatus.ACTIVE.value,
                        Subscription.is_trial == True,
                        Subscription.end_date <= threshold_time,
                        Subscription.end_date > datetime.utcnow()
                    )
                )
            )
            trial_expiring = result.scalars().all()
            
            for subscription in trial_expiring:
                user = subscription.user
                if not user:
                    continue

                if await notification_sent(db, user.id, subscription.id, "trial_2h"):
                    continue

                if self.bot:
                    success = await self._send_trial_ending_notification(user, subscription)
                    if success:
                        await record_notification(db, user.id, subscription.id, "trial_2h")
                        logger.info(f"🎁 Пользователю {user.telegram_id} отправлено уведомление об окончании тестовой подписки через 2 часа")
            
            if trial_expiring:
                await self._log_monitoring_event(
                    db, "trial_expiring_notifications_sent",
                    f"Отправлено {len(trial_expiring)} уведомлений об окончании тестовых подписок",
                    {"count": len(trial_expiring)}
                )
                
        except Exception as e:
            logger.error(f"Ошибка проверки истекающих тестовых подписок: {e}")

    async def _check_trial_inactivity_notifications(self, db: AsyncSession):
        if not NotificationSettingsService.are_notifications_globally_enabled():
            return
        if not self.bot:
            return

        try:
            now = datetime.utcnow()
            one_hour_ago = now - timedelta(hours=1)

            result = await db.execute(
                select(Subscription)
                .options(selectinload(Subscription.user))
                .where(
                    and_(
                        Subscription.status == SubscriptionStatus.ACTIVE.value,
                        Subscription.is_trial == True,
                        Subscription.start_date.isnot(None),
                        Subscription.start_date <= one_hour_ago,
                        Subscription.end_date > now,
                    )
                )
            )

            subscriptions = result.scalars().all()
            sent_1h = 0
            sent_24h = 0

            for subscription in subscriptions:
                user = subscription.user
                if not user:
                    continue

                if (subscription.traffic_used_gb or 0) > 0:
                    continue

                start_date = subscription.start_date
                if not start_date:
                    continue

                time_since_start = now - start_date

                if (NotificationSettingsService.is_trial_inactive_1h_enabled()
                        and timedelta(hours=1) <= time_since_start < timedelta(hours=24)):
                    if not await notification_sent(db, user.id, subscription.id, "trial_inactive_1h"):
                        success = await self._send_trial_inactive_notification(user, subscription, 1)
                        if success:
                            await record_notification(db, user.id, subscription.id, "trial_inactive_1h")
                            sent_1h += 1

                if NotificationSettingsService.is_trial_inactive_24h_enabled() and time_since_start >= timedelta(hours=24):
                    if not await notification_sent(db, user.id, subscription.id, "trial_inactive_24h"):
                        success = await self._send_trial_inactive_notification(user, subscription, 24)
                        if success:
                            await record_notification(db, user.id, subscription.id, "trial_inactive_24h")
                            sent_24h += 1

            if sent_1h or sent_24h:
                await self._log_monitoring_event(
                    db,
                    "trial_inactivity_notifications",
                    f"Отправлено {sent_1h} уведомлений спустя 1 час и {sent_24h} спустя 24 часа",
                    {"sent_1h": sent_1h, "sent_24h": sent_24h},
                )

        except Exception as e:
            logger.error(f"Ошибка проверки неактивных тестовых подписок: {e}")

    async def _check_expired_subscription_followups(self, db: AsyncSession):
        if not NotificationSettingsService.are_notifications_globally_enabled():
            return
        if not self.bot:
            return

        try:
            now = datetime.utcnow()

            result = await db.execute(
                select(Subscription)
                .options(selectinload(Subscription.user))
                .where(
                    and_(
                        Subscription.is_trial == False,
                        Subscription.end_date <= now,
                    )
                )
            )

            subscriptions = result.scalars().all()
            sent_day1 = 0
            sent_wave2 = 0
            sent_wave3 = 0

            for subscription in subscriptions:
                user = subscription.user
                if not user:
                    continue

                if subscription.end_date is None:
                    continue

                time_since_end = now - subscription.end_date
                if time_since_end.total_seconds() < 0:
                    continue

                days_since = time_since_end.total_seconds() / 86400

                # Day 1 reminder
                if NotificationSettingsService.is_expired_1d_enabled() and 1 <= days_since < 2:
                    if not await notification_sent(db, user.id, subscription.id, "expired_1d"):
                        success = await self._send_expired_day1_notification(user, subscription)
                        if success:
                            await record_notification(db, user.id, subscription.id, "expired_1d")
                            sent_day1 += 1

                # Second wave (2-3 days) discount
                if NotificationSettingsService.is_second_wave_enabled() and 2 <= days_since < 4:
                    if not await notification_sent(db, user.id, subscription.id, "expired_discount_wave2"):
                        percent = NotificationSettingsService.get_second_wave_discount_percent()
                        valid_hours = NotificationSettingsService.get_second_wave_valid_hours()
                        bonus_amount = settings.PRICE_30_DAYS * percent // 100
                        offer = await upsert_discount_offer(
                            db,
                            user_id=user.id,
                            subscription_id=subscription.id,
                            notification_type="expired_discount_wave2",
                            discount_percent=percent,
                            bonus_amount_kopeks=bonus_amount,
                            valid_hours=valid_hours,
                        )
                        success = await self._send_expired_discount_notification(
                            user,
                            subscription,
                            percent,
                            offer.expires_at,
                            offer.id,
                            "second",
                            bonus_amount,
                        )
                        if success:
                            await record_notification(db, user.id, subscription.id, "expired_discount_wave2")
                            sent_wave2 += 1

                # Third wave (N days) discount
                if NotificationSettingsService.is_third_wave_enabled():
                    trigger_days = NotificationSettingsService.get_third_wave_trigger_days()
                    if trigger_days <= days_since < trigger_days + 1:
                        if not await notification_sent(db, user.id, subscription.id, "expired_discount_wave3"):
                            percent = NotificationSettingsService.get_third_wave_discount_percent()
                            valid_hours = NotificationSettingsService.get_third_wave_valid_hours()
                            bonus_amount = settings.PRICE_30_DAYS * percent // 100
                            offer = await upsert_discount_offer(
                                db,
                                user_id=user.id,
                                subscription_id=subscription.id,
                                notification_type="expired_discount_wave3",
                                discount_percent=percent,
                                bonus_amount_kopeks=bonus_amount,
                                valid_hours=valid_hours,
                            )
                            success = await self._send_expired_discount_notification(
                                user,
                                subscription,
                                percent,
                                offer.expires_at,
                                offer.id,
                                "third",
                                bonus_amount,
                                trigger_days=trigger_days,
                            )
                            if success:
                                await record_notification(db, user.id, subscription.id, "expired_discount_wave3")
                                sent_wave3 += 1

            if sent_day1 or sent_wave2 or sent_wave3:
                await self._log_monitoring_event(
                    db,
                    "expired_followups_sent",
                    (
                        "Follow-ups: 1д={0}, скидка 2-3д={1}, скидка N={2}".format(
                            sent_day1,
                            sent_wave2,
                            sent_wave3,
                        )
                    ),
                    {
                        "day1": sent_day1,
                        "wave2": sent_wave2,
                        "wave3": sent_wave3,
                    },
                )

        except Exception as e:
            logger.error(f"Ошибка проверки напоминаний об истекшей подписке: {e}")

    async def _get_expiring_paid_subscriptions(self, db: AsyncSession, days_before: int) -> List[Subscription]:
        current_time = datetime.utcnow()
        threshold_date = current_time + timedelta(days=days_before)
        
        result = await db.execute(
            select(Subscription)
            .options(selectinload(Subscription.user))
            .where(
                and_(
                    Subscription.status == SubscriptionStatus.ACTIVE.value,
                    Subscription.is_trial == False, 
                    Subscription.end_date > current_time,
                    Subscription.end_date <= threshold_date
                )
            )
        )
        
        logger.debug(f"🔍 Поиск платных подписок, истекающих в ближайшие {days_before} дней")
        logger.debug(f"📅 Текущее время: {current_time}")
        logger.debug(f"📅 Пороговая дата: {threshold_date}")
        
        subscriptions = result.scalars().all()
        logger.info(f"📊 Найдено {len(subscriptions)} платных подписок для уведомлений")
        
        return subscriptions
    
    async def _process_autopayments(self, db: AsyncSession):
        try:
            current_time = datetime.utcnow()
            
            result = await db.execute(
                select(Subscription)
                .options(selectinload(Subscription.user))
                .where(
                    and_(
                        Subscription.status == SubscriptionStatus.ACTIVE.value,
                        Subscription.autopay_enabled == True,
                        Subscription.is_trial == False 
                    )
                )
            )
            all_autopay_subscriptions = result.scalars().all()
            
            autopay_subscriptions = []
            for sub in all_autopay_subscriptions:
                days_before_expiry = (sub.end_date - current_time).days
                if days_before_expiry <= sub.autopay_days_before:
                    autopay_subscriptions.append(sub)
            
            processed_count = 0
            failed_count = 0
            
            for subscription in autopay_subscriptions:
                user = subscription.user
                if not user:
                    continue
                
                renewal_cost = settings.PRICE_30_DAYS
                
                autopay_key = f"autopay_{user.telegram_id}_{subscription.id}"
                if autopay_key in self._notified_users:
                    continue
                
                if user.balance_kopeks >= renewal_cost:
                    success = await subtract_user_balance(
                        db, user, renewal_cost,
                        "Автопродление подписки"
                    )
                    
                    if success:
                        await extend_subscription(db, subscription, 30)
                        await self.subscription_service.update_remnawave_user(db, subscription)
                        
                        if self.bot:
                            await self._send_autopay_success_notification(user, renewal_cost, 30)
                        
                        processed_count += 1
                        self._notified_users.add(autopay_key)
                        logger.info(f"💳 Автопродление подписки пользователя {user.telegram_id} успешно")
                    else:
                        failed_count += 1
                        if self.bot:
                            await self._send_autopay_failed_notification(user, user.balance_kopeks, renewal_cost)
                        logger.warning(f"💳 Ошибка списания средств для автопродления пользователя {user.telegram_id}")
                else:
                    failed_count += 1
                    if self.bot:
                        await self._send_autopay_failed_notification(user, user.balance_kopeks, renewal_cost)
                    logger.warning(f"💳 Недостаточно средств для автопродления у пользователя {user.telegram_id}")
            
            if processed_count > 0 or failed_count > 0:
                await self._log_monitoring_event(
                    db, "autopayments_processed",
                    f"Автоплатежи: успешно {processed_count}, неудачно {failed_count}",
                    {"processed": processed_count, "failed": failed_count}
                )
                
        except Exception as e:
            logger.error(f"Ошибка обработки автоплатежей: {e}")
    
    async def _send_subscription_expired_notification(self, user: User) -> bool:
        try:
            message = """
⛔ <b>Подписка истекла</b>

Ваша подписка истекла. Для восстановления доступа продлите подписку.

🔧 Доступ к серверам заблокирован до продления.
"""
            
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💎 Купить подписку", callback_data="menu_buy")],
                [InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="balance_topup")]
            ])

            await self._send_message_with_logo(
                chat_id=user.telegram_id,
                text=message,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            return True

        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            if self._handle_unreachable_user(user, exc, "уведомление об истечении подписки"):
                return True
            logger.error(
                "Ошибка Telegram API при отправке уведомления об истечении подписки пользователю %s: %s",
                user.telegram_id,
                exc,
            )
            return False
        except Exception as e:
            logger.error(
                "Ошибка отправки уведомления об истечении подписки пользователю %s: %s",
                user.telegram_id,
                e,
            )
            return False
    
    async def _send_subscription_expiring_notification(self, user: User, subscription: Subscription, days: int) -> bool:
        try:
            from app.utils.formatters import format_days_declension
            
            texts = get_texts(user.language)
            days_text = format_days_declension(days, user.language)
            
            if subscription.autopay_enabled:
                autopay_status = "✅ Включен - подписка продлится автоматически"
                action_text = f"💰 Убедитесь, что на балансе достаточно средств: {texts.format_price(user.balance_kopeks)}"
            else:
                autopay_status = "❌ Отключен - не забудьте продлить вручную!"
                action_text = "💡 Включите автоплатеж или продлите подписку вручную"
            
            message = f"""
⚠️ <b>Подписка истекает через {days_text}!</b>

Ваша платная подписка истекает {subscription.end_date.strftime("%d.%m.%Y %H:%M")}.

💳 <b>Автоплатеж:</b> {autopay_status}

{action_text}
"""
            
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⏰ Продлить подписку", callback_data="subscription_extend")],
                [InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="balance_topup")],
                [InlineKeyboardButton(text="📱 Моя подписка", callback_data="menu_subscription")]
            ])

            await self._send_message_with_logo(
                chat_id=user.telegram_id,
                text=message,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            return True

        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            if self._handle_unreachable_user(user, exc, "уведомление об истекающей подписке"):
                return True
            logger.error(
                "Ошибка Telegram API при отправке уведомления об истечении подписки пользователю %s: %s",
                user.telegram_id,
                exc,
            )
            return False
        except Exception as e:
            logger.error(
                "Ошибка отправки уведомления об истечении подписки пользователю %s: %s",
                user.telegram_id,
                e,
            )
            return False
    
    async def _send_trial_ending_notification(self, user: User, subscription: Subscription) -> bool:
        try:
            texts = get_texts(user.language)

            message = f"""
🎁 <b>Тестовая подписка скоро закончится!</b>

Ваша тестовая подписка истекает через 2 часа.

💎 <b>Не хотите остаться без VPN?</b>
Переходите на полную подписку!

🔥 <b>Специальное предложение:</b>
• 30 дней всего за {settings.format_price(settings.PRICE_30_DAYS)}
• Безлимитный трафик
• Все серверы доступны
• Скорость до 1ГБит/сек

⚡️ Успейте оформить до окончания тестового периода!
"""
            
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💎 Купить подписку", callback_data="menu_buy")],
                [InlineKeyboardButton(text="💰 Пополнить баланс", callback_data="balance_topup")]
            ])

            await self._send_message_with_logo(
                chat_id=user.telegram_id,
                text=message,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            return True

        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            if self._handle_unreachable_user(user, exc, "уведомление о завершении тестовой подписки"):
                return True
            logger.error(
                "Ошибка Telegram API при отправке уведомления о завершении тестовой подписки пользователю %s: %s",
                user.telegram_id,
                exc,
            )
            return False
        except Exception as e:
            logger.error(
                "Ошибка отправки уведомления об окончании тестовой подписки пользователю %s: %s",
                user.telegram_id,
                e,
            )
            return False

    async def _send_trial_inactive_notification(self, user: User, subscription: Subscription, hours: int) -> bool:
        try:
            texts = get_texts(user.language)
            if hours >= 24:
                template = texts.get(
                    "TRIAL_INACTIVE_24H",
                    (
                        "⏳ <b>Вы ещё не подключились к VPN</b>\n\n"
                        "Прошли сутки с активации тестового периода, но трафик не зафиксирован."
                        "\n\nНажмите кнопку ниже, чтобы подключиться."
                    ),
                )
            else:
                template = texts.get(
                    "TRIAL_INACTIVE_1H",
                    (
                        "⏳ <b>Прошёл час, а подключения нет</b>\n\n"
                        "Если возникли сложности с запуском — воспользуйтесь инструкциями."
                    ),
                )

            message = template.format(
                price=settings.format_price(settings.PRICE_30_DAYS),
                end_date=subscription.end_date.strftime("%d.%m.%Y %H:%M"),
            )

            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"), callback_data="subscription_connect")],
                [InlineKeyboardButton(text=texts.t("MY_SUBSCRIPTION_BUTTON", "📱 Моя подписка"), callback_data="menu_subscription")],
                [InlineKeyboardButton(text=texts.t("SUPPORT_BUTTON", "🆘 Поддержка"), callback_data="menu_support")],
            ])

            await self._send_message_with_logo(
                chat_id=user.telegram_id,
                text=message,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            return True

        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            if self._handle_unreachable_user(user, exc, "уведомление о бездействии на тесте"):
                return True
            logger.error(
                "Ошибка Telegram API при отправке уведомления об отсутствии подключения пользователю %s: %s",
                user.telegram_id,
                exc,
            )
            return False
        except Exception as e:
            logger.error(
                "Ошибка отправки уведомления об отсутствии подключения пользователю %s: %s",
                user.telegram_id,
                e,
            )
            return False

    async def _send_expired_day1_notification(self, user: User, subscription: Subscription) -> bool:
        try:
            texts = get_texts(user.language)
            template = texts.get(
                "SUBSCRIPTION_EXPIRED_1D",
                (
                    "⛔ <b>Подписка закончилась</b>\n\n"
                    "Доступ был отключён {end_date}. Продлите подписку, чтобы вернуться в сервис."
                ),
            )
            message = template.format(
                end_date=subscription.end_date.strftime("%d.%m.%Y %H:%M"),
                price=settings.format_price(settings.PRICE_30_DAYS),
            )

            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=texts.t("SUBSCRIPTION_EXTEND", "💎 Продлить подписку"), callback_data="subscription_extend")],
                [InlineKeyboardButton(text=texts.t("BALANCE_TOPUP", "💳 Пополнить баланс"), callback_data="balance_topup")],
                [InlineKeyboardButton(text=texts.t("SUPPORT_BUTTON", "🆘 Поддержка"), callback_data="menu_support")],
            ])

            await self._send_message_with_logo(
                chat_id=user.telegram_id,
                text=message,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            return True

        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            if self._handle_unreachable_user(user, exc, "напоминание об истекшей подписке"):
                return True
            logger.error(
                "Ошибка Telegram API при отправке напоминания об истекшей подписке пользователю %s: %s",
                user.telegram_id,
                exc,
            )
            return False
        except Exception as e:
            logger.error(
                "Ошибка отправки напоминания об истекшей подписке пользователю %s: %s",
                user.telegram_id,
                e,
            )
            return False

    async def _send_expired_discount_notification(
        self,
        user: User,
        subscription: Subscription,
        percent: int,
        expires_at: datetime,
        offer_id: int,
        wave: str,
        bonus_amount: int,
        trigger_days: int = None,
    ) -> bool:
        try:
            texts = get_texts(user.language)

            if wave == "second":
                template = texts.get(
                    "SUBSCRIPTION_EXPIRED_SECOND_WAVE",
                    (
                        "🔥 <b>Скидка {percent}% на продление</b>\n\n"
                        "Нажмите «Получить скидку», и мы начислим {bonus} на баланс. "
                        "Предложение действует до {expires_at}."
                    ),
                )
            else:
                template = texts.get(
                    "SUBSCRIPTION_EXPIRED_THIRD_WAVE",
                    (
                        "🎁 <b>Индивидуальная скидка {percent}%</b>\n\n"
                        "Прошло {trigger_days} дней без подписки — возвращайтесь, и мы добавим {bonus} на баланс. "
                        "Скидка действует до {expires_at}."
                    ),
                )

            message = template.format(
                percent=percent,
                bonus=settings.format_price(bonus_amount),
                expires_at=expires_at.strftime("%d.%m.%Y %H:%M"),
                trigger_days=trigger_days or "",
            )

            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎁 Получить скидку", callback_data=f"claim_discount_{offer_id}")],
                [InlineKeyboardButton(text=texts.t("SUBSCRIPTION_EXTEND", "💎 Продлить подписку"), callback_data="subscription_extend")],
                [InlineKeyboardButton(text=texts.t("BALANCE_TOPUP", "💳 Пополнить баланс"), callback_data="balance_topup")],
                [InlineKeyboardButton(text=texts.t("SUPPORT_BUTTON", "🆘 Поддержка"), callback_data="menu_support")],
            ])

            await self._send_message_with_logo(
                chat_id=user.telegram_id,
                text=message,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            return True

        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            if self._handle_unreachable_user(user, exc, "скидочное уведомление"):
                return True
            logger.error(
                "Ошибка Telegram API при отправке скидочного уведомления пользователю %s: %s",
                user.telegram_id,
                exc,
            )
            return False
        except Exception as e:
            logger.error(
                "Ошибка отправки скидочного уведомления пользователю %s: %s",
                user.telegram_id,
                e,
            )
            return False

    async def _send_autopay_success_notification(self, user: User, amount: int, days: int):
        try:
            texts = get_texts(user.language)
            message = texts.AUTOPAY_SUCCESS.format(
                days=days,
                amount=settings.format_price(amount)
            )
            await self._send_message_with_logo(
                chat_id=user.telegram_id,
                text=message,
                parse_mode="HTML",
            )
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            if not self._handle_unreachable_user(user, exc, "уведомление об успешном автоплатеже"):
                logger.error(
                    "Ошибка Telegram API при отправке уведомления об автоплатеже пользователю %s: %s",
                    user.telegram_id,
                    exc,
                )
        except Exception as e:
            logger.error(
                "Ошибка отправки уведомления об автоплатеже пользователю %s: %s",
                user.telegram_id,
                e,
            )

    async def _send_autopay_failed_notification(self, user: User, balance: int, required: int):
        try:
            texts = get_texts(user.language)
            message = texts.AUTOPAY_FAILED.format(
                balance=settings.format_price(balance),
                required=settings.format_price(required)
            )
            
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="balance_topup")],
                [InlineKeyboardButton(text="📱 Моя подписка", callback_data="menu_subscription")]
            ])
            
            await self._send_message_with_logo(
                chat_id=user.telegram_id,
                text=message,
                parse_mode="HTML",
                reply_markup=keyboard,
            )

        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            if not self._handle_unreachable_user(user, exc, "уведомление о неудачном автоплатеже"):
                logger.error(
                    "Ошибка Telegram API при отправке уведомления о неудачном автоплатеже пользователю %s: %s",
                    user.telegram_id,
                    exc,
                )
        except Exception as e:
            logger.error(
                "Ошибка отправки уведомления о неудачном автоплатеже пользователю %s: %s",
                user.telegram_id,
                e,
            )
    
    async def _cleanup_inactive_users(self, db: AsyncSession):
        try:
            now = datetime.utcnow()
            if now.hour != 3: 
                return
            
            inactive_users = await get_inactive_users(db, settings.INACTIVE_USER_DELETE_MONTHS)
            deleted_count = 0
            
            for user in inactive_users:
                if not user.subscription or not user.subscription.is_active:
                    success = await delete_user(db, user)
                    if success:
                        deleted_count += 1
            
            if deleted_count > 0:
                await self._log_monitoring_event(
                    db, "inactive_users_cleanup",
                    f"Удалено {deleted_count} неактивных пользователей",
                    {"deleted_count": deleted_count}
                )
                logger.info(f"🗑️ Удалено {deleted_count} неактивных пользователей")
                
        except Exception as e:
            logger.error(f"Ошибка очистки неактивных пользователей: {e}")
    
    async def _sync_with_remnawave(self, db: AsyncSession):
        try:
            now = datetime.utcnow()
            if now.minute != 0:
                return
            
            async with self.subscription_service.api as api:
                system_stats = await api.get_system_stats()
                
                await self._log_monitoring_event(
                    db, "remnawave_sync",
                    "Синхронизация с RemnaWave завершена",
                    {"stats": system_stats}
                )
                
        except Exception as e:
            logger.error(f"Ошибка синхронизации с RemnaWave: {e}")
            await self._log_monitoring_event(
                db, "remnawave_sync_error",
                f"Ошибка синхронизации с RemnaWave: {str(e)}",
                {"error": str(e)},
                is_success=False
            )
    
    async def _check_ticket_sla(self, db: AsyncSession):
        try:
            # Quick guards
            # Allow runtime toggle from SupportSettingsService
            try:
                from app.services.support_settings_service import SupportSettingsService
                sla_enabled_runtime = SupportSettingsService.get_sla_enabled()
            except Exception:
                sla_enabled_runtime = getattr(settings, 'SUPPORT_TICKET_SLA_ENABLED', True)
            if not sla_enabled_runtime:
                return
            if not self.bot:
                return
            if not settings.is_admin_notifications_enabled():
                return

            from datetime import datetime, timedelta
            try:
                from app.services.support_settings_service import SupportSettingsService
                sla_minutes = max(1, int(SupportSettingsService.get_sla_minutes()))
            except Exception:
                sla_minutes = max(1, int(getattr(settings, 'SUPPORT_TICKET_SLA_MINUTES', 5)))
            cooldown_minutes = max(1, int(getattr(settings, 'SUPPORT_TICKET_SLA_REMINDER_COOLDOWN_MINUTES', 15)))
            now = datetime.utcnow()
            stale_before = now - timedelta(minutes=sla_minutes)
            cooldown_before = now - timedelta(minutes=cooldown_minutes)

            # Tickets to remind: open, no admin reply yet after user's last message (status OPEN), stale by SLA,
            # and either never reminded or cooldown passed
            result = await db.execute(
                select(Ticket)
                .options(selectinload(Ticket.user))
                .where(
                    and_(
                        Ticket.status == TicketStatus.OPEN.value,
                        Ticket.updated_at <= stale_before,
                        or_(Ticket.last_sla_reminder_at.is_(None), Ticket.last_sla_reminder_at <= cooldown_before),
                    )
                )
            )
            tickets = result.scalars().all()
            if not tickets:
                return

            from app.services.admin_notification_service import AdminNotificationService

            reminders_sent = 0
            service = AdminNotificationService(self.bot)

            for ticket in tickets:
                try:
                    waited_minutes = max(0, int((now - ticket.updated_at).total_seconds() // 60))
                    title = (ticket.title or '').strip()
                    if len(title) > 60:
                        title = title[:57] + '...'

                    text = (
                        f"⏰ <b>Ожидание ответа на тикет превышено</b>\n\n"
                        f"🆔 <b>ID:</b> <code>{ticket.id}</code>\n"
                        f"👤 <b>User ID:</b> <code>{ticket.user_id}</code>\n"
                        f"📝 <b>Заголовок:</b> {title or '—'}\n"
                        f"⏱️ <b>Ожидает ответа:</b> {waited_minutes} мин\n"
                    )

                    sent = await service.send_ticket_event_notification(text)
                    if sent:
                        ticket.last_sla_reminder_at = now
                        reminders_sent += 1
                        # commit after each to persist timestamp and avoid duplicate reminders on crash
                        await db.commit()
                except Exception as notify_error:
                    logger.error(f"Ошибка отправки SLA-уведомления по тикету {ticket.id}: {notify_error}")

            if reminders_sent > 0:
                await self._log_monitoring_event(
                    db,
                    "ticket_sla_reminders_sent",
                    f"Отправлено {reminders_sent} SLA-напоминаний по тикетам",
                    {"count": reminders_sent},
                )
        except Exception as e:
            logger.error(f"Ошибка проверки SLA тикетов: {e}")

    async def _sla_loop(self):
        try:
            interval_seconds = max(10, int(getattr(settings, 'SUPPORT_TICKET_SLA_CHECK_INTERVAL_SECONDS', 60)))
        except Exception:
            interval_seconds = 60
        while self.is_running:
            try:
                async for db in get_db():
                    try:
                        await self._check_ticket_sla(db)
                    finally:
                        break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Ошибка в SLA-цикле: {e}")
            await asyncio.sleep(interval_seconds)

    async def _log_monitoring_event(
        self,
        db: AsyncSession,
        event_type: str,
        message: str,
        data: Dict[str, Any] = None,
        is_success: bool = True
    ):
        try:
            log_entry = MonitoringLog(
                event_type=event_type,
                message=message,
                data=data or {},
                is_success=is_success
            )
            
            db.add(log_entry)
            await db.commit()
            
        except Exception as e:
            logger.error(f"Ошибка логирования события мониторинга: {e}")

    async def get_monitoring_status(self, db: AsyncSession) -> Dict[str, Any]:
        try:
            from sqlalchemy import select, desc
            
            recent_events_result = await db.execute(
                select(MonitoringLog)
                .order_by(desc(MonitoringLog.created_at))
                .limit(10)
            )
            recent_events = recent_events_result.scalars().all()
            
            yesterday = datetime.utcnow() - timedelta(days=1)
            
            events_24h_result = await db.execute(
                select(MonitoringLog)
                .where(MonitoringLog.created_at >= yesterday)
            )
            events_24h = events_24h_result.scalars().all()
            
            successful_events = sum(1 for event in events_24h if event.is_success)
            failed_events = sum(1 for event in events_24h if not event.is_success)
            
            return {
                "is_running": self.is_running,
                "last_update": datetime.utcnow(),
                "recent_events": [
                    {
                        "type": event.event_type,
                        "message": event.message,
                        "success": event.is_success,
                        "created_at": event.created_at
                    }
                    for event in recent_events
                ],
                "stats_24h": {
                    "total_events": len(events_24h),
                    "successful": successful_events,
                    "failed": failed_events,
                    "success_rate": round(successful_events / len(events_24h) * 100, 1) if events_24h else 0
                }
            }
            
        except Exception as e:
            logger.error(f"Ошибка получения статуса мониторинга: {e}")
            return {
                "is_running": self.is_running,
                "last_update": datetime.utcnow(),
                "recent_events": [],
                "stats_24h": {
                    "total_events": 0,
                    "successful": 0,
                    "failed": 0,
                    "success_rate": 0
                }
            }
    
    async def force_check_subscriptions(self, db: AsyncSession) -> Dict[str, int]:
        try:
            expired_subscriptions = await get_expired_subscriptions(db)
            expired_count = 0
            
            for subscription in expired_subscriptions:
                await deactivate_subscription(db, subscription)
                expired_count += 1
            
            expiring_subscriptions = await get_expiring_subscriptions(db, 1)
            expiring_count = len(expiring_subscriptions)
            
            autopay_subscriptions = await get_subscriptions_for_autopay(db)
            autopay_processed = 0
            
            for subscription in autopay_subscriptions:
                user = await get_user_by_id(db, subscription.user_id)
                if user and user.balance_kopeks >= settings.PRICE_30_DAYS:
                    autopay_processed += 1
            
            await self._log_monitoring_event(
                db, "manual_check_subscriptions",
                f"Принудительная проверка: истекло {expired_count}, истекает {expiring_count}, автоплатежей {autopay_processed}",
                {
                    "expired": expired_count,
                    "expiring": expiring_count,
                    "autopay_ready": autopay_processed
                }
            )
            
            return {
                "expired": expired_count,
                "expiring": expiring_count,
                "autopay_ready": autopay_processed
            }
            
        except Exception as e:
            logger.error(f"Ошибка принудительной проверки подписок: {e}")
            return {"expired": 0, "expiring": 0, "autopay_ready": 0}
    
    async def get_monitoring_logs(
        self,
        db: AsyncSession,
        limit: int = 50,
        event_type: Optional[str] = None,
        page: int = 1,
        per_page: int = 20
    ) -> List[Dict[str, Any]]:
        try:
            from sqlalchemy import select, desc
            
            query = select(MonitoringLog).order_by(desc(MonitoringLog.created_at))
            
            if event_type:
                query = query.where(MonitoringLog.event_type == event_type)
            
            if page > 1 or per_page != 20:
                offset = (page - 1) * per_page
                query = query.offset(offset).limit(per_page)
            else:
                query = query.limit(limit)
            
            result = await db.execute(query)
            logs = result.scalars().all()
            
            return [
                {
                    "id": log.id,
                    "event_type": log.event_type,
                    "message": log.message,
                    "data": log.data,
                    "is_success": log.is_success,
                    "created_at": log.created_at
                }
                for log in logs
            ]
            
        except Exception as e:
            logger.error(f"Ошибка получения логов мониторинга: {e}")
            return []

    async def get_monitoring_logs_count(
        self, 
        db: AsyncSession,
        event_type: Optional[str] = None
    ) -> int:
        try:
            from sqlalchemy import select, func
            
            query = select(func.count(MonitoringLog.id))
            
            if event_type:
                query = query.where(MonitoringLog.event_type == event_type)
            
            result = await db.execute(query)
            count = result.scalar()
            
            return count or 0
            
        except Exception as e:
            logger.error(f"Ошибка получения количества логов: {e}")
            return 0
    
    async def cleanup_old_logs(self, db: AsyncSession, days: int = 30) -> int:
        try:
            from sqlalchemy import delete, select
            
            if days == 0:
                result = await db.execute(delete(MonitoringLog))
            else:
                cutoff_date = datetime.utcnow() - timedelta(days=days)
                result = await db.execute(
                    delete(MonitoringLog).where(MonitoringLog.created_at < cutoff_date)
                )
            
            deleted_count = result.rowcount
            await db.commit()
            
            if days == 0:
                logger.info(f"🗑️ Удалены все логи мониторинга ({deleted_count} записей)")
            else:
                logger.info(f"🗑️ Удалено {deleted_count} старых записей логов (старше {days} дней)")
                
            return deleted_count
            
        except Exception as e:
            logger.error(f"Ошибка очистки логов: {e}")
            await db.rollback()
            return 0


monitoring_service = MonitoringService()
