#!/usr/bin/env python3
"""Build evidence-preserving unified CAD features from CAD, OCR, and Vision."""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any


def apply_matrix(matrix: list[list[float]], point: Any) -> list[float] | None:
    if not isinstance(point, (list, tuple)) or len(point) < 2:
        return None
    x, y = float(point[0]), float(point[1])
    result = [
        matrix[0][0] * x + matrix[0][1] * y + matrix[0][2],
        matrix[1][0] * x + matrix[1][1] * y + matrix[1][2],
        matrix[2][0] * x + matrix[2][1] * y + matrix[2][2],
    ]
    if abs(result[2]) < 1e-12:
        return None
    return [result[0] / result[2], result[1] / result[2]]


def _text_token(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"[^A-Z0-9.+-]", "", str(value).upper())


def _vision_blob(item: dict[str, Any]) -> str:
    values = [
        item.get("observation_type"),
        item.get("description"),
        item.get("uncertainty"),
        json.dumps(item.get("candidate_feature"), ensure_ascii=False),
    ]
    return _text_token(" ".join(str(value) for value in values if value is not None))


def _region_distance(point: list[float] | None, region: Any) -> float | None:
    if point is None or not isinstance(region, (list, tuple)) or len(region) != 4:
        return None
    try:
        x, y = float(point[0]), float(point[1])
        x0, y0, x1, y1 = map(float, region)
    except (TypeError, ValueError):
        return None
    left, right = sorted((x0, x1))
    top, bottom = sorted((y0, y1))
    dx = max(left - x, 0.0, x - right)
    dy = max(top - y, 0.0, y - bottom)
    return math.hypot(dx, dy)


def _vision_match(
    feature: dict[str, Any],
    observation: dict[str, Any],
) -> tuple[float, str] | None:
    observation_type = observation.get("observation_type", "other")
    if observation_type not in {
        "feature_candidate", "leader_relation", "radial_dimension",
        "diameter_dimension", "linear_dimension", "angular_dimension",
        "dimension_annotation", "other",
    }:
        return None
    canonical = feature.get("canonical") or {}
    token = _text_token(canonical.get("text") or canonical.get("text_raw"))
    blob = _vision_blob(observation)
    text_score = 1.0 if token and len(token) >= 2 and token in blob else 0.0
    position = (feature.get("position") or {}).get("pixel_projected")
    if position is None:
        position = (feature.get("position") or {}).get("pixel")
    distance = _region_distance(position, observation.get("region_px"))
    spatial_score = 0.0
    if distance is not None:
        spatial_score = max(0.0, 1.0 - distance / 220.0)

    # Generic observations such as a global radial-dimension summary should
    # not be attached to every dimension just because they mention its value.
    if observation_type == "other" and text_score and distance is not None:
        return None
    dimension_observation = observation_type.endswith("_dimension") or observation_type == "dimension_annotation"
    if feature.get("feature_type") != "dimension_annotation" and (
        observation_type == "leader_relation" or dimension_observation
    ):
        return None

    if text_score >= 1.0 and (distance is None or distance <= 260.0):
        return (0.70 * text_score + 0.30 * spatial_score, "text_and_region")
    if observation_type == "leader_relation" and spatial_score >= 0.35:
        return (0.40 * spatial_score, "region")
    return None


