#!/usr/bin/env python3
"""Shared normalization helpers for the CAD/OCR demo."""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any


def clean_cad_text(value: Any) -> str:
    """Remove common DXF/MTEXT formatting while retaining visible text."""

    if value is None:
        return ""
    text = str(value)
    text = text.replace("\\P", " ")
    text = re.sub(r"\\[A-Za-z][^;{}]*;", "", text)
    text = text.replace("{", "").replace("}", "")
    text = text.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


def normalize_engineering_text(value: Any) -> str:
    """Create a comparison key without destroying the original text."""

    text = clean_cad_text(value)
    text = unicodedata.normalize("NFKC", text).upper()
    replacements = {
        "⌀": "Ø",
        "∅": "Ø",
        "Φ": "Ø",
        "﹣": "-",
        "−": "-",
        "×": "X",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return re.sub(r"\s+", "", text)


def number_or_none(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(number):
        return None
    if number.is_integer():
        return int(number)
    return number


def parse_engineering_text(value: Any) -> dict[str, Any] | None:
    """Parse a conservative engineering-text candidate, never a final fact."""

    normalized = normalize_engineering_text(value)
    if not normalized:
        return None

    number = r"([-+]?(?:\d+(?:\.\d*)?|\.\d+))"
    match = re.match(r"^R" + number, normalized)
    if match:
        return {"kind": "radius", "value": number_or_none(match.group(1)), "unit": None}

    match = re.match(r"^Ø" + number, normalized)
    if match:
        return {"kind": "diameter", "value": number_or_none(match.group(1)), "unit": None}

    match = re.match(r"^" + number + r"°$", normalized)
    if match:
        return {"kind": "angle", "value": number_or_none(match.group(1)), "unit": None}

    if re.fullmatch(number, normalized):
        return {"kind": "numeric", "value": number_or_none(normalized), "unit": None}

    return None


def point2(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None


def distance2(a: Any, b: Any) -> float | None:
    pa = point2(a)
    pb = point2(b)
    if pa is None or pb is None:
        return None
    return math.hypot(pa[0] - pb[0], pa[1] - pb[1])


def dimension_kind(dimension_type: Any) -> str | None:
    if not isinstance(dimension_type, str):
        return None
    if "DIAMETER" in dimension_type:
        return "diameter"
    if "RADIUS" in dimension_type:
        return "radius"
    if "ANG" in dimension_type:
        return "angle"
    if "ORDINATE" in dimension_type:
        return "ordinate"
    if "LINEAR" in dimension_type or "ALIGNED" in dimension_type:
        return "linear"
    if dimension_type.startswith("DIMENSION"):
        return "dimension"
    return None


def format_number(value: Any, digits: int = 6) -> str:
    number = number_or_none(value)
    if number is None:
        return ""
    if isinstance(number, int):
        return str(number)
    return f"{number:.{digits}f}".rstrip("0").rstrip(".")


def parse_native_dimension_text(entity: dict[str, Any], display_text: Any = None) -> dict[str, Any]:
    """Normalize one native DIMENSION, using linked MTEXT when available."""

    raw_text = entity.get("text")
    visible_text = display_text if display_text not in (None, "") else raw_text
    cleaned = clean_cad_text(visible_text)
    kind = dimension_kind(entity.get("dimension_type"))
    measurement = number_or_none(entity.get("measurement"))

    if "<>" in cleaned and measurement is not None:
        if kind == "angle":
            cleaned = cleaned.replace("<>", format_number(math.degrees(float(measurement))) + "°")
        elif kind == "radius":
            cleaned = cleaned.replace("<>", format_number(measurement))
        else:
            cleaned = cleaned.replace("<>", format_number(measurement))

    normalized = normalize_engineering_text(cleaned)
    parsed = parse_engineering_text(cleaned)
    if kind in {"radius", "diameter", "linear", "ordinate"} and measurement is not None:
        parsed = {"kind": kind, "value": measurement, "unit": None}
    elif kind == "angle" and measurement is not None:
        parsed = {"kind": "angle", "value": math.degrees(float(measurement)), "unit": None}

    return {
        "text_raw": raw_text,
        "display_text_raw": display_text,
        "text_clean": cleaned,
        "text_normalized": normalized,
        "dimension_kind": kind,
        "measurement_raw": measurement,
        "parsed": parsed,
    }
