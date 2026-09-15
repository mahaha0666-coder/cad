"""PostgreSQL verification of Qdrant drawing candidates."""

from __future__ import annotations

from typing import Any

from app.repositories import DrawingRepository

from .query_planner import HybridQueryPlan


STATUS_SCORES = {
    "verified": 1.0,
    "partially_verified": 0.65,
    "conflict": 0.20,
    "semantic_candidate": 0.42,
    "rejected": 0.0,
}


def _evidence_score(feature: dict[str, Any] | None, *, is_summary: bool = False) -> float:
    if is_summary:
        return 0.8
    if not feature:
        return 0.0
    if feature.get("fusion_status") == "confirmed":
        return 1.0
    if feature.get("fusion_status") == "conflict":
        # Conflicting evidence must not receive the same evidence bonus as a
        # confirmed feature, even when both have many source records.
        return 0.3
    score = 0.25
    if feature.get("cad_evidence_count", 0):
        score += 0.25
    if feature.get("native_evidence_count", 0):
        score += 0.2
    if feature.get("ocr_evidence_count", 0):
        score += 0.15
    if feature.get("vision_evidence_count", 0):
        score += 0.05
    return min(score, 1.0)


def _constraint_result(
    plan: HybridQueryPlan,
    *,
    dimension_items: list[dict[str, Any]],
    entity_counts: dict[str, int],
) -> dict[str, Any]:
    dimension_required = bool(plan.dimension_kind or plan.dimension_value is not None)
    dimension_ok = bool(dimension_items) if dimension_required else True
    entity_checks = {entity_type: entity_counts.get(entity_type, 0) > 0 for entity_type in plan.entity_types}
    entity_ok = all(entity_checks.values()) if entity_checks else True
    return {
        "passed": dimension_ok and entity_ok,
        "dimension": {
            "required": dimension_required,
            "kind": plan.dimension_kind,
            "value": plan.dimension_value,
            "tolerance": plan.tolerance,
            "matched_count": len(dimension_items),
            "items": dimension_items[:10],
        },
        "entity_types": {
            "required": list(plan.entity_types),
            "counts": {entity_type: entity_counts.get(entity_type, 0) for entity_type in plan.entity_types},
            "passed": entity_checks,
        },
    }


def verify_candidates(
    repository: DrawingRepository,
    candidates: list[dict[str, Any]],
    plan: HybridQueryPlan,
) -> list[dict[str, Any]]:
    """Verify candidates with authoritative PostgreSQL facts.

    The number of PostgreSQL calls is bounded by distinct revisions rather than
    the number of vector hits. This avoids an N+1 query pattern while keeping
    the implementation compatible with the current repository API.
    """

    revision_ids = [candidate["revision_id"] for candidate in candidates if candidate.get("revision_id")]
    revisions = repository.get_revisions_by_ids(revision_ids)
    revision_map = {str(row["revision_id"]): row for row in revisions}

    feature_ids = [
        feature_id
        for candidate in candidates
        for feature_id in candidate.get("source_feature_ids", [])
    ]
    features = repository.get_current_features_by_ids(feature_ids)
    feature_map = {str(row["feature_id"]): row for row in features}

    revision_checks: dict[str, dict[str, Any]] = {}
    for revision_id, revision in revision_map.items():
        summary = repository.get_drawing_summary(revision_id=revision_id)
        dimension_items: list[dict[str, Any]] = []
        if plan.dimension_kind or plan.dimension_value is not None:
            dimension_items = repository.find_dimensions(
                revision_id=revision_id,
                dimension_kind=plan.dimension_kind,
                value=plan.dimension_value,
                tolerance=plan.tolerance,
                limit=100,
            )["items"]
        entity_counts = summary["counts"].get("entities", {})
        revision_checks[revision_id] = {
            "summary": summary,
            "constraints": _constraint_result(
                plan,
                dimension_items=dimension_items,
                entity_counts=entity_counts,
            ),
        }

    verified: list[dict[str, Any]] = []
    for candidate in candidates:
        revision_id = str(candidate.get("revision_id"))
        revision = revision_map.get(revision_id)
        check = revision_checks.get(revision_id)
        if not revision or not check:
            status = "rejected"
            exact_score = 0.0
            evidence_score = 0.0
            exact_payload: dict[str, Any] = {"source_exists": False}
            warnings = ["Qdrant 候选无法在 PostgreSQL 中找到对应图纸版本"]
        else:
            source_features = [
                feature_map[feature_id]
                for feature_id in candidate.get("source_feature_ids", [])
                if feature_id in feature_map
            ]
            source_types = set(candidate.get("source_types", []))
            source_is_summary = "drawing_revision" in source_types
            constraint_passed = bool(check["constraints"]["passed"])
            has_confirmed = any(item.get("fusion_status") == "confirmed" for item in source_features)
            has_conflict = any(item.get("fusion_status") == "conflict" for item in source_features)
            has_partial = any(item.get("fusion_status") in {"cad_only", "ocr_only", "ambiguous"} for item in source_features)
            has_vision_only = any(
                item.get("feature_type") == "vision_observation" or item.get("fusion_status") == "vision_only"
                for item in source_features
            )

            if not constraint_passed:
                status = "rejected"
            elif plan.has_exact_constraints and (source_is_summary or has_confirmed):
                # Explicit CAD constraints passed in PostgreSQL. A summary hit
                # proves the revision-level condition; a confirmed feature hit
                # also carries fused source evidence.
                status = "verified"
            elif has_conflict:
                status = "conflict"
            elif has_vision_only and not has_confirmed:
                status = "semantic_candidate"
            elif source_is_summary or has_confirmed or has_partial:
                # Without an explicit exact constraint, semantic similarity
                # and source existence do not prove the natural-language
                # structure. Keep the candidate traceable but provisional.
                status = "partially_verified"
            elif source_features:
                status = "semantic_candidate"
            else:
                status = "partially_verified"

            exact_score = STATUS_SCORES[status]
            evidence_score = max(
                (_evidence_score(item) for item in source_features),
                default=_evidence_score(None, is_summary=source_is_summary),
            )
            if has_conflict or (has_vision_only and not has_confirmed):
                # A conflict or a Vision-only candidate is useful context,
                # but must not obtain a confirmed-evidence bonus.
                evidence_score = min(evidence_score, 0.3)
            exact_payload = {
                "source_exists": bool(source_features or source_is_summary),
                "revision": revision,
                "summary": {
                    "counts": check["summary"]["counts"],
                    "assets": check["summary"].get("assets", []),
                },
                "constraints": check["constraints"],
                "matched_features": source_features,
            }
            warnings = []
            if has_conflict:
                warnings.append("候选关联的 Unified Feature 存在 CAD/OCR 或视觉证据冲突")
            if has_vision_only:
                warnings.append("候选包含 Vision-only 观察，仍需人工或更多 CAD 证据确认")
            if not source_features and not source_is_summary:
                warnings.append("语义块存在，但其来源 Unified Feature 未在当前版本中找到")

        semantic_score = float(candidate.get("semantic_score", 0.0))
        final_score = (
            0.60 * semantic_score + 0.30 * exact_score + 0.10 * evidence_score
            if status != "rejected"
            else 0.0
        )
        item = dict(candidate)
        item.update({
            "status": status,
            "semantic_score": round(semantic_score, 6),
            "exact_score": round(exact_score, 6),
            "evidence_score": round(evidence_score, 6),
            "final_score": round(final_score, 6),
            "exact": exact_payload,
            "warnings": warnings,
        })
        verified.append(item)

    return sorted(verified, key=lambda item: item["final_score"], reverse=True)
