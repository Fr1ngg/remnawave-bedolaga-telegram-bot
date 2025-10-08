from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class AuthRegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    email: Optional[EmailStr] = None
    name: Optional[str] = None


class AuthLoginRequest(BaseModel):
    username: str
    password: str


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: Optional[datetime] = None


class AdminProfileResponse(BaseModel):
    id: int
    username: str
    email: Optional[EmailStr] = None
    name: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class AdminProfileUpdateRequest(BaseModel):
    email: Optional[EmailStr] = None
    name: Optional[str] = None
    password: Optional[str] = Field(default=None, min_length=6, max_length=128)


class RegistrationStatusResponse(BaseModel):
    can_register: bool


