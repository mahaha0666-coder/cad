#!/usr/bin/env python3
"""Search the local CAD semantic index from the command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from app.semantic import SemanticSearchService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query")
    parser.add_argument("--drawing-key")
    parser.add_argument("--revision-id")
    parser.add_argument("--source-type", choices=["drawing_revision", "unified_feature"])
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--min-score", type=float, default=0.35)
    args = parser.parse_args()
    result = SemanticSearchService().search(
        query=args.query,
        drawing_key=args.drawing_key,
        revision_id=args.revision_id,
        source_type=args.source_type,
        limit=args.limit,
        min_score=args.min_score,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
