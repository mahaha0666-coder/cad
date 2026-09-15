#!/usr/bin/env python3
"""Run RapidOCR on the dataset's AutoCAD canvas and preserve source pixels."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path

import cv2
import numpy as np

import run_ocr
from ocr_postprocess import merge_adjacent_items


def move_items_to_source_space(items: list[dict], offset_x: int, offset_y: int) -> None:
    for item in items:
        polygon = np.asarray(item["polygon_px"], dtype=float).reshape(-1, 2)
        polygon[:, 0] += offset_x
        polygon[:, 1] += offset_y
        item["polygon_px"] = polygon.tolist()
        item["center_px"] = polygon.mean(axis=0).tolist()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("output_json", type=Path)
    parser.add_argument("--visualized", type=Path)
    parser.add_argument("--reference-cad", type=Path)
    parser.add_argument("--preprocess", choices=["raw", "gray", "invert", "auto"], default="auto")
    parser.add_argument("--crop", choices=["autocad-canvas", "none"], default="autocad-canvas")
    args = parser.parse_args()

    source = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
    if source is None:
        raise SystemExit(f"Could not read image: {args.image}")
    source_height, source_width = source.shape[:2]
    if args.crop == "autocad-canvas":
        crop_box = (0, 60, min(1750, source_width), min(1025, source_height))
    else:
        crop_box = (0, 0, source_width, source_height)
    x0, y0, x1, y1 = crop_box
    working = source[y0:y1, x0:x1]

    reference = run_ocr.load_reference_texts(args.reference_cad)
    engine = run_ocr.RapidOCR()
    modes = ["raw", "gray", "invert"] if args.preprocess == "auto" else [args.preprocess]
    candidates = []
    for mode in modes:
        variant = run_ocr.run_variant(engine, working, mode)
        move_items_to_source_space(variant["items"], x0, y0)
        variant["items"] = merge_adjacent_items(variant["items"])
        candidates.append({
            "mode": mode,
            "items": variant["items"],
            "image": variant["image"],
            "comparison": run_ocr.candidate_score(variant["items"], reference),
        })
    selected = max(
        candidates,
        key=lambda candidate: (
            candidate["comparison"]["selection_score"],
            len(candidate["items"]),
        ),
    ) if reference else candidates[0]

    output = {
        "schema_version": "cad-ocr-v1",
        "drawing_key": args.image.stem,
        "image": {
            "source_file": args.image.name,
            "width": source_width,
            "height": source_height,
            "coordinate_system": "pixel_top_left",
        },
        "engine": {
            "name": "RapidOCR",
            "version": importlib.metadata.version("rapidocr"),
            "backend": "onnxruntime-cpu",
            "onnxruntime_version": importlib.metadata.version("onnxruntime"),
        },
        "preprocessing": {
            "selected": selected["mode"],
            "crop": {
                "name": args.crop,
                "box_px": [x0, y0, x1, y1],
                "working_size": [x1 - x0, y1 - y0],
            },
            "alpha_handling": "source loaded as BGR; source alpha was not used",
        },
        "items": selected["items"],
        "statistics": {
            "item_count": len(selected["items"]),
            "high_confidence_count": sum(item["confidence"] >= 0.8 for item in selected["items"]),
        },
        "comparison": {
            "reference_file": args.reference_cad.name if args.reference_cad else None,
            "candidates": [
                {"mode": candidate["mode"], **candidate["comparison"]}
                for candidate in candidates
            ],
        },
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.visualized:
        run_ocr.draw_visual(source, selected["items"], args.visualized)
    print(json.dumps(output["statistics"], ensure_ascii=False, indent=2))
    print(f"selected_preprocess: {selected['mode']}")
    print(f"output: {args.output_json}")
    if args.visualized:
        print(f"visualized: {args.visualized}")


if __name__ == "__main__":
    main()
