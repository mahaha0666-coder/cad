#!/usr/bin/env python3
"""Normalize native CAD text and associate DIMENSION display MTEXT records."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from cad_ocr_common import (
    clean_cad_text,
    distance2,
    parse_native_dimension_text,
    point2,
    normalize_engineering_text,
)


def collect_bounds(entities: list[dict[str, Any]]) -> list[float] | None:
    points: list[tuple[float, float]] = []

    def add_point(value: Any) -> None:
        point = point2(value)
        if point is not None:
            points.append(point)

    for entity in entities:
        for key in ("start", "end", "center", "position", "text_position"):
            add_point(entity.get(key))
        vertices = entity.get("vertices")
        if isinstance(vertices, list):
            for vertex in vertices:
                if isinstance(vertex, dict):
                    add_point(vertex.get("point"))
                else:
                    add_point(vertex)
        radius = entity.get("radius")
        center = point2(entity.get("center"))
        if center is not None and isinstance(radius, (int, float)) and math.isfinite(float(radius)):
            radius = abs(float(radius))
            points.extend([
                (center[0] - radius, center[1] - radius),
                (center[0] + radius, center[1] + radius),
            ])

    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def normalized_item(entity: dict[str, Any], display_entity: dict[str, Any] | None = None) -> dict[str, Any]:
    entity_type = entity.get("type")
    if entity_type == "DIMENSION":
        display_text = display_entity.get("text") if display_entity else None
        normalized = parse_native_dimension_text(entity, display_text)
        item = {
            "id": f"NATIVE_{entity.get('id', 'unknown')}",
            "source_entity_ids": [entity.get("id")],
            "source_entity_types": ["DIMENSION"],
            "entity_type": "DIMENSION",
            "position_cad": entity.get("text_position"),
            **normalized,
        }
        if display_entity:
            item["source_entity_ids"].append(display_entity.get("id"))
            item["source_entity_types"].append("MTEXT")
            item["display_entity_id"] = display_entity.get("id")
        item["dimension_type"] = entity.get("dimension_type")
        item["block_id"] = entity.get("block_id")
        return item

    raw_text = entity.get("text")
    cleaned = clean_cad_text(raw_text)
    return {
        "id": f"NATIVE_{entity.get('id', 'unknown')}",
        "source_entity_ids": [entity.get("id")],
        "source_entity_types": [entity_type],
        "entity_type": entity_type,
        "position_cad": entity.get("position"),
        "text_raw": raw_text,
        "display_text_raw": None,
        "text_clean": cleaned,
        "text_normalized": normalize_engineering_text(cleaned),
        "dimension_kind": None,
        "measurement_raw": None,
        "parsed": None,
    }


def normalize(input_path: Path, output_path: Path) -> dict[str, Any]:
    structured = json.loads(input_path.read_text(encoding="utf-8"))
    entities = structured.get("entities", [])
    dimensions = [entity for entity in entities if entity.get("type") == "DIMENSION"]
    mtexts = [entity for entity in entities if entity.get("type") == "MTEXT"]
    used_mtext: set[str] = set()
    native_items: list[dict[str, Any]] = []
    linked_count = 0

    for dimension in dimensions:
        position = dimension.get("text_position")
        candidates = []
        for mtext in mtexts:
            mtext_id = mtext.get("id")
            if mtext_id in used_mtext:
                continue
            distance = distance2(position, mtext.get("position"))
            if distance is not None and distance <= 1e-3:
                candidates.append((distance, mtext))
        display_entity = min(candidates, key=lambda pair: pair[0])[1] if candidates else None
        if display_entity is not None:
            used_mtext.add(display_entity.get("id"))
            linked_count += 1
        native_items.append(normalized_item(dimension, display_entity))

    for entity in entities:
        if entity.get("type") in {"TEXT", "MTEXT"} and entity.get("id") not in used_mtext:
            native_items.append(normalized_item(entity))

    output = {
        "schema_version": "cad-native-text-v1",
        "drawing": structured.get("drawing", {}),
        "source_structured_json": input_path.name,
        "cad_coordinate_system": "drawing_engineering_coordinates",
        "cad_bounds": collect_bounds(entities),
        "items": native_items,
        "statistics": {
            "native_text_item_count": len(native_items),
            "dimension_count": len(dimensions),
            "linked_dimension_mtext_count": linked_count,
            "standalone_text_count": sum(item["entity_type"] in {"TEXT", "MTEXT"} for item in native_items),
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_json", type=Path)
    parser.add_argument("output_json", type=Path)
    args = parser.parse_args()
    result = normalize(args.input_json, args.output_json)
    print(json.dumps(result["statistics"], ensure_ascii=False, indent=2))
    print(f"output: {args.output_json}")


if __name__ == "__main__":
    main()
