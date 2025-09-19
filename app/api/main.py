import logging
import time

from dishka.integrations.fastapi import setup_dishka
from requests import Request
from starlette.middleware.cors import CORSMiddleware

from app.api.di import setup_di_fastapi
from app.api.domain.schemas.exception.base import AppException, ErrorResponse
from app.api.versions.v1.routers import subscriptions, users
from app.config import settings

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

def setup_exception_handlers(api: FastAPI):
    @api.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(detail=exc.detail, code=exc.code).dict(),
        )

def create_app() -> FastAPI:
    di = setup_di_fastapi()
    api = FastAPI(
        docs_url=settings.DOCS_BOT_API,
        title="API Bedolaga project",
        debug=settings.DEBUG_BOT_API
    )
    api.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    setup_dishka(app=api, container=di)
    api.include_router(users.router, prefix="/api/v1/users", tags=["users"])
    api.include_router(subscriptions.router, prefix="/api/v1/subscriptions", tags=["subscriptions"])
    setup_exception_handlers(api)
    return api

app = create_app()

@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()

    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    logger.debug("Process time: %s", process_time)
    return response
