"""Shared helpers for importing the CAD demo data into PostgreSQL."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb


def new_id() -> uuid.UUID:
    return uuid.uuid4()


def as_json(value: Any) -> Jsonb:
    return Jsonb(value)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "drawing"


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def xyz(value: Any) -> list[float] | None:
    if value is None:
        return None
    values = list(value)
    if len(values) < 3:
        values.extend([0.0] * (3 - len(values)))
    return [float(values[0]), float(values[1]), float(values[2])]


def xy(value: Any) -> list[float] | None:
    if value is None:
        return None
    values = list(value)
    if len(values) < 2:
        return None
    return [float(values[0]), float(values[1])]


def flatten_matrix(value: Any) -> list[float]:
    flattened = [float(item) for row in value for item in row]
    if len(flattened) != 9:
        raise ValueError(f"Expected a 3x3 affine matrix, got {len(flattened)} values")
    return flattened


def find_dataset_file(dataset_dir: Path, base_name: str, suffix: str) -> Path | None:
    exact = dataset_dir / f"{base_name}{suffix}"
    if exact.exists():
        return exact

    wanted = f"{base_name}{suffix}".casefold()
    for candidate in dataset_dir.iterdir():
        if candidate.is_file() and candidate.name.casefold() == wanted:
            return candidate
    return None
