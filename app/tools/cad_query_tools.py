"""Whitelisted CAD query tools suitable for DeepSeek Function Calling.

The model can choose a tool and provide JSON arguments, but it cannot submit
SQL. The executor validates the arguments and delegates to CADQueryService.
"""

from __future__ import annotations

from typing import Any, Callable

from app.services.cad_query_service import CADQueryService


class ToolExecutionError(ValueError):
    """Raised when a tool name or tool arguments are invalid."""


def _require_selector(arguments: dict[str, Any]) -> None:
    has_key = bool(arguments.get("drawing_key"))
    has_revision = bool(arguments.get("revision_id"))
    if has_key == has_revision:
        raise ToolExecutionError("exactly one of drawing_key or revision_id is required")


def _selector(arguments: dict[str, Any]) -> dict[str, Any]:
    _require_selector(arguments)
    return {key: arguments[key] for key in ("drawing_key", "revision_id") if arguments.get(key)}


def _optional_selector(arguments: dict[str, Any]) -> dict[str, Any]:
    if arguments.get("drawing_key") and arguments.get("revision_id"):
        raise ToolExecutionError("provide at most one of drawing_key or revision_id")
    return {key: arguments[key] for key in ("drawing_key", "revision_id") if arguments.get(key)}


def _bounded_int(arguments: dict[str, Any], name: str, default: int, maximum: int = 500) -> int:
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ToolExecutionError(f"{name} must be an integer between 1 and {maximum}")
    return value


def _offset(arguments: dict[str, Any]) -> int:
    value = arguments.get("offset", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ToolExecutionError("offset must be a non-negative integer")
    return value


def _optional_string(arguments: dict[str, Any], name: str) -> str | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ToolExecutionError(f"{name} must be a non-empty string when provided")
    return value.strip()


def _execute_list_drawings(service: CADQueryService, arguments: dict[str, Any]) -> dict[str, Any]:
    return service.list_drawings(
        search=_optional_string(arguments, "search"),
        limit=_bounded_int(arguments, "limit", 50),
        offset=_offset(arguments),
    )


def _execute_summary(service: CADQueryService, arguments: dict[str, Any]) -> dict[str, Any]:
    return service.get_drawing_summary(**_selector(arguments))


def _execute_count_entities(service: CADQueryService, arguments: dict[str, Any]) -> dict[str, Any]:
    entity_type = _optional_string(arguments, "entity_type")
    return service.count_entities(**_selector(arguments), entity_type=entity_type)


def _execute_list_entities(service: CADQueryService, arguments: dict[str, Any]) -> dict[str, Any]:
    entity_type = _optional_string(arguments, "entity_type")
    if entity_type:
        entity_type = entity_type.upper()
        if entity_type not in {
            "LINE", "CIRCLE", "ARC", "POLYLINE", "LWPOLYLINE", "TEXT", "MTEXT", "DIMENSION", "INSERT"
        }:
            raise ToolExecutionError("entity_type is not supported by the current query tool")
    return service.list_entities(
        **_selector(arguments),
        entity_type=entity_type,
        layer_name=_optional_string(arguments, "layer_name"),
        cad_id=_optional_string(arguments, "cad_id"),
        limit=_bounded_int(arguments, "limit", 100),
        offset=_offset(arguments),
        include_raw_properties=bool(arguments.get("include_raw_properties", False)),
    )


def _execute_layers(service: CADQueryService, arguments: dict[str, Any]) -> dict[str, Any]:
    return service.list_layers(**_selector(arguments), limit=_bounded_int(arguments, "limit", 200, 500))


def _execute_blocks(service: CADQueryService, arguments: dict[str, Any]) -> dict[str, Any]:
    return service.list_blocks(**_selector(arguments), limit=_bounded_int(arguments, "limit", 200, 500))


def _execute_native(service: CADQueryService, arguments: dict[str, Any]) -> dict[str, Any]:
    parsed_kind = _optional_string(arguments, "parsed_kind")
    if parsed_kind and parsed_kind not in {"linear", "radius", "diameter", "angle"}:
        raise ToolExecutionError("parsed_kind must be linear, radius, diameter, or angle")
    return service.list_native_annotations(
        **_selector(arguments),
        text=_optional_string(arguments, "text"),
        parsed_kind=parsed_kind,
        limit=_bounded_int(arguments, "limit", 100),
        offset=_offset(arguments),
    )


def _execute_ocr(service: CADQueryService, arguments: dict[str, Any]) -> dict[str, Any]:
    confidence = arguments.get("min_confidence")
    if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1):
        raise ToolExecutionError("min_confidence must be a number between 0 and 1")
    return service.list_ocr_items(
        **_selector(arguments),
        text=_optional_string(arguments, "text"),
        min_confidence=confidence,
        limit=_bounded_int(arguments, "limit", 100),
        offset=_offset(arguments),
    )


