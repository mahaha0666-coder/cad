#!/usr/bin/env python3
"""Import the current CAD demo artifacts into PostgreSQL.

The importer intentionally keeps raw DWG/PNG/JSON files on disk. PostgreSQL
stores their metadata and the filtered/derived records needed for querying.
"""

from __future__ import annotations

import argparse
import mimetypes
import os
import sys
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from common import (  # noqa: E402
    as_json,
    find_dataset_file,
    flatten_matrix,
    load_json,
    new_id,
    sha256_file,
    sha256_text,
    slugify,
    xy,
    xyz,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=os.getenv("DATABASE_URL"), help="PostgreSQL DSN")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--dataset", type=Path, default=Path("dataset/AutoCAD_2D_Drawings"))
    parser.add_argument("--demo-dir", type=Path, default=Path("demo"))
    parser.add_argument("--migrate", action="store_true", help="Apply db/migrations before importing")
    parser.add_argument("--limit", type=int, help="Import at most N drawings")
    return parser.parse_args()


def resolve_path(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def relative_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def apply_migrations(conn: psycopg.Connection[Any], root: Path) -> None:
    migration_dir = root / "db" / "migrations"
    with conn.cursor() as cur:
        for migration in sorted(migration_dir.glob("*.sql")):
            cur.execute(migration.read_text(encoding="utf-8"))
    conn.commit()


def get_or_insert_drawing(cur: psycopg.Cursor[Any], drawing_key: str, title: str) -> Any:
    cur.execute("SELECT id FROM core.drawings WHERE drawing_key = %s", (drawing_key,))
    row = cur.fetchone()
    if row:
        return row[0]

    drawing_id = new_id()
    cur.execute(
        """
        INSERT INTO core.drawings (id, drawing_key, title, drawing_category)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        (drawing_id, drawing_key, title, "mechanical_2d"),
    )
    return cur.fetchone()[0]


def get_or_insert_revision(
    cur: psycopg.Cursor[Any], drawing_id: Any, structured: dict[str, Any]
) -> Any:
    cur.execute(
        """
        SELECT id FROM core.drawing_revisions
        WHERE drawing_id = %s AND revision_sequence = 1
        """,
        (drawing_id,),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    revision_id = new_id()
    drawing = structured.get("drawing", {})
    cur.execute(
        """
        INSERT INTO core.drawing_revisions (
            id, drawing_id, revision_sequence, status, schema_version, cad_bounds
        )
        VALUES (%s, %s, 1, 'imported', %s, %s)
        RETURNING id
        """,
        (
            revision_id,
            drawing_id,
            structured.get("schema_version"),
            as_json(structured.get("cad_bounds", {})),
        ),
    )
    return cur.fetchone()[0]


def upsert_asset(
    cur: psycopg.Cursor[Any],
    root: Path,
    revision_id: Any,
    role: str,
    path: Path | None,
    schema_version: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Any | None:
    if path is None or not path.exists():
        return None

    digest = sha256_file(path)
    cur.execute(
        """
        INSERT INTO core.source_assets (
            id, drawing_revision_id, asset_role, relative_path, file_name,
            media_type, file_size, sha256, schema_version, metadata
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (drawing_revision_id, asset_role, sha256)
        DO UPDATE SET relative_path = EXCLUDED.relative_path
        RETURNING id
        """,
        (
            new_id(),
            revision_id,
            role,
            relative_path(root, path),
            path.name,
            mimetypes.guess_type(path.name)[0],
            path.stat().st_size,
            digest,
            schema_version,
            as_json(metadata or {}),
        ),
    )
    return cur.fetchone()[0]


def upsert_processing_run(
    cur: psycopg.Cursor[Any],
    revision_id: Any,
    stage: str,
    tool_name: str,
    tool_version: str | None,
    fingerprint: str,
    config: dict[str, Any],
    statistics: dict[str, Any],
) -> Any:
    cur.execute(
        """
        UPDATE core.processing_runs
        SET is_current = false
        WHERE drawing_revision_id = %s AND stage = %s AND run_fingerprint <> %s
        """,
        (revision_id, stage, fingerprint),
    )
    cur.execute(
        """
        INSERT INTO core.processing_runs (
            id, drawing_revision_id, stage, tool_name, tool_version,
            run_fingerprint, config, statistics, status, is_current,
            started_at, finished_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'completed', true, now(), now())
        ON CONFLICT (drawing_revision_id, stage, run_fingerprint)
        DO UPDATE SET statistics = EXCLUDED.statistics,
                      config = EXCLUDED.config,
                      is_current = true,
                      status = 'completed',
                      finished_at = now()
        RETURNING id
        """,
        (
            new_id(),
            revision_id,
            stage,
            tool_name,
            tool_version,
            fingerprint,
            as_json(config),
            as_json(statistics),
        ),
    )
    return cur.fetchone()[0]


def import_structured(
    cur: psycopg.Cursor[Any], revision_id: Any, structured: dict[str, Any], file_digest: str
) -> dict[str, Any]:
    stats = structured.get("statistics", {})
    upsert_processing_run(
        cur,
        revision_id,
        "cad_parse",
        structured.get("drawing", {}).get("parser", "LibreDWG"),
        None,
        sha256_text(f"cad_parse|{file_digest}|{structured.get('schema_version')}"),
        {"schema_version": structured.get("schema_version")},
        stats,
    )

    layer_ids: dict[str, Any] = {}
    for layer in structured.get("layers", []):
        cur.execute(
            """
            INSERT INTO cad.layers (id, drawing_revision_id, cad_id, name, handle_json, source_index)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (drawing_revision_id, cad_id)
            DO UPDATE SET name = EXCLUDED.name,
                          handle_json = EXCLUDED.handle_json,
                          source_index = EXCLUDED.source_index
            RETURNING id
            """,
            (
                new_id(),
                revision_id,
                layer.get("id"),
                layer.get("name", ""),
                as_json(layer.get("handle")),
                layer.get("source_index"),
            ),
        )
        layer_ids[layer.get("id")] = cur.fetchone()[0]

    block_ids: dict[str, Any] = {}
    for block in structured.get("blocks", []):
        cur.execute(
            """
            INSERT INTO cad.blocks (
                id, drawing_revision_id, cad_id, name, handle_json, source_index,
                base_point_xyz, is_anonymous, block_entity_cad_id,
                endblk_entity_cad_id, layout_cad_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (drawing_revision_id, cad_id)
            DO UPDATE SET name = EXCLUDED.name,
                          handle_json = EXCLUDED.handle_json,
                          source_index = EXCLUDED.source_index,
                          base_point_xyz = EXCLUDED.base_point_xyz,
                          is_anonymous = EXCLUDED.is_anonymous,
                          block_entity_cad_id = EXCLUDED.block_entity_cad_id,
                          endblk_entity_cad_id = EXCLUDED.endblk_entity_cad_id,
                          layout_cad_id = EXCLUDED.layout_cad_id
            RETURNING id
            """,
            (
                new_id(),
                revision_id,
                block.get("id"),
                block.get("name", ""),
                as_json(block.get("handle")),
                block.get("source_index"),
                block.get("base_point"),
                bool(block.get("anonymous", 0)),
                block.get("block_entity_id"),
                block.get("endblk_entity_id"),
                block.get("layout_id"),
            ),
        )
        block_ids[block.get("id")] = cur.fetchone()[0]

    entity_ids: dict[str, Any] = {}
    for entity in structured.get("entities", []):
        cad_id = entity.get("id")
        entity_type = entity.get("type", "UNKNOWN")
        cur.execute(
            """
            INSERT INTO cad.entities (
                id, drawing_revision_id, cad_id, entity_type, layer_id,
                source_index, handle_json, raw_properties
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (drawing_revision_id, cad_id)
            DO UPDATE SET entity_type = EXCLUDED.entity_type,
                          layer_id = EXCLUDED.layer_id,
                          source_index = EXCLUDED.source_index,
                          handle_json = EXCLUDED.handle_json,
                          raw_properties = EXCLUDED.raw_properties
            RETURNING id
            """,
            (
                new_id(),
                revision_id,
                cad_id,
                entity_type,
                layer_ids.get(entity.get("layer")),
                entity.get("source_index"),
                as_json(entity.get("handle")),
                as_json(entity),
            ),
        )
        entity_id = cur.fetchone()[0]
        entity_ids[cad_id] = entity_id

        if entity_type == "LINE":
            start = xyz(entity.get("start")) or [0.0, 0.0, 0.0]
            end = xyz(entity.get("end")) or [0.0, 0.0, 0.0]
            cur.execute(
                """
                INSERT INTO cad.lines (entity_id, start_x, start_y, start_z, end_x, end_y, end_z)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (entity_id) DO UPDATE SET
                    start_x = EXCLUDED.start_x, start_y = EXCLUDED.start_y, start_z = EXCLUDED.start_z,
                    end_x = EXCLUDED.end_x, end_y = EXCLUDED.end_y, end_z = EXCLUDED.end_z
                """,
                (entity_id, *start, *end),
            )
        elif entity_type == "CIRCLE":
            center = xyz(entity.get("center")) or [0.0, 0.0, 0.0]
            cur.execute(
                """
                INSERT INTO cad.circles (entity_id, center_x, center_y, center_z, radius)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (entity_id) DO UPDATE SET
                    center_x = EXCLUDED.center_x, center_y = EXCLUDED.center_y,
                    center_z = EXCLUDED.center_z, radius = EXCLUDED.radius
                """,
                (entity_id, *center, entity.get("radius")),
            )
        elif entity_type == "ARC":
            center = xyz(entity.get("center")) or [0.0, 0.0, 0.0]
            cur.execute(
                """
                INSERT INTO cad.arcs (
                    entity_id, center_x, center_y, center_z, radius, start_angle, end_angle
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (entity_id) DO UPDATE SET
                    center_x = EXCLUDED.center_x, center_y = EXCLUDED.center_y,
                    center_z = EXCLUDED.center_z, radius = EXCLUDED.radius,
                    start_angle = EXCLUDED.start_angle, end_angle = EXCLUDED.end_angle
                """,
                (
                    entity_id,
                    *center,
                    entity.get("radius"),
                    entity.get("start_angle"),
                    entity.get("end_angle"),
                ),
            )
        elif entity_type in {"POLYLINE", "LWPOLYLINE"}:
            cur.execute(
                """
                INSERT INTO cad.polylines (entity_id, is_closed, flags, vertices)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (entity_id) DO UPDATE SET
                    is_closed = EXCLUDED.is_closed, flags = EXCLUDED.flags,
                    vertices = EXCLUDED.vertices
                """,
                (
                    entity_id,
                    bool(entity.get("closed")) if entity.get("closed") is not None else None,
                    entity.get("flag"),
                    as_json(entity.get("vertices", [])),
                ),
            )
        elif entity_type in {"TEXT", "MTEXT"}:
            position = xyz(entity.get("position"))
            cur.execute(
                """
                INSERT INTO cad.texts (
                    entity_id, text_kind, text_raw, position_x, position_y, position_z, text_height
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (entity_id) DO UPDATE SET
                    text_kind = EXCLUDED.text_kind, text_raw = EXCLUDED.text_raw,
                    position_x = EXCLUDED.position_x, position_y = EXCLUDED.position_y,
                    position_z = EXCLUDED.position_z, text_height = EXCLUDED.text_height
                """,
                (
                    entity_id,
                    entity_type,
                    entity.get("text"),
                    *(position or [None, None, None]),
                    entity.get("text_height"),
                ),
            )
        elif entity_type == "DIMENSION":
            position = xyz(entity.get("text_position"))
            cur.execute(
                """
                INSERT INTO cad.dimensions (
                    entity_id, dimension_type, text_raw, measurement_raw,
                    text_position_x, text_position_y, text_position_z, dimension_block_cad_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (entity_id) DO UPDATE SET
                    dimension_type = EXCLUDED.dimension_type, text_raw = EXCLUDED.text_raw,
                    measurement_raw = EXCLUDED.measurement_raw,
                    text_position_x = EXCLUDED.text_position_x,
                    text_position_y = EXCLUDED.text_position_y,
                    text_position_z = EXCLUDED.text_position_z,
                    dimension_block_cad_id = EXCLUDED.dimension_block_cad_id
                """,
                (
                    entity_id,
                    entity.get("dimension_type"),
                    entity.get("text"),
                    entity.get("measurement"),
                    *(position or [None, None, None]),
                    entity.get("block_id"),
                ),
            )
        elif entity_type == "INSERT":
            position = xyz(entity.get("position"))
            scale = entity.get("scale") or [None, None, None]
            scale = list(scale) + [None] * (3 - len(scale))
            cur.execute(
                """
                INSERT INTO cad.inserts (
                    entity_id, block_name, block_cad_id, position_x, position_y, position_z,
                    rotation, scale_x, scale_y, scale_z
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (entity_id) DO UPDATE SET
                    block_name = EXCLUDED.block_name, block_cad_id = EXCLUDED.block_cad_id,
                    position_x = EXCLUDED.position_x, position_y = EXCLUDED.position_y,
                    position_z = EXCLUDED.position_z, rotation = EXCLUDED.rotation,
                    scale_x = EXCLUDED.scale_x, scale_y = EXCLUDED.scale_y, scale_z = EXCLUDED.scale_z
                """,
                (
                    entity_id,
                    entity.get("block_name"),
                    entity.get("block_id"),
                    *(position or [None, None, None]),
                    entity.get("rotation"),
                    scale[0],
                    scale[1],
                    scale[2],
                ),
            )

    for block in structured.get("blocks", []):
        block_id = block_ids.get(block.get("id"))
        if not block_id:
            continue
        for ordinal, cad_id in enumerate(block.get("entity_ids", [])):
            entity_id = entity_ids.get(cad_id)
            if not entity_id:
                continue
            cur.execute(
                """
                INSERT INTO cad.block_memberships (block_id, entity_id, ordinal)
                VALUES (%s, %s, %s)
                ON CONFLICT (block_id, entity_id)
                DO UPDATE SET ordinal = EXCLUDED.ordinal
                """,
                (block_id, entity_id, ordinal),
            )

    return {"layer_ids": layer_ids, "block_ids": block_ids, "entity_ids": entity_ids}


def import_native_annotations(
    cur: psycopg.Cursor[Any], revision_id: Any, native: dict[str, Any], file_digest: str,
    entity_ids: dict[str, Any]
) -> dict[str, Any]:
    cur.execute("UPDATE core.drawing_revisions SET cad_bounds = %s, cad_coordinate_system = %s WHERE id = %s", (as_json(native.get("cad_bounds", {})), native.get("cad_coordinate_system"), revision_id))
    stats = native.get("statistics", {})
    run_id = upsert_processing_run(
        cur,
        revision_id,
        "native_normalize",
        native.get("drawing", {}).get("parser", "LibreDWG"),
        native.get("schema_version"),
        sha256_text(f"native_normalize|{file_digest}|{native.get('schema_version')}"),
        {"schema_version": native.get("schema_version")},
        stats,
    )
    del run_id

    annotation_ids: dict[str, Any] = {}
    for item in native.get("items", []):
        parsed = item.get("parsed") or {}
        position = xyz(item.get("position_cad"))
        cur.execute(
            """
            INSERT INTO cad.native_annotations (
                id, drawing_revision_id, external_id, entity_type, text_raw,
                display_text_raw, text_clean, text_normalized,
                native_dimension_type, native_measurement, parsed_kind,
                parsed_value, parsed_unit, position_x, position_y, position_z,
                display_entity_cad_id, dimension_block_cad_id, normalizer_version, metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (drawing_revision_id, external_id)
            DO UPDATE SET entity_type = EXCLUDED.entity_type,
                          text_raw = EXCLUDED.text_raw,
                          display_text_raw = EXCLUDED.display_text_raw,
                          text_clean = EXCLUDED.text_clean,
                          text_normalized = EXCLUDED.text_normalized,
                          native_dimension_type = EXCLUDED.native_dimension_type,
                          native_measurement = EXCLUDED.native_measurement,
                          parsed_kind = EXCLUDED.parsed_kind,
                          parsed_value = EXCLUDED.parsed_value,
                          parsed_unit = EXCLUDED.parsed_unit,
                          position_x = EXCLUDED.position_x,
                          position_y = EXCLUDED.position_y,
                          position_z = EXCLUDED.position_z,
                          display_entity_cad_id = EXCLUDED.display_entity_cad_id,
                          dimension_block_cad_id = EXCLUDED.dimension_block_cad_id,
                          normalizer_version = EXCLUDED.normalizer_version,
                          metadata = EXCLUDED.metadata
            RETURNING id
            """,
            (
                new_id(),
                revision_id,
                item.get("id"),
                item.get("entity_type", "ANNOTATION"),
                item.get("text_raw"),
                item.get("display_text_raw"),
                item.get("text_clean"),
                item.get("text_normalized"),
                item.get("dimension_type"),
                item.get("measurement_raw"),
                parsed.get("kind"),
                parsed.get("value"),
                parsed.get("unit"),
                *(position or [None, None, None]),
                item.get("display_entity_id"),
                item.get("block_id"),
                native.get("schema_version"),
                as_json({"source_entity_types": item.get("source_entity_types", [])}),
            ),
        )
        annotation_id = cur.fetchone()[0]
        annotation_ids[item.get("id")] = annotation_id

        for source_index, cad_id in enumerate(item.get("source_entity_ids", [])):
            entity_id = entity_ids.get(cad_id)
            if not entity_id:
                continue
            role = "display_text_entity" if cad_id == item.get("display_entity_id") else "source_entity"
            cur.execute(
                """
                INSERT INTO cad.native_annotation_entities (annotation_id, entity_id, role)
                VALUES (%s, %s, %s)
                ON CONFLICT (annotation_id, entity_id, role) DO NOTHING
                """,
                (annotation_id, entity_id, role),
            )

    return annotation_ids


def import_ocr(
    cur: psycopg.Cursor[Any], revision_id: Any, ocr: dict[str, Any], file_digest: str, image_asset_id: Any
) -> dict[str, Any]:
    engine = ocr.get("engine", {})
    fingerprint = sha256_text(f"ocr|{file_digest}|{ocr.get('schema_version')}|{engine}")
    cur.execute(
        """
        UPDATE ocr.runs SET is_current = false
        WHERE drawing_revision_id = %s AND run_fingerprint <> %s
        """,
        (revision_id, fingerprint),
    )
    cur.execute(
        """
        INSERT INTO ocr.runs (
            id, drawing_revision_id, image_asset_id, engine_name, engine_version,
            backend, backend_version, preprocessing, postprocessing, statistics,
            run_fingerprint, is_current
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true)
        ON CONFLICT (drawing_revision_id, run_fingerprint)
        DO UPDATE SET image_asset_id = EXCLUDED.image_asset_id,
                      preprocessing = EXCLUDED.preprocessing,
                      postprocessing = EXCLUDED.postprocessing,
                      statistics = EXCLUDED.statistics,
                      is_current = true
        RETURNING id
        """,
        (
            new_id(),
            revision_id,
            image_asset_id,
            engine.get("name", "RapidOCR"),
            engine.get("version"),
            engine.get("backend"),
            engine.get("onnxruntime_version"),
            as_json(ocr.get("preprocessing", {})),
            as_json(ocr.get("postprocessing", {})),
            as_json(ocr.get("statistics", {})),
            fingerprint,
        ),
    )
    ocr_run_id = cur.fetchone()[0]
    cur.execute("UPDATE ocr.runs SET schema_version = %s, image_width = %s, image_height = %s, coordinate_system = %s WHERE id = %s", (ocr.get("schema_version"), (ocr.get("image") or {}).get("width"), (ocr.get("image") or {}).get("height"), (ocr.get("image") or {}).get("coordinate_system"), ocr_run_id))

    item_ids: dict[str, Any] = {}
    for item in ocr.get("items", []):
        parsed = item.get("parsed") or {}
        merge_metadata = item.get("merge") or {}
        source_item_ids = item.get("source_item_ids") or []
        cur.execute(
            """
            INSERT INTO ocr.items (
                id, ocr_run_id, external_id, text_raw, text_normalized, confidence,
                parsed_kind, parsed_value, parsed_unit, polygon_px, center_px,
                is_merged, merge_metadata, raw_payload
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ocr_run_id, external_id)
            DO UPDATE SET text_raw = EXCLUDED.text_raw,
                          text_normalized = EXCLUDED.text_normalized,
                          confidence = EXCLUDED.confidence,
                          parsed_kind = EXCLUDED.parsed_kind,
                          parsed_value = EXCLUDED.parsed_value,
                          parsed_unit = EXCLUDED.parsed_unit,
                          polygon_px = EXCLUDED.polygon_px,
                          center_px = EXCLUDED.center_px,
                          is_merged = EXCLUDED.is_merged,
                          merge_metadata = EXCLUDED.merge_metadata,
                          raw_payload = EXCLUDED.raw_payload
            RETURNING id
            """,
            (
                new_id(),
                ocr_run_id,
                item.get("id"),
                item.get("text_raw"),
                item.get("text_normalized"),
                item.get("confidence"),
                parsed.get("kind"),
                parsed.get("value"),
                parsed.get("unit"),
                as_json(item.get("polygon_px")),
                as_json(item.get("center_px")),
                bool(source_item_ids) or bool(merge_metadata),
                as_json(merge_metadata),
                as_json(item),
            ),
        )
        item_ids[item.get("id")] = cur.fetchone()[0]

    for item in ocr.get("items", []):
        merged_id = item_ids.get(item.get("id"))
        for ordinal, source_external_id in enumerate(item.get("source_item_ids") or []):
            source_id = item_ids.get(source_external_id)
            if not merged_id or not source_id:
                continue
            cur.execute(
                """
                INSERT INTO ocr.item_sources (merged_item_id, source_item_id, ordinal)
                VALUES (%s, %s, %s)
                ON CONFLICT (merged_item_id, source_item_id)
                DO UPDATE SET ordinal = EXCLUDED.ordinal
                """,
                (merged_id, source_id, ordinal),
            )

    return {"ocr_run_id": ocr_run_id, "item_ids": item_ids}


def import_alignment(
    cur: psycopg.Cursor[Any], revision_id: Any, alignment: dict[str, Any],
    native_asset_id: Any, ocr_run_id: Any, native_ids: dict[str, Any], ocr_ids: dict[str, Any],
    file_digest: str,
) -> dict[str, Any]:
    registration = alignment.get("registration", {})
    matrix = flatten_matrix(registration.get("cad_to_image_matrix", []))
    fingerprint = sha256_text(f"alignment|{file_digest}|{alignment.get('schema_version')}|{matrix}")
    cur.execute(
        """
        UPDATE ocr.alignment_runs SET is_current = false
        WHERE drawing_revision_id = %s AND run_fingerprint <> %s
        """,
        (revision_id, fingerprint),
    )
    cur.execute(
        """
        INSERT INTO ocr.alignment_runs (
            id, drawing_revision_id, native_asset_id, ocr_run_id, method,
            cad_to_image_matrix, anchor_count, inlier_anchor_count, rmse_px,
            pixel_content_bounds, status, run_fingerprint, is_current
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true)
        ON CONFLICT (drawing_revision_id, run_fingerprint)
        DO UPDATE SET native_asset_id = EXCLUDED.native_asset_id,
                      ocr_run_id = EXCLUDED.ocr_run_id,
                      cad_to_image_matrix = EXCLUDED.cad_to_image_matrix,
                      anchor_count = EXCLUDED.anchor_count,
                      inlier_anchor_count = EXCLUDED.inlier_anchor_count,
                      rmse_px = EXCLUDED.rmse_px,
                      pixel_content_bounds = EXCLUDED.pixel_content_bounds,
                      status = EXCLUDED.status,
                      is_current = true
        RETURNING id
        """,
        (
            new_id(), revision_id, native_asset_id, ocr_run_id,
            registration.get("method", "unknown"), matrix,
            registration.get("anchor_count", 0), registration.get("inlier_anchor_count", 0),
            registration.get("rmse_px"), as_json(registration.get("pixel_content_bounds")),
            registration.get("status", "unknown"), fingerprint,
        ),
    )
    alignment_run_id = cur.fetchone()[0]

    for anchor in alignment.get("anchors", []):
        native_id = native_ids.get(anchor.get("native_id"))
        ocr_id = ocr_ids.get(anchor.get("ocr_id"))
        if not native_id or not ocr_id:
            continue
        cur.execute(
            """
            INSERT INTO ocr.alignment_anchors (
                id, alignment_run_id, native_annotation_id, ocr_item_id,
                matched_text, cad_point, pixel_point, is_inlier, error_px
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (alignment_run_id, native_annotation_id, ocr_item_id)
            DO UPDATE SET matched_text = EXCLUDED.matched_text,
                          cad_point = EXCLUDED.cad_point,
                          pixel_point = EXCLUDED.pixel_point,
                          is_inlier = EXCLUDED.is_inlier,
                          error_px = EXCLUDED.error_px
            """,
            (
                new_id(), alignment_run_id, native_id, ocr_id,
                anchor.get("text"), as_json(anchor.get("cad")), as_json(anchor.get("pixel")),
                anchor.get("inlier"), anchor.get("error_px"),
            ),
        )

    for match in alignment.get("matches", []):
        native_id = native_ids.get(match.get("native_item_id"))
        ocr_id = ocr_ids.get(match.get("ocr_item_id"))
        if not native_id or not ocr_id:
            continue
        cur.execute(
            """
            INSERT INTO ocr.alignment_matches (
                id, alignment_run_id, native_annotation_id, ocr_item_id,
                text_similarity, distance_px, score, status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (alignment_run_id, native_annotation_id, ocr_item_id)
            DO UPDATE SET text_similarity = EXCLUDED.text_similarity,
                          distance_px = EXCLUDED.distance_px,
                          score = EXCLUDED.score,
                          status = EXCLUDED.status
            """,
            (
                new_id(), alignment_run_id, native_id, ocr_id,
                match.get("text_similarity"), match.get("distance_px"),
                match.get("score"), match.get("status", "unknown"),
            ),
        )

    return {"alignment_run_id": alignment_run_id}


def import_fusion(
    cur: psycopg.Cursor[Any], revision_id: Any, unified: dict[str, Any],
    alignment_run_id: Any, entity_ids: dict[str, Any], native_ids: dict[str, Any],
    ocr_ids: dict[str, Any], vision_ids: dict[str, Any], file_digest: str,
) -> dict[str, Any]:
    stats = unified.get("statistics", {})
    fingerprint = sha256_text(f"fusion|{file_digest}|{unified.get('schema_version')}|{alignment_run_id}")
    cur.execute(
        """
        UPDATE fusion.runs SET is_current = false
        WHERE drawing_revision_id = %s AND run_fingerprint <> %s
        """,
        (revision_id, fingerprint),
    )
    cur.execute(
        """
        INSERT INTO fusion.runs (
            id, drawing_revision_id, alignment_run_id, algorithm_name,
            algorithm_version, config, statistics, run_fingerprint, is_current
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, true)
        ON CONFLICT (drawing_revision_id, run_fingerprint)
        DO UPDATE SET alignment_run_id = EXCLUDED.alignment_run_id,
                      config = EXCLUDED.config,
                      statistics = EXCLUDED.statistics,
                      is_current = true
        RETURNING id
        """,
        (
            new_id(), revision_id, alignment_run_id, "build_unified_features",
            unified.get("schema_version"), as_json({"schema_version": unified.get("schema_version")}),
            as_json(stats), fingerprint,
        ),
    )
    fusion_run_id = cur.fetchone()[0]
    feature_ids: dict[str, Any] = {}

    for feature in unified.get("features", []):
        canonical = feature.get("canonical") or {}
        position = feature.get("position") or {}
        fusion = feature.get("fusion") or {}
        cur.execute(
            """
            INSERT INTO fusion.features (
                id, fusion_run_id, drawing_revision_id, external_id, feature_type,
                canonical_text, canonical_text_raw, dimension_kind, canonical_value, unit,
                cad_position, pixel_position, projected_position, fusion_status,
                fusion_score, text_similarity, distance_px, metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (fusion_run_id, external_id)
            DO UPDATE SET feature_type = EXCLUDED.feature_type,
                          canonical_text = EXCLUDED.canonical_text,
                          canonical_text_raw = EXCLUDED.canonical_text_raw,
                          dimension_kind = EXCLUDED.dimension_kind,
                          canonical_value = EXCLUDED.canonical_value,
                          unit = EXCLUDED.unit,
                          cad_position = EXCLUDED.cad_position,
                          pixel_position = EXCLUDED.pixel_position,
                          projected_position = EXCLUDED.projected_position,
                          fusion_status = EXCLUDED.fusion_status,
                          fusion_score = EXCLUDED.fusion_score,
                          text_similarity = EXCLUDED.text_similarity,
                          distance_px = EXCLUDED.distance_px,
                          metadata = EXCLUDED.metadata
            RETURNING id
            """,
            (
                new_id(), fusion_run_id, revision_id, feature.get("id"), feature.get("feature_type"),
                canonical.get("text"), canonical.get("text_raw"), canonical.get("dimension_kind"),
                canonical.get("value"), canonical.get("unit"),
                as_json(position.get("cad")), as_json(position.get("pixel")),
                as_json(position.get("pixel_projected")), fusion.get("status", "unknown"),
                fusion.get("score"), fusion.get("text_similarity"), fusion.get("distance_px"),
                as_json({
                    "source_entity_ids": feature.get("source_entity_ids", []),
                    "ocr_item_ids": feature.get("ocr_item_ids", []),
                    "vision_item_ids": feature.get("vision_item_ids", []),
                }),
            ),
        )
        feature_id = cur.fetchone()[0]
        feature_ids[feature.get("id")] = feature_id

        for cad_id in feature.get("source_entity_ids", []):
            entity_id = entity_ids.get(cad_id)
            if not entity_id:
                continue
            cur.execute(
                """
                INSERT INTO fusion.feature_cad_entities (feature_id, entity_id)
                VALUES (%s, %s)
                ON CONFLICT (feature_id, entity_id, evidence_role) DO NOTHING
                """,
                (feature_id, entity_id),
            )

        evidence = feature.get("evidence") or {}
        native_evidence = evidence.get("cad_native") or {}
        native_id = native_ids.get(native_evidence.get("item_id"))
        if native_id:
            cur.execute(
                """
                INSERT INTO fusion.feature_native_annotations (feature_id, annotation_id)
                VALUES (%s, %s)
                ON CONFLICT (feature_id, annotation_id, evidence_role) DO NOTHING
                """,
                (feature_id, native_id),
            )

        ocr_evidence = evidence.get("image_ocr") or {}
        ocr_id = ocr_ids.get(ocr_evidence.get("item_id"))
        if ocr_id:
            confidence = ocr_evidence.get("confidence")
            cur.execute(
                """
                INSERT INTO fusion.feature_ocr_items (feature_id, ocr_item_id, confidence)
                VALUES (%s, %s, %s)
                ON CONFLICT (feature_id, ocr_item_id, evidence_role)
                DO UPDATE SET confidence = EXCLUDED.confidence
                """,
                (feature_id, ocr_id, confidence),
            )

        for vision_external_id in feature.get("vision_item_ids", []):
            observation_id = vision_ids.get(vision_external_id)
            if not observation_id:
                continue
            vision_evidence = next(
                (
                    item for item in (evidence.get("vision") or [])
                    if item.get("item_id") == vision_external_id
                ),
                {},
            )
            cur.execute(
                """
                INSERT INTO fusion.feature_vision_observations (
                    feature_id, observation_id, confidence
                )
                VALUES (%s, %s, %s)
                ON CONFLICT (feature_id, observation_id, evidence_role)
                DO UPDATE SET confidence = EXCLUDED.confidence
                """,
                (feature_id, observation_id, vision_evidence.get("model_confidence")),
            )

        if fusion.get("status") == "conflict":
            cur.execute(
                """
                INSERT INTO fusion.conflicts (
                    id, drawing_revision_id, feature_id, conflict_type,
                    cad_value, ocr_value, status
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'open')
                ON CONFLICT (feature_id, conflict_type)
                DO UPDATE SET cad_value = EXCLUDED.cad_value,
                              ocr_value = EXCLUDED.ocr_value
                """,
                (
                    new_id(), revision_id, feature_id, "cad_ocr_value_or_text_mismatch",
                    as_json(native_evidence), as_json(ocr_evidence),
                ),
            )

    return {"fusion_run_id": fusion_run_id, "feature_ids": feature_ids}


def discover_records(root: Path, dataset_dir: Path, demo_dir: Path) -> list[dict[str, Path]]:
    structured_dir = demo_dir / "structured"
    records: list[dict[str, Path]] = []
    seen_dwg_paths: set[Path] = set()
    for structured_path in sorted(structured_dir.glob("*_structured.json")):
        structured = load_json(structured_path)
        source_file = structured.get("drawing", {}).get("source_file")
        if not source_file:
            continue
        base_name = Path(source_file).stem
        dwg = find_dataset_file(dataset_dir, base_name, ".dwg")
        if dwg is None:
            continue
        resolved_dwg = dwg.resolve()
        if resolved_dwg in seen_dwg_paths:
            continue
        seen_dwg_paths.add(resolved_dwg)
        records.append(
            {
                "structured": structured_path,
                "dwg": dwg,
                "png": find_dataset_file(dataset_dir, base_name, ".png"),
                "native": demo_dir / "ocr" / "normalized" / f"{base_name}_native_text.json",
                "ocr": demo_dir / "ocr" / "raw" / f"{base_name}_ocr.json",
                "alignment": demo_dir / "aligned" / f"{base_name}_alignment.json",
                "vision": demo_dir / "vision" / "normalized" / f"{base_name}_vision.json",
                "unified": demo_dir / "unified" / f"{base_name}_unified_features.json",
            }
        )
    return records


def import_record(conn: psycopg.Connection[Any], root: Path, record: dict[str, Path]) -> dict[str, Any]:
    structured = load_json(record["structured"])
    base_name = Path(structured["drawing"]["source_file"]).stem
    drawing_key = record.get("drawing_key") or slugify(base_name)

    with conn.transaction():
        with conn.cursor() as cur:
            drawing_id = get_or_insert_drawing(cur, drawing_key, base_name)
            revision_id = record.get("revision_id") or get_or_insert_revision(cur, drawing_id, structured)

            assets: dict[str, Any] = {}
            assets["dwg"] = upsert_asset(cur, root, revision_id, "dwg", record["dwg"])
            assets["png"] = upsert_asset(cur, root, revision_id, "png", record.get("png"))
            assets["structured_json"] = upsert_asset(
                cur, root, revision_id, "structured_json", record["structured"], structured.get("schema_version")
            )
            for role in ("native", "ocr", "alignment", "vision", "unified"):
                path = record.get(role)
                if path and path.exists():
                    payload = load_json(path)
                    assets[f"{role}_json"] = upsert_asset(
                        cur, root, revision_id, f"{role}_json", path, payload.get("schema_version")
                    )

            structured_result = import_structured(
                cur, revision_id, structured, sha256_file(record["structured"])
            )
            entity_ids = structured_result["entity_ids"]

            native_ids: dict[str, Any] = {}
            if record.get("native") and record["native"].exists():
                native = load_json(record["native"])
                native_ids = import_native_annotations(
                    cur, revision_id, native, sha256_file(record["native"]), entity_ids
                )

            ocr_result: dict[str, Any] = {"ocr_run_id": None, "item_ids": {}}
            if record.get("ocr") and record["ocr"].exists():
                ocr = load_json(record["ocr"])
                ocr_result = import_ocr(
                    cur, revision_id, ocr, sha256_file(record["ocr"]), assets.get("png")
                )

            alignment_result: dict[str, Any] = {"alignment_run_id": None}
            if record.get("alignment") and record["alignment"].exists():
                alignment = load_json(record["alignment"])
                alignment_result = import_alignment(
                    cur,
                    revision_id,
                    alignment,
                    assets.get("native_json"),
                    ocr_result["ocr_run_id"],
                    native_ids,
                    ocr_result["item_ids"],
                    sha256_file(record["alignment"]),
                )

            vision_ids: dict[str, Any] = {}
            cur.execute(
                """
                SELECT o.external_id, o.id
                FROM vision.observations AS o
                JOIN vision.runs AS vr ON vr.id = o.vision_run_id AND vr.is_current
                WHERE vr.drawing_revision_id = %s
                """,
                (revision_id,),
            )
            vision_ids = {row[0]: row[1] for row in cur.fetchall()}

            fusion_result: dict[str, Any] = {"fusion_run_id": None, "feature_ids": {}}
            if record.get("unified") and record["unified"].exists():
                unified = load_json(record["unified"])
                fusion_result = import_fusion(
                    cur,
                    revision_id,
                    unified,
                    alignment_result["alignment_run_id"],
                    entity_ids,
                    native_ids,
                    ocr_result["item_ids"],
                    vision_ids,
                    sha256_file(record["unified"]),
                )

            return {
                "drawing_key": drawing_key,
                "drawing_id": str(drawing_id),
                "revision_id": str(revision_id),
                "entity_count": len(entity_ids),
                "native_annotation_count": len(native_ids),
                "ocr_item_count": len(ocr_result["item_ids"]),
                "vision_observation_count": len(vision_ids),
                "feature_count": len(fusion_result["feature_ids"]),
            }


def main() -> int:
    args = parse_args()
    if not args.dsn:
        print("DATABASE_URL or --dsn is required", file=sys.stderr)
        return 2

    root = args.root.resolve()
    dataset_dir = resolve_path(root, args.dataset)
    demo_dir = resolve_path(root, args.demo_dir)
    records = discover_records(root, dataset_dir, demo_dir)
    if args.limit is not None:
        records = records[: args.limit]
    if not records:
        print(f"No paired structured/DWG records found under {demo_dir} and {dataset_dir}", file=sys.stderr)
        return 1

    with psycopg.connect(args.dsn) as conn:
        if args.migrate:
            apply_migrations(conn, root)
        results = [import_record(conn, root, record) for record in records]

    for result in results:
        print(
            f"{result['drawing_key']}: entities={result['entity_count']} "
            f"native={result['native_annotation_count']} ocr={result['ocr_item_count']} "
            f"features={result['feature_count']}"
        )
    print(f"Imported {len(results)} drawing revisions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
