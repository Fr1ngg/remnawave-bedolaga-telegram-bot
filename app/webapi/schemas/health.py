from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class HealthFeatureFlags(BaseModel):
    """Флаги доступности функций административного API."""

    monitoring: bool
    maintenance: bool
    reporting: bool
    webhooks: bool

    model_config = ConfigDict(extra="forbid")


class HealthCheckResponse(BaseModel):
    """Ответ на health-check административного API."""

    status: str
    api_version: str
    bot_version: str | None
    features: HealthFeatureFlags

    model_config = ConfigDict(extra="forbid")
