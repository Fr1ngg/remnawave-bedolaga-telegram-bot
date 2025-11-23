from __future__ import annotations

import logging
from typing import Optional

from aiogram import Bot

logger = logging.getLogger(__name__)


async def build_media_url(bot: Bot, file_id: str) -> Optional[str]:
    """Resolve Telegram file_id to direct download URL.

    Returns None if file_path cannot be fetched.
    """

    try:
        file = await bot.get_file(file_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to fetch Telegram file info: %s", exc)
        return None

    if not file or not file.file_path:
        return None

    return f"https://api.telegram.org/file/bot{bot.token}/{file.file_path}"
