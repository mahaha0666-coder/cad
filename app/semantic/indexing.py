"""Incremental semantic indexing for one drawing revision."""

from __future__ import annotations

import hashlib
import uuid
from collections import Counter
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from .chunks import SemanticChunk, build_semantic_chunks
from .config import SemanticConfig
from .service import SemanticSearchService


def _fingerprint(chunks: list[SemanticChunk], config: SemanticConfig) -> str:
    digest = hashlib.sha256()
    digest.update(config.collection_name.encode())
    digest.update(config.embedding_model.encode())
    for chunk in chunks:
        digest.update(str(chunk.id).encode())
        digest.update(chunk.content_hash.encode())
    return digest.hexdigest()


def _start_run(
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
            DO UPDATE SET status='running', error_message=NULL,
                          started_at=now(), finished_at=NULL
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
        return cur.fetchone()[0]


def _persist_revision_chunks(
    connection: psycopg.Connection[Any],
    revision_id: str,
    chunks: list[SemanticChunk],
    config: SemanticConfig,
    run_id: uuid.UUID,
    statistics: dict[str, Any],
) -> None:
    with connection.cursor() as cur:
        # A rerun of one revision may remove a feature. Deactivate only that
        # revision's previous rows; other drawings remain current.
        cur.execute(
            """
            UPDATE semantic.chunks
            SET is_current = false, updated_at = now()
            WHERE drawing_revision_id = %s
            """,
            (revision_id,),
        )
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
                DO UPDATE SET drawing_revision_id = EXCLUDED.drawing_revision_id,
                              source_id = EXCLUDED.source_id,
                              chunk_kind = EXCLUDED.chunk_kind,
                              content = EXCLUDED.content,
                              content_hash = EXCLUDED.content_hash,
                              metadata = EXCLUDED.metadata,
                              vector_point_id = EXCLUDED.vector_point_id,
                              embedding_model = EXCLUDED.embedding_model,
                              last_index_run_id = EXCLUDED.last_index_run_id,
                              is_current = true,
                              indexed_at = now(),
                              updated_at = now()
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


def index_revision(
    connection: psycopg.Connection[Any],
    revision_id: str,
    *,
    config: SemanticConfig | None = None,
    service: SemanticSearchService | None = None,
) -> dict[str, Any]:
    """Build, embed and persist only chunks belonging to one revision."""

    resolved_config = config or SemanticConfig.from_env()
    resolved_service = service or SemanticSearchService(resolved_config)
    chunks = [
        chunk for chunk in build_semantic_chunks(connection)
        if str(chunk.drawing_revision_id) == str(revision_id)
    ]
    if not chunks:
        return {
            "revision_id": str(revision_id),
            "chunk_count": 0,
            "indexed_point_count": 0,
            "source_counts": {},
            "incremental": True,
        }

    run_id = _start_run(connection, resolved_config, _fingerprint(chunks, resolved_config))
    connection.commit()
    try:
        indexed = resolved_service.index_chunks(chunks)
        new_ids = {str(chunk.point_id) for chunk in chunks}
        with connection.cursor() as cur:
            cur.execute(
                """
                SELECT vector_point_id::text
                FROM semantic.chunks
                WHERE drawing_revision_id = %s AND is_current
                """,
                (revision_id,),
            )
            old_ids = {row[0] for row in cur.fetchall()}
        stale_ids = old_ids - new_ids
        resolved_service.delete_points(stale_ids)
        statistics = {
            "revision_id": str(revision_id),
            "chunk_count": len(chunks),
            "indexed_point_count": indexed,
            "deleted_stale_point_count": len(stale_ids),
            "source_counts": dict(sorted(Counter(chunk.source_type for chunk in chunks).items())),
            "incremental": True,
        }
        _persist_revision_chunks(connection, revision_id, chunks, resolved_config, run_id, statistics)
        connection.commit()
        return {"index_run_id": str(run_id), **statistics}
    except Exception as exc:
        connection.rollback()
        with connection.cursor() as cur:
            cur.execute(
                """
                UPDATE semantic.index_runs
                SET status='failed', error_message=%s, finished_at=now()
                WHERE id=%s
                """,
                (str(exc)[:4000], run_id),
            )
        connection.commit()
        raise
