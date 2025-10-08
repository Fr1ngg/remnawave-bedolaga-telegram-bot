from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class BackupCreateResponse(BaseModel):
    task_id: str
    status: str = Field(..., description="Текущий статус задачи")


class BackupInfo(BaseModel):
    filename: str
    filepath: str
    timestamp: Optional[datetime] = None
    tables_count: Optional[int] = None
    total_records: Optional[int] = None
    compressed: bool
    file_size_bytes: int
    file_size_mb: float
    created_by: Optional[int] = None
    database_type: Optional[str] = None
    version: Optional[str] = None
    error: Optional[str] = None


class BackupListResponse(BaseModel):
    items: list[BackupInfo]
    total: int
    limit: int
    offset: int


class BackupStatusResponse(BaseModel):
    task_id: str
    status: str
    message: Optional[str] = None
    file_path: Optional[str] = Field(
        default=None,
        description="Полный путь до созданного бекапа, если задача завершена",
    )
    created_by: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class BackupTaskInfo(BackupStatusResponse):
    pass


class BackupTaskListResponse(BaseModel):
    items: list[BackupTaskInfo]
    total: int
