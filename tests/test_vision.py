#!/usr/bin/env python3
"""Unit tests for the local Vision integration without external API calls."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.llm.deepseek_vision_client import (  # noqa: E402
    DeepSeekVisionClient,
    DeepSeekVisionConfig,
)
from app.vision.schema import normalize_vision_response  # noqa: E402


PNG = ROOT / "dataset/AutoCAD_2D_Drawings/2D PROFILE DRAWING WITH RADIAL AND ANGULAR DIMENSION.png"


class FakeCompletions:
    def __init__(self):
        self.request = None

    def create(self, **request):
        self.request = request
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"observations": []}')
                )
            ]
        )


class FakeOpenAI:
    def __init__(self):
        self.chat = SimpleNamespace(completions=FakeCompletions())


class VisionIntegrationTests(unittest.TestCase):
    def test_config_uses_separate_vision_model(self):
        with patch.dict(
            os.environ,
            {
                "DEEPSEEK_API_KEY": "test-key",
                "DEEPSEEK_VISION_MODEL": "test-vision-model",
                "DEEPSEEK_VISION_DETAIL": "low",
            },
            clear=False,
        ):
            config = DeepSeekVisionConfig.from_env()
        self.assertEqual(config.model, "test-vision-model")
        self.assertEqual(config.detail, "low")

    def test_client_sends_text_and_image_content(self):
        client = DeepSeekVisionClient(
            DeepSeekVisionConfig(api_key="test-key", model="vision-test")
        )
        fake = FakeOpenAI()
        client._client = fake
        client.analyze_data_url(
            data_url="data:image/png;base64,AAAA",
            prompt="return JSON",
        )
        request = fake.chat.completions.request
        self.assertEqual(request["model"], "vision-test")
        self.assertEqual(request["messages"][0]["role"], "user")
        self.assertEqual(request["messages"][0]["content"][0]["type"], "text")
        self.assertEqual(request["messages"][0]["content"][1]["type"], "image_url")
        self.assertEqual(
            request["messages"][0]["content"][1]["image_url"]["detail"],
            "original",
        )

    def test_normalizer_adds_normalized_region_and_forces_candidate(self):
        document = normalize_vision_response(
            response_text="""{
              "drawing_type": "sectioned_part",
              "summary": "A drawing",
              "observations": [
                {
                  "id": "VIS_9",
                  "observation_type": "feature_candidate",
                  "description": "possible hole",
                  "region_px": [192, 108, 960, 540],
                  "model_confidence": 0.8,
                  "status": "confirmed"
                }
              ]
            }""",
            image_path=PNG,
            drawing_key="demo",
            model_name="vision-test",
            prompt_version="test",
            detail="original",
        )
        self.assertEqual((document.image.width, document.image.height), (1920, 1080))
        self.assertEqual(document.observations[0].status, "candidate")
        self.assertEqual(
            document.observations[0].region_normalized,
            [0.1, 0.1, 0.5, 0.5],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
