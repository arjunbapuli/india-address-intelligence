"""Audio -> STT -> address resolution (V2 foundation)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.api.deps import Services, get_services
from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.metrics import metrics
from app.core.ratelimit import enforce_rate_limit
from app.core.security import authenticate
from app.database.repositories.records import new_resolution_id
from app.intelligence.resolution.models import Resolution
from app.intelligence.resolution.resolver import Hints
from app.stt.base import STTError

router = APIRouter(prefix="/v1", tags=["audio"])


class AudioResolution(BaseModel):
    success: bool = True
    transcript: str
    stt_provider: str
    stt_confidence: float | None = None
    stt_language: str | None = None
    resolution: Resolution


@router.post(
    "/address/resolve/audio",
    response_model=AudioResolution,
    summary="Resolve an address from an audio recording (requires STT_PROVIDER)",
    description=(
        "Transcribes the recording with the configured STT provider (Deepgram, OpenAI Whisper, Sarvam) and "
        "runs the transcript through the same pipeline as /v1/address/resolve. Returns 501 when no STT "
        "provider is configured. Audio is not stored.\n\n**Latency:** STT time (typically 0.5-3 s) + ~10 ms."
    ),
)
async def resolve_audio(audio: UploadFile = File(...), language: str | None = Form(None),
                        city: str | None = Form(None), state: str | None = Form(None),
                        client_id: str = Depends(authenticate), svc: Services = Depends(get_services)
                        ) -> AudioResolution:
    if svc.stt is None:
        raise ApiError(501, "stt_not_configured", "Set STT_PROVIDER and STT_API_KEY to enable audio input.")
    enforce_rate_limit(client_id, cost=5)
    settings = get_settings()
    data = await audio.read(settings.max_audio_bytes + 1)
    if len(data) > settings.max_audio_bytes:
        raise ApiError(413, "audio_too_large", f"audio must be at most {settings.max_audio_bytes} bytes")
    try:
        tr = await svc.stt.transcribe(data, audio.content_type or "audio/wav", language)
    except STTError as exc:
        raise ApiError(502, "stt_failed", "Speech-to-text provider failed.") from exc
    if not tr.text.strip():
        raise ApiError(422, "empty_transcript", "No speech was recognised in the audio.")
    res = await run_in_threadpool(svc.resolver.resolve, tr.text, Hints(city=city, state=state, language=language))
    res.metadata.resolution_id = new_resolution_id()
    metrics.record_resolution(res.resolution_status)
    return AudioResolution(transcript=tr.text, stt_provider=tr.provider, stt_confidence=tr.confidence,
                           stt_language=tr.language, resolution=res)
