#!/usr/bin/env python3
"""Register native CAD text to OCR pixels and associate repeated annotations."""

from __future__ import annotations

import argparse
import json
import math
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from cad_ocr_common import distance2, point2


def image_content_bounds(image_path: Path, width: int, height: int) -> list[float]:
    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return [0.0, 0.0, float(width), float(height)]
    border = np.concatenate([
        image[:20, :].ravel(),
        image[-20:, :].ravel(),
        image[:, :20].ravel(),
        image[:, -20:].ravel(),
    ])
    threshold = max(45.0, float(np.median(border)) + 12.0)
    ys, xs = np.where(image > threshold)
    if len(xs) < 50:
        return [0.0, 0.0, float(width), float(height)]
    return [float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1)]


def initial_matrix(cad_bounds: list[float] | None, pixel_bounds: list[float]) -> np.ndarray:
    if not cad_bounds:
        return np.eye(3, dtype=float)
    min_x, min_y, max_x, max_y = map(float, cad_bounds)
    px0, py0, px1, py1 = map(float, pixel_bounds)
    cad_w = max(max_x - min_x, 1e-9)
    cad_h = max(max_y - min_y, 1e-9)
    sx = (px1 - px0) / cad_w
    sy = (py1 - py0) / cad_h
    return np.array([
        [sx, 0.0, px0 - min_x * sx],
        [0.0, -sy, py0 + max_y * sy],
        [0.0, 0.0, 1.0],
    ])


def apply_matrix(matrix: np.ndarray, point: Any) -> list[float] | None:
    value = point2(point)
    if value is None:
        return None
    transformed = matrix @ np.array([value[0], value[1], 1.0])
    if abs(float(transformed[2])) < 1e-12:
        return None
    return [float(transformed[0] / transformed[2]), float(transformed[1] / transformed[2])]


