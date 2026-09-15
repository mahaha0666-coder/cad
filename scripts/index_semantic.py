#!/usr/bin/env python3
"""Build CAD semantic chunks, embed them locally, and index them in Qdrant."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from app.semantic import SemanticConfig, SemanticSearchService  # noqa: E402
from app.semantic.chunks import SemanticChunk, build_semantic_chunks  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=os.getenv("DATABASE_URL", "postgresql:///cad_kb"))
    parser.add_argument("--migrate", action="store_true", help="Apply all SQL migrations before indexing")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument(
        "--report",
        type=Path,
        default=PROJECT_ROOT / "demo" / "reports" / "semantic_index_summary.json",
    )
    return parser.parse_args()


def apply_migrations(connection: psycopg.Connection[Any]) -> None:
    for migration in sorted((PROJECT_ROOT / "db" / "migrations").glob("*.sql")):
        with connection.cursor() as cur:
            cur.execute(migration.read_text(encoding="utf-8"))
    connection.commit()


def fingerprint(chunks: list[SemanticChunk], config: SemanticConfig) -> str:
    digest = hashlib.sha256()
    digest.update(config.collection_name.encode())
    digest.update(config.embedding_model.encode())
    for chunk in chunks:
        digest.update(str(chunk.id).encode())
        digest.update(chunk.content_hash.encode())
    return digest.hexdigest()


def start_run(
    connection: psycopg.Connection[Any],
    config: SemanticConfig,
    run_fingerprint: str,
) -> uuid.UUID:
    run_id = uuid.uuid4()
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO semantic.index_runs (
                id, collection_name, embedding_model, embedding_dimensions,
                run_fingerprint, status, started_at
            )
            VALUES (%s, %s, %s, %s, %s, 'running', now())
            ON CONFLICT (run_fingerprint)
            DO UPDATE SET status='running', error_message=NULL, started_at=now(), finished_at=NULL
            RETURNING id
            """,
            (
                run_id,
                config.collection_name,
                config.embedding_model,
                config.embedding_dimensions,
                run_fingerprint,
            ),
        )
        run_id = cur.fetchone()[0]
    connection.commit()
    return run_id


def current_point_ids(connection: psycopg.Connection[Any]) -> set[str]:
    with connection.cursor() as cur:
        cur.execute("SELECT vector_point_id::text FROM semantic.chunks WHERE is_current")
        return {row[0] for row in cur.fetchall()}


def persist_chunks(
    connection: psycopg.Connection[Any],
    chunks: list[SemanticChunk],
    config: SemanticConfig,
    run_id: uuid.UUID,
    statistics: dict[str, Any],
) -> None:
    with connection.cursor() as cur:
        cur.execute("UPDATE semantic.chunks SET is_current=false, updated_at=now() WHERE is_current")
        for chunk in chunks:
            cur.execute(
                """
                INSERT INTO semantic.chunks (
                    id, drawing_revision_id, source_type, source_id,
                    source_external_id, chunk_kind, content, content_hash,
                    metadata, vector_point_id, embedding_model,
                    last_index_run_id, is_current, indexed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true, now())
                ON CONFLICT (id)
                DO UPDATE SET source_id=EXCLUDED.source_id,
                              chunk_kind=EXCLUDED.chunk_kind,
                              content=EXCLUDED.content,
                              content_hash=EXCLUDED.content_hash,
                              metadata=EXCLUDED.metadata,
                              embedding_model=EXCLUDED.embedding_model,
                              last_index_run_id=EXCLUDED.last_index_run_id,
                              is_current=true,
                              indexed_at=now(),
                              updated_at=now()
                """,
                (
                    chunk.id,
                    chunk.drawing_revision_id,
                    chunk.source_type,
                    chunk.source_id,
                    chunk.source_external_id,
                    chunk.chunk_kind,
                    chunk.content,
                    chunk.content_hash,
                    Jsonb(chunk.metadata),
                    chunk.point_id,
                    config.embedding_model,
                    run_id,
                ),
            )
        cur.execute(
            """
            UPDATE semantic.index_runs
            SET status='completed', statistics=%s, finished_at=now()
            WHERE id=%s
            """,
            (Jsonb(statistics), run_id),
        )
    connection.commit()


def fail_run(connection: psycopg.Connection[Any], run_id: uuid.UUID, error: Exception) -> None:
    connection.rollback()
    with connection.cursor() as cur:
        cur.execute(
            """
            UPDATE semantic.index_runs
            SET status='failed', error_message=%s, finished_at=now()
            WHERE id=%s
            """,
            (str(error)[:4000], run_id),
        )
    connection.commit()


def main() -> int:
    args = parse_args()
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be positive")
    config = SemanticConfig.from_env()
    service = SemanticSearchService(config)

    with psycopg.connect(args.dsn) as connection:
        if args.migrate:
            apply_migrations(connection)
        chunks = build_semantic_chunks(connection)
        if not chunks:
            raise SystemExit("No semantic chunks were generated")
        run_id = start_run(connection, config, fingerprint(chunks, config))
        old_ids = current_point_ids(connection)
        try:
            indexed = service.index_chunks(chunks, batch_size=args.batch_size)
            new_ids = {str(chunk.point_id) for chunk in chunks}
            stale_ids = old_ids - new_ids
            service.delete_points(stale_ids)
            source_counts = Counter(chunk.source_type for chunk in chunks)
            stats = {
                "chunk_count": len(chunks),
                "indexed_point_count": indexed,
                "deleted_stale_point_count": len(stale_ids),
                "source_counts": dict(sorted(source_counts.items())),
            }
            persist_chunks(connection, chunks, config, run_id, stats)
        except Exception as exc:
            fail_run(connection, run_id, exc)
            raise

    report = {
        "schema_version": "cad-semantic-index-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "backend": "qdrant-local" if not config.qdrant_url else "qdrant-server",
        "collection": config.collection_name,
        "embedding_model": config.embedding_model,
        "embedding_dimensions": config.embedding_dimensions,
        "index_run_id": str(run_id),
        **stats,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
