from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class TicketMediaUploadResponse(BaseModel):
    upload_token: str
    filename: str
    content_type: Optional[str] = None
    size: int
    media_type: str


class TicketMessageResponse(BaseModel):
    id: int
    user_id: int
    message_text: str
    is_from_admin: bool
    has_media: bool
    media_type: Optional[str] = None
    media_caption: Optional[str] = None
    media_file_id: Optional[str] = None
    media_url: Optional[str] = None
    created_at: datetime


class TicketResponse(BaseModel):
    id: int
    user_id: int
    title: str
    status: str
    priority: str
    created_at: datetime
    updated_at: datetime
    closed_at: Optional[datetime] = None
    user_reply_block_permanent: bool
    user_reply_block_until: Optional[datetime] = None
    messages: List[TicketMessageResponse] = Field(default_factory=list)


class TicketStatusUpdateRequest(BaseModel):
    status: str


class TicketPriorityUpdateRequest(BaseModel):
    priority: str


class TicketReplyBlockRequest(BaseModel):
    permanent: bool = False
    until: Optional[datetime] = None


class TicketReplyRequest(BaseModel):
    message_text: Optional[str] = None
    media_type: Optional[str] = None
    media_file_id: Optional[str] = None
    media_caption: Optional[str] = None
    upload_token: Optional[str] = None