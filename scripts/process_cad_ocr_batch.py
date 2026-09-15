#!/usr/bin/env python3
"""Process all matching DWG/PNG pairs through the CAD/OCR fusion pipeline."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import cv2

import run_ocr
from cad_ocr_common import normalize_engineering_text
from extract_cad import extract
from normalize_cad_text import normalize
from ocr_postprocess import merge_adjacent_items


def move_items_to_source_space(items: list[dict[str, Any]], offset_x: int, offset_y: int) -> None:
    for item in items:
        polygon = [[float(point[0]) + offset_x, float(point[1]) + offset_y] for point in item["polygon_px"]]
        item["polygon_px"] = polygon
        item["center_px"] = [
            sum(point[0] for point in polygon) / len(polygon),
            sum(point[1] for point in polygon) / len(polygon),
        ]


def load_reference_texts(native_path: Path) -> set[str]:
    data = json.loads(native_path.read_text(encoding="utf-8"))
    return {
        item["text_normalized"]
        for item in data.get("items", [])
        if item.get("text_normalized")
    }


def run_image_ocr(
    engine: Any,
    image_path: Path,
    output_path: Path,
    visualized_path: Path,
    reference_path: Path,
) -> dict[str, Any]:
    source = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if source is None:
        raise RuntimeError(f"Could not read image: {image_path}")
    source_height, source_width = source.shape[:2]
    crop_box = (0, 60, min(1750, source_width), min(1025, source_height))
    x0, y0, x1, y1 = crop_box
    working = source[y0:y1, x0:x1]
    reference = load_reference_texts(reference_path)
    candidates = []
    for mode in ("raw", "gray", "invert"):
        variant = run_ocr.run_variant(engine, working, mode)
        source_count = len(variant["items"])
        move_items_to_source_space(variant["items"], x0, y0)
        items = merge_adjacent_items(variant["items"])
        candidates.append({
            "mode": mode,
            "items": items,
            "image": variant["image"],
            "source_item_count": source_count,
            "merged_item_count": source_count - len(items),
            "comparison": run_ocr.candidate_score(items, reference),
        })
    selected = max(candidates, key=lambda candidate: (
        candidate["comparison"]["selection_score"],
        candidate["comparison"]["recall"],
        -candidate["comparison"]["recognized_unique"],
    ))
    output = {
        "schema_version": "cad-ocr-v1",
        "drawing_key": image_path.stem,
        "image": {
            "source_file": image_path.name,
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
                "name": "autocad-canvas",
                "box_px": [x0, y0, x1, y1],
                "working_size": [x1 - x0, y1 - y0],
            },
            "alpha_handling": "source loaded as BGR; source alpha was not used",
        },
        "postprocessing": {
            "adjacent_box_merge": {
                "enabled": True,
                "rule": "engineering_prefix_plus_adjacent_numeric",
                "source_item_count": selected["source_item_count"],
                "merged_item_count": selected["merged_item_count"],
            }
        },
        "items": selected["items"],
        "statistics": {
            "item_count": len(selected["items"]),
            "high_confidence_count": sum(item["confidence"] >= 0.8 for item in selected["items"]),
            "merged_item_count": selected["merged_item_count"],
        },
        "comparison": {
            "reference_file": reference_path.name,
            "candidates": [
                {
                    "mode": candidate["mode"],
                    "source_item_count": candidate["source_item_count"],
                    "merged_item_count": candidate["merged_item_count"],
                    **candidate["comparison"],
                }
                for candidate in candidates
            ],
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    run_ocr.draw_visual(source, selected["items"], visualized_path)
    return output


def run_command(command: list[str]) -> None:
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("dataset/AutoCAD_2D_Drawings"))
    parser.add_argument("--dwgread", type=Path, default=Path("libredwg-build/dwgread"))
    parser.add_argument("--output-root", type=Path, default=Path("demo"))
    args = parser.parse_args()

    dwgs = {path.stem.lower(): path for path in args.dataset.glob("*.dwg")}
    pngs = {path.stem.lower(): path for path in args.dataset.glob("*.png")}
    keys = sorted(set(dwgs) & set(pngs))
    if not keys:
        raise SystemExit("No matching DWG/PNG pairs found")
    if len(keys) != len(dwgs) or len(keys) != len(pngs):
        raise SystemExit("DWG/PNG files are not one-to-one by basename")
    if not args.dwgread.exists():
        raise SystemExit(f"dwgread not found: {args.dwgread}")

    raw_dir = args.output_root / "raw"
    structured_dir = args.output_root / "structured"
    native_dir = args.output_root / "ocr" / "normalized"
    ocr_dir = args.output_root / "ocr" / "raw"
    visual_dir = args.output_root / "ocr" / "visualized"
    aligned_dir = args.output_root / "aligned"
    vision_dir = args.output_root / "vision" / "normalized"
    unified_dir = args.output_root / "unified"
    for directory in (raw_dir, structured_dir, native_dir, ocr_dir, visual_dir, aligned_dir, vision_dir, unified_dir):
        directory.mkdir(parents=True, exist_ok=True)

    engine = run_ocr.RapidOCR()
    summary: list[dict[str, Any]] = []
    for index, key in enumerate(keys, start=1):
        dwg = dwgs[key]
        png = pngs[key]
        stem = dwg.stem
        raw_path = raw_dir / f"{stem}_raw.json"
        structured_path = structured_dir / f"{stem}_structured.json"
        native_path = native_dir / f"{stem}_native_text.json"
        ocr_path = ocr_dir / f"{stem}_ocr.json"
        visual_path = visual_dir / f"{stem}_ocr_visualized.png"
        alignment_path = aligned_dir / f"{stem}_alignment.json"
        vision_path = vision_dir / f"{stem}_vision.json"
        unified_path = unified_dir / f"{stem}_unified_features.json"

        print(f"[{index}/{len(keys)}] LibreDWG: {dwg.name}", flush=True)
        result = subprocess.run(
            [str(args.dwgread), "-O", "JSON", str(dwg)],
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"dwgread failed for {dwg}: {result.stderr}")
        raw_path.write_text(result.stdout, encoding="utf-8")
        extract(raw_path, structured_path, source_file=dwg.name)
        normalize(structured_path, native_path)
        print(f"[{index}/{len(keys)}] OCR: {png.name}", flush=True)
        ocr_output = run_image_ocr(engine, png, ocr_path, visual_path, native_path)

        run_command([
            sys.executable,
            str(Path(__file__).with_name("align_cad_ocr.py")),
            str(native_path),
            str(ocr_path),
            str(alignment_path),
            "--image",
            str(png),
        ])
        fusion_command = [
            sys.executable,
            str(Path(__file__).with_name("build_unified_features.py")),
            str(native_path),
            str(ocr_path),
            str(alignment_path),
            str(unified_path),
        ]
        if vision_path.exists():
            fusion_command.extend(["--vision-json", str(vision_path)])
        run_command(fusion_command)
        alignment = json.loads(alignment_path.read_text(encoding="utf-8"))
        unified = json.loads(unified_path.read_text(encoding="utf-8"))
        record = {
            "drawing": stem,
            "dwg": str(dwg),
            "png": str(png),
            "ocr_items": ocr_output["statistics"]["item_count"],
            "merged_items": ocr_output["statistics"]["merged_item_count"],
            "ocr_selected_preprocess": ocr_output["preprocessing"]["selected"],
            "anchors": alignment["registration"]["anchor_count"],
            "registration_rmse_px": alignment["registration"]["rmse_px"],
            "fusion": alignment["statistics"],
            "unified_features": unified["statistics"]["feature_count"],
            "vision_observations": unified["statistics"].get("vision_observation_count", 0),
            "vision_attached_features": unified["statistics"].get("vision_attached_feature_count", 0),
            "vision_only_features": unified["statistics"].get("vision_only_feature_count", 0),
        }
        summary.append(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)

    manifest = {
        "schema_version": "cad-ocr-batch-v1",
        "dataset": str(args.dataset),
        "pair_count": len(summary),
        "records": summary,
    }
    manifest_path = args.output_root / "reports" / "cad_ocr_batch_summary.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"manifest: {manifest_path}")


if __name__ == "__main__":
    main()
