"""Deterministic query planning for the first hybrid-retrieval version.

The planner extracts only high-confidence, explicit constraints. The free-form
part of the question remains the Qdrant semantic query. It intentionally does
not turn ambiguous words such as ``孔`` into a hard CIRCLE constraint.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_NUMBER = r"([0-9]+(?:\.[0-9]+)?)"
_DIMENSION_PATTERNS: tuple[tuple[str, str, str], ...] = (
    ("radius", rf"(?:半径|radius|\bR)\s*[:：]?\s*{_NUMBER}"),
    ("diameter", rf"(?:直径|diameter|[Ø⌀Φφ]|%%[Cc])\s*[:：]?\s*{_NUMBER}"),
    ("angle", rf"{_NUMBER}\s*(?:度|°|deg(?:ree)?s?|角度)"),
    ("linear", rf"(?:长度|距离|length|distance)\s*[:：]?\s*{_NUMBER}"),
)

_ENTITY_PATTERNS = (
    ("LWPOLYLINE", r"\bLWPOLYLINE\b"),
    ("POLYLINE", r"\bPOLYLINE\b|多段线|折线"),
    ("DIMENSION", r"\bDIMENSION\b|尺寸标注|标注尺寸"),
    ("INSERT", r"\bINSERT\b|块实例|图块插入"),
    ("MTEXT", r"\bMTEXT\b|多行文字"),
    ("TEXT", r"\bTEXT\b|单行文字"),
    ("CIRCLE", r"\bCIRCLE\b|圆实体|圆形实体"),
    ("ARC", r"\bARC\b|圆弧实体"),
    ("LINE", r"\bLINE\b|直线实体"),
)


@dataclass(frozen=True)
class HybridQueryPlan:
    query: str
    drawing_key: str | None
    revision_id: str | None
    dimension_kind: str | None
    dimension_value: float | None
    tolerance: float
    entity_types: tuple[str, ...]
    explicit_constraints: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def has_exact_constraints(self) -> bool:
        return bool(self.dimension_kind or self.dimension_value is not None or self.entity_types)

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "scope": {
                key: value
                for key, value in {
                    "drawing_key": self.drawing_key,
                    "revision_id": self.revision_id,
                }.items()
                if value
            },
            "dimension": {
                "kind": self.dimension_kind,
                "value": self.dimension_value,
                "tolerance": self.tolerance,
            },
            "entity_types": list(self.entity_types),
            "explicit_constraints": list(self.explicit_constraints),
            "warnings": list(self.warnings),
        }


def _parse_dimension(query: str) -> tuple[str | None, float | None, list[str]]:
    for kind, pattern in _DIMENSION_PATTERNS:
        match = re.search(pattern, query, flags=re.IGNORECASE)
        if match:
            return kind, float(match.group(1)), [f"{kind}={match.group(1)}"]
    for kind in ("radius", "diameter", "angle", "linear"):
        words = {
            "radius": ("半径", "radius"),
            "diameter": ("直径", "diameter"),
            "angle": ("角度", "angle"),
            "linear": ("线性尺寸", "linear"),
        }[kind]
        if any(word.lower() in query.lower() for word in words):
            return kind, None, [f"dimension_kind={kind}"]
    return None, None, []


def _parse_entity_types(query: str) -> tuple[str, ...]:
    result: list[str] = []
    for entity_type, pattern in _ENTITY_PATTERNS:
        if re.search(pattern, query, flags=re.IGNORECASE):
            result.append(entity_type)
    return tuple(result)


def build_query_plan(
    query: str,
    *,
    drawing_key: str | None = None,
    revision_id: str | None = None,
    dimension_kind: str | None = None,
    dimension_value: float | None = None,
    tolerance: float = 0.01,
    entity_types: list[str] | tuple[str, ...] | None = None,
) -> HybridQueryPlan:
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("query must be non-empty")
    if drawing_key and revision_id:
        raise ValueError("provide only one of drawing_key or revision_id")
    if tolerance < 0:
        raise ValueError("tolerance must be non-negative")

    parsed_kind, parsed_value, constraints = _parse_dimension(normalized_query)
    final_kind = dimension_kind or parsed_kind
    final_value = dimension_value if dimension_value is not None else parsed_value
    if final_value is not None and final_kind is None:
        raise ValueError("dimension_kind is required when dimension_value is provided")

    parsed_entities = list(_parse_entity_types(normalized_query))
    for item in entity_types or ():
        normalized = item.strip().upper()
        if normalized not in {
            "LINE", "CIRCLE", "ARC", "POLYLINE", "LWPOLYLINE", "TEXT", "MTEXT", "DIMENSION", "INSERT"
        }:
            raise ValueError(f"unsupported entity type: {item}")
        if normalized not in parsed_entities:
            parsed_entities.append(normalized)
        constraints.append(f"entity_type={normalized}")

    if dimension_kind and f"dimension_kind={dimension_kind}" not in constraints:
        constraints.append(f"dimension_kind={dimension_kind}")
    if dimension_value is not None and not any(item.endswith(f"={dimension_value:g}") for item in constraints):
        constraints.append(f"dimension_value={dimension_value:g}")

    warnings: list[str] = []
    if any(token in normalized_query for token in ("孔", "hole", "槽", "slot", "齿", "tooth", "对称", "支架")):
        warnings.append("结构名称和零件语义仍是语义候选，不能仅凭文字直接视为精确几何事实")

    return HybridQueryPlan(
        query=normalized_query,
        drawing_key=drawing_key,
        revision_id=revision_id,
        dimension_kind=final_kind,
        dimension_value=final_value,
        tolerance=tolerance,
        entity_types=tuple(parsed_entities),
        explicit_constraints=tuple(dict.fromkeys(constraints)),
        warnings=tuple(warnings),
    )