def _execute_vision(service: CADQueryService, arguments: dict[str, Any]) -> dict[str, Any]:
    status = _optional_string(arguments, "status")
    if status and status not in {"candidate", "confirmed", "conflict", "ambiguous", "vision_only"}:
        raise ToolExecutionError("status is not a recognized vision status")
    confidence = arguments.get("min_confidence")
    if confidence is not None and (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= confidence <= 1
    ):
        raise ToolExecutionError("min_confidence must be a number between 0 and 1")
    return service.list_vision_observations(
        **_selector(arguments),
        observation_type=_optional_string(arguments, "observation_type"),
        status=status,
        min_confidence=confidence,
        limit=_bounded_int(arguments, "limit", 100),
        offset=_offset(arguments),
    )


def _execute_dimensions(service: CADQueryService, arguments: dict[str, Any]) -> dict[str, Any]:
    kind = _optional_string(arguments, "dimension_kind")
    if kind:
        kind = kind.lower()
        if kind not in {"linear", "radius", "diameter", "angle"}:
            raise ToolExecutionError("dimension_kind must be linear, radius, diameter, or angle")
    value = arguments.get("value")
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0):
        raise ToolExecutionError("value must be a non-negative number")
    tolerance = arguments.get("tolerance", 0.01)
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or tolerance < 0:
        raise ToolExecutionError("tolerance must be a non-negative number")
    return service.find_dimensions(
        **_selector(arguments),
        dimension_kind=kind,
        value=value,
        tolerance=tolerance,
        text=_optional_string(arguments, "text"),
        limit=_bounded_int(arguments, "limit", 100),
        offset=_offset(arguments),
    )


def _execute_conflicts(service: CADQueryService, arguments: dict[str, Any]) -> dict[str, Any]:
    status = arguments.get("status", "open")
    if status is not None and status not in {"open", "resolved", "ignored"}:
        raise ToolExecutionError("status must be open, resolved, ignored, or null")
    return service.list_conflicts(
        **_selector(arguments),
        status=status,
        limit=_bounded_int(arguments, "limit", 100),
        offset=_offset(arguments),
    )


def _execute_feature_evidence(service: CADQueryService, arguments: dict[str, Any]) -> dict[str, Any]:
    feature_id = arguments.get("feature_id")
    if not isinstance(feature_id, str) or not feature_id.strip():
        raise ToolExecutionError("feature_id is required")
    return service.get_feature_evidence(feature_id=feature_id.strip())


def _execute_compare(service: CADQueryService, arguments: dict[str, Any]) -> dict[str, Any]:
    status = arguments.get("status")
    if status is not None and status not in {"confirmed", "cad_only", "ocr_only", "conflict", "ambiguous"}:
        raise ToolExecutionError("status is not a recognized fusion status")
    return service.compare_cad_ocr(
        **_selector(arguments),
        status=status,
        limit=_bounded_int(arguments, "limit", 100),
        offset=_offset(arguments),
    )


def _execute_hybrid_search(service: CADQueryService, arguments: dict[str, Any]) -> dict[str, Any]:
    query = _optional_string(arguments, "query")
    if not query:
        raise ToolExecutionError("query is required")
    dimension_kind = _optional_string(arguments, "dimension_kind")
    if dimension_kind:
        dimension_kind = dimension_kind.lower()
        if dimension_kind not in {"linear", "radius", "diameter", "angle"}:
            raise ToolExecutionError("dimension_kind must be linear, radius, diameter, or angle")
    dimension_value = arguments.get("dimension_value")
    if dimension_value is not None and (
        isinstance(dimension_value, bool)
        or not isinstance(dimension_value, (int, float))
        or dimension_value < 0
    ):
        raise ToolExecutionError("dimension_value must be a non-negative number")
    tolerance = arguments.get("tolerance", 0.01)
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or tolerance < 0:
        raise ToolExecutionError("tolerance must be a non-negative number")
    if dimension_value is not None and dimension_kind is None:
        raise ToolExecutionError("dimension_kind is required when dimension_value is provided")
    entity_types = arguments.get("entity_types", [])
    if not isinstance(entity_types, list) or any(not isinstance(item, str) for item in entity_types):
        raise ToolExecutionError("entity_types must be a list of strings")
    allowed_types = {
        "LINE", "CIRCLE", "ARC", "POLYLINE", "LWPOLYLINE",
        "TEXT", "MTEXT", "DIMENSION", "INSERT",
    }
    normalized_types = []
    for item in entity_types:
        normalized = item.strip().upper()
        if normalized not in allowed_types:
            raise ToolExecutionError(f"unsupported entity type: {item}")
        if normalized not in normalized_types:
            normalized_types.append(normalized)
    min_score = arguments.get("min_score", 0.35)
    if isinstance(min_score, bool) or not isinstance(min_score, (int, float)) or not 0 <= min_score <= 1:
        raise ToolExecutionError("min_score must be a number between 0 and 1")
    return service.hybrid_search(
        **_optional_selector(arguments),
        query=query,
        top_k=_bounded_int(arguments, "top_k", 30, 50),
        final_limit=_bounded_int(arguments, "final_limit", 10, 20),
        min_score=float(min_score),
        dimension_kind=dimension_kind,
        dimension_value=dimension_value,
        tolerance=float(tolerance),
        entity_types=normalized_types,
    )


