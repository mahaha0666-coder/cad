"""OpenAI-compatible client for DeepSeek's image-capable model."""

from __future__ import annotations

import base64
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI

from .deepseek_client import DeepSeekConfigurationError, DeepSeekRequestError

VISION_DETAILS = {"low", "high", "original", "auto"}
THINKING_MODES = {"enabled", "disabled"}


@dataclass(frozen=True)
class DeepSeekVisionConfig:
    """Configuration kept separate from the text/function-calling model."""

    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-flash-vision-exp"
    detail: str = "original"
    timeout_seconds: float = 60.0
    max_output_tokens: int = 4096
    thinking: str = "disabled"

    @classmethod
    def from_env(cls) -> "DeepSeekVisionConfig":
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise DeepSeekConfigurationError(
                "DEEPSEEK_API_KEY is not configured; set it in the server environment"
            )
        try:
            timeout = float(os.getenv("DEEPSEEK_VISION_TIMEOUT_SECONDS", "60"))
            max_output_tokens = int(
                os.getenv("DEEPSEEK_VISION_MAX_OUTPUT_TOKENS", "4096")
            )
        except ValueError as exc:
            raise DeepSeekConfigurationError(
                "DeepSeek vision timeout and max output settings must be numeric"
            ) from exc
        if timeout <= 0:
            raise DeepSeekConfigurationError(
                "DEEPSEEK_VISION_TIMEOUT_SECONDS must be positive"
            )
        if not 256 <= max_output_tokens <= 32768:
            raise DeepSeekConfigurationError(
                "DEEPSEEK_VISION_MAX_OUTPUT_TOKENS must be between 256 and 32768"
            )
        detail = os.getenv("DEEPSEEK_VISION_DETAIL", "original").strip().lower()
        if detail not in VISION_DETAILS:
            raise DeepSeekConfigurationError(
                f"DEEPSEEK_VISION_DETAIL must be one of {sorted(VISION_DETAILS)}"
            )
        thinking = os.getenv("DEEPSEEK_VISION_THINKING", "disabled").strip().lower()
        if thinking not in THINKING_MODES:
            raise DeepSeekConfigurationError(
                f"DEEPSEEK_VISION_THINKING must be one of {sorted(THINKING_MODES)}"
            )
        return cls(
            api_key=api_key,
            base_url=os.getenv(
                "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
            ).strip(),
            model=os.getenv(
                "DEEPSEEK_VISION_MODEL",
                "deepseek-v4-flash-vision-exp",
            ).strip(),
            detail=detail,
            timeout_seconds=timeout,
            max_output_tokens=max_output_tokens,
            thinking=thinking,
        )


class DeepSeekVisionClient:
    """Send one local image to the vision model and return the SDK response."""

    def __init__(self, config: DeepSeekVisionConfig):
        self.config = config
        self._client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_seconds,
        )

    @classmethod
    def from_env(cls) -> "DeepSeekVisionClient":
        return cls(DeepSeekVisionConfig.from_env())

    def analyze_file(self, image_path: Path, prompt: str) -> Any:
        if not image_path.is_file():
            raise FileNotFoundError(f"Vision image does not exist: {image_path}")
        media_type, _ = mimetypes.guess_type(image_path.name)
        if media_type not in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
            raise ValueError("Vision input must be a PNG, JPEG, GIF, or WebP image")
        image_data = base64.b64encode(image_path.read_bytes()).decode("ascii")
        return self.analyze_data_url(
            data_url=f"data:{media_type};base64,{image_data}",
            prompt=prompt,
        )

    def analyze_data_url(self, *, data_url: str, prompt: str) -> Any:
        if not data_url.startswith("data:image/"):
            raise ValueError("Vision data_url must be an image data URL")
        if not prompt.strip():
            raise ValueError("Vision prompt must not be empty")
        request: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": data_url,
                                "detail": self.config.detail,
                            },
                        },
                    ],
                }
            ],
            "max_tokens": self.config.max_output_tokens,
            "extra_body": {"thinking": {"type": self.config.thinking}},
        }
        try:
            return self._client.chat.completions.create(**request)
        except Exception as exc:
            raise DeepSeekRequestError(
                "DeepSeek vision request failed; inspect server logs for the provider error"
            ) from exc


def response_text(response: Any) -> str:
    """Extract response text without exposing the provider response object."""

    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise DeepSeekRequestError(
            "DeepSeek vision response did not contain message content"
        ) from exc
    if not isinstance(content, str) or not content.strip():
        raise DeepSeekRequestError("DeepSeek vision response content was empty")
    return content.strip()
