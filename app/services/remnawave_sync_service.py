import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, time
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.database import AsyncSessionLocal
from app.database.crud.server_squad import sync_with_remnawave
from app.services.remnawave_service import (
    RemnaWaveConfigurationError,
    RemnaWaveService,
)
from app.utils.cache import cache


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RemnaWaveAutoSyncStatus:
    enabled: bool
    times: List[time]
    next_run: Optional[datetime]
    last_run_started_at: Optional[datetime]
    last_run_finished_at: Optional[datetime]
    last_run_success: Optional[bool]
    last_run_reason: Optional[str]
    last_run_error: Optional[str]
    last_user_stats: Optional[Dict[str, Any]]
    last_server_stats: Optional[Dict[str, Any]]
    is_running: bool


class RemnaWaveAutoSyncService:
    def __init__(
        self,
        service_factory: Callable[[], RemnaWaveService] = RemnaWaveService,
    ) -> None:
        self._scheduler_task: Optional[asyncio.Task] = None
        self._scheduler_lock = asyncio.Lock()
        self._sync_lock = asyncio.Lock()
        self._service_factory = service_factory
        self._service = self._service_factory()

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._initialized = False
        self._pending_refresh = False
        self._pending_run_immediately = False

        self._next_run: Optional[datetime] = None
        self._last_run_started_at: Optional[datetime] = None
        self._last_run_finished_at: Optional[datetime] = None
        self._last_run_success: Optional[bool] = None
        self._last_run_reason: Optional[str] = None
        self._last_run_error: Optional[str] = None
        self._last_user_stats: Optional[Dict[str, Any]] = None
        self._last_server_stats: Optional[Dict[str, Any]] = None

    async def initialize(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._initialized = True

        run_immediately = self._pending_run_immediately
        if self._pending_refresh:
            self._pending_refresh = False
            self._pending_run_immediately = False
            await self.refresh_schedule(run_immediately=run_immediately)
        else:
            await self.refresh_schedule()

    def refresh_configuration(self) -> None:
        """Rebuild the RemnaWave service instance using current settings."""
        self._refresh_service()

    async def refresh_schedule(self, *, run_immediately: bool = False) -> None:
        async with self._scheduler_lock:
            if self._scheduler_task and not self._scheduler_task.done():
                self._scheduler_task.cancel()
                try:
                    await self._scheduler_task
                except asyncio.CancelledError:
                    pass
                finally:
                    self._scheduler_task = None

            if not settings.REMNAWAVE_AUTO_SYNC_ENABLED:
                self._next_run = None
                return

            times = settings.get_remnawave_auto_sync_times()
            if not times:
                logger.warning(
                    "⚠️ Автосинхронизация включена, но расписание пустое. Укажите время запуска."
                )
                self._next_run = None
                return

            self._scheduler_task = asyncio.create_task(self._run_scheduler(times))

        if run_immediately:
            asyncio.create_task(self.run_sync_now(reason="immediate"))

    def schedule_refresh(self, *, run_immediately: bool = False) -> None:
        if not self._initialized:
            self._pending_refresh = True
            if run_immediately:
                self._pending_run_immediately = True
            return

        loop = self._loop or asyncio.get_running_loop()
        loop.create_task(self.refresh_schedule(run_immediately=run_immediately))

    async def stop(self) -> None:
        async with self._scheduler_lock:
            if self._scheduler_task and not self._scheduler_task.done():
                self._scheduler_task.cancel()
                try:
                    await self._scheduler_task
                except asyncio.CancelledError:
                    pass
            self._scheduler_task = None
            self._next_run = None

    async def run_sync_now(self, *, reason: str = "manual") -> Dict[str, Any]:
        if self._sync_lock.locked():
            return {"started": False, "reason": "already_running"}

        async with self._sync_lock:
            self._last_run_started_at = datetime.utcnow()
            self._last_run_finished_at = None
            self._last_run_reason = reason
            self._last_run_error = None
            self._last_run_success = None

            try:
                user_stats, server_stats = await self._perform_sync()
            except RemnaWaveConfigurationError as error:
                message = str(error)
                self._last_run_error = message
                self._last_run_success = False
                self._last_user_stats = None
                self._last_server_stats = None
                self._last_run_finished_at = datetime.utcnow()
                logger.error("❌ Автосинхронизация RemnaWave: %s", message)
                return {
                    "started": True,
                    "success": False,
                    "error": message,
                    "user_stats": None,
                    "server_stats": None,
                }
            except Exception as error:
                message = str(error)
                self._last_run_error = message
                self._last_run_success = False
                self._last_user_stats = None
                self._last_server_stats = None
                self._last_run_finished_at = datetime.utcnow()
                logger.exception("❌ Ошибка автосинхронизации RemnaWave: %s", error)
                return {
                    "started": True,
                    "success": False,
                    "error": message,
                    "user_stats": None,
                    "server_stats": None,
                }

            self._last_run_success = True
            self._last_run_error = None
            self._last_user_stats = user_stats
            self._last_server_stats = server_stats
            self._last_run_finished_at = datetime.utcnow()

            return {
                "started": True,
                "success": True,
                "error": None,
                "user_stats": user_stats,
                "server_stats": server_stats,
            }

    def get_status(self) -> RemnaWaveAutoSyncStatus:
        times = settings.get_remnawave_auto_sync_times()
        enabled = settings.REMNAWAVE_AUTO_SYNC_ENABLED and bool(times)

        return RemnaWaveAutoSyncStatus(
            enabled=enabled,
            times=times,
            next_run=self._next_run,
            last_run_started_at=self._last_run_started_at,
            last_run_finished_at=self._last_run_finished_at,
            last_run_success=self._last_run_success,
            last_run_reason=self._last_run_reason,
            last_run_error=self._last_run_error,
            last_user_stats=self._last_user_stats,
            last_server_stats=self._last_server_stats,
            is_running=self._sync_lock.locked(),
        )

    async def _run_scheduler(self, times: List[time]) -> None:
        try:
            while True:
                next_run = self._calculate_next_run(times)
                self._next_run = next_run

                delay = (next_run - datetime.utcnow()).total_seconds()
                if delay > 0:
                    await asyncio.sleep(delay)

                await self.run_sync_now(reason="auto")
        except asyncio.CancelledError:
            raise
        finally:
            self._next_run = None

    def _refresh_service(self) -> RemnaWaveService:
        self._service = self._service_factory()
        return self._service

    async def _perform_sync(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        service = self._refresh_service()

        if not service.is_configured:
            raise RemnaWaveConfigurationError(
                service.configuration_error or "RemnaWave API не настроен"
            )

        async with AsyncSessionLocal() as session:
            user_stats = await service.sync_users_from_panel(session, "all")
            server_stats = await self._sync_servers(session, service)

        return user_stats, server_stats

    async def _sync_servers(
        self,
        session: AsyncSession,
        service: RemnaWaveService,
    ) -> Dict[str, Any]:
        squads = await service.get_all_squads()

        if not squads:
            logger.warning("⚠️ Не удалось получить сквады из RemnaWave для автосинхронизации")
            return {"created": 0, "updated": 0, "removed": 0, "total": 0}

        created, updated, removed = await sync_with_remnawave(session, squads)

        try:
            await cache.delete_pattern("available_countries*")
        except Exception as error:
            logger.warning("⚠️ Не удалось очистить кеш стран после автосинхронизации: %s", error)

        return {
            "created": created,
            "updated": updated,
            "removed": removed,
            "total": len(squads),
        }

    @staticmethod
    def _calculate_next_run(times: List[time]) -> datetime:
        now = datetime.utcnow()
        today = now.date()

        for scheduled in sorted(times):
            candidate = datetime.combine(today, scheduled)
            if candidate > now:
                return candidate

        first_time = sorted(times)[0]
        next_day = today + timedelta(days=1)
        return datetime.combine(next_day, first_time)


def _create_service() -> RemnaWaveAutoSyncService:
    service = RemnaWaveAutoSyncService()
    return service


remnawave_sync_service = _create_service()
