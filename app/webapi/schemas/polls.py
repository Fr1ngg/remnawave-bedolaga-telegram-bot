from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class PollOptionCreate(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)

    @field_validator("text")
    @classmethod
    def strip_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("Option text cannot be empty")
        return text


class PollQuestionCreate(BaseModel):
    text: str = Field(..., min_length=1, max_length=1000)
    options: list[PollOptionCreate] = Field(..., min_length=2)

    @field_validator("text")
    @classmethod
    def strip_question_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("Question text cannot be empty")
        return text

    @field_validator("options")
    @classmethod
    def validate_options(cls, value: list[PollOptionCreate]) -> list[PollOptionCreate]:
        seen: set[str] = set()
        for option in value:
            normalized = option.text.lower()
            if normalized in seen:
                raise ValueError("Option texts must be unique within a question")
            seen.add(normalized)
        return value


class PollCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=4000)
    reward_enabled: bool = False
    reward_amount_kopeks: int = Field(default=0, ge=0, le=1_000_000_000)
    questions: list[PollQuestionCreate] = Field(..., min_length=1)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        title = value.strip()
        if not title:
            raise ValueError("Title cannot be empty")
        return title

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        description = value.strip()
        return description or None

    @model_validator(mode="after")
    def validate_reward(self) -> "PollCreateRequest":
        if self.reward_enabled and self.reward_amount_kopeks <= 0:
            raise ValueError("Reward amount must be positive when rewards are enabled")
        if not self.reward_enabled:
            self.reward_amount_kopeks = 0
        return self


class PollQuestionOptionResponse(BaseModel):
    id: int
    text: str
    order: int


class PollQuestionResponse(BaseModel):
    id: int
    text: str
    order: int
    options: list[PollQuestionOptionResponse]


class PollSummaryResponse(BaseModel):
    id: int
    title: str
    description: Optional[str]
    reward_enabled: bool
    reward_amount_kopeks: int
    reward_amount_rubles: float
    questions_count: int
    responses_count: int
    created_at: datetime
    updated_at: datetime


class PollDetailResponse(BaseModel):
    id: int
    title: str
    description: Optional[str]
    reward_enabled: bool
    reward_amount_kopeks: int
    reward_amount_rubles: float
    questions: list[PollQuestionResponse]
    created_at: datetime
    updated_at: datetime


class PollListResponse(BaseModel):
    items: list[PollSummaryResponse]
    total: int
    limit: int
    offset: int


class PollOptionStats(BaseModel):
    id: int
    text: str
    count: int


class PollQuestionStats(BaseModel):
    id: int
    text: str
    order: int
    options: list[PollOptionStats]


class PollStatisticsResponse(BaseModel):
    poll_id: int
    poll_title: str
    total_responses: int
    completed_responses: int
    reward_sum_kopeks: int
    reward_sum_rubles: float
    questions: list[PollQuestionStats]


class PollAnswerResponse(BaseModel):
    question_id: Optional[int]
    question_text: Optional[str]
    option_id: Optional[int]
    option_text: Optional[str]
    created_at: datetime


class PollUserResponse(BaseModel):
    id: int
    user_id: Optional[int]
    user_telegram_id: Optional[int]
    user_username: Optional[str]
    sent_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    reward_given: bool
    reward_amount_kopeks: int
    reward_amount_rubles: float
    answers: list[PollAnswerResponse]


class PollResponsesListResponse(BaseModel):
    items: list[PollUserResponse]
    total: int
    limit: int
    offset: int
