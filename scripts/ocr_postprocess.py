#!/usr/bin/env python3
"""Post-processing for conservative OCR box fusion."""

from __future__ import annotations

import re
from typing import Any

import numpy as np

from cad_ocr_common import normalize_engineering_text, parse_engineering_text


ENGINEERING_PREFIXES = {"R", "Ø", "±"}


def _box(item: dict[str, Any]) -> tuple[float, float, float, float]:
    points = np.asarray(item.get("polygon_px", []), dtype=float).reshape(-1, 2)
    if len(points) == 0:
        center = item.get("center_px", [0.0, 0.0])
        x, y = float(center[0]), float(center[1])
        return x, y, x, y
    return float(points[:, 0].min()), float(points[:, 1].min()), float(points[:, 0].max()), float(points[:, 1].max())


def _numeric_text(text: str) -> bool:
    return bool(re.match(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)", text))


def _merge_candidate(prefix: dict[str, Any], numeric: dict[str, Any]) -> dict[str, Any] | None:
    prefix_text = prefix.get("text_normalized", "")
    numeric_text = numeric.get("text_normalized", "")
    if prefix_text not in ENGINEERING_PREFIXES or not _numeric_text(numeric_text):
        return None

    px0, py0, px1, py1 = _box(prefix)
    nx0, ny0, nx1, ny1 = _box(numeric)
    pcx, pcy = (px0 + px1) / 2.0, (py0 + py1) / 2.0
    ncx, ncy = (nx0 + nx1) / 2.0, (ny0 + ny1) / 2.0
    pwidth, pheight = max(px1 - px0, 1.0), max(py1 - py0, 1.0)
    nwidth, nheight = max(nx1 - nx0, 1.0), max(ny1 - ny0, 1.0)
    max_width = max(pwidth, nwidth)
    max_height = max(pheight, nheight)

    vertical_gap = max(0.0, max(py0, ny0) - min(py1, ny1))
    horizontal_gap = max(0.0, max(px0, nx0) - min(px1, nx1))
    vertical = (
        abs(pcx - ncx) <= max(24.0, 0.45 * max_width)
        and vertical_gap <= max(16.0, 0.55 * max_height)
        and abs(pcy - ncy) <= max(80.0, 2.5 * max_height)
    )
    horizontal = (
        abs(pcy - ncy) <= max(16.0, 0.55 * max_height)
        and horizontal_gap <= max(24.0, 1.8 * max_height)
        and abs(pcx - ncx) <= max(100.0, 3.0 * max_width)
    )
    if not vertical and not horizontal:
        return None

    if vertical and (not horizontal or abs(pcx - ncx) <= abs(pcy - ncy)):
        layout = "vertical"
        ordered = sorted((prefix, numeric), key=lambda item: item["center_px"][1])
        gap = vertical_gap
    else:
        layout = "horizontal"
        ordered = sorted((prefix, numeric), key=lambda item: item["center_px"][0])
        gap = horizontal_gap

    return {
        "prefix": prefix,
        "numeric": numeric,
        "layout": layout,
        "ordered": ordered,
        "gap_px": gap,
        "distance_px": float(np.hypot(pcx - ncx, pcy - ncy)),
    }


def _merge_items(prefix: dict[str, Any], numeric: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    ordered = candidate["ordered"]
    source_ids = [item["id"] for item in ordered]
    raw_parts = [str(item.get("text_raw", "")) for item in ordered]
    combined_raw = "".join(raw_parts)
    combined_normalized = normalize_engineering_text(combined_raw)
    points = np.concatenate([
        np.asarray(item.get("polygon_px", []), dtype=float).reshape(-1, 2)
        for item in ordered
    ])
    min_x, min_y = points.min(axis=0)
    max_x, max_y = points.max(axis=0)
    polygon = [[float(min_x), float(min_y)], [float(max_x), float(min_y)], [float(max_x), float(max_y)], [float(min_x), float(max_y)]]
    confidence = min(float(item.get("confidence", 0.0)) for item in ordered)
    merged = {
        "id": "OCR_MERGED_" + "_".join(source_ids),
        "text_raw": combined_raw,
        "text_normalized": combined_normalized,
        "polygon_px": polygon,
        "center_px": [float((min_x + max_x) / 2.0), float((min_y + max_y) / 2.0)],
        "confidence": confidence,
        "source_item_ids": source_ids,
        "text_parts_raw": raw_parts,
        "merge": {
            "merged": True,
            "reason": "engineering_prefix_plus_adjacent_numeric",
            "layout": candidate["layout"],
            "gap_px": candidate["gap_px"],
            "distance_px": candidate["distance_px"],
        },
    }
    parsed = parse_engineering_text(combined_raw)
    if parsed is not None:
        merged["parsed"] = parsed
    return merged


def merge_adjacent_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge only a prefix token with one nearby numeric OCR box.

    The operation is deliberately conservative: two numeric boxes are never
    merged, and all merged item IDs/text parts are retained for traceability.
    """

    remaining = list(items)
    merged_items: list[dict[str, Any]] = []
    while remaining:
        current = remaining.pop(0)
        if current.get("text_normalized") not in ENGINEERING_PREFIXES:
            merged_items.append(current)
            continue
        candidates = []
        for index, other in enumerate(remaining):
            candidate = _merge_candidate(current, other)
            if candidate is not None:
                candidates.append((candidate["distance_px"], index, candidate))
        if not candidates:
            merged_items.append(current)
            continue
        _, index, candidate = min(candidates, key=lambda value: value[0])
        numeric = remaining.pop(index)
        merged_items.append(_merge_items(current, numeric, candidate))

    return merged_items