def _add_vision_evidence(
    features: list[dict[str, Any]],
    vision_items: list[dict[str, Any]],
) -> tuple[int, int]:
    candidates: list[tuple[float, str, str, str]] = []
    for feature in features:
        for item in vision_items:
            match = _vision_match(feature, item)
            if match:
                candidates.append((match[0], feature["id"], item["id"], match[1]))
    candidates.sort(reverse=True)

    used_features: set[str] = set()
    used_items: set[str] = set()
    attached = 0
    for score, feature_id, item_id, reason in candidates:
        if feature_id in used_features or item_id in used_items:
            continue
        feature = next(item for item in features if item["id"] == feature_id)
        vision = next(item for item in vision_items if item["id"] == item_id)
        if score < 0.45:
            continue
        feature.setdefault("vision_item_ids", []).append(item_id)
        feature.setdefault("evidence", {})["vision"] = [{
            "item_id": item_id,
            "observation_type": vision.get("observation_type"),
            "description": vision.get("description"),
            "region_px": vision.get("region_px"),
            "model_confidence": vision.get("model_confidence"),
            "match_score": round(score, 6),
            "match_reason": reason,
            "status": vision.get("status", "candidate"),
        }]
        feature.setdefault("fusion", {})["vision_match_score"] = round(score, 6)
        used_features.add(feature_id)
        used_items.add(item_id)
        attached += 1

    vision_only = 0
    for item in vision_items:
        if item["id"] in used_items:
            continue
        region = item.get("region_px")
        pixel_position = None
        if isinstance(region, list) and len(region) == 4:
            pixel_position = [
                (float(region[0]) + float(region[2])) / 2.0,
                (float(region[1]) + float(region[3])) / 2.0,
            ]
        features.append({
            "id": f"UF_{len(features) + 1:04d}",
            "feature_type": "vision_observation",
            "source_entity_ids": [],
            "ocr_item_ids": [],
            "vision_item_ids": [item["id"]],
            "canonical": {
                "text": item.get("description"),
                "text_raw": item.get("description"),
                "dimension_kind": None,
                "value": None,
                "unit": None,
            },
            "position": {"pixel": pixel_position} if pixel_position else {},
            "evidence": {
                "cad_native": None,
                "image_ocr": None,
                "vision": [{
                    "item_id": item["id"],
                    "observation_type": item.get("observation_type"),
                    "description": item.get("description"),
                    "region_px": item.get("region_px"),
                    "model_confidence": item.get("model_confidence"),
                    "status": item.get("status", "candidate"),
                }],
            },
            "fusion": {
                "status": "vision_only",
                "score": item.get("model_confidence"),
                "vision_match_score": None,
            },
        })
        vision_only += 1
    return attached, vision_only


