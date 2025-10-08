from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class SystemMetricBase(BaseModel):
    agent_id: str = Field(..., max_length=128, description="Unique identifier of the reporting agent")
    server_name: Optional[str] = Field(None, max_length=255, description="Human-readable server name")
    server_squad_uuid: Optional[str] = Field(None, max_length=255, description="UUID of linked server squad if applicable")
    cpu_percent: float = Field(..., ge=0, le=100, description="CPU utilization percentage")
    memory_percent: Optional[float] = Field(None, ge=0, le=100, description="RAM utilization percentage")
    memory_used_mb: Optional[int] = Field(None, ge=0, description="Used RAM in megabytes")
    memory_total_mb: Optional[int] = Field(None, ge=0, description="Total RAM in megabytes")
    swap_percent: Optional[float] = Field(None, ge=0, le=100, description="Swap utilization percentage")
    load_avg_1m: Optional[float] = Field(None, ge=0, description="Load average for 1 minute")
    load_avg_5m: Optional[float] = Field(None, ge=0, description="Load average for 5 minutes")
    load_avg_15m: Optional[float] = Field(None, ge=0, description="Load average for 15 minutes")
    uptime_seconds: Optional[int] = Field(None, ge=0, description="System uptime in seconds")
    process_count: Optional[int] = Field(None, ge=0, description="Number of running processes")
    recorded_at: Optional[datetime] = Field(None, description="Timestamp captured on the agent")
    extra: Dict[str, Any] = Field(default_factory=dict, description="Additional metrics provided by the agent")


class SystemMetricIngestRequest(SystemMetricBase):
    pass


class LinkedServerSquad(BaseModel):
    id: int
    squad_uuid: str
    display_name: Optional[str] = None


class SystemMetricResponse(BaseModel):
    id: int
    agent_id: str
    server_name: Optional[str]
    server_squad_id: Optional[int]
    cpu_percent: float
    memory_percent: Optional[float]
    memory_used_mb: Optional[int]
    memory_total_mb: Optional[int]
    swap_percent: Optional[float]
    load_avg_1m: Optional[float]
    load_avg_5m: Optional[float]
    load_avg_15m: Optional[float]
    uptime_seconds: Optional[int]
    process_count: Optional[int]
    recorded_at: Optional[datetime]
    extra: Dict[str, Any]
    created_at: datetime
    server_squad: Optional[LinkedServerSquad] = None


class SystemMetricListResponse(BaseModel):
    items: list[SystemMetricResponse]
    total: int
    limit: int
    offset: int
