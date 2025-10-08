from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class TransactionResponse(BaseModel):
    id: int
    user_id: int
    type: str
    amount_kopeks: int
    amount_rubles: float
    description: Optional[str] = None
    payment_method: Optional[str] = None
    external_id: Optional[str] = None
    is_completed: bool
    created_at: datetime
    completed_at: Optional[datetime] = None


class TransactionListResponse(BaseModel):
    items: list[TransactionResponse]
    total: int
    limit: int
    offset: int
