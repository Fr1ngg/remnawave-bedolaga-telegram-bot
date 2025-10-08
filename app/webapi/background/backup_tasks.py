from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from app.services.backup_service import backup_service


@dataclass(slots=True)
class BackupTaskState:
    task_id: str
    status: str = "queued"
    message: Optional[str] = None
    file_path: Optional[str] = None
    created_by: Optional[int] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


class BackupTaskManager:
    def __init__(self) -> None:
        self._tasks: dict[str, BackupTaskState] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, *, created_by: Optional[int]) -> BackupTaskState:
        task_id = uuid.uuid4().hex
        state = BackupTaskState(task_id=task_id, created_by=created_by)

        async with self._lock:
            self._tasks[task_id] = state

        asyncio.create_task(self._run_task(state))
        return state

    async def _run_task(self, state: BackupTaskState) -> None:
        state.status = "running"
        state.updated_at = datetime.utcnow()

        try:
            success, message, file_path = await backup_service.create_backup(
                created_by=state.created_by
            )
            state.message = message
            state.file_path = file_path
            state.status = "completed" if success else "failed"
        except Exception as exc:  # noqa: BLE001
            state.status = "failed"
            state.message = f"Unexpected error: {exc}"
        finally:
            state.updated_at = datetime.utcnow()

    async def get(self, task_id: str) -> Optional[BackupTaskState]:
        async with self._lock:
            return self._tasks.get(task_id)

    async def list(self, *, active_only: bool = False) -> list[BackupTaskState]:
        async with self._lock:
            states = list(self._tasks.values())

        if active_only:
            return [
                state
                for state in states
                if state.status in {"queued", "running"}
            ]

        return states


backup_task_manager = BackupTaskManager()
