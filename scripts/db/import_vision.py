#!/usr/bin/env python3
"""Import one normalized Vision JSON document into PostgreSQL."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import psycopg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "db"))

from common import as_json, new_id, sha256_text  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vision_json", type=Path)
    parser.add_argument("--dsn", default=os.getenv("DATABASE_URL", "postgresql:///cad_kb"))
    parser.add_argument("--migrate", action="store_true")
    parser.add_argument("--raw-json", type=Path)
    return parser.parse_args()


def apply_migrations(conn: psycopg.Connection[Any]) -> None:
    with conn.cursor() as cur:
        for migration in sorted((ROOT / "db" / "migrations").glob("*.sql")):
            cur.execute(migration.read_text(encoding="utf-8"))
    conn.commit()


def main() -> int:
    args = parse_args()
    document = json.loads(args.vision_json.read_text(encoding="utf-8"))
    drawing_key = document["drawing_key"]
    image = document["image"]
    model = document["model"]
    raw_response = None
    if args.raw_json and args.raw_json.exists():
        raw_response = json.loads(args.raw_json.read_text(encoding="utf-8"))

    with psycopg.connect(args.dsn) as conn:
        if args.migrate:
            apply_migrations(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id
                FROM core.drawing_revisions AS r
                JOIN core.drawings AS d ON d.id = r.drawing_id
                WHERE d.drawing_key = %s
                ORDER BY r.revision_sequence DESC, r.created_at DESC
                LIMIT 1
                """,
                (drawing_key,),
            )
            revision = cur.fetchone()
            if not revision:
                raise RuntimeError(f"No drawing revision found for drawing_key={drawing_key}")
            revision_id = revision[0]

            cur.execute(
                """
                SELECT id
                FROM core.source_assets
                WHERE drawing_revision_id = %s
                  AND asset_role = 'png'
                  AND sha256 = %s
                LIMIT 1
                """,
                (revision_id, image["sha256"]),
            )
            image_asset = cur.fetchone()
            image_asset_id = image_asset[0] if image_asset else None

            fingerprint = sha256_text(
                "vision|"
                + image["sha256"]
                + "|"
                + model["name"]
                + "|"
                + model["prompt_version"]
                + "|"
                + json.dumps(document.get("statistics", {}), sort_keys=True)
            )
            cur.execute(
                """
                UPDATE vision.runs
                SET is_current = false
                WHERE drawing_revision_id = %s AND run_fingerprint <> %s
                """,
                (revision_id, fingerprint),
            )
            cur.execute(
                """
                INSERT INTO vision.runs (
                    id, drawing_revision_id, image_asset_id, model_name,
                    prompt_version, detail_level, image_sha256,
                    request_config, raw_response, statistics,
                    run_fingerprint, status, is_current
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, 'completed', true
                )
                ON CONFLICT (drawing_revision_id, run_fingerprint)
                DO UPDATE SET
                    image_asset_id = EXCLUDED.image_asset_id,
                    model_name = EXCLUDED.model_name,
                    prompt_version = EXCLUDED.prompt_version,
                    detail_level = EXCLUDED.detail_level,
                    raw_response = EXCLUDED.raw_response,
                    statistics = EXCLUDED.statistics,
                    status = 'completed',
                    is_current = true
                RETURNING id
                """,
                (
                    new_id(), revision_id, image_asset_id, model["name"],
                    model["prompt_version"], model.get("detail"), image["sha256"],
                    as_json({"schema_version": document.get("schema_version")}),
                    as_json(raw_response) if raw_response is not None else None,
                    as_json(document.get("statistics", {})),
                    fingerprint,
                ),
            )
            run_id = cur.fetchone()[0]

            for item in document.get("observations", []):
                cur.execute(
                    """
                    INSERT INTO vision.observations (
                        id, vision_run_id, external_id, observation_type,
                        description, region_px, region_normalized,
                        candidate_feature, model_confidence, status,
                        uncertainty, properties, raw_payload
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (vision_run_id, external_id)
                    DO UPDATE SET
                        observation_type = EXCLUDED.observation_type,
                        description = EXCLUDED.description,
                        region_px = EXCLUDED.region_px,
                        region_normalized = EXCLUDED.region_normalized,
                        candidate_feature = EXCLUDED.candidate_feature,
                        model_confidence = EXCLUDED.model_confidence,
                        status = EXCLUDED.status,
                        uncertainty = EXCLUDED.uncertainty,
                        properties = EXCLUDED.properties,
                        raw_payload = EXCLUDED.raw_payload
                    """,
                    (
                        new_id(), run_id, item["id"], item["observation_type"],
                        item["description"], as_json(item.get("region_px")),
                        as_json(item.get("region_normalized")),
                        as_json(item.get("candidate_feature")),
                        item.get("model_confidence"), item.get("status", "candidate"),
                        item.get("uncertainty"), as_json(item.get("properties", {})),
                        as_json(item),
                    ),
                )
        conn.commit()

    print(f"drawing_key={drawing_key}")
    print(f"vision_run_id={run_id}")
    print(f"observations={len(document.get('observations', []))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
