"""Keep tool payloads useful and bounded before sending them to the LLM."""

from __future__ import annotations

import copy
import json
from typing import Any


def _remove_large_fields(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in {"raw_properties", "raw_payload"}:
                continue
            result[key] = _remove_large_fields(item)
        return result
    if isinstance(value, list):
        return [_remove_large_fields(item) for item in value]
    return value


def compact_tool_result(result: dict[str, Any], *, max_items: int = 40, max_chars: int = 18000) -> dict[str, Any]:
    """Return a bounded copy while preserving count and truncation metadata."""

    compact = _remove_large_fields(copy.deepcopy(result))
    items = compact.get("items")
    if isinstance(items, list) and len(items) > max_items:
        compact["items"] = items[:max_items]
        compact["items_truncated"] = True
        compact["returned_item_count"] = max_items
    serialized = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
    if len(serialized) <= max_chars:
        return compact
    if isinstance(compact.get("items"), list):
        compact["items"] = compact["items"][:5]
        compact["items_truncated"] = True
    compact["result_truncated_for_model"] = True
    return compact


def result_count(result: dict[str, Any]) -> int | None:
    value = result.get("count")
    if isinstance(value, int):
        return value
    items = result.get("items")
    return len(items) if isinstance(items, list) else None


def collect_evidence(result: dict[str, Any]) -> list[dict[str, str | None]]:
    """Collect stable evidence references independently of the model answer."""

    found: list[dict[str, str | None]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(evidence_type: str, item: dict[str, Any], id_key: str = "id", external_key: str = "external_id") -> None:
        identifier = item.get(id_key)
        external_id = item.get(external_key)
        if identifier is None and external_id is None:
            return
        key = (evidence_type, str(identifier or ""), str(external_id or ""))
        if key in seen:
            return
        seen.add(key)
        found.append({
            "evidence_type": evidence_type,
            "id": str(identifier) if identifier is not None else None,
            "external_id": str(external_id) if external_id is not None else None,
            "status": item.get("fusion_status") or item.get("status"),
        })

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, evidence_type in (
                ("cad_entity", "cad_entity"),
                ("native_annotation", "native_annotation"),
                ("feature", "unified_feature"),
                ("ocr_item", "ocr_item"),
            ):
                nested = value.get(key)
                if isinstance(nested, dict):
                    add(evidence_type, nested)
            if value.get("feature_type") and value.get("external_id"):
                add("unified_feature", value)
            if value.get("feature_id") and value.get("external_id"):
                add("unified_feature", value, id_key="feature_id")
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(result)
    return found
