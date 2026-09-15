"""End-to-end processing for uploaded DWG/PNG drawing pairs.

The local demo uses FastAPI BackgroundTasks. Each stage writes an artifact to
the upload directory, imports its structured result into PostgreSQL, and
updates one durable ingestion job row. A production deployment can move the
same ``run_upload_pipeline`` function to Celery/RQ/Arq without changing the
HTTP contract.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import UploadFile
from dotenv import load_dotenv

from app.db.connection import connect
from app.repositories.ingestion_repository import IngestionRepository
from app.semantic.indexing import index_revision


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")
ALLOWED_DWG_SUFFIXES = {".dwg"}
ALLOWED_PNG_SUFFIXES = {".png"}
DEFAULT_MAX_DWG_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_PNG_BYTES = 40 * 1024 * 1024


class UploadValidationError(ValueError):
    """Raised when the uploaded file pair is invalid."""


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise UploadValidationError(f"{name} must be an integer") from exc
    if value <= 0:
        raise UploadValidationError(f"{name} must be positive")
    return value


def _safe_filename(filename: str | None, suffixes: set[str], label: str) -> str:
    name = Path(filename or "").name
    if not name or name in {".", ".."}:
        raise UploadValidationError(f"{label} filename is required")
    if Path(name).suffix.lower() not in suffixes:
        expected = ", ".join(sorted(suffixes))
        raise UploadValidationError(f"{label} must use one of: {expected}")
    return name


def _slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "drawing"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


async def _read_upload(upload: UploadFile, *, max_bytes: int, label: str) -> bytes:
    payload = await upload.read()
    if not payload:
        raise UploadValidationError(f"{label} is empty")
    if len(payload) > max_bytes:
        raise UploadValidationError(f"{label} exceeds the {max_bytes} byte limit")
    return payload


def _upload_root() -> Path:
    configured = Path(os.getenv("UPLOAD_ROOT", "data/uploads"))
    return configured if configured.is_absolute() else PROJECT_ROOT / configured


async def create_upload_job(
    *,
    dwg: UploadFile,
    png: UploadFile | None,
    drawing_key: str | None,
    title: str | None,
) -> dict[str, Any]:
    """Persist source files and create the durable job before background work."""

    dwg_name = _safe_filename(dwg.filename, ALLOWED_DWG_SUFFIXES, "DWG")
    png_name = _safe_filename(png.filename, ALLOWED_PNG_SUFFIXES, "PNG") if png else None
    dwg_bytes = await _read_upload(
        dwg,
        max_bytes=_env_int("UPLOAD_MAX_DWG_BYTES", DEFAULT_MAX_DWG_BYTES),
        label="DWG",
    )
    png_bytes = (
        await _read_upload(
            png,
            max_bytes=_env_int("UPLOAD_MAX_PNG_BYTES", DEFAULT_MAX_PNG_BYTES),
            label="PNG",
        )
        if png
        else None
    )

    normalized_key = _slugify(drawing_key or Path(dwg_name).stem)
    normalized_title = (title or Path(dwg_name).stem).strip()[:255] or normalized_key
    # Generate the directory name before creating the database row. The
    # repository receives only paths and digests, so the transaction remains
    # small and no uploaded content is placed in PostgreSQL.
    import uuid

    job_id = uuid.uuid4()
    source_dir = _upload_root() / str(job_id) / "source"
    source_dir.mkdir(parents=True, exist_ok=False)
    dwg_path = source_dir / dwg_name
    dwg_path.write_bytes(dwg_bytes)
    png_path = source_dir / png_name if png_name else None
    if png_path and png_bytes is not None:
        png_path.write_bytes(png_bytes)

    try:
        with connect() as connection:
            repository = IngestionRepository(connection)
            result = repository.create_job(
                drawing_key=normalized_key,
                title=normalized_title,
                dwg_path=dwg_path,
                png_path=png_path,
                dwg_sha256=_sha256(dwg_path),
                png_sha256=_sha256(png_path) if png_path else None,
                job_id=job_id,
            )
        return {**result, "upload_directory": str(source_dir.parent)}
    except Exception:
        # A database failure must not leave a partially saved source pair.
        # This directory is an exact per-job target, not a broad cleanup.
        for path in sorted(source_dir.iterdir(), reverse=True):
            path.unlink()
        source_dir.rmdir()
        source_dir.parent.rmdir()
        raise


def get_upload_job(job_id: str | UUID) -> dict[str, Any]:
    with connect() as connection:
        return IngestionRepository(connection).get_job(job_id)


def _run_command(command: list[str], *, timeout: int = 900) -> str:
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        timeout=timeout,
        cwd=PROJECT_ROOT,
    )
    if result.returncode != 0:
        command_text = " ".join(command)
        raise RuntimeError(
            f"command failed ({result.returncode}): {command_text}\n"
            f"stdout: {result.stdout[-4000:]}\n"
            f"stderr: {result.stderr[-4000:]}"
        )
    return result.stdout


def _write_json(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    json.loads(text)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _set_revision_status(revision_id: str, status: str) -> None:
    with connect() as connection:
        with connection.transaction():
            connection.execute(
                "UPDATE core.drawing_revisions SET status = %s WHERE id = %s",
                (status, revision_id),
            )


def _stage(
    repository: IngestionRepository,
    job_id: str,
    statistics: dict[str, Any],
    name: str,
    progress: int,
) -> None:
    repository.update_job(
        job_id,
        status="processing",
        current_stage=name,
        progress=progress,
        statistics=statistics,
        started=True,
    )


def _artifact_paths(job: dict[str, Any]) -> dict[str, Path]:
    dwg = Path(job["source_dwg_path"])
    root = dwg.parent.parent
    stem = dwg.stem
    return {
        "root": root,
        "dwg": dwg,
        "png": Path(job["source_png_path"]) if job.get("source_png_path") else None,
        "raw": root / "raw" / f"{stem}_raw.json",
        "structured": root / "structured" / f"{stem}_structured.json",
        "native": root / "ocr" / "normalized" / f"{stem}_native_text.json",
        "ocr": root / "ocr" / "raw" / f"{stem}_ocr.json",
        "visualized": root / "ocr" / "visualized" / f"{stem}_ocr_visualized.png",
        "vision": root / "vision" / "normalized" / f"{stem}_vision.json",
        "vision_raw": root / "vision" / "raw" / f"{stem}_vision_raw.json",
        "alignment": root / "aligned" / f"{stem}_alignment.json",
        "unified": root / "unified" / f"{stem}_unified_features.json",
    }


def run_upload_pipeline(job_id: str | UUID) -> None:
    """Run all enabled stages for one queued upload job."""

    current_stage = "queued"
    revision_id_for_failure: str | None = None
    statistics: dict[str, Any] = {"warnings": []}
    try:
        with connect() as connection:
            repository = IngestionRepository(connection)
            job = repository.get_job(job_id)
            paths = _artifact_paths(job)
            drawing_key = job["drawing_key"]
            revision_id = job["revision_id"]
            revision_id_for_failure = str(revision_id)

            current_stage = "cad_parse"
            _stage(repository, str(job_id), statistics, current_stage, 10)
            dwgread = Path(os.getenv("DWGREAD_PATH", "libredwg-build/dwgread"))
            if not dwgread.is_absolute():
                dwgread = PROJECT_ROOT / dwgread
            if not dwgread.is_file():
                raise RuntimeError(f"LibreDWG dwgread not found: {dwgread}")
            raw_stdout = _run_command([str(dwgread), "-O", "JSON", str(paths["dwg"])])
            _write_json(paths["raw"], raw_stdout)

            scripts_dir = PROJECT_ROOT / "scripts"
            sys.path.insert(0, str(scripts_dir))
            from extract_cad import extract
            from normalize_cad_text import normalize

            extract(paths["raw"], paths["structured"], source_file=paths["dwg"].name)
            normalize(paths["structured"], paths["native"])
            statistics["cad"] = _load_json(paths["structured"]).get("statistics", {})
            statistics["native"] = _load_json(paths["native"]).get("statistics", {})
            repository.update_job(str(job_id), statistics=statistics)

            png_path = paths["png"]
            if png_path:
                current_stage = "ocr"
                _stage(repository, str(job_id), statistics, current_stage, 30)
                from process_cad_ocr_batch import run_image_ocr
                import run_ocr

                engine = run_ocr.RapidOCR()
                ocr_document = run_image_ocr(
                    engine,
                    png_path,
                    paths["ocr"],
                    paths["visualized"],
                    paths["native"],
                )
                ocr_document["drawing_key"] = drawing_key
                paths["ocr"].write_text(
                    json.dumps(ocr_document, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                statistics["ocr"] = ocr_document.get("statistics", {})
                repository.update_job(str(job_id), statistics=statistics)

                run_vision = os.getenv("UPLOAD_RUN_VISION", "true").lower() in {
                    "1", "true", "yes", "on"
                }
                if run_vision and os.getenv("DEEPSEEK_API_KEY", "").strip():
                    current_stage = "vision"
                    _stage(repository, str(job_id), statistics, current_stage, 45)
                    _run_command(
                        [
                            sys.executable,
                            str(scripts_dir / "run_vision.py"),
                            str(png_path),
                            "--drawing-key",
                            drawing_key,
                            "--output",
                            str(paths["vision"]),
                            "--raw-output",
                            str(paths["vision_raw"]),
                        ],
                        timeout=300,
                    )
                    # Vision is imported before fusion so feature evidence can
                    # be linked during the same revision import transaction.
                    dsn = os.getenv("DATABASE_URL", "postgresql:///cad_kb")
                    _run_command(
                        [
                            sys.executable,
                            str(scripts_dir / "db" / "import_vision.py"),
                            str(paths["vision"]),
                            "--raw-json",
                            str(paths["vision_raw"]),
                            "--dsn",
                            dsn,
                        ],
                        timeout=120,
                    )
                    statistics["vision"] = _load_json(paths["vision"]).get("statistics", {})
                else:
                    reason = "disabled by UPLOAD_RUN_VISION" if not run_vision else "DEEPSEEK_API_KEY is not configured"
                    statistics["warnings"].append(f"vision skipped: {reason}")
                repository.update_job(str(job_id), statistics=statistics)

                current_stage = "fusion"
                _stage(repository, str(job_id), statistics, current_stage, 58)
                align_command = [
                    sys.executable,
                    str(scripts_dir / "align_cad_ocr.py"),
                    str(paths["native"]),
                    str(paths["ocr"]),
                    str(paths["alignment"]),
                    "--image",
                    str(png_path),
                ]
                _run_command(align_command)
                fusion_command = [
                    sys.executable,
                    str(scripts_dir / "build_unified_features.py"),
                    str(paths["native"]),
                    str(paths["ocr"]),
                    str(paths["alignment"]),
                    str(paths["unified"]),
                ]
                if paths["vision"].exists():
                    fusion_command.extend(["--vision-json", str(paths["vision"])])
                _run_command(fusion_command)
                statistics["alignment"] = _load_json(paths["alignment"]).get("statistics", {})
                statistics["fusion"] = _load_json(paths["unified"]).get("statistics", {})
                repository.update_job(str(job_id), statistics=statistics)
            else:
                statistics["warnings"].append("PNG not supplied; OCR, Vision, alignment and fusion were skipped")
                repository.update_job(str(job_id), statistics=statistics)

            current_stage = "postgresql_import"
            _stage(repository, str(job_id), statistics, current_stage, 72)
            sys.path.insert(0, str(scripts_dir / "db"))
            import import_all

            record: dict[str, Any] = {
                "structured": paths["structured"],
                "dwg": paths["dwg"],
                "png": paths["png"],
                "native": paths["native"],
                "ocr": paths["ocr"] if paths["ocr"].exists() else None,
                "alignment": paths["alignment"] if paths["alignment"].exists() else None,
                "vision": paths["vision"] if paths["vision"].exists() else None,
                "unified": paths["unified"] if paths["unified"].exists() else None,
                "drawing_key": drawing_key,
                "revision_id": revision_id,
            }
            import_result = import_all.import_record(connection, PROJECT_ROOT, record)
            statistics["postgresql"] = import_result
            repository.update_job(str(job_id), statistics=statistics)

            current_stage = "semantic_index"
            _stage(repository, str(job_id), statistics, current_stage, 90)
            semantic_result = index_revision(connection, str(revision_id))
            statistics["semantic"] = semantic_result
            repository.update_job(str(job_id), statistics=statistics)

            _set_revision_status(str(revision_id), "processed")
            repository.update_job(
                str(job_id),
                status="completed",
                current_stage="completed",
                progress=100,
                statistics=statistics,
                finished=True,
            )
    except Exception as exc:
        LOGGER.exception("Drawing ingestion failed for job %s", job_id)
        statistics["error_type"] = type(exc).__name__
        try:
            with connect() as connection:
                repository = IngestionRepository(connection)
                repository.update_job(
                    str(job_id),
                    status="failed",
                    current_stage=current_stage,
                    statistics=statistics,
                    error_message=str(exc),
                    finished=True,
                )
                if revision_id_for_failure:
                    with connection.transaction():
                        connection.execute(
                            "UPDATE core.drawing_revisions SET status = 'failed' WHERE id = %s",
                            (revision_id_for_failure,),
                        )
        except Exception:
            LOGGER.exception("Could not persist failed ingestion status for job %s", job_id)

