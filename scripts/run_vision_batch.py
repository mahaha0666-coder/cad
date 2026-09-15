#!/usr/bin/env python3
"""Run the DeepSeek Vision extraction over all PNG drawings in a dataset."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from app.llm.deepseek_vision_client import DeepSeekVisionClient, response_text  # noqa: E402
from app.prompts.cad_vision import VISION_PROMPT_VERSION, build_vision_prompt  # noqa: E402
from app.vision.schema import normalize_vision_response  # noqa: E402


def slug(value: str) -> str:
    return "-".join(value.lower().split()).replace("/", "-")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=ROOT / "dataset" / "AutoCAD_2D_Drawings",
    )
    parser.add_argument(
        "--normalized-dir",
        type=Path,
        default=ROOT / "demo" / "vision" / "normalized",
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=ROOT / "demo" / "vision" / "raw",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=ROOT / "demo" / "reports" / "vision_batch_summary.json",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-run images even when normalized output exists",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    images = sorted(args.dataset_dir.glob("*.png"))
    if not images:
        raise SystemExit(f"No PNG files found in {args.dataset_dir}")

    client = DeepSeekVisionClient.from_env()
    args.normalized_dir.mkdir(parents=True, exist_ok=True)
    args.raw_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []

    for image in images:
        key = slug(image.stem)
        output = args.normalized_dir / f"{image.stem}_vision.json"
        raw_output = args.raw_dir / f"{image.stem}_vision_raw.json"
        item: dict[str, object] = {
            "drawing_key": key,
            "source_file": image.name,
            "output": str(output.relative_to(ROOT)),
        }
        if output.exists() and not args.force:
            item["status"] = "skipped_existing"
            results.append(item)
            print(f"skip={image.name}")
            continue
        try:
            response = client.analyze_file(image, build_vision_prompt(key))
            text = response_text(response)
            document = normalize_vision_response(
                response_text=text,
                image_path=image,
                drawing_key=key,
                model_name=client.config.model,
                prompt_version=VISION_PROMPT_VERSION,
                detail=client.config.detail,
            )
            output.write_text(
                json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
            )
            raw_output.write_text(
                json.dumps({"response_text": text}, ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
            )
            item["status"] = "completed"
            item["observation_count"] = len(document.observations)
            print(f"completed={image.name} observations={len(document.observations)}")
        except Exception as exc:
            item["status"] = "error"
            item["error"] = str(exc)
            print(f"error={image.name} error={exc}", file=sys.stderr)
        results.append(item)

    args.summary.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema_version": "cad-vision-batch-v1",
        "model": client.config.model,
        "prompt_version": VISION_PROMPT_VERSION,
        "dataset_dir": str(args.dataset_dir),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "total_images": len(images),
        "completed": sum(item["status"] == "completed" for item in results),
        "skipped_existing": sum(item["status"] == "skipped_existing" for item in results),
        "errors": sum(item["status"] == "error" for item in results),
        "items": results,
    }
    args.summary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"summary={args.summary}")
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
