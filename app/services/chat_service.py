"""DeepSeek tool-calling orchestration for CAD questions."""

from __future__ import annotations

import json
import threading
from typing import Any
from uuid import uuid4

from app.llm.deepseek_client import DeepSeekClient, DeepSeekRequestError
from app.prompts.cad_assistant import build_system_prompt
from app.schemas.chat import ChatRequest, ChatResponse, EvidenceReference, ToolTrace
from app.tools.cad_query_tools import TOOL_DEFINITIONS, ToolExecutionError, execute_tool

from .tool_result_formatter import collect_evidence, compact_tool_result, result_count


class ChatLoopError(RuntimeError):
    """Raised when the model does not finish within the server tool budget."""


SCOPED_TOOLS = {
    "get_drawing_summary",
    "count_entities",
    "list_entities",
    "list_layers",
    "list_blocks",
    "list_native_annotations",
    "list_ocr_items",
    "list_vision_observations",
    "find_dimensions",
    "list_conflicts",
    "compare_cad_ocr",
    "semantic_search",
    "hybrid_search",
}


class CADChatService:
    """Owns the model/tool loop and a small in-memory demo session store."""

    def __init__(self, client: DeepSeekClient):
        self.client = client
        self._sessions: dict[str, list[dict[str, Any]]] = {}
        self._scopes: dict[str, dict[str, str]] = {}
        self._lock = threading.Lock()

    def chat(self, query_service: Any, request: ChatRequest) -> ChatResponse:
        session_id = request.session_id or str(uuid4())
        scope = self._resolve_scope(session_id, request)
        messages = self._initial_messages(session_id, request, scope)
        messages.append({"role": "user", "content": request.message})
        traces: list[ToolTrace] = []
        evidence: list[dict[str, str | None]] = []
        warnings: list[str] = []

        for _ in range(self.client.config.max_tool_rounds):
            response = self.client.complete(messages=messages, tools=TOOL_DEFINITIONS)
            message = response.choices[0].message
            assistant_message = self._assistant_message(message)
            messages.append(assistant_message)
            tool_calls = getattr(message, "tool_calls", None) or []
            if not tool_calls:
                answer = (getattr(message, "content", None) or "").strip()
                if not answer:
                    raise ChatLoopError("DeepSeek returned neither a final answer nor a tool call")
                self._save_session(session_id, messages, scope)
                return ChatResponse(
                    session_id=session_id,
                    answer=answer,
                    drawing_scope=scope or None,
                    tool_calls=traces,
                    evidence=[EvidenceReference(**item) for item in evidence],
                    warnings=warnings,
                )

            for tool_call in tool_calls:
                if len(traces) >= 16:
                    raise ChatLoopError("too many tool calls in one request")
                name = getattr(tool_call.function, "name", "")
                raw_arguments = getattr(tool_call.function, "arguments", "{}") or "{}"
                call_id = getattr(tool_call, "id", "unknown")
                arguments: dict[str, Any] = {}
                try:
                    parsed = json.loads(raw_arguments)
                    if not isinstance(parsed, dict):
                        raise ValueError("tool arguments must be a JSON object")
                    arguments = self._bind_scope(name, parsed, scope)
                    result = execute_tool(query_service, name, arguments)
                    compact = compact_tool_result(result)
                    traces.append(ToolTrace(
                        call_id=call_id,
                        name=name,
                        arguments=arguments,
                        status="success",
                        result_count=result_count(result),
                    ))
                    evidence.extend(collect_evidence(result))
                except (ValueError, ToolExecutionError) as exc:
                    error = str(exc)
                    compact = {"ok": False, "error": error}
                    traces.append(ToolTrace(
                        call_id=call_id,
                        name=name or "unknown",
                        arguments=arguments,
                        status="error",
                        error=error,
                    ))
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(compact, ensure_ascii=False, separators=(",", ":")),
                })

        self._save_session(session_id, messages, scope)
        raise ChatLoopError("DeepSeek exceeded the maximum CAD tool-call rounds")

    def _resolve_scope(self, session_id: str, request: ChatRequest) -> dict[str, str]:
        if request.drawing_key:
            scope = {"drawing_key": request.drawing_key}
        elif request.revision_id:
            scope = {"revision_id": request.revision_id}
        else:
            scope = self._scopes.get(session_id, {})
        if scope:
            self._scopes[session_id] = scope
        return scope

    def _initial_messages(
        self,
        session_id: str,
        request: ChatRequest,
        scope: dict[str, str],
    ) -> list[dict[str, Any]]:
        system = {"role": "system", "content": build_system_prompt(**scope)}
        if request.history:
            return [system] + [item.model_dump() for item in request.history]
        saved = self._sessions.get(session_id)
        if saved:
            return [system] + [item for item in saved if item.get("role") != "system"]
        return [system]

    @staticmethod
    def _bind_scope(name: str, arguments: dict[str, Any], scope: dict[str, str]) -> dict[str, Any]:
        if name not in SCOPED_TOOLS or not scope:
            return arguments
        bound = dict(arguments)
        scope_key, scope_value = next(iter(scope.items()))
        supplied_key = bound.get("drawing_key") or bound.get("revision_id")
        if supplied_key and supplied_key != scope_value:
            raise ToolExecutionError("tool attempted to query outside the active drawing scope")
        if not supplied_key:
            bound[scope_key] = scope_value
        return bound

    @staticmethod
    def _assistant_message(message: Any) -> dict[str, Any]:
        result: dict[str, Any] = {
            "role": "assistant",
            "content": getattr(message, "content", None),
        }
        reasoning = getattr(message, "reasoning_content", None)
        if reasoning:
            result["reasoning_content"] = reasoning
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            result["tool_calls"] = [
                {
                    "id": call.id,
                    "type": getattr(call, "type", "function"),
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in tool_calls
            ]
        return result

    def _save_session(self, session_id: str, messages: list[dict[str, Any]], scope: dict[str, str]) -> None:
        # Store only a bounded conversation for the local demo. Long-term
        # session persistence belongs in a later PostgreSQL chat migration.
        with self._lock:
            system = next((item for item in messages if item.get("role") == "system"), None)
            tail = [item for item in messages if item.get("role") != "system"][-30:]
            self._sessions[session_id] = ([system] if system else []) + tail
            if scope:
                self._scopes[session_id] = scope
