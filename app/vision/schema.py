"""Stable, conservative schema for model-generated CAD visual observations."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class VisionImage(BaseModel):
    source_file: str
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    coordinate_system: str = "pixel_top_left"
    sha256: str


class VisionModel(BaseModel):
    name: str
    prompt_version: str
    detail: str


class VisionObservation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    observation_type: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=4000)
    region_px: list[float] | None = None
    region_normalized: list[float] | None = None
    candidate_feature: dict[str, Any] | None = None
    model_confidence: float | None = Field(default=None, ge=0, le=1)
    status: str = "candidate"
    uncertainty: str | None = Field(default=None, max_length=2000)
    properties: dict[str, Any] = Field(default_factory=dict)

    @field_validator("region_px", "region_normalized")
    @classmethod
    def validate_region(cls, value: list[float] | None) -> list[float] | None:
        if value is not None and len(value) != 4:
            raise ValueError("region must contain [x_min, y_min, x_max, y_max]")
        return value


class VisionDocument(BaseModel):
    schema_version: str = "cad-vision-v1"
    drawing_key: str
    image: VisionImage
    model: VisionModel
    drawing_type: str | None = None
    summary: str | None = None
    observations: list[VisionObservation] = Field(default_factory=list)
    statistics: dict[str, Any] = Field(default_factory=dict)


def image_dimensions(path: Path) -> tuple[int, int]:
    """Read PNG dimensions without adding an imaging dependency."""

    data = path.read_bytes()
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    raise ValueError("Only PNG input is supported by the first Vision CLI")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_text(text: str) -> str:
    cleaned = text.strip()
    fenced = re.fullmatch(
        r"`{3}(?:json)?s*(.*?)s*`{3}",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return fenced.group(1).strip() if fenced else cleaned


def _region(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return None


def normalize_vision_response(
    *,
    response_text: str,
    image_path: Path,
    drawing_key: str,
    model_name: str,
    prompt_version: str,
    detail: str,
) -> VisionDocument:
    """Parse model JSON into a controlled schema.

    Model confirmation labels are deliberately discarded: visual output is
    evidence, not an authoritative engineering fact.
    """

    payload = json.loads(_json_text(response_text))
    if not isinstance(payload, dict):
        raise ValueError("Vision response must be a JSON object")
    width, height = image_dimensions(image_path)
    raw_observations = payload.get("observations", [])
    if not isinstance(raw_observations, list):
        raise ValueError("Vision response observations must be an array")

    observations: list[VisionObservation] = []
    for index, raw in enumerate(raw_observations, start=1):
        if not isinstance(raw, dict):
            continue
        region_px = _region(
            raw.get("region_px")
            or raw.get("evidence_region_px")
            or raw.get("bbox")
        )
        region_normalized = _region(raw.get("region_normalized"))
        if region_normalized is None and region_px:
            region_normalized = [
                region_px[0] / width,
                region_px[1] / height,
                region_px[2] / width,
                region_px[3] / height,
            ]
        confidence = raw.get("model_confidence", raw.get("confidence"))
        try:
            confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence = None
        if confidence is not None and not 0 <= confidence <= 1:
            confidence = None
        candidate = raw.get("candidate_feature")
        if candidate is not None and not isinstance(candidate, dict):
            candidate = {"value": candidate}
        observations.append(
            VisionObservation(
                id=str(raw.get("id") or f"VIS_{index:04d}"),
                observation_type=str(
                    raw.get("observation_type") or raw.get("type") or "other"
                ),
                description=str(raw.get("description") or "未提供描述"),
                region_px=region_px,
                region_normalized=region_normalized,
                candidate_feature=candidate,
                model_confidence=confidence,
                status="candidate",
                uncertainty=(
                    str(raw["uncertainty"])
                    if raw.get("uncertainty") is not None
                    else None
                ),
                properties={
                    key: value
                    for key, value in raw.items()
                    if key not in {
                        "id", "observation_type", "type", "description",
                        "region_px", "evidence_region_px", "bbox",
                        "region_normalized", "candidate_feature",
                        "model_confidence", "confidence", "status", "uncertainty",
                    }
                },
            )
        )

    return VisionDocument(
        drawing_key=drawing_key,
        image=VisionImage(
            source_file=image_path.name,
            width=width,
            height=height,
            sha256=sha256_file(image_path),
        ),
        model=VisionModel(
            name=model_name,
            prompt_version=prompt_version,
            detail=detail,
        ),
        drawing_type=payload.get("drawing_type"),
        summary=payload.get("summary"),
        observations=observations,
        statistics={
            "observation_count": len(observations),
            "candidate_count": len(observations),
        },
    )
