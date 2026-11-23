"""Speech-to-text abstraction (V2). Audio -> transcript -> the same text pipeline.

No custom STT model is trained here. Providers wrap existing services and are
selected with STT_PROVIDER. Each provider returns a `Transcript`; the API then
calls the address resolver on `Transcript.text`, so every improvement to the
text pipeline applies to voice input automatically.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx


class STTError(RuntimeError):
    pass


@dataclass
class Transcript:
    text: str
    provider: str
    language: str | None = None
    confidence: float | None = None
    alternatives: list[str] = field(default_factory=list)


class SpeechToTextProvider(ABC):
    name = "base"

    @abstractmethod
    async def transcribe(self, audio: bytes, mime_type: str = "audio/wav", language: str | None = None) -> Transcript:
        ...


class DeepgramProvider(SpeechToTextProvider):
    """Deepgram pre-recorded API. `language`: e.g. 'en-IN', 'hi', or None for auto-detect."""

    name = "deepgram"

    def __init__(self, api_key: str, model: str = "nova-3", transport: httpx.AsyncBaseTransport | None = None):
        self.api_key, self.model, self.transport = api_key, model or "nova-3", transport

    async def transcribe(self, audio: bytes, mime_type: str = "audio/wav", language: str | None = None) -> Transcript:
        params = {"model": self.model, "smart_format": "true", "punctuate": "true"}
        if language:
            params["language"] = language
        else:
            params["detect_language"] = "true"
        async with httpx.AsyncClient(timeout=30, transport=self.transport) as c:
            r = await c.post("https://api.deepgram.com/v1/listen", params=params, content=audio,
                             headers={"Authorization": f"Token {self.api_key}", "Content-Type": mime_type})
        if r.status_code >= 400:
            raise STTError(f"deepgram HTTP {r.status_code}")
        ch = r.json()["results"]["channels"][0]
        alts = ch.get("alternatives", [])
        if not alts:
            raise STTError("deepgram returned no transcript")
        return Transcript(text=alts[0].get("transcript", ""), provider=self.name,
                          language=ch.get("detected_language", language), confidence=alts[0].get("confidence"),
                          alternatives=[a.get("transcript", "") for a in alts[1:]])


class OpenAIWhisperProvider(SpeechToTextProvider):
    name = "openai_whisper"

    def __init__(self, api_key: str, model: str = "whisper-1", transport: httpx.AsyncBaseTransport | None = None):
        self.api_key, self.model, self.transport = api_key, model or "whisper-1", transport

    async def transcribe(self, audio: bytes, mime_type: str = "audio/wav", language: str | None = None) -> Transcript:
        data = {"model": self.model}
        if language:
            data["language"] = language.split("-")[0]
        async with httpx.AsyncClient(timeout=60, transport=self.transport) as c:
            r = await c.post("https://api.openai.com/v1/audio/transcriptions",
                             headers={"Authorization": f"Bearer {self.api_key}"}, data=data,
                             files={"file": ("audio", audio, mime_type)})
        if r.status_code >= 400:
            raise STTError(f"openai_whisper HTTP {r.status_code}")
        return Transcript(text=r.json().get("text", ""), provider=self.name, language=language)


class SarvamProvider(SpeechToTextProvider):
    """Sarvam AI speech-to-text (Indian languages). Model name configurable via STT_MODEL."""

    name = "sarvam"

    def __init__(self, api_key: str, model: str = "saarika:v2.5", transport: httpx.AsyncBaseTransport | None = None):
        self.api_key, self.model, self.transport = api_key, model or "saarika:v2.5", transport

    async def transcribe(self, audio: bytes, mime_type: str = "audio/wav", language: str | None = None) -> Transcript:
        data = {"model": self.model}
        if language:
            data["language_code"] = language
        async with httpx.AsyncClient(timeout=60, transport=self.transport) as c:
            r = await c.post("https://api.sarvam.ai/speech-to-text", headers={"api-subscription-key": self.api_key},
                             data=data, files={"file": ("audio.wav", audio, mime_type)})
        if r.status_code >= 400:
            raise STTError(f"sarvam HTTP {r.status_code}")
        body = r.json()
        return Transcript(text=body.get("transcript", ""), provider=self.name,
                          language=body.get("language_code", language))


class MockSTTProvider(SpeechToTextProvider):
    name = "mock"

    def __init__(self, text: str = "") -> None:
        self.text = text

    async def transcribe(self, audio: bytes, mime_type: str = "audio/wav", language: str | None = None) -> Transcript:
        # Tests may send UTF-8 text as "audio" to simulate a transcript
        text = self.text or audio.decode("utf-8", errors="ignore")
        return Transcript(text=text, provider=self.name, language=language, confidence=1.0)


def build_stt(provider: str, api_key: str, model: str = "") -> SpeechToTextProvider | None:
    if provider == "none":
        return None
    if provider == "mock":
        return MockSTTProvider()
    if not api_key:
        return None
    return {"deepgram": DeepgramProvider, "openai_whisper": OpenAIWhisperProvider, "sarvam": SarvamProvider}[provider](
        api_key, model)
