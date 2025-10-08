from __future__ import annotations

from fastapi import APIRouter, Security

from app.config import settings
from app.services.version_service import version_service

from ..dependencies import require_api_token
from ..schemas.health import HealthCheckResponse, HealthFeatureFlags

router = APIRouter()


@router.get("/health", tags=["health"], response_model=HealthCheckResponse)
async def health_check(_: object = Security(require_api_token)) -> HealthCheckResponse:
    return HealthCheckResponse(
        status="ok",
        api_version=settings.WEB_API_VERSION,
        bot_version=version_service.current_version,
        features=HealthFeatureFlags(
            monitoring=settings.MONITORING_INTERVAL > 0,
            maintenance=True,
            reporting=True,
            webhooks=bool(settings.WEBHOOK_URL),
        ),
    )
