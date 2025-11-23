from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class MediaUploadResponse(BaseModel):
    file_id: str
    media_type: str
    media_url: Optional[str] = None
    file_name: Optional[str] = None
    file_size: Optional[int] = None
