"""Pydantic models for the exact API contract (section 3 of the assignment)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

MAX_MESSAGE_LENGTH = 4000
MAX_SESSION_ID_LENGTH = 128


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=MAX_SESSION_ID_LENGTH)
    # Length is enforced in the validator below, *after* trimming whitespace.
    message: str = Field(min_length=1)
    system_prompt: str | None = None

    @field_validator("session_id")
    @classmethod
    def _clean_session_id(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("session_id must not be empty")
        if len(v) > MAX_SESSION_ID_LENGTH:
            raise ValueError(
                f"session_id must be at most {MAX_SESSION_ID_LENGTH} characters"
            )
        return v

    @field_validator("message")
    @classmethod
    def _clean_message(cls, v: str) -> str:
        # 1-4000 chars after trimming whitespace.
        v = v.strip()
        if not v:
            raise ValueError("message must not be empty after trimming whitespace")
        if len(v) > MAX_MESSAGE_LENGTH:
            raise ValueError(
                f"message must be at most {MAX_MESSAGE_LENGTH} characters after trimming whitespace"
            )
        return v


class ChatMetadata(BaseModel):
    resolution_source: Literal["LIVE_INFERENCE", "LLM_CACHE"]
    model: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    key_id: str | None
    fallback_attempts: int


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    metadata: ChatMetadata


class MessageOut(BaseModel):
    role: str
    content: str
    metadata: dict[str, Any] | None = None


class HistoryResponse(BaseModel):
    session_id: str
    messages: list[MessageOut]
