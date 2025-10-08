from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Security, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.server_squad import get_server_squad_by_uuid
from app.database.crud.system_metric import create_server_metric, get_latest_metric, list_server_metrics
from app.database.models import ServerHealthMetric, ServerSquad

from ..dependencies import get_db_session, require_api_token
from ..schemas.system_metrics import (
    LinkedServerSquad,
    SystemMetricIngestRequest,
    SystemMetricListResponse,
    SystemMetricResponse,
)

router = APIRouter(prefix="/system-metrics", tags=["monitoring"])


def _serialize_server_squad(squad: Optional[ServerSquad]) -> Optional[LinkedServerSquad]:
    if not squad:
        return None
    return LinkedServerSquad(
        id=squad.id,
        squad_uuid=squad.squad_uuid,
        display_name=squad.display_name,
    )


def _serialize_metric(metric: ServerHealthMetric) -> SystemMetricResponse:
    return SystemMetricResponse(
        id=metric.id,
        agent_id=metric.agent_id,
        server_name=metric.server_name,
        server_squad_id=metric.server_squad_id,
        cpu_percent=metric.cpu_percent,
        memory_percent=metric.memory_percent,
        memory_used_mb=metric.memory_used_mb,
        memory_total_mb=metric.memory_total_mb,
        swap_percent=metric.swap_percent,
        load_avg_1m=metric.load_avg_1m,
        load_avg_5m=metric.load_avg_5m,
        load_avg_15m=metric.load_avg_15m,
        uptime_seconds=metric.uptime_seconds,
        process_count=metric.process_count,
        recorded_at=metric.recorded_at,
        extra=metric.extra or {},
        created_at=metric.created_at,
        server_squad=_serialize_server_squad(getattr(metric, "server_squad", None)),
    )


@router.post("", response_model=SystemMetricResponse, status_code=status.HTTP_201_CREATED)
async def ingest_metric(
    payload: SystemMetricIngestRequest,
    request: Request,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> SystemMetricResponse:
    server_squad: Optional[ServerSquad] = None
    if payload.server_squad_uuid:
        server_squad = await get_server_squad_by_uuid(db, payload.server_squad_uuid)
        if not server_squad:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Server squad not found")

    extra = dict(payload.extra or {})
    if request.client:
        extra.setdefault("reported_ip", request.client.host)

    metric = await create_server_metric(
        db,
        agent_id=payload.agent_id,
        server_name=payload.server_name,
        server_squad_id=server_squad.id if server_squad else None,
        cpu_percent=payload.cpu_percent,
        memory_percent=payload.memory_percent,
        memory_used_mb=payload.memory_used_mb,
        memory_total_mb=payload.memory_total_mb,
        swap_percent=payload.swap_percent,
        load_avg_1m=payload.load_avg_1m,
        load_avg_5m=payload.load_avg_5m,
        load_avg_15m=payload.load_avg_15m,
        uptime_seconds=payload.uptime_seconds,
        process_count=payload.process_count,
        recorded_at=payload.recorded_at,
        extra=extra,
    )

    if server_squad:
        metric.server_squad = server_squad

    return _serialize_metric(metric)


@router.get("", response_model=SystemMetricListResponse)
async def list_metrics(
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
    agent_id: Optional[str] = Query(None, description="Filter by agent identifier"),
    server_squad_uuid: Optional[str] = Query(None, description="Filter by server squad UUID"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> SystemMetricListResponse:
    server_squad_id: Optional[int] = None
    if server_squad_uuid:
        server_squad = await get_server_squad_by_uuid(db, server_squad_uuid)
        if not server_squad:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Server squad not found")
        server_squad_id = server_squad.id

    metrics, total = await list_server_metrics(
        db,
        agent_id=agent_id,
        server_squad_id=server_squad_id,
        limit=limit,
        offset=offset,
    )

    return SystemMetricListResponse(
        items=[_serialize_metric(metric) for metric in metrics],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/latest", response_model=SystemMetricResponse)
async def latest_metric(
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
    agent_id: Optional[str] = Query(None, description="Agent identifier"),
    server_squad_uuid: Optional[str] = Query(None, description="Server squad UUID"),
) -> SystemMetricResponse:
    if not agent_id and not server_squad_uuid:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Provide agent_id or server_squad_uuid")

    server_squad_id: Optional[int] = None
    if server_squad_uuid:
        server_squad = await get_server_squad_by_uuid(db, server_squad_uuid)
        if not server_squad:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Server squad not found")
        server_squad_id = server_squad.id

    metric = await get_latest_metric(
        db,
        agent_id=agent_id,
        server_squad_id=server_squad_id,
    )

    if not metric:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No metrics found")

    return _serialize_metric(metric)
