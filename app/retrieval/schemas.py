"""HTTP schemas for hybrid CAD retrieval."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


DimensionKind = Literal["linear", "radius", "diameter", "angle"]

SUPPORTED_ENTITY_TYPES = {
    "LINE",
    "CIRCLE",
    "ARC",
    "POLYLINE",
    "LWPOLYLINE",
    "TEXT",
    "MTEXT",
    "DIMENSION",
    "INSERT",
}


class HybridSearchRequest(BaseModel):
    """A natural-language query plus optional explicit hard constraints."""

    query: str = Field(min_length=1, max_length=4000)
    drawing_key: str | None = Field(default=None, min_length=1, max_length=256)
    revision_id: str | None = Field(default=None, min_length=1, max_length=128)
    top_k: int = Field(default=30, ge=1, le=50, description="Qdrant recall size")
    final_limit: int = Field(default=10, ge=1, le=20)
    min_score: float = Field(default=0.35, ge=0, le=1)
    dimension_kind: DimensionKind | None = None
    dimension_value: float | None = Field(default=None, ge=0)
    tolerance: float = Field(default=0.01, ge=0)
    entity_types: list[str] = Field(default_factory=list, max_length=9)

    @field_validator("entity_types")
    @classmethod
    def normalize_entity_types(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            item = value.strip().upper()
            if item not in SUPPORTED_ENTITY_TYPES:
                raise ValueError(f"entity_types contains unsupported type: {value}")
            if item not in normalized:
                normalized.append(item)
        return normalized

    @model_validator(mode="after")
    def validate_scope_and_dimension(self) -> "HybridSearchRequest":
        if self.drawing_key and self.revision_id:
            raise ValueError("provide only one of drawing_key or revision_id")
        if self.dimension_value is not None and self.dimension_kind is None:
            raise ValueError("dimension_kind is required when dimension_value is provided")
        return self
