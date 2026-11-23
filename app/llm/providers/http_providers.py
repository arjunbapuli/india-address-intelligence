"""HTTP implementations for OpenAI, Anthropic, Google Gemini and OpenAI-compatible servers.

Plain httpx calls (no vendor SDKs) keep dependencies small and make every
provider swappable through environment variables only.
"""

from __future__ import annotations

import httpx

from app.llm.providers.base import LLMError, LLMProvider, parse_json_object


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str = "gpt-4o-mini", base_url: str = "https://api.openai.com/v1",
                 timeout: float = 8.0, transport: httpx.BaseTransport | None = None) -> None:
        self.api_key, self.model, self.base_url = api_key, model or "gpt-4o-mini", base_url.rstrip("/")
        self.client = httpx.Client(timeout=timeout, transport=transport)

    def complete_json(self, system: str, user: str) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        r = self.client.post(f"{self.base_url}/chat/completions", headers=headers, json=body)
        if r.status_code >= 400:
            raise LLMError(f"{self.name} HTTP {r.status_code}")
        try:
            content = r.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"{self.name}: unexpected response shape") from exc
        return parse_json_object(content)


class OpenAICompatibleProvider(OpenAIProvider):
    """vLLM, Ollama (/v1), Together, Groq, Fireworks, LM Studio ... (open-source models)."""

    name = "openai_compatible"

    def __init__(self, api_key: str, model: str, base_url: str, timeout: float = 8.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        if not base_url:
            raise LLMError("LLM_BASE_URL is required for openai_compatible")
        super().__init__(api_key, model, base_url, timeout, transport)


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str = "claude-haiku-5-5", timeout: float = 8.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.api_key, self.model = api_key, model or "claude-haiku-5-5"
        self.client = httpx.Client(timeout=timeout, transport=transport)

    def complete_json(self, system: str, user: str) -> dict:
        r = self.client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": self.model, "max_tokens": 600, "temperature": 0, "system": system,
                  "messages": [{"role": "user", "content": user}]},
        )
        if r.status_code >= 400:
            raise LLMError(f"anthropic HTTP {r.status_code}")
        try:
            blocks = r.json()["content"]
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        except (KeyError, ValueError, TypeError) as exc:
            raise LLMError("anthropic: unexpected response shape") from exc
        return parse_json_object(text)


class GoogleProvider(LLMProvider):
    name = "google"

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash", timeout: float = 8.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.api_key, self.model = api_key, model or "gemini-2.0-flash"
        self.client = httpx.Client(timeout=timeout, transport=transport)

    def complete_json(self, system: str, user: str) -> dict:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        r = self.client.post(
            url,
            headers={"x-goog-api-key": self.api_key, "content-type": "application/json"},
            json={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
            },
        )
        if r.status_code >= 400:
            raise LLMError(f"google HTTP {r.status_code}")
        try:
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError("google: unexpected response shape") from exc
        return parse_json_object(text)


class MockProvider(LLMProvider):
    """Deterministic provider for tests and demos. Returns a canned response or a callable's output."""

    name = "mock"

    def __init__(self, response: dict | None = None, fn=None) -> None:
        self.response = response or {}
        self.fn = fn
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str) -> dict:
        self.calls.append((system, user))
        if self.fn is not None:
            return self.fn(user)
        return dict(self.response)
