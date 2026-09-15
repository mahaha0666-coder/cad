#!/usr/bin/env python3
"""Unit tests for the DeepSeek tool loop without making a network request."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.schemas.chat import ChatRequest  # noqa: E402
from app.services.chat_service import CADChatService  # noqa: E402


def assistant_message(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls, reasoning_content=None)


def tool_call(call_id, name, arguments):
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.config = SimpleNamespace(max_tool_rounds=4)
        self.requests = []

    def complete(self, *, messages, tools):
        self.requests.append(messages)
        return next(self.responses)


class CADChatServiceTests(unittest.TestCase):
    def test_tool_call_is_executed_and_scope_is_bound(self):
        client = FakeClient([
            SimpleNamespace(choices=[SimpleNamespace(message=assistant_message(
                tool_calls=[tool_call("call_1", "count_entities", '{"entity_type":"CIRCLE"}')]
            ))]),
            SimpleNamespace(choices=[SimpleNamespace(message=assistant_message("这张图有 7 个圆。"))]),
        ])
        service = SimpleNamespace()
        service.count_entities = lambda **kwargs: {
            "drawing": {"drawing_key": kwargs["drawing_key"]},
            "total": 7,
            "by_type": {"CIRCLE": 7},
        }
        chat = CADChatService(client)
        response = chat.chat(service, ChatRequest(
            message="有几个圆？",
            drawing_key="2d-profile-drawing-with-radial-and-angular-dimension",
        ))
        self.assertEqual(response.answer, "这张图有 7 个圆。")
        self.assertEqual(response.tool_calls[0].status, "success")
        tool_message = next(item for item in client.requests[1] if item.get("role") == "tool")
        self.assertEqual(tool_message["role"], "tool")
        self.assertIn("2d-profile-drawing-with-radial-and-angular-dimension", tool_message["content"])

    def test_model_cannot_escape_active_drawing_scope(self):
        client = FakeClient([
            SimpleNamespace(choices=[SimpleNamespace(message=assistant_message(
                tool_calls=[tool_call("call_1", "count_entities", '{"drawing_key":"other","entity_type":"CIRCLE"}')]
            ))]),
            SimpleNamespace(choices=[SimpleNamespace(message=assistant_message("无法查询指定范围外的图纸。"))]),
        ])
        service = SimpleNamespace(count_entities=lambda **_: {"count": 0})
        response = CADChatService(client).chat(service, ChatRequest(
            message="查询另一个图纸",
            drawing_key="2d-profile-drawing-with-radial-and-angular-dimension",
        ))
        self.assertEqual(response.tool_calls[0].status, "error")
        self.assertIn("outside the active drawing scope", response.tool_calls[0].error)

    def test_tool_loop_has_a_round_limit(self):
        client = FakeClient([
            SimpleNamespace(choices=[SimpleNamespace(message=assistant_message(
                tool_calls=[tool_call("call_1", "count_entities", '{"entity_type":"CIRCLE"}')]
            ))]),
        ])
        client.config.max_tool_rounds = 1
        service = SimpleNamespace(count_entities=lambda **_: {"count": 7})
        with self.assertRaisesRegex(RuntimeError, "maximum CAD tool-call rounds"):
            CADChatService(client).chat(service, ChatRequest(message="有几个圆？", drawing_key="demo"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
