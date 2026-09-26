"""Thin synchronous client for the Ollama HTTP API (chat / stream / pull / show)."""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx


class OllamaError(Exception):
    """Raised when the Ollama server returns an error or is unreachable."""


class OllamaClient:
    def __init__(self, host: str, model: str, timeout: float = 600.0):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout

    def _url(self, path: str) -> str:
        return f"{self.host}{path}"

    # -- health -----------------------------------------------------------

    def is_alive(self) -> bool:
        try:
            r = httpx.get(self._url("/api/tags"), timeout=5)
            return r.status_code == 200
        except httpx.RequestError:
            return False

    def exists(self) -> bool:
        try:
            r = httpx.post(self._url("/api/show"), json={"name": self.model}, timeout=30)
            return r.status_code == 200
        except httpx.RequestError:
            return False

    def list_models(self) -> list[str]:
        r = httpx.get(self._url("/api/tags"), timeout=30)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]

    # -- pull -------------------------------------------------------------

    def pull(self, stream: bool = True) -> Iterator[dict[str, Any]]:
        with httpx.stream(
            "POST",
            self._url("/api/pull"),
            json={"name": self.model, "stream": stream},
            timeout=None,
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if line:
                    yield json.loads(line)

    # -- create ------------------------------------------------------------

    def create(self, name: str, modelfile: str) -> str:
        """Create a model from Modelfile text (POST /api/create).

        Returns the last status reported by the server (e.g. "success").
        """
        last_status = ""
        with httpx.stream(
            "POST",
            self._url("/api/create"),
            json={"model": name, "modelfile": modelfile, "stream": True},
            timeout=None,
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                try:
                    status = json.loads(line).get("status", "")
                except json.JSONDecodeError:
                    continue
                if status:
                    last_status = status
        return last_status

    # -- chat -------------------------------------------------------------

    def chat(
        self,
        messages: list[dict[str, str]],
        options: dict[str, Any] | None = None,
        format: dict | str | None = None,  # noqa: A002 - Ollama API field name
    ) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": options or {},
        }
        if format is not None:
            payload["format"] = format
        r = httpx.post(self._url("/api/chat"), json=payload, timeout=self.timeout)
        if r.status_code != 200:
            raise OllamaError(f"Ollama chat failed ({r.status_code}): {r.text[:300]}")
        return r.json()["message"]["content"]

    def stream_chat(
        self,
        messages: list[dict[str, str]],
        options: dict[str, Any] | None = None,
        format: dict | str | None = None,  # noqa: A002 - Ollama API field name
    ) -> Iterator[str]:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": options or {},
        }
        if format is not None:
            payload["format"] = format
        with httpx.stream(
            "POST", self._url("/api/chat"), json=payload, timeout=None
        ) as r:
            if r.status_code != 200:
                raise OllamaError(
                    f"Ollama stream failed ({r.status_code}): {r.text[:300]}"
                )
            for line in r.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                if chunk.get("done") and not chunk.get("message"):
                    continue
                delta = chunk.get("message", {}).get("content", "")
                if delta:
                    yield delta
