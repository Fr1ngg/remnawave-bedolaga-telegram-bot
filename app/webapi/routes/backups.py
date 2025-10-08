from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Security, status
from fastapi.responses import FileResponse

from app.services.backup_service import backup_service

from ..background.backup_tasks import backup_task_manager
from ..dependencies import require_api_token
from ..schemas.backups import (
    BackupCreateResponse,
    BackupInfo,
    BackupListResponse,
    BackupSettingsResponse,
    BackupSettingsUpdateRequest,
    BackupStatusResponse,
    BackupTaskInfo,
    BackupTaskListResponse,
)


router = APIRouter()


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None

    try:
        if value.endswith("Z"):
            value = value.replace("Z", "+00:00")
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _serialize_backup(raw: dict) -> BackupInfo:
    timestamp = _parse_datetime(raw.get("timestamp"))
    tables_count = _to_int(raw.get("tables_count"))
    total_records = _to_int(raw.get("total_records"))
    file_size_bytes = _to_int(raw.get("file_size_bytes")) or 0
    file_size_mb = raw.get("file_size_mb")
    try:
        file_size_mb = float(file_size_mb)
    except (TypeError, ValueError):
        file_size_mb = round(file_size_bytes / 1024 / 1024, 2)

    created_by = _to_int(raw.get("created_by"))

    return BackupInfo(
        filename=str(raw.get("filename")),
        filepath=str(raw.get("filepath")),
        timestamp=timestamp,
        tables_count=tables_count,
        total_records=total_records,
        compressed=bool(raw.get("compressed", False)),
        file_size_bytes=file_size_bytes,
        file_size_mb=float(file_size_mb),
        created_by=created_by,
        database_type=raw.get("database_type"),
        version=raw.get("version"),
        error=raw.get("error"),
    )


@router.post(
    "",
    response_model=BackupCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Запустить создание резервной копии",
)
async def create_backup_endpoint(
    token: Any = Security(require_api_token),
) -> BackupCreateResponse:
    created_by = getattr(token, "id", None)
    state = await backup_task_manager.enqueue(created_by=created_by)
    return BackupCreateResponse(task_id=state.task_id, status=state.status)


@router.post(
    "/restore",
    status_code=status.HTTP_200_OK,
    summary="Восстановить данные из резервной копии",
)
async def restore_backup_endpoint(
    filepath: str,
    clear_existing: bool = Query(False, description="Очистить существующие данные перед восстановлением"),
    _: Any = Security(require_api_token),
) -> dict:
    success, message = await backup_service.restore_backup(filepath, clear_existing=clear_existing)
    if not success:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, message)
    return {"detail": message}


@router.get("/settings", response_model=BackupSettingsResponse)
async def get_backup_settings(_: Any = Security(require_api_token)) -> BackupSettingsResponse:
    s = await backup_service.get_backup_settings()
    return BackupSettingsResponse(
        auto_backup_enabled=s.auto_backup_enabled,
        backup_interval_hours=s.backup_interval_hours,
        backup_time=s.backup_time,
        max_backups_keep=s.max_backups_keep,
        compression_enabled=s.compression_enabled,
        include_logs=s.include_logs,
        backup_location=s.backup_location,
    )


@router.put("/settings", response_model=BackupSettingsResponse)
async def update_backup_settings(payload: BackupSettingsUpdateRequest, _: Any = Security(require_api_token)) -> BackupSettingsResponse:
    ok = await backup_service.update_backup_settings(**{k: v for k, v in payload.model_dump(exclude_none=True).items()})
    if not ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Не удалось обновить настройки")
    return await get_backup_settings()  # type: ignore[return-value]


@router.delete("/{filename}")
async def delete_backup_file(filename: str, _: Any = Security(require_api_token)) -> dict:
    success, message = await backup_service.delete_backup(filename)
    if not success:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, message)
    return {"detail": message}


@router.get("/download/{filename}")
async def download_backup_file(filename: str, _: Any = Security(require_api_token)) -> FileResponse:
    path = backup_service.backup_dir / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Файл бекапа не найден")
    return FileResponse(str(path), media_type="application/octet-stream", filename=filename)


@router.get(
    "",
    response_model=BackupListResponse,
    summary="Список резервных копий",
)
async def list_backups(
    _: Any = Security(require_api_token),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> BackupListResponse:
    backups = await backup_service.get_backup_list()
    total = len(backups)

    slice_backups = backups[offset : offset + limit]
    items = [_serialize_backup(raw) for raw in slice_backups]

    return BackupListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/status/{task_id}",
    response_model=BackupStatusResponse,
    summary="Статус создания резервной копии",
)
async def get_backup_status(
    task_id: str,
    _: Any = Security(require_api_token),
) -> BackupStatusResponse:
    state = await backup_task_manager.get(task_id)
    if not state:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")

    return BackupStatusResponse(
        task_id=state.task_id,
        status=state.status,
        message=state.message,
        file_path=state.file_path,
        created_by=state.created_by,
        created_at=state.created_at,
        updated_at=state.updated_at,
    )


@router.get(
    "/tasks",
    response_model=BackupTaskListResponse,
    summary="Список фоновых задач бекапов",
)
async def list_backup_tasks(
    _: Any = Security(require_api_token),
    active_only: bool = Query(False, description="Вернуть только активные задачи"),
) -> BackupTaskListResponse:
    states = await backup_task_manager.list(active_only=active_only)

    items = [
        BackupTaskInfo(
            task_id=state.task_id,
            status=state.status,
            message=state.message,
            file_path=state.file_path,
            created_by=state.created_by,
            created_at=state.created_at,
            updated_at=state.updated_at,
        )
        for state in states
    ]

    return BackupTaskListResponse(items=items, total=len(items))
