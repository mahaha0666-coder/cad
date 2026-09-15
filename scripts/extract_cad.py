#!/usr/bin/env python3
"""Extract a small, stable CAD-oriented representation from LibreDWG JSON.

The input format is intentionally inspected at runtime: LibreDWG stores table
records in OBJECTS using ``object`` and drawing entities using ``entity``.
Only fields useful for the first CAD knowledge-base layer are copied out.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any


SUPPORTED_ENTITY_TYPES = {
    "LINE",
    "CIRCLE",
    "ARC",
    "POLYLINE",
    "POLYLINE_2D",
    "POLYLINE_3D",
    "LWPOLYLINE",
    "TEXT",
    "MTEXT",
    "DIMENSION",
    "DIMENSION_ALIGNED",
    "DIMENSION_LINEAR",
    "DIMENSION_ANG2LN",
    "DIMENSION_ANG3PT",
    "DIMENSION_RADIUS",
    "DIMENSION_DIAMETER",
    "DIMENSION_ORDINATE",
    "INSERT",
    "MINSERT",
}

DIMENSION_TYPES = {
    name
    for name in SUPPORTED_ENTITY_TYPES
    if name.startswith("DIMENSION")
}


def handle_tail(value: Any) -> int | None:
    """Return the numeric object-handle part of a LibreDWG handle/ref array."""

    if isinstance(value, list) and value:
        candidate = value[-1]
        if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
            return int(candidate)
    return None


def handle_id(value: Any) -> str | None:
    tail = handle_tail(value)
    if tail is None:
        return None
    return f"H{tail:X}"


def point(value: Any) -> list[Any] | None:
    if isinstance(value, list) and len(value) >= 2:
        return value[:3]
    return None


def first_present(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def put_if_present(target: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        target[key] = value


def layer_name(raw_layer: Any, layers_by_handle: dict[int, str]) -> str | None:
    if isinstance(raw_layer, str):
        return raw_layer
    tail = handle_tail(raw_layer)
    return layers_by_handle.get(tail) if tail is not None else None


def add_common_entity_fields(
    source: dict[str, Any],
    normalized_type: str,
    layers_by_handle: dict[int, str],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": handle_id(source.get("handle")) or f"index:{source.get('index', 'unknown')}",
        "type": normalized_type,
    }
    if "handle" in source:
        result["handle"] = source["handle"]
    if "index" in source:
        result["source_index"] = source["index"]

    raw_layer = source.get("layer")
    put_if_present(result, "layer", layer_name(raw_layer, layers_by_handle))
    if isinstance(raw_layer, list):
        result["layer_handle"] = raw_layer
    return result


def extract_polyline_points(source: dict[str, Any]) -> Any:
    # LWPOLYLINE uses ``points`` in the observed LibreDWG schema.  The
    # fallbacks make this safe for older/newer exports without inventing data.
    return first_present(source, "points", "vertices", "vertex")


def extract_entity(
    source: dict[str, Any],
    layers_by_handle: dict[int, str],
    blocks_by_handle: dict[int, dict[str, Any]],
) -> dict[str, Any] | None:
    raw_type = source.get("entity")
    if not isinstance(raw_type, str) or raw_type not in SUPPORTED_ENTITY_TYPES:
        return None

    if raw_type in DIMENSION_TYPES:
        normalized_type = "DIMENSION"
    elif raw_type in {"POLYLINE_2D", "POLYLINE_3D"}:
        normalized_type = "POLYLINE"
    elif raw_type == "MINSERT":
        normalized_type = "INSERT"
    else:
        normalized_type = raw_type

    result = add_common_entity_fields(source, normalized_type, layers_by_handle)

    if raw_type == "LINE":
        put_if_present(result, "start", point(source.get("start")))
        put_if_present(result, "end", point(source.get("end")))
    elif raw_type == "CIRCLE":
        put_if_present(result, "center", point(source.get("center")))
        put_if_present(result, "radius", source.get("radius"))
    elif raw_type == "ARC":
        put_if_present(result, "center", point(source.get("center")))
        put_if_present(result, "radius", source.get("radius"))
        put_if_present(result, "start_angle", source.get("start_angle"))
        put_if_present(result, "end_angle", source.get("end_angle"))
    elif raw_type in {"POLYLINE", "POLYLINE_2D", "POLYLINE_3D", "LWPOLYLINE"}:
        put_if_present(result, "vertices", extract_polyline_points(source))
        if "flag" in source:
            result["flag"] = source["flag"]
        if "elevation" in source:
            result["elevation"] = source["elevation"]
        # In LibreDWG's LWPOLYLINE flag, bit 512 denotes closed geometry.
        flag = source.get("flag")
        if isinstance(flag, int) and raw_type == "LWPOLYLINE":
            result["closed"] = bool(flag & 512)
    elif raw_type in {"TEXT", "MTEXT"}:
        text_value = first_present(source, "text", "text_value")
        text_position = first_present(source, "ins_pt", "position")
        put_if_present(result, "text", text_value)
        put_if_present(result, "position", point(text_position))
        put_if_present(result, "text_height", source.get("text_height", source.get("height")))
    elif raw_type in DIMENSION_TYPES:
        result["dimension_type"] = raw_type
        put_if_present(result, "text", first_present(source, "user_text", "text"))
        put_if_present(result, "measurement", first_present(source, "act_measurement", "measurement"))
        put_if_present(result, "text_position", point(source.get("text_midpt")))
        raw_block = source.get("block")
        if isinstance(raw_block, list):
            result["block_handle"] = raw_block
            block_id = handle_id(raw_block)
            put_if_present(result, "block_id", block_id)
    elif raw_type in {"INSERT", "MINSERT"}:
        raw_block = first_present(source, "block_header", "block")
        block_id = handle_id(raw_block)
        block = blocks_by_handle.get(handle_tail(raw_block))
        block_name = first_present(source, "block_name", "name")
        if block_name is None and block is not None:
            block_name = block.get("name")
        put_if_present(result, "block_name", block_name)
        put_if_present(result, "position", point(first_present(source, "ins_pt", "position")))
        if isinstance(raw_block, list):
            result["block_handle"] = raw_block
        put_if_present(result, "block_id", block_id)
        put_if_present(result, "scale", source.get("scale"))
        put_if_present(result, "rotation", source.get("rotation"))

    return result


def extract(input_path: Path, output_path: Path, source_file: str | None) -> dict[str, Any]:
    with input_path.open("r", encoding="utf-8") as stream:
        raw_text = stream.read()
        raw_text = re.sub(r"(?<![A-Za-z0-9_])[-+]?nan\b", "null", raw_text, flags=re.IGNORECASE)
        raw_text = re.sub(r"(?<![A-Za-z0-9_])[-+]?(?:inf|infinity)\b", "null", raw_text, flags=re.IGNORECASE)
        raw = json.loads(raw_text, parse_constant=lambda _value: None)

    objects = raw.get("OBJECTS", [])
    if not isinstance(objects, list):
        raise ValueError("LibreDWG JSON does not contain an OBJECTS list")

    layer_records = [obj for obj in objects if obj.get("object") == "LAYER"]
    layers_by_handle = {
        handle_tail(obj.get("handle")): obj.get("name")
        for obj in layer_records
        if handle_tail(obj.get("handle")) is not None and isinstance(obj.get("name"), str)
    }
    layers = []
    for source in layer_records:
        record: dict[str, Any] = {
            "id": handle_id(source.get("handle")) or f"index:{source.get('index', 'unknown')}",
            "name": source.get("name"),
        }
        put_if_present(record, "handle", source.get("handle"))
        put_if_present(record, "source_index", source.get("index"))
        layers.append(record)

    block_records = [obj for obj in objects if obj.get("object") == "BLOCK_HEADER"]
    blocks_by_handle: dict[int, dict[str, Any]] = {}
    blocks = []
    for source in block_records:
        block_id = handle_id(source.get("handle")) or f"index:{source.get('index', 'unknown')}"
        record: dict[str, Any] = {
            "id": block_id,
            "name": source.get("name"),
        }
        put_if_present(record, "handle", source.get("handle"))
        put_if_present(record, "source_index", source.get("index"))
        put_if_present(record, "base_point", point(source.get("base_pt")))
        put_if_present(record, "anonymous", source.get("anonymous"))
        entity_refs = source.get("entities")
        if isinstance(entity_refs, list):
            record["entity_ids"] = [
                entity_id
                for ref in entity_refs
                if (entity_id := handle_id(ref)) is not None
            ]
        for ref_key, output_key in (("block_entity", "block_entity_id"), ("endblk_entity", "endblk_entity_id"), ("layout", "layout_id")):
            ref = source.get(ref_key)
            put_if_present(record, output_key, handle_id(ref))
        blocks.append(record)
        tail = handle_tail(source.get("handle"))
        if tail is not None:
            blocks_by_handle[tail] = record

    raw_entity_counts = Counter(
        obj.get("entity")
        for obj in objects
        if isinstance(obj.get("entity"), str)
    )
    entities = []
    extracted_entity_counts = Counter()
    for source in objects:
        entity = extract_entity(source, layers_by_handle, blocks_by_handle)
        if entity is not None:
            entities.append(entity)
            extracted_entity_counts[entity["type"]] += 1

    skipped_entity_counts = Counter(raw_entity_counts)
    for raw_type, count in raw_entity_counts.items():
        normalized = "DIMENSION" if raw_type in DIMENSION_TYPES else raw_type
        if raw_type in DIMENSION_TYPES:
            skipped_entity_counts[raw_type] -= count
        elif raw_type in {"POLYLINE_2D", "POLYLINE_3D", "MINSERT"}:
            skipped_entity_counts[raw_type] -= count
        elif raw_type in SUPPORTED_ENTITY_TYPES:
            skipped_entity_counts[raw_type] -= count
    skipped_entity_counts = Counter({key: value for key, value in skipped_entity_counts.items() if value > 0})

    result = {
        "schema_version": "cad-structured-v1",
        "drawing": {
            "source_file": source_file or input_path.stem,
            "source_format": "DWG",
            "parser": "LibreDWG",
        },
        "layers": layers,
        "blocks": blocks,
        "entities": entities,
        "statistics": {
            "raw_entity_counts": dict(sorted(raw_entity_counts.items())),
            "extracted_entity_counts": dict(sorted(extracted_entity_counts.items())),
            "skipped_entity_counts": dict(sorted(skipped_entity_counts.items())),
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_json", type=Path, help="LibreDWG JSON input")
    parser.add_argument("output_json", type=Path, help="structured JSON output")
    parser.add_argument(
        "--source-file",
        help="Original CAD filename/path; only the basename is stored",
    )
    args = parser.parse_args()

    source_file = Path(args.source_file).name if args.source_file else None
    result = extract(args.input_json, args.output_json, source_file)
    print(json.dumps(result["statistics"], ensure_ascii=False, indent=2))
    print(f"layers: {len(result['layers'])}")
    print(f"blocks: {len(result['blocks'])}")
    print(f"entities: {len(result['entities'])}")


if __name__ == "__main__":
    main()
