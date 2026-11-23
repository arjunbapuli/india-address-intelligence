"""LLM provider interface. Providers return parsed JSON; they never see the gazetteer."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod


class LLMError(RuntimeError):
    pass


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    def complete_json(self, system: str, user: str) -> dict:
        """Return a JSON object produced by the model for the given prompts."""


def parse_json_object(text: str) -> dict:
    """Extract the first JSON object from model text (tolerates ```json fences)."""
    text = text.strip()
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        raise LLMError("model did not return a JSON object")
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        raise LLMError(f"invalid JSON from model: {exc}") from exc
    if not isinstance(obj, dict):
        raise LLMError("model JSON is not an object")
    return obj
