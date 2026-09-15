#!/usr/bin/env python3
"""Run one exact CAD query tool from the command line.

Example:
  python scripts/query_cad.py count_entities \
    '{"drawing_key":"2d-profile-drawing-with-radial-and-angular-dimension"}'
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db.connection import connect  # noqa: E402
from app.repositories import DrawingRepository  # noqa: E402
from app.services.cad_query_service import CADQueryService  # noqa: E402
from app.tools.cad_query_tools import execute_tool  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tool", help="whitelisted tool name")
    parser.add_argument("arguments", nargs="?", default="{}", help="JSON object of tool arguments")
    parser.add_argument("--dsn", default=os.getenv("DATABASE_URL", "postgresql:///cad_kb"))
    args = parser.parse_args()
    try:
        arguments = json.loads(args.arguments)
        if not isinstance(arguments, dict):
            raise ValueError("arguments must decode to a JSON object")
        with connect(args.dsn) as connection:
            result = execute_tool(CADQueryService(DrawingRepository(connection)), args.tool, arguments)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        print(f"query failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
