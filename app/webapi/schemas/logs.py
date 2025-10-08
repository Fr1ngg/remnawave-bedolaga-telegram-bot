from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class LogEntry(BaseModel):
    """Единица лога, распарсенная из строки файла журналов."""

    time: datetime
    logger: str = Field(default="")
    level: str = Field(default="")
    message: str = Field(default="")
    raw: str = Field(default="")


class LogListResponse(BaseModel):
    items: List[LogEntry] = Field(default_factory=list)
    total: int = 0


