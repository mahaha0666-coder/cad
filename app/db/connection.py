"""PostgreSQL connection helpers.

The application layer keeps connection ownership outside the repository. This
makes the repository easy to use from a web request, a CLI command, or tests.
"""

from __future__ import annotations

import os
from typing import Any

import psycopg


def connect(dsn: str | None = None) -> psycopg.Connection[Any]:
    """Open a PostgreSQL connection using an explicit DSN or DATABASE_URL."""

    resolved_dsn = dsn or os.getenv("DATABASE_URL", "postgresql:///cad_kb")
    if not resolved_dsn:
        raise ValueError("A PostgreSQL DSN is required; pass dsn or set DATABASE_URL")
    return psycopg.connect(resolved_dsn)
