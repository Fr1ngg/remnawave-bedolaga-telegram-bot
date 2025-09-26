import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from enum import Enum
from typing import Optional, Tuple

from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy import func, select

from app.config import settings
from app.database.crud.subscription import get_subscriptions_statistics
from app.database.database import AsyncSessionLocal
from app.database.models import (
    Subscription,
    SubscriptionConversion,
    Ticket,
    TicketStatus,
    Transaction,
    TransactionType,
)


logger = logging.getLogger(__name__)


class ReportingServiceError(RuntimeError):
    """Base error for the reporting service."""


class ReportPeriod(Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


@dataclass(slots=True)
class ReportPeriodRange:
    start_msk: datetime
    end_msk: datetime
    label: str


class ReportingService:
    """Generates admin summary reports and can schedule daily delivery."""

    def __init__(self) -> None:
        self.bot: Optional[Bot] = None
        self._task: Optional[asyncio.Task] = None
        self._moscow_tz = ZoneInfo("Europe/Moscow")

    def set_bot(self, bot: Bot) -> None:
        self.bot = bot

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        await self.stop()

        if not settings.ADMIN_REPORTS_ENABLED:
            logger.info("Сервис отчетов отключен настройками")
            return

        if not self.bot:
            logger.warning("Невозможно запустить сервис отчетов без экземпляра бота")
            return

        chat_id = settings.get_reports_chat_id()
        if not chat_id:
            logger.warning("Сервис отчетов не запущен: не указан чат для отправки отчетов")
            return

        send_time = settings.get_reports_send_time()
        if not send_time:
            logger.warning("Сервис отчетов не запущен: не указано время ежедневной отправки")
            return

        self._task = asyncio.create_task(self._auto_daily_loop(send_time))
        logger.info(
            "📊 Сервис отчетов запущен: ежедневная отправка в %s по МСК",
            send_time.strftime("%H:%M"),
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def send_report(
        self,
        period: ReportPeriod,
        *,
        report_date: Optional[date] = None,
        send_to_topic: bool = False,
    ) -> str:
        report_text = await self._build_report(period, report_date)

        if send_to_topic:
            await self._deliver_report(report_text)

        return report_text

    async def _auto_daily_loop(self, send_time: datetime_time) -> None:
        try:
            next_run_utc, report_date = self._calculate_next_run(send_time)

            while True:
                now_utc = datetime.now(timezone.utc)
                delay = (next_run_utc - now_utc).total_seconds()

                if delay > 0:
                    await asyncio.sleep(delay)

                try:
                    await self.send_report(
                        ReportPeriod.DAILY,
                        report_date=report_date,
                        send_to_topic=True,
                    )
                    logger.info(
                        "📊 Автоматический отчет за %s отправлен",
                        report_date.strftime("%d.%m.%Y"),
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.error("Ошибка автоматической отправки отчета: %s", exc)

                next_run_utc, report_date = self._calculate_next_run(send_time)

        except asyncio.CancelledError:
            logger.info("Сервис отчетов остановлен")
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("Критическая ошибка в сервисе отчетов: %s", exc)

    def _calculate_next_run(
        self,
        send_time: datetime_time,
    ) -> Tuple[datetime, date]:
        now_msk = datetime.now(self._moscow_tz)
        candidate = datetime.combine(now_msk.date(), send_time, tzinfo=self._moscow_tz)

        if now_msk >= candidate:
            candidate += timedelta(days=1)

        report_date = (candidate - timedelta(days=1)).date()
        return candidate.astimezone(timezone.utc), report_date

    async def _deliver_report(self, report_text: str) -> None:
        if not self.bot:
            raise ReportingServiceError("Бот не инициализирован для отправки отчета")

        chat_id = settings.get_reports_chat_id()
        if not chat_id:
            raise ReportingServiceError("Не задан чат для отправки отчета")

        topic_id = settings.get_reports_topic_id()

        try:
            await self.bot.send_message(
                chat_id=chat_id,
                text=report_text,
                message_thread_id=topic_id,
            )
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            logger.error("Не удалось отправить отчет: %s", exc)
            raise ReportingServiceError("Не удалось отправить отчет в чат") from exc

    async def _build_report(
        self,
        period: ReportPeriod,
        report_date: Optional[date],
    ) -> str:
        period_range = self._get_period_range(period, report_date)
        start_utc = period_range.start_msk.astimezone(timezone.utc).replace(tzinfo=None)
        end_utc = period_range.end_msk.astimezone(timezone.utc).replace(tzinfo=None)

        async with AsyncSessionLocal() as session:
            totals = await self._collect_current_totals(session)
            period_stats = await self._collect_period_stats(session, start_utc, end_utc)

        header = (
            f"📊 <b>Отчет за {period_range.label}</b>"
            if period == ReportPeriod.DAILY
            else f"📊 <b>Отчет за период {period_range.label}</b>"
        )

        lines = [
            header,
            "",
            "🎯 <b>Триалы</b>",
            f"• Активных сейчас: {totals['active_trials']}",
            f"• Новых за период: {period_stats['new_trials']}",
            "",
            "💎 <b>Платные подписки</b>",
            f"• Активных сейчас: {totals['active_paid']}",
            f"• Новых за период: {period_stats['new_paid_subscriptions']}",
            "",
            "🎟️ <b>Тикеты поддержки</b>",
            f"• Активных сейчас: {totals['open_tickets']}",
            f"• Новых за период: {period_stats['new_tickets']}",
            "",
            "💰 <b>Платежи</b>",
            f"• Оплат подписок: {period_stats['subscription_payments_count']} на сумму "
            f"{self._format_amount(period_stats['subscription_payments_amount'])}",
            f"• Пополнений: {period_stats['deposits_count']} на сумму "
            f"{self._format_amount(period_stats['deposits_amount'])}",
            f"• Всего поступлений: {period_stats['total_payments_count']} на сумму "
            f"{self._format_amount(period_stats['total_payments_amount'])}",
        ]

        return "\n".join(lines)

    def _get_period_range(
        self,
        period: ReportPeriod,
        report_date: Optional[date],
    ) -> ReportPeriodRange:
        now_msk = datetime.now(self._moscow_tz)

        if period == ReportPeriod.DAILY:
            target_date = report_date or (now_msk.date() - timedelta(days=1))
            start = datetime.combine(target_date, datetime_time.min, tzinfo=self._moscow_tz)
            end = start + timedelta(days=1)
        elif period == ReportPeriod.WEEKLY:
            end_date = report_date or now_msk.date()
            start_date = end_date - timedelta(days=7)
            start = datetime.combine(start_date, datetime_time.min, tzinfo=self._moscow_tz)
            end = datetime.combine(end_date, datetime_time.min, tzinfo=self._moscow_tz)
        elif period == ReportPeriod.MONTHLY:
            end_date = report_date or now_msk.date()
            start_date = end_date - timedelta(days=30)
            start = datetime.combine(start_date, datetime_time.min, tzinfo=self._moscow_tz)
            end = datetime.combine(end_date, datetime_time.min, tzinfo=self._moscow_tz)
        else:  # pragma: no cover - defensive branch
            raise ReportingServiceError(f"Неизвестный период отчета: {period}")

        label = self._format_period_label(start, end)
        return ReportPeriodRange(start, end, label)

    async def _collect_current_totals(self, session) -> dict:
        stats = await get_subscriptions_statistics(session)
        open_tickets_result = await session.execute(
            select(func.count(Ticket.id)).where(
                Ticket.status.in_(
                    [
                        TicketStatus.OPEN.value,
                        TicketStatus.ANSWERED.value,
                        TicketStatus.PENDING.value,
                    ]
                )
            )
        )
        open_tickets = int(open_tickets_result.scalar() or 0)
        return {
            "active_trials": stats.get("trial_subscriptions", 0) or 0,
            "active_paid": stats.get("paid_subscriptions", 0) or 0,
            "open_tickets": open_tickets,
        }

    async def _collect_period_stats(
        self,
        session,
        start_utc: datetime,
        end_utc: datetime,
    ) -> dict:
        new_trials_result = await session.execute(
            select(func.count(Subscription.id)).where(
                Subscription.created_at >= start_utc,
                Subscription.created_at < end_utc,
                Subscription.is_trial == True,  # noqa: E712
            )
        )
        new_trials = int(new_trials_result.scalar() or 0)

        direct_paid_result = await session.execute(
            select(func.count(Subscription.id)).where(
                Subscription.created_at >= start_utc,
                Subscription.created_at < end_utc,
                Subscription.is_trial == False,  # noqa: E712
            )
        )
        direct_paid = int(direct_paid_result.scalar() or 0)

        conversions_result = await session.execute(
            select(func.count(SubscriptionConversion.id)).where(
                SubscriptionConversion.converted_at >= start_utc,
                SubscriptionConversion.converted_at < end_utc,
            )
        )
        conversions_count = int(conversions_result.scalar() or 0)

        subscription_payments_row = (
            await session.execute(
                select(
                    func.count(Transaction.id),
                    func.coalesce(func.sum(Transaction.amount_kopeks), 0),
                ).where(
                    Transaction.type == TransactionType.SUBSCRIPTION_PAYMENT.value,
                    Transaction.is_completed == True,  # noqa: E712
                    Transaction.created_at >= start_utc,
                    Transaction.created_at < end_utc,
                )
            )
        ).one()

        deposits_row = (
            await session.execute(
                select(
                    func.count(Transaction.id),
                    func.coalesce(func.sum(Transaction.amount_kopeks), 0),
                ).where(
                    Transaction.type == TransactionType.DEPOSIT.value,
                    Transaction.is_completed == True,  # noqa: E712
                    Transaction.created_at >= start_utc,
                    Transaction.created_at < end_utc,
                )
            )
        ).one()

        subscription_payments_count = int(subscription_payments_row[0] or 0)
        subscription_payments_amount = int(subscription_payments_row[1] or 0)
        deposits_count = int(deposits_row[0] or 0)
        deposits_amount = int(deposits_row[1] or 0)

        total_payments_count = subscription_payments_count + deposits_count
        total_payments_amount = subscription_payments_amount + deposits_amount
        new_tickets_result = await session.execute(
            select(func.count(Ticket.id)).where(
                Ticket.created_at >= start_utc,
                Ticket.created_at < end_utc,
            )
        )
        new_tickets = int(new_tickets_result.scalar() or 0)

        return {
            "new_trials": new_trials,
            "new_paid_subscriptions": direct_paid + conversions_count,
            "subscription_payments_count": subscription_payments_count,
            "subscription_payments_amount": subscription_payments_amount,
            "deposits_count": deposits_count,
            "deposits_amount": deposits_amount,
            "total_payments_count": total_payments_count,
            "total_payments_amount": total_payments_amount,
            "new_tickets": new_tickets,
        }

    def _format_period_label(self, start: datetime, end: datetime) -> str:
        start_date = start.astimezone(self._moscow_tz).date()
        end_boundary = (end - timedelta(seconds=1)).astimezone(self._moscow_tz)
        end_date = end_boundary.date()

        if start_date == end_date:
            return start_date.strftime("%d.%m.%Y")

        return (
            f"{start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}"
        )

    def _format_amount(self, amount_kopeks: int) -> str:
        if not amount_kopeks:
            return "0 ₽"

        rubles = amount_kopeks / 100
        return f"{rubles:,.2f} ₽".replace(",", " ")


reporting_service = ReportingService()

