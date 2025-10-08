from __future__ import annotations

from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Security, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.webapi.dependencies import get_db_session, require_api_token
from app.services.admin_notification_service import AdminNotificationService


router = APIRouter()

# --- Simple in-memory SSE broker ---
class _SseBroker:
    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[str]] = []

    async def publish(self, data: str) -> None:
        # Also include a heartbeat for clients in case of long idle; clients ignore unknown prefixes
        for q in list(self._subscribers):
            try:
                await q.put(data)
            except Exception:
                pass

    async def subscribe(self) -> AsyncIterator[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
        self._subscribers.append(q)
        async def heartbeat():
            while True:
                await asyncio.sleep(20)
                try:
                    await q.put(":heartbeat")  # comment line in SSE ignored by most clients
                except Exception:
                    break
        hb = asyncio.create_task(heartbeat())
        try:
            while True:
                payload = await q.get()
                yield payload
        finally:
            hb.cancel()
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

import asyncio
broker = _SseBroker()


@router.post("/test", status_code=status.HTTP_202_ACCEPTED)
async def send_test_notification(
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    try:
        from app.bot import bot as running_bot
    except Exception:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Bot is not ready")

    if running_bot is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Bot is not ready")

    service = AdminNotificationService(running_bot)
    ok = await service._send_message("🧪 Тестовое уведомление из веб‑админки")
    if not ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Отправка не настроена (чат/права)")
    return {"detail": "Отправлено"}
@router.get("/events")
async def events_stream(_: Any = Security(require_api_token)):
    async def event_generator():
        async for message in broker.subscribe():
            if message.startswith(":"):
                # SSE comment/heartbeat
                yield f"{message}\n\n"
            else:
                yield f"data: {message}\n\n"

    headers = {"Content-Type": "text/event-stream", "Cache-Control": "no-cache", "Connection": "keep-alive"}
    return StreamingResponse(event_generator(), headers=headers)


