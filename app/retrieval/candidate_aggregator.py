"""Collapse chunk-level Qdrant hits into drawing-revision candidates."""

from __future__ import annotations

from collections import OrderedDict
from typing import Any


def aggregate_semantic_hits(items: list[dict[str, Any]], *, hits_per_candidate: int = 5) -> list[dict[str, Any]]:
    groups: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for item in sorted(items, key=lambda value: float(value.get("score", 0.0)), reverse=True):
        revision_id = item.get("revision_id")
        if not revision_id:
            continue
        group = groups.setdefault(
            str(revision_id),
            {
                "revision_id": str(revision_id),
                "drawing_key": item.get("drawing_key"),
                "drawing_title": item.get("drawing_title"),
                "semantic_score": 0.0,
                "source_types": [],
                "source_feature_ids": [],
                "semantic_hits": [],
            },
        )
        score = float(item.get("score", 0.0))
        group["semantic_score"] = max(group["semantic_score"], score)
        source_type = item.get("source_type")
        if source_type and source_type not in group["source_types"]:
            group["source_types"].append(source_type)
        source_id = item.get("source_id")
        if source_type == "unified_feature" and source_id and source_id not in group["source_feature_ids"]:
            group["source_feature_ids"].append(source_id)
        if len(group["semantic_hits"]) < hits_per_candidate:
            group["semantic_hits"].append(item)
    return list(groups.values())
