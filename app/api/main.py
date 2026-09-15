"""FastAPI entry point for the read-only CAD query demo."""

from __future__ import annotations

from pathlib import Path
import os
from typing import Any
from uuid import UUID

import psycopg
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.responses import FileResponse, JSONResponse

from app.db.connection import connect
from app.ingestion.pipeline import (
    UploadValidationError,
    create_upload_job,
    get_upload_job,
    run_upload_pipeline,
)
from app.llm.deepseek_client import DeepSeekClient, DeepSeekConfigurationError, DeepSeekRequestError
from app.services.chat_service import CADChatService, ChatLoopError
from app.repositories import (
    DrawingNotFoundError,
    DrawingRepository,
    IngestionJobNotFoundError,
    RetrievalRunNotFoundError,
)
from app.services.cad_query_service import CADQueryService
from app.semantic import SemanticSearchError, SemanticSearchService
from app.retrieval import HybridRetrievalService, HybridSearchRequest
from app.tools.cad_query_tools import ToolExecutionError, execute_tool


app = FastAPI(
    title="CAD Knowledge Base API",
    version="0.1.0",
    description="Hybrid semantic recall and exact queries over imported CAD, OCR and fusion data.",
)

_cors_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOW_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ToolCallRequest(BaseModel):
    """Request body for a whitelisted CAD tool call."""

    arguments: dict[str, Any] = Field(default_factory=dict)
_chat_service: CADChatService | None = None
_semantic_service: SemanticSearchService | None = None
_hybrid_service: HybridRetrievalService | None = None
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def get_repository() -> Any:
    """Create one short-lived repository for a request.

    This is intentionally simple for the local demo. A production deployment
    should replace it with a psycopg connection pool dependency.
    """

    try:
        with connect() as connection:
            yield DrawingRepository(connection)
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable") from exc


def service_from(repository: DrawingRepository) -> CADQueryService:
    service = CADQueryService(repository, get_semantic_service_singleton())
    service.set_hybrid_search_service(get_hybrid_service_singleton())
    return service


def get_semantic_service_singleton() -> SemanticSearchService:
    global _semantic_service
    if _semantic_service is None:
        _semantic_service = SemanticSearchService()
    return _semantic_service


def get_hybrid_service_singleton() -> HybridRetrievalService:
    global _hybrid_service
    if _hybrid_service is None:
        _hybrid_service = HybridRetrievalService(get_semantic_service_singleton())
    return _hybrid_service


