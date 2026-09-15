#!/usr/bin/env python3
"""Tests for semantic chunk construction and tool routing."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import psycopg


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.semantic.chunks import build_semantic_chunks  # noqa: E402
from app.semantic.config import SemanticConfig  # noqa: E402
from app.semantic.service import SemanticSearchService  # noqa: E402
from app.tools.cad_query_tools import ToolExecutionError, execute_tool  # noqa: E402


class FakeModel:
    def query_embed(self, _: str):
        yield np.zeros(512, dtype=np.float32)


class FakeClient:
    def get_collections(self):
        return SimpleNamespace(collections=[SimpleNamespace(name="cad_semantic_chunks")])

    def query_points(self, **kwargs):
        assert kwargs["limit"] == 3
        return SimpleNamespace(points=[SimpleNamespace(
            score=0.88,
            payload={
                "chunk_id": "chunk-1",
                "drawing_revision_id": "revision-1",
                "drawing_key": "drawing-1",
                "drawing_title": "Drawing 1",
                "source_type": "unified_feature",
                "source_id": "feature-1",
                "source_external_id": "UF_0001",
                "chunk_kind": "unified_feature",
                "content": "中央圆形通孔。",
                "feature_type": "vision_observation",
                "fusion_status": "vision_only",
            },
        )])


class SemanticTests(unittest.TestCase):
    def test_chunk_builder_uses_current_database_data(self) -> None:
        with psycopg.connect(os.getenv("DATABASE_URL", "postgresql:///cad_kb")) as connection:
            chunks = build_semantic_chunks(connection)
        self.assertEqual(len(chunks), 222)
        self.assertEqual(sum(chunk.source_type == "drawing_revision" for chunk in chunks), 6)
        self.assertEqual(sum(chunk.source_type == "unified_feature" for chunk in chunks), 216)
        self.assertTrue(any("T型槽" in chunk.content for chunk in chunks))

    def test_search_returns_traceable_payload(self) -> None:
        service = SemanticSearchService(SemanticConfig())
        service._client = FakeClient()
        service._model = FakeModel()
        result = service.search(query="带中央通孔的图纸", limit=3)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["source_id"], "feature-1")
        self.assertEqual(result["items"][0]["score"], 0.88)

    def test_semantic_tool_allows_global_or_scoped_search(self) -> None:
        fake_service = SimpleNamespace(semantic_search=lambda **kwargs: kwargs)
        global_result = execute_tool(fake_service, "semantic_search", {"query": "对称安装板"})
        self.assertEqual(global_result["query"], "对称安装板")
        scoped_result = execute_tool(fake_service, "semantic_search", {
            "query": "中央孔",
            "drawing_key": "drawing-1",
        })
        self.assertEqual(scoped_result["drawing_key"], "drawing-1")
        with self.assertRaises(ToolExecutionError):
            execute_tool(fake_service, "semantic_search", {
                "query": "中央孔",
                "drawing_key": "drawing-1",
                "revision_id": "revision-1",
            })


if __name__ == "__main__":
    unittest.main(verbosity=2)
