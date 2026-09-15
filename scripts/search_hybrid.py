#!/usr/bin/env python3
"""Run Qdrant recall plus PostgreSQL exact verification from the CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db.connection import connect  # noqa: E402
from app.repositories import DrawingRepository  # noqa: E402
from app.retrieval import HybridRetrievalService  # noqa: E402
from app.semantic import SemanticSearchService  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="自然语言 CAD 检索问题")
    parser.add_argument("--drawing-key")
    parser.add_argument("--revision-id")
    parser.add_argument("--top-k", type=int, default=30)
    parser.add_argument("--final-limit", type=int, default=10)
    parser.add_argument("--min-score", type=float, default=0.35)
    parser.add_argument("--dimension-kind", choices=["linear", "radius", "diameter", "angle"])
    parser.add_argument("--dimension-value", type=float)
    parser.add_argument("--tolerance", type=float, default=0.01)
    args = parser.parse_args()
    if args.drawing_key and args.revision_id:
        parser.error("--drawing-key and --revision-id cannot be used together")

    with connect() as connection:
        result = HybridRetrievalService(SemanticSearchService()).search(
            DrawingRepository(connection),
            query=args.query,
            drawing_key=args.drawing_key,
            revision_id=args.revision_id,
            top_k=args.top_k,
            final_limit=args.final_limit,
            min_score=args.min_score,
            dimension_kind=args.dimension_kind,
            dimension_value=args.dimension_value,
            tolerance=args.tolerance,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
