"""Write/read repository for asynchronous drawing ingestion jobs."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


class IngestionJobNotFoundError(LookupError):
    """Raised when an upload job does not exist."""


def _jsonable(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


class IngestionRepository:
    """Repository for upload jobs and their initial drawing metadata."""

    def __init__(self, connection: psycopg.Connection[Any]):
        self.connection = connection

    @staticmethod
    def _relative_path(path: Path) -> str:
        project_root = Path(__file__).resolve().parents[2]
        try:
            return path.resolve().relative_to(project_root).as_posix()
        except ValueError:
            return path.resolve().as_posix()

    def create_job(
        self,
        *,
        drawing_key: str,
        title: str,
        dwg_path: Path,
        png_path: Path | None,
        dwg_sha256: str,
        png_sha256: str | None,
        job_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Create a drawing, a new revision, source assets and one job atomically."""

        drawing_id = uuid4()
        revision_id = uuid4()
        job_id = job_id or uuid4()
        with self.connection.transaction():
            with self.connection.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO core.drawings (id, drawing_key, title, drawing_category)
                    VALUES (%s, %s, %s, 'mechanical_2d')
                    ON CONFLICT (drawing_key)
                    DO UPDATE SET title = COALESCE(NULLIF(EXCLUDED.title, ''), core.drawings.title),
                                  updated_at = now()
                    RETURNING id
                    """,
                    (drawing_id, drawing_key, title),
                )
                drawing_id = cur.fetchone()[0]

                cur.execute(
                    """
                    SELECT id, revision_sequence
                    FROM core.drawing_revisions
                    WHERE drawing_id = %s
                    ORDER BY revision_sequence DESC, created_at DESC
                    LIMIT 1
                    FOR UPDATE
                    """,
                    (drawing_id,),
                )
                previous = cur.fetchone()
                revision_sequence = (previous[1] + 1) if previous else 1
                supersedes_id = previous[0] if previous else None
                cur.execute(
                    """
                    INSERT INTO core.drawing_revisions (
                        id, drawing_id, revision_code, revision_sequence,
                        supersedes_id, status, schema_version
                    )
                    VALUES (%s, %s, %s, %s, %s, 'queued', 'cad-structured-v1')
                    RETURNING id
                    """,
                    (revision_id, drawing_id, f"R{revision_sequence}", revision_sequence, supersedes_id),
                )
                revision_id = cur.fetchone()[0]

                self._insert_asset(
                    cur,
                    revision_id=revision_id,
                    role="dwg",
                    path=dwg_path,
                    digest=dwg_sha256,
                )
                if png_path and png_sha256:
                    self._insert_asset(
                        cur,
                        revision_id=revision_id,
                        role="png",
                        path=png_path,
                        digest=png_sha256,
                    )

                cur.execute(
                    """
                    INSERT INTO core.ingestion_jobs (
                        id, drawing_id, revision_id, drawing_key, status,
                        current_stage, progress, source_dwg_path, source_png_path
                    )
                    VALUES (%s, %s, %s, %s, 'queued', 'queued', 0, %s, %s)
                    """,
                    (job_id, drawing_id, revision_id, drawing_key, str(dwg_path), str(png_path) if png_path else None),
                )

        return {
            "job_id": str(job_id),
            "drawing_id": str(drawing_id),
            "revision_id": str(revision_id),
            "drawing_key": drawing_key,
            "status": "queued",
        }

    @staticmethod
    def _insert_asset(
        cur: psycopg.Cursor[Any],
        *,
        revision_id: UUID,
        role: str,
        path: Path,
        digest: str,
    ) -> None:
        import mimetypes

        cur.execute(
            """
            INSERT INTO core.source_assets (
                id, drawing_revision_id, asset_role, relative_path, file_name,
                media_type, file_size, sha256, metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, '{}'::jsonb)
            ON CONFLICT (drawing_revision_id, asset_role, sha256)
            DO UPDATE SET relative_path = EXCLUDED.relative_path,
                          file_name = EXCLUDED.file_name,
                          file_size = EXCLUDED.file_size
            """,
            (
                uuid4(),
                revision_id,
                role,
                IngestionRepository._relative_path(path),
                path.name,
                mimetypes.guess_type(path.name)[0],
                path.stat().st_size,
                digest,
            ),
        )

    def update_job(
        self,
        job_id: str | UUID,
        *,
        status: str | None = None,
        current_stage: str | None = None,
        progress: int | None = None,
        statistics: Mapping[str, Any] | None = None,
        error_message: str | None = None,
        started: bool = False,
        finished: bool = False,
    ) -> None:
        assignments = ["updated_at = now()"]
        params: list[Any] = []
        if status is not None:
            assignments.append("status = %s")
            params.append(status)
        if current_stage is not None:
            assignments.append("current_stage = %s")
            params.append(current_stage)
        if progress is not None:
            assignments.append("progress = %s")
            params.append(progress)
        if statistics is not None:
            assignments.append("statistics = %s")
            params.append(Jsonb(dict(statistics)))
        if error_message is not None:
            assignments.append("error_message = %s")
            params.append(error_message[:4000])
        if started:
            assignments.append("started_at = COALESCE(started_at, now())")
        if finished:
            assignments.append("finished_at = now()")
        params.append(job_id)
        with self.connection.transaction():
            with self.connection.cursor() as cur:
                cur.execute(
                    f"UPDATE core.ingestion_jobs SET {', '.join(assignments)} WHERE id = %s",
                    params,
                )

    def get_job(self, job_id: str | UUID) -> dict[str, Any]:
        with self.connection.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT j.id, j.drawing_id, j.revision_id, j.drawing_key,
                       j.status, j.current_stage, j.progress,
                       j.source_dwg_path, j.source_png_path, j.statistics,
                       j.error_message, j.created_at, j.started_at,
                       j.finished_at, j.updated_at,
                       d.title, r.revision_code, r.revision_sequence,
                       r.status AS revision_status
                FROM core.ingestion_jobs AS j
                JOIN core.drawings AS d ON d.id = j.drawing_id
                JOIN core.drawing_revisions AS r ON r.id = j.revision_id
                WHERE j.id = %s
                """,
                (job_id,),
            )
            row = cur.fetchone()
        if not row:
            raise IngestionJobNotFoundError(f"No ingestion job found for id={job_id}")
        return _jsonable(dict(row))
