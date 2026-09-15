"""End-to-end Qdrant recall followed by PostgreSQL verification."""

from __future__ import annotations

from time import perf_counter
from typing import Any

from app.repositories import DrawingRepository
from app.semantic import SemanticSearchService

from .candidate_aggregator import aggregate_semantic_hits
from .exact_verifier import verify_candidates
from .query_planner import build_query_plan


class HybridRetrievalService:
    def __init__(self, semantic_service: SemanticSearchService):
        self.semantic_service = semantic_service

    def search(
        self,
        repository: DrawingRepository,
        *,
        query: str,
        drawing_key: str | None = None,
        revision_id: str | None = None,
        top_k: int = 30,
        final_limit: int = 10,
        min_score: float = 0.35,
        dimension_kind: str | None = None,
        dimension_value: float | None = None,
        tolerance: float = 0.01,
        entity_types: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        if not 1 <= top_k <= 50:
            raise ValueError("top_k must be between 1 and 50")
        if not 1 <= final_limit <= 20:
            raise ValueError("final_limit must be between 1 and 20")
        if not 0 <= min_score <= 1:
            raise ValueError("min_score must be between 0 and 1")

        started = perf_counter()
        try:
            plan = build_query_plan(
                query,
                drawing_key=drawing_key,
                revision_id=revision_id,
                dimension_kind=dimension_kind,
                dimension_value=dimension_value,
                tolerance=tolerance,
                entity_types=entity_types,
            )
            semantic = self.semantic_service.search(
                query=plan.query,
                drawing_key=plan.drawing_key,
                revision_id=plan.revision_id,
                limit=top_k,
                min_score=min_score,
            )
        except Exception as exc:
            try:
                failed_run_id = repository.record_failed_hybrid_search(
                    query=query,
                    scope={
                        key: value
                        for key, value in {
                            "drawing_key": drawing_key,
                            "revision_id": revision_id,
                        }.items()
                        if value
                    },
                    semantic_collection=self.semantic_service.config.collection_name,
                    embedding_model=self.semantic_service.config.embedding_model,
                    top_k=top_k,
                    final_limit=final_limit,
                    min_score=min_score,
                    error_message=f"{type(exc).__name__}: {exc}",
                    query_plan=plan.as_dict() if "plan" in locals() else None,
                )
                setattr(exc, "retrieval_run_id", failed_run_id)
            except Exception:
                # Preserve the original retrieval error if the audit database
                # write itself is unavailable.
                pass
            raise
        candidates = aggregate_semantic_hits(semantic["items"])
        verified = verify_candidates(repository, candidates, plan) if candidates else []
        items = verified[:final_limit]
        warnings = list(plan.warnings)
        if not semantic["items"]:
            warnings.append("Qdrant 没有达到 min_score 的语义候选")
        if any(item["status"] == "rejected" for item in verified):
            warnings.append("部分语义候选未通过 PostgreSQL 精确约束验证，已标记为 rejected")
        if any(item["status"] in {"conflict", "semantic_candidate"} for item in items):
            warnings.append("结果中包含冲突或仅语义候选，请查看每个结果的 exact 和 warnings")

        retrieval = {
            "semantic_collection": semantic["collection"],
            "embedding_model": semantic["embedding_model"],
            "semantic_hit_count": len(semantic["items"]),
            "candidate_revision_count": len(candidates),
            "total_candidates": len(verified),
            "returned_count": len(items),
            "top_k": top_k,
            "final_limit": final_limit,
            "min_score": min_score,
            "duration_ms": round((perf_counter() - started) * 1000, 3),
            "formula": "0.60*semantic_score + 0.30*exact_score + 0.10*evidence_score",
        }
        search_run_id = repository.record_hybrid_search(
            query=plan.query,
            query_plan=plan.as_dict(),
            scope=semantic["scope"],
            retrieval=retrieval,
            candidates=verified,
            warnings=warnings,
        )
        return {
            "search_run_id": search_run_id,
            "query": plan.query,
            "query_plan": plan.as_dict(),
            "scope": semantic["scope"],
            "retrieval": retrieval,
            "count": len(items),
            "total_candidates": len(verified),
            "items": [
                {"rank": index, **item}
                for index, item in enumerate(items, start=1)
            ],
            "warnings": warnings,
        }
