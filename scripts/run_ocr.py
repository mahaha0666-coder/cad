#!/usr/bin/env python3
"""Run RapidOCR on a CAD drawing image and write a stable OCR JSON record."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from rapidocr import RapidOCR

from cad_ocr_common import normalize_engineering_text, parse_engineering_text


def load_reference_texts(path: Path | None) -> set[str]:
    if path is None:
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data.get("items"), list):
        values = [item.get("text_normalized") for item in data["items"]]
        return {value for value in values if value}
    values = []
    for entity in data.get("entities", []):
        if entity.get("type") in {"TEXT", "MTEXT", "DIMENSION"}:
            normalized = normalize_engineering_text(entity.get("text"))
            if normalized:
                values.append(normalized)
    return set(values)


def preprocess(image: np.ndarray, mode: str) -> np.ndarray:
    if mode == "raw":
        return image.copy()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if mode == "gray":
        return gray
    if mode == "invert":
        return cv2.bitwise_not(gray)
    raise ValueError(f"Unsupported preprocessing mode: {mode}")


def jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, tuple):
        return [jsonable(item) for item in value]
    if isinstance(value, list):
        return [jsonable(item) for item in value]
    return value


def run_variant(engine: RapidOCR, image: np.ndarray, mode: str) -> dict[str, Any]:
    processed = preprocess(image, mode)
    result = engine(processed)
    boxes = result.boxes
    texts = result.txts
    scores = result.scores
    items: list[dict[str, Any]] = []

    if boxes is not None and texts is not None and scores is not None:
        for index, (box, text, score) in enumerate(zip(boxes, texts, scores), start=1):
            polygon = jsonable(box)
            points = np.asarray(polygon, dtype=float).reshape(-1, 2)
            center = points.mean(axis=0).tolist()
            raw_text = str(text)
            item = {
                "id": f"OCR_{index:04d}",
                "text_raw": raw_text,
                "text_normalized": normalize_engineering_text(raw_text),
                "polygon_px": polygon,
                "center_px": center,
                "confidence": float(score),
            }
            parsed = parse_engineering_text(raw_text)
            if parsed is not None:
                item["parsed"] = parsed
            items.append(item)

    return {"mode": mode, "items": items, "image": processed}


def candidate_score(items: list[dict[str, Any]], reference: set[str]) -> dict[str, Any]:
    recognized = {item["text_normalized"] for item in items if item.get("text_normalized")}
    matched = recognized & reference
    precision = len(matched) / len(recognized) if recognized else 0.0
    recall = len(matched) / len(reference) if reference else 0.0
    score = 0.6 * precision + 0.4 * recall if reference else 0.0
    return {
        "recognized_unique": len(recognized),
        "matched_unique": len(matched),
        "reference_unique": len(reference),
        "precision": precision,
        "recall": recall,
        "selection_score": score,
    }


def draw_visual(image: np.ndarray, items: list[dict[str, Any]], output: Path) -> None:
    canvas = image.copy()
    if len(canvas.shape) == 2:
        canvas = cv2.cvtColor(canvas, cv2.COLOR_GRAY2BGR)
    for item in items:
        polygon = np.asarray(item["polygon_px"], dtype=np.int32).reshape(-1, 1, 2)
        cv2.polylines(canvas, [polygon], True, (0, 220, 0), 2, cv2.LINE_AA)
        center = tuple(int(round(v)) for v in item["center_px"])
        label = f"{item['id']} {item['confidence']:.2f}"
        cv2.putText(canvas, label, center, cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1, cv2.LINE_AA)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), canvas):
        raise RuntimeError(f"Could not write visualization: {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("output_json", type=Path)
    parser.add_argument("--visualized", type=Path)
    parser.add_argument("--reference-cad", type=Path)
    parser.add_argument("--preprocess", choices=["raw", "gray", "invert", "auto"], default="auto")
    args = parser.parse_args()

    image = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f"Could not read image: {args.image}")

    reference = load_reference_texts(args.reference_cad)
    engine = RapidOCR()
    modes = ["raw", "gray", "invert"] if args.preprocess == "auto" else [args.preprocess]
    candidates: list[dict[str, Any]] = []
    for mode in modes:
        variant = run_variant(engine, image, mode)
        comparison = candidate_score(variant["items"], reference)
        candidates.append({
            "mode": mode,
            "items": variant["items"],
            "image": variant["image"],
            "comparison": comparison,
        })

    if reference:
        selected = max(candidates, key=lambda candidate: (candidate["comparison"]["selection_score"], len(candidate["items"])))
    else:
        selected = candidates[0]

    height, width = image.shape[:2]
    output = {
        "schema_version": "cad-ocr-v1",
        "drawing_key": args.image.stem,
        "image": {
            "source_file": args.image.name,
            "width": width,
            "height": height,
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
        draw_visual(selected["image"], selected["items"], args.visualized)

    print(json.dumps(output["statistics"], ensure_ascii=False, indent=2))
    print(f"selected_preprocess: {selected['mode']}")
    print(f"output: {args.output_json}")
    if args.visualized:
        print(f"visualized: {args.visualized}")


if __name__ == "__main__":
    main()
