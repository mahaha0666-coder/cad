"""Read-only repository for exact CAD/OCR/fusion queries.

This module is deliberately the only layer that knows the PostgreSQL table
layout. Callers use drawing keys and domain filters instead of composing SQL.
No method accepts arbitrary SQL, table names, or SQL fragments from a caller.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


SUPPORTED_ENTITY_TYPES = frozenset(
    {"LINE", "CIRCLE", "ARC", "POLYLINE", "LWPOLYLINE", "TEXT", "MTEXT", "DIMENSION", "INSERT"}
)
DIMENSION_KINDS = frozenset({"linear", "radius", "diameter", "angle"})
FUSION_STATUSES = frozenset({"confirmed", "cad_only", "ocr_only", "conflict", "ambiguous"})


def _jsonable(value: Any) -> Any:
    """Convert psycopg values into JSON-compatible values recursively."""

    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


class DrawingNotFoundError(LookupError):
    """Raised when a requested drawing or revision does not exist."""


class RetrievalRunNotFoundError(LookupError):
    """Raised when a retrieval audit run does not exist."""


class DrawingRepository:
    """Repository for precise, provenance-preserving CAD queries.

    The connection is supplied by the caller and is never created implicitly.
    The repository performs reads only; transaction/connection lifecycle is
    owned by the caller.
    """

    def __init__(self, connection: psycopg.Connection[Any]):
        self.connection = connection

    def _fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self.connection.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return [_jsonable(row) for row in cur.fetchall()]

    def _fetchone(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        with self.connection.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return _jsonable(row) if row else None

    @staticmethod
    def _validate_limit_offset(limit: int, offset: int) -> None:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if offset < 0:
            raise ValueError("offset must be non-negative")

    def resolve_revision(
        self,
        *,
        drawing_key: str | None = None,
        revision_id: str | UUID | None = None,
    ) -> dict[str, Any]:
        """Resolve one revision, requiring an explicit drawing scope.

        A drawing key resolves to its highest revision sequence. Callers that
        need historical precision should pass revision_id explicitly.
        """

        if bool(drawing_key) == bool(revision_id):
            raise ValueError("Provide exactly one of drawing_key or revision_id")

        if revision_id is not None:
            row = self._fetchone(
                """
                SELECT r.id AS revision_id, r.drawing_id, d.drawing_key, d.title,
                       r.revision_code, r.revision_sequence, r.status,
                       r.cad_unit, r.unit_scale, r.schema_version,
                       r.cad_coordinate_system, r.cad_bounds
                FROM core.drawing_revisions AS r
                JOIN core.drawings AS d ON d.id = r.drawing_id
                WHERE r.id = %s
                """,
                (revision_id,),
            )
        else:
            row = self._fetchone(
                """
                SELECT r.id AS revision_id, r.drawing_id, d.drawing_key, d.title,
                       r.revision_code, r.revision_sequence, r.status,
                       r.cad_unit, r.unit_scale, r.schema_version,
                       r.cad_coordinate_system, r.cad_bounds
                FROM core.drawing_revisions AS r
                JOIN core.drawings AS d ON d.id = r.drawing_id
                WHERE d.drawing_key = %s
                ORDER BY r.revision_sequence DESC, r.created_at DESC
                LIMIT 1
                """,
                (drawing_key,),
            )

        if not row:
            target = f"revision_id={revision_id}" if revision_id is not None else f"drawing_key={drawing_key}"
            raise DrawingNotFoundError(f"No drawing revision found for {target}")
        return row

    def _resolve_scoped_revision(
        self,
        *,
        drawing_key: str | None = None,
        revision_id: str | UUID | None = None,
    ) -> dict[str, Any]:
        """Resolve a revision and, when both are supplied, verify their scope."""

        if drawing_key and revision_id:
            revision = self.resolve_revision(revision_id=revision_id)
            if revision["drawing_key"] != drawing_key:
                raise DrawingNotFoundError(
                    f"revision_id={revision_id} does not belong to drawing_key={drawing_key}"
                )
            return revision
        return self.resolve_revision(drawing_key=drawing_key, revision_id=revision_id)

    def list_drawings(
        self,
        *,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List drawings with their latest revision and exact data counts."""

        self._validate_limit_offset(limit, offset)
        search_pattern = f"%{search.strip()}%" if search and search.strip() else None
        rows = self._fetchall(
            """
            SELECT d.id AS drawing_id, d.drawing_key, d.title, d.drawing_number,
                   d.part_number, d.drawing_category,
                   r.id AS revision_id, r.revision_code, r.revision_sequence,
                   r.status AS revision_status, r.cad_unit,
                   COALESCE(ec.entity_count, 0) AS entity_count,
                   COALESCE(nac.native_annotation_count, 0) AS native_annotation_count,
                   COALESCE(oc.ocr_item_count, 0) AS ocr_item_count,
                   COALESCE(fc.feature_count, 0) AS unified_feature_count,
                   COALESCE(vc.observation_count, 0) AS vision_observation_count
            FROM core.drawings AS d
            LEFT JOIN LATERAL (
                SELECT r1.*
                FROM core.drawing_revisions AS r1
                WHERE r1.drawing_id = d.id
                ORDER BY r1.revision_sequence DESC, r1.created_at DESC
                LIMIT 1
            ) AS r ON true
            LEFT JOIN LATERAL (
                SELECT COUNT(*)::integer AS entity_count
                FROM cad.entities AS e
                WHERE e.drawing_revision_id = r.id
            ) AS ec ON true
            LEFT JOIN LATERAL (
                SELECT COUNT(*)::integer AS native_annotation_count
                FROM cad.native_annotations AS na
                WHERE na.drawing_revision_id = r.id
            ) AS nac ON true
            LEFT JOIN LATERAL (
                SELECT COUNT(*)::integer AS ocr_item_count
                FROM ocr.items AS oi
                JOIN ocr.runs AS oru ON oru.id = oi.ocr_run_id AND oru.is_current
                WHERE oru.drawing_revision_id = r.id
            ) AS oc ON true
            LEFT JOIN LATERAL (
                SELECT COUNT(*)::integer AS feature_count
                FROM fusion.features AS f
                JOIN fusion.runs AS fr ON fr.id = f.fusion_run_id AND fr.is_current
                WHERE f.drawing_revision_id = r.id
            ) AS fc ON true
            LEFT JOIN LATERAL (
                SELECT COUNT(*)::integer AS observation_count
                FROM vision.observations AS vo
                JOIN vision.runs AS vr ON vr.id = vo.vision_run_id AND vr.is_current
                WHERE vr.drawing_revision_id = r.id
            ) AS vc ON true
            WHERE (%s::text IS NULL OR d.drawing_key ILIKE %s OR d.title ILIKE %s)
            ORDER BY d.title
            LIMIT %s OFFSET %s
            """,
            (search_pattern, search_pattern, search_pattern, limit, offset),
        )
        return {"items": rows, "limit": limit, "offset": offset, "count": len(rows)}

    def get_drawing_summary(
        self,
        *,
        drawing_key: str | None = None,
        revision_id: str | UUID | None = None,
    ) -> dict[str, Any]:
        """Return one revision summary, including parser skip statistics."""

        revision = self.resolve_revision(drawing_key=drawing_key, revision_id=revision_id)
        rid = revision["revision_id"]
        counts = self._fetchall(
            """
            SELECT entity_type, COUNT(*)::integer AS count
            FROM cad.entities
            WHERE drawing_revision_id = %s
            GROUP BY entity_type
            ORDER BY entity_type
            """,
            (rid,),
        )
        layers = self._fetchone(
            "SELECT COUNT(*)::integer AS count FROM cad.layers WHERE drawing_revision_id = %s", (rid,)
        )
        blocks = self._fetchone(
            "SELECT COUNT(*)::integer AS count FROM cad.blocks WHERE drawing_revision_id = %s", (rid,)
        )
        native = self._fetchone(
            "SELECT COUNT(*)::integer AS count FROM cad.native_annotations WHERE drawing_revision_id = %s", (rid,)
        )
        ocr = self._fetchone(
            """
            SELECT COUNT(*)::integer AS count
            FROM ocr.items AS oi
            JOIN ocr.runs AS r ON r.id = oi.ocr_run_id AND r.is_current
            WHERE r.drawing_revision_id = %s
            """,
            (rid,),
        )
        features = self._fetchone(
            """
            SELECT COUNT(*)::integer AS count
            FROM fusion.features AS f
            JOIN fusion.runs AS r ON r.id = f.fusion_run_id AND r.is_current
            WHERE f.drawing_revision_id = %s
            """,
            (rid,),
        )
        vision = self._fetchone(
            """
            SELECT COUNT(*)::integer AS count
            FROM vision.observations AS o
            JOIN vision.runs AS r ON r.id = o.vision_run_id AND r.is_current
            WHERE r.drawing_revision_id = %s
            """,
            (rid,),
        )
        processing = self._fetchone(
            """
            SELECT statistics, tool_name, tool_version, run_fingerprint, finished_at
            FROM core.processing_runs
            WHERE drawing_revision_id = %s AND stage = 'cad_parse' AND is_current
            ORDER BY finished_at DESC NULLS LAST
            LIMIT 1
            """,
            (rid,),
        )
        assets = self._fetchall(
            """
            SELECT asset_role, file_name, relative_path, media_type, file_size, sha256
            FROM core.source_assets
            WHERE drawing_revision_id = %s
            ORDER BY asset_role, file_name
            """,
            (rid,),
        )
        return {
            "drawing": revision,
            "counts": {
                "entities": {row["entity_type"]: row["count"] for row in counts},
                "layers": layers["count"] if layers else 0,
                "blocks": blocks["count"] if blocks else 0,
                "native_annotations": native["count"] if native else 0,
                "ocr_items": ocr["count"] if ocr else 0,
                "unified_features": features["count"] if features else 0,
                "vision_observations": vision["count"] if vision else 0,
            },
            "cad_parse": processing or {},
            "assets": assets,
        }


    def list_revisions(self, drawing_key: str, limit: int = 100) -> dict[str, Any]:
        """List all revisions and their current derived-data counts."""

        if not drawing_key or not drawing_key.strip():
            raise ValueError("drawing_key must be non-empty")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        items = self._fetchall(
            """
            SELECT r.id AS revision_id, r.drawing_id, d.drawing_key, d.title,
                   r.revision_code, r.revision_sequence, r.supersedes_id,
                   r.status, r.cad_unit, r.unit_scale, r.schema_version,
                   r.cad_coordinate_system, r.cad_bounds, r.created_at,
                   (SELECT COUNT(*)::integer FROM cad.entities e
                    WHERE e.drawing_revision_id = r.id) AS entity_count,
                   (SELECT COUNT(*)::integer FROM cad.native_annotations n
                    WHERE n.drawing_revision_id = r.id) AS native_annotation_count,
                   (SELECT COUNT(*)::integer FROM ocr.items i
                    JOIN ocr.runs o ON o.id = i.ocr_run_id AND o.is_current
                    WHERE o.drawing_revision_id = r.id) AS ocr_item_count,
                   (SELECT COUNT(*)::integer FROM vision.observations o
                    JOIN vision.runs v ON v.id = o.vision_run_id AND v.is_current
                    WHERE v.drawing_revision_id = r.id) AS vision_observation_count,
                   (SELECT COUNT(*)::integer FROM fusion.features f
                    JOIN fusion.runs fr ON fr.id = f.fusion_run_id AND fr.is_current
                    WHERE f.drawing_revision_id = r.id) AS unified_feature_count,
                   (SELECT COUNT(*)::integer FROM semantic.chunks c
                    WHERE c.drawing_revision_id = r.id AND c.is_current) AS semantic_chunk_count,
                   (SELECT COUNT(*)::integer FROM core.source_assets a
                    WHERE a.drawing_revision_id = r.id) AS asset_count
            FROM core.drawing_revisions r
            JOIN core.drawings d ON d.id = r.drawing_id
            WHERE d.drawing_key = %s
            ORDER BY r.revision_sequence DESC, r.created_at DESC
            LIMIT %s
            """,
            (drawing_key, limit),
        )
        if not items:
            raise DrawingNotFoundError(f"No drawing found for drawing_key={drawing_key}")
        return {
            "drawing": {
                "drawing_id": items[0]["drawing_id"] if items else None,
                "drawing_key": drawing_key,
                "title": items[0]["title"] if items else None,
            },
            "items": items,
            "count": len(items),
        }

    def list_assets(
        self,
        *,
        drawing_key: str | None = None,
        revision_id: str | UUID | None = None,
        asset_role: str | None = None,
    ) -> dict[str, Any]:
        """List source and derived files belonging to one revision."""

        revision = self._resolve_scoped_revision(drawing_key=drawing_key, revision_id=revision_id)
        items = self._fetchall(
            """
            SELECT a.id, a.drawing_revision_id, a.asset_role, a.file_name,
                   a.relative_path, a.media_type, a.file_size, a.sha256,
                   a.schema_version, a.metadata, a.created_at
            FROM core.source_assets a
            WHERE a.drawing_revision_id = %s
              AND (%s::text IS NULL OR a.asset_role = %s)
            ORDER BY a.asset_role, a.file_name
            """,
            (revision["revision_id"], asset_role, asset_role),
        )
        return {"drawing": revision, "items": items, "count": len(items)}

    def get_asset(self, asset_id: str | UUID) -> dict[str, Any]:
        """Return one asset metadata record for the safe content endpoint."""

        asset = self._fetchone(
            """
            SELECT a.id, a.drawing_revision_id, a.asset_role, a.file_name,
                   a.relative_path, a.media_type, a.file_size, a.sha256,
                   a.schema_version, a.metadata, d.drawing_key,
                   r.revision_sequence
            FROM core.source_assets a
            JOIN core.drawing_revisions r ON r.id = a.drawing_revision_id
            JOIN core.drawings d ON d.id = r.drawing_id
            WHERE a.id = %s
            """,
            (asset_id,),
        )
        if not asset:
            raise DrawingNotFoundError(f"No source asset found for asset_id={asset_id}")
        return asset

    def list_unified_features(
        self,
        *,
        drawing_key: str | None = None,
        revision_id: str | UUID | None = None,
        feature_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List current Unified Features with compact evidence references."""

        self._validate_limit_offset(limit, offset)
        if status is not None and status not in FUSION_STATUSES:
            raise ValueError(f"status must be one of {sorted(FUSION_STATUSES)}")
        revision = self._resolve_scoped_revision(drawing_key=drawing_key, revision_id=revision_id)
        rows = self._fetchall(
            """
            SELECT f.id, f.external_id, f.feature_type, f.canonical_text,
                   f.canonical_text_raw, f.dimension_kind, f.canonical_value,
                   f.unit, f.cad_position, f.pixel_position, f.projected_position,
                   f.fusion_status, f.fusion_score, f.text_similarity,
                   f.distance_px, f.metadata,
                   (SELECT COUNT(*)::integer FROM fusion.feature_cad_entities x
                    WHERE x.feature_id = f.id) AS cad_evidence_count,
                   (SELECT COUNT(*)::integer FROM fusion.feature_native_annotations x
                    WHERE x.feature_id = f.id) AS native_evidence_count,
                   (SELECT COUNT(*)::integer FROM fusion.feature_ocr_items x
                    WHERE x.feature_id = f.id) AS ocr_evidence_count,
                   (SELECT COUNT(*)::integer FROM fusion.feature_vision_observations x
                    WHERE x.feature_id = f.id) AS vision_evidence_count,
                   (SELECT COUNT(*)::integer FROM fusion.conflicts x
                    WHERE x.feature_id = f.id AND x.status = 'open') AS open_conflict_count,
                   (SELECT COALESCE(jsonb_agg(jsonb_build_object(
                               'cad_id', e.cad_id, 'entity_type', e.entity_type,
                               'role', x.evidence_role)), '[]'::jsonb)
                    FROM fusion.feature_cad_entities x
                    JOIN cad.entities e ON e.id = x.entity_id
                    WHERE x.feature_id = f.id) AS cad_evidence,
                   (SELECT COALESCE(jsonb_agg(jsonb_build_object(
                               'external_id', n.external_id, 'text', n.text_normalized,
                               'role', x.evidence_role)), '[]'::jsonb)
                    FROM fusion.feature_native_annotations x
                    JOIN cad.native_annotations n ON n.id = x.annotation_id
                    WHERE x.feature_id = f.id) AS native_evidence,
                   (SELECT COALESCE(jsonb_agg(jsonb_build_object(
                               'external_id', o.external_id, 'text', o.text_normalized,
                               'polygon_px', o.polygon_px, 'role', x.evidence_role)), '[]'::jsonb)
                    FROM fusion.feature_ocr_items x
                    JOIN ocr.items o ON o.id = x.ocr_item_id
                    WHERE x.feature_id = f.id) AS ocr_evidence
            FROM fusion.features f
            JOIN fusion.runs fr ON fr.id = f.fusion_run_id AND fr.is_current
            WHERE f.drawing_revision_id = %s
              AND (%s::text IS NULL OR f.feature_type = %s)
              AND (%s::text IS NULL OR f.fusion_status = %s)
            ORDER BY f.external_id
            LIMIT %s OFFSET %s
            """,
            (
                revision["revision_id"], feature_type, feature_type,
                status, status, limit, offset,
            ),
        )
        return {
            "drawing": revision,
            "items": rows,
            "count": len(rows),
            "limit": limit,
            "offset": offset,
        }

    def get_viewer_data(
        self,
        *,
        drawing_key: str | None = None,
        revision_id: str | UUID | None = None,
    ) -> dict[str, Any]:
        """Return one revision's complete first-version viewer payload."""

        revision = self._resolve_scoped_revision(drawing_key=drawing_key, revision_id=revision_id)
        assets = self.list_assets(revision_id=revision["revision_id"])["items"]
        layers = self.list_layers(revision_id=revision["revision_id"], limit=500)["items"]
        blocks = self.list_blocks(revision_id=revision["revision_id"], limit=500)["items"]
        entities = self.list_entities(
            revision_id=revision["revision_id"], limit=500, include_raw_properties=False
        )["items"]
        native = self.list_native_annotations(revision_id=revision["revision_id"], limit=500)["items"]
        ocr = self.list_ocr_items(revision_id=revision["revision_id"], limit=500)["items"]
        vision = self.list_vision_observations(revision_id=revision["revision_id"], limit=500)["items"]
        features = self.list_unified_features(revision_id=revision["revision_id"], limit=500)["items"]
        registration = self._fetchone(
            """
            SELECT id, method, cad_to_image_matrix, anchor_count,
                   inlier_anchor_count, rmse_px, pixel_content_bounds, status
            FROM ocr.alignment_runs
            WHERE drawing_revision_id = %s AND is_current
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (revision["revision_id"],),
        ) or {}
        return {
            "drawing": revision,
            "assets": assets,
            "registration": registration,
            "browser": {"layers": layers, "blocks": blocks},
            "cad": {"entities": entities},
            "annotations": {
                "native": native,
                "ocr": ocr,
                "vision": vision,
            },
            "features": features,
            "statistics": {
                "entity_count": len(entities),
                "native_annotation_count": len(native),
                "ocr_item_count": len(ocr),
                "vision_observation_count": len(vision),
                "feature_count": len(features),
            },
        }

    def get_revisions_by_ids(self, revision_ids: Sequence[str | UUID]) -> list[dict[str, Any]]:
        """Resolve a batch of revision IDs for hybrid candidate verification."""

        values = [str(value) for value in revision_ids if value]
        if not values:
            return []
        return self._fetchall(
            """
            SELECT r.id AS revision_id, r.drawing_id, d.drawing_key, d.title,
                   r.revision_code, r.revision_sequence, r.status,
                   r.cad_unit, r.unit_scale, r.schema_version,
                   r.cad_coordinate_system, r.cad_bounds
            FROM core.drawing_revisions AS r
            JOIN core.drawings AS d ON d.id = r.drawing_id
            WHERE r.id = ANY(%s::uuid[])
            ORDER BY d.drawing_key, r.revision_sequence
            """,
            (values,),
        )

    def get_current_features_by_ids(self, feature_ids: Sequence[str | UUID]) -> list[dict[str, Any]]:
        """Load current Unified Features and compact evidence counts in bulk."""

        values = [str(value) for value in feature_ids if value]
        if not values:
            return []
        return self._fetchall(
            """
            SELECT f.id AS feature_id, f.drawing_revision_id AS revision_id,
                   f.external_id, f.feature_type, f.canonical_text,
                   f.canonical_text_raw, f.dimension_kind, f.canonical_value,
                   f.unit, f.cad_position, f.pixel_position, f.projected_position,
                   f.fusion_status, f.fusion_score, f.text_similarity,
                   f.distance_px, f.metadata,
                   d.drawing_key, d.title,
                   (SELECT COUNT(*)::integer
                    FROM fusion.feature_cad_entities x
                    WHERE x.feature_id = f.id) AS cad_evidence_count,
                   (SELECT COUNT(*)::integer
                    FROM fusion.feature_native_annotations x
                    WHERE x.feature_id = f.id) AS native_evidence_count,
                   (SELECT COUNT(*)::integer
                    FROM fusion.feature_ocr_items x
                    WHERE x.feature_id = f.id) AS ocr_evidence_count,
                   (SELECT COUNT(*)::integer
                    FROM fusion.feature_vision_observations x
                    WHERE x.feature_id = f.id) AS vision_evidence_count,
                   (SELECT COUNT(*)::integer
                    FROM fusion.conflicts x
                    WHERE x.feature_id = f.id AND x.status = 'open') AS open_conflict_count
            FROM fusion.features AS f
            JOIN fusion.runs AS fr ON fr.id = f.fusion_run_id AND fr.is_current
            JOIN core.drawing_revisions AS r ON r.id = f.drawing_revision_id
            JOIN core.drawings AS d ON d.id = r.drawing_id
            WHERE f.id = ANY(%s::uuid[])
            """,
            (values,),
        )

    def record_hybrid_search(
        self,
        *,
        query: str,
        query_plan: dict[str, Any],
        scope: dict[str, Any],
        retrieval: dict[str, Any],
        candidates: Sequence[dict[str, Any]],
        warnings: Sequence[str],
    ) -> str:
        """Persist one completed hybrid search and its verification checks."""

        run_id = uuid4()
        status_counts: dict[str, int] = {}
        for candidate in candidates:
            status = str(candidate.get("status", "unknown"))
            status_counts[status] = status_counts.get(status, 0) + 1
        metrics = {
            "status_counts": status_counts,
            "verified_count": status_counts.get("verified", 0),
            "partial_count": status_counts.get("partially_verified", 0),
            "semantic_candidate_count": status_counts.get("semantic_candidate", 0),
            "conflict_count": status_counts.get("conflict", 0),
            "rejected_count": status_counts.get("rejected", 0),
            "returned_count": retrieval.get("returned_count", 0),
        }
        with self.connection.cursor() as cur:
            cur.execute(
                """
                INSERT INTO retrieval.search_runs (
                    id, query, query_plan, scope, semantic_collection, embedding_model,
                    top_k, final_limit, min_score, semantic_hit_count,
                    candidate_revision_count, total_candidate_count, returned_count,
                    duration_ms, score_formula, status, metrics, warnings, finished_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    'completed', %s, %s, now()
                )
                """,
                (
                    run_id,
                    query,
                    Jsonb(_jsonable(query_plan)),
                    Jsonb(_jsonable(scope)),
                    retrieval.get("semantic_collection", ""),
                    retrieval.get("embedding_model", ""),
                    retrieval.get("top_k", 30),
                    retrieval.get("final_limit", 10),
                    retrieval.get("min_score", 0.35),
                    retrieval.get("semantic_hit_count", 0),
                    retrieval.get("candidate_revision_count", 0),
                    retrieval.get("total_candidates", len(candidates)),
                    retrieval.get("returned_count", 0),
                    retrieval.get("duration_ms"),
                    retrieval.get("formula", "0.60*semantic_score + 0.30*exact_score + 0.10*evidence_score"),
                    Jsonb(_jsonable(metrics)),
                    Jsonb(list(warnings)),
                ),
            )
            for rank, candidate in enumerate(candidates, start=1):
                candidate_id = uuid4()
                exact = candidate.get("exact") or {}
                checks = exact.get("constraints") or {}
                dimension = checks.get("dimension") or {}
                entity_types = checks.get("entity_types") or {}
                cur.execute(
                    """
                    INSERT INTO retrieval.candidates (
                        id, search_run_id, rank, revision_id, drawing_key, drawing_title,
                        semantic_score, exact_score, evidence_score, final_score, status,
                        source_types, source_feature_ids, semantic_hits, exact_result, warnings
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        candidate_id,
                        run_id,
                        rank,
                        candidate.get("revision_id"),
                        candidate.get("drawing_key"),
                        candidate.get("drawing_title"),
                        candidate.get("semantic_score", 0.0),
                        candidate.get("exact_score", 0.0),
                        candidate.get("evidence_score", 0.0),
                        candidate.get("final_score", 0.0),
                        candidate.get("status", "semantic_candidate"),
                        list(candidate.get("source_types", [])),
                        Jsonb(_jsonable(candidate.get("source_feature_ids", []))),
                        Jsonb(_jsonable(candidate.get("semantic_hits", []))),
                        Jsonb(_jsonable(exact)),
                        Jsonb(_jsonable(candidate.get("warnings", []))),
                    ),
                )

                if dimension.get("required"):
                    cur.execute(
                        """
                        INSERT INTO retrieval.constraint_checks (
                            id, search_run_id, candidate_id, constraint_type,
                            constraint_name, expected, actual, passed
                        ) VALUES (%s, %s, %s, 'dimension', 'dimension', %s, %s, %s)
                        """,
                        (
                            uuid4(),
                            run_id,
                            candidate_id,
                            Jsonb({
                                "kind": dimension.get("kind"),
                                "value": dimension.get("value"),
                                "tolerance": dimension.get("tolerance"),
                            }),
                            Jsonb({
                                "matched_count": dimension.get("matched_count", 0),
                                "items": dimension.get("items", []),
                            }),
                            bool(dimension.get("matched_count", 0)),
                        ),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO retrieval.constraint_checks (
                            id, search_run_id, candidate_id, constraint_type,
                            constraint_name, expected, actual, passed
                        ) VALUES (%s, %s, %s, 'provenance', 'source_exists', %s, %s, %s)
                        """,
                        (
                            uuid4(),
                            run_id,
                            candidate_id,
                            Jsonb({"source_exists": True}),
                            Jsonb({"source_exists": bool(exact.get("source_exists"))}),
                            bool(exact.get("source_exists")),
                        ),
                    )

                passed_by_type = entity_types.get("passed") or {}
                counts_by_type = entity_types.get("counts") or {}
                for entity_type in entity_types.get("required") or []:
                    cur.execute(
                        """
                        INSERT INTO retrieval.constraint_checks (
                            id, search_run_id, candidate_id, constraint_type,
                            constraint_name, expected, actual, passed
                        ) VALUES (%s, %s, %s, 'entity_type', %s, %s, %s, %s)
                        """,
                        (
                            uuid4(),
                            run_id,
                            candidate_id,
                            entity_type,
                            Jsonb({"entity_type": entity_type, "minimum_count": 1}),
                            Jsonb({"entity_type": entity_type, "count": counts_by_type.get(entity_type, 0)}),
                            bool(passed_by_type.get(entity_type, False)),
                        ),
                    )
        return str(run_id)

    def record_failed_hybrid_search(
        self,
        *,
        query: str,
        scope: dict[str, Any],
        semantic_collection: str,
        embedding_model: str,
        top_k: int,
        final_limit: int,
        min_score: float,
        error_message: str,
        query_plan: dict[str, Any] | None = None,
    ) -> str:
        """Persist a failed hybrid search when planning or semantic recall fails."""

        run_id = uuid4()
        with self.connection.cursor() as cur:
            cur.execute(
                """
                INSERT INTO retrieval.search_runs (
                    id, query, query_plan, scope, semantic_collection, embedding_model,
                    top_k, final_limit, min_score, score_formula, status, metrics,
                    warnings, error_message, finished_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    'failed', %s, '[]'::jsonb, %s, now()
                )
                """,
                (
                    run_id,
                    query,
                    Jsonb(_jsonable(query_plan or {})),
                    Jsonb(_jsonable(scope)),
                    semantic_collection,
                    embedding_model,
                    top_k,
                    final_limit,
                    min_score,
                    "0.60*semantic_score + 0.30*exact_score + 0.10*evidence_score",
                    Jsonb({"error": error_message}),
                    error_message[:4000],
                ),
            )
        return str(run_id)

    def get_hybrid_search_audit(self, search_run_id: str | UUID) -> dict[str, Any]:
        """Return one persisted hybrid search with candidates and checks."""

        run = self._fetchone(
            "SELECT * FROM retrieval.search_runs WHERE id = %s",
            (search_run_id,),
        )
        if not run:
            raise RetrievalRunNotFoundError(f"No retrieval search run found for id={search_run_id}")
        candidates = self._fetchall(
            """
            SELECT id, rank, revision_id, drawing_key, drawing_title,
                   semantic_score, exact_score, evidence_score, final_score, status,
                   source_types, source_feature_ids, semantic_hits, exact_result, warnings, created_at
            FROM retrieval.candidates
            WHERE search_run_id = %s
            ORDER BY rank NULLS LAST, id
            """,
            (search_run_id,),
        )
        checks = self._fetchall(
            """
            SELECT id, candidate_id, constraint_type, constraint_name, expected, actual, passed, created_at
            FROM retrieval.constraint_checks
            WHERE search_run_id = %s
            ORDER BY candidate_id, constraint_type, constraint_name
            """,
            (search_run_id,),
        )
        checks_by_candidate: dict[str, list[dict[str, Any]]] = {}
        for check in checks:
            checks_by_candidate.setdefault(str(check["candidate_id"]), []).append(check)
        for candidate in candidates:
            candidate["constraint_checks"] = checks_by_candidate.get(str(candidate["id"]), [])
        return {"search_run": run, "candidates": candidates, "constraint_checks": checks}

    def count_entities(
        self,
        *,
        drawing_key: str | None = None,
        revision_id: str | UUID | None = None,
        entity_type: str | None = None,
    ) -> dict[str, Any]:
        """Count entities, optionally restricted to one type."""

        revision = self.resolve_revision(drawing_key=drawing_key, revision_id=revision_id)
        normalized_type = entity_type.upper() if entity_type else None
        rows = self._fetchall(
            """
            SELECT entity_type, COUNT(*)::integer AS count
            FROM cad.entities
            WHERE drawing_revision_id = %s
              AND (%s::text IS NULL OR entity_type = %s)
            GROUP BY entity_type
            ORDER BY entity_type
            """,
            (revision["revision_id"], normalized_type, normalized_type),
        )
        return {
            "drawing": {
                "drawing_key": revision["drawing_key"],
                "revision_id": revision["revision_id"],
                "revision_sequence": revision["revision_sequence"],
            },
            "total": sum(row["count"] for row in rows),
            "by_type": {row["entity_type"]: row["count"] for row in rows},
            "entity_type_filter": normalized_type,
        }

    def list_entities(
        self,
        *,
        drawing_key: str | None = None,
        revision_id: str | UUID | None = None,
        entity_type: str | None = None,
        layer_name: str | None = None,
        cad_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
        include_raw_properties: bool = False,
    ) -> dict[str, Any]:
        """List typed CAD entities with a compact, JSON-safe detail object."""

        self._validate_limit_offset(limit, offset)
        revision = self.resolve_revision(drawing_key=drawing_key, revision_id=revision_id)
        normalized_type = entity_type.upper() if entity_type else None
        rows = self._fetchall(
            """
            SELECT e.id AS entity_id, e.cad_id, e.entity_type, e.source_index,
                   e.handle_json AS handle, l.name AS layer,
                   e.raw_properties,
                   ln.start_x, ln.start_y, ln.start_z, ln.end_x, ln.end_y, ln.end_z,
                   c.center_x AS circle_center_x, c.center_y AS circle_center_y,
                   c.center_z AS circle_center_z, c.radius AS circle_radius,
                   a.center_x AS arc_center_x, a.center_y AS arc_center_y,
                   a.center_z AS arc_center_z, a.radius AS arc_radius,
                   a.start_angle, a.end_angle,
                   p.is_closed, p.flags AS polyline_flags, p.vertices,
                   t.text_kind, t.text_raw, t.position_x AS text_position_x,
                   t.position_y AS text_position_y, t.position_z AS text_position_z,
                   t.text_height,
                   dm.dimension_type, dm.text_raw AS dimension_text_raw,
                   dm.measurement_raw, dm.text_position_x, dm.text_position_y,
                   dm.text_position_z, dm.dimension_block_cad_id,
                   i.block_name, i.block_cad_id, i.position_x AS insert_position_x,
                   i.position_y AS insert_position_y, i.position_z AS insert_position_z,
                   i.rotation, i.scale_x, i.scale_y, i.scale_z
            FROM cad.entities AS e
            LEFT JOIN cad.layers AS l ON l.id = e.layer_id
            LEFT JOIN cad.lines AS ln ON ln.entity_id = e.id
            LEFT JOIN cad.circles AS c ON c.entity_id = e.id
            LEFT JOIN cad.arcs AS a ON a.entity_id = e.id
            LEFT JOIN cad.polylines AS p ON p.entity_id = e.id
            LEFT JOIN cad.texts AS t ON t.entity_id = e.id
            LEFT JOIN cad.dimensions AS dm ON dm.entity_id = e.id
            LEFT JOIN cad.inserts AS i ON i.entity_id = e.id
            WHERE e.drawing_revision_id = %s
              AND (%s::text IS NULL OR e.entity_type = %s)
              AND (%s::text IS NULL OR l.name = %s)
              AND (%s::text IS NULL OR e.cad_id = %s)
            ORDER BY e.source_index NULLS LAST, e.cad_id
            LIMIT %s OFFSET %s
            """,
            (
                revision["revision_id"], normalized_type, normalized_type,
                layer_name, layer_name, cad_id, cad_id, limit, offset,
            ),
        )
        items = [self._entity_item(row, include_raw_properties) for row in rows]
        return {
            "drawing": {
                "drawing_key": revision["drawing_key"],
                "revision_id": revision["revision_id"],
                "revision_sequence": revision["revision_sequence"],
            },
            "items": items,
            "count": len(items),
            "limit": limit,
            "offset": offset,
        }

    @staticmethod
    def _entity_item(row: dict[str, Any], include_raw_properties: bool) -> dict[str, Any]:
        item: dict[str, Any] = {
            "id": row["entity_id"],
            "cad_id": row["cad_id"],
            "type": row["entity_type"],
            "layer": row["layer"],
            "source_index": row["source_index"],
            "handle": row["handle"],
        }
        entity_type = row["entity_type"]
        if entity_type == "LINE":
            item["geometry"] = {
                "start": [row["start_x"], row["start_y"], row["start_z"]],
                "end": [row["end_x"], row["end_y"], row["end_z"]],
            }
        elif entity_type == "CIRCLE":
            item["geometry"] = {
                "center": [row["circle_center_x"], row["circle_center_y"], row["circle_center_z"]],
                "radius": row["circle_radius"],
            }
        elif entity_type == "ARC":
            item["geometry"] = {
                "center": [row["arc_center_x"], row["arc_center_y"], row["arc_center_z"]],
                "radius": row["arc_radius"],
                "start_angle": row["start_angle"],
                "end_angle": row["end_angle"],
            }
        elif entity_type in {"POLYLINE", "LWPOLYLINE"}:
            item["geometry"] = {
                "closed": row["is_closed"],
                "flags": row["polyline_flags"],
                "vertices": row["vertices"] or [],
            }
        elif entity_type in {"TEXT", "MTEXT"}:
            item["text"] = {
                "kind": row["text_kind"],
                "raw": row["text_raw"],
                "position": [row["text_position_x"], row["text_position_y"], row["text_position_z"]],
                "height": row["text_height"],
            }
        elif entity_type == "DIMENSION":
            item["dimension"] = {
                "type": row["dimension_type"],
                "text_raw": row["dimension_text_raw"],
                "measurement_raw": row["measurement_raw"],
                "text_position": [row["text_position_x"], row["text_position_y"], row["text_position_z"]],
                "dimension_block_cad_id": row["dimension_block_cad_id"],
            }
        elif entity_type == "INSERT":
            item["insert"] = {
                "block_name": row["block_name"],
                "block_cad_id": row["block_cad_id"],
                "position": [row["insert_position_x"], row["insert_position_y"], row["insert_position_z"]],
                "rotation": row["rotation"],
                "scale": [row["scale_x"], row["scale_y"], row["scale_z"]],
            }
        if include_raw_properties:
            item["raw_properties"] = row["raw_properties"] or {}
        return item

    def list_layers(
        self,
        *,
        drawing_key: str | None = None,
        revision_id: str | UUID | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """List layers and the number of entities assigned to each layer."""

        self._validate_limit_offset(limit, 0)
        revision = self.resolve_revision(drawing_key=drawing_key, revision_id=revision_id)
        rows = self._fetchall(
            """
            SELECT l.id, l.cad_id, l.name, l.handle_json AS handle, l.source_index,
                   COUNT(e.id)::integer AS entity_count
            FROM cad.layers AS l
            LEFT JOIN cad.entities AS e ON e.layer_id = l.id
            WHERE l.drawing_revision_id = %s
            GROUP BY l.id
            ORDER BY l.name, l.cad_id
            LIMIT %s
            """,
            (revision["revision_id"], limit),
        )
        return {"drawing": revision, "items": rows, "count": len(rows)}

    def list_blocks(
        self,
        *,
        drawing_key: str | None = None,
        revision_id: str | UUID | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """List block definitions and their member counts."""

        self._validate_limit_offset(limit, 0)
        revision = self.resolve_revision(drawing_key=drawing_key, revision_id=revision_id)
        rows = self._fetchall(
            """
            SELECT b.id, b.cad_id, b.name, b.handle_json AS handle, b.source_index,
                   b.base_point_xyz, b.is_anonymous, b.block_entity_cad_id,
                   b.endblk_entity_cad_id, b.layout_cad_id,
                   COUNT(bm.entity_id)::integer AS member_count
            FROM cad.blocks AS b
            LEFT JOIN cad.block_memberships AS bm ON bm.block_id = b.id
            WHERE b.drawing_revision_id = %s
            GROUP BY b.id
            ORDER BY b.name, b.cad_id
            LIMIT %s
            """,
            (revision["revision_id"], limit),
        )
        return {"drawing": revision, "items": rows, "count": len(rows)}

    def list_native_annotations(
        self,
        *,
        drawing_key: str | None = None,
        revision_id: str | UUID | None = None,
        text: str | None = None,
        parsed_kind: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Find normalized native CAD annotations and source entity IDs."""

        self._validate_limit_offset(limit, offset)
        revision = self.resolve_revision(drawing_key=drawing_key, revision_id=revision_id)
        pattern = f"%{text.strip()}%" if text and text.strip() else None
        rows = self._fetchall(
            """
            SELECT na.id, na.external_id, na.entity_type, na.text_raw,
                   na.display_text_raw, na.text_clean, na.text_normalized,
                   na.native_dimension_type, na.native_measurement,
                   na.parsed_kind, na.parsed_value, na.parsed_unit,
                   na.position_x, na.position_y, na.position_z,
                   na.display_entity_cad_id, na.dimension_block_cad_id,
                   na.normalizer_version,
                   COALESCE(
                       jsonb_agg(DISTINCT jsonb_build_object('cad_id', e.cad_id, 'role', nae.role))
                       FILTER (WHERE e.id IS NOT NULL), '[]'::jsonb
                   ) AS source_entities
            FROM cad.native_annotations AS na
            LEFT JOIN cad.native_annotation_entities AS nae ON nae.annotation_id = na.id
            LEFT JOIN cad.entities AS e ON e.id = nae.entity_id
            WHERE na.drawing_revision_id = %s
              AND (%s::text IS NULL OR na.text_normalized ILIKE %s OR na.text_clean ILIKE %s)
              AND (%s::text IS NULL OR na.parsed_kind = %s)
            GROUP BY na.id
            ORDER BY na.position_y NULLS LAST, na.position_x NULLS LAST, na.external_id
            LIMIT %s OFFSET %s
            """,
            (
                revision["revision_id"], pattern, pattern, pattern,
                parsed_kind, parsed_kind, limit, offset,
            ),
        )
        return {"drawing": revision, "items": rows, "count": len(rows), "limit": limit, "offset": offset}

    def list_ocr_items(
        self,
        *,
        drawing_key: str | None = None,
        revision_id: str | UUID | None = None,
        text: str | None = None,
        min_confidence: float | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List OCR observations from the current OCR run."""

        self._validate_limit_offset(limit, offset)
        if min_confidence is not None and not 0 <= min_confidence <= 1:
            raise ValueError("min_confidence must be between 0 and 1")
        revision = self.resolve_revision(drawing_key=drawing_key, revision_id=revision_id)
        pattern = f"%{text.strip()}%" if text and text.strip() else None
        rows = self._fetchall(
            """
            SELECT oi.id, oi.external_id, oi.text_raw, oi.text_normalized,
                   oi.confidence, oi.parsed_kind, oi.parsed_value, oi.parsed_unit,
                   oi.polygon_px, oi.center_px, oi.is_merged, oi.merge_metadata,
                   oi.raw_payload, r.id AS ocr_run_id, r.engine_name,
                   r.engine_version, r.coordinate_system, r.image_width, r.image_height
            FROM ocr.items AS oi
            JOIN ocr.runs AS r ON r.id = oi.ocr_run_id
            WHERE r.drawing_revision_id = %s AND r.is_current
              AND (%s::text IS NULL OR oi.text_normalized ILIKE %s OR oi.text_raw ILIKE %s)
              AND (%s::text IS NULL OR oi.confidence >= %s)
            ORDER BY oi.center_px->1 NULLS LAST, oi.center_px->0 NULLS LAST, oi.external_id
            LIMIT %s OFFSET %s
            """,
            (
                revision["revision_id"], pattern, pattern, pattern,
                min_confidence, min_confidence, limit, offset,
            ),
        )
        return {"drawing": revision, "items": rows, "count": len(rows), "limit": limit, "offset": offset}

    def find_dimensions(
        self,
        *,
        drawing_key: str | None = None,
        revision_id: str | UUID | None = None,
        dimension_kind: str | None = None,
        value: float | None = None,
        tolerance: float = 0.01,
        text: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Find CAD dimensions while retaining native/fusion evidence.

        ``value`` is compared with the normalized engineering value. For
        angular dimensions this is degrees, while ``measurement_raw`` remains
        the source CAD value in radians. Units are never inferred here.
        """

        self._validate_limit_offset(limit, offset)
        if dimension_kind is not None:
            dimension_kind = dimension_kind.lower()
            if dimension_kind not in DIMENSION_KINDS:
                raise ValueError(f"dimension_kind must be one of {sorted(DIMENSION_KINDS)}")
        if value is not None and value < 0:
            raise ValueError("value must be non-negative")
        if tolerance < 0:
            raise ValueError("tolerance must be non-negative")
        revision = self.resolve_revision(drawing_key=drawing_key, revision_id=revision_id)
        pattern = f"%{text.strip()}%" if text and text.strip() else None
        rows = self._fetchall(
            """
            WITH current_features AS (
                SELECT f.id, f.external_id, f.feature_type, f.canonical_text,
                       f.canonical_text_raw, f.dimension_kind, f.canonical_value,
                       f.unit, f.fusion_status, f.fusion_score,
                       f.text_similarity, f.distance_px, f.cad_position,
                       f.pixel_position, f.metadata,
                       fce.entity_id
                FROM fusion.features AS f
                JOIN fusion.runs AS fr ON fr.id = f.fusion_run_id AND fr.is_current
                JOIN fusion.feature_cad_entities AS fce ON fce.feature_id = f.id
                WHERE f.drawing_revision_id = %s
            ), native_for_dimension AS (
                SELECT DISTINCT ON (nae.entity_id)
                       nae.entity_id, na.id AS native_id, na.external_id AS native_external_id,
                       na.text_raw AS native_text_raw, na.text_normalized,
                       na.parsed_kind, na.parsed_value, na.parsed_unit,
                       na.native_measurement, na.native_dimension_type,
                       na.position_x AS native_position_x, na.position_y AS native_position_y,
                       na.position_z AS native_position_z
                FROM cad.native_annotation_entities AS nae
                JOIN cad.native_annotations AS na ON na.id = nae.annotation_id
                WHERE na.drawing_revision_id = %s
                ORDER BY nae.entity_id,
                         CASE WHEN nae.role = 'source_entity' THEN 0 ELSE 1 END,
                         na.id
            )
            SELECT e.id AS entity_id, e.cad_id, e.entity_type, l.name AS layer,
                   dm.dimension_type, dm.text_raw AS dimension_text_raw,
                   dm.measurement_raw, dm.text_position_x, dm.text_position_y,
                   dm.text_position_z, dm.dimension_block_cad_id,
                   nf.native_id, nf.native_external_id, nf.native_text_raw,
                   nf.text_normalized AS native_text_normalized,
                   nf.parsed_kind AS native_parsed_kind,
                   nf.parsed_value AS native_parsed_value,
                   nf.parsed_unit AS native_parsed_unit,
                   nf.native_measurement, nf.native_dimension_type,
                   nf.native_position_x, nf.native_position_y, nf.native_position_z,
                   cf.id AS feature_id, cf.external_id AS feature_external_id,
                   cf.feature_type, cf.canonical_text, cf.canonical_text_raw,
                   cf.dimension_kind AS feature_dimension_kind,
                   cf.canonical_value, cf.unit AS feature_unit,
                   cf.fusion_status, cf.fusion_score, cf.text_similarity,
                   cf.distance_px, cf.cad_position, cf.pixel_position, cf.metadata
            FROM cad.entities AS e
            JOIN cad.dimensions AS dm ON dm.entity_id = e.id
            LEFT JOIN cad.layers AS l ON l.id = e.layer_id
            LEFT JOIN native_for_dimension AS nf ON nf.entity_id = e.id
            LEFT JOIN current_features AS cf ON cf.entity_id = e.id
            WHERE e.drawing_revision_id = %s
              AND (
                    %s::text IS NULL
                    OR COALESCE(nf.parsed_kind,
                         CASE dm.dimension_type
                           WHEN 'DIMENSION_RADIUS' THEN 'radius'
                           WHEN 'DIMENSION_DIAMETER' THEN 'diameter'
                           WHEN 'DIMENSION_ANG2LN' THEN 'angle'
                           ELSE 'linear'
                         END) = %s::text
                  )
              AND (
                    %s::double precision IS NULL
                    OR ABS(
                      COALESCE(nf.parsed_value,
                        CASE WHEN dm.dimension_type = 'DIMENSION_ANG2LN'
                             THEN degrees(dm.measurement_raw)
                             ELSE dm.measurement_raw END) - %s
                    ) <= %s
                  )
              AND (
                    %s::text IS NULL
                    OR COALESCE(nf.text_normalized, dm.text_raw) ILIKE %s
                  )
            ORDER BY COALESCE(nf.parsed_value,
                       CASE WHEN dm.dimension_type = 'DIMENSION_ANG2LN'
                            THEN degrees(dm.measurement_raw)
                            ELSE dm.measurement_raw END), e.cad_id
            LIMIT %s OFFSET %s
            """,
            (
                revision["revision_id"], revision["revision_id"], revision["revision_id"],
                dimension_kind, dimension_kind, value, value, tolerance,
                pattern, pattern, limit, offset,
            ),
        )
        items = [self._dimension_item(row) for row in rows]
        return {
            "drawing": revision,
            "items": items,
            "count": len(items),
            "limit": limit,
            "offset": offset,
            "query": {
                "dimension_kind": dimension_kind,
                "value": value,
                "tolerance": tolerance,
                "text": text,
            },
        }

    @staticmethod
    def _dimension_item(row: dict[str, Any]) -> dict[str, Any]:
        inferred_kind = row["native_parsed_kind"]
        if inferred_kind is None:
            inferred_kind = {
                "DIMENSION_RADIUS": "radius",
                "DIMENSION_DIAMETER": "diameter",
                "DIMENSION_ANG2LN": "angle",
            }.get(row["dimension_type"], "linear")
        normalized_value = row["native_parsed_value"]
        if normalized_value is None and row["measurement_raw"] is not None:
            normalized_value = (
                row["measurement_raw"] * 180.0 / 3.141592653589793
                if inferred_kind == "angle"
                else row["measurement_raw"]
            )
        return {
            "cad_entity": {
                "id": row["entity_id"],
                "cad_id": row["cad_id"],
                "type": row["entity_type"],
                "layer": row["layer"],
            },
            "dimension": {
                "kind": inferred_kind,
                "text_raw": row["dimension_text_raw"],
                "measurement": normalized_value,
                "unit": row["native_parsed_unit"],
                "measurement_raw": row["measurement_raw"],
                "text_position": [row["text_position_x"], row["text_position_y"], row["text_position_z"]],
                "dimension_type": row["dimension_type"],
                "dimension_block_cad_id": row["dimension_block_cad_id"],
            },
            "native_annotation": {
                "id": row["native_id"],
                "external_id": row["native_external_id"],
                "text_raw": row["native_text_raw"],
                "text_normalized": row["native_text_normalized"],
                "parsed_kind": row["native_parsed_kind"],
                "parsed_value": row["native_parsed_value"],
                "parsed_unit": row["native_parsed_unit"],
                "native_measurement": row["native_measurement"],
                "position": [row["native_position_x"], row["native_position_y"], row["native_position_z"]],
            } if row["native_id"] is not None else None,
            "feature": {
                "id": row["feature_id"],
                "external_id": row["feature_external_id"],
                "feature_type": row["feature_type"],
                "canonical_text": row["canonical_text"],
                "canonical_value": row["canonical_value"],
                "dimension_kind": row["feature_dimension_kind"],
                "unit": row["feature_unit"],
                "fusion_status": row["fusion_status"],
                "fusion_score": row["fusion_score"],
                "text_similarity": row["text_similarity"],
                "distance_px": row["distance_px"],
            } if row["feature_id"] is not None else None,
        }

    def list_conflicts(
        self,
        *,
        drawing_key: str | None = None,
        revision_id: str | UUID | None = None,
        status: str | None = "open",
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List fusion conflicts with their feature and source values."""

        self._validate_limit_offset(limit, offset)
        if status is not None and status not in {"open", "resolved", "ignored"}:
            raise ValueError("status must be open, resolved, ignored, or null")
        revision = self.resolve_revision(drawing_key=drawing_key, revision_id=revision_id)
        rows = self._fetchall(
            """
            SELECT c.id, c.conflict_type, c.status, c.cad_value, c.ocr_value,
                   c.resolution, c.reviewed_by, c.reviewed_at, c.created_at,
                   f.id AS feature_id, f.external_id AS feature_external_id,
                   f.feature_type, f.canonical_text, f.canonical_value,
                   f.dimension_kind, f.fusion_status, f.fusion_score
            FROM fusion.conflicts AS c
            JOIN fusion.features AS f ON f.id = c.feature_id
            WHERE c.drawing_revision_id = %s
              AND (%s::text IS NULL OR c.status = %s)
            ORDER BY c.created_at, c.id
            LIMIT %s OFFSET %s
            """,
            (revision["revision_id"], status, status, limit, offset),
        )
        return {"drawing": revision, "items": rows, "count": len(rows), "limit": limit, "offset": offset}

    def get_feature_evidence(self, feature_id: str | UUID) -> dict[str, Any]:
        """Return one Unified Feature and all linked CAD/native/OCR evidence."""

        feature = self._fetchone(
            """
            SELECT f.id, f.fusion_run_id, f.drawing_revision_id, f.external_id,
                   f.feature_type, f.canonical_text, f.canonical_text_raw,
                   f.dimension_kind, f.canonical_value, f.unit, f.cad_position,
                   f.pixel_position, f.projected_position, f.fusion_status,
                   f.fusion_score, f.text_similarity, f.distance_px, f.metadata,
                   d.drawing_key, d.title, r.revision_sequence
            FROM fusion.features AS f
            JOIN core.drawing_revisions AS r ON r.id = f.drawing_revision_id
            JOIN core.drawings AS d ON d.id = r.drawing_id
            WHERE f.id = %s
            """,
            (feature_id,),
        )
        if not feature:
            raise DrawingNotFoundError(f"No Unified Feature found for feature_id={feature_id}")
        cad_entities = self._fetchall(
            """
            SELECT e.id, e.cad_id, e.entity_type, l.name AS layer,
                   fce.evidence_role, fce.confidence
            FROM fusion.feature_cad_entities AS fce
            JOIN cad.entities AS e ON e.id = fce.entity_id
            LEFT JOIN cad.layers AS l ON l.id = e.layer_id
            WHERE fce.feature_id = %s
            ORDER BY e.cad_id
            """,
            (feature_id,),
        )
        native = self._fetchall(
            """
            SELECT na.id, na.external_id, na.entity_type, na.text_raw,
                   na.text_normalized, na.parsed_kind, na.parsed_value,
                   na.parsed_unit, na.position_x, na.position_y, na.position_z,
                   fna.evidence_role, fna.confidence
            FROM fusion.feature_native_annotations AS fna
            JOIN cad.native_annotations AS na ON na.id = fna.annotation_id
            WHERE fna.feature_id = %s
            ORDER BY na.external_id
            """,
            (feature_id,),
        )
        ocr = self._fetchall(
            """
            SELECT oi.id, oi.external_id, oi.text_raw, oi.text_normalized,
                   oi.confidence, oi.parsed_kind, oi.parsed_value, oi.parsed_unit,
                   oi.polygon_px, oi.center_px, foi.evidence_role, foi.confidence AS evidence_confidence
            FROM fusion.feature_ocr_items AS foi
            JOIN ocr.items AS oi ON oi.id = foi.ocr_item_id
            WHERE foi.feature_id = %s
            ORDER BY oi.external_id
            """,
            (feature_id,),
        )
        conflicts = self._fetchall(
            """
            SELECT id, conflict_type, status, cad_value, ocr_value, resolution
            FROM fusion.conflicts
            WHERE feature_id = %s
            ORDER BY created_at, id
            """,
            (feature_id,),
        )
        vision = self._fetchall(
            """
            SELECT o.id, o.external_id, o.observation_type, o.description,
                   o.region_px, o.region_normalized, o.candidate_feature,
                   o.model_confidence, o.status, o.uncertainty,
                   fvo.evidence_role, fvo.confidence AS evidence_confidence
            FROM fusion.feature_vision_observations AS fvo
            JOIN vision.observations AS o ON o.id = fvo.observation_id
            WHERE fvo.feature_id = %s
            ORDER BY o.external_id
            """,
            (feature_id,),
        )
        return {
            "feature": feature,
            "evidence": {
                "cad_entities": cad_entities,
                "native_annotations": native,
                "ocr_items": ocr,
                "vision_observations": vision,
            },
            "conflicts": conflicts,
        }

    def compare_cad_ocr(
        self,
        *,
        drawing_key: str | None = None,
        revision_id: str | UUID | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List current Unified Features as the CAD/OCR comparison view."""

        self._validate_limit_offset(limit, offset)
        if status is not None and status not in FUSION_STATUSES:
            raise ValueError(f"status must be one of {sorted(FUSION_STATUSES)}")
        revision = self.resolve_revision(drawing_key=drawing_key, revision_id=revision_id)
        rows = self._fetchall(
            """
            SELECT f.id, f.external_id, f.feature_type, f.canonical_text,
                   f.canonical_text_raw, f.dimension_kind, f.canonical_value,
                   f.unit, f.cad_position, f.pixel_position, f.fusion_status,
                   f.fusion_score, f.text_similarity, f.distance_px,
                   (SELECT COUNT(*)::integer FROM fusion.feature_cad_entities x WHERE x.feature_id = f.id) AS cad_evidence_count,
                   (SELECT COUNT(*)::integer FROM fusion.feature_native_annotations x WHERE x.feature_id = f.id) AS native_evidence_count,
                   (SELECT COUNT(*)::integer FROM fusion.feature_ocr_items x WHERE x.feature_id = f.id) AS ocr_evidence_count,
                   (SELECT COUNT(*)::integer FROM fusion.conflicts x WHERE x.feature_id = f.id AND x.status = 'open') AS open_conflict_count
            FROM fusion.features AS f
            JOIN fusion.runs AS fr ON fr.id = f.fusion_run_id AND fr.is_current
            WHERE f.drawing_revision_id = %s
              AND (%s::text IS NULL OR f.fusion_status = %s)
            ORDER BY f.external_id
            LIMIT %s OFFSET %s
            """,
            (revision["revision_id"], status, status, limit, offset),
        )
        return {"drawing": revision, "items": rows, "count": len(rows), "limit": limit, "offset": offset}

    def list_vision_observations(
        self,
        *,
        drawing_key: str | None = None,
        revision_id: str | UUID | None = None,
        observation_type: str | None = None,
        status: str | None = None,
        min_confidence: float | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List current model-generated visual observations for one revision."""

        self._validate_limit_offset(limit, offset)
        if min_confidence is not None and not 0 <= min_confidence <= 1:
            raise ValueError("min_confidence must be between 0 and 1")
        allowed_statuses = {"candidate", "confirmed", "conflict", "ambiguous", "vision_only"}
        if status is not None and status not in allowed_statuses:
            raise ValueError(f"status must be one of {sorted(allowed_statuses)}")
        revision = self.resolve_revision(drawing_key=drawing_key, revision_id=revision_id)
        rows = self._fetchall(
            """
            SELECT o.id, o.external_id, o.observation_type, o.description,
                   o.region_px, o.region_normalized, o.candidate_feature,
                   o.model_confidence, o.status, o.uncertainty, o.properties,
                   r.id AS vision_run_id, r.model_name, r.prompt_version,
                   r.detail_level, r.image_sha256
            FROM vision.observations AS o
            JOIN vision.runs AS r ON r.id = o.vision_run_id AND r.is_current
            WHERE r.drawing_revision_id = %s
              AND (%s::text IS NULL OR o.observation_type = %s)
              AND (%s::text IS NULL OR o.status = %s)
              AND (%s::double precision IS NULL OR o.model_confidence >= %s)
            ORDER BY o.external_id
            LIMIT %s OFFSET %s
            """,
            (
                revision["revision_id"], observation_type, observation_type,
                status, status, min_confidence, min_confidence, limit, offset,
            ),
        )
        return {
            "drawing": revision,
            "items": rows,
            "count": len(rows),
            "limit": limit,
            "offset": offset,
        }
