"""FastAPI route for DeepSeek-backed CAD chat."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.main import get_repository, service_from
from app.repositories import DrawingRepository
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat_service import CADChatService


router = APIRouter(prefix="/api", tags=["chat"])


def get_chat_service() -> CADChatService:
    # Import lazily so health, Swagger, and exact-query endpoints still work
    # when the optional DeepSeek key has not been configured.
    from app.api.main import get_chat_service_singleton

    return get_chat_service_singleton()


@router.post("/chat", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    repository: DrawingRepository = Depends(get_repository),
    chat_service: CADChatService = Depends(get_chat_service),
) -> ChatResponse:
    return chat_service.chat(service_from(repository), request)
