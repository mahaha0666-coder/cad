#!/usr/bin/env python3
"""Run DeepSeek Vision on one local CAD PNG and write normalized Vision JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from app.llm.deepseek_vision_client import (  # noqa: E402
    DeepSeekVisionClient,
    response_text,
)
from app.prompts.cad_vision import (  # noqa: E402
    VISION_PROMPT_VERSION,
    build_vision_prompt,
)
from app.vision.schema import normalize_vision_response  # noqa: E402


def slug(value: str) -> str:
    return "-".join(value.lower().split()).replace("/", "-")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path, help="local PNG drawing image")
    parser.add_argument(
        "--drawing-key",
        help="drawing key; defaults to a slug of the image stem",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="normalized Vision JSON path",
    )
    parser.add_argument(
        "--raw-output",
        type=Path,
        help="optional raw model response JSON path",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    image_path = args.image.resolve()
    drawing_key = args.drawing_key or slug(image_path.stem)
    client = DeepSeekVisionClient.from_env()
    raw_response = client.analyze_file(
        image_path,
        build_vision_prompt(drawing_key),
    )
    text = response_text(raw_response)
    document = normalize_vision_response(
        response_text=text,
        image_path=image_path,
        drawing_key=drawing_key,
        model_name=client.config.model,
        prompt_version=VISION_PROMPT_VERSION,
        detail=client.config.detail,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            document.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    if args.raw_output:
        args.raw_output.parent.mkdir(parents=True, exist_ok=True)
        args.raw_output.write_text(
            json.dumps({"response_text": text}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(f"vision_model={client.config.model}")
    print(f"drawing_key={drawing_key}")
    print(f"output={args.output}")
    print(f"observations={len(document.observations)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