def _execute_semantic_search(service: CADQueryService, arguments: dict[str, Any]) -> dict[str, Any]:
    query = _optional_string(arguments, "query")
    if not query:
        raise ToolExecutionError("query is required")
    source_type = _optional_string(arguments, "source_type")
    if source_type and source_type not in {"drawing_revision", "unified_feature"}:
        raise ToolExecutionError("source_type must be drawing_revision or unified_feature")
    min_score = arguments.get("min_score", 0.35)
    if isinstance(min_score, bool) or not isinstance(min_score, (int, float)) or not 0 <= min_score <= 1:
        raise ToolExecutionError("min_score must be a number between 0 and 1")
    return service.semantic_search(
        **_optional_selector(arguments),
        query=query,
        source_type=source_type,
        limit=_bounded_int(arguments, "limit", 10, 50),
        min_score=float(min_score),
    )


_EXECUTORS: dict[str, Callable[[CADQueryService, dict[str, Any]], dict[str, Any]]] = {
    "list_drawings": _execute_list_drawings,
    "get_drawing_summary": _execute_summary,
    "count_entities": _execute_count_entities,
    "list_entities": _execute_list_entities,
    "list_layers": _execute_layers,
    "list_blocks": _execute_blocks,
    "list_native_annotations": _execute_native,
    "list_ocr_items": _execute_ocr,
    "list_vision_observations": _execute_vision,
    "find_dimensions": _execute_dimensions,
    "list_conflicts": _execute_conflicts,
    "get_feature_evidence": _execute_feature_evidence,
    "compare_cad_ocr": _execute_compare,
    "semantic_search": _execute_semantic_search,
    "hybrid_search": _execute_hybrid_search,
}


def _selector_properties() -> dict[str, Any]:
    return {
        "drawing_key": {"type": "string", "description": "逻辑图纸 key，例如 2d-profile-drawing-with-radial-and-angular-dimension"},
        "revision_id": {"type": "string", "description": "明确的 drawing_revisions.id；查询历史版本时使用"},
    }


def _selector_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": _selector_properties(),
        "oneOf": [{"required": ["drawing_key"]}, {"required": ["revision_id"]}],
    }


