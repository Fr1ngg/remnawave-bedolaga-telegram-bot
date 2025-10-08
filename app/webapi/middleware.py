from __future__ import annotations

import logging
from time import monotonic
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


logger = logging.getLogger("web_api")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Логирование входящих запросов в административный API."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = monotonic()
        response: Response | None = None
        try:
            response = await call_next(request)
            return response
        finally:
            duration_ms = (monotonic() - start) * 1000
            status = response.status_code if response else "error"
            logger.info(
                "%s %s -> %s (%.2f ms)",
                request.method,
                request.url.path,
                status,
                duration_ms,
            )
