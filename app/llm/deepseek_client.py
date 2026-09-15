"""OpenAI-compatible DeepSeek client used by the CAD chat service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from openai import OpenAI


class DeepSeekConfigurationError(RuntimeError):
    """Raised when the server is missing a required DeepSeek setting."""


class DeepSeekRequestError(RuntimeError):
    """Raised when a request to DeepSeek cannot be completed."""


@dataclass(frozen=True)
class DeepSeekConfig:
    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-flash"
    thinking: str = "disabled"
    timeout_seconds: float = 30.0
    max_tool_rounds: int = 4

    @classmethod
    def from_env(cls) -> "DeepSeekConfig":
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise DeepSeekConfigurationError(
                "DEEPSEEK_API_KEY is not configured; set it in the server environment"
            )
        try:
            timeout = float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "30"))
            rounds = int(os.getenv("DEEPSEEK_MAX_TOOL_ROUNDS", "4"))
        except ValueError as exc:
            raise DeepSeekConfigurationError("DeepSeek timeout and round settings must be numeric") from exc
        if timeout <= 0:
            raise DeepSeekConfigurationError("DEEPSEEK_TIMEOUT_SECONDS must be positive")
        if not 1 <= rounds <= 8:
            raise DeepSeekConfigurationError("DEEPSEEK_MAX_TOOL_ROUNDS must be between 1 and 8")
        thinking = os.getenv("DEEPSEEK_THINKING", "disabled").strip().lower()
        if thinking not in {"enabled", "disabled"}:
            raise DeepSeekConfigurationError("DEEPSEEK_THINKING must be enabled or disabled")
        return cls(
            api_key=api_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip(),
            model=os.getenv(
                "DEEPSEEK_TEXT_MODEL",
                os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            ).strip(),
            thinking=thinking,
            timeout_seconds=timeout,
            max_tool_rounds=rounds,
        )


class DeepSeekClient:
    """Small provider adapter; the chat service owns the tool loop."""

    def __init__(self, config: DeepSeekConfig):
        self.config = config
        self._client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_seconds,
        )

    @classmethod
    def from_env(cls) -> "DeepSeekClient":
        return cls(DeepSeekConfig.from_env())

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Any:
        request: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
        }
        # The first demo explicitly disables thinking. If enabled later,
        # reasoning_content must be retained and passed through every tool turn.
        request["extra_body"] = {"thinking": {"type": self.config.thinking}}
        try:
            return self._client.chat.completions.create(**request)
        except Exception as exc:  # provider-specific exceptions vary by SDK version
            raise DeepSeekRequestError("DeepSeek request failed; inspect server logs for the provider error") from exc