def build(
    native_path: Path,
    ocr_path: Path,
    alignment_path: Path,
    output_path: Path,
    vision_path: Path | None = None,
) -> dict[str, Any]:
    native = json.loads(native_path.read_text(encoding="utf-8"))
    ocr = json.loads(ocr_path.read_text(encoding="utf-8"))
    alignment = json.loads(alignment_path.read_text(encoding="utf-8"))
    vision = (
        json.loads(vision_path.read_text(encoding="utf-8"))
        if vision_path and vision_path.exists()
        else None
    )
    native_by_id = {item["id"]: item for item in native.get("items", [])}
    ocr_by_id = {item["id"]: item for item in ocr.get("items", [])}
    matrix = alignment.get("registration", {}).get("cad_to_image_matrix")
    matches = alignment.get("matches", [])
    used_native: set[str] = set()
    used_ocr: set[str] = set()
    features: list[dict[str, Any]] = []

    def add_feature(
        native_item: dict[str, Any] | None,
        ocr_item: dict[str, Any] | None,
        fusion: dict[str, Any],
    ) -> None:
        feature_index = len(features) + 1
        native_ids = native_item.get("source_entity_ids", []) if native_item else []
        ocr_ids = [ocr_item.get("id")] if ocr_item else []
        parsed = (ocr_item or {}).get("parsed") or (native_item or {}).get("parsed") or {}
        dimension_kind = (native_item or {}).get("dimension_kind") or parsed.get("kind")
        value = parsed.get("value")
        if native_item and native_item.get("parsed"):
            value = native_item["parsed"].get("value")
        text_normalized = (
            (native_item or {}).get("text_normalized")
            or (ocr_item or {}).get("text_normalized")
        )
        text_raw = (native_item or {}).get("text_clean") or (ocr_item or {}).get("text_raw")
        if native_item and native_item.get("entity_type") == "DIMENSION":
            feature_type = "dimension_annotation"
        elif native_item:
            feature_type = "native_text_annotation"
        elif parsed:
            feature_type = "dimension_candidate"
        else:
            feature_type = "ocr_text_observation"

        position: dict[str, Any] = {}
        if native_item:
            position["cad"] = native_item.get("position_cad")
            if matrix:
                position["pixel_projected"] = apply_matrix(
                    matrix, native_item.get("position_cad")
                )
        if ocr_item:
            position["pixel"] = ocr_item.get("center_px")

        features.append({
            "id": f"UF_{feature_index:04d}",
            "feature_type": feature_type,
            "source_entity_ids": native_ids,
            "ocr_item_ids": ocr_ids,
            "vision_item_ids": [],
            "canonical": {
                "text": text_normalized,
                "text_raw": text_raw,
                "dimension_kind": dimension_kind,
                "value": value,
                "unit": parsed.get("unit"),
            },
            "position": position,
            "evidence": {
                "cad_native": {
                    "item_id": native_item.get("id"),
                    "text_raw": native_item.get("text_raw"),
                    "display_text_raw": native_item.get("display_text_raw"),
                    "entity_ids": native_ids,
                } if native_item else None,
                "image_ocr": {
                    "item_id": ocr_item.get("id"),
                    "text_raw": ocr_item.get("text_raw"),
                    "confidence": ocr_item.get("confidence"),
                    "polygon_px": ocr_item.get("polygon_px"),
                } if ocr_item else None,
                "vision": [],
            },
            "fusion": fusion,
        })

    for match in matches:
        native_id = match.get("native_item_id")
        ocr_id = match.get("ocr_item_id")
        if match.get("status") == "ambiguous":
            native_item = native_by_id.get(native_id)
            add_feature(native_item, None, {
                "status": "ambiguous",
                "score": None,
                "candidate_ocr_items": match.get("candidates", []),
            })
            used_native.add(native_id)
            continue
        native_item = native_by_id.get(native_id)
        ocr_item = ocr_by_id.get(ocr_id)
        add_feature(native_item, ocr_item, {
            "status": match.get("status"),
            "score": match.get("score"),
            "text_similarity": match.get("text_similarity"),
            "distance_px": match.get("distance_px"),
        })
        used_native.add(native_id)
        used_ocr.add(ocr_id)

    for item_id, item in native_by_id.items():
        if item_id not in used_native:
            add_feature(item, None, {"status": "cad_only", "score": None})
    for item_id, item in ocr_by_id.items():
        if item_id not in used_ocr:
            add_feature(
                native_item=None,
                ocr_item=item,
                fusion={"status": "ocr_only", "score": item.get("confidence")},
            )

    vision_items = (vision or {}).get("observations", [])
    attached_vision, vision_only = _add_vision_evidence(features, vision_items)

    status_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    for feature in features:
        status = feature["fusion"]["status"]
        kind = feature["feature_type"]
        status_counts[status] = status_counts.get(status, 0) + 1
        type_counts[kind] = type_counts.get(kind, 0) + 1

    output = {
        "schema_version": "cad-unified-features-v2",
        "drawing": native.get("drawing", {}),
        "sources": {
            "structured_json": native.get("source_structured_json"),
            "native_text_json": native_path.name,
            "ocr_json": ocr_path.name,
            "alignment_json": alignment_path.name,
            "vision_json": vision_path.name if vision_path and vision_path.exists() else None,
        },
        "registration": alignment.get("registration"),
        "features": features,
        "statistics": {
            "feature_count": len(features),
            "fusion_status_counts": dict(sorted(status_counts.items())),
            "feature_type_counts": dict(sorted(type_counts.items())),
            "vision_observation_count": len(vision_items),
            "vision_attached_feature_count": attached_vision,
            "vision_only_feature_count": vision_only,
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + chr(10),
        encoding="utf-8",
    )
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("native_json", type=Path)
    parser.add_argument("ocr_json", type=Path)
    parser.add_argument("alignment_json", type=Path)
    parser.add_argument("output_json", type=Path)
    parser.add_argument("--vision-json", type=Path)
    args = parser.parse_args()
    result = build(
        args.native_json,
        args.ocr_json,
        args.alignment_json,
        args.output_json,
        args.vision_json,
    )
    print(json.dumps(result["statistics"], ensure_ascii=False, indent=2))
    print(f"output: {args.output_json}")


if __name__ == "__main__":
    main()