@app.exception_handler(DrawingNotFoundError)
async def drawing_not_found_handler(_, exc: DrawingNotFoundError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(RetrievalRunNotFoundError)
async def retrieval_run_not_found_handler(_, exc: RetrievalRunNotFoundError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(DeepSeekConfigurationError)
async def deepseek_configuration_handler(_, exc: DeepSeekConfigurationError):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(DeepSeekRequestError)
async def deepseek_request_handler(_, exc: DeepSeekRequestError):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(ChatLoopError)
async def chat_loop_handler(_, exc: ChatLoopError):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(SemanticSearchError)
async def semantic_search_handler(_, exc: SemanticSearchError):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


def get_chat_service_singleton() -> CADChatService:
    global _chat_service
    if _chat_service is None:
        _chat_service = CADChatService(DeepSeekClient.from_env())
    return _chat_service

@app.exception_handler(ToolExecutionError)
async def tool_error_handler(_, exc: ToolExecutionError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/", tags=["system"])
def root() -> dict[str, str]:
    return {
        "service": "CAD Knowledge Base API",
        "status": "ok",
        "docs": "/docs",
    }


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/dependencies", tags=["system"])
def dependency_health() -> Any:
    result: dict[str, Any] = {"status": "ok", "postgresql": {}, "qdrant": {}}
    try:
        with connect() as connection:
            connection.execute("SELECT 1").fetchone()
        result["postgresql"] = {"status": "ok"}
    except psycopg.Error as exc:
        result["status"] = "degraded"
        result["postgresql"] = {"status": "error", "error": type(exc).__name__}

    try:
        semantic = get_semantic_service_singleton()
        points = semantic.client.count(
            collection_name=semantic.config.collection_name,
            exact=True,
        ).count
        result["qdrant"] = {
            "status": "ok",
            "url": semantic.config.qdrant_url,
            "collection": semantic.config.collection_name,
            "points": points,
        }
    except Exception as exc:
        result["status"] = "degraded"
        result["qdrant"] = {"status": "error", "error": type(exc).__name__}

    if result["status"] != "ok":
        return JSONResponse(status_code=503, content=result)
    return result


@app.get("/api/drawings", tags=["drawings"])
def list_drawings(
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    repository: DrawingRepository = Depends(get_repository),
) -> dict[str, Any]:
    return service_from(repository).list_drawings(search=search, limit=limit, offset=offset)


@app.get("/api/drawings/{drawing_key}/revisions", tags=["drawings"])
def list_revisions(
    drawing_key: str,
    limit: int = Query(default=100, ge=1, le=500),
    repository: DrawingRepository = Depends(get_repository),
) -> dict[str, Any]:
    try:
        return service_from(repository).list_revisions(drawing_key=drawing_key, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/drawings/{drawing_key}/assets", tags=["drawings"])
def list_assets(
    drawing_key: str,
    revision_id: UUID | None = Query(default=None),
    asset_role: str | None = Query(default=None),
    repository: DrawingRepository = Depends(get_repository),
) -> dict[str, Any]:
    try:
        result = service_from(repository).list_assets(
            drawing_key=drawing_key,
            revision_id=revision_id,
            asset_role=asset_role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    for asset in result["items"]:
        asset.pop("relative_path", None)
        asset["content_url"] = f"/api/assets/{asset['id']}/content"
    return result


@app.get("/api/drawings/{drawing_key}/features", tags=["fusion"])
def list_unified_features(
    drawing_key: str,
    revision_id: UUID | None = Query(default=None),
    feature_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    repository: DrawingRepository = Depends(get_repository),
) -> dict[str, Any]:
    try:
        return service_from(repository).list_unified_features(
            drawing_key=drawing_key,
            revision_id=revision_id,
            feature_type=feature_type,
            status=status,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/drawings/{drawing_key}/viewer", tags=["viewer"])
def drawing_viewer(
    drawing_key: str,
    revision_id: UUID | None = Query(default=None),
    repository: DrawingRepository = Depends(get_repository),
) -> dict[str, Any]:
    try:
        result = service_from(repository).get_viewer_data(
            drawing_key=drawing_key,
            revision_id=revision_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    for asset in result["assets"]:
        asset.pop("relative_path", None)
        asset["content_url"] = f"/api/assets/{asset['id']}/content"
    return result


@app.get("/api/assets/{asset_id}/content", tags=["drawings"])
def asset_content(
    asset_id: UUID,
    repository: DrawingRepository = Depends(get_repository),
) -> FileResponse:
    asset = repository.get_asset(asset_id)
    project_root = PROJECT_ROOT.resolve()
    candidate = Path(asset["relative_path"])
    resolved = (candidate if candidate.is_absolute() else project_root / candidate).resolve()
    try:
        resolved.relative_to(project_root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Asset is outside the project asset root") from exc
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="Asset file is missing")
    return FileResponse(
        resolved,
        media_type=asset.get("media_type") or "application/octet-stream",
    )


@app.post("/api/drawings/upload", status_code=202, tags=["ingestion"])
async def upload_drawing(
    background_tasks: BackgroundTasks,
    dwg: UploadFile = File(..., description="DWG source file"),
    png: UploadFile | None = File(default=None, description="Optional rendered PNG"),
    drawing_key: str | None = Form(default=None),
    title: str | None = Form(default=None),
) -> dict[str, Any]:
    """Save a DWG/PNG pair and queue the complete CAD ingestion pipeline."""

    try:
        job = await create_upload_job(
            dwg=dwg,
            png=png,
            drawing_key=drawing_key,
            title=title,
        )
    except UploadValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable") from exc

    background_tasks.add_task(run_upload_pipeline, job["job_id"])
    return {
        **job,
        "status_url": f"/api/drawings/upload/{job['job_id']}",
        "message": "Upload accepted; CAD/OCR/Vision processing has been queued",
    }


@app.get("/api/drawings/upload/{job_id}", tags=["ingestion"])
def upload_status(job_id: UUID) -> dict[str, Any]:
    """Return durable status and stage statistics for one upload job."""

    try:
        return get_upload_job(job_id)
    except IngestionJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="PostgreSQL is unavailable") from exc


@app.post("/api/search/hybrid", tags=["hybrid"])
def hybrid_search(
    request: HybridSearchRequest,
    repository: DrawingRepository = Depends(get_repository),
) -> dict[str, Any]:
    try:
        return service_from(repository).hybrid_search(**request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/search/runs/{search_run_id}", tags=["hybrid"])
def hybrid_search_audit(
    search_run_id: UUID,
    repository: DrawingRepository = Depends(get_repository),
) -> dict[str, Any]:
    return repository.get_hybrid_search_audit(search_run_id)


@app.get("/api/semantic/search", tags=["semantic"])
def semantic_search(
    q: str = Query(min_length=1),
    drawing_key: str | None = Query(default=None),
    revision_id: str | None = Query(default=None),
    source_type: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
    min_score: float = Query(default=0.35, ge=0, le=1),
) -> dict[str, Any]:
    try:
        return get_semantic_service_singleton().search(
            query=q,
            drawing_key=drawing_key,
            revision_id=revision_id,
            source_type=source_type,
            limit=limit,
            min_score=min_score,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/drawings/{drawing_key}/summary", tags=["drawings"])
def drawing_summary(
    drawing_key: str,
    repository: DrawingRepository = Depends(get_repository),
) -> dict[str, Any]:
    return service_from(repository).get_drawing_summary(drawing_key=drawing_key)


@app.get("/api/drawings/{drawing_key}/entities", tags=["cad"])
def list_entities(
    drawing_key: str,
    entity_type: str | None = Query(default=None),
    layer_name: str | None = Query(default=None),
    cad_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    include_raw_properties: bool = Query(default=False),
    repository: DrawingRepository = Depends(get_repository),
) -> dict[str, Any]:
    return service_from(repository).list_entities(
        drawing_key=drawing_key,
        entity_type=entity_type,
        layer_name=layer_name,
        cad_id=cad_id,
        limit=limit,
        offset=offset,
        include_raw_properties=include_raw_properties,
    )


@app.get("/api/drawings/{drawing_key}/dimensions", tags=["cad"])
def find_dimensions(
    drawing_key: str,
    dimension_kind: str | None = Query(default=None),
    value: float | None = Query(default=None, ge=0),
    tolerance: float = Query(default=0.01, ge=0),
    text: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    repository: DrawingRepository = Depends(get_repository),
) -> dict[str, Any]:
    return service_from(repository).find_dimensions(
        drawing_key=drawing_key,
        dimension_kind=dimension_kind,
        value=value,
        tolerance=tolerance,
        text=text,
        limit=limit,
        offset=offset,
    )


@app.get("/api/drawings/{drawing_key}/layers", tags=["cad"])
def list_layers(
    drawing_key: str,
    limit: int = Query(default=200, ge=1, le=500),
    repository: DrawingRepository = Depends(get_repository),
) -> dict[str, Any]:
    return service_from(repository).list_layers(drawing_key=drawing_key, limit=limit)


@app.get("/api/drawings/{drawing_key}/blocks", tags=["cad"])
def list_blocks(
    drawing_key: str,
    limit: int = Query(default=200, ge=1, le=500),
    repository: DrawingRepository = Depends(get_repository),
) -> dict[str, Any]:
    return service_from(repository).list_blocks(drawing_key=drawing_key, limit=limit)


@app.get("/api/drawings/{drawing_key}/native-annotations", tags=["annotations"])
def list_native_annotations(
    drawing_key: str,
    text: str | None = Query(default=None),
    parsed_kind: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    repository: DrawingRepository = Depends(get_repository),
) -> dict[str, Any]:
    return service_from(repository).list_native_annotations(
        drawing_key=drawing_key,
        text=text,
        parsed_kind=parsed_kind,
        limit=limit,
        offset=offset,
    )


@app.get("/api/drawings/{drawing_key}/ocr", tags=["annotations"])
def list_ocr_items(
    drawing_key: str,
    text: str | None = Query(default=None),
    min_confidence: float | None = Query(default=None, ge=0, le=1),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    repository: DrawingRepository = Depends(get_repository),
) -> dict[str, Any]:
    return service_from(repository).list_ocr_items(
        drawing_key=drawing_key,
        text=text,
        min_confidence=min_confidence,
        limit=limit,
        offset=offset,
    )


@app.get("/api/drawings/{drawing_key}/vision", tags=["vision"])
def list_vision_observations(
    drawing_key: str,
    observation_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    min_confidence: float | None = Query(default=None, ge=0, le=1),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    repository: DrawingRepository = Depends(get_repository),
) -> dict[str, Any]:
    return service_from(repository).list_vision_observations(
        drawing_key=drawing_key,
        observation_type=observation_type,
        status=status,
        min_confidence=min_confidence,
        limit=limit,
        offset=offset,
    )


@app.get("/api/drawings/{drawing_key}/conflicts", tags=["fusion"])
def list_conflicts(
    drawing_key: str,
    status: str | None = Query(default="open"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    repository: DrawingRepository = Depends(get_repository),
) -> dict[str, Any]:
    return service_from(repository).list_conflicts(
        drawing_key=drawing_key,
        status=status,
        limit=limit,
        offset=offset,
    )


@app.get("/api/drawings/{drawing_key}/cad-ocr", tags=["fusion"])
def compare_cad_ocr(
    drawing_key: str,
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    repository: DrawingRepository = Depends(get_repository),
) -> dict[str, Any]:
    return service_from(repository).compare_cad_ocr(
        drawing_key=drawing_key,
        status=status,
        limit=limit,
        offset=offset,
    )


@app.get("/api/features/{feature_id}/evidence", tags=["fusion"])
def feature_evidence(
    feature_id: str,
    repository: DrawingRepository = Depends(get_repository),
) -> dict[str, Any]:
    return service_from(repository).get_feature_evidence(feature_id=feature_id)


@app.post("/api/tools/{tool_name}", tags=["tools"])
def run_tool(
    tool_name: str,
    request: ToolCallRequest,
    repository: DrawingRepository = Depends(get_repository),
) -> dict[str, Any]:
    """Execute one whitelisted tool; intended for the future LLM adapter."""

    return execute_tool(service_from(repository), tool_name, request.arguments)

from app.api.chat import router as chat_router  # noqa: E402

app.include_router(chat_router)
