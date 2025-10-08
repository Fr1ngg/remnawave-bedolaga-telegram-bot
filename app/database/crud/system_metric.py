from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import ServerHealthMetric


async def create_server_metric(
    db: AsyncSession,
    *,
    agent_id: str,
    server_name: Optional[str],
    server_squad_id: Optional[int],
    cpu_percent: float,
    memory_percent: Optional[float],
    memory_used_mb: Optional[int],
    memory_total_mb: Optional[int],
    swap_percent: Optional[float],
    load_avg_1m: Optional[float],
    load_avg_5m: Optional[float],
    load_avg_15m: Optional[float],
    uptime_seconds: Optional[int],
    process_count: Optional[int],
    recorded_at: Optional[datetime],
    extra: Optional[dict[str, Any]],
) -> ServerHealthMetric:
    metric = ServerHealthMetric(
        agent_id=agent_id,
        server_name=server_name,
        server_squad_id=server_squad_id,
        cpu_percent=cpu_percent,
        memory_percent=memory_percent,
        memory_used_mb=memory_used_mb,
        memory_total_mb=memory_total_mb,
        swap_percent=swap_percent,
        load_avg_1m=load_avg_1m,
        load_avg_5m=load_avg_5m,
        load_avg_15m=load_avg_15m,
        uptime_seconds=uptime_seconds,
        process_count=process_count,
        recorded_at=recorded_at or datetime.utcnow(),
        extra=extra or {},
    )
    db.add(metric)
    await db.commit()
    await db.refresh(metric)
    return metric


def _apply_filters(
    query: Select,
    *,
    agent_id: Optional[str] = None,
    server_squad_id: Optional[int] = None,
) -> Select:
    if agent_id:
        query = query.where(ServerHealthMetric.agent_id == agent_id)
    if server_squad_id is not None:
        query = query.where(ServerHealthMetric.server_squad_id == server_squad_id)
    return query


async def list_server_metrics(
    db: AsyncSession,
    *,
    agent_id: Optional[str] = None,
    server_squad_id: Optional[int] = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[ServerHealthMetric], int]:
    base_query = select(ServerHealthMetric)
    count_query = select(func.count()).select_from(ServerHealthMetric)

    base_query = _apply_filters(base_query, agent_id=agent_id, server_squad_id=server_squad_id)
    count_query = _apply_filters(count_query, agent_id=agent_id, server_squad_id=server_squad_id)

    base_query = base_query.options(selectinload(ServerHealthMetric.server_squad))
    base_query = base_query.order_by(ServerHealthMetric.created_at.desc())

    result = await db.execute(base_query.limit(limit).offset(offset))
    items = list(result.scalars().unique().all())

    total_result = await db.execute(count_query)
    total = int(total_result.scalar() or 0)

    return items, total


async def get_latest_metric(
    db: AsyncSession,
    *,
    agent_id: Optional[str] = None,
    server_squad_id: Optional[int] = None,
) -> Optional[ServerHealthMetric]:
    query = select(ServerHealthMetric)
    query = _apply_filters(query, agent_id=agent_id, server_squad_id=server_squad_id)
    query = query.options(selectinload(ServerHealthMetric.server_squad))
    query = query.order_by(ServerHealthMetric.created_at.desc()).limit(1)

    result = await db.execute(query)
    return result.scalars().first()
