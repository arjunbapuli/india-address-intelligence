"""Shared service objects, built once at startup and stored on app.state."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.geography.gazetteer import Gazetteer, get_gazetteer
from app.geography.providers.base import build_external_providers
from app.geography.providers.fallback import GeocoderFallback
from app.intelligence.resolution.resolver import AddressResolver
from app.llm.client import build_extractor
from app.stt.base import SpeechToTextProvider, build_stt


@dataclass
class Services:
    gazetteer: Gazetteer
    resolver: AddressResolver
    stt: SpeechToTextProvider | None
    llm_enabled: bool
    external_geocoders: list[str]


def build_services(g: Gazetteer, settings: Settings | None = None, llm_extractor=None,
                   stt: SpeechToTextProvider | None = None) -> Services:
    s = settings or get_settings()
    extractor = llm_extractor if llm_extractor is not None else build_extractor(s)
    providers = build_external_providers(s)
    fallback = GeocoderFallback(g, providers) if providers else None
    resolver = AddressResolver(g, s, llm_extractor=extractor, geo_fallback=fallback)
    return Services(
        gazetteer=g, resolver=resolver,
        stt=stt if stt is not None else build_stt(s.stt_provider, s.stt_api_key, s.stt_model),
        llm_enabled=extractor is not None, external_geocoders=[p.name for p in providers],
    )


def get_services(request: Request) -> Services:
    svc: Services | None = getattr(request.app.state, "services", None)
    if svc is None:
        g = get_gazetteer()
        if g is None:
            raise ApiError(503, "gazetteer_not_loaded",
                           "Geographic data is not loaded yet. Check /health; run `python -m scripts.ingest`.")
        svc = build_services(g)
        request.app.state.services = svc
    return svc
