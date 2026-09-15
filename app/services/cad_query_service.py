"""Small service facade for tool calls.

The service keeps the tool layer independent of PostgreSQL table details and
provides one place for future authorization, logging, and query budgets.
"""

from __future__ import annotations

from typing import Any

from app.repositories import DrawingRepository


class CADQueryService:
    def __init__(self, repository: DrawingRepository, semantic_search: Any | None = None):
        self.repository = repository
        self.semantic_search_service = semantic_search
        self.hybrid_search_service: Any | None = None

    def set_hybrid_search_service(self, service: Any) -> None:
        """Attach the hybrid service without changing repository ownership."""

        self.hybrid_search_service = service

    def list_drawings(self, **kwargs: Any) -> dict[str, Any]:
        return self.repository.list_drawings(**kwargs)

    def get_drawing_summary(self, **kwargs: Any) -> dict[str, Any]:
        return self.repository.get_drawing_summary(**kwargs)

    def list_revisions(self, **kwargs: Any) -> dict[str, Any]:
        return self.repository.list_revisions(**kwargs)

    def list_assets(self, **kwargs: Any) -> dict[str, Any]:
        return self.repository.list_assets(**kwargs)

    def list_unified_features(self, **kwargs: Any) -> dict[str, Any]:
        return self.repository.list_unified_features(**kwargs)

    def get_viewer_data(self, **kwargs: Any) -> dict[str, Any]:
        return self.repository.get_viewer_data(**kwargs)

    def get_asset(self, asset_id: str) -> dict[str, Any]:
        return self.repository.get_asset(asset_id)

    def count_entities(self, **kwargs: Any) -> dict[str, Any]:
        return self.repository.count_entities(**kwargs)

    def list_entities(self, **kwargs: Any) -> dict[str, Any]:
        return self.repository.list_entities(**kwargs)

    def list_layers(self, **kwargs: Any) -> dict[str, Any]:
        return self.repository.list_layers(**kwargs)

    def list_blocks(self, **kwargs: Any) -> dict[str, Any]:
        return self.repository.list_blocks(**kwargs)

    def list_native_annotations(self, **kwargs: Any) -> dict[str, Any]:
        return self.repository.list_native_annotations(**kwargs)

    def list_ocr_items(self, **kwargs: Any) -> dict[str, Any]:
        return self.repository.list_ocr_items(**kwargs)

    def find_dimensions(self, **kwargs: Any) -> dict[str, Any]:
        return self.repository.find_dimensions(**kwargs)

    def list_conflicts(self, **kwargs: Any) -> dict[str, Any]:
        return self.repository.list_conflicts(**kwargs)

    def get_feature_evidence(self, **kwargs: Any) -> dict[str, Any]:
        return self.repository.get_feature_evidence(**kwargs)

    def compare_cad_ocr(self, **kwargs: Any) -> dict[str, Any]:
        return self.repository.compare_cad_ocr(**kwargs)

    def list_vision_observations(self, **kwargs: Any) -> dict[str, Any]:
        return self.repository.list_vision_observations(**kwargs)

    def semantic_search(self, **kwargs: Any) -> dict[str, Any]:
        if self.semantic_search_service is None:
            raise ValueError("semantic search service is not configured")
        return self.semantic_search_service.search(**kwargs)

    def hybrid_search(self, **kwargs: Any) -> dict[str, Any]:
        if self.hybrid_search_service is None:
            raise ValueError("hybrid search service is not configured")
        return self.hybrid_search_service.search(self.repository, **kwargs)