def _merge_schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema = _selector_schema()
    schema["properties"].update(properties)
    if required:
        schema["required"] = list(dict.fromkeys(required))
    return schema


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {"type": "function", "function": {"name": "list_drawings", "description": "列出可查询的 CAD 图纸及最新版本的实体、OCR和融合统计。", "parameters": {"type": "object", "properties": {"search": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}, "offset": {"type": "integer", "minimum": 0}}}}},
    {"type": "function", "function": {"name": "get_drawing_summary", "description": "获取一张 CAD 图纸版本的精确汇总、源文件和解析跳过统计。", "parameters": _selector_schema()}},
    {"type": "function", "function": {"name": "count_entities", "description": "统计图纸中的 CAD 实体类型数量。", "parameters": _merge_schema({"entity_type": {"type": "string"}})}},
    {"type": "function", "function": {"name": "list_entities", "description": "查询 CAD 实体及其几何、图层、句柄和类型详情。", "parameters": _merge_schema({"entity_type": {"type": "string", "enum": sorted({"LINE", "CIRCLE", "ARC", "POLYLINE", "LWPOLYLINE", "TEXT", "MTEXT", "DIMENSION", "INSERT"})}, "layer_name": {"type": "string"}, "cad_id": {"type": "string"}, "include_raw_properties": {"type": "boolean"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}, "offset": {"type": "integer", "minimum": 0}})}},
    {"type": "function", "function": {"name": "list_layers", "description": "列出图层及每个图层的实体数量。", "parameters": _merge_schema({"limit": {"type": "integer", "minimum": 1, "maximum": 500}})}},
    {"type": "function", "function": {"name": "list_blocks", "description": "列出图块定义及成员实体数量。", "parameters": _merge_schema({"limit": {"type": "integer", "minimum": 1, "maximum": 500}})}},
    {"type": "function", "function": {"name": "list_native_annotations", "description": "查询 DWG 原生文字/尺寸归一化结果，保留原始文字和源实体关系。", "parameters": _merge_schema({"text": {"type": "string"}, "parsed_kind": {"type": "string", "enum": ["linear", "radius", "diameter", "angle"]}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}, "offset": {"type": "integer", "minimum": 0}})}},
    {"type": "function", "function": {"name": "list_ocr_items", "description": "查询当前 OCR 运行产生的文字观察、框、置信度和合并信息。", "parameters": _merge_schema({"text": {"type": "string"}, "min_confidence": {"type": "number", "minimum": 0, "maximum": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}, "offset": {"type": "integer", "minimum": 0}})}},
    {"type": "function", "function": {"name": "list_vision_observations", "description": "查询视觉模型对图纸产生的候选观察，例如剖视图、中心线、对称关系、标题栏和候选工程特征。Vision 结果不是精确 CAD 数值。", "parameters": _merge_schema({"observation_type": {"type": "string"}, "status": {"type": "string", "enum": ["candidate", "confirmed", "conflict", "ambiguous", "vision_only"]}, "min_confidence": {"type": "number", "minimum": 0, "maximum": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}, "offset": {"type": "integer", "minimum": 0}})}},
    {"type": "function", "function": {"name": "find_dimensions", "description": "按半径、直径、线性或角度及容差查找精确 CAD 尺寸，并返回原生和融合证据。", "parameters": _merge_schema({"dimension_kind": {"type": "string", "enum": ["linear", "radius", "diameter", "angle"]}, "value": {"type": "number", "minimum": 0}, "tolerance": {"type": "number", "minimum": 0, "default": 0.01}, "text": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}, "offset": {"type": "integer", "minimum": 0}})}},
    {"type": "function", "function": {"name": "list_conflicts", "description": "列出 CAD 与 OCR/融合之间的冲突及双方证据。", "parameters": _merge_schema({"status": {"type": ["string", "null"], "enum": ["open", "resolved", "ignored", None], "default": "open"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}, "offset": {"type": "integer", "minimum": 0}})}},
    {"type": "function", "function": {"name": "get_feature_evidence", "description": "根据 Unified Feature ID 获取 CAD、原生标注、OCR和冲突证据。", "parameters": {"type": "object", "properties": {"feature_id": {"type": "string"}}, "required": ["feature_id"]}}},
    {"type": "function", "function": {"name": "compare_cad_ocr", "description": "按融合状态查看 CAD/OCR 统一特征及证据数量。", "parameters": _merge_schema({"status": {"type": "string", "enum": ["confirmed", "cad_only", "ocr_only", "conflict", "ambiguous"]}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}, "offset": {"type": "integer", "minimum": 0}})}},
    {"type": "function", "function": {"name": "hybrid_search", "description": "混合检索 CAD 图纸：先用 Qdrant 按自然语言召回候选，再用 PostgreSQL 验证图纸版本、实体类型、尺寸和 Unified Feature 证据。回答语义问题时优先使用此工具；verified 才表示通过精确验证。", "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "自然语言检索问题，例如：查找带齿形结构且有中央通孔的机械零件"}, "drawing_key": {"type": "string"}, "revision_id": {"type": "string"}, "top_k": {"type": "integer", "minimum": 1, "maximum": 50, "default": 30}, "final_limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10}, "min_score": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.35}, "dimension_kind": {"type": "string", "enum": ["linear", "radius", "diameter", "angle"]}, "dimension_value": {"type": "number", "minimum": 0}, "tolerance": {"type": "number", "minimum": 0, "default": 0.01}, "entity_types": {"type": "array", "items": {"type": "string", "enum": ["LINE", "CIRCLE", "ARC", "POLYLINE", "LWPOLYLINE", "TEXT", "MTEXT", "DIMENSION", "INSERT"]}}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "semantic_search", "description": "按自然语言含义搜索相似图纸和统一工程特征。结果用于低层语义召回；需要精确验证时优先使用 hybrid_search。", "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "自然语言检索意图，例如：查找带T型槽和中央通孔的对称型材"}, "drawing_key": {"type": "string"}, "revision_id": {"type": "string"}, "source_type": {"type": "string", "enum": ["drawing_revision", "unified_feature"]}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}, "min_score": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.35}}, "required": ["query"]}}},
]


def execute_tool(service: CADQueryService, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate and execute one whitelisted tool call."""

    if name not in _EXECUTORS:
        raise ToolExecutionError(f"unknown CAD query tool: {name}")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ToolExecutionError("tool arguments must be a JSON object")
    try:
        return _EXECUTORS[name](service, arguments)
    except ToolExecutionError:
        raise
    except (ValueError, LookupError, RuntimeError) as exc:
        raise ToolExecutionError(str(exc)) from exc
