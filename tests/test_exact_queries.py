#!/usr/bin/env python3
"""Integration tests against the imported local cad_kb database."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg  # noqa: E402

from app.repositories import DrawingRepository  # noqa: E402
from app.services.cad_query_service import CADQueryService  # noqa: E402
from app.tools.cad_query_tools import ToolExecutionError, execute_tool  # noqa: E402


DSN = os.getenv("DATABASE_URL", "postgresql:///cad_kb")
RADIAL_KEY = "2d-profile-drawing-with-radial-and-angular-dimension"


class ExactQueryIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.connection = psycopg.connect(DSN)
        cls.repository = DrawingRepository(cls.connection)
        cls.service = CADQueryService(cls.repository)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

    def test_list_drawings_returns_six_imported_drawings(self) -> None:
        result = self.repository.list_drawings()
        self.assertEqual(result["count"], 6)
        self.assertEqual(sum(item["entity_count"] for item in result["items"]), 850)

    def test_summary_preserves_counts_and_skipped_entities(self) -> None:
        result = self.repository.get_drawing_summary(drawing_key=RADIAL_KEY)
        self.assertEqual(result["counts"]["entities"]["LINE"], 77)
        self.assertEqual(result["counts"]["entities"]["DIMENSION"], 20)
        self.assertEqual(result["counts"]["native_annotations"], 20)
        self.assertEqual(result["counts"]["ocr_items"], 21)
        self.assertIn("skipped_entity_counts", result["cad_parse"]["statistics"])

    def test_dimension_query_uses_normalized_value_and_returns_evidence(self) -> None:
        result = self.repository.find_dimensions(
            drawing_key=RADIAL_KEY,
            dimension_kind="radius",
            value=40,
            tolerance=0.01,
        )
        self.assertGreaterEqual(result["count"], 1)
        self.assertTrue(all(item["dimension"]["kind"] == "radius" for item in result["items"]))
        self.assertTrue(all(abs(item["dimension"]["measurement"] - 40) <= 0.01 for item in result["items"]))
        self.assertTrue(any(item["native_annotation"] for item in result["items"]))

    def test_entity_query_returns_geometry(self) -> None:
        result = self.repository.list_entities(drawing_key=RADIAL_KEY, entity_type="CIRCLE", limit=20)
        self.assertEqual(result["count"], 7)
        self.assertIn("center", result["items"][0]["geometry"])
        self.assertIn("radius", result["items"][0]["geometry"])

    def test_tool_executor_is_whitelisted_and_validates_scope(self) -> None:
        result = execute_tool(self.service, "count_entities", {"drawing_key": RADIAL_KEY, "entity_type": "circle"})
        self.assertEqual(result["by_type"], {"CIRCLE": 7})
        with self.assertRaises(ToolExecutionError):
            execute_tool(self.service, "count_entities", {})
        with self.assertRaises(ToolExecutionError):
            execute_tool(self.service, "unknown_sql", {})

    def test_feature_evidence_has_no_orphan_for_known_feature(self) -> None:
        feature = self.repository.compare_cad_ocr(drawing_key=RADIAL_KEY, limit=1)["items"][0]
        result = self.repository.get_feature_evidence(feature["id"])
        self.assertEqual(result["feature"]["id"], feature["id"])
        self.assertIn("evidence", result)

    def test_vision_observations_are_queryable_and_linked(self) -> None:
        result = self.repository.list_vision_observations(drawing_key=RADIAL_KEY)
        self.assertEqual(result["count"], 8)
        self.assertTrue(all(item["status"] == "candidate" for item in result["items"]))
        with self.connection.cursor() as cur:
            cur.execute(
                """
                SELECT f.id
                FROM fusion.features AS f
                JOIN fusion.runs AS r ON r.id = f.fusion_run_id AND r.is_current
                JOIN fusion.feature_vision_observations AS link ON link.feature_id = f.id
                WHERE f.drawing_revision_id = %s
                LIMIT 1
                """,
                (result["drawing"]["revision_id"],),
            )
            feature_id = cur.fetchone()[0]
        evidence = self.repository.get_feature_evidence(feature_id)
        self.assertGreaterEqual(len(evidence["evidence"]["vision_observations"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
