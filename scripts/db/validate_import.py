#!/usr/bin/env python3
"""Validate PostgreSQL rows against the current structured demo artifacts."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import psycopg

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from common import load_json  # noqa: E402
from import_all import discover_records, resolve_path  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=os.getenv("DATABASE_URL"), help="PostgreSQL DSN")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--dataset", type=Path, default=Path("dataset/AutoCAD_2D_Drawings"))
    parser.add_argument("--demo-dir", type=Path, default=Path("demo"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("demo/reports/postgres_import_validation.json"),
    )
    return parser.parse_args()


def one(cur: psycopg.Cursor[Any], sql: str, params: tuple[Any, ...] = ()) -> Any:
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None


def counts_by_type(cur: psycopg.Cursor[Any], revision_id: Any) -> dict[str, int]:
    cur.execute(
        """
        SELECT entity_type, COUNT(*)
        FROM cad.entities
        WHERE drawing_revision_id = %s
        GROUP BY entity_type
        ORDER BY entity_type
        """,
        (revision_id,),
    )
    return {row[0]: row[1] for row in cur.fetchall()}


def validate_record(cur: psycopg.Cursor[Any], record: dict[str, Path]) -> dict[str, Any]:
    structured = load_json(record["structured"])
    title = Path(structured["drawing"]["source_file"]).stem
    drawing_key = "-".join(title.lower().split())
    # The importer collapses non-alphanumeric separators; use its actual key.
    from common import slugify

    drawing_key = slugify(title)

    cur.execute(
        """
        SELECT id FROM core.drawings WHERE drawing_key = %s
        """,
        (drawing_key,),
    )
    drawing_row = cur.fetchone()
    if not drawing_row:
        return {
            "drawing_key": drawing_key,
            "status": "missing_drawing",
            "ok": False,
        }
    drawing_id = drawing_row[0]
    cur.execute(
        """
        SELECT id FROM core.drawing_revisions
        WHERE drawing_id = %s AND revision_sequence = 1
        """,
        (drawing_id,),
    )
    revision_row = cur.fetchone()
    if not revision_row:
        return {
            "drawing_key": drawing_key,
            "status": "missing_revision",
            "ok": False,
        }
    revision_id = revision_row[0]

    expected_entities = Counter(item.get("type", "UNKNOWN") for item in structured.get("entities", []))
    actual_entities = counts_by_type(cur, revision_id)

    expected_ocr = None
    actual_ocr = None
    if record.get("ocr") and record["ocr"].exists():
        expected_ocr = len(load_json(record["ocr"]).get("items", []))
        cur.execute(
            "SELECT id FROM ocr.runs WHERE drawing_revision_id = %s AND is_current",
            (revision_id,),
        )
        ocr_run = cur.fetchone()
        if ocr_run:
            actual_ocr = one(cur, "SELECT COUNT(*) FROM ocr.items WHERE ocr_run_id = %s", (ocr_run[0],))

    expected_features = None
    expected_statuses: dict[str, int] = {}
    actual_features = None
    actual_statuses: dict[str, int] = {}
    if record.get("unified") and record["unified"].exists():
        unified = load_json(record["unified"])
        expected_features = len(unified.get("features", []))
        expected_statuses = dict(Counter(
            (item.get("fusion") or {}).get("status", "unknown")
            for item in unified.get("features", [])
        ))
        cur.execute(
            "SELECT id FROM fusion.runs WHERE drawing_revision_id = %s AND is_current",
            (revision_id,),
        )
        fusion_run = cur.fetchone()
        if fusion_run:
            actual_features = one(
                cur, "SELECT COUNT(*) FROM fusion.features WHERE fusion_run_id = %s", (fusion_run[0],)
            )
            cur.execute(
                """
                SELECT fusion_status, COUNT(*)
                FROM fusion.features
                WHERE fusion_run_id = %s
                GROUP BY fusion_status
                ORDER BY fusion_status
                """,
                (fusion_run[0],),
            )
            actual_statuses = {row[0]: row[1] for row in cur.fetchall()}

    checks = {
        "entity_counts": dict(expected_entities) == actual_entities,
        "ocr_count": expected_ocr is None or expected_ocr == actual_ocr,
        "feature_count": expected_features is None or expected_features == actual_features,
        "feature_statuses": expected_statuses == actual_statuses,
    }
    return {
        "drawing_key": drawing_key,
        "revision_id": str(revision_id),
        "expected": {
            "entity_counts": dict(expected_entities),
            "ocr_count": expected_ocr,
            "feature_count": expected_features,
            "feature_statuses": expected_statuses,
        },
        "actual": {
            "entity_counts": actual_entities,
            "ocr_count": actual_ocr,
            "feature_count": actual_features,
            "feature_statuses": actual_statuses,
        },
        "checks": checks,
        "ok": all(checks.values()),
    }


def validate_orphans(cur: psycopg.Cursor[Any]) -> dict[str, int]:
    checks = {
        "native_annotation_entity_orphans": """
            SELECT COUNT(*) FROM cad.native_annotation_entities nae
            LEFT JOIN cad.native_annotations na ON na.id = nae.annotation_id
            LEFT JOIN cad.entities e ON e.id = nae.entity_id
            WHERE na.id IS NULL OR e.id IS NULL
        """,
        "feature_cad_evidence_orphans": """
            SELECT COUNT(*) FROM fusion.feature_cad_entities fce
            LEFT JOIN fusion.features f ON f.id = fce.feature_id
            LEFT JOIN cad.entities e ON e.id = fce.entity_id
            WHERE f.id IS NULL OR e.id IS NULL
        """,
        "feature_native_evidence_orphans": """
            SELECT COUNT(*) FROM fusion.feature_native_annotations fna
            LEFT JOIN fusion.features f ON f.id = fna.feature_id
            LEFT JOIN cad.native_annotations na ON na.id = fna.annotation_id
            WHERE f.id IS NULL OR na.id IS NULL
        """,
        "feature_ocr_evidence_orphans": """
            SELECT COUNT(*) FROM fusion.feature_ocr_items foi
            LEFT JOIN fusion.features f ON f.id = foi.feature_id
            LEFT JOIN ocr.items oi ON oi.id = foi.ocr_item_id
            WHERE f.id IS NULL OR oi.id IS NULL
        """,
        "feature_vision_evidence_orphans": """
            SELECT COUNT(*) FROM fusion.feature_vision_observations fvo
            LEFT JOIN fusion.features f ON f.id = fvo.feature_id
            LEFT JOIN vision.observations vo ON vo.id = fvo.observation_id
            WHERE f.id IS NULL OR vo.id IS NULL
        """,
        "semantic_chunk_source_orphans": """
            SELECT COUNT(*) FROM semantic.chunks sc
            LEFT JOIN core.drawing_revisions r ON r.id = sc.drawing_revision_id
            LEFT JOIN fusion.features f
              ON sc.source_type = 'unified_feature' AND f.id = sc.source_id
            WHERE r.id IS NULL
               OR (sc.source_type = 'drawing_revision' AND sc.source_id <> sc.drawing_revision_id)
               OR (sc.source_type = 'unified_feature' AND f.id IS NULL)
        """,
    }
    return {name: int(one(cur, sql) or 0) for name, sql in checks.items()}


def main() -> int:
    args = parse_args()
    if not args.dsn:
        print("DATABASE_URL or --dsn is required", file=sys.stderr)
        return 2

    root = args.root.resolve()
    records = discover_records(root, resolve_path(root, args.dataset), resolve_path(root, args.demo_dir))
    with psycopg.connect(args.dsn) as conn:
        with conn.cursor() as cur:
            record_results = [validate_record(cur, record) for record in records]
            orphan_counts = validate_orphans(cur)
            totals = {
                "drawings": one(cur, "SELECT COUNT(*) FROM core.drawings"),
                "drawing_revisions": one(cur, "SELECT COUNT(*) FROM core.drawing_revisions"),
                "cad_entities": one(cur, "SELECT COUNT(*) FROM cad.entities"),
                "ocr_items": one(cur, "SELECT COUNT(*) FROM ocr.items"),
                "unified_features": one(cur, "SELECT COUNT(*) FROM fusion.features"),
                "current_vision_runs": one(cur, "SELECT COUNT(*) FROM vision.runs WHERE is_current"),
                "current_vision_observations": one(cur, """
                    SELECT COUNT(*) FROM vision.observations vo
                    JOIN vision.runs vr ON vr.id = vo.vision_run_id AND vr.is_current
                """),
                "vision_evidence_links": one(cur, "SELECT COUNT(*) FROM fusion.feature_vision_observations"),
                "current_semantic_chunks": one(cur, "SELECT COUNT(*) FROM semantic.chunks WHERE is_current"),
            }

    report = {
        "schema_version": "cad-postgres-validation-v1",
        "record_count": len(record_results),
        "totals": totals,
        "orphan_counts": orphan_counts,
        "records": record_results,
        "ok": bool(record_results)
        and all(result["ok"] for result in record_results)
        and all(count == 0 for count in orphan_counts.values()),
    }
    output = resolve_path(root, args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
