"""Request/response models. Field names are fixed by the API contract."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    message: str = Field(...)
    system_prompt: Optional[str] = Field(default=None, max_length=4000)

    @field_validator("session_id")
    @classmethod
    def _session_id_not_blank(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("session_id must not be empty")
        if len(trimmed) > 128:
            raise ValueError("session_id must be 1-128 characters")
        return trimmed

    @field_validator("message")
    @classmethod
    def _message_length_after_trim(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("message must not be empty")
        if len(trimmed) > 4000:
            raise ValueError("message must be 1-4000 characters after trimming")
        return trimmed

    @field_validator("system_prompt")
    @classmethod
    def _normalise_system_prompt(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None


class ChatMetadata(BaseModel):
    resolution_source: Literal["LIVE_INFERENCE", "LLM_CACHE"]
    model: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    key_id: Optional[str] = None
    fallback_attempts: int = 0


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    metadata: ChatMetadata


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    metadata: Optional[Dict[str, Any]] = None


class HistoryResponse(BaseModel):
    session_id: str
    messages: List[HistoryMessage]


class HealthResponse(BaseModel):
    status: str
    keys_configured: int
    cache_enabled: bool
