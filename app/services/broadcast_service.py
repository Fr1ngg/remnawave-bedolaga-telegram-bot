from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup

from app.database.database import AsyncSessionLocal
from app.database.models import BroadcastHistory
from app.handlers.admin.messages import (
    create_broadcast_keyboard,
    get_custom_users,
    get_target_users,
)


logger = logging.getLogger(__name__)


VALID_MEDIA_TYPES = {"photo", "video", "document"}


@dataclass(slots=True)
class BroadcastMediaConfig:
    type: str
    file_id: str
    caption: Optional[str] = None


@dataclass(slots=True)
class BroadcastConfig:
    target: str
    message_text: str
    selected_buttons: list[str]
    media: Optional[BroadcastMediaConfig] = None
    initiator_name: Optional[str] = None


@dataclass(slots=True)
class _BroadcastTask:
    task: asyncio.Task
    cancel_event: asyncio.Event


class BroadcastService:
    """Handles broadcast execution triggered from the admin web API."""

    def __init__(self) -> None:
        self._bot: Optional[Bot] = None
        self._tasks: dict[int, _BroadcastTask] = {}
        self._lock = asyncio.Lock()

    def set_bot(self, bot: Bot) -> None:
        self._bot = bot

    def is_running(self, broadcast_id: int) -> bool:
        task_entry = self._tasks.get(broadcast_id)
        return bool(task_entry and not task_entry.task.done())

    async def start_broadcast(self, broadcast_id: int, config: BroadcastConfig) -> None:
        if self._bot is None:
            logger.error("Невозможно запустить рассылку %s: бот не инициализирован", broadcast_id)
            await self._mark_failed(broadcast_id)
            return

        cancel_event = asyncio.Event()

        async with self._lock:
            if broadcast_id in self._tasks and not self._tasks[broadcast_id].task.done():
                logger.warning("Рассылка %s уже запущена", broadcast_id)
                return

            task = asyncio.create_task(
                self._run_broadcast(broadcast_id, config, cancel_event),
                name=f"broadcast-{broadcast_id}",
            )
            self._tasks[broadcast_id] = _BroadcastTask(task=task, cancel_event=cancel_event)
            task.add_done_callback(lambda _: self._tasks.pop(broadcast_id, None))

    async def request_stop(self, broadcast_id: int) -> bool:
        async with self._lock:
            task_entry = self._tasks.get(broadcast_id)
            if not task_entry:
                return False

            task_entry.cancel_event.set()
            return True

    async def _run_broadcast(
        self,
        broadcast_id: int,
        config: BroadcastConfig,
        cancel_event: asyncio.Event,
    ) -> None:
        sent_count = 0
        failed_count = 0

        try:
            if cancel_event.is_set():
                await self._mark_cancelled(broadcast_id, sent_count, failed_count)
                return

            async with AsyncSessionLocal() as session:
                broadcast = await session.get(BroadcastHistory, broadcast_id)
                if not broadcast:
                    logger.error("Запись рассылки %s не найдена в БД", broadcast_id)
                    return

                broadcast.status = "in_progress"
                broadcast.sent_count = 0
                broadcast.failed_count = 0
                await session.commit()

            recipients = await self._fetch_recipients(config.target)

            async with AsyncSessionLocal() as session:
                broadcast = await session.get(BroadcastHistory, broadcast_id)
                if not broadcast:
                    logger.error("Запись рассылки %s удалена до запуска", broadcast_id)
                    return

                broadcast.total_count = len(recipients)
                await session.commit()

            if cancel_event.is_set():
                await self._mark_cancelled(broadcast_id, sent_count, failed_count)
                return

            if not recipients:
                logger.info("Рассылка %s: получатели не найдены", broadcast_id)
                await self._mark_finished(broadcast_id, sent_count, failed_count, cancelled=False)
                return

            keyboard = self._build_keyboard(config.selected_buttons)

            for index, user in enumerate(recipients, start=1):
                if cancel_event.is_set():
                    await self._mark_cancelled(broadcast_id, sent_count, failed_count)
                    return

                telegram_id = getattr(user, "telegram_id", None)
                if telegram_id is None:
                    failed_count += 1
                    continue

                try:
                    await self._deliver_message(telegram_id, config, keyboard)
                    sent_count += 1
                except Exception as exc:  # noqa: BLE001
                    failed_count += 1
                    logger.error(
                        "Ошибка отправки рассылки %s пользователю %s: %s",
                        broadcast_id,
                        telegram_id,
                        exc,
                    )

                if index % 20 == 0:
                    await asyncio.sleep(1)

            await self._mark_finished(broadcast_id, sent_count, failed_count, cancelled=False)

        except asyncio.CancelledError:
            await self._mark_cancelled(broadcast_id, sent_count, failed_count)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Критическая ошибка при выполнении рассылки %s: %s", broadcast_id, exc)
            await self._mark_failed(broadcast_id, sent_count, failed_count)

    async def _fetch_recipients(self, target: str):
        async with AsyncSessionLocal() as session:
            if target.startswith("custom_"):
                criteria = target[len("custom_"):]
                return await get_custom_users(session, criteria)
            return await get_target_users(session, target)

    def _build_keyboard(self, selected_buttons: Optional[list[str]]) -> Optional[InlineKeyboardMarkup]:
        if selected_buttons is None:
            selected_buttons = []
        return create_broadcast_keyboard(selected_buttons)

    async def _deliver_message(
        self,
        telegram_id: int,
        config: BroadcastConfig,
        keyboard: Optional[InlineKeyboardMarkup],
    ) -> None:
        if not self._bot:
            raise RuntimeError("Телеграм-бот не инициализирован")

        if config.media and config.media.type in VALID_MEDIA_TYPES:
            caption = config.media.caption or config.message_text
            if config.media.type == "photo":
                await self._bot.send_photo(
                    chat_id=telegram_id,
                    photo=config.media.file_id,
                    caption=caption,
                    reply_markup=keyboard,
                )
            elif config.media.type == "video":
                await self._bot.send_video(
                    chat_id=telegram_id,
                    video=config.media.file_id,
                    caption=caption,
                    reply_markup=keyboard,
                )
            elif config.media.type == "document":
                await self._bot.send_document(
                    chat_id=telegram_id,
                    document=config.media.file_id,
                    caption=caption,
                    reply_markup=keyboard,
                )
            return

        await self._bot.send_message(
            chat_id=telegram_id,
            text=config.message_text,
            reply_markup=keyboard,
        )

    async def _mark_finished(
        self,
        broadcast_id: int,
        sent_count: int,
        failed_count: int,
        *,
        cancelled: bool,
    ) -> None:
        async with AsyncSessionLocal() as session:
            broadcast = await session.get(BroadcastHistory, broadcast_id)
            if not broadcast:
                return

            broadcast.sent_count = sent_count
            broadcast.failed_count = failed_count
            broadcast.status = "cancelled" if cancelled else (
                "completed" if failed_count == 0 else "partial"
            )
            broadcast.completed_at = datetime.utcnow()
            await session.commit()

    async def _mark_cancelled(
        self,
        broadcast_id: int,
        sent_count: int,
        failed_count: int,
    ) -> None:
        await self._mark_finished(
            broadcast_id,
            sent_count,
            failed_count,
            cancelled=True,
        )

    async def _mark_failed(
        self,
        broadcast_id: int,
        sent_count: int = 0,
        failed_count: int = 0,
    ) -> None:
        async with AsyncSessionLocal() as session:
            broadcast = await session.get(BroadcastHistory, broadcast_id)
            if not broadcast:
                return

            broadcast.sent_count = sent_count
            broadcast.failed_count = failed_count or broadcast.failed_count
            broadcast.status = "failed"
            broadcast.completed_at = datetime.utcnow()
            await session.commit()


broadcast_service = BroadcastService()

