from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class RemnaWaveConnectionStatus(BaseModel):
    status: str
    message: str
    api_url: Optional[str] = None
    status_code: Optional[int] = None
    system_info: Optional[Dict[str, Any]] = None


class RemnaWaveStatusResponse(BaseModel):
    is_configured: bool
    configuration_error: Optional[str] = None
    connection: Optional[RemnaWaveConnectionStatus] = None


class RemnaWaveNode(BaseModel):
    uuid: str
    name: str
    address: str
    country_code: Optional[str] = None
    port: Optional[int] = None
    is_connected: bool
    is_disabled: bool
    is_node_online: bool
    is_xray_running: bool
    users_online: Optional[int] = None
    traffic_used_bytes: Optional[int] = None
    traffic_limit_bytes: Optional[int] = None
    # extra (optional) fields from panel
    xray_version: Optional[str] = None
    node_version: Optional[str] = None
    xray_uptime_seconds: Optional[int] = None
    cpu_count: Optional[int] = None
    cpu_model: Optional[str] = None
    total_ram_bytes: Optional[int] = None


class RemnaWaveNodeListResponse(BaseModel):
    items: List[RemnaWaveNode]
    total: int


class RemnaWaveNodeActionRequest(BaseModel):
    action: Literal["enable", "disable", "restart"]


class RemnaWaveNodeActionResponse(BaseModel):
    success: bool
    detail: Optional[str] = None


class RemnaWaveNodeStatisticsResponse(BaseModel):
    node: RemnaWaveNode
    realtime: Optional[Dict[str, Any]] = None
    usage_history: List[Dict[str, Any]] = Field(default_factory=list)
    last_updated: Optional[datetime] = None


class RemnaWaveNodeUsageResponse(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)


class RemnaWaveBandwidth(BaseModel):
    realtime_download: int
    realtime_upload: int
    realtime_total: int


class RemnaWaveTrafficPeriod(BaseModel):
    current: int
    previous: int
    difference: Optional[str] = None


class RemnaWaveTrafficPeriods(BaseModel):
    last_2_days: RemnaWaveTrafficPeriod
    last_7_days: RemnaWaveTrafficPeriod
    last_30_days: RemnaWaveTrafficPeriod
    current_month: RemnaWaveTrafficPeriod
    current_year: RemnaWaveTrafficPeriod


class RemnaWaveSystemSummary(BaseModel):
    users_online: int
    total_users: int
    active_connections: int
    nodes_online: int
    users_last_day: int
    users_last_week: int
    users_never_online: int
    total_user_traffic: int


class RemnaWaveServerInfo(BaseModel):
    cpu_cores: int
    cpu_physical_cores: int
    memory_total: int
    memory_used: int
    memory_free: int
    memory_available: int
    uptime_seconds: int


class RemnaWaveSystemStatsResponse(BaseModel):
    system: RemnaWaveSystemSummary
    users_by_status: Dict[str, int]
    server_info: RemnaWaveServerInfo
    bandwidth: RemnaWaveBandwidth
    traffic_periods: RemnaWaveTrafficPeriods
    nodes_realtime: List[Dict[str, Any]] = Field(default_factory=list)
    nodes_weekly: List[Dict[str, Any]] = Field(default_factory=list)
    last_updated: Optional[datetime] = None


class RemnaWaveSquad(BaseModel):
    uuid: str
    name: str
    members_count: int
    inbounds_count: int
    inbounds: List[Dict[str, Any]] = Field(default_factory=list)


class RemnaWaveSquadListResponse(BaseModel):
    items: List[RemnaWaveSquad]
    total: int


class RemnaWaveSquadCreateRequest(BaseModel):
    name: str
    inbound_uuids: List[str] = Field(default_factory=list)


class RemnaWaveSquadUpdateRequest(BaseModel):
    name: Optional[str] = None
    inbound_uuids: Optional[List[str]] = None


class RemnaWaveSquadActionRequest(BaseModel):
    action: Literal["add_all_users", "remove_all_users", "delete", "rename", "update_inbounds"]
    name: Optional[str] = None
    inbound_uuids: Optional[List[str]] = None


class RemnaWaveOperationResponse(BaseModel):
    success: bool
    detail: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


class RemnaWaveInboundsResponse(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)


class RemnaWaveUserTrafficResponse(BaseModel):
    telegram_id: int
    used_traffic_bytes: int
    used_traffic_gb: float
    lifetime_used_traffic_bytes: int
    lifetime_used_traffic_gb: float
    traffic_limit_bytes: int
    traffic_limit_gb: float
    subscription_url: Optional[str] = None


class RemnaWaveSyncFromPanelRequest(BaseModel):
    mode: Literal["all", "new_only", "update_only"] = "all"


class RemnaWaveGenericSyncResponse(BaseModel):
    success: bool
    detail: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
