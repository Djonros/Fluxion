"""APIBackend: OpenAI-compatible API client for cloud inference.

Works with any provider that exposes an OpenAI-style /v1/chat/completions
endpoint: OpenRouter, Together AI, Groq, Fireworks, Anyscale, vLLM, etc.
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from typing import Any

import httpx

from .config import GenerationSettings, Settings
from .inference import ModelBackend

logger = logging.getLogger(__name__)


class APIBackend(ModelBackend):
    """OpenAI-compatible API backend.

    Set environment variables:
        FLUXION_API_KEY       — API key for the provider
        FLUXION_API_BASE_URL  — base URL (default: OpenRouter)
        FLUXION_API_MODEL     — model name (default: from Settings)
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        model: str = "qwen/qwen2.5-coder-7b-instruct",
        timeout: float = 120.0,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    @classmethod
    def from_settings(cls, settings: Settings) -> APIBackend:
        return cls(
            api_key=os.environ.get("FLUXION_API_KEY", settings.hf_token or ""),
            base_url=os.environ.get("FLUXION_API_BASE_URL", "https://openrouter.ai/api/v1"),
            model=os.environ.get("FLUXION_API_MODEL", settings.model),
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(
        self,
        messages: list[dict[str, str]],
        gen: GenerationSettings | None,
        stream: bool,
    ) -> dict[str, Any]:
        g = gen or GenerationSettings()
        return {
            "model": self.model,
            "messages": messages,
            "temperature": g.temperature,
            "top_p": g.top_p,
            "max_tokens": g.max_tokens,
            "stream": stream,
        }

    def generate(
        self,
        messages: list[dict[str, str]],
        gen: GenerationSettings | None = None,
    ) -> str:
        payload = self._payload(messages, gen, stream=False)
        r = httpx.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            headers=self._headers(),
            timeout=self.timeout,
        )
        if r.status_code != 200:
            raise RuntimeError(f"API request failed ({r.status_code}): {r.text[:300]}")
        return r.json()["choices"][0]["message"]["content"]

    def stream(
        self,
        messages: list[dict[str, str]],
        gen: GenerationSettings | None = None,
    ) -> Iterator[str]:
        payload = self._payload(messages, gen, stream=True)
        with httpx.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            json=payload,
            headers=self._headers(),
            timeout=None,
        ) as r:
            if r.status_code != 200:
                raise RuntimeError(f"API stream failed ({r.status_code}): {r.text[:300]}")
            for line in r.iter_lines():
                if not line or line == "data: [DONE]":
                    continue
                if line.startswith("data: "):
                    line = line[6:]
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                if delta:
                    yield delta

    def is_available(self) -> bool:
        return bool(self.api_key)
