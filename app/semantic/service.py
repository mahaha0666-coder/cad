"""Qdrant-backed semantic indexing and retrieval."""

from __future__ import annotations

import threading
from collections.abc import Iterable, Sequence
from typing import Any

from fastembed import TextEmbedding
from qdrant_client import QdrantClient, models

from .chunks import SemanticChunk
from .config import SemanticConfig


class SemanticSearchError(RuntimeError):
    """Raised when the semantic index is unavailable or invalid."""


class SemanticSearchService:
    def __init__(self, config: SemanticConfig | None = None):
        self.config = config or SemanticConfig.from_env()
        self._client: QdrantClient | None = None
        self._model: TextEmbedding | None = None
        self._lock = threading.Lock()

    @property
    def client(self) -> QdrantClient:
        with self._lock:
            if self._client is None:
                if self.config.qdrant_url:
                    self._client = QdrantClient(
                        url=self.config.qdrant_url,
                        api_key=self.config.qdrant_api_key,
                        grpc_port=self.config.qdrant_grpc_port,
                        prefer_grpc=self.config.qdrant_prefer_grpc,
                    )
                else:
                    self.config.qdrant_path.mkdir(parents=True, exist_ok=True)
                    self._client = QdrantClient(path=str(self.config.qdrant_path))
            return self._client

    @property
    def model(self) -> TextEmbedding:
        with self._lock:
            if self._model is None:
                self._model = TextEmbedding(model_name=self.config.embedding_model)
                actual = self._model.embedding_size
                if callable(actual):
                    actual = actual()
                if int(actual) != self.config.embedding_dimensions:
                    raise SemanticSearchError(
                        f"embedding dimension mismatch: configured={self.config.embedding_dimensions}, actual={actual}"
                    )
            return self._model

    def ensure_collection(self) -> None:
        names = {item.name for item in self.client.get_collections().collections}
        if self.config.collection_name not in names:
            self.client.create_collection(
                collection_name=self.config.collection_name,
                vectors_config=models.VectorParams(
                    size=self.config.embedding_dimensions,
                    distance=models.Distance.COSINE,
                ),
            )

    def index_chunks(self, chunks: Sequence[SemanticChunk], batch_size: int = 32) -> int:
        self.ensure_collection()
        indexed = 0
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start:start + batch_size]
            vectors = list(self.model.passage_embed([item.content for item in batch]))
            points = [
                models.PointStruct(
                    id=str(chunk.point_id),
                    vector=vector.tolist(),
                    payload=chunk.payload(),
                )
                for chunk, vector in zip(batch, vectors, strict=True)
            ]
            self.client.upsert(
                collection_name=self.config.collection_name,
                points=points,
                wait=True,
            )
            indexed += len(points)
        return indexed

    def delete_points(self, point_ids: Iterable[str]) -> None:
        ids = list(point_ids)
        if ids:
            self.client.delete(
                collection_name=self.config.collection_name,
                points_selector=models.PointIdsList(points=ids),
                wait=True,
            )

    def search(
        self,
        *,
        query: str,
        drawing_key: str | None = None,
        revision_id: str | None = None,
        source_type: str | None = None,
        limit: int = 10,
        min_score: float = 0.35,
    ) -> dict[str, Any]:
        query = query.strip()
        if not query:
            raise ValueError("query must be non-empty")
        if drawing_key and revision_id:
            raise ValueError("provide at most one of drawing_key or revision_id")
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        if not 0 <= min_score <= 1:
            raise ValueError("min_score must be between 0 and 1")
        if source_type and source_type not in {"drawing_revision", "unified_feature"}:
            raise ValueError("source_type must be drawing_revision or unified_feature")

        names = {item.name for item in self.client.get_collections().collections}
        if self.config.collection_name not in names:
            raise SemanticSearchError("semantic collection is not indexed; run scripts/index_semantic.py first")

        must: list[models.FieldCondition] = []
        for key, value in (
            ("drawing_key", drawing_key),
            ("drawing_revision_id", revision_id),
            ("source_type", source_type),
        ):
            if value:
                must.append(models.FieldCondition(key=key, match=models.MatchValue(value=value)))
        query_filter = models.Filter(must=must) if must else None
        vector = next(iter(self.model.query_embed(query))).tolist()
        response = self.client.query_points(
            collection_name=self.config.collection_name,
            query=vector,
            query_filter=query_filter,
            limit=limit,
            score_threshold=min_score,
            with_payload=True,
        )
        items = []
        for point in response.points:
            payload = dict(point.payload or {})
            items.append({
                "score": float(point.score),
                "chunk_id": payload.get("chunk_id"),
                "drawing_key": payload.get("drawing_key"),
                "drawing_title": payload.get("drawing_title"),
                "revision_id": payload.get("drawing_revision_id"),
                "source_type": payload.get("source_type"),
                "source_id": payload.get("source_id"),
                "source_external_id": payload.get("source_external_id"),
                "chunk_kind": payload.get("chunk_kind"),
                "content": payload.get("content"),
                "feature_type": payload.get("feature_type"),
                "fusion_status": payload.get("fusion_status"),
                "dimension_kind": payload.get("dimension_kind"),
                "canonical_value": payload.get("canonical_value"),
                "unit": payload.get("unit"),
            })
        return {
            "query": query,
            "collection": self.config.collection_name,
            "embedding_model": self.config.embedding_model,
            "scope": {key: value for key, value in {
                "drawing_key": drawing_key,
                "revision_id": revision_id,
                "source_type": source_type,
            }.items() if value},
            "count": len(items),
            "items": items,
        }