def exact_unique_anchors(native_items: list[dict[str, Any]], ocr_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    native_by_text: dict[str, list[dict[str, Any]]] = {}
    ocr_by_text: dict[str, list[dict[str, Any]]] = {}
    for item in native_items:
        key = item.get("text_normalized")
        if key:
            native_by_text.setdefault(key, []).append(item)
    for item in ocr_items:
        key = item.get("text_normalized")
        if key:
            ocr_by_text.setdefault(key, []).append(item)
    anchors = []
    for key in sorted(set(native_by_text) & set(ocr_by_text)):
        if len(native_by_text[key]) == 1 and len(ocr_by_text[key]) == 1:
            native = native_by_text[key][0]
            ocr = ocr_by_text[key][0]
            if point2(native.get("position_cad")) and point2(ocr.get("center_px")):
                anchors.append({
                    "text": key,
                    "native_id": native.get("id"),
                    "ocr_id": ocr.get("id"),
                    "cad": native.get("position_cad"),
                    "pixel": ocr.get("center_px"),
                })
    return anchors


def fit_matrix(anchors: list[dict[str, Any]], fallback: np.ndarray) -> tuple[np.ndarray, list[bool]]:
    if len(anchors) < 3:
        return fallback, [True] * len(anchors)
    source = np.asarray([anchor["cad"] for anchor in anchors], dtype=np.float64)
    target = np.asarray([anchor["pixel"] for anchor in anchors], dtype=np.float64)
    fitted, inliers = cv2.estimateAffine2D(
        source,
        target,
        method=cv2.RANSAC,
        ransacReprojThreshold=80.0,
        maxIters=3000,
        confidence=0.99,
    )
    if fitted is None:
        return fallback, [True] * len(anchors)
    matrix = np.array([
        [fitted[0, 0], fitted[0, 1], fitted[0, 2]],
        [fitted[1, 0], fitted[1, 1], fitted[1, 2]],
        [0.0, 0.0, 1.0],
    ])
    flags = [bool(value) for value in inliers.ravel()] if inliers is not None else [True] * len(anchors)
    return matrix, flags


def kind_compatible(native: dict[str, Any], ocr: dict[str, Any]) -> float:
    native_kind = native.get("dimension_kind")
    parsed = ocr.get("parsed") or {}
    ocr_kind = parsed.get("kind")
    if not native_kind or not ocr_kind:
        return 0.5
    if native_kind == ocr_kind or {native_kind, ocr_kind} <= {"linear", "numeric"}:
        return 1.0
    return 0.0


def associate(
    native_items: list[dict[str, Any]],
    ocr_items: list[dict[str, Any]],
    matrix: np.ndarray,
    max_distance: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    for native in native_items:
        projected = apply_matrix(matrix, native.get("position_cad"))
        if projected is None:
            continue
        for ocr in ocr_items:
            similarity = SequenceMatcher(
                None,
                native.get("text_normalized", ""),
                ocr.get("text_normalized", ""),
            ).ratio()
            distance = distance2(projected, ocr.get("center_px"))
            if distance is None or distance > max_distance or similarity < 0.65:
                continue
            distance_score = max(0.0, 1.0 - distance / max_distance)
            score = (
                0.55 * similarity
                + 0.30 * distance_score
                + 0.10 * kind_compatible(native, ocr)
                + 0.05 * float(ocr.get("confidence", 0.0))
            )
            candidates.append({
                "native_item_id": native.get("id"),
                "ocr_item_id": ocr.get("id"),
                "text_similarity": similarity,
                "distance_px": distance,
                "score": score,
            })

    by_native: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        by_native.setdefault(candidate["native_item_id"], []).append(candidate)
    for values in by_native.values():
        values.sort(key=lambda value: value["score"], reverse=True)

    selected: list[dict[str, Any]] = []
    used_ocr: set[str] = set()
    for native_id, values in sorted(by_native.items(), key=lambda pair: pair[1][0]["score"], reverse=True):
        available = [value for value in values if value["ocr_item_id"] not in used_ocr]
        if not available or available[0]["score"] < 0.55:
            continue
        if len(available) > 1 and available[0]["score"] - available[1]["score"] < 0.08:
            selected.append({
                "native_item_id": native_id,
                "status": "ambiguous",
                "candidates": available[:3],
            })
            continue
        match = dict(available[0])
        match["status"] = "confirmed" if match["text_similarity"] >= 0.95 else "conflict"
        selected.append(match)
        used_ocr.add(match["ocr_item_id"])
    return selected, candidates


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("native_json", type=Path)
    parser.add_argument("ocr_json", type=Path)
    parser.add_argument("output_json", type=Path)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--max-distance-px", type=float, default=80.0)
    args = parser.parse_args()

    native = json.loads(args.native_json.read_text(encoding="utf-8"))
    ocr = json.loads(args.ocr_json.read_text(encoding="utf-8"))
    width = int(ocr["image"]["width"])
    height = int(ocr["image"]["height"])
    pixels = image_content_bounds(args.image, width, height) if args.image else [0.0, 0.0, float(width), float(height)]
    fallback = initial_matrix(native.get("cad_bounds"), pixels)
    anchors = exact_unique_anchors(native.get("items", []), ocr.get("items", []))
    matrix, inlier_flags = fit_matrix(anchors, fallback)
    anchor_errors = []
    for anchor in anchors:
        projected = apply_matrix(matrix, anchor["cad"])
        error = distance2(projected, anchor["pixel"])
        anchor_errors.append(error if error is not None else float("inf"))
    inlier_errors = [error for error, flag in zip(anchor_errors, inlier_flags) if flag and math.isfinite(error)]
    rmse = math.sqrt(sum(error * error for error in inlier_errors) / len(inlier_errors)) if inlier_errors else None
    matches, candidates = associate(native.get("items", []), ocr.get("items", []), matrix, args.max_distance_px)
    matched_native = {match.get("native_item_id") for match in matches if match.get("status") != "ambiguous"}
    matched_ocr = {match.get("ocr_item_id") for match in matches if match.get("status") != "ambiguous"}
    native_ids = {item.get("id") for item in native.get("items", [])}
    ocr_ids = {item.get("id") for item in ocr.get("items", [])}

    output = {
        "schema_version": "cad-ocr-alignment-v1",
        "drawing_key": native.get("drawing", {}).get("source_file", args.native_json.stem),
        "registration": {
            "method": "text-anchor-ransac-affine" if len(anchors) >= 3 else "bounds-initial-transform",
            "cad_to_image_matrix": matrix.tolist(),
            "anchor_count": len(anchors),
            "inlier_anchor_count": sum(inlier_flags),
            "rmse_px": rmse,
            "pixel_content_bounds": pixels,
            "status": "registered" if len(anchors) >= 3 else "insufficient_anchors",
        },
        "anchors": [
            {**anchor, "inlier": flag, "error_px": error}
            for anchor, flag, error in zip(anchors, inlier_flags, anchor_errors)
        ],
        "matches": matches,
        "unmatched": {
            "native_item_ids": sorted(native_ids - matched_native),
            "ocr_item_ids": sorted(ocr_ids - matched_ocr),
        },
        "candidate_count": len(candidates),
        "statistics": {
            "confirmed": sum(match.get("status") == "confirmed" for match in matches),
            "conflict": sum(match.get("status") == "conflict" for match in matches),
            "ambiguous": sum(match.get("status") == "ambiguous" for match in matches),
            "cad_only": len(native_ids - matched_native),
            "ocr_only": len(ocr_ids - matched_ocr),
        },
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output["registration"], ensure_ascii=False, indent=2))
    print(json.dumps(output["statistics"], ensure_ascii=False, indent=2))
    print(f"output: {args.output_json}")


if __name__ == "__main__":
    main()
