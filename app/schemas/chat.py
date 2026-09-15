"""HTTP schemas for the CAD chat endpoint."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ChatHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    drawing_key: str | None = Field(default=None, min_length=1, max_length=256)
    revision_id: str | None = Field(default=None, min_length=1, max_length=128)
    history: list[ChatHistoryMessage] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_scope(self) -> "ChatRequest":
        if self.drawing_key and self.revision_id:
            raise ValueError("provide only one of drawing_key or revision_id")
        return self


class ToolTrace(BaseModel):
    call_id: str
    name: str
    arguments: dict[str, Any]
    status: Literal["success", "error"]
    result_count: int | None = None
    error: str | None = None


class EvidenceReference(BaseModel):
    evidence_type: str
    id: str | None = None
    external_id: str | None = None
    status: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    drawing_scope: dict[str, str] | None = None
    tool_calls: list[ToolTrace] = Field(default_factory=list)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
