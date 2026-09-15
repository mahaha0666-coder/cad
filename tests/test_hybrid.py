#!/usr/bin/env python3
"""Tests for Qdrant recall followed by PostgreSQL verification."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.retrieval import HybridRetrievalService  # noqa: E402


class FakeSemanticService:
    def __init__(self, items):
        self.items = items

    def search(self, **kwargs):
        return {
            "collection": "cad_semantic_chunks",
            "embedding_model": "test-model",
            "scope": {},
            "items": self.items,
        }


class FakeRepository:
    def __init__(self):
        self.revisions = {
            "revision-1": {"revision_id": "revision-1", "drawing_key": "drawing-one", "title": "Drawing One", "revision_sequence": 1},
            "revision-2": {"revision_id": "revision-2", "drawing_key": "drawing-two", "title": "Drawing Two", "revision_sequence": 1},
        }

    def get_revisions_by_ids(self, revision_ids):
        return [self.revisions[item] for item in revision_ids if item in self.revisions]

    def get_current_features_by_ids(self, feature_ids):
        if "feature-2" not in feature_ids:
            return []
        return [{
            "feature_id": "feature-2", "revision_id": "revision-2", "external_id": "UF_0002",
            "feature_type": "dimension_annotation", "canonical_text": "R100", "dimension_kind": "radius",
            "canonical_value": 100.0, "unit": None, "fusion_status": "confirmed", "fusion_score": 0.99,
            "cad_evidence_count": 2, "native_evidence_count": 1, "ocr_evidence_count": 1, "vision_evidence_count": 0,
        }]

    def get_drawing_summary(self, *, revision_id):
        return {"drawing": self.revisions[revision_id], "counts": {"entities": {"CIRCLE": 1}}, "assets": []}

    def find_dimensions(self, *, revision_id, **kwargs):
        if revision_id == "revision-1":
            return {"items": [{"dimension": {"kind": "radius", "measurement": 100.0}}]}
        return {"items": []}

    def record_hybrid_search(self, **kwargs):
        self.audit = kwargs
        return "run-1"


class HybridRetrievalTests(unittest.TestCase):
    def test_explicit_dimension_is_verified_by_postgresql(self):
        semantic = FakeSemanticService([
            {"score": 0.80, "revision_id": "revision-1", "drawing_key": "drawing-one", "drawing_title": "Drawing One", "source_type": "drawing_revision", "source_id": "revision-1", "source_external_id": "revision-1", "chunk_id": "chunk-1"},
            {"score": 0.70, "revision_id": "revision-2", "drawing_key": "drawing-two", "drawing_title": "Drawing Two", "source_type": "unified_feature", "source_id": "feature-2", "source_external_id": "UF_0002", "chunk_id": "chunk-2"},
        ])
        result = HybridRetrievalService(semantic).search(FakeRepository(), query="R100 半径", top_k=10, final_limit=10)
        self.assertEqual(result["items"][0]["drawing_key"], "drawing-one")
        self.assertEqual(result["items"][0]["status"], "verified")
        self.assertEqual(result["items"][1]["status"], "rejected")
        self.assertEqual(result["search_run_id"], "run-1")

    def test_pure_semantic_result_remains_provisional(self):
        semantic = FakeSemanticService([
            {"score": 0.80, "revision_id": "revision-2", "drawing_key": "drawing-two", "drawing_title": "Drawing Two", "source_type": "unified_feature", "source_id": "feature-2", "source_external_id": "UF_0002", "chunk_id": "chunk-2"},
        ])
        result = HybridRetrievalService(semantic).search(FakeRepository(), query="带T型槽的型材", top_k=10, final_limit=10)
        self.assertEqual(result["items"][0]["status"], "partially_verified")
        self.assertIn("语义候选", result["warnings"][0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
