from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html

from app.services.system_settings_service import bot_configuration_service


router = APIRouter()


def _docs_enabled() -> bool:
    try:
        definition = bot_configuration_service.get_definition("WEB_API_DOCS_ENABLED")
        current = bot_configuration_service.get_current_value(definition.key)
        return bool(current)
    except Exception:
        return False


@router.get("/openapi.json")
async def openapi_spec(request: Request) -> JSONResponse:
    if not _docs_enabled():
        raise HTTPException(status_code=404, detail="Docs disabled")
    app = request.app
    return JSONResponse(app.openapi())


@router.get("/docs")
async def swagger_ui(request: Request) -> HTMLResponse:
    if not _docs_enabled():
        raise HTTPException(status_code=404, detail="Docs disabled")
    return get_swagger_ui_html(openapi_url="/openapi.json", title="API Docs")


@router.get("/redoc")
async def redoc_ui(request: Request) -> HTMLResponse:
    if not _docs_enabled():
        raise HTTPException(status_code=404, detail="Docs disabled")
    return get_redoc_html(openapi_url="/openapi.json", title="API ReDoc")


