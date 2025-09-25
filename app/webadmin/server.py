from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.config import settings
from app.services.system_settings_service import bot_configuration_service

from .router import router


logger = logging.getLogger(__name__)

INDEX_PATH = Path(__file__).resolve().parents[2] / "webadmin" / "index.html"
try:
    INDEX_HTML = INDEX_PATH.read_text(encoding="utf-8")
except FileNotFoundError:  # pragma: no cover - defensive
    INDEX_HTML = "<h1>Web admin UI template not found</h1>"
    logger.error("Не найден шаблон webadmin/index.html")


def create_webadmin_app() -> FastAPI:
    app = FastAPI(
        title="Bedolaga Web Admin",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    allowed_origins = settings.get_webadmin_allowed_origins()
    if not allowed_origins:
        # По умолчанию разрешаем только хост, на котором запущена админка
        allowed_origins = [
            f"http://{settings.WEBADMIN_HOST}:{settings.WEBADMIN_PORT}",
            f"https://{settings.WEBADMIN_HOST}:{settings.WEBADMIN_PORT}",
        ]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins if allowed_origins else ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    async def startup_event() -> None:  # pragma: no cover - FastAPI lifecycle
        try:
            await bot_configuration_service.initialize()
        except Exception as error:
            logger.error("Не удалось инициализировать конфигурацию бота: %s", error)

    app.include_router(router)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(INDEX_HTML)

    @app.get("/{path:path}", response_class=HTMLResponse)
    async def spa(path: str) -> HTMLResponse:  # noqa: ARG001 - путь не используется
        return HTMLResponse(INDEX_HTML)

    return app


class WebAdminServer:
    def __init__(self) -> None:
        self.app = create_webadmin_app()
        self._config = uvicorn.Config(
            self.app,
            host=settings.WEBADMIN_HOST,
            port=settings.WEBADMIN_PORT,
            loop="asyncio",
            lifespan="on",
            access_log=False,
        )
        self._server = uvicorn.Server(self._config)
        self._server.install_signal_handlers = False
        self._task: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return

        self._task = asyncio.create_task(self._server.serve())

        started_task = asyncio.create_task(self._server.started.wait())
        done, pending = await asyncio.wait(
            {self._task, started_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()

        for task in done:
            if task is not self._task:
                try:
                    await task
                except asyncio.CancelledError:  # pragma: no cover - cleanup
                    pass

        if self._task.done():
            exception = self._task.exception()
            if exception:
                raise exception

    async def stop(self) -> None:
        if self._task is None:
            return

        self._server.should_exit = True
        await self._task
        self._task = None

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def get_exception(self) -> Optional[BaseException]:
        if self._task and self._task.done():
            return self._task.exception()
        return None
